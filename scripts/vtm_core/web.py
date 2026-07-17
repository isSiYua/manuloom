from __future__ import annotations

import hashlib
import html
import ipaddress
import mimetypes
import re
import socket
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable

try:
    from readability import Document as ReadabilityDocument
except ImportError:  # Installed by requirements in production; fixtures retain a fallback.
    ReadabilityDocument = None

try:
    import extruct
except ImportError:  # Installed by requirements in production; basic meta tags still work.
    extruct = None

from .models import Frame, Segment
from .sources import SourceReference


MAX_HTML_BYTES = 8 * 1024 * 1024
MAX_IMAGE_BYTES = 15 * 1024 * 1024
MAX_DOCUMENT_IMAGES = 60
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)
TRACKING_QUERY_KEYS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "spm", "from", "source", "share_token",
}
EXCLUDED_TAGS = {
    "script", "style", "noscript", "template", "svg", "canvas", "form",
    "button", "nav", "footer", "aside",
}
CONTENT_HINTS = (
    "article", "article-content", "article_content", "post-content", "post_content",
    "entry-content", "entry_content", "content_views", "main-content", "markdown-body",
    "rich-content", "正文",
)
NOISE_HINTS = (
    "comment", "recommend", "related", "sidebar", "toolbar", "copyright", "login",
    "advert", "share", "profile", "author-card", "上一篇", "下一篇",
)
AUTHOR_HINTS = ("author-name", "author_name", "nickname", "nick-name", "user-name", "follow-nickname")
DATE_HINTS = ("publish-time", "publish_time", "published-time", "article-time", "date-time")
BLOCK_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "pre", "blockquote", "figcaption"}


@dataclass(slots=True)
class HtmlNode:
    tag: str
    attrs: dict[str, str] = field(default_factory=dict)
    children: list[HtmlNode | str] = field(default_factory=list)
    parent: HtmlNode | None = None


class _TreeParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = HtmlNode("document")
        self.current = self.root

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = HtmlNode(
            tag.lower(),
            {str(key).lower(): str(value or "") for key, value in attrs},
            parent=self.current,
        )
        self.current.children.append(node)
        if tag.lower() not in {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}:
            self.current = node

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if self.current.tag == tag.lower():
            self.current = self.current.parent or self.root

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        cursor = self.current
        while cursor is not self.root:
            if cursor.tag == tag:
                self.current = cursor.parent or self.root
                return
            cursor = cursor.parent or self.root

    def handle_data(self, data: str) -> None:
        if data:
            self.current.children.append(data)


