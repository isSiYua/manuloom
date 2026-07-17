from __future__ import annotations

import math
import os
import re
import shutil
import statistics
import subprocess
from pathlib import Path

from .bilibili import BilibiliClient, UA, VideoInfo
from .llm import image_message, vision_client
from .models import Frame, Segment
from .utils import require_command, timestamp

SHOWINFO_RE = re.compile(r"pts_time:([0-9]+(?:\.[0-9]+)?)")
VISUAL_HINT_RE = re.compile(
    r"(?:图|表|界面|屏幕|这里|这个|步骤|设置|代码|参数|数据|对比|效果|演示|点击|选择|输入|看一下|如图|如下|\d)"
)
DEFAULT_VISION_FRAME_BUDGET = 6
# This is a cost/safety ceiling, not a sampling target.  The actual paid review
# count is derived from transcript-planner requests and distinct scene/slide
# candidates.  Long PPT tutorials may therefore use well over one frame per
# minute while talking-head videos may use none.
MAX_ADAPTIVE_VISION_FRAME_BUDGET = 60
MAX_PAID_VISION_REVIEWS_PER_MINUTE = 2
TEXT_SENSITIVE_VISUAL_KINDS = {
    "text", "list", "table", "code", "formula",
    "diagram", "chart", "process", "ui", "paper_figure", "comparison",
}


def asset_filename(task_key: str, index: int, at: float) -> str:
    """Return a short, portable asset name independent of transcript text."""
    prefix = re.sub(r"[^0-9A-Za-z_-]+", "-", task_key).strip("-_") or "manual-1"
    return f"{prefix}-{index:03d}-{timestamp(at)}.png"


def adaptive_vision_frame_budget(duration: float, max_frames: int) -> int:
    """Legacy duration fallback used only when the planner requests no visuals."""
    if max_frames <= 0:
        return 0
    budget = math.ceil(max(0.0, duration) / 60.0)
    return min(max_frames, MAX_ADAPTIVE_VISION_FRAME_BUDGET, max(6, budget))


def semantic_vision_frame_budget(
    selected: list[tuple[Frame, float]],
    visual_requests: list[dict[str, object]] | None,
    max_frames: int,
    duration: float | None = None,
) -> int:
    """Derive paid vision work from AI-planned ranges and distinct candidates.

    Duration is deliberately not part of the primary policy.  Every locally
    deduplicated candidate inside a transcript-grounded request may be reviewed,
    up to the explicit safety ceiling.  This lets a fast PPT presentation retain
    many distinct slides and a visually sparse conversation retain very few.
    """
    if max_frames <= 0 or not selected:
        return 0
    ceiling = max(
        0,
        min(
            max_frames,
            int(os.getenv("VTM_MAX_VISION_FRAMES", str(MAX_ADAPTIVE_VISION_FRAME_BUDGET))),
        ),
    )
    if ceiling <= 0:
        return 0
    requested_ids: set[int] = set()
    for frame, _score in selected:
        for request in visual_requests or []:
            try:
                start = float(request.get("time_start", 0.0))
                end = float(request.get("time_end", start))
            except (TypeError, ValueError):
                continue
            if start <= frame.timestamp <= end:
                requested_ids.add(id(frame))
                break
    if requested_ids:
        if duration is None:
            duration = max((frame.timestamp for frame, _score in selected), default=0.0)
        dynamic_cost_cap = max(
            DEFAULT_VISION_FRAME_BUDGET,
            math.ceil(max(0.0, duration) / 60.0 * MAX_PAID_VISION_REVIEWS_PER_MINUTE),
        )
        return min(ceiling, dynamic_cost_cap, len(requested_ids))
    return min(ceiling, DEFAULT_VISION_FRAME_BUDGET, len(selected))
OCR_WEIRD_SYMBOL_RE = re.compile(r"[�□■◆◇●○※¤¦]")


def _run(command: list[str], *, capture: bool = False, timeout: int = 900) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        command,
        check=True,
        stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        timeout=timeout,
    )


def probe_duration(video: Path) -> float:
    ffprobe = require_command("ffprobe")
    result = _run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(video),
        ],
        capture=True,
    )
    return float(result.stdout.decode().strip())


