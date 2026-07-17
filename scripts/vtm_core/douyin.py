"""Public Douyin video acquisition adapter.

The public share-page routing and ``_ROUTER_DATA`` mapping are adapted from
``JNHFlow21/social-post-extractor-mcp`` at commit
``72718caf3e7bb08a8bf4b990d074a53aec5bd5b9`` (Apache-2.0).  This version uses
the standard library, accepts only single public videos, bounds every response,
validates redirects/CDN hosts, and hands media to this project's existing local
ASR and manuscript pipeline.  See ``licenses/Apache-2.0.txt`` and
``references/third-party-notices.md``.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import Frame, Segment
from .sources import SourceReference
from .utils import require_command
from .visual import _run


DOUYIN_HOSTS = {
    "douyin.com",
    "www.douyin.com",
    "v.douyin.com",
    "iesdouyin.com",
    "www.iesdouyin.com",
    "v.iesdouyin.com",
}
DOUYIN_MEDIA_SUFFIXES = (
    ".snssdk.com",
    ".douyinvod.com",
    ".douyinpic.com",
    ".bytecdn.cn",
    ".bytecdn.com",
    ".byteimg.com",
    ".zjcdn.com",
)
DOUYIN_MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 "
    "Mobile/15E148 Safari/604.1"
)
MAX_SHARE_HTML_BYTES = 8 * 1024 * 1024
MAX_VIDEO_BYTES = 2 * 1024 * 1024 * 1024
_URL_RE = re.compile(r"https?://[^\s<>\"']+")
_VIDEO_RE = re.compile(r"/(?:share/)?video/(\d+)(?:/|$)")
_ROUTER_DATA_RE = re.compile(r"window\._ROUTER_DATA\s*=\s*(.*?)</script>", re.DOTALL)


def _host_matches(host: str, suffixes: tuple[str, ...]) -> bool:
    return any(host == suffix[1:] or host.endswith(suffix) for suffix in suffixes)


def _first_url(value: str) -> str:
    match = _URL_RE.search(str(value or "").strip())
    if not match:
        raise ValueError("没有在抖音分享内容中找到 HTTP(S) 链接")
    return match.group(0).rstrip(".,;!?)）】]，。；！")


def _platform_url(value: str) -> str:
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme not in {"http", "https"} or (parsed.hostname or "").lower() not in DOUYIN_HOSTS:
        raise ValueError("只支持公开 douyin.com 或 iesdouyin.com 视频链接")
    if parsed.username or parsed.password:
        raise ValueError("抖音链接不能包含用户名或密码")
    return value


def _media_url(value: str) -> str:
    parsed = urllib.parse.urlparse(str(value or ""))
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not _host_matches(host, DOUYIN_MEDIA_SUFFIXES):
        raise RuntimeError("抖音返回了不受信任的媒体主机")
    return value


class _DouyinRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self, *, media: bool = False):
        self.media = media
        super().__init__()

    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> Any:
        (_media_url if self.media else _platform_url)(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _opener(*, media: bool = False) -> urllib.request.OpenerDirector:
    handlers: list[Any] = [_DouyinRedirectHandler(media=media)]
    proxy = os.getenv("VTM_SOURCE_PROXY", "").strip()
    if proxy:
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    return urllib.request.build_opener(*handlers)


def _published_at(value: Any) -> str:
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        return ""
    if seconds <= 0:
        return ""
    return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat()


def _first_media_url(value: Any) -> str:
    for item in value if isinstance(value, list) else []:
        candidate = str(item or "").strip()
        if candidate:
            if candidate.startswith("http://"):
                candidate = "https://" + candidate[7:]
            return candidate
    return ""


@dataclass(slots=True)
class DouyinVideoInfo:
    url: str
    video_id: str
    title: str
    duration: float
    owner: str
    cover: str
    width: int
    height: int
    published_at: str
    extraction_engine: str = "social-post-extractor-mcp-router-data"
    video_url: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def parse_douyin_router_data(payload: dict[str, Any], video_id: str) -> DouyinVideoInfo:
    """Map the upstream public share payload into the project's video model."""

    loader_data = payload.get("loaderData") if isinstance(payload, dict) else None
    if not isinstance(loader_data, dict):
        raise RuntimeError("抖音公开页面缺少 loaderData")
    video_info: dict[str, Any] | None = None
    for page in loader_data.values():
        if isinstance(page, dict) and isinstance(page.get("videoInfoRes"), dict):
            video_info = page["videoInfoRes"]
            break
    items = video_info.get("item_list") if video_info else None
    item = items[0] if isinstance(items, list) and items and isinstance(items[0], dict) else None
    if not isinstance(item, dict):
        raise RuntimeError("抖音公开页面没有返回视频详情，可能已删除或受到风控")

    actual_id = str(item.get("aweme_id") or video_id)
    video = item.get("video") if isinstance(item.get("video"), dict) else {}
    play_addr = video.get("play_addr") if isinstance(video.get("play_addr"), dict) else {}
    video_url = _first_media_url(play_addr.get("url_list"))
    if not video_url:
        if item.get("images") or item.get("image_post_info"):
            raise RuntimeError("该链接是抖音图文作品；当前阶段只处理抖音视频")
        raise RuntimeError("抖音公开页面没有返回可用视频地址")
    video_url = _media_url(video_url)

    author = item.get("author") if isinstance(item.get("author"), dict) else {}
    cover = video.get("cover") if isinstance(video.get("cover"), dict) else {}
    duration_ms = video.get("duration")
    try:
        duration = float(duration_ms or 0)
    except (TypeError, ValueError):
        duration = 0.0
    if duration > 1000:
        duration /= 1000.0
    title = str(item.get("desc") or f"抖音视频 {actual_id}").strip()
    return DouyinVideoInfo(
        url=f"https://www.douyin.com/video/{actual_id}",
        video_id=actual_id,
        title=title,
        duration=duration,
        owner=str(author.get("nickname") or author.get("unique_id") or "").strip(),
        cover=_first_media_url(cover.get("url_list")),
        width=int(video.get("width") or 0),
        height=int(video.get("height") or 0),
        published_at=_published_at(item.get("create_time")),
        video_url=video_url,
    )