@dataclass(frozen=True, slots=True)
class DocumentImage:
    url: str
    after_segment_id: str
    order: int
    alt: str = ""
    caption: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class GenericWebInfo:
    url: str
    source_id: str
    title: str
    author: str
    site_name: str
    published_at: str
    extraction_engine: str
    segments: tuple[dict[str, Any], ...]
    images: tuple[dict[str, Any], ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _node_text(node: HtmlNode, *, include_alt: bool = False) -> str:
    parts: list[str] = []
    for child in node.children:
        if isinstance(child, str):
            parts.append(child)
        elif child.tag not in EXCLUDED_TAGS:
            if child.tag == "br":
                parts.append("\n")
            elif include_alt and child.tag == "img" and child.attrs.get("alt"):
                parts.append(child.attrs["alt"])
            else:
                parts.append(_node_text(child, include_alt=include_alt))
    merged = ""
    for part in parts:
        if (
            merged
            and part
            and not merged[-1].isspace()
            and not part[0].isspace()
            and (
                (merged[-1].isascii() and merged[-1].isalnum() and part[0].isascii() and part[0].isalnum())
                or (merged[-1] in ".!?:;" and part[0].isascii() and part[0].isalnum())
            )
        ):
            merged += " "
        merged += part
    text = html.unescape(merged).replace("\xa0", " ")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()


def _walk(node: HtmlNode) -> Iterable[HtmlNode]:
    yield node
    for child in node.children:
        if isinstance(child, HtmlNode):
            yield from _walk(child)


def _meta(root: HtmlNode, *names: str) -> str:
    wanted = {name.lower() for name in names}
    for node in _walk(root):
        if node.tag != "meta":
            continue
        key = (node.attrs.get("property") or node.attrs.get("name") or "").lower()
        if key in wanted and node.attrs.get("content"):
            return node.attrs["content"].strip()
    return ""


def _short_labeled_text(root: HtmlNode, hints: tuple[str, ...], *, maximum: int) -> str:
    for node in _walk(root):
        marker = f"{node.attrs.get('id', '')} {node.attrs.get('class', '')}".lower()
        if not any(hint in marker for hint in hints):
            continue
        text = re.sub(r"\s+", " ", _node_text(node)).strip()
        if 1 < len(text) <= maximum:
            return text
    return ""


def _structured_article_metadata(raw_html: str, url: str) -> dict[str, str]:
    if extruct is None:
        return {}
    try:
        extracted = extruct.extract(raw_html, base_url=url, syntaxes=["json-ld"])
    except Exception:
        return {}
    objects: list[dict[str, Any]] = []

    def collect(value: Any) -> None:
        if isinstance(value, dict):
            objects.append(value)
            graph = value.get("@graph")
            if isinstance(graph, list):
                for item in graph:
                    collect(item)
        elif isinstance(value, list):
            for item in value:
                collect(item)

    collect(extracted.get("json-ld") or [])
    preferred = next(
        (
            item
            for item in objects
            if any(
                name in {"article", "newsarticle", "blogposting", "techarticle", "answer"}
                for name in (
                    [str(value).lower() for value in item.get("@type", [])]
                    if isinstance(item.get("@type"), list)
                    else [str(item.get("@type") or "").lower()]
                )
            )
        ),
        objects[0] if objects else {},
    )
    author_value = preferred.get("author")
    authors = author_value if isinstance(author_value, list) else [author_value]
    author_names: list[str] = []
    for author in authors:
        name = author.get("name") if isinstance(author, dict) else author
        if name and str(name).strip() not in author_names:
            author_names.append(str(name).strip())
    publisher = preferred.get("publisher")
    publisher_name = publisher.get("name") if isinstance(publisher, dict) else ""
    return {
        "title": str(preferred.get("headline") or preferred.get("name") or "").strip(),
        "author": ", ".join(author_names)[:300],
        "published_at": str(
            preferred.get("datePublished") or preferred.get("dateCreated") or ""
        ).strip(),
        "site_name": str(publisher_name or "").strip(),
    }


def _select_content_root(root: HtmlNode) -> HtmlNode:
    candidates: list[tuple[float, HtmlNode]] = []
    for node in _walk(root):
        if node.tag not in {"article", "main", "div", "section", "body"}:
            continue
        marker = f"{node.attrs.get('id', '')} {node.attrs.get('class', '')}".lower()
        text_length = len(_node_text(node))
        if text_length < 80:
            continue
        hint_bonus = 5000 if node.tag in {"article", "main"} else 0
        hint_bonus += 7000 if any(hint in marker for hint in CONTENT_HINTS) else 0
        noise_penalty = 9000 if any(hint in marker for hint in NOISE_HINTS) else 0
        link_text = sum(len(_node_text(item)) for item in _walk(node) if item.tag == "a")
        score = hint_bonus + text_length - min(text_length, link_text) * 0.55 - noise_penalty
        candidates.append((score, node))
    return max(candidates, key=lambda item: item[0])[1] if candidates else root


def _image_url(node: HtmlNode, base_url: str) -> str:
    raw = next(
        (node.attrs.get(key, "") for key in ("data-original", "data-src", "data-lazy-src", "src") if node.attrs.get(key)),
        "",
    ).strip()
    if not raw or raw.startswith(("data:", "blob:", "javascript:")):
        return ""
    resolved = urllib.parse.urljoin(base_url, raw)
    parsed = urllib.parse.urlparse(resolved)
    return resolved if parsed.scheme in {"http", "https"} and parsed.hostname else ""


def _table_markdown(node: HtmlNode) -> str:
    rows: list[list[str]] = []
    for row in _walk(node):
        if row.tag != "tr":
            continue
        cells = [
            re.sub(r"\s+", " ", _node_text(cell)).replace("|", "\\|").strip()
            for cell in row.children
            if isinstance(cell, HtmlNode) and cell.tag in {"th", "td"}
        ]
        if cells:
            rows.append(cells)
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    normalized = [row + [""] * (width - len(row)) for row in rows]
    return "\n".join(
        ["| " + " | ".join(normalized[0]) + " |", "| " + " | ".join(["---"] * width) + " |"]
        + ["| " + " | ".join(row) + " |" for row in normalized[1:]]
    )


def parse_html_document(raw_html: str, url: str) -> tuple[dict[str, str], list[Segment], list[DocumentImage]]:
    parser = _TreeParser()
    parser.feed(raw_html)
    root = parser.root

    structured = _structured_article_metadata(raw_html, url)

    title = _meta(root, "og:title", "twitter:title") or structured.get("title", "")
    if not title:
        title_node = next((node for node in _walk(root) if node.tag == "title"), None)
        title = _node_text(title_node) if title_node else ""
    author = _meta(root, "author", "article:author") or structured.get("author", "") or _short_labeled_text(
        root, AUTHOR_HINTS, maximum=80
    )
    site_name = _meta(root, "og:site_name") or structured.get("site_name", "") or (urllib.parse.urlparse(url).hostname or "")
    published_at = _meta(
        root,
        "article:published_time",
        "date",
        "datepublished",
        "publishdate",
        "pubdate",
    ) or structured.get("published_at", "") or _short_labeled_text(root, DATE_HINTS, maximum=100)

    extraction_engine = "deterministic_html_fallback"
    fallback_content = _select_content_root(root)
    content = fallback_content
    if ReadabilityDocument is not None:
        try:
            readable = ReadabilityDocument(
                raw_html,
                url=url,
                positive_keywords=CONTENT_HINTS,
                negative_keywords=NOISE_HINTS,
            )
            readable_title = str(readable.title() or "").strip()
            readable_author = str(readable.author() or "").strip()
            summary = readable.summary(html_partial=True, keep_all_images=True)
            cleaned_parser = _TreeParser()
            cleaned_parser.feed(summary)
            cleaned_root = cleaned_parser.root
            if len(_node_text(cleaned_root)) >= 80:
                content = cleaned_root
                extraction_engine = "readability-lxml"
                fidelity_tags = {"table", "pre", "img"}
                original_counts = {
                    tag: sum(1 for node in _walk(fallback_content) if node.tag == tag)
                    for tag in fidelity_tags
                }
                cleaned_counts = {
                    tag: sum(1 for node in _walk(cleaned_root) if node.tag == tag)
                    for tag in fidelity_tags
                }
                if any(cleaned_counts[tag] < original_counts[tag] for tag in fidelity_tags):
                    content = fallback_content
                    extraction_engine = "readability-lxml+structured-fidelity"
                title = title or readable_title
                if not author and readable_author not in {"", "[no-author]"}:
                    author = readable_author
        except Exception:
            # The bounded deterministic fallback handles malformed pages.
            pass

    segments: list[Segment] = []
    images: list[DocumentImage] = []
    seen_images: set[str] = set()
    consumed: set[int] = set()

    def add_text(text: str, tag: str) -> str:
        text = text.strip()
        if not text:
            return segments[-1].id if segments else ""
        if tag.startswith("h") and len(tag) == 2:
            text = f"{'#' * max(2, int(tag[1]))} {text}"
        elif tag == "li":
            text = f"- {text}"
        elif tag == "blockquote":
            text = "\n".join(f"> {line}" for line in text.splitlines())
        elif tag == "pre":
            text = f"```\n{text}\n```"
        if segments and segments[-1].text == text:
            return segments[-1].id
        index = len(segments) + 1
        segment = Segment(f"s{index:06d}", float(index - 1), float(index), text, locator_kind="document_order")
        segments.append(segment)
        return segment.id

    def add_image(node: HtmlNode, after_id: str) -> None:
        image_url = _image_url(node, url)
        if not image_url or image_url in seen_images:
            return
        try:
            width = int(re.sub(r"\D", "", node.attrs.get("width", "")) or 0)
            height = int(re.sub(r"\D", "", node.attrs.get("height", "")) or 0)
        except ValueError:
            width = height = 0
        if width and height and max(width, height) < 120:
            return
        seen_images.add(image_url)
        images.append(
            DocumentImage(
                image_url,
                after_id or (segments[-1].id if segments else "s000001"),
                len(images) + 1,
                alt=re.sub(r"\s+", " ", node.attrs.get("alt", "")).strip()[:300],
            )
        )

    def visit(node: HtmlNode, after_id: str = "") -> str:
        marker = f"{node.attrs.get('id', '')} {node.attrs.get('class', '')}".lower()
        if node.tag in EXCLUDED_TAGS or any(hint in marker for hint in NOISE_HINTS):
            return after_id
        if node.tag == "img":
            add_image(node, after_id)
            return after_id
        if node.tag == "table":
            consumed.add(id(node))
            after_id = add_text(_table_markdown(node), "table")
            for child in _walk(node):
                if child.tag == "img":
                    add_image(child, after_id)
            return after_id
        if node.tag in BLOCK_TAGS:
            consumed.add(id(node))
            after_id = add_text(_node_text(node), node.tag)
            for child in _walk(node):
                if child.tag == "img":
                    add_image(child, after_id)
            return after_id
        for child in node.children:
            if isinstance(child, HtmlNode) and id(child) not in consumed:
                after_id = visit(child, after_id)
        return after_id

    visit(content)
    if not segments:
        raise RuntimeError("公开页面中没有找到可用正文")
    if sum(len(segment.text) for segment in segments) < 80:
        raise RuntimeError("公开页面正文过短，可能需要登录或浏览器渲染")
    published_match = re.search(r"\d{4}[-/.]\d{1,2}[-/.]\d{1,2}(?:[T\s]\d{1,2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:?\d{2})?)?", published_at)
    if published_match:
        published_at = published_match.group(0)
    return {
        "title": re.sub(r"\s+", " ", title).strip() or segments[0].text.lstrip("# ")[:100],
        "author": re.sub(r"\s+", " ", author).strip(),
        "site_name": re.sub(r"\s+", " ", site_name).strip(),
        "published_at": re.sub(r"\s+", " ", published_at).strip(),
        "extraction_engine": extraction_engine,
    }, segments, images


def validate_public_url(value: str, *, resolve_dns: bool = True) -> str:
    text = str(value or "").strip()
    if not urllib.parse.urlparse(text).scheme:
        text = "https://" + text
    parsed = urllib.parse.urlparse(text)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("网页来源只接受公开 HTTP(S) URL")
    if parsed.username or parsed.password:
        raise ValueError("网页 URL 不得包含用户名或密码")
    host = parsed.hostname.strip("[]").lower()
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        raise ValueError("拒绝读取本机或内网页面")
    try:
        addresses = [ipaddress.ip_address(host)]
    except ValueError:
        addresses = []
        if resolve_dns:
            try:
                addresses = list({ipaddress.ip_address(item[4][0]) for item in socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)})
            except socket.gaierror as exc:
                raise ValueError("网页域名无法解析") from exc
    if any(not address.is_global for address in addresses):
        raise ValueError("拒绝读取本机、内网或保留地址")
    return urllib.parse.urlunparse(parsed._replace(fragment=""))


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        target = validate_public_url(urllib.parse.urljoin(req.full_url, newurl))
        return super().redirect_request(req, fp, code, msg, headers, target)


