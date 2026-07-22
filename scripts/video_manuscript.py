#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fcntl
import importlib.util
import json
import os
import re
import secrets
import signal
import shutil
import subprocess
import sys
import time
import zipfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from vtm_core.asr import faster_whisper_model_path, funasr_ready, prepare_funasr  # noqa: E402
from vtm_core import PIPELINE_VERSION, __version__  # noqa: E402
from vtm_core.configuration import (  # noqa: E402
    SECRET_ENV_KEYS,
    configuration_menu,
    platform_configuration,
    remove_secret,
    secret_specs_public,
    secret_store_path,
    set_secret_interactive,
)
from vtm_core.sources import DocumentSourceAdapter, adapter_for  # noqa: E402
from vtm_core.pipeline import Options, run  # noqa: E402
from vtm_core.output import remove_index_entries, update_indexes  # noqa: E402
from vtm_core.direct_manuscript import create_direct_manuscript  # noqa: E402
from vtm_core.llm import text_client  # noqa: E402
from vtm_core.local_web import (  # noqa: E402
    DEFAULT_PORT,
    browser_token_path,
    load_or_create_browser_token,
    serve_local,
)
from vtm_core.models import Frame, Segment  # noqa: E402
from vtm_core.utils import atomic_json, load_json  # noqa: E402
from vtm_core.visual import (  # noqa: E402
    DEFAULT_VISION_FRAME_BUDGET,
    MAX_ADAPTIVE_VISION_FRAME_BUDGET,
)
from vtm_core.tasks import (  # noqa: E402
    get_task,
    list_tasks,
    mark_deleted,
    mark_restored,
    reserve_task,
    state_root,
    update_task,
)

_RUNTIME_ENV_KEYS = {
    "BILIBILI_COOKIE", "VTM_LLM_API_KEY", "DEEPSEEK_API_KEY", "VTM_LLM_BASE_URL",
    "VTM_LLM_MODEL", "VTM_VAULT", "VTM_STATE_DIR", "VTM_TIMEZONE",
    "VTM_VISION_API_KEY", "VTM_VISION_BASE_URL", "VTM_VISION_MODEL",
    "VTM_MAX_VISION_FRAMES", "VTM_ASR_BACKEND", "VTM_ASR_MODEL", "VTM_VISUAL_HEIGHT",
    "VTM_FINAL_VISUAL_HEIGHT", "VTM_PROGRESS_TARGET",
    "VTM_MAX_CONCURRENT_JOBS",
} | set(SECRET_ENV_KEYS)


def _raise_cancelled(_signum: int, _frame: object) -> None:
    raise KeyboardInterrupt


def install_cancel_handlers() -> None:
    """Turn gateway SIGTERM/SIGINT cancellation into normal CLI cleanup."""
    signal.signal(signal.SIGTERM, _raise_cancelled)
    signal.signal(signal.SIGINT, _raise_cancelled)


def load_runtime_env() -> None:
    configured = os.getenv("VTM_ENV_FILE")
    candidates = (
        [Path(configured).expanduser()]
        if configured
        else [secret_store_path(), Path.home() / ".hermes" / ".env"]
    )
    for path in candidates:
        if not path.is_file():
            continue
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key, value = key.strip(), value.strip()
            if key not in _RUNTIME_ENV_KEYS or key in os.environ:
                continue
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                value = value[1:-1]
            os.environ[key] = value


def default_vault() -> Path:
    return Path(os.getenv("VTM_VAULT", str(Path.home() / "ObsidianVault"))).expanduser()


def duplicate_skill_paths() -> list[str]:
    root = Path.home() / ".hermes" / "skills"
    matches: list[str] = []
    if not root.is_dir():
        return matches
    for manifest in root.rglob("SKILL.md"):
        try:
            prefix = manifest.read_text(encoding="utf-8")[:2000]
        except OSError:
            continue
        if re.search(r"(?m)^name:\s*[\"']?manuloom[\"']?\s*$", prefix):
            matches.append(str(manifest.parent))
    return sorted(matches)


_PROGRESS_STAGES = {
    "正在读取视频信息。": 1,
    "正在读取来源信息。": 1,
    "正在获取或识别字幕。": 2,
    "正在获取完整文字证据。": 2,
    "正在清理和重排完整文字稿。": 3,
    "正在提取并匹配关键画面。": 4,
    "正在生成 Obsidian 文稿。": 5,
    "处理完成。": 6,
}

def progress_label(task_id: int, task_key: str = "") -> str:
    return f"今日任务 {task_id}" + (f" · {task_key}" if task_key else "")


def format_progress(label: str, message: str) -> str:
    stage = _PROGRESS_STAGES.get(message)
    return f"[{label}] [{stage}/6] {message}" if stage else f"[{label}] {message}"


def format_gateway_completion(label: str, result: dict[str, object]) -> str:
    status = "处理完成。"
    title = Path(str(result.get("note") or "视频文稿")).stem
    try:
        frame_count = int(str(result.get("frames") or 0))
    except ValueError:
        frame_count = 0
    is_video = str(result.get("source_kind") or "video") == "video"
    return (
        f"[{label}] [6/6] {status} 标题：{title}；"
        f"{'字幕来源' if is_video else '文字来源'}：{result.get('transcript_source') or '未知'}；"
        f"{'保留画面' if is_video else '保留原图'}：{frame_count}；"
        f"Markdown：{result.get('note')}。"
        f"任务已暂存服务器，需要时说“下载 {result.get('id')}”。"
    )


def _hermes_binary() -> str:
    binary = shutil.which("hermes")
    if not binary:
        candidate = Path.home() / ".local" / "bin" / "hermes"
        binary = str(candidate) if candidate.is_file() else ""
    if not binary:
        raise RuntimeError("hermes send 不可用")
    return binary


def send_hermes_progress(target: str, message: str) -> None:
    """Deliver one progress line through Hermes' credential-reusing send CLI."""
    binary = _hermes_binary()
    completed = subprocess.run(
        [binary, "send", "--to", target, "--quiet", message],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=20,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"hermes send 返回 {completed.returncode}")


