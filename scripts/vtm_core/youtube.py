from __future__ import annotations

import html
import json
import os
import re
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .models import Frame, Segment
from .sources import SourceReference
from .utils import require_command
from .visual import _run


YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
    "www.youtu.be",
}
YOUTUBE_MEDIA_SUFFIXES = (".googlevideo.com", ".youtube.com", ".googleusercontent.com")
VIDEO_ID_RE = re.compile(r"^[0-9A-Za-z_-]{11}$")


@dataclass(slots=True)
class YouTubeVideoInfo:
    url: str
    video_id: str
    title: str
    duration: float
    owner: str
    channel_id: str = ""
    upload_date: str = ""
    language: str = ""
    thumbnail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _yt_dlp() -> Any:
    try:
        import yt_dlp
    except ImportError as exc:
        raise RuntimeError("yt-dlp is required; install scripts/requirements.txt") from exc
    return yt_dlp


def _allowed_remote_url(value: str) -> str:
    parsed = urllib.parse.urlparse(value)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not any(host == suffix[1:] or host.endswith(suffix) for suffix in YOUTUBE_MEDIA_SUFFIXES):
        raise RuntimeError("Rejected unexpected YouTube media or subtitle host")
    return value


def _cue_time(value: str) -> float:
    fields = value.replace(",", ".").split(":")
    if len(fields) == 2:
        fields.insert(0, "0")
    if len(fields) != 3:
        raise ValueError("invalid WebVTT timestamp")
    return int(fields[0]) * 3600 + int(fields[1]) * 60 + float(fields[2])


def parse_youtube_vtt(text: str) -> list[Segment]:
    segments: list[Segment] = []
    blocks = re.split(r"\n\s*\n", text.replace("\r\n", "\n"))
    for block in blocks:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        timing_index = next((index for index, line in enumerate(lines) if "-->" in line), None)
        if timing_index is None:
            continue
        timing = lines[timing_index].split("-->", 1)
        try:
            start = _cue_time(timing[0].strip())
            end = _cue_time(timing[1].strip().split()[0])
        except (ValueError, IndexError):
            continue
        body = " ".join(lines[timing_index + 1 :])
        body = html.unescape(re.sub(r"<[^>]+>", "", body)).strip()
        if not body:
            continue
        if segments and body == segments[-1].text and start <= segments[-1].end + 0.25:
            segments[-1].end = max(segments[-1].end, end)
            continue
        segments.append(Segment(f"s{len(segments) + 1:06d}", start, end, body))
    return segments


def parse_youtube_json3(text: str) -> list[Segment]:
    payload = json.loads(text)
    segments: list[Segment] = []
    for event in payload.get("events") or []:
        body = "".join(str(item.get("utf8") or "") for item in event.get("segs") or [])
        body = " ".join(body.replace("\n", " ").split())
        if not body:
            continue
        start = float(event.get("tStartMs") or 0) / 1000.0
        duration = float(event.get("dDurationMs") or 0) / 1000.0
        end = start + max(duration, 0.05)
        if segments and body == segments[-1].text and start <= segments[-1].end + 0.25:
            segments[-1].end = max(segments[-1].end, end)
            continue
        segments.append(Segment(f"s{len(segments) + 1:06d}", start, end, body))
    return segments