class DouyinClient:
    def __init__(self, timeout: float = 30.0):
        self.timeout = timeout

    def _fetch_html(self, url: str) -> tuple[str, str]:
        request = urllib.request.Request(
            _platform_url(url),
            headers={"User-Agent": DOUYIN_MOBILE_UA, "Accept": "text/html,*/*"},
        )
        try:
            with _opener().open(request, timeout=self.timeout) as response:
                final_url = _platform_url(response.geturl())
                declared = int(response.headers.get("Content-Length") or 0)
                if declared > MAX_SHARE_HTML_BYTES:
                    raise RuntimeError("抖音分享页面超过安全大小限制")
                body = response.read(MAX_SHARE_HTML_BYTES + 1)
                if len(body) > MAX_SHARE_HTML_BYTES:
                    raise RuntimeError("抖音分享页面超过安全大小限制")
                charset = response.headers.get_content_charset() or "utf-8"
        except (urllib.error.URLError, TimeoutError) as exc:
            raise RuntimeError("连接抖音公开页面失败") from exc
        try:
            return body.decode(charset, errors="replace"), final_url
        except LookupError:
            return body.decode("utf-8", errors="replace"), final_url

    @staticmethod
    def video_id(value: str) -> str:
        url = _platform_url(_first_url(value))
        match = _VIDEO_RE.search(urllib.parse.urlparse(url).path)
        if not match:
            raise ValueError("抖音短链接尚未解析，无法取得视频 ID")
        return match.group(1)

    def canonicalize(self, value: str) -> str:
        source = _platform_url(_first_url(value))
        match = _VIDEO_RE.search(urllib.parse.urlparse(source).path)
        if match:
            return f"https://www.douyin.com/video/{match.group(1)}"
        _html, resolved = self._fetch_html(source)
        match = _VIDEO_RE.search(urllib.parse.urlparse(resolved).path)
        if not match:
            raise ValueError("抖音分享链接没有解析到单个视频")
        return f"https://www.douyin.com/video/{match.group(1)}"

    def inspect(self, value: str) -> DouyinVideoInfo:
        canonical = self.canonicalize(value)
        video_id = self.video_id(canonical)
        html, _final = self._fetch_html(f"https://www.iesdouyin.com/share/video/{video_id}")
        match = _ROUTER_DATA_RE.search(html)
        if not match:
            raise RuntimeError("抖音公开页面中没有找到 _ROUTER_DATA，页面结构可能已变化")
        try:
            payload = json.loads(match.group(1).strip())
        except json.JSONDecodeError as exc:
            raise RuntimeError("抖音公开页面的 _ROUTER_DATA 无法解析") from exc
        return parse_douyin_router_data(payload, video_id)


