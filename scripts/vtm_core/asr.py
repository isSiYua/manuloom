from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import tempfile
import wave
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any

from .models import Segment

FUNASR_SENTINEL = Path.home() / ".cache" / "manuloom" / "funasr-paraformer.ready.json"
FUNASR_MODEL = "iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-pytorch"
FUNASR_VAD_MODEL = "iic/speech_fsmn_vad_zh-cn-16k-common-pytorch"
FUNASR_PUNC_MODEL = "iic/punc_ct-transformer_zh-cn-common-vocab272727-pytorch"
FUNASR_BATCH_SECONDS = 60


def funasr_ready() -> bool:
    if not FUNASR_SENTINEL.is_file():
        return False
    try:
        payload = json.loads(FUNASR_SENTINEL.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return payload.get("version") == 2 and bool(payload.get("vad")) and bool(payload.get("punc"))


def faster_whisper_model_path(model_name: str) -> Path | None:
    supplied = Path(model_name).expanduser()
    try:
        supplied_is_model = supplied.is_dir() and (supplied / "model.bin").is_file()
    except OSError:
        # A service account may inherit an inaccessible cwd (for example /root)
        # while model_name is an ordinary alias such as "medium".
        supplied_is_model = False
    if supplied_is_model:
        return supplied
    hf_home = Path(os.getenv("HF_HOME", str(Path.home() / ".cache" / "huggingface")))
    repository = hf_home / "hub" / f"models--Systran--faster-whisper-{model_name}"
    snapshots = repository / "snapshots"
    if snapshots.is_dir():
        for candidate in sorted(snapshots.iterdir(), reverse=True):
            if candidate.is_dir() and (candidate / "model.bin").is_file():
                return candidate
    return None


def faster_whisper_ready(model_name: str) -> bool:
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        return False
    return faster_whisper_model_path(model_name) is not None


def prepare_funasr() -> dict[str, Any]:
    """Download Paraformer, VAD and punctuation models once during deployment."""
    try:
        from funasr import AutoModel
    except ImportError as exc:
        raise RuntimeError(
            "FunASR is not installed; install scripts/requirements-asr-cn.txt during deployment"
        ) from exc
    with tempfile.TemporaryDirectory(prefix="vtm-prepare-asr-") as temp:
        temp_path = Path(temp)
        sample = temp_path / "silence.wav"
        with wave.open(str(sample), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(16000)
            handle.writeframes(b"\0\0" * 16000)
        try:
            model = AutoModel(
                model=FUNASR_MODEL,
                vad_model=FUNASR_VAD_MODEL,
                punc_model=FUNASR_PUNC_MODEL,
                disable_update=True,
            )
            model.generate(input=str(sample), batch_size_s=60)
        except Exception as exc:
            raise RuntimeError(
                "FunASR/VAD/punctuation preparation failed; rerun prepare-asr from a network that can reach ModelScope"
            ) from exc
    FUNASR_SENTINEL.parent.mkdir(parents=True, exist_ok=True)
    FUNASR_SENTINEL.write_text(
        json.dumps(
            {
                "version": 2,
                "backend": "funasr",
                "model": FUNASR_MODEL,
                "vad": FUNASR_VAD_MODEL,
                "punc": FUNASR_PUNC_MODEL,
                "language": "zh",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return {
        "backend": "funasr",
        "model": "paraformer",
        "vad": "fsmn-vad",
        "punc": "ct-punc",
        "ready": True,
    }


def _srt_seconds(value: str) -> float:
    match = re.fullmatch(r"(\d+):(\d{2}):(\d{2})[,.](\d{3})", value.strip())
    if not match:
        raise ValueError(f"Invalid SRT timestamp: {value}")
    hours, minutes, seconds, millis = (int(item) for item in match.groups())
    return hours * 3600 + minutes * 60 + seconds + millis / 1000


def parse_srt(text: str) -> list[Segment]:
    segments: list[Segment] = []
    blocks = re.split(r"\r?\n\s*\r?\n", text.strip())
    for block in blocks:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        timing_index = next((index for index, line in enumerate(lines) if "-->" in line), None)
        if timing_index is None:
            continue
        start_text, end_text = [item.strip().split()[0] for item in lines[timing_index].split("-->", 1)]
        content = " ".join(lines[timing_index + 1 :]).strip()
        if not content:
            continue
        segments.append(
            Segment(
                id=f"s{len(segments) + 1:06d}",
                start=_srt_seconds(start_text),
                end=_srt_seconds(end_text),
                text=content,
            )
        )
    return segments


def _to_wav(media: Path) -> Path:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg is required to prepare audio for ASR")
    destination = media.with_name("asr-input.wav")
    completed = subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(media),
            "-ac",
            "1",
            "-ar",
            "16000",
            str(destination),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        timeout=1800,
        check=False,
    )
    if completed.returncode != 0 or not destination.is_file():
        raise RuntimeError("ffmpeg could not convert the downloaded audio for ASR")
    return destination


def _media_duration(media: Path) -> float:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return 0.0
    completed = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(media)],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        timeout=60,
        check=False,
    )
    try:
        return float(completed.stdout.strip())
    except ValueError:
        return 0.0


def _sentence_parts(text: str, max_chars: int = 90) -> list[str]:
    pieces = [item.strip() for item in re.findall(r".*?(?:[。！？!?；;]|$)", text) if item.strip()]
    result: list[str] = []
    for piece in pieces:
        while len(piece) > max_chars:
            cut = max(piece.rfind(mark, 0, max_chars) for mark in ("，", ",", "、", "：", ":"))
            cut = cut + 1 if cut >= max_chars // 3 else max_chars
            result.append(piece[:cut].strip())
            piece = piece[cut:].strip()
        if piece:
            result.append(piece)
    return result


def resegment_text(text: str, start: float, end: float) -> list[Segment]:
    parts = _sentence_parts(re.sub(r"\s+", " ", text).strip())
    if not parts:
        return []
    total = sum(max(1, len(part)) for part in parts)
    cursor = start
    result: list[Segment] = []
    for index, part in enumerate(parts, start=1):
        share = (end - start) * max(1, len(part)) / total
        part_end = end if index == len(parts) else cursor + share
        result.append(Segment(f"s{index:06d}", cursor, part_end, part))
        cursor = part_end
    return result


def normalize_segments(segments: list[Segment], duration: float | None = None) -> list[Segment]:
    """Repair coarse subtitle blocks, then enforce useful time granularity."""
    repaired: list[Segment] = []
    for segment in segments:
        span = max(0.0, segment.end - segment.start)
        if span > 35 or len(segment.text) > 180:
            repaired.extend(resegment_text(segment.text, segment.start, segment.end))
        else:
            repaired.append(segment)
    for index, segment in enumerate(repaired, start=1):
        segment.id = f"s{index:06d}"
    if not repaired:
        raise RuntimeError("ASR produced no usable transcript segments")
    previous = -0.001
    for segment in repaired:
        if segment.start < previous - 0.05 or segment.end <= segment.start:
            raise RuntimeError("Transcript timestamps are invalid or non-monotonic")
        if segment.end - segment.start > 45 or len(segment.text) > 240:
            raise RuntimeError("Transcript segmentation quality gate failed: oversized segment")
        previous = segment.end
    media_duration = duration or repaired[-1].end
    if media_duration >= 90 and len(repaired) < 4:
        raise RuntimeError("Transcript segmentation quality gate failed: too few timed segments")
    return repaired


def _transcribe_funasr(media: Path) -> tuple[list[Segment], dict[str, Any]]:
    if not funasr_ready():
        raise RuntimeError(
            "FunASR Paraformer is not prepared; run `scripts/vtm prepare-asr` once during deployment"
        )
    wav = _to_wav(media)
    try:
        from funasr import AutoModel
        # Background-process output is delivered directly to messaging
        # platforms. Suppress third-party model banners so only the CLI's six
        # Chinese stage messages are user-visible.
        with open(os.devnull, "w", encoding="utf-8") as sink, redirect_stdout(sink), redirect_stderr(sink):
            model = AutoModel(
                model=FUNASR_MODEL,
                vad_model=FUNASR_VAD_MODEL,
                punc_model=FUNASR_PUNC_MODEL,
                disable_update=True,
            )
            # Keep peak CPU memory bounded on the reference 8 GB server. The
            # deployment self-test also uses this batch size.
            generated = model.generate(input=str(wav), batch_size_s=FUNASR_BATCH_SECONDS)
    except Exception as exc:
        detail = re.sub(r"\s+", " ", str(exc)).strip()
        if len(detail) > 240:
            detail = detail[:237] + "..."
        cause = f"{type(exc).__name__}: {detail}" if detail else type(exc).__name__
        raise RuntimeError(
            f"FunASR transcription failed with the prepared VAD/punctuation pipeline ({cause})"
        ) from exc
    segments: list[Segment] = []
    for result in generated if isinstance(generated, list) else [generated]:
        if not isinstance(result, dict):
            continue
        sentence_info = result.get("sentence_info") or result.get("sentence") or []
        for item in sentence_info:
            text = str(item.get("text") or "").strip()
            start = float(item.get("start") or item.get("start_time") or 0) / 1000
            end = float(item.get("end") or item.get("end_time") or 0) / 1000
            if text and end > start:
                segments.append(Segment("", start, end, text))
        if not sentence_info and result.get("text"):
            segments.extend(resegment_text(str(result["text"]), 0.0, _media_duration(wav)))
    segments = normalize_segments(segments, _media_duration(wav))
    return segments, {
        "source": "funasr_paraformer",
        "model": "paraformer+fsmn-vad+ct-punc",
        "device": "cpu",
        "language": "zh",
        "host": platform.platform(),
    }


def _transcribe_faster_whisper(
    media: Path, model_name: str
) -> tuple[list[Segment], dict[str, Any]]:
    local_model = faster_whisper_model_path(model_name)
    if local_model is None:
        raise RuntimeError(
            "The faster-whisper model is not cached locally; prepare FunASR during deployment instead"
        )
    # Set offline mode before importing huggingface_hub through faster-whisper.
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise RuntimeError("faster-whisper is not installed") from exc

    device = "cuda" if os.getenv("CUDA_VISIBLE_DEVICES", "") not in {"", "-1"} else "cpu"
    compute = "float16" if device == "cuda" else "int8"
    try:
        model = WhisperModel(str(local_model), device=device, compute_type=compute)
    except Exception:
        device, compute = "cpu", "int8"
        model = WhisperModel(str(local_model), device=device, compute_type=compute)
    stream, info = model.transcribe(
        str(media),
        language="zh",
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 500},
        beam_size=5,
        condition_on_previous_text=True,
    )
    segments: list[Segment] = []
    for index, item in enumerate(stream, start=1):
        content = str(item.text or "").strip()
        if content:
            segments.append(
                Segment(
                    id=f"s{index:06d}",
                    start=float(item.start),
                    end=float(item.end),
                    text=content,
                )
            )
    segments = normalize_segments(segments, _media_duration(media))
    return segments, {
        "source": "faster_whisper",
        "model": model_name,
        "device": device,
        "compute_type": compute,
        "language": getattr(info, "language", "zh"),
        "language_probability": getattr(info, "language_probability", None),
        "host": platform.platform(),
    }


def transcribe(
    media: Path,
    model_name: str = "medium",
    backend: str | None = None,
) -> tuple[list[Segment], dict[str, Any]]:
    selected = (backend or os.getenv("VTM_ASR_BACKEND") or "auto").lower()
    if selected not in {"auto", "funasr", "faster-whisper"}:
        raise ValueError("ASR backend must be auto, funasr, or faster-whisper")
    if selected in {"auto", "funasr"} and funasr_ready():
        return _transcribe_funasr(media)
    if selected == "funasr":
        return _transcribe_funasr(media)
    if selected in {"auto", "faster-whisper"} and faster_whisper_ready(model_name):
        return _transcribe_faster_whisper(media, model_name)
    if selected == "faster-whisper":
        return _transcribe_faster_whisper(media, model_name)
    raise RuntimeError(
        "No deployment-ready ASR model is available. Run `scripts/vtm prepare-asr` once; "
        "video jobs never download or install models automatically."
    )
