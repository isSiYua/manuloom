from __future__ import annotations

import urllib.parse
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from .bilibili import ALLOWED_HOSTS, BilibiliClient, VideoInfo


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

    def inspect(self, value: str, selector: int | None = None) -> Any: ...

    def reference(self, inspected: Any) -> SourceReference: ...


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


def source_adapters() -> tuple[SourceAdapter, ...]:
    """Return installed adapters in deterministic matching order."""

    return (BilibiliSourceAdapter(),)


def adapter_for(value: str) -> SourceAdapter:
    matches = [adapter for adapter in source_adapters() if adapter.can_handle(value)]
    if not matches:
        raise ValueError("当前尚未安装可处理该链接的来源适配器")
    if len(matches) > 1:
        names = ", ".join(adapter.platform for adapter in matches)
        raise RuntimeError(f"多个来源适配器同时匹配该链接：{names}")
    return matches[0]