def send_hermes_document(target: str, archive: Path) -> None:
    """Send a binary attachment in one deterministic Hermes CLI call."""
    resolved = archive.expanduser().resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"待发送压缩包不存在：{resolved}")
    binary = _hermes_binary()
    completed = subprocess.run(
        [
            binary,
            "send",
            "--to",
            target,
            "--quiet",
            f"[[as_document]] MEDIA:{resolved}",
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        timeout=120,
        check=False,
    )
    if completed.returncode != 0:
        detail = " ".join(completed.stderr.splitlines()).strip()[:240]
        raise RuntimeError(
            f"飞书附件发送失败（hermes send {completed.returncode}）"
            + (f"：{detail}" if detail else "")
        )


class GatewayProgressReporter:
    """Emit auditable output and optionally deliver it directly through Hermes."""

    def __init__(self, label: str, *, target: str = "", sender=None):
        self.label = label
        self.target = str(target or "").strip()
        self.sender = sender or send_hermes_progress
        self.delivery_errors: list[str] = []

    def _emit(self, line: str) -> None:
        print(line, file=sys.stderr, flush=True)
        if not self.target:
            return
        try:
            self.sender(self.target, line)
        except Exception as exc:
            self.delivery_errors.append(f"{type(exc).__name__}: {exc}")

    def progress(self, message: str) -> None:
        # The final line needs result metadata, so main() emits it after the
        # registry and note paths have been committed.
        if _PROGRESS_STAGES.get(message) == 6:
            return
        self._emit(format_progress(self.label, message))

    def status(self, marker: str, message: str) -> None:
        clean = " ".join(str(message).splitlines()).strip()
        self._emit(f"[{self.label}] [{marker}] {clean}")

    def completion(self, result: dict[str, object]) -> None:
        self._emit(format_gateway_completion(self.label, result))

    def terminal(self, marker: str, message: str) -> None:
        clean = " ".join(str(message).splitlines()).strip()
        self._emit(f"[{self.label}] [6/6 · {marker}] {clean}")


def find_existing_video_task(vault: Path, bvid: str, part: int = 1) -> dict[str, object] | None:
    blocking = {"complete", "needs_review", "queued", "running", "failed", "cancelled"}
    matches = [
        item
        for item in list_tasks(vault, all_tasks=True)
        if str(item.get("bvid") or "").lower() == bvid.lower()
        and int(item.get("part") or 1) == int(part)
        and item.get("status") in blocking
    ]
    return max(matches, key=lambda item: str(item.get("created_at") or ""), default=None)


def find_existing_source_task(vault: Path, source_key: str) -> dict[str, object] | None:
    blocking = {"complete", "needs_review", "queued", "running", "failed", "cancelled"}
    matches = [
        item
        for item in list_tasks(vault, all_tasks=True)
        if str(item.get("source_key") or "") == source_key
        and item.get("status") in blocking
    ]
    return max(matches, key=lambda item: str(item.get("created_at") or ""), default=None)


def _resolve_by_bvid(vault: Path, bvid: str) -> dict[str, object]:
    matches = [item for item in list_tasks(vault, all_tasks=True) if str(item.get("bvid", "")).lower() == bvid.lower()]
    if not matches:
        raise KeyError(f"找不到 {bvid} 的笔记")
    complete = [item for item in matches if item.get("status") == "complete"]
    return max(complete or matches, key=lambda item: str(item.get("created_at") or ""))


def sync_task_from_audit(
    vault: Path,
    task_key: str,
    *,
    status: str,
    error: str = "",
) -> dict[str, object]:
    """Persist metadata learned after reservation, including failed/cancelled jobs."""
    metadata = load_json(state_root() / "tasks" / task_key / "metadata.json") or {}
    updates: dict[str, object] = {"status": status, "error": error}
    for key in (
        "bvid", "platform", "source_kind", "source_id", "source_key",
        "part", "url", "title",
    ):
        value = metadata.get(key)
        if value not in (None, ""):
            updates[key] = value
    return update_task(vault, task_key, **updates)


def bundle_job(
    vault: Path, bvid: str | None = None, *, task_id: str | int | None = None,
    include_source: bool = False,
) -> dict[str, object]:
    task = get_task(vault, task_id) if task_id is not None else _resolve_by_bvid(vault, str(bvid or ""))
    if task.get("status") != "complete":
        raise RuntimeError(f"任务 {task['task_key']} 当前状态为 {task.get('status')}，不可下载")
    job_dir = Path(str(task.get("job_dir") or "")).expanduser().resolve()
    note = Path(str(task.get("note") or "")).expanduser().resolve()
    vault_root = vault.expanduser().resolve()
    if (
        not job_dir.is_dir()
        or not job_dir.is_relative_to(vault_root)
        or not note.is_file()
        or note.parent != job_dir
    ):
        raise FileNotFoundError("任务笔记目录不存在或记录不一致")
    selected = [note]
    assets = job_dir / "assets"
    if assets.is_dir():
        asset_files = [path for path in assets.rglob("*") if path.is_file()]
        if any(path.is_symlink() for path in asset_files):
            raise RuntimeError("笔记资源目录包含符号链接，拒绝打包")
        selected.extend(asset_files)
    source = state_root() / "tasks" / str(task["task_key"])
    if include_source and source.is_dir():
        source_files = [path for path in source.rglob("*") if path.is_file()]
        if any(path.is_symlink() for path in source_files):
            raise RuntimeError("任务审计目录包含符号链接，拒绝打包")
        selected.extend(source_files)
    exports = state_root() / "exports"
    exports.mkdir(parents=True, exist_ok=True)
    archive_id = str(task.get("source_id") or task.get("bvid") or "source")
    archive_kind = (
        "video-manuscript"
        if str(task.get("source_kind") or "video") == "video"
        else "source-manuscript"
    )
    archive = exports / f"{task['task_key']}-{archive_id}-{archive_kind}.zip"
    top = job_dir.name
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as handle:
        handle.writestr((Path(top) / "assets").as_posix() + "/", b"")
        for path in selected:
            if path.is_relative_to(job_dir):
                arcname = (Path(top) / path.relative_to(job_dir)).as_posix()
            else:
                arcname = (Path(top) / "source" / path.relative_to(source)).as_posix()
            handle.write(path, arcname=arcname)
    with zipfile.ZipFile(archive) as handle:
        roots = {Path(name).parts[0] for name in handle.namelist() if Path(name).parts}
    if roots != {top}:
        archive.unlink(missing_ok=True)
        raise RuntimeError("压缩包边界检查失败")
    update_task(vault, str(task["task_key"]), bundle=str(archive))
    return {
        "status": "complete", "id": task["id"], "task_key": task["task_key"],
        "bvid": task.get("bvid"), "bundle": str(archive), "files": len(selected),
        "expires_in_hours": 24, "includes_source": include_source,
    }


def delete_job(vault: Path, identity: str) -> dict[str, object]:
    task = get_task(vault, identity)
    job_value = str(task.get("job_dir") or "").strip()
    job_dir = Path(job_value).expanduser().resolve() if job_value else None
    vault_root = vault.expanduser().resolve()
    if (job_dir is None or not job_dir.is_dir()) and task.get("status") in {"failed", "cancelled"}:
        trash = state_root() / "trash" / str(task["task_key"])
        if trash.exists():
            raise RuntimeError("回收站中已存在同名任务，请先检查")
        trash.mkdir(parents=True)
        source = state_root() / "tasks" / str(task["task_key"])
        if source.is_dir():
            shutil.move(str(source), str(trash / "audit"))
        atomic_json(
            trash / "record.json",
            {
                "status": task.get("status"),
                "error": task.get("error"),
            },
        )
        marked = mark_deleted(vault, str(task["task_key"]), trash)
        return {
            "status": "deleted",
            "id": marked["id"],
            "task_key": marked["task_key"],
            "retention_days": 30,
            "record_only": True,
        }
    if job_dir is None or not job_dir.is_dir() or not job_dir.is_relative_to(vault_root):
        raise RuntimeError("笔记目录不存在或不在 Obsidian Vault 内，拒绝删除")
    trash = state_root() / "trash" / str(task["task_key"])
    if trash.exists():
        raise RuntimeError("回收站中已存在同名任务，请先检查")
    trash.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(job_dir), str(trash))
    marked = mark_deleted(vault, str(task["task_key"]), trash)
    remove_index_entries(vault_root, str(task["task_key"]))
    bundle = str(task.get("bundle") or "")
    if bundle:
        Path(bundle).unlink(missing_ok=True)
    return {"status": "deleted", "id": marked["id"], "task_key": marked["task_key"], "retention_days": 30}