def _download_public_video(url: str, destination: Path, referer: str) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".part")
    temporary.unlink(missing_ok=True)
    request = urllib.request.Request(
        _media_url(url),
        headers={
            "User-Agent": DOUYIN_MOBILE_UA,
            "Referer": referer,
            "Accept": "video/*,application/octet-stream;q=0.9,*/*;q=0.1",
        },
    )
    try:
        with _opener(media=True).open(request, timeout=90) as response:
            _media_url(response.geturl())
            content_type = response.headers.get_content_type().lower()
            if not (content_type.startswith("video/") or content_type == "application/octet-stream"):
                raise RuntimeError(f"抖音媒体返回了不支持的内容类型：{content_type}")
            declared = int(response.headers.get("Content-Length") or 0)
            if declared > MAX_VIDEO_BYTES:
                raise RuntimeError("抖音视频超过 2GB 安全上限")
            total = 0
            with temporary.open("wb") as handle:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_VIDEO_BYTES:
                        raise RuntimeError("抖音视频超过 2GB 安全上限")
                    handle.write(chunk)
        if not temporary.is_file() or temporary.stat().st_size == 0:
            raise RuntimeError("抖音视频下载结果为空")
        temporary.replace(destination)
        return destination
    finally:
        temporary.unlink(missing_ok=True)