def raw_gray_frame(video: Path, at: float, size: int = 32) -> bytes:
    ffmpeg = require_command("ffmpeg")
    result = _run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{max(0, at):.3f}",
            "-i",
            str(video),
            "-frames:v",
            "1",
            "-vf",
            f"scale={size}:{size},format=gray",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "gray",
            "-",
        ],
        capture=True,
        timeout=30,
    )
    expected = size * size
    if len(result.stdout) < expected:
        raise RuntimeError(f"Could not decode frame at {at:.2f}s")
    return result.stdout[:expected]


def average_hash(pixels: bytes) -> str:
    mean = sum(pixels) / max(1, len(pixels))
    bits = [1 if value >= mean else 0 for value in pixels]
    value = 0
    chunks: list[str] = []
    for index, bit in enumerate(bits, start=1):
        value = (value << 1) | bit
        if index % 4 == 0:
            chunks.append(format(value, "x"))
            value = 0
    return "".join(chunks)


def hash_distance(a: str, b: str) -> float:
    if not a or not b:
        return 1.0
    bits = min(len(a), len(b)) * 4
    diff = (int(a[: bits // 4], 16) ^ int(b[: bits // 4], 16)).bit_count()
    return diff / max(1, bits)


def duplicate_distance_threshold(
    timestamp_value: float,
    visual_requests: list[dict[str, object]] | None,
) -> float:
    """Use stricter deduplication inside requested evidence windows.

    Consecutive slides and progressive PPT/whiteboard demonstrations can share
    the same template while changing only one sentence, annotation, diagram
    edge, or completed result.  A 32x32 average hash sees those frames as near-
    identical.  Keep the small changes for OCR/vision review; downstream
    information-gain classification still removes genuinely repeated or
    decorative frames.
    """
    for request in visual_requests or []:
        try:
            start = float(request.get("time_start", 0.0))
            end = float(request.get("time_end", start))
        except (TypeError, ValueError):
            continue
        kind = str(request.get("expected_kind") or "").strip().lower()
        if start <= timestamp_value <= end and kind in TEXT_SENSITIVE_VISUAL_KINDS:
            return 0.045
    return 0.10


def quality(pixels: bytes) -> tuple[float, float]:
    if not pixels:
        return 0.0, 0.0
    brightness = statistics.fmean(pixels) / 255
    contrast = statistics.pstdev(pixels) / 128 if len(pixels) > 1 else 0
    return round(brightness, 4), round(contrast, 4)


def calibrate_threshold(video: Path, duration: float, samples: int = 9) -> float:
    if duration <= 0:
        return 0.2
    points = [duration * (0.05 + index * 0.9 / max(1, samples - 1)) for index in range(samples)]
    hashes: list[str] = []
    for point in points:
        try:
            hashes.append(average_hash(raw_gray_frame(video, point)))
        except Exception:
            continue
    diffs = [hash_distance(hashes[index - 1], hashes[index]) for index in range(1, len(hashes))]
    if not diffs:
        return 0.2
    ordered = sorted(diffs)
    def at(ratio: float) -> float:
        return ordered[min(len(ordered) - 1, round((len(ordered) - 1) * ratio))]

    median, p75, p90 = at(0.5), at(0.75), at(0.9)
    threshold = max(median * 0.15, p75 * 0.2, p90 * 0.25)
    if p75 >= 0.12:
        threshold = min(threshold, 0.05)
    elif p90 < 0.05:
        threshold = 0.05
    return round(min(0.30, max(0.05, threshold)), 2)


def detect_scenes(video: Path, threshold: float) -> list[float]:
    ffmpeg = require_command("ffmpeg")
    result = subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-i",
            str(video),
            "-vf",
            f"select='gt(scene,{threshold})',showinfo",
            "-an",
            "-f",
            "null",
            "-",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        timeout=1800,
        check=False,
    )
    text = result.stderr.decode("utf-8", errors="replace")
    return sorted({float(value) for value in SHOWINFO_RE.findall(text)})


def nearest_segments(segments: list[Segment], at: float, window: float = 8.0) -> list[Segment]:
    close = [segment for segment in segments if segment.start - window <= at <= segment.end + window]
    return sorted(close, key=lambda segment: abs((segment.start + segment.end) / 2 - at))[:8]


def candidate_times(
    scenes: list[float],
    segments: list[Segment],
    duration: float,
    max_frames: int,
    visual_requests: list[dict[str, object]] | None = None,
) -> list[tuple[float, float]]:
    candidates: dict[int, tuple[float, float]] = {}

    def add(at: float, score: float) -> None:
        if 1.0 <= at <= max(1.0, duration - 0.2):
            key = round(at * 2)
            previous = candidates.get(key)
            if previous is None or score > previous[1]:
                candidates[key] = (at, score)

    for at in scenes:
        nearby = nearest_segments(segments, at)
        hint = sum(1 for item in nearby if VISUAL_HINT_RE.search(item.text))
        add(at + 0.35, 2.0 + min(3, hint))
    hinted = [segment for segment in segments if VISUAL_HINT_RE.search(segment.text)]
    for segment in hinted:
        add((segment.start + segment.end) / 2, 3.0)
    for request in visual_requests or []:
        try:
            start = max(0.0, float(request.get("time_start", 0.0)))
            end = min(duration, max(start, float(request.get("time_end", start))))
        except (TypeError, ValueError):
            continue
        # Preserve every distinct scene/slide change inside an AI-requested
        # range.  Start/mid/end probes cover static slides when scene detection
        # misses a dissolve or screen recording transition.
        request_scenes = [at for at in scenes if start <= at <= end]
        for at in request_scenes:
            add(at + 0.35, 7.0)
        add(min(end, start + 0.45), 6.0)
        add((start + end) / 2, 6.5)
        if end - start >= 3:
            add(max(start, end - 0.45), 6.0)
    if not visual_requests:
        fallback_count = max(2, min(max_frames, math.ceil(duration / 90)))
        for index in range(fallback_count):
            add(duration * (index + 1) / (fallback_count + 1), 0.5)
    values = list(candidates.values())
    limit = max_frames * 6
    bins = max(1, min(max_frames * 3, math.ceil(duration / 8)))
    width = max(1.0, duration / bins)
    chosen: list[tuple[float, float]] = []
    for index in range(bins):
        bucket = [item for item in values if index * width <= item[0] < (index + 1) * width]
        chosen.extend(sorted(bucket, key=lambda item: item[1], reverse=True)[:3])
    keys = {round(item[0] * 2) for item in chosen}
    for item in sorted(values, key=lambda value: value[1], reverse=True):
        if len(chosen) >= limit:
            break
        if round(item[0] * 2) not in keys:
            chosen.append(item)
            keys.add(round(item[0] * 2))
    return sorted(chosen[:limit], key=lambda item: item[0])


def capture_frame(video: Path, at: float, output: Path) -> None:
    ffmpeg = require_command("ffmpeg")
    output.parent.mkdir(parents=True, exist_ok=True)
    _run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{at:.3f}",
            "-i",
            str(video),
            "-frames:v",
            "1",
            "-q:v",
            "2",
            "-y",
            str(output),
        ],
        timeout=60,
    )