def plan_bulk_delete(
    vault: Path,
    *,
    keep: list[str],
    all_history: bool,
) -> dict[str, object]:
    if not all_history:
        raise ValueError("批量删除必须明确使用 --all-history")
    keep_keys: set[str] = set()
    for identity in keep:
        keep_keys.add(str(get_task(vault, identity)["task_key"]))
    tasks = list_tasks(vault, all_tasks=True)
    active = [
        str(item["task_key"])
        for item in tasks
        if item.get("status") in {"queued", "running"}
    ]
    candidates = [
        str(item["task_key"])
        for item in tasks
        if str(item["task_key"]) not in keep_keys
        and item.get("status") not in {"queued", "running"}
    ]
    token = secrets.token_hex(6)
    pending = state_root() / "pending"
    pending.mkdir(parents=True, exist_ok=True)
    payload = {
        "kind": "bulk-delete",
        "token": token,
        "created_at": time.time(),
        "expires_at": time.time() + 15 * 60,
        "vault": str(vault.expanduser().resolve()),
        "task_keys": candidates,
        "keep_keys": sorted(keep_keys),
        "active_protected": active,
        "status": "planned",
    }
    atomic_json(pending / f"bulk-delete-{token}.json", payload)
    return {
        "status": "confirmation_required",
        "operation": "bulk-delete",
        "confirmation_token": token,
        "delete_count": len(candidates),
        "keep": sorted(keep_keys),
        "active_protected": active,
        "expires_in_minutes": 15,
    }


def confirm_bulk_delete(
    vault: Path,
    token: str,
    *,
    send_target: str = "",
) -> dict[str, object]:
    if not re.fullmatch(r"[0-9a-f]{12}", token):
        raise ValueError("无效的批量删除确认令牌")
    plan_path = state_root() / "pending" / f"bulk-delete-{token}.json"
    plan = load_json(plan_path)
    if not plan or plan.get("kind") != "bulk-delete":
        raise KeyError("找不到待确认的批量删除计划")
    if str(plan.get("vault")) != str(vault.expanduser().resolve()):
        raise RuntimeError("批量删除计划不属于当前 Vault")
    if float(plan.get("expires_at") or 0) < time.time():
        raise RuntimeError("批量删除确认已过期，请重新发起")
    if plan.get("status") != "planned":
        raise RuntimeError("该批量删除计划已经执行或失效")
    task_keys = [str(value) for value in plan.get("task_keys") or []]
    if send_target:
        send_hermes_progress(
            send_target,
            f"[任务管理 · {token}] [RUNNING] 正在批量删除 {len(task_keys)} 个历史任务；视频生成任务不会因此暂停。",
        )
    deleted: list[str] = []
    failures: list[dict[str, str]] = []
    total = len(task_keys)
    for index, task_key in enumerate(task_keys, start=1):
        try:
            delete_job(vault, task_key)
            deleted.append(task_key)
        except Exception as exc:
            failures.append({"task_key": task_key, "error": str(exc)})
        if send_target and total > 10 and (index % 10 == 0 or index == total):
            send_hermes_progress(
                send_target,
                f"[任务管理 · {token}] [RUNNING] 已处理 {index}/{total}。",
            )
    plan["status"] = "complete" if not failures else "partial"
    plan["completed_at"] = time.time()
    plan["deleted"] = deleted
    plan["failures"] = failures
    atomic_json(plan_path, plan)
    marker = "COMPLETE" if not failures else "PARTIAL"
    if send_target:
        send_hermes_progress(
            send_target,
            f"[任务管理 · {token}] [{marker}] 批量删除结束：成功 {len(deleted)}，失败 {len(failures)}。",
        )
    return {
        "status": "complete" if not failures else "partial",
        "operation": "bulk-delete",
        "deleted_count": len(deleted),
        "deleted": deleted,
        "failure_count": len(failures),
        "failures": failures,
        "retention_days": 30,
    }


def submit_detached(raw_argv: list[str]) -> dict[str, object]:
    child_argv = list(raw_argv)
    try:
        command_index = child_argv.index("submit")
    except ValueError as exc:
        raise RuntimeError("无法构造后台视频任务") from exc
    child_argv[command_index] = "run"
    log_root = state_root() / "logs" / "submissions"
    log_root.mkdir(parents=True, exist_ok=True)
    log_path = log_root / f"{int(time.time())}-{os.getpid()}-{secrets.token_hex(3)}.log"
    with log_path.open("ab", buffering=0) as handle:
        process = subprocess.Popen(
            [str(SCRIPT_DIR / "vtm"), *child_argv],
            stdin=subprocess.DEVNULL,
            stdout=handle,
            stderr=handle,
            start_new_session=True,
            close_fds=True,
        )
    return {
        "status": "submitted",
        "pid": process.pid,
        "task_number_source": "first_progress_message",
        "chat_session_released": True,
        "assistant_reply": "已提交后台处理，任务编号将在第一条进度中显示。",
        "assistant_reply_contract": "return_assistant_reply_verbatim_and_stop",
        "progress_delivery": "out_of_band_only",
        "progress_in_this_result": False,
    }


@contextmanager
def video_job_slot(
    vault: Path,
    task_key: str,
    reporter: GatewayProgressReporter | None,
) -> Iterator[int]:
    try:
        limit = int(os.getenv("VTM_MAX_CONCURRENT_JOBS", "2"))
    except ValueError:
        limit = 2
    limit = max(1, min(limit, 4))
    lock_root = state_root() / "locks"
    lock_root.mkdir(parents=True, exist_ok=True)
    waiting_reported = False
    handle = None
    slot = -1
    while handle is None:
        for index in range(limit):
            candidate = (lock_root / f"video-job-{index}.lock").open("a+")
            try:
                fcntl.flock(candidate.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                candidate.close()
                continue
            handle = candidate
            slot = index
            break
        if handle is None:
            update_task(
                vault,
                task_key,
                status="queued",
                pid=os.getpid(),
                stage=0,
                stage_message="处理槽已满，正在排队",
            )
            if reporter and not waiting_reported:
                reporter.status("QUEUED", f"当前已有 {limit} 个视频任务在处理，本任务已排队；聊天仍可继续使用。")
            waiting_reported = True
            time.sleep(2)
    try:
        update_task(
            vault,
            task_key,
            status="running",
            pid=os.getpid(),
            stage=0,
            stage_message="已取得处理槽",
        )
        if reporter and waiting_reported:
            reporter.status("RUNNING", "排队结束，现已开始处理。")
        yield slot
    finally:
        if handle is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()


def restore_job(vault: Path, identity: str) -> dict[str, object]:
    task = get_task(vault, identity, include_deleted=True)
    if not task.get("deleted_at"):
        raise RuntimeError("该任务不在回收站")
    trash = Path(str(task.get("trash_dir") or "")).expanduser().resolve()
    trash_root = (state_root() / "trash").resolve()
    if not trash.is_relative_to(trash_root):
        raise RuntimeError("回收站路径不在受管状态目录内，拒绝恢复")
    record = load_json(trash / "record.json") if trash.is_dir() else None
    if record:
        source = state_root() / "tasks" / str(task["task_key"])
        audit = trash / "audit"
        if source.exists():
            raise RuntimeError("任务审计目录已存在，拒绝覆盖恢复")
        if audit.is_dir():
            source.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(audit), str(source))
        restored = update_task(
            vault,
            str(task["task_key"]),
            status=str(record.get("status") or "failed"),
            error=str(record.get("error") or ""),
            deleted_at="",
            trash_dir="",
        )
        shutil.rmtree(trash, ignore_errors=True)
        return {
            "status": restored["status"],
            "id": restored["id"],
            "task_key": restored["task_key"],
            "record_only": True,
        }
    destination = Path(str(task.get("job_dir") or "")).expanduser().resolve()
    vault_root = vault.expanduser().resolve()
    if not destination.is_relative_to(vault_root):
        raise RuntimeError("原笔记路径不在 Obsidian Vault 内，拒绝恢复")
    notes_in_trash = sorted(trash.glob("*.md")) if trash.is_dir() else []
    if not trash.is_dir() or not notes_in_trash or destination.exists():
        raise RuntimeError("回收站内容不存在，或原位置已被占用")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(trash), str(destination))
    notes = sorted(destination.glob("*.md"))
    restored = mark_restored(vault, str(task["task_key"]), destination, notes[0])
    metadata = load_json(state_root() / "tasks" / str(task["task_key"]) / "metadata.json") or {}
    metadata.update(
        task_key=task["task_key"], task_date=task["task_date"],
        title=task.get("title") or notes[0].stem,
    )
    update_indexes(vault.expanduser().resolve(), notes[0], metadata)
    return {"status": "complete", "id": restored["id"], "task_key": restored["task_key"], "note": restored["note"]}