class DouyinSourceAdapter:
    platform = "douyin"
    source_kind = "video"

    def __init__(self, client: DouyinClient | None = None):
        self.client = client or DouyinClient()
        self._source_path: Path | None = None

    def can_handle(self, value: str) -> bool:
        try:
            url = _platform_url(_first_url(value))
        except ValueError:
            return False
        return (urllib.parse.urlparse(url).hostname or "").lower() in DOUYIN_HOSTS

    def normalize_input_url(self, value: str) -> str:
        return self.client.canonicalize(value)

    def canonicalize_input(self, value: str) -> str:
        return self.normalize_input_url(value)

    def source_id_from_url(self, value: str) -> str:
        return self.client.video_id(value)

    def selector_from_url(self, value: str, explicit: int | None = None) -> None:
        del value, explicit
        return None

    def inspect(self, value: str, selector: int | None = None) -> DouyinVideoInfo:
        del selector
        return self.client.inspect(value)

    def reference(self, inspected: DouyinVideoInfo) -> SourceReference:
        return SourceReference(
            platform=self.platform,
            source_kind=self.source_kind,
            source_id=inspected.video_id,
            canonical_url=inspected.url,
            title=inspected.title,
            author=inspected.owner,
            duration=inspected.duration,
        )

    def restore_info(self, metadata: dict[str, Any]) -> DouyinVideoInfo:
        values: dict[str, Any] = {}
        for item in fields(DouyinVideoInfo):
            if item.name in metadata:
                values[item.name] = metadata[item.name]
        return DouyinVideoInfo(**values)

    def metadata(self, inspected: DouyinVideoInfo) -> dict[str, Any]:
        payload = inspected.to_dict()
        payload.pop("video_url", None)  # Signed public CDN URLs expire and add no durable evidence.
        payload.update(self.reference(inspected).to_dict())
        return payload

    def primary_transcript(self, inspected: DouyinVideoInfo) -> tuple[list[Segment], dict[str, Any]]:
        del inspected
        return [], {"warning": "抖音公开分享页没有提供原生字幕轨道"}

    def secondary_transcript(self, inspected: DouyinVideoInfo) -> tuple[list[Segment], dict[str, Any]]:
        del inspected
        return [], {"warning": "抖音视频将使用项目已准备的本地 ASR"}

    def _source_video(self, inspected: DouyinVideoInfo, work_dir: Path) -> Path:
        if self._source_path and self._source_path.is_file():
            return self._source_path
        destination = work_dir / "douyin-source.mp4"
        if destination.is_file() and destination.stat().st_size > 0:
            self._source_path = destination
            return destination
        current = inspected
        if not current.video_url:
            current = self.client.inspect(inspected.url)
        try:
            self._source_path = _download_public_video(current.video_url, destination, current.url)
        except Exception:
            refreshed = self.client.inspect(inspected.url)
            self._source_path = _download_public_video(refreshed.video_url, destination, refreshed.url)
        return self._source_path

    def download_audio(
        self, inspected: DouyinVideoInfo, work_dir: Path, *, cookies_file: Path | None = None
    ) -> Path:
        del cookies_file
        return self._source_video(inspected, work_dir)

    def download_analysis_video(
        self,
        inspected: DouyinVideoInfo,
        work_dir: Path,
        *,
        cookies_file: Path | None = None,
        max_height: int = 720,
    ) -> Path:
        del cookies_file
        source = self._source_video(inspected, work_dir)
        if inspected.height and inspected.height <= max_height:
            return source
        destination = work_dir / f"douyin-analysis-{max_height}p.mp4"
        if destination.is_file() and destination.stat().st_size > 0:
            return destination
        ffmpeg = require_command("ffmpeg")
        _run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(source),
                "-vf",
                f"scale=-2:min({max_height}\\,ih)",
                "-an",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "23",
                "-y",
                str(destination),
            ],
            timeout=1800,
        )
        if not destination.is_file() or destination.stat().st_size == 0:
            raise RuntimeError("抖音 720p 分析视频生成失败")
        return destination

    def recapture_frames(
        self, inspected: DouyinVideoInfo, frames: list[Frame], *, max_height: int = 1080
    ) -> dict[str, int | str]:
        retained = [frame for frame in frames if frame.keep_image and frame.path]
        if not retained:
            return {"requested_height": max_height, "actual_height": 0, "upgraded_count": 0}
        if not self._source_path or not self._source_path.is_file():
            raise RuntimeError("抖音原始视频已不可用，无法进行高清定点取帧")
        actual_height = min(inspected.height or max_height, max_height)
        ffmpeg = require_command("ffmpeg")
        upgraded = 0
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
                        "-ss",
                        f"{frame.timestamp:.3f}",
                        "-i",
                        str(self._source_path),
                        "-vf",
                        f"scale=-2:min({max_height}\\,ih)",
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
                    frame.final_height = actual_height
                    upgraded += 1
            finally:
                temporary.unlink(missing_ok=True)
        return {
            "requested_height": max_height,
            "actual_height": actual_height,
            "upgraded_count": upgraded,
            "method": "local_original_timestamp_seek",
        }

    def context(self, inspected: DouyinVideoInfo) -> str:
        published = f"；发布时间：{inspected.published_at}" if inspected.published_at else ""
        return f"标题：{inspected.title}；抖音作者：{inspected.owner}{published}"

    def folder_marker(self, inspected: DouyinVideoInfo) -> str:
        return f"DY-{inspected.video_id}"
