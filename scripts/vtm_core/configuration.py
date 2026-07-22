from __future__ import annotations

import fcntl
import getpass
import os
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


@dataclass(frozen=True, slots=True)
class SecretSpec:
    id: str
    env_key: str
    label: str
    platform: str
    optional: bool = True


@dataclass(frozen=True, slots=True)
class PlatformSpec:
    number: int
    id: str
    label: str
    aliases: tuple[str, ...]
    adapter_installed: bool
    public_mode: str
    credentials: tuple[str, ...]
    setup_url: str
    limitations: str


SECRET_SPECS = (
    SecretSpec("text_llm_key", "VTM_LLM_API_KEY", "文字编辑模型 API Key", "core", False),
    SecretSpec("vision_api_key", "VTM_VISION_API_KEY", "视觉模型 API Key", "core"),
    SecretSpec("source_proxy", "VTM_SOURCE_PROXY", "境外来源 HTTPS 代理 URL", "core"),
    SecretSpec("bilibili_cookie", "BILIBILI_COOKIE", "Bilibili 完整 Cookie Header", "bilibili"),
    SecretSpec("youtube_api_key", "YOUTUBE_API_KEY", "YouTube Data API Key", "youtube"),
    SecretSpec("zhihu_z_c0", "ZHIHU_Z_C0", "知乎登录 Cookie z_c0", "zhihu"),
)

PLATFORM_SPECS = (
    PlatformSpec(
        1,
        "bilibili",
        "Bilibili",
        ("b站", "哔哩哔哩", "bilibili"),
        True,
        "公开视频不需要 API；登录状态仅用于可选字幕、AI 片段或清晰度增强。",
        ("bilibili_cookie",),
        "https://www.bilibili.com/",
        "Cookie 不绕过付费、版权、地区或平台风控；应使用低风险专用账号。",
    ),
    PlatformSpec(
        2,
        "youtube",
        "YouTube",
        ("youtube", "油管"),
        True,
        "公开视频无凭据模式可用。Data API Key 只作为可选官方元数据/API 增强。",
        ("youtube_api_key",),
        "https://console.cloud.google.com/apis/credentials",
        "用户私有数据需要独立 OAuth 授权；API Key 不等于任意字幕读取权限。",
    ),
    PlatformSpec(
        3,
        "zhihu",
        "知乎",
        ("知乎", "zhihu"),
        True,
        "回答和文章会先尝试无凭据读取；若被知乎风控拒绝，需要用户自己的 z_c0。",
        ("zhihu_z_c0",),
        "https://www.zhihu.com/",
        "z_c0 仅用于有权访问的内容，不绕过付费、删除、账号权限或平台风控。官方 Access Secret 是邀测搜索产品，不是任意 URL 全文凭据。",
    ),
    PlatformSpec(
        4,
        "generic_web",
        "普通网页 / CSDN",
        ("网页", "普通网页", "csdn", "generic_web"),
        True,
        "公开文章无凭据模式已安装；正文、结构、表格和原图按页面顺序提取。",
        (),
        "",
        "登录、付费、删除、JavaScript-only 或风险控制内容不会被绕过；评论和推荐默认排除。CSDN 若对当前出口返回 521，需更换合规网络出口后重试。",
    ),
    PlatformSpec(
        5,
        "douyin",
        "抖音",
        ("抖音", "douyin"),
        True,
        "公开视频分享链接无凭据模式已安装；无原生字幕时使用服务器本地 ASR。",
        (),
        "https://developer.open-douyin.com/",
        "公共分享页提取不使用 Client Key；开放平台凭据仅保留给未来已审核应用和用户授权能力。平台风控或已删除内容不会被绕过。",
    ),
    PlatformSpec(
        6,
        "xiaohongshu",
        "小红书",
        ("小红书", "xhs", "xiaohongshu", "rednote"),
        True,
        "公开图文笔记无凭据模式已安装；正文与原图按来源顺序进入文档管线。",
        (),
        "https://www.xiaohongshu.com/",
        "只处理公开图文笔记；视频笔记、登录、验证码、删除和风险控制内容不会被绕过。商家/小程序凭据不是通用笔记读取权限。",
    ),
)

SECRET_ENV_KEYS = frozenset(spec.env_key for spec in SECRET_SPECS)


def secret_store_path(home: Path | None = None) -> Path:
    configured = os.getenv("VTM_SECRET_FILE")
    if configured:
        return Path(configured).expanduser().resolve()
    root = home or Path.home()
    return root / ".config" / "manuloom" / "secrets.env"


def resolve_platform(value: str | int) -> PlatformSpec:
    text = str(value).strip().lower()
    for spec in PLATFORM_SPECS:
        names = {str(spec.number), spec.id, spec.label.lower(), *(alias.lower() for alias in spec.aliases)}
        if text in names:
            return spec
    raise KeyError(f"未知平台：{value}")


def resolve_secret(value: str) -> SecretSpec:
    text = str(value).strip().lower()
    for spec in SECRET_SPECS:
        if text in {spec.id.lower(), spec.env_key.lower()}:
            return spec
    raise KeyError(f"未知配置项：{value}")