def cancel_job(vault: Path, identity: str | None = None, *, latest_running: bool = False) -> dict[str, object]:
    if latest_running:
        running = [
            item
            for item in list_tasks(vault, all_tasks=True)
            if item.get("status") in {"queued", "running"}
        ]
        if not running:
            raise KeyError("当前没有排队中或运行中的视频任务")
        task = max(running, key=lambda item: str(item.get("created_at") or ""))
    elif identity:
        task = get_task(vault, identity)
    else:
        raise ValueError("需要 --task 或 --latest-running")
    if task.get("status") not in {"queued", "running"}:
        raise RuntimeError(
            f"任务 {task['task_key']} 当前状态为 {task.get('status')}，不是排队中或运行中任务"
        )
    task_key = str(task["task_key"])
    pid = int(task.get("pid") or 0)
    if pid > 1 and pid != os.getpid():
        try:
            if os.getpgid(pid) == pid:
                os.killpg(pid, signal.SIGTERM)
            else:
                os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except PermissionError as exc:
            raise RuntimeError(f"无法终止任务进程 {pid}") from exc
    shutil.rmtree(state_root() / "work" / task_key, ignore_errors=True)
    sources = vault.expanduser().resolve() / "Sources" / "Videos"
    if sources.is_dir():
        for candidate in sources.rglob(f"{task_key}-*"):
            if candidate.is_dir() and not candidate.is_symlink() and not any(candidate.glob("*.md")):
                shutil.rmtree(candidate, ignore_errors=True)
    remove_index_entries(vault.expanduser().resolve(), task_key)
    atomic_json(state_root() / "tasks" / task_key / "job.json", {
        "status": "cancelled", "error": "用户已终止任务"
    })
    updated = update_task(
        vault,
        task_key,
        status="cancelled",
        error="用户已终止任务",
        pid=None,
        stage=6,
        stage_message="已取消",
    )
    return {"status": "cancelled", "id": updated["id"], "task_key": task_key}


def cleanup_storage() -> dict[str, int]:
    now = time.time()
    removed = {"work": 0, "exports": 0, "trash": 0}
    for name, age_hours in (("work", 6), ("exports", 24), ("trash", 24 * 30)):
        root = state_root() / name
        if not root.is_dir():
            continue
        for path in root.iterdir():
            try:
                stale = now - path.stat().st_mtime > age_hours * 3600
            except OSError:
                continue
            if not stale:
                continue
            shutil.rmtree(path, ignore_errors=True) if path.is_dir() else path.unlink(missing_ok=True)
            removed[name] += 1
    return removed