class YouTubeClient:
    def __init__(self, timeout: float = 20.0):
        self.timeout = timeout
        self._cache: dict[str, dict[str, Any]] = {}

    @staticmethod
    def video_id(value: str) -> str:
        text = str(value or "").strip()
        if VIDEO_ID_RE.fullmatch(text):
            return text
        parsed = urllib.parse.urlparse(text)
        host = (parsed.hostname or "").lower()
        if parsed.scheme not in {"http", "https"} or host not in YOUTUBE_HOSTS:
            raise ValueError("Only public youtube.com and youtu.be HTTP(S) links are accepted")
        candidate = ""
        if host.endswith("youtu.be"):
            candidate = parsed.path.strip("/").split("/", 1)[0]
        elif parsed.path == "/watch":
            candidate = urllib.parse.parse_qs(parsed.query).get("v", [""])[0]
        else:
            parts = [part for part in parsed.path.split("/") if part]
            if len(parts) >= 2 and parts[0] in {"shorts", "live", "embed"}:
                candidate = parts[1]
        if not VIDEO_ID_RE.fullmatch(candidate):
            raise ValueError("Could not find a YouTube video id in the URL")
        return candidate

    @classmethod
    def normalize_input_url(cls, value: str) -> str:
        return f"https://www.youtube.com/watch?v={cls.video_id(value)}"

    def _extract(self, url: str, *, format_selector: str | None = None) -> dict[str, Any]:
        video_id = self.video_id(url)
        cache_key = f"{video_id}:{format_selector or 'metadata'}"
        if cache_key in self._cache:
            return self._cache[cache_key]
        options: dict[str, Any] = {
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "skip_download": True,
            "socket_timeout": self.timeout,
            "retries": 2,
            "extractor_retries": 2,
        }
        if format_selector:
            options["format"] = format_selector
        if os.getenv("VTM_SOURCE_PROXY"):
            options["proxy"] = os.environ["VTM_SOURCE_PROXY"]
        with _yt_dlp().YoutubeDL(options) as downloader:
            payload = downloader.extract_info(self.normalize_input_url(url), download=False)
        if not isinstance(payload, dict) or not payload.get("id"):
            raise RuntimeError("YouTube returned no usable video metadata")
        self._cache[cache_key] = payload
        return payload

    def inspect(self, url: str) -> YouTubeVideoInfo:
        payload = self._extract(url)
        return YouTubeVideoInfo(
            url=self.normalize_input_url(url),
            video_id=str(payload.get("id") or self.video_id(url)),
            title=str(payload.get("title") or payload.get("id") or "YouTube video"),
            duration=float(payload.get("duration") or 0),
            owner=str(payload.get("channel") or payload.get("uploader") or ""),
            channel_id=str(payload.get("channel_id") or ""),
            upload_date=str(payload.get("upload_date") or ""),
            language=str(payload.get("language") or ""),
            thumbnail=str(payload.get("thumbnail") or ""),
        )

    @staticmethod
    def _track(
        tracks: dict[str, Any], preferred_language: str, *, prefer_original: bool = False
    ) -> tuple[str, dict[str, Any]] | None:
        original = sorted(language for language in tracks if language.endswith("-orig"))
        if prefer_original and original:
            languages = [preferred_language, f"{preferred_language}-orig", *original]
        else:
            languages = [preferred_language, *original, "zh-Hans", "zh-CN", "zh", "en", "en-US"]
            languages.extend(sorted(tracks))
        seen: set[str] = set()
        for language in languages:
            if not language or language in seen or language not in tracks:
                continue
            seen.add(language)
            entries = tracks.get(language) or []
            for extension in ("json3", "vtt"):
                for entry in entries:
                    if entry.get("ext") == extension and entry.get("url"):
                        return language, entry
        return None

    def subtitles(self, info: YouTubeVideoInfo) -> tuple[list[Segment], dict[str, Any]]:
        payload = self._extract(info.url)
        for source, tracks, prefer_original in (
            ("youtube_manual_subtitle", payload.get("subtitles") or {}, False),
            ("youtube_automatic_caption", payload.get("automatic_captions") or {}, True),
        ):
            selected = self._track(tracks, info.language, prefer_original=prefer_original)
            if not selected:
                continue
            language, entry = selected
            url = _allowed_remote_url(str(entry["url"]))
            request = urllib.request.Request(url, headers=dict(entry.get("http_headers") or payload.get("http_headers") or {}))
            proxy = os.getenv("VTM_SOURCE_PROXY")
            opener = (
                urllib.request.build_opener(
                    urllib.request.ProxyHandler({"http": proxy, "https": proxy})
                )
                if proxy
                else None
            )
            open_request = opener.open if opener else urllib.request.urlopen
            with open_request(request, timeout=self.timeout) as response:
                final_url = _allowed_remote_url(response.geturl())
                del final_url
                text = response.read().decode("utf-8", errors="replace")
            extension = str(entry.get("ext") or "")
            segments = parse_youtube_json3(text) if extension == "json3" else parse_youtube_vtt(text)
            if segments:
                return segments, {"source": source, "language": language, "format": extension}
        return [], {"warning": "No public YouTube subtitle track is available"}

    def final_stream(self, info: YouTubeVideoInfo, max_height: int) -> dict[str, Any]:
        payload = self._extract(
            info.url,
            format_selector=f"bestvideo[height<={max_height}]/best[height<={max_height}]",
        )
        url = _allowed_remote_url(str(payload.get("url") or ""))
        return {
            "url": url,
            "height": int(payload.get("height") or 0),
            "headers": dict(payload.get("http_headers") or {}),
        }


