from __future__ import annotations

import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


def state_root() -> Path:
    return Path(
        os.getenv(
            "VTM_STATE_DIR",
            str(Path.home() / ".local" / "share" / "video-manuscript"),
        )
    ).expanduser().resolve()


def local_timezone() -> ZoneInfo:
    return ZoneInfo(os.getenv("VTM_TIMEZONE", "Asia/Shanghai"))


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _local_date(value: datetime | None = None) -> str:
    return (value or _now()).astimezone(local_timezone()).date().isoformat()


def _database() -> Path:
    path = state_root() / "tasks.sqlite3"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _connect() -> sqlite3.Connection:
    connection = sqlite3.connect(_database(), timeout=30, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS tasks (
            row_id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_date TEXT NOT NULL,
            daily_no INTEGER NOT NULL,
            task_key TEXT NOT NULL UNIQUE,
            bvid TEXT,
            platform TEXT NOT NULL DEFAULT 'bilibili',
            source_kind TEXT NOT NULL DEFAULT 'video',
            source_id TEXT,
            source_key TEXT,
            part INTEGER NOT NULL DEFAULT 1,
            url TEXT NOT NULL DEFAULT '',
            title TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            job_dir TEXT,
            note TEXT,
            bundle TEXT,
            error TEXT,
            deleted_at TEXT,
            trash_dir TEXT,
            UNIQUE(task_date, daily_no)
        )
        """
    )
    existing_columns = {
        str(row["name"]) for row in connection.execute("PRAGMA table_info(tasks)").fetchall()
    }
    for name, definition in {
        "pid": "INTEGER",
        "stage": "INTEGER",
        "stage_message": "TEXT",
        "platform": "TEXT NOT NULL DEFAULT 'bilibili'",
        "source_kind": "TEXT NOT NULL DEFAULT 'video'",
        "source_id": "TEXT",
        "source_key": "TEXT",
    }.items():
        if name not in existing_columns:
            connection.execute(f"ALTER TABLE tasks ADD COLUMN {name} {definition}")
    connection.execute(
        "CREATE INDEX IF NOT EXISTS tasks_source_key_idx ON tasks(source_key)"
    )
    connection.execute(
        """
        UPDATE tasks
        SET source_id = COALESCE(source_id, bvid),
            source_key = COALESCE(source_key, 'bilibili:' || bvid || ':p' || part)
        WHERE bvid IS NOT NULL AND bvid != ''
        """
    )
    return connection


def _row(row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    result["id"] = int(result["daily_no"])
    result["history_id"] = result["task_key"]
    return result


def _parse_created(value: str | None, fallback: float | None = None) -> datetime:
    if value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return datetime.fromtimestamp(fallback or _now().timestamp(), tz=timezone.utc)


def _insert_imported(connection: sqlite3.Connection, item: dict[str, Any]) -> None:
    created = _parse_created(str(item.get("created_at") or ""))
    day = created.astimezone(local_timezone()).date().isoformat()
    next_no = int(
        connection.execute(
            "SELECT COALESCE(MAX(daily_no), 0) + 1 FROM tasks WHERE task_date = ?", (day,)
        ).fetchone()[0]
    )
    compact = day.replace("-", "")
    connection.execute(
        """
        INSERT OR IGNORE INTO tasks
        (task_date, daily_no, task_key, bvid, platform, source_kind, source_id, source_key,
         url, title, status, created_at, updated_at,
         job_dir, note, bundle, error)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            day,
            next_no,
            f"{compact}-{next_no}",
            item.get("bvid"),
            item.get("platform") or "bilibili",
            item.get("source_kind") or "video",
            item.get("source_id") or item.get("bvid"),
            item.get("source_key") or (
                f"bilibili:{item.get('bvid')}:p{int(item.get('part') or 1)}"
                if item.get("bvid") else None
            ),
            item.get("url") or "",
            item.get("title") or item.get("bvid") or "历史视频笔记",
            item.get("status") or "complete",
            created.astimezone(timezone.utc).isoformat(),
            item.get("updated_at") or created.astimezone(timezone.utc).isoformat(),
            item.get("job_dir"),
            item.get("note"),
            item.get("bundle"),
            item.get("error"),
        ),
    )


def _migrate_legacy(connection: sqlite3.Connection, vault: Path) -> None:
    if connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]:
        return
    legacy = vault.expanduser().resolve() / ".video-manuscript-tasks.json"
    if legacy.is_file():
        try:
            payload = json.loads(legacy.read_text(encoding="utf-8"))
            for item in payload.get("tasks") or []:
                _insert_imported(connection, item)
        except (OSError, json.JSONDecodeError, TypeError):
            pass
    roots = [vault.expanduser().resolve() / "Video Notes", vault.expanduser().resolve() / "Sources" / "Videos"]
    known = {
        str(Path(value).expanduser().resolve())
        for (value,) in connection.execute("SELECT job_dir FROM tasks WHERE job_dir IS NOT NULL")
    }
    for root in roots:
        if not root.is_dir():
            continue
        for note in sorted(root.rglob("*.md"), key=lambda path: path.stat().st_mtime):
            job_dir = note.parent.resolve()
            if str(job_dir) in known or job_dir.name in {"Indexes", "Daily", "Templates"}:
                continue
            match = re.search(r"(BV[0-9A-Za-z]{10})", job_dir.name, flags=re.I)
            if not match:
                continue
            created = datetime.fromtimestamp(note.stat().st_mtime, tz=timezone.utc)
            _insert_imported(
                connection,
                {
                    "bvid": match.group(1),
                    "title": note.stem,
                    "status": "complete",
                    "created_at": created.isoformat(),
                    "job_dir": str(job_dir),
                    "note": str(note),
                },
            )
            known.add(str(job_dir))