def evaluate_text_core(
    source_dir: Path,
    output_dir: Path | None = None,
    *,
    llm_model: str | None = None,
    reset_checkpoint: bool = False,
) -> dict[str, object]:
    """Evaluate manuscript editing without reserving a task or publishing a note."""
    source = source_dir.expanduser().resolve()
    raw = load_json(source / "raw-transcript.json")
    if not isinstance(raw, dict) or not isinstance(raw.get("segments"), list):
        raise FileNotFoundError("评测目录缺少有效的 raw-transcript.json")
    metadata = load_json(source / "metadata.json") or {}
    segments = [Segment(**row) for row in raw["segments"]]
    destination = (
        output_dir.expanduser().resolve()
        if output_dir
        else source / "evaluations" / PIPELINE_VERSION
    )
    destination.mkdir(parents=True, exist_ok=True)
    checkpoint = destination / "manuscript-checkpoint.json"
    if reset_checkpoint:
        checkpoint.unlink(missing_ok=True)
    title = str(metadata.get("title") or metadata.get("bvid") or source.name)
    owner = str(metadata.get("owner") or metadata.get("creator") or "")
    visual_manifest = load_json(source / "visual-manifest.json") or {}
    frames = [
        Frame(**row)
        for row in visual_manifest.get("frames", [])
        if isinstance(row, dict)
    ]
    paragraphs, coverage = create_direct_manuscript(
        segments,
        text_client(llm_model),
        context=f"标题：{title}；UP 主：{owner}",
        frames=frames,
        checkpoint_path=checkpoint,
    )
    preview_lines = [f"# {title}", ""]
    for paragraph in paragraphs:
        if paragraph.heading:
            preview_lines.extend([f"## {paragraph.heading}", ""])
        if paragraph.subheading:
            preview_lines.extend([f"### {paragraph.subheading}", ""])
        preview_lines.extend([paragraph.text, ""])
    preview = destination / "manuscript-preview.md"
    preview.write_text("\n".join(preview_lines).rstrip() + "\n", encoding="utf-8")
    atomic_json(destination / "coverage.json", coverage)
    atomic_json(
        destination / "clean-transcript.json",
        {"paragraphs": [paragraph.to_dict() for paragraph in paragraphs]},
    )
    return {
        "status": "pass",
        "pipeline_version": PIPELINE_VERSION,
        "source_dir": str(source),
        "evaluation_dir": str(destination),
        "preview": str(preview),
        "task_reserved": False,
        "vault_written": False,
        "editorial_diagnostics": coverage.get("editorial_diagnostics"),
    }


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Create a complete illustrated manuscript from a supported source")
    commands = root.add_subparsers(dest="command", required=True)
    run_options = argparse.ArgumentParser(add_help=False)
    run_options.add_argument("--url")
    run_options.add_argument("--vault", type=Path, default=default_vault())
    run_options.add_argument("--part", type=int)
    run_options.add_argument("--cookies-file", type=Path)
    run_options.add_argument("--no-visual", action="store_true")
    run_options.add_argument("--max-frames", type=int, default=60)
    run_options.add_argument("--keep-video", action="store_true")
    run_options.add_argument("--llm-model")
    run_options.add_argument("--asr-model", default=os.getenv("VTM_ASR_MODEL", "medium"))
    run_options.add_argument("--asr-backend", choices=("auto", "funasr", "faster-whisper"), default=os.getenv("VTM_ASR_BACKEND", "auto"))
    run_options.add_argument("--visual-height", type=int, default=int(os.getenv("VTM_VISUAL_HEIGHT", "720")))
    run_options.add_argument("--final-visual-height", type=int, default=int(os.getenv("VTM_FINAL_VISUAL_HEIGHT", "1080")))
    run_options.add_argument("--resume", type=Path)
    run_options.add_argument("--force", action="store_true", help="regenerate even when the same source already exists")
    run_options.add_argument("--no-progress", action="store_true")
    run_options.add_argument(
        "--gateway-output",
        action="store_true",
        help="emit only concise messaging-platform progress and completion text",
    )
    run_options.add_argument(
        "--progress-target",
        default=os.getenv("VTM_PROGRESS_TARGET", ""),
        help="optional Hermes send target such as feishu or feishu:chat_id",
    )
    commands.add_parser("run", parents=[run_options], help="process a supported source")
    commands.add_parser(
        "submit",
        parents=[run_options],
        help="detach a video job so the messaging session is released immediately",
    )
    inspect_parser = commands.add_parser("inspect", help="inspect supported-source metadata and text evidence")
    inspect_parser.add_argument("--url", required=True)
    inspect_parser.add_argument("--part", type=int)
    commands.add_parser("doctor")
    commands.add_parser("contract", help="print the deterministic manuscript and task protocol")
    configure_parser = commands.add_parser(
        "configure",
        help="show configuration guidance or securely manage one allowlisted secret",
    )
    configure_parser.add_argument(
        "action",
        nargs="?",
        default="menu",
        choices=("menu", "status", "platform", "secret", "remove"),
    )
    configure_parser.add_argument("target", nargs="?")
    configure_parser.add_argument(
        "--confirm",
        action="store_true",
        help="required when removing a configured secret",
    )
    evaluate_parser = commands.add_parser(
        "evaluate", help="evaluate text editing from existing audit artifacts without creating a task"
    )
    evaluate_parser.add_argument("--source-dir", type=Path, required=True)
    evaluate_parser.add_argument("--output-dir", type=Path)
    evaluate_parser.add_argument("--llm-model")
    evaluate_parser.add_argument("--reset-checkpoint", action="store_true")
    commands.add_parser("prepare-asr")
    bundle_parser = commands.add_parser("bundle")
    identity = bundle_parser.add_mutually_exclusive_group(required=True)
    identity.add_argument("--task")
    identity.add_argument("--bvid")
    bundle_parser.add_argument("--vault", type=Path, default=default_vault())
    bundle_parser.add_argument("--include-source", action="store_true")
    bundle_parser.add_argument(
        "--send-target",
        default="",
        help="package and immediately send the ZIP through Hermes, for example feishu",
    )
    tasks_parser = commands.add_parser("tasks")
    tasks_parser.add_argument("--vault", type=Path, default=default_vault())
    tasks_parser.add_argument("--all", action="store_true")
    tasks_parser.add_argument("--include-deleted", action="store_true")
    delete_parser = commands.add_parser("delete")
    delete_parser.add_argument("--task", required=True)
    delete_parser.add_argument("--vault", type=Path, default=default_vault())
    delete_parser.add_argument("--confirm", action="store_true", required=True)
    bulk_delete_parser = commands.add_parser(
        "delete-many",
        help="plan or confirm one deterministic bulk soft-delete operation",
    )
    bulk_delete_parser.add_argument("--vault", type=Path, default=default_vault())
    bulk_delete_parser.add_argument("--all-history", action="store_true")
    bulk_delete_parser.add_argument("--keep", action="append", default=[])
    bulk_delete_parser.add_argument("--confirm-token")
    bulk_delete_parser.add_argument("--send-target", default="")
    restore_parser = commands.add_parser("restore")
    restore_parser.add_argument("--task", required=True)
    restore_parser.add_argument("--vault", type=Path, default=default_vault())
    cancel_parser = commands.add_parser("cancel")
    cancel_identity = cancel_parser.add_mutually_exclusive_group(required=True)
    cancel_identity.add_argument("--task")
    cancel_identity.add_argument("--latest-running", action="store_true")
    cancel_parser.add_argument("--vault", type=Path, default=default_vault())
    commands.add_parser("cleanup")
    token_parser = commands.add_parser(
        "browser-token",
        help="create or print the local browser-extension pairing token",
    )
    token_parser.add_argument("--rotate", action="store_true")
    serve_parser = commands.add_parser(
        "serve",
        help="run the loopback-only local web and browser-extension bridge",
    )
    serve_parser.add_argument("--vault", type=Path, default=default_vault())
    serve_parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    return root


def doctor() -> dict[str, object]:
    asr_model = os.getenv("VTM_ASR_MODEL", "medium")
    faster_installed = importlib.util.find_spec("faster_whisper") is not None
    faster_model = faster_whisper_model_path(asr_model) if faster_installed else None
    state = state_root()
    state.mkdir(parents=True, exist_ok=True)
    duplicates = duplicate_skill_paths()
    return {
        "package_version": __version__,
        "pipeline_version": PIPELINE_VERSION,
        "python": sys.version.split()[0], "ffmpeg": shutil.which("ffmpeg"), "ffprobe": shutil.which("ffprobe"),
        "tesseract": shutil.which("tesseract"), "yt_dlp": importlib.util.find_spec("yt_dlp") is not None,
        "asr_ready": funasr_ready() or faster_model is not None, "funasr_vad_punc_ready": funasr_ready(),
        "faster_whisper_model_ready": faster_model is not None,
        "bilibili_cookie": bool(os.getenv("BILIBILI_COOKIE")),
        "zhihu_client": importlib.util.find_spec("zhihu_cli") is not None,
        "zhihu_z_c0_configured": bool(os.getenv("ZHIHU_Z_C0")),
        "douyin_public_adapter": True,
        "xiaohongshu_public_adapter": True,
        "text_llm_key": bool(os.getenv("VTM_LLM_API_KEY") or os.getenv("DEEPSEEK_API_KEY")),
        "text_model": os.getenv("VTM_LLM_MODEL", "deepseek-v4-flash"),
        "vision_configured": all(os.getenv(name) for name in ("VTM_VISION_API_KEY", "VTM_VISION_BASE_URL", "VTM_VISION_MODEL")),
        "vision_model": os.getenv("VTM_VISION_MODEL", ""),
        "source_proxy_configured": bool(os.getenv("VTM_SOURCE_PROXY")),
        "default_vision_frame_budget": DEFAULT_VISION_FRAME_BUDGET,
        "vision_frame_budget_policy": "transcript_planner_ranges_plus_distinct_scenes",
            "max_adaptive_vision_frame_budget": MAX_ADAPTIVE_VISION_FRAME_BUDGET,
            "max_paid_vision_reviews_per_minute": 2,
        "visual_analysis_height": int(os.getenv("VTM_VISUAL_HEIGHT", "720")),
        "final_visual_height": int(os.getenv("VTM_FINAL_VISUAL_HEIGHT", "1080")),
        "state_dir": str(state), "free_disk_gb": round(shutil.disk_usage(state).free / 1024**3, 1),
        "duplicate_skill_paths": duplicates,
        "skill_installation_ok": len(duplicates) <= 1,
        "progress_target": os.getenv("VTM_PROGRESS_TARGET", ""),
        "hermes_send_available": bool(shutil.which("hermes") or (Path.home() / ".local" / "bin" / "hermes").is_file()),
        "max_concurrent_video_jobs": max(1, min(int(os.getenv("VTM_MAX_CONCURRENT_JOBS", "2")), 4)),
    }