class YouTubeSourceAdapter:
    platform = "youtube"
    source_kind = "video"

    def __init__(self, client: YouTubeClient | None = None):
        self.client = client or YouTubeClient()

    def can_handle(self, value: str) -> bool:
        try:
            self.client.video_id(value)
            return True
        except ValueError:
            return False

    def normalize_input_url(self, value: str) -> str:
        return self.client.normalize_input_url(value)

    def canonicalize_input(self, value: str) -> str:
        return self.normalize_input_url(value)

    def source_id_from_url(self, value: str) -> str:
        return self.client.video_id(value)

    def selector_from_url(self, value: str, explicit: int | None = None) -> None:
        del value, explicit
        return None

    def inspect(self, value: str, selector: int | None = None) -> YouTubeVideoInfo:
        del selector
        return self.client.inspect(value)

    def reference(self, inspected: YouTubeVideoInfo) -> SourceReference:
        return SourceReference(
            platform=self.platform,
            source_kind=self.source_kind,
            source_id=inspected.video_id,
            canonical_url=inspected.url,
            title=inspected.title,
            author=inspected.owner,
            duration=inspected.duration,
        )

    def restore_info(self, metadata: dict[str, Any]) -> YouTubeVideoInfo:
        return YouTubeVideoInfo(**{key: metadata[key] for key in YouTubeVideoInfo.__dataclass_fields__})

    def metadata(self, inspected: YouTubeVideoInfo) -> dict[str, Any]:
        payload = inspected.to_dict()
        payload.update(self.reference(inspected).to_dict())
        return payload

    def primary_transcript(self, inspected: YouTubeVideoInfo) -> tuple[list[Segment], dict[str, Any]]:
        return self.client.subtitles(inspected)

    def secondary_transcript(self, inspected: YouTubeVideoInfo) -> tuple[list[Segment], dict[str, Any]]:
        del inspected
        return [], {"warning": "YouTube has no secondary transcript source"}

    def download_audio(
        self, inspected: YouTubeVideoInfo, work_dir: Path, *, cookies_file: Path | None = None
    ) -> Path:
        from .media import download_media

        return download_media(inspected.url, work_dir, audio_only=True, cookies_file=cookies_file)

    def download_analysis_video(
        self,
        inspected: YouTubeVideoInfo,
        work_dir: Path,
        *,
        cookies_file: Path | None = None,
        max_height: int = 720,
    ) -> Path:
        from .media import download_media

        return download_media(
            inspected.url,
            work_dir,
            audio_only=False,
            cookies_file=cookies_file,
            max_height=max_height,
        )

    def recapture_frames(
        self, inspected: YouTubeVideoInfo, frames: list[Frame], *, max_height: int = 1080
    ) -> dict[str, int | str]:
        retained = [frame for frame in frames if frame.keep_image and frame.path]
        if not retained:
            return {"requested_height": max_height, "actual_height": 0, "upgraded_count": 0}
        stream = self.client.final_stream(inspected, max_height)
        actual_height = int(stream.get("height") or 0)
        headers = "".join(f"{key}: {value}\r\n" for key, value in stream["headers"].items())
        ffmpeg = require_command("ffmpeg")
        upgraded = 0
        for frame in retained:
            output = Path(frame.path)
            temporary = output.with_name(output.stem + ".highres.png")
            try:
                command = [ffmpeg, "-hide_banner", "-loglevel", "error"]
                if os.getenv("VTM_SOURCE_PROXY"):
                    command.extend(["-http_proxy", os.environ["VTM_SOURCE_PROXY"]])
                if headers:
                    command.extend(["-headers", headers])
                command.extend(
                    [
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
                    ]
                )
                _run(command, timeout=90)
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

    def context(self, inspected: YouTubeVideoInfo) -> str:
        return f"标题：{inspected.title}；频道：{inspected.owner}"

    def folder_marker(self, inspected: YouTubeVideoInfo) -> str:
        return f"YT-{inspected.video_id}"