def recapture_retained_frames(
    client: BilibiliClient,
    info: VideoInfo,
    frames: list[Frame],
    *,
    max_height: int = 1080,
) -> dict[str, int | str]:
    """Replace retained analysis frames from the highest available final stream.

    ffmpeg seeks against the remote DASH stream, so the pipeline does not need to
    download and decode an entire 1080p video merely to replace a few screenshots.
    """
    retained = [frame for frame in frames if frame.keep_image and frame.path]
    if not retained:
        return {"requested_height": max_height, "actual_height": 0, "upgraded_count": 0}
    stream = client.media_stream(info, audio_only=False, max_height=max_height)
    actual_height = int(stream.get("height") or 0)
    ffmpeg = require_command("ffmpeg")
    upgraded = 0
    headers = f"User-Agent: {UA}\r\nReferer: https://www.bilibili.com/video/{info.bvid}/\r\n"
    for frame in retained:
        output = Path(frame.path)
        temporary = output.with_name(output.stem + ".highres.png")
        try:
            _run(
                [
                    ffmpeg,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-headers",
                    headers,
                    "-ss",
                    f"{frame.timestamp:.3f}",
                    "-i",
                    str(stream["url"]),
                    "-frames:v",
                    "1",
                    "-q:v",
                    "2",
                    "-y",
                    str(temporary),
                ],
                timeout=90,
            )
            if temporary.is_file() and temporary.stat().st_size > 0:
                temporary.replace(output)
                frame.final_height = actual_height or None
                upgraded += 1
        finally:
            temporary.unlink(missing_ok=True)
    return {
        "requested_height": max_height,
        "actual_height": actual_height,
        "upgraded_count": upgraded,
        "method": "remote_timestamp_seek",
    }


