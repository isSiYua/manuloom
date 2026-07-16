from __future__ import annotations

from pathlib import Path
from typing import Any
from contextlib import contextmanager

from .models import Frame, Paragraph
from .utils import atomic_json, timestamp
from .visual import hash_distance


NEARBY_DUPLICATE_SECONDS = 12.0
NEARBY_DUPLICATE_HASH_DISTANCE = 0.12


def yaml_scalar(value: Any) -> str:
    text = str(value if value is not None else "")
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ") + '"'


def plan_frame_evidence_groups(
    paragraphs: list[Paragraph], frames: list[Frame]
) -> dict[int, list[Frame]]:
    """Retain every distinct visual item aligned to each paragraph."""
    had_generated_notes = any(frame.extracted_markdown.strip() for frame in frames)
    aligned = sorted(
        [frame for frame in frames if frame.paragraph_index is not None],
        key=lambda frame: frame.timestamp,
    )

    # Scene extraction intentionally uses a strict global threshold so that it
    # does not lose small but meaningful UI changes. Once frames have been
    # aligned to adjacent prose, apply a second, time-bounded comparison: two
    # near-identical screens shown seconds apart are redundant evidence, even
    # when they were assigned to neighbouring paragraphs.
    confidence_rank = {"high": 3, "medium": 2, "low": 1, "unknown": 0}
    completeness_rank = {"complete": 3, "partial": 2, "unknown": 0}

    def evidence_score(frame: Frame) -> tuple[float, ...]:
        return (
            confidence_rank.get(frame.evidence_confidence, 0),
            completeness_rank.get(frame.evidence_completeness, 0),
            frame.ocr_confidence,
            len(frame.vision_description),
            frame.contrast,
            frame.brightness,
            frame.timestamp,
        )

    kept: list[Frame] = []
    for frame in aligned:
        duplicate: Frame | None = None
        for existing in reversed(kept):
            gap = frame.timestamp - existing.timestamp
            if gap > NEARBY_DUPLICATE_SECONDS:
                break
            if (
                frame.ahash
                and existing.ahash
                and hash_distance(frame.ahash, existing.ahash)
                <= NEARBY_DUPLICATE_HASH_DISTANCE
            ):
                duplicate = existing
                break
        if duplicate is None:
            kept.append(frame)
            continue
        if evidence_score(frame) > evidence_score(duplicate):
            duplicate.extracted_markdown = ""
            kept[kept.index(duplicate)] = frame
        else:
            frame.extracted_markdown = ""

    groups: dict[int, list[Frame]] = {}
    for frame in sorted(kept, key=lambda item: item.timestamp):
        paragraph_index = frame.paragraph_index
        if paragraph_index is None or not 0 <= paragraph_index < len(paragraphs):
            continue
        groups.setdefault(paragraph_index, []).append(frame)

    # Visual text belongs to the frame that proved it.  Rebuild generated notes
    # after deduplication so dropping one duplicate never erases an unrelated
    # list, code block, formula or slide transcription from the same paragraph.
    if had_generated_notes:
        for paragraph_index, paragraph in enumerate(paragraphs):
            notes: list[str] = []
            for frame in groups.get(paragraph_index, []):
                note = frame.extracted_markdown.strip()
                if note and note not in notes:
                    notes.append(note)
            paragraph.visual_note = "\n\n".join(notes) or None
    return groups


def plan_frame_evidence(
    paragraphs: list[Paragraph], frames: list[Frame]
) -> dict[int, Frame]:
    """Backward-compatible strongest-frame view used by older integrations."""
    groups = plan_frame_evidence_groups(paragraphs, frames)
    return {paragraph_index: group[0] for paragraph_index, group in groups.items() if group}