def _open_public_url(
    url: str, *, max_bytes: int, accepted_prefixes: tuple[str, ...]
) -> tuple[bytes, str, str, str]:
    safe_url = validate_public_url(url)
    request = urllib.request.Request(
        safe_url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,image/avif,image/webp,image/*;q=0.8,*/*;q=0.5",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7",
        },
    )
    opener = urllib.request.build_opener(_SafeRedirectHandler())
    with opener.open(request, timeout=25) as response:
        final_url = validate_public_url(response.geturl())
        content_type = response.headers.get_content_type().lower()
        if accepted_prefixes and not any(content_type.startswith(prefix) for prefix in accepted_prefixes):
            raise RuntimeError(f"来源返回了不支持的内容类型：{content_type}")
        declared = int(response.headers.get("Content-Length") or 0)
        if declared > max_bytes:
            raise RuntimeError("来源内容超过安全大小限制")
        body = response.read(max_bytes + 1)
        if len(body) > max_bytes:
            raise RuntimeError("来源内容超过安全大小限制")
        charset = response.headers.get_content_charset() or "utf-8"
        return body, final_url, content_type, charset


def canonicalize_web_url(value: str, *, resolve_dns: bool = False) -> str:
    normalized = validate_public_url(value, resolve_dns=resolve_dns)
    parsed = urllib.parse.urlparse(normalized)
    query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    clean_query = urllib.parse.urlencode([(key, val) for key, val in query if key.lower() not in TRACKING_QUERY_KEYS])
    path = parsed.path or "/"
    return urllib.parse.urlunparse(parsed._replace(scheme=parsed.scheme.lower(), netloc=parsed.netloc.lower(), path=path, query=clean_query, fragment=""))