def _configured(secret_id: str, environ: Mapping[str, str]) -> bool:
    spec = resolve_secret(secret_id)
    return bool(environ.get(spec.env_key))


def configuration_menu(environ: Mapping[str, str] | None = None) -> dict[str, object]:
    env = os.environ if environ is None else environ
    platforms: list[dict[str, object]] = []
    for spec in PLATFORM_SPECS:
        configured = {secret_id: _configured(secret_id, env) for secret_id in spec.credentials}
        platforms.append(
            {
                "number": spec.number,
                "id": spec.id,
                "label": spec.label,
                "adapter_installed": spec.adapter_installed,
                "public_mode": spec.public_mode,
                "credentials": configured,
            }
        )
    return {
        "status": "configuration_menu",
        "core": {
            "text_llm_key": bool(env.get("VTM_LLM_API_KEY") or env.get("DEEPSEEK_API_KEY")),
            "vision_configured": all(
                env.get(key) for key in ("VTM_VISION_API_KEY", "VTM_VISION_BASE_URL", "VTM_VISION_MODEL")
            ),
            "vault": env.get("VTM_VAULT") or "~/ObsidianVault",
        },
        "platforms": platforms,
        "reply_hint": "发送“配置 1”或“配置 B站”；裸数字不会执行配置。",
        "secret_delivery": "never_send_in_chat",
    }


def platform_configuration(value: str | int, environ: Mapping[str, str] | None = None) -> dict[str, object]:
    env = os.environ if environ is None else environ
    spec = resolve_platform(value)
    credentials = []
    for secret_id in spec.credentials:
        secret = resolve_secret(secret_id)
        credentials.append(
            {
                "id": secret.id,
                "label": secret.label,
                "optional": secret.optional,
                "configured": bool(env.get(secret.env_key)),
            }
        )
    return {
        "status": "platform_configuration",
        "platform": spec.id,
        "label": spec.label,
        "adapter_installed": spec.adapter_installed,
        "public_mode": spec.public_mode,
        "credentials": credentials,
        "setup_url": spec.setup_url,
        "limitations": spec.limitations,
        "secret_instruction": (
            (
                "通过 SSH 运行 scripts/vtm configure secret <配置项>；终端隐藏输入。"
                "不要把 Cookie、API Key、Secret 或 Token 发到聊天。"
            )
            if credentials
            else "当前公开模式无需配置 Cookie、API Key、Secret 或 Token。"
        ),
    }


def _read_secret_store(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key in SECRET_ENV_KEYS:
            values[key] = value.strip()
    return values


def _write_secret_store(path: Path, values: Mapping[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    lock_path = path.with_suffix(path.suffix + ".lock")
    with lock_path.open("a+", encoding="utf-8") as lock:
        lock_path.chmod(0o600)
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write("# Managed by manuloom. Do not share.\n")
                for key in sorted(values):
                    if key in SECRET_ENV_KEYS and values[key]:
                        handle.write(f"{key}={values[key]}\n")
            os.replace(temporary, path)
            path.chmod(0o600)
        finally:
            temporary.unlink(missing_ok=True)


def set_secret(secret_id: str, value: str, *, path: Path | None = None) -> dict[str, object]:
    spec = resolve_secret(secret_id)
    clean = str(value).strip()
    if not clean or "\n" in clean or "\r" in clean:
        raise ValueError("配置值不能为空或包含换行")
    destination = path or secret_store_path()
    values = _read_secret_store(destination)
    values[spec.env_key] = clean
    _write_secret_store(destination, values)
    os.environ[spec.env_key] = clean
    return {
        "status": "configured",
        "secret": spec.id,
        "configured": True,
        "value_printed": False,
        "path": str(destination),
        "permissions": "0600",
    }


def set_secret_interactive(secret_id: str, *, path: Path | None = None) -> dict[str, object]:
    spec = resolve_secret(secret_id)
    value = getpass.getpass(f"请输入{spec.label}（输入不会回显）：")
    return set_secret(spec.id, value, path=path)


def remove_secret(secret_id: str, *, path: Path | None = None) -> dict[str, object]:
    spec = resolve_secret(secret_id)
    destination = path or secret_store_path()
    values = _read_secret_store(destination)
    existed = spec.env_key in values
    values.pop(spec.env_key, None)
    _write_secret_store(destination, values)
    os.environ.pop(spec.env_key, None)
    return {
        "status": "removed",
        "secret": spec.id,
        "removed": existed,
        "value_printed": False,
    }


def secret_specs_public() -> list[dict[str, object]]:
    """Expose labels and presence fields only; environment key values stay private."""

    return [
        {
            "id": spec.id,
            "label": spec.label,
            "platform": spec.platform,
            "optional": spec.optional,
            "configured": bool(
                os.getenv(spec.env_key)
                or (spec.id == "text_llm_key" and os.getenv("DEEPSEEK_API_KEY"))
            ),
        }
        for spec in SECRET_SPECS
    ]