def contract() -> dict[str, object]:
    """Return the executable protocol without consulting Agent memory."""
    return {
        "pipeline_version": PIPELINE_VERSION,
        "authority": "current SKILL.md and deterministic CLI",
        "output": {
            "kind": "detailed_edited_manuscript",
            "summary_only": False,
            "raw_asr_publishable": False,
            "unsupported_editor_commentary": False,
        },
        "preservation": {
            "required": [
                "claims", "reasons", "explanations", "examples", "steps",
                "commands", "code", "urls", "numbers", "conditions",
                "exceptions", "comparisons", "warnings", "exact_names_titles",
                "conclusions",
            ],
            "operational_details": [
                "entry_path", "exact_buttons_options", "permissions",
                "input_purpose", "adjustable_parameters", "wait_time",
                "cost_quota", "output_fields", "verification", "failure_conditions",
            ],
            "removable_only": [
                "fillers", "false_starts", "promotional_requests", "true_repetition",
            ],
            "detail_preservation_is_semantic_llm_review": True,
            "mechanical_word_retention_gate": False,
        },
        "source_id_audit": {
            "every_source_assigned_to_one_chronological_paragraph": True,
            "document_blocks_assigned_in_original_order": True,
            "document_order_is_not_published_as_video_time": True,
            "model_copies_individual_ids": False,
            "model_output_fields": ["start_source_id"],
            "assignment_method": "deterministic_paragraph_start_ranges",
            "filler_text_is_forced_into_prose": False,
        },
        "semantic_editing": {
            "architecture": "whole_transcript_plan_visual_reconcile_write_restore_copyedit",
            "document_passes": 4,
            "editing_stages": [
                "whole_transcript_structure_and_visual_request_planning",
                "bounded_prewrite_asr_and_term_reconciliation",
                "golden_style_structured_writing_with_visual_evidence",
                "whole_transcript_detail_restoration",
                "golden_style_concise_copyedit",
            ],
            "writer_reads_complete_transcript": True,
            "reviewer_reads_complete_transcript_and_draft": True,
            "writer_reads_visual_evidence_before_drafting": True,
            "writer_reads_bundled_golden_style_reference": True,
            "style_reference_facts_copyable": False,
            "conciseness_removes_repetition_not_independent_details": True,
            "asr_uncertainty_may_not_delete_surrounding_meaning": True,
            "already_good_source_prose_may_remain_close_to_source": True,
            "article_plan_is_structure_not_factual_authority": True,
            "adjacent_asr_fragments_repaired_as_one_sentence": True,
            "logical_polarity_preserved_during_asr_repair": True,
            "high_confidence_visible_term_overrides_phonetic_gibberish": True,
            "reconciliation_minimal_semantic_anchors_required_in_final": True,
            "reconciliation_full_sentence_verbatim_required": False,
            "same_timestamp_visual_conflict_checks_unflagged_uppercase_phonetics": True,
            "each_planned_paragraph_receives_complete_local_source_excerpt": True,
            "detail_and_final_receive_aligned_source_draft_packets": True,
            "tutorial_step_subheadings_supported": True,
            "promotional_outro_removed": True,
            "regression_confirmed_short_asr_residues_repaired_after_copyedit": True,
            "llm_plans_sections_paragraphs_and_sentence_rewriting": True,
            "program_decides_semantic_importance": False,
            "format_response_attempts_per_pass": 2,
            "resume_reuses_persisted_raw_transcript": True,
            "transient_provider_retry_attempts": 3,
            "semantic_checkpoint_source_signature": True,
            "final_with_obvious_residue_retried_once": True,
            "failed_draft_publishable": False,
            "text_provider_openai_compatible_and_swappable": True,
        },
        "developer_evaluation": {
            "existing_raw_transcript_reusable": True,
            "reserves_task_number": False,
            "writes_vault": False,
            "downloads_media": False,
            "checkpoint_reusable": True,
        },
        "acquisition": {
            "source_adapter_registry": True,
            "bilibili_behavior_preserved_behind_adapter": True,
            "future_sources_fork_manuscript_core": False,
            "installed_video_platforms": ["bilibili", "youtube", "douyin"],
            "installed_document_platforms": ["generic_web", "zhihu", "xiaohongshu"],
            "youtube_public_mode_requires_api_key": False,
            "youtube_manual_subtitle_precedes_automatic": True,
            "youtube_automatic_caption_prefers_original_language": True,
            "youtube_missing_subtitle_uses_one_audio_stream_asr": True,
            "source_network_requests_have_bounded_timeouts": True,
            "generic_web_public_http_only": True,
            "generic_web_private_network_requests_rejected": True,
            "generic_web_navigation_comments_recommendations_excluded": True,
            "generic_web_preserves_headings_lists_tables_code_and_original_images": True,
            "generic_web_primary_extractor": "readability-lxml",
            "generic_web_structured_metadata_parser": "extruct",
            "generic_web_structured_fidelity_fallback": True,
            "zhihu_upstream_client": "zhihu-tui",
            "zhihu_public_read_attempted_before_session": True,
            "zhihu_optional_session_secret": "ZHIHU_Z_C0",
            "zhihu_comments_excluded": True,
            "zhihu_links_formulas_code_and_original_images_preserved": True,
            "douyin_upstream_parser": "social-post-extractor-mcp",
            "douyin_public_share_mode_requires_key": False,
            "douyin_missing_subtitle_uses_one_video_download_local_asr": True,
            "douyin_analysis_video_capped_at_configured_height": True,
            "douyin_original_media_reused_for_final_frame_recapture": True,
            "xiaohongshu_upstream_parser": "social-post-extractor-mcp",
            "xiaohongshu_public_image_note_requires_key": False,
            "xiaohongshu_text_and_original_image_order_preserved": True,
            "xiaohongshu_video_notes_excluded": True,
            "optional_source_proxy_is_allowlisted_secret": True,
            "order": [
                "native_or_ai_subtitle",
                "authenticated_ai_conclusion",
                "one_audio_stream_plus_prepared_local_asr",
            ],
            "bilibili_yt_dlp_is_fallback": True,
            "youtube_yt_dlp_is_public_adapter_backend": True,
            "models_downloaded_during_user_job": False,
        },
        "visuals": {
            "analysis_height": 720,
            "final_requested_height": 1080,
            "paid_vision_frame_policy": "AI-planned transcript ranges × locally distinct scenes/slides",
            "short_video_baseline_paid_vision_frames": DEFAULT_VISION_FRAME_BUDGET,
            "maximum_paid_vision_frames": MAX_ADAPTIVE_VISION_FRAME_BUDGET,
            "paid_vision_frames_temporally_distributed": True,
            "paid_vision_frames_semantically_requested": True,
            "multiple_distinct_frames_per_paragraph_supported": True,
            "copyable_visual_text_requires_high_complete_vision_or_ocr_confirmation": True,
            "planner_requests_visual_time_ranges_before_writing": True,
            "dedicated_visual_planner_reads_complete_transcript": True,
            "dedicated_visual_planner_changes_text_plan": False,
            "planner_requested_ranges_prioritized_for_paid_vision": True,
            "qwen_evidence_available_to_deepseek_writer": True,
            "partial_or_dense_evidence_keeps_image": True,
            "ocr_gibberish_filtered_before_visual_editing": True,
            "visual_text_classification_batch_size": 12,
            "classification_failure_keeps_aligned_original": True,
            "medium_or_low_confidence_visual_text_publishable": False,
            "partial_or_unverified_visual_text_publishable": False,
            "copyable_visual_text_minimum_ocr_confidence": 50,
            "uncertain_visual_description_used_as_alt_text": False,
            "nearby_duplicate_aligned_frames_removed": True,
            "complete_simple_text_may_replace_image": True,
            "decorative_or_zero_information_gain_frames_removed": True,
            "same_template_requested_text_changes_preserved_for_review": True,
            "paid_vision_reviews_dynamic_cap_per_minute": 2,
            "asr_suspect_visual_endpoints_prioritized_within_same_budget": True,
            "vision_description_length_alone_does_not_force_image_retention": True,
            "images_follow_matching_passage": True,
            "document_original_images_follow_preceding_content_block": True,
            "document_original_images_have_no_fake_video_timestamp": True,
            "image_gallery_before_manuscript": False,
            "screen_only_facts_use_callout": "画面补充",
            "vision_provider_openai_compatible_and_swappable": True,
            "model_switch_preserves_skill_contract_not_identical_model_quality": True,
        },
        "release_gates": {
            "valid_structured_llm_document_required": True,
            "workflow_headings_required_for_long_video": True,
            "strict_chronological_source_mapping_required": True,
            "semantic_quality_checked_by_second_llm_pass": True,
            "failed_note_written_to_vault": False,
            "failed_note_indexed_or_downloadable": False,
        },
        "progress": {
            "stages": 6,
            "detached_submit_required_on_hermes": True,
            "chat_session_released_after_submit": True,
            "maximum_concurrent_video_jobs": max(
                1, min(int(os.getenv("VTM_MAX_CONCURRENT_JOBS", "2")), 4)
            ),
            "overflow_jobs_report_queued_state": True,
            "task_stage_persisted_for_status_queries": True,
            "stage_changes_only": True,
            "failure_terminal": "[6/6 · FAILED]",
            "cancellation_terminal": "CANCELLED",
        },
        "tasks": {
            "bare_number_is_instruction": False,
            "download_is_one_shot_attachment": True,
            "duplicate_requires_explicit_force": True,
            "regeneration_creates_new_task": True,
            "daily_numbers_reset_and_are_not_reused": True,
            "record_only_failed_cancelled_delete_restore": True,
            "downloadable_statuses": ["complete"],
            "soft_delete_days": 30,
            "bulk_delete_is_single_deterministic_operation": True,
            "bulk_delete_confirmation_token_minutes": 15,
            "active_video_jobs_protected_from_bulk_delete": True,
            "bulk_delete_emits_start_progress_completion": True,
            "cross_platform_identity_fields": [
                "platform", "source_kind", "source_id", "source_key"
            ],
            "legacy_bvid_identity_preserved": True,
        },
        "storage": {
            "temporary_media_removed_on_success_failure_cancel": True,
            "export_expiry_hours": 24,
            "minimum_free_disk_gb": 5,
            "zip_single_note_root": True,
            "zip_contains_unrelated_vault_notes": False,
            "operational_state_outside_vault": True,
            "visual_asset_filename": "YYYYMMDD-N-sequence-timestamp.png",
            "visual_asset_names_ascii_only": True,
            "missing_candidate_frame_fails_note": False,
        },
        "secrets": {
            "runtime_env_allowlisted": True,
            "secret_values_printed_or_searched": False,
            "bilibili_cookie_forwarded_to_subtitle_or_media_cdn": False,
            "chat_secret_delivery_allowed": False,
            "interactive_tty_secret_entry": True,
            "dedicated_secret_file_permissions": "0600",
            "configuration_status_contains_secret_values": False,
        },
        "configuration": {
            "deterministic_platform_menu": True,
            "bare_number_configures_platform": False,
            "platform_reply_requires_configure_verb": True,
            "public_mode_preferred": True,
            "adapter_reports_access_limitations": True,
        },
    }


