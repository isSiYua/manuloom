from __future__ import annotations

import shutil
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .asr import normalize_segments, transcribe
from . import PIPELINE_VERSION
from .bilibili import BilibiliClient, VideoInfo
from .sources import BilibiliSourceAdapter
from .llm import text_client
from .media import download_media
from .direct_manuscript import (
    complete_direct_manuscript,
    create_direct_plan,
    create_visual_request_plan,
    merge_visual_requests,
    visual_requests_from_plan,
)
from .models import Frame, Segment
from .output import (
    compose_markdown,
    plan_frame_evidence_groups,
    remove_index_entries,
    update_indexes,
    write_artifacts,
)
from .tasks import state_root
from .transcript import enrich_with_visual_evidence
from .utils import atomic_json, load_json, safe_name
from .visual import extract_useful_frames, recapture_retained_frames


@dataclass(slots=True)
class Options:
    url: str
    vault: Path
    part: int | None = None
    cookies_file: Path | None = None
    no_visual: bool = False
    max_frames: int = 60
    keep_video: bool = False
    llm_model: str | None = None
    asr_model: str = "medium"
    asr_backend: str = "auto"
    visual_height: int = 720
    final_visual_height: int = 1080
    resume: Path | None = None
    progress: Callable[[str], None] | None = None
    task_key: str = "manual-1"
    task_date: str = ""
    daily_no: int = 1


def _progress(options: Options, message: str) -> None:
    if options.progress:
        options.progress(message)


def _part_url(url: str, part: int) -> str:
    parsed = urllib.parse.urlparse(url)
    query = urllib.parse.parse_qs(parsed.query)
    query["p"] = [str(part)]
    return urllib.parse.urlunparse(parsed._replace(query=urllib.parse.urlencode(query, doseq=True)))


def _segments_from_artifact(payload: dict[str, Any]) -> list[Segment]:
    return [Segment(**item) for item in payload.get("segments") or []]


def _resume_checkpoint(resume_dir: Path | None, destination: Path) -> None:
    if not resume_dir or destination.exists():
        return
    candidate = resume_dir.expanduser().resolve() / "manuscript-checkpoint.json"
    if candidate.is_file():
        shutil.copy2(candidate, destination)


