from __future__ import annotations

import base64
import os
import random
import shutil
import string
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from .bilibili import BilibiliClient, MEDIA_HOST_SUFFIXES, UA, VideoInfo


def build_dm_img_params() -> dict[str, Any]:
    """Build browser-shaped WBI parameters; adapted from BiliNote (MIT)."""
    def random_text(low: int, high: int) -> str:
        return "".join(
            random.choices(string.ascii_letters + string.digits, k=random.randint(low, high))
        )
    return {
        "web_location": 1550101,
        "dm_img_list": "[]",
        "dm_img_str": base64.b64encode(random_text(16, 64).encode()).decode().rstrip("="),
        "dm_cover_img_str": base64.b64encode(random_text(32, 128).encode()).decode().rstrip("="),
        "dm_img_inter": '{"ds":[],"wh":[6093,6631,31],"of":[430,760,380]}',
    }


def apply_bilibili_patch() -> bool:
    try:
        from yt_dlp.extractor.bilibili import BilibiliBaseIE
    except Exception:
        return False
    original = BilibiliBaseIE._download_playinfo
    if getattr(original, "_vtm_bili_dm_patched", False):
        return True

    def patched(self: Any, bvid: str, cid: int, headers: Any = None, query: Any = None):
        return original(
            self,
            bvid,
            cid,
            headers=headers,
            query={**build_dm_img_params(), **(query or {})},
        )

    patched._vtm_bili_dm_patched = True  # type: ignore[attr-defined]
    BilibiliBaseIE._download_playinfo = patched
    return True


def download_media(
    url: str,
    work_dir: Path,
    *,
    audio_only: bool,
    cookies_file: Path | None = None,
    client: BilibiliClient | None = None,
    info: VideoInfo | None = None,
    max_height: int = 720,
) -> Path:
    direct_error: Exception | None = None
    if client is not None and info is not None:
        try:
            return download_player_stream(
                client,
                info,
                work_dir,
                audio_only=audio_only,
                max_height=max_height,
            )
        except Exception as exc:
            direct_error = exc
    try:
        import yt_dlp
    except ImportError as exc:
        raise RuntimeError("yt-dlp is required; install scripts/requirements.txt") from exc
    apply_bilibili_patch()
    work_dir.mkdir(parents=True, exist_ok=True)
    template = str(work_dir / ("audio.%(ext)s" if audio_only else "video.%(ext)s"))
    options: dict[str, Any] = {
        "outtmpl": template,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "retries": 3,
        "fragment_retries": 3,
        "socket_timeout": 20,
        "extractor_retries": 2,
    }
    if cookies_file:
        options["cookiefile"] = str(cookies_file)
    if os.getenv("VTM_SOURCE_PROXY"):
        options["proxy"] = os.environ["VTM_SOURCE_PROXY"]
    if audio_only:
        options.update({"format": "bestaudio/best", "postprocessors": []})
    else:
        options.update(
            {
                "format": (
                    f"bestvideo[height<={max_height}]/"
                    f"best[height<={max_height}]/"
                    f"worstvideo[height<={max_height}]"
                )
            }
        )
    try:
        with yt_dlp.YoutubeDL(options) as downloader:
            extracted = downloader.extract_info(url, download=True)
            requested = extracted.get("requested_downloads") or []
            paths = [Path(item["filepath"]) for item in requested if item.get("filepath")]
            prepared = Path(downloader.prepare_filename(extracted))
    except Exception as exc:
        if direct_error is not None:
            raise RuntimeError(
                "Both Bilibili player API and yt-dlp media acquisition failed "
                f"({type(direct_error).__name__}; {type(exc).__name__})"
            ) from exc
        raise
    existing = [path for path in paths + [prepared] if path.exists()]
    if not existing:
        existing = sorted(work_dir.glob("audio.*" if audio_only else "video.*"))
    if not existing:
        raise RuntimeError("yt-dlp completed without a discoverable media file")
    return existing[0]


def download_player_stream(
    client: BilibiliClient,
    info: VideoInfo,
    work_dir: Path,
    *,
    audio_only: bool,
    max_height: int = 720,
) -> Path:
    """Download one selected DASH/progressive stream from a Bilibili CDN."""
    stream = client.media_stream(info, audio_only=audio_only, max_height=max_height)
    extension = str(stream.get("container") or "m4s")
    destination = work_dir / (f"audio.{extension}" if audio_only else f"video.{extension}")
    work_dir.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(
        str(stream["url"]),
        headers={
            "User-Agent": UA,
            "Referer": f"https://www.bilibili.com/video/{info.bvid}/",
        },
    )
    with urllib.request.urlopen(request, timeout=max(60.0, client.timeout)) as response:
        final = urllib.parse.urlparse(response.geturl())
        host = (final.hostname or "").lower()
        if final.scheme != "https" or not host.endswith(MEDIA_HOST_SUFFIXES):
            raise RuntimeError("Bilibili media redirected to an unexpected host")
        with destination.open("wb") as handle:
            shutil.copyfileobj(response, handle, length=1024 * 1024)
    if not destination.exists() or destination.stat().st_size == 0:
        raise RuntimeError("Bilibili player stream download produced an empty file")
    return destination
