from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class Segment:
    id: str
    start: float
    end: float
    text: str
    locator_kind: str = "time"

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        if self.locator_kind == "time":
            payload.pop("locator_kind")
        return payload


@dataclass(slots=True)
class Paragraph:
    source_ids: list[str]
    text: str
    start: float | None = None
    end: float | None = None
    heading: str | None = None
    visual_note: str | None = None
    subheading: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class InformationUnit:
    id: str
    source_ids: list[str]
    start: float
    end: float
    action: str
    kind: str
    topic: str
    text: str
    details: list[str] = field(default_factory=list)
    exact_anchors: list[str] = field(default_factory=list)
    drop_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class OutlineSection:
    id: str
    title: str
    unit_ids: list[str]
    objective: str = ""
    format_hint: str = "prose"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class Frame:
    timestamp: float
    path: str
    source_ids: list[str] = field(default_factory=list)
    ocr_text: str = ""
    ocr_confidence: float = 0.0
    vision_description: str = ""
    ahash: str = ""
    brightness: float = 0.0
    contrast: float = 0.0
    paragraph_index: int | None = None
    content_kind: str = "other"
    keep_image: bool = True
    extracted_markdown: str = ""
    evidence_confidence: str = "unknown"
    evidence_completeness: str = "unknown"
    information_density: str = "unknown"
    information_gain: str = "unknown"
    final_height: int | None = None
    media_kind: str = "video_frame"
    locator_label: str = ""
    source_url: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        if self.media_kind == "video_frame":
            payload.pop("media_kind")
        if not self.locator_label:
            payload.pop("locator_label")
        if not self.source_url:
            payload.pop("source_url")
        return payload
