from __future__ import annotations

import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from .bilibili import ALLOWED_HOSTS, BilibiliClient, VideoInfo
from .models import Frame, Segment


@dataclass(frozen=True, slots=True)
class SourceReference:
    """Stable source identity shared by tasks, adapters, and renderers."""

    platform: str
    source_kind: str
    source_id: str
    canonical_url: str
    title: str
    author: str = ""
    part: int | None = None
    duration: float | None = None

    @property
    def source_key(self) -> str:
        suffix = f":p{self.part}" if self.part is not None else ""
        return f"{self.platform}:{self.source_id}{suffix}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "platform": self.platform,
            "source_kind": self.source_kind,
            "source_id": self.source_id,
            "source_key": self.source_key,
            "canonical_url": self.canonical_url,
            "title": self.title,
            "author": self.author,
            "part": self.part,
            "duration": self.duration,
        }


@runtime_checkable
class SourceAdapter(Protocol):
    """Small discovery boundary implemented by every content source.

    Acquisition capabilities deliberately stay on more specific video or
    document protocols.  A generic adapter must first be able to identify and
    canonicalize its source without making the manuscript core platform-aware.
    """

    platform: str
    source_kind: str

    def can_handle(self, value: str) -> bool: ...

    def normalize_input_url(self, value: str) -> str: ...

    def canonicalize_input(self, value: str) -> str: ...

    def source_id_from_url(self, value: str) -> str: ...

    def selector_from_url(self, value: str, explicit: int | None = None) -> int | None: ...

    def inspect(self, value: str, selector: int | None = None) -> Any: ...

    def reference(self, inspected: Any) -> SourceReference: ...


@runtime_checkable
class VideoSourceAdapter(SourceAdapter, Protocol):
    def restore_info(self, metadata: dict[str, Any]) -> Any: ...

    def metadata(self, inspected: Any) -> dict[str, Any]: ...

    def primary_transcript(self, inspected: Any) -> tuple[list[Segment], dict[str, Any]]: ...

    def secondary_transcript(self, inspected: Any) -> tuple[list[Segment], dict[str, Any]]: ...

    def download_audio(
        self, inspected: Any, work_dir: Path, *, cookies_file: Path | None = None
    ) -> Path: ...

    def download_analysis_video(
        self,
        inspected: Any,
        work_dir: Path,
        *,
        cookies_file: Path | None = None,
        max_height: int = 720,
    ) -> Path: ...

    def recapture_frames(
        self, inspected: Any, frames: list[Frame], *, max_height: int = 1080
    ) -> dict[str, int | str]: ...

    def context(self, inspected: Any) -> str: ...

    def folder_marker(self, inspected: Any) -> str: ...


@runtime_checkable
class DocumentSourceAdapter(SourceAdapter, Protocol):
    def restore_info(self, metadata: dict[str, Any]) -> Any: ...

    def metadata(self, inspected: Any) -> dict[str, Any]: ...

    def content_segments(self, inspected: Any) -> tuple[list[Segment], dict[str, Any]]: ...

    def download_images(
        self, inspected: Any, assets_dir: Path, *, limit: int = 60
    ) -> list[Frame]: ...

    def context(self, inspected: Any) -> str: ...

    def folder_marker(self, inspected: Any) -> str: ...