def run_ocr(image: Path) -> tuple[str, float]:
    tesseract = shutil.which("tesseract")
    if not tesseract:
        return "", 0.0
    for language in ("chi_sim+eng", "eng"):
        result = subprocess.run(
            [tesseract, str(image), "stdout", "-l", language, "tsv"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=60,
            check=False,
        )
        words: list[str] = []
        weighted_confidence = 0.0
        weight = 0
        for line in result.stdout.decode("utf-8", errors="replace").splitlines()[1:]:
            columns = line.split("\t", 11)
            if len(columns) != 12:
                continue
            token = columns[11].strip()
            try:
                confidence = float(columns[10])
            except ValueError:
                continue
            if token and confidence >= 0:
                words.append(token)
                token_weight = max(1, len(token))
                weighted_confidence += confidence * token_weight
                weight += token_weight
        text = re.sub(r"\s+", " ", " ".join(words)).strip()
        if text:
            return text, round(weighted_confidence / max(1, weight), 1)
    return "", 0.0


def ocr_text_is_usable(text: str, confidence: float) -> bool:
    """Reject obvious OCR garbage without discarding the underlying frame.

    Adapted from summarize's slide-OCR quality heuristics: require meaningful
    content and reject symbol-heavy or replacement-character-heavy output.
    Chinese OCR often inserts spaces between single characters, so this check
    deliberately avoids an English-only short-token ratio.
    """
    compact = re.sub(r"\s+", "", text)
    if len(compact) < 4 or confidence < 18:
        return False
    symbol_count = sum(
        1
        for char in compact
        if not (char.isalnum() or "\u4e00" <= char <= "\u9fff")
    )
    weird_count = len(OCR_WEIRD_SYMBOL_RE.findall(compact))
    if symbol_count / max(1, len(compact)) > 0.38:
        return False
    if weird_count / max(1, len(compact)) > 0.08:
        return False
    return True


def describe_if_needed(
    image: Path, transcript: str, ocr: str, editorial_request: str = ""
) -> str:
    client = vision_client()
    if client is None:
        return ""
    prompt = (
        "只描述画面中清晰可见、对笔记有用的信息。完整抄录可辨认的节目名单、文章或单集标题、"
        "人名、命令、代码、URL、数字、选项和配置。代码要保留缩进与符号，公式要转写为 LaTeX；"
        "同时指出画面属于纯文字、表格、代码、公式、流程图、结构图、图表、界面操作、前后对比还是论文原图。"
        "不要推断画面外背景，不要把附近字幕当作画面内容。"
        + ("本帧的编辑查看目的：" + editorial_request[:500] + "\n" if editorial_request else "")
        + "附近字幕仅用于判断相关性："
        + transcript[:1000]
        + "\n已有 OCR："
        + ocr[:1500]
    )
    try:
        return client.chat([image_message(image, prompt)], temperature=0.0, max_tokens=1200).strip()
    except Exception:
        return ""


def select_temporally_diverse(
    retained: list[tuple[Frame, float]], duration: float, limit: int
) -> list[tuple[Frame, float]]:
    """Prefer the best evidence in each time region before filling globally."""
    if not retained or limit <= 0:
        return []
    bins = max(1, min(limit, math.ceil(duration / 45)))
    width = max(1.0, duration / bins)
    selected: list[tuple[Frame, float]] = []
    for index in range(bins):
        bucket = [
            item for item in retained
            if index * width <= item[0].timestamp < (index + 1) * width
        ]
        if bucket:
            selected.append(max(bucket, key=lambda item: item[1]))
    chosen = {id(item[0]) for item in selected}
    for item in sorted(retained, key=lambda value: value[1], reverse=True):
        if len(selected) >= limit:
            break
        if id(item[0]) not in chosen:
            selected.append(item)
            chosen.add(id(item[0]))
    return sorted(selected[:limit], key=lambda item: item[0].timestamp)


def vision_priority_ids(
    selected: list[tuple[Frame, float]], budget: int, duration: float | None = None
) -> set[int]:
    """Spend vision across the timeline, then favor strength within each region."""
    if budget <= 0:
        return set()
    if duration is None:
        duration = max((frame.timestamp for frame, _score in selected), default=0.0)
    diverse = select_temporally_diverse(selected, duration, budget)
    return {id(frame) for frame, _ in diverse}


def vision_priority_ids_for_requests(
    selected: list[tuple[Frame, float]],
    budget: int,
    duration: float,
    visual_requests: list[dict[str, object]] | None,
) -> set[int]:
    """Review distinct frames in requested ranges, then use fallback diversity.

    Older versions selected only one frame per request, which silently lost
    successive PPT slides inside a single paragraph.  Requests now act as
    semantic windows: every distinct selected candidate in those windows is
    eligible until the explicit budget is exhausted.
    """
    if budget <= 0:
        return set()
    chosen: list[tuple[Frame, float]] = []
    chosen_ids: set[int] = set()

    def candidates_for(request: dict[str, object]) -> list[tuple[Frame, float]]:
        try:
            start = max(0.0, float(request.get("time_start", 0.0)))
            end = min(duration, max(start, float(request.get("time_end", start))))
        except (TypeError, ValueError):
            return []
        midpoint = (start + end) / 2
        candidates = [item for item in selected if start <= item[0].timestamp <= end]
        if not candidates:
            nearby = sorted(selected, key=lambda item: abs(item[0].timestamp - midpoint))
            candidates = [item for item in nearby[:1] if abs(item[0].timestamp - midpoint) <= 8]
        return sorted(candidates, key=lambda value: value[0].timestamp)

    def add(item: tuple[Frame, float]) -> bool:
        if id(item[0]) in chosen_ids:
            return False
        chosen.append(item)
        chosen_ids.add(id(item[0]))
        return True

    requests = list(visual_requests or [])
    priority = [
        request
        for request in requests
        if "ASR" in str(request.get("purpose") or "").upper()
    ]
    ordinary = [request for request in requests if request not in priority]

    # ASR evidence often arrives as two consecutive same-template cards: the
    # first names the identifier and the second carries the polarity.  Spend
    # at most two already-budgeted reviews on the temporal endpoints of each
    # narrow suspect range before ordinary visual requests.
    for request in priority:
        candidates = candidates_for(request)
        probes = candidates if len(candidates) <= 2 else [candidates[0], candidates[-1]]
        for item in probes:
            add(item)
            if len(chosen) >= budget:
                return chosen_ids

    # Give ordinary ranges one representative before using leftover budget.
    for request in ordinary:
        candidates = [
            item for item in candidates_for(request) if id(item[0]) not in chosen_ids
        ]
        if candidates:
            add(max(candidates, key=lambda item: item[1]))
        if len(chosen) >= budget:
            return chosen_ids

    requested_remaining: list[tuple[Frame, float]] = []
    requested_seen: set[int] = set()
    for request in requests:
        for item in candidates_for(request):
            identity = id(item[0])
            if identity not in chosen_ids and identity not in requested_seen:
                requested_remaining.append(item)
                requested_seen.add(identity)
    for item in select_temporally_diverse(
        requested_remaining, duration, budget - len(chosen)
    ):
        add(item)
    if len(chosen) >= budget:
        return chosen_ids

    remaining = [item for item in selected if id(item[0]) not in chosen_ids]
    for item in select_temporally_diverse(remaining, duration, budget - len(chosen)):
        chosen_ids.add(id(item[0]))
    return chosen_ids


def _request_for_timestamp(
    timestamp_value: float, visual_requests: list[dict[str, object]] | None
) -> str:
    matches: list[str] = []
    for request in visual_requests or []:
        try:
            start = float(request.get("time_start", 0.0))
            end = float(request.get("time_end", start))
        except (TypeError, ValueError):
            continue
        if start <= timestamp_value <= end:
            purpose = str(request.get("purpose") or "").strip()
            if purpose:
                matches.append(purpose)
    return "；".join(matches[:2])


def extract_useful_frames(
    video: Path,
    assets_dir: Path,
    segments: list[Segment],
    *,
    max_frames: int,
    visual_requests: list[dict[str, object]] | None = None,
    task_key: str = "manual-1",
) -> tuple[list[Frame], dict[str, float | int]]:
    duration = probe_duration(video)
    threshold = calibrate_threshold(video, duration)
    scenes = detect_scenes(video, threshold)
    retained: list[tuple[Frame, float]] = []
    for at, base_score in candidate_times(
        scenes, segments, duration, max_frames, visual_requests
    ):
        try:
            pixels = raw_gray_frame(video, at)
        except Exception:
            continue
        ahash = average_hash(pixels)
        duplicate_threshold = duplicate_distance_threshold(at, visual_requests)
        if any(
            hash_distance(ahash, existing.ahash) < duplicate_threshold
            for existing, _ in retained
        ):
            continue
        brightness, contrast = quality(pixels)
        if brightness < 0.06 or brightness > 0.98 or contrast < 0.04:
            continue
        nearby = nearest_segments(segments, at)
        nearby_text = "".join(segment.text for segment in nearby)
        temp = assets_dir / f"candidate-{len(retained) + 1:03d}.png"
        capture_frame(video, at, temp)
        if not temp.is_file() or temp.stat().st_size <= 0:
            temp.unlink(missing_ok=True)
            continue
        ocr, ocr_confidence = run_ocr(temp)
        if ocr and not ocr_text_is_usable(ocr, ocr_confidence):
            ocr = ""
            ocr_confidence = 0.0
        score = base_score + min(2.0, len(ocr) / 80) + min(1.0, contrast)
        frame = Frame(
            timestamp=at,
            path=str(temp),
            source_ids=[segment.id for segment in nearby],
            ocr_text=ocr,
            ocr_confidence=ocr_confidence,
            ahash=ahash,
            brightness=brightness,
            contrast=contrast,
        )
        retained.append((frame, score))
    selected = select_temporally_diverse(retained, duration, max_frames)
    final_frames: list[Frame] = []
    selected_paths = {Path(frame.path) for frame, _ in selected}
    vision_budget = semantic_vision_frame_budget(
        selected, visual_requests, max_frames, duration=duration
    )
    vision_ids = vision_priority_ids_for_requests(
        selected, vision_budget, duration, visual_requests
    )
    for index, (frame, _) in enumerate(selected, start=1):
        source = Path(frame.path)
        if not source.is_file() or source.stat().st_size <= 0:
            continue
        nearby_text = "".join(
            segment.text for segment in segments if segment.id in set(frame.source_ids)
        )
        destination = assets_dir / asset_filename(task_key, index, frame.timestamp)
        source.replace(destination)
        frame.path = str(destination)
        if id(frame) in vision_ids:
            frame.vision_description = describe_if_needed(
                destination,
                nearby_text,
                frame.ocr_text,
                _request_for_timestamp(frame.timestamp, visual_requests),
            )
        final_frames.append(frame)
    for candidate in assets_dir.glob("candidate-*.png"):
        if candidate not in selected_paths:
            candidate.unlink(missing_ok=True)
    return final_frames, {
        "duration": duration,
        "scene_threshold": threshold,
        "scene_count": len(scenes),
        "candidate_count": len(retained),
        "retained_count": len(final_frames),
        "planner_visual_request_count": len(visual_requests or []),
        "vision_review_count": len(vision_ids),
    }