def compose_markdown(
    note_path: Path,
    metadata: dict[str, Any],
    paragraphs: list[Paragraph],
    frames: list[Frame],
) -> None:
    note_path.parent.mkdir(parents=True, exist_ok=True)
    platform = str(metadata.get("platform") or ("bilibili" if metadata.get("bvid") else "unknown"))
    source_id = str(metadata.get("source_id") or metadata.get("bvid") or "")
    creator = str(metadata.get("author") or metadata.get("owner") or "")
    lines = [
        "---",
        f"title: {yaml_scalar(metadata.get('title'))}",
        f"source: {yaml_scalar(metadata.get('url'))}",
    ]
    if platform == "bilibili":
        lines.extend(
            [
                f"bvid: {yaml_scalar(metadata.get('bvid'))}",
                f"part: {metadata.get('part', 1)}",
            ]
        )
    else:
        lines.extend(
            [
                f"platform: {yaml_scalar(platform)}",
                f"source_id: {yaml_scalar(source_id)}",
            ]
        )
    lines.extend([
        f"creator: {yaml_scalar(creator)}",
        f"task: {yaml_scalar(metadata.get('task_key'))}",
        f"created: {yaml_scalar(metadata.get('task_date'))}",
        f"status: {yaml_scalar(metadata.get('quality_status', 'complete'))}",
        f"pipeline_version: {yaml_scalar(metadata.get('pipeline_version', 'unknown'))}",
        'type: "video-manuscript"',
        f"tags: [video-manuscript, {platform}]",
        "---",
        "",
        f"# {metadata.get('title') or source_id}",
        "",
        (
            f"> 来源：[Bilibili]({metadata.get('url')}) · UP主：{creator or '未知'}"
            if platform == "bilibili"
            else f"> 来源：[{platform}]({metadata.get('url')}) · 作者/频道：{creator or '未知'}"
        ),
        "",
    ])
    frame_plan = plan_frame_evidence_groups(paragraphs, frames)
    for paragraph_index, paragraph in enumerate(paragraphs):
        if paragraph.heading:
            lines.extend([f"## {paragraph.heading}", ""])
        if paragraph.subheading:
            lines.extend([f"### {paragraph.subheading}", ""])
        lines.extend([paragraph.text, ""])
        if paragraph.visual_note:
            lines.extend(["> [!info] 画面补充"])
            lines.extend(f"> {line}" for line in paragraph.visual_note.splitlines())
            lines.append("")
        # Place every distinct, irreplaceable item immediately after the passage
        # it explains.  Complete text-only frames are represented by the callout
        # above and removed; diagrams, charts, UI and partial/dense screens remain.
        for frame in frame_plan.get(paragraph_index, []):
            if not frame.keep_image:
                Path(frame.path).unlink(missing_ok=True)
                continue
            relative = Path(frame.path).relative_to(note_path.parent).as_posix()
            # Low/medium-confidence vision descriptions are deliberately not
            # promoted to alt text: alt text is still published prose and must
            # obey the same evidence standard as a visible callout.
            alt = (
                frame.vision_description
                if frame.evidence_confidence == "high"
                and frame.evidence_completeness == "complete"
                and frame.ocr_confidence >= 50
                and frame.vision_description
                and len(frame.vision_description) <= 120
                else f"{frame.content_kind if frame.content_kind != 'other' else '视频关键画面'} {timestamp(frame.timestamp)}"
            )
            alt = alt.replace("[", "").replace("]", "")[:160]
            lines.extend(
                [
                    f"![{alt}]({relative})",
                    "",
                    f"*画面时间：{timestamp(frame.timestamp)}*",
                    "",
                ]
            )
    planned_ids = {id(frame) for group in frame_plan.values() for frame in group}
    for frame in frames:
        if id(frame) in planned_ids and frame.keep_image:
            continue
        Path(frame.path).unlink(missing_ok=True)
    note_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


@contextmanager
def _index_lock(vault: Path):
    lock_path = vault / "Indexes" / ".video-index.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock:
        try:
            import fcntl
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        except (ImportError, OSError):
            pass
        yield


def update_indexes(vault: Path, note_path: Path, metadata: dict[str, Any]) -> None:
    task_key = str(metadata.get("task_key") or "")
    day = str(metadata.get("task_date") or "")
    title = str(metadata.get("title") or note_path.stem)
    creator = str(metadata.get("author") or metadata.get("owner") or "未知作者")
    target = note_path.relative_to(vault).with_suffix("").as_posix()
    entry = f"- [[{target}|{title}]] · `{task_key}` · {creator}"
    with _index_lock(vault):
        master = vault / "Indexes" / "视频资料库.md"
        daily = vault / "Indexes" / "Daily" / f"{day}.md"
        daily.parent.mkdir(parents=True, exist_ok=True)
        if not master.exists():
            master.write_text("# 视频资料库\n\n", encoding="utf-8")
        if entry not in master.read_text(encoding="utf-8"):
            with master.open("a", encoding="utf-8") as handle:
                handle.write(entry + "\n")
        if not daily.exists():
            daily.write_text(f"# {day} 视频笔记\n\n", encoding="utf-8")
        if entry not in daily.read_text(encoding="utf-8"):
            with daily.open("a", encoding="utf-8") as handle:
                handle.write(entry + "\n")


def remove_index_entries(vault: Path, task_key: str) -> None:
    """Remove every index row for one task after deletion or failed publication."""
    indexes = vault / "Indexes"
    if not task_key or not indexes.is_dir():
        return
    with _index_lock(vault):
        for index in indexes.rglob("*.md"):
            original = index.read_text(encoding="utf-8")
            filtered = "\n".join(
                line for line in original.splitlines() if task_key not in line
            ).rstrip()
            replacement = filtered + "\n" if filtered else ""
            if replacement != original:
                index.write_text(replacement, encoding="utf-8")


def write_artifacts(
    source_dir: Path,
    *,
    metadata: dict[str, Any],
    segments: list[Any],
    transcript_meta: dict[str, Any],
    paragraphs: list[Paragraph],
    coverage: dict[str, Any],
    frames: list[Frame],
    visual_meta: dict[str, Any],
) -> None:
    atomic_json(source_dir / "metadata.json", metadata)
    atomic_json(
        source_dir / "raw-transcript.json",
        {"metadata": transcript_meta, "segments": [segment.to_dict() for segment in segments]},
    )
    atomic_json(
        source_dir / "clean-transcript.json",
        {"paragraphs": [paragraph.to_dict() for paragraph in paragraphs]},
    )
    atomic_json(source_dir / "coverage.json", coverage)
    if isinstance(coverage.get("information_units"), list):
        atomic_json(
            source_dir / "information-units.json",
            {"units": coverage["information_units"]},
        )
    if isinstance(coverage.get("outline"), list):
        atomic_json(source_dir / "outline.json", {"sections": coverage["outline"]})
    atomic_json(
        source_dir / "visual-manifest.json",
        {"metadata": visual_meta, "frames": [frame.to_dict() for frame in frames]},
    )