def reserve_task(
    vault: Path,
    *,
    url: str,
    bvid: str | None = None,
    part: int = 1,
    platform: str = "bilibili",
    source_kind: str = "video",
    source_id: str | None = None,
    source_key: str | None = None,
    status: str = "running",
) -> dict[str, Any]:
    connection = _connect()
    try:
        _migrate_legacy(connection, vault)
        connection.execute("BEGIN IMMEDIATE")
        day = _local_date()
        daily_no = int(
            connection.execute(
                "SELECT COALESCE(MAX(daily_no), 0) + 1 FROM tasks WHERE task_date = ?", (day,)
            ).fetchone()[0]
        )
        now = _now().isoformat()
        task_key = f"{day.replace('-', '')}-{daily_no}"
        connection.execute(
            """
            INSERT INTO tasks
            (task_date, daily_no, task_key, bvid, platform, source_kind, source_id, source_key,
             part, url, title, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                day,
                daily_no,
                task_key,
                bvid,
                platform,
                source_kind,
                source_id or bvid,
                source_key or (f"bilibili:{bvid}:p{part}" if bvid else None),
                part,
                url,
                source_id or bvid or "正在读取来源信息",
                status,
                now,
                now,
            ),
        )
        connection.commit()
        return get_task(vault, task_key)
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def get_task(vault: Path, identity: str | int, *, include_deleted: bool = False) -> dict[str, Any]:
    connection = _connect()
    try:
        _migrate_legacy(connection, vault)
        text = str(identity).strip()
        if re.fullmatch(r"\d{8}-\d+", text):
            row = connection.execute("SELECT * FROM tasks WHERE task_key = ?", (text,)).fetchone()
        elif text.isdigit():
            row = connection.execute(
                "SELECT * FROM tasks WHERE task_date = ? AND daily_no = ?",
                (_local_date(), int(text)),
            ).fetchone()
        else:
            raise KeyError(f"无法识别任务编号：{identity}")
        if row is None or (row["deleted_at"] and not include_deleted):
            raise KeyError(f"找不到任务 {identity}；普通数字只代表今天的任务")
        return _row(row)
    finally:
        connection.close()


def update_task(vault: Path, identity: str | int, **updates: Any) -> dict[str, Any]:
    current = get_task(vault, identity, include_deleted=True)
    allowed = {
        "bvid", "platform", "source_kind", "source_id", "source_key", "part", "url",
        "title", "status", "job_dir", "note", "bundle",
        "error", "deleted_at", "trash_dir", "pid", "stage", "stage_message",
    }
    values = {key: value for key, value in updates.items() if key in allowed}
    values["updated_at"] = _now().isoformat()
    assignments = ", ".join(f"{key} = ?" for key in values)
    connection = _connect()
    try:
        connection.execute(
            f"UPDATE tasks SET {assignments} WHERE row_id = ?",
            (*values.values(), current["row_id"]),
        )
    finally:
        connection.close()
    return get_task(vault, current["task_key"], include_deleted=True)


def list_tasks(
    vault: Path,
    *,
    all_tasks: bool = False,
    include_deleted: bool = False,
    day: str | None = None,
) -> list[dict[str, Any]]:
    connection = _connect()
    try:
        _migrate_legacy(connection, vault)
        where: list[str] = []
        params: list[Any] = []
        if not all_tasks:
            where.append("task_date = ?")
            params.append(day or _local_date())
        if not include_deleted:
            where.append("deleted_at IS NULL")
        clause = " WHERE " + " AND ".join(where) if where else ""
        rows = connection.execute(
            "SELECT * FROM tasks" + clause + " ORDER BY task_date DESC, daily_no ASC", params
        ).fetchall()
        return [_row(row) for row in rows]
    finally:
        connection.close()


def mark_deleted(vault: Path, identity: str | int, trash_dir: Path) -> dict[str, Any]:
    return update_task(
        vault,
        identity,
        status="deleted",
        deleted_at=_now().isoformat(),
        trash_dir=str(trash_dir),
        bundle="",
    )


def mark_restored(vault: Path, identity: str | int, job_dir: Path, note: Path) -> dict[str, Any]:
    return update_task(
        vault,
        identity,
        status="complete",
        deleted_at="",
        trash_dir="",
        job_dir=str(job_dir),
        note=str(note),
    )