class GenericWebSourceAdapter:
    platform = "generic_web"
    source_kind = "document"
    _reserved_hosts = (
        "bilibili.com", "b23.tv", "youtube.com", "youtu.be", "zhihu.com",
        "douyin.com", "iesdouyin.com", "xiaohongshu.com", "xhslink.com",
    )

    def can_handle(self, value: str) -> bool:
        raw = str(value or "").strip()
        if "://" not in raw and "." not in raw:
            return False
        try:
            parsed = urllib.parse.urlparse(self.normalize_input_url(raw))
        except ValueError:
            return False
        host = (parsed.hostname or "").lower()
        return bool(host) and not any(host == item or host.endswith("." + item) for item in self._reserved_hosts)

    def normalize_input_url(self, value: str) -> str:
        return canonicalize_web_url(value, resolve_dns=False)

    def canonicalize_input(self, value: str) -> str:
        return self.normalize_input_url(value)

    def source_id_from_url(self, value: str) -> str:
        canonical = self.canonicalize_input(value)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:20]

    def selector_from_url(self, value: str, explicit: int | None = None) -> None:
        del value, explicit
        return None

    def inspect(self, value: str, selector: int | None = None) -> GenericWebInfo:
        del selector
        body, final_url, _content_type, charset = _open_public_url(
            self.canonicalize_input(value), max_bytes=MAX_HTML_BYTES, accepted_prefixes=("text/html", "application/xhtml+xml")
        )
        try:
            raw_html = body.decode(charset, errors="replace")
        except LookupError:
            raw_html = body.decode("utf-8", errors="replace")
        canonical = canonicalize_web_url(final_url, resolve_dns=False)
        metadata, segments, images = parse_html_document(raw_html, canonical)
        source_id = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:20]
        return GenericWebInfo(
            canonical,
            source_id,
            metadata["title"],
            metadata["author"],
            metadata["site_name"],
            metadata["published_at"],
            metadata["extraction_engine"],
            tuple(segment.to_dict() for segment in segments),
            tuple(image.to_dict() for image in images),
        )

    def reference(self, inspected: GenericWebInfo) -> SourceReference:
        return SourceReference(
            platform=self.platform,
            source_kind=self.source_kind,
            source_id=inspected.source_id,
            canonical_url=inspected.url,
            title=inspected.title,
            author=inspected.author,
        )

    def restore_info(self, metadata: dict[str, Any]) -> GenericWebInfo:
        fields = GenericWebInfo.__dataclass_fields__
        payload = {key: metadata[key] for key in fields}
        payload["segments"] = tuple(payload["segments"])
        payload["images"] = tuple(payload["images"])
        return GenericWebInfo(**payload)

    def metadata(self, inspected: GenericWebInfo) -> dict[str, Any]:
        payload = inspected.to_dict()
        payload.update(self.reference(inspected).to_dict())
        return payload

    def content_segments(self, inspected: GenericWebInfo) -> tuple[list[Segment], dict[str, Any]]:
        segments = [Segment(**item) for item in inspected.segments]
        return segments, {
            "source": "public_html_document",
            "locator_kind": "document_order",
            "segment_count": len(segments),
            "image_count": len(inspected.images),
            "extraction_engine": inspected.extraction_engine,
        }

    def download_images(self, inspected: GenericWebInfo, assets_dir: Path, *, limit: int = 60) -> list[Frame]:
        assets_dir.mkdir(parents=True, exist_ok=True)
        frames: list[Frame] = []
        segment_order = {str(item["id"]): float(item["end"]) for item in inspected.segments}
        retained_limit = min(MAX_DOCUMENT_IMAGES, max(0, limit))
        for image in inspected.images:
            if len(frames) >= retained_limit:
                break
            try:
                body, final_url, content_type, _charset = _open_public_url(
                    str(image["url"]), max_bytes=MAX_IMAGE_BYTES, accepted_prefixes=("image/",)
                )
            except Exception:
                continue
            suffix = mimetypes.guess_extension(content_type or "") or Path(urllib.parse.urlparse(final_url).path).suffix.lower()
            if suffix == ".jpe":
                suffix = ".jpg"
            if suffix not in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
                continue
            path = assets_dir / f"source-{len(frames) + 1:03d}{suffix}"
            path.write_bytes(body)
            after_id = str(image.get("after_segment_id") or "")
            frames.append(
                Frame(
                    timestamp=segment_order.get(after_id, float(image.get("order") or len(frames) + 1)),
                    path=str(path),
                    source_ids=[after_id] if after_id else [],
                    vision_description=str(image.get("alt") or "")[:300],
                    content_kind="source_image",
                    keep_image=True,
                    media_kind="source_image",
                    locator_label=f"原文第 {int(image.get('order') or len(frames) + 1)} 张图片",
                    source_url=final_url,
                )
            )
        return frames

    def context(self, inspected: GenericWebInfo) -> str:
        creator = f"；作者：{inspected.author}" if inspected.author else ""
        published = f"；发布时间：{inspected.published_at}" if inspected.published_at else ""
        return f"来源类型：公开网页文章；标题：{inspected.title}；站点：{inspected.site_name}{creator}{published}"

    def folder_marker(self, inspected: GenericWebInfo) -> str:
        return f"WEB-{inspected.source_id[:12]}"