class BilibiliSourceAdapter:
    """Compatibility adapter around the frozen Bilibili 1.0.0 client."""

    platform = "bilibili"
    source_kind = "video"

    def __init__(self, client: BilibiliClient | None = None):
        self.client = client or BilibiliClient()

    def can_handle(self, value: str) -> bool:
        text = str(value or "").strip()
        normalized = self.client.normalize_input_url(text)
        parsed = urllib.parse.urlparse(normalized)
        return (parsed.hostname or "").lower() in ALLOWED_HOSTS

    def normalize_input_url(self, value: str) -> str:
        return self.client.normalize_input_url(value)

    def canonicalize_input(self, value: str) -> str:
        normalized = self.normalize_input_url(value)
        parsed = urllib.parse.urlparse(normalized)
        if (parsed.hostname or "").lower() in {"b23.tv", "www.b23.tv"}:
            return self.client.resolve(normalized)
        return normalized

    def source_id_from_url(self, value: str) -> str:
        return self.client.extract_bvid(value)

    def selector_from_url(self, value: str, explicit: int | None = None) -> int:
        return self.client.extract_part(value, explicit)

    def inspect(self, value: str, selector: int | None = None) -> VideoInfo:
        return self.client.inspect(value, selector)

    def reference(self, inspected: VideoInfo) -> SourceReference:
        title = (
            inspected.title
            if not inspected.part_title or inspected.part_title == inspected.title
            else f"{inspected.title} - {inspected.part_title}"
        )
        return SourceReference(
            platform=self.platform,
            source_kind=self.source_kind,
            source_id=inspected.bvid,
            canonical_url=inspected.url,
            title=title,
            author=inspected.owner,
            part=inspected.part,
            duration=inspected.duration,
        )

    def restore_info(self, metadata: dict[str, Any]) -> VideoInfo:
        return VideoInfo(**{key: metadata[key] for key in VideoInfo.__dataclass_fields__})

    def metadata(self, inspected: VideoInfo) -> dict[str, Any]:
        payload = inspected.to_dict()
        payload.update(self.reference(inspected).to_dict())
        payload["owner"] = inspected.owner
        return payload

    def primary_transcript(self, inspected: VideoInfo) -> tuple[list[Segment], dict[str, Any]]:
        return self.client.subtitles(inspected)

    def secondary_transcript(self, inspected: VideoInfo) -> tuple[list[Segment], dict[str, Any]]:
        return self.client.ai_transcript(inspected)

    @staticmethod
    def _part_url(url: str, part: int) -> str:
        parsed = urllib.parse.urlparse(url)
        query = urllib.parse.parse_qs(parsed.query)
        query["p"] = [str(part)]
        return urllib.parse.urlunparse(parsed._replace(query=urllib.parse.urlencode(query, doseq=True)))

    def download_audio(
        self, inspected: VideoInfo, work_dir: Path, *, cookies_file: Path | None = None
    ) -> Path:
        from .media import download_media

        return download_media(
            self._part_url(inspected.url, inspected.part),
            work_dir,
            audio_only=True,
            cookies_file=cookies_file,
            client=self.client,
            info=inspected,
        )

    def download_analysis_video(
        self,
        inspected: VideoInfo,
        work_dir: Path,
        *,
        cookies_file: Path | None = None,
        max_height: int = 720,
    ) -> Path:
        from .media import download_media

        return download_media(
            self._part_url(inspected.url, inspected.part),
            work_dir,
            audio_only=False,
            cookies_file=cookies_file,
            client=self.client,
            info=inspected,
            max_height=max_height,
        )

    def recapture_frames(
        self, inspected: VideoInfo, frames: list[Frame], *, max_height: int = 1080
    ) -> dict[str, int | str]:
        from .visual import recapture_retained_frames

        return recapture_retained_frames(self.client, inspected, frames, max_height=max_height)

    def context(self, inspected: VideoInfo) -> str:
        reference = self.reference(inspected)
        return f"标题：{reference.title}；UP 主：{reference.author}"

    def folder_marker(self, inspected: VideoInfo) -> str:
        return f"{inspected.bvid}-p{inspected.part}"


def source_adapters() -> tuple[SourceAdapter, ...]:
    """Return installed adapters in deterministic matching order."""

    from .youtube import YouTubeSourceAdapter
    from .web import GenericWebSourceAdapter

    return (BilibiliSourceAdapter(), YouTubeSourceAdapter(), GenericWebSourceAdapter())


def adapter_by_platform(platform: str) -> SourceAdapter:
    normalized = str(platform or "").strip().lower()
    for adapter in source_adapters():
        if adapter.platform == normalized:
            return adapter
    raise KeyError(f"当前尚未安装平台适配器：{platform}")


def adapter_for(value: str) -> SourceAdapter:
    matches = [adapter for adapter in source_adapters() if adapter.can_handle(value)]
    if not matches:
        raise ValueError("当前尚未安装可处理该链接的来源适配器")
    if len(matches) > 1:
        names = ", ".join(adapter.platform for adapter in matches)
        raise RuntimeError(f"多个来源适配器同时匹配该链接：{names}")
    return matches[0]