def main() -> int:
    install_cancel_handlers()
    load_runtime_env()
    args = parser().parse_args()
    active_key: str | None = None
    active_id: int | None = None
    active_vault: Path | None = None
    active_label: str | None = None
    gateway_reporter: GatewayProgressReporter | None = None
    try:
        cleanup_storage()
        if args.command == "doctor":
            result = doctor()
        elif args.command == "contract":
            result = contract()
        elif args.command == "configure":
            if args.action in {"menu", "status"}:
                result = configuration_menu()
                if args.action == "status":
                    result["secrets"] = secret_specs_public()
            elif args.action == "platform":
                if not args.target:
                    raise ValueError("configure platform requires a platform number or name")
                result = platform_configuration(args.target)
            elif args.action == "secret":
                if not args.target:
                    raise ValueError("configure secret requires a configuration item")
                if not sys.stdin.isatty():
                    raise RuntimeError(
                        "秘密配置必须在 SSH 交互终端中录入；不要通过聊天、命令参数或管道传递"
                    )
                result = set_secret_interactive(args.target)
            else:
                if not args.target:
                    raise ValueError("configure remove requires a configuration item")
                if not args.confirm:
                    raise ValueError("移除秘密配置需要显式提供 --confirm")
                result = remove_secret(args.target)
        elif args.command == "evaluate":
            result = evaluate_text_core(
                args.source_dir,
                args.output_dir,
                llm_model=args.llm_model,
                reset_checkpoint=args.reset_checkpoint,
            )
        elif args.command == "submit":
            result = submit_detached(sys.argv[1:])
        elif args.command == "prepare-asr":
            print("正在从 ModelScope 准备 Paraformer、VAD 和标点模型。", file=sys.stderr, flush=True)
            result = prepare_funasr()
        elif args.command == "bundle":
            result = bundle_job(args.vault, args.bvid, task_id=args.task, include_source=args.include_source)
            if args.send_target:
                send_hermes_document(args.send_target, Path(str(result["bundle"])))
                result["sent"] = True
                result["send_target"] = args.send_target
        elif args.command == "tasks":
            result = {"scope": "all" if args.all else "today", "tasks": list_tasks(args.vault, all_tasks=args.all, include_deleted=args.include_deleted)}
        elif args.command == "delete":
            result = delete_job(args.vault, args.task)
        elif args.command == "delete-many":
            if args.confirm_token:
                result = confirm_bulk_delete(
                    args.vault,
                    args.confirm_token,
                    send_target=args.send_target,
                )
            else:
                result = plan_bulk_delete(
                    args.vault,
                    keep=args.keep,
                    all_history=args.all_history,
                )
        elif args.command == "restore":
            result = restore_job(args.vault, args.task)
        elif args.command == "cancel":
            result = cancel_job(args.vault, args.task, latest_running=args.latest_running)
        elif args.command == "cleanup":
            result = {"status": "complete", "removed": cleanup_storage()}
        elif args.command == "browser-token":
            token_path = browser_token_path()
            if args.rotate:
                token_path.unlink(missing_ok=True)
            token, token_path = load_or_create_browser_token(token_path)
            result = {"token": token, "path": str(token_path), "permissions": "0600"}
        elif args.command == "serve":
            serve_local(vault=args.vault, port=args.port)
            return 0
        elif args.command == "inspect":
            adapter = adapter_for(args.url)
            canonical = adapter.canonicalize_input(args.url)
            adapter = adapter_for(canonical)
            info = adapter.inspect(canonical, args.part)
            if isinstance(adapter, DocumentSourceAdapter):
                segments, transcript_meta = adapter.content_segments(info)
            else:
                segments, transcript_meta = adapter.primary_transcript(info)
            inspected_metadata = adapter.metadata(info)
            if isinstance(adapter, DocumentSourceAdapter):
                inspected_metadata.pop("segments", None)
                inspected_metadata.pop("images", None)
                inspected_metadata["image_count"] = int(transcript_meta.get("image_count") or 0)
            result = {
                "metadata": inspected_metadata,
                "source_segments": len(segments),
                "transcript": transcript_meta,
            }
        else:
            duplicates = duplicate_skill_paths()
            if len(duplicates) > 1:
                raise RuntimeError(
                    "检测到多个同名 manuloom Skill，请将备份移出 ~/.hermes/skills："
                    + "；".join(duplicates)
                )
            if not args.url and not args.resume:
                raise ValueError("--url is required unless --resume is supplied")
            if not 0 <= args.max_frames <= 120:
                raise ValueError("--max-frames must be between 0 and 120")
            if args.part is not None and args.part < 1:
                raise ValueError("--part must be 1 or greater")
            if not 240 <= args.visual_height <= 1080:
                raise ValueError("--visual-height must be between 240 and 1080")
            if not args.visual_height <= args.final_visual_height <= 2160:
                raise ValueError(
                    "--final-visual-height must be between the analysis height and 2160"
                )
            active_vault = args.vault
            hinted_bvid = None
            hinted_source_id = None
            source_key = None
            platform = "bilibili"
            source_kind = "video"
            requested_part = args.part or 1
            if args.url:
                adapter = adapter_for(args.url)
                args.url = adapter.canonicalize_input(args.url)
                adapter = adapter_for(args.url)
                platform = adapter.platform
                source_kind = adapter.source_kind
                hinted_source_id = adapter.source_id_from_url(args.url)
                selector = adapter.selector_from_url(args.url, args.part)
                requested_part = int(selector or 1)
                hinted_bvid = hinted_source_id if platform == "bilibili" else None
                source_key = f"{platform}:{hinted_source_id}"
                if selector is not None:
                    source_key += f":p{selector}"
            if source_key and not args.force:
                found = find_existing_source_task(args.vault, source_key)
                if found:
                    failure = f" 上次失败原因：{found.get('error')}" if found.get("status") == "failed" else ""
                    raise RuntimeError(
                        f"该来源已存在：{found['task_key']}（{found['title']}，状态 {found['status']}）。"
                        f"{failure} 不要自动重试；如需重新生成，必须由用户明确提出，再使用 --force。"
                    )
            task = reserve_task(
                args.vault,
                url=args.url or "",
                bvid=hinted_bvid,
                part=requested_part,
                platform=platform,
                source_kind=source_kind,
                source_id=hinted_source_id,
                source_key=source_key,
                status="queued",
            )
            active_key = str(task["task_key"])
            active_id = int(task["id"])
            label = progress_label(int(task["id"]), active_key)
            active_label = label
            gateway_reporter = (
                GatewayProgressReporter(label, target=args.progress_target)
                if args.gateway_output
                else None
            )
            output_progress = (
                gateway_reporter.progress
                if gateway_reporter
                else lambda message: print(format_progress(label, message), file=sys.stderr, flush=True)
            )
            update_task(args.vault, active_key, pid=os.getpid(), status="queued", stage=0)

            def progress_callback(message: str) -> None:
                stage = _PROGRESS_STAGES.get(message)
                if stage:
                    update_task(
                        args.vault,
                        active_key or "",
                        status="running",
                        pid=os.getpid(),
                        stage=stage,
                        stage_message=message,
                    )
                output_progress(message)

            with video_job_slot(args.vault, active_key, gateway_reporter):
                result = run(Options(
                    url=args.url or "", vault=args.vault,
                    part=requested_part if platform == "bilibili" else None,
                    cookies_file=args.cookies_file,
                    no_visual=args.no_visual or args.max_frames == 0, max_frames=args.max_frames,
                    keep_video=args.keep_video, llm_model=args.llm_model, asr_model=args.asr_model,
                    asr_backend=args.asr_backend, visual_height=args.visual_height,
                    final_visual_height=args.final_visual_height, resume=args.resume,
                    task_key=active_key, task_date=str(task["task_date"]), daily_no=int(task["id"]),
                    progress=None if args.no_progress else progress_callback,
                ))
            result.update(id=task["id"], task_key=active_key)
            update_task(
                args.vault, active_key,
                bvid=result.get("bvid") or hinted_bvid,
                platform=result.get("platform") or platform,
                source_kind=result.get("source_kind") or source_kind,
                source_id=result.get("source_id") or hinted_source_id,
                source_key=result.get("source_key") or source_key,
                part=result.get("part") or args.part or 1,
                url=result.get("url") or args.url or "",
                title=result.get("title") or Path(str(result["note"])).stem,
                status=result["status"], note=result["note"], job_dir=result["job_dir"], error="",
                pid=None, stage=6, stage_message="处理完成。",
            )
        if args.command == "run" and args.gateway_output:
            if gateway_reporter:
                gateway_reporter.completion(result)
            else:
                print(format_gateway_completion(active_label or "视频任务", result), file=sys.stderr, flush=True)
        else:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except KeyboardInterrupt:
        if active_key and active_vault:
            try:
                sync_task_from_audit(
                    active_vault,
                    active_key,
                    status="cancelled",
                    error="用户已终止任务",
                )
                update_task(
                    active_vault,
                    active_key,
                    pid=None,
                    stage=6,
                    stage_message="已取消",
                )
            except Exception:
                pass
        if args.command == "run" and args.gateway_output:
            reporter = gateway_reporter or GatewayProgressReporter(
                active_label or "视频任务",
                target=str(getattr(args, "progress_target", "") or ""),
            )
            reporter.terminal("CANCELLED", "已取消。任务已结束，临时音视频已清理。")
        else:
            print(json.dumps({"status": "cancelled", "error": "用户已终止任务"}, ensure_ascii=False), file=sys.stderr)
        return 130
    except Exception as exc:
        if active_key and active_vault:
            try:
                sync_task_from_audit(active_vault, active_key, status="failed", error=str(exc))
                update_task(
                    active_vault,
                    active_key,
                    pid=None,
                    stage=6,
                    stage_message="处理失败",
                )
            except Exception:
                pass
        if args.command == "run" and args.gateway_output:
            reporter = gateway_reporter or GatewayProgressReporter(
                active_label or "视频任务",
                target=str(getattr(args, "progress_target", "") or ""),
            )
            retry = f"修复后可说“重新生成任务 {active_id}”。" if active_id else ""
            reporter.terminal(
                "FAILED",
                f"处理失败：{exc}。任务已结束，未生成笔记；临时音视频已清理。{retry}",
            )
        else:
            print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