def run(options: Options) -> dict[str, Any]:
    # Keep the accepted Bilibili client behavior frozen behind the first
    # source-adapter boundary. Future sources are selected before entering the
    # shared manuscript core instead of forking this core per website.
    adapter = BilibiliSourceAdapter(BilibiliClient())
    client = adapter.client
    state = state_root()
    vault_root = options.vault.expanduser().resolve()
    vault_root.mkdir(parents=True, exist_ok=True)
    source_dir = state / "tasks" / options.task_key
    work_dir = state / "work" / options.task_key
    source_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)
    job_path = source_dir / "job.json"
    try:
        for storage in {state, vault_root}:
            free = shutil.disk_usage(storage).free
            if free < 5 * 1024**3:
                raise RuntimeError("服务器可用磁盘不足 5GB，已拒绝新任务以避免写满磁盘")
        _progress(options, "正在读取视频信息。")
        if options.resume:
            metadata = load_json(options.resume.resolve() / "metadata.json")
            if not metadata:
                raise RuntimeError("续跑目录缺少 metadata.json")
            info = VideoInfo(**{key: metadata[key] for key in VideoInfo.__dataclass_fields__})
        else:
            info = client.inspect(options.url, options.part)
        full_title = info.title if not info.part_title or info.part_title == info.title else f"{info.title} - {info.part_title}"
        folder = safe_name(f"{options.task_key}-{full_title} [{info.bvid}-p{info.part}]")
        year, month = options.task_date[:4], options.task_date[:7]
        job_dir = vault_root / "Sources" / "Videos" / year / month / folder
        assets_dir = job_dir / "assets"
        assets_dir.mkdir(parents=True, exist_ok=True)
        metadata = info.to_dict()
        metadata.update(
            title=full_title,
            processed_at=datetime.now(timezone.utc).isoformat(),
            task_key=options.task_key,
            task_date=options.task_date,
            pipeline_version=PIPELINE_VERSION,
        )
        atomic_json(source_dir / "metadata.json", metadata)
        atomic_json(job_path, {"status": "running", "stage": "transcript", "warnings": []})

        _progress(options, "正在获取或识别字幕。")
        raw_path = source_dir / "raw-transcript.json"
        raw = load_json(raw_path)
        if not raw and options.resume:
            raw = load_json(options.resume.resolve() / "raw-transcript.json")
            if raw:
                atomic_json(raw_path, raw)
        if raw:
            segments = _segments_from_artifact(raw)
            transcript_meta = raw.get("metadata") or {"source": "resumed"}
        else:
            segments, transcript_meta = client.subtitles(info)
            if not segments:
                segments, transcript_meta = client.ai_transcript(info)
            if not segments:
                audio_path = download_media(
                    _part_url(info.url, info.part), work_dir, audio_only=True,
                    cookies_file=options.cookies_file, client=client, info=info,
                )
                segments, transcript_meta = transcribe(audio_path, options.asr_model, backend=options.asr_backend)
            segments = normalize_segments(segments, info.duration)
            atomic_json(raw_path, {"metadata": transcript_meta, "segments": [segment.to_dict() for segment in segments]})

        _progress(options, "正在清理和重排完整文字稿。")
        atomic_json(job_path, {"status": "running", "stage": "semantic_edit", "warnings": []})
        llm = text_client(options.llm_model)
        checkpoint_path = source_dir / "manuscript-checkpoint.json"
        _resume_checkpoint(options.resume, checkpoint_path)
        plan = create_direct_plan(
            segments,
            llm,
            context=f"标题：{full_title}；UP 主：{info.owner}",
            checkpoint_path=checkpoint_path,
        )

        frames: list[Frame] = []
        visual_meta: dict[str, Any] = {"disabled": options.no_visual}
        _progress(options, "正在提取并匹配关键画面。")
        if not options.no_visual:
            atomic_json(job_path, {"status": "running", "stage": "visual", "warnings": []})
            outline_requests = visual_requests_from_plan(plan, segments)
            visual_plan_warning = ""
            try:
                dedicated_requests = create_visual_request_plan(segments, llm, plan)
            except Exception as exc:
                dedicated_requests = []
                visual_plan_warning = f"独立视觉范围规划失败，使用文章大纲请求（{type(exc).__name__}）"
            visual_requests = merge_visual_requests(outline_requests, dedicated_requests)
            video_path = download_media(
                _part_url(info.url, info.part), work_dir, audio_only=False,
                cookies_file=options.cookies_file, client=client, info=info,
                max_height=options.visual_height,
            )
            frames, extracted_visual_meta = extract_useful_frames(
                video_path,
                assets_dir,
                segments,
                max_frames=options.max_frames,
                visual_requests=visual_requests,
                task_key=options.task_key,
            )
            visual_meta.update(extracted_visual_meta)
            visual_meta["outline_visual_request_count"] = len(outline_requests)
            visual_meta["dedicated_visual_request_count"] = len(dedicated_requests)
            if visual_plan_warning:
                visual_meta["visual_planner_warning"] = visual_plan_warning

        paragraphs, coverage = complete_direct_manuscript(
            segments,
            llm,
            plan,
            context=f"标题：{full_title}；UP 主：{info.owner}",
            frames=frames,
            checkpoint_path=checkpoint_path,
        )
        if coverage.get("missing_ids"):
            raise RuntimeError("文字稿覆盖审计失败，未生成完成状态")

        if frames:
            visual_warnings = enrich_with_visual_evidence(paragraphs, frames, llm)
            try:
                planned = plan_frame_evidence_groups(paragraphs, frames)
                visual_meta["final_frame_upgrade"] = recapture_retained_frames(
                    client,
                    info,
                    [
                        frame
                        for group in planned.values()
                        for frame in group
                        if frame.keep_image
                    ],
                    max_height=options.final_visual_height,
                )
            except Exception as exc:
                visual_meta["final_frame_upgrade"] = {
                    "requested_height": options.final_visual_height,
                    "upgraded_count": 0,
                    "warning": f"最高可用清晰度重新取帧失败（{type(exc).__name__}）",
                }
                coverage["warnings"].append(
                    f"最终高清取帧失败，保留分析帧（{type(exc).__name__}）"
                )
            coverage["warnings"].extend(visual_warnings)
            # Uncertain OCR is never promoted to copyable text: the original
            # frame is retained instead, so it does not downgrade an otherwise
            # faithful manuscript.

        _progress(options, "正在生成 Obsidian 文稿。")
        quality_status = str(coverage.get("quality_status") or "failed")
        if quality_status != "pass":
            raise RuntimeError("文字稿未通过发布门禁，已拒绝写入 Obsidian Vault")
        metadata["quality_status"] = quality_status
        note_path = job_dir / f"{safe_name(full_title)}.md"
        compose_markdown(note_path, metadata, paragraphs, frames)
        kept_frame_count = sum(1 for frame in frames if frame.path and Path(frame.path).is_file())
        for frame in frames:
            frame.path = (
                Path(frame.path).relative_to(job_dir).as_posix()
                if frame.path and Path(frame.path).is_file()
                else ""
            )
        visual_meta["retained_image_count"] = kept_frame_count
        visual_meta["text_only_evidence_count"] = sum(
            1 for frame in frames if frame.extracted_markdown and not frame.keep_image
        )
        write_artifacts(
            source_dir, metadata=metadata, segments=segments, transcript_meta=transcript_meta,
            paragraphs=paragraphs, coverage=coverage, frames=frames, visual_meta=visual_meta,
        )
        update_indexes(vault_root, note_path, metadata)
        status = "complete"
        result = {
            "status": status, "stage": "complete", "note": str(note_path), "job_dir": str(job_dir),
            "source_dir": str(source_dir), "transcript_source": transcript_meta.get("source"),
            "semantic_editing": coverage.get("semantic_editing", False), "frames": kept_frame_count,
            "warnings": coverage.get("warnings", []), "bvid": info.bvid, "part": info.part,
            "title": full_title, "url": info.url,
        }
        atomic_json(job_path, result)
        _progress(options, "处理完成。")
        return result
    except KeyboardInterrupt:
        atomic_json(job_path, {"status": "cancelled", "error": "用户已终止任务"})
        remove_index_entries(vault_root, options.task_key)
        incomplete = locals().get("job_dir")
        if isinstance(incomplete, Path) and incomplete.is_dir():
            shutil.rmtree(incomplete, ignore_errors=True)
        raise
    except Exception as exc:
        atomic_json(job_path, {"status": "failed", "error": str(exc)})
        remove_index_entries(vault_root, options.task_key)
        incomplete = locals().get("job_dir")
        if isinstance(incomplete, Path) and incomplete.is_dir():
            shutil.rmtree(incomplete, ignore_errors=True)
        raise
    finally:
        if not options.keep_video:
            shutil.rmtree(work_dir, ignore_errors=True)
