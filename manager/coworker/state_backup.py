"""Portable Manager state backup and restore.

The backup format is intentionally conservative: it preserves user-owned state that can be
inspected safely on another install, while excluding credentials and machine/runtime handles.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
import time
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from .delegation import BINDING_KEY as HAAS_DELEGATION_BINDING_KEY
from .secrets import write_private_text

FORMAT = "openharness-state-backup"
VERSION = 1

SQLITE_FILES = {
    "coworker.db",
    "automation.db",
    "journal.db",
    "teams.db",
    "chat.db",
}

JSON_FILES = {
    "prefs.json",
    "memory-settings.json",
    "personas.json",
    "persona_connections.json",
    "session_connections.json",
    "session_skills.json",
    "inbox_routing.json",
}

SECRET_FILES = {
    ".env",
    "secrets.json",
    "board-tokens.json",
    "haas-token",
}

RUNTIME_FILES = {
    "haas-supervised.yaml",
}

MAX_ARCHIVE_ENTRIES = 20_000
MAX_ARCHIVE_BYTES = 2 * 1024 * 1024 * 1024


class BackupError(RuntimeError):
    pass


def create_backup(source_dir: str | Path, archive_path: str | Path) -> dict[str, Any]:
    source = Path(source_dir).expanduser().resolve()
    archive = Path(archive_path).expanduser().resolve()
    if not source.is_dir():
        raise BackupError(f"state directory does not exist: {source}")
    if _is_relative_to(archive, source):
        raise BackupError("backup archive must be outside the source state directory")
    archive.parent.mkdir(parents=True, exist_ok=True)

    included: list[dict[str, Any]] = []
    excluded: dict[str, int] = {}
    total_bytes = 0

    fd, temp_name = tempfile.mkstemp(
        prefix=f".{archive.name}.", suffix=".tmp", dir=str(archive.parent)
    )
    os.close(fd)
    temp = Path(temp_name)
    try:
        with zipfile.ZipFile(temp, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for rel, src in _iter_portable_entries(source, excluded):
                data_path = src
                cleanup: Path | None = None
                try:
                    if rel in SQLITE_FILES and _is_sqlite(src):
                        cleanup = _copy_sqlite_snapshot(src)
                        data_path = cleanup
                    zf.write(data_path, rel)
                    size = src.stat().st_size
                    total_bytes += size
                    included.append({"path": rel, "bytes": size})
                finally:
                    if cleanup is not None:
                        cleanup.unlink(missing_ok=True)

            manifest = {
                "format": FORMAT,
                "version": VERSION,
                "createdAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "source": {"stateDirName": source.name},
                "included": included,
                "excluded": excluded,
                "warnings": [
                    "Credentials, tokens, logs and live runtime state are excluded.",
                    "Restored sessions require fresh approval grants.",
                    "Restored automations are disabled until the user re-enables them.",
                ],
                "totalBytes": total_bytes,
            }
            zf.writestr("manifest.json", json.dumps(manifest, indent=2, sort_keys=True))
        os.replace(temp, archive)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise
    return {
        "ok": True,
        "archive": str(archive),
        "included": included,
        "excluded": excluded,
        "totalBytes": total_bytes,
    }


def inspect_backup(archive_path: str | Path) -> dict[str, Any]:
    archive = Path(archive_path).expanduser()
    with zipfile.ZipFile(archive) as zf:
        manifest = _read_manifest(zf)
        infos = _validate_archive_entries(zf)
    return {
        "ok": True,
        "archive": str(archive),
        "format": manifest["format"],
        "version": manifest["version"],
        "entryCount": len(infos),
        "totalBytes": sum(info.file_size for info in infos),
        "included": manifest.get("included", []),
        "excluded": manifest.get("excluded", {}),
        "warnings": manifest.get("warnings", []),
    }


def restore_backup(
    archive_path: str | Path,
    target_dir: str | Path,
    *,
    force: bool = False,
) -> dict[str, Any]:
    archive = Path(archive_path).expanduser().resolve()
    target = Path(target_dir).expanduser().resolve()
    if target.exists() and any(target.iterdir()) and not force:
        raise BackupError("target state directory is not empty; use force to replace it")
    target.parent.mkdir(parents=True, exist_ok=True)

    stage = Path(
        tempfile.mkdtemp(prefix=f".{target.name}.restore-", dir=str(target.parent))
    ).resolve()
    backup_old: Path | None = None
    try:
        with zipfile.ZipFile(archive) as zf:
            manifest = _read_manifest(zf)
            infos = _validate_archive_entries(zf)
            for info in infos:
                if info.filename == "manifest.json":
                    continue
                rel = PurePosixPath(info.filename)
                dest = (stage / Path(*rel.parts)).resolve()
                if not _is_relative_to(dest, stage):
                    raise BackupError(f"unsafe archive entry: {info.filename}")
                dest.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, open(dest, "wb") as out:
                    shutil.copyfileobj(src, out)
        _post_restore_safety(stage)
        if target.exists():
            if force:
                backup_old = target.with_name(f".{target.name}.replace-{int(time.time())}")
                os.replace(target, backup_old)
            elif any(target.iterdir()):
                raise BackupError("target state directory is not empty; use force to replace it")
            else:
                target.rmdir()
        os.replace(stage, target)
        if backup_old is not None:
            shutil.rmtree(backup_old, ignore_errors=True)
        return {
            "ok": True,
            "target": str(target),
            "format": manifest["format"],
            "version": manifest["version"],
            "entryCount": len(infos),
        }
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        if backup_old is not None and not target.exists() and backup_old.exists():
            os.replace(backup_old, target)
        raise


def _iter_portable_entries(source: Path, excluded: dict[str, int]):
    for rel in sorted(SQLITE_FILES | JSON_FILES):
        path = source / rel
        if path.exists() and _portable_regular_file(path, excluded):
            yield rel, path
    conv_dir = source / "conversations"
    if conv_dir.is_dir():
        for path in sorted(conv_dir.glob("*.jsonl")):
            if not _portable_regular_file(path, excluded):
                continue
            if path.name.startswith(".") or "/" in path.name or "\\" in path.name:
                _excluded(excluded, "unsafe_path")
                continue
            yield f"conversations/{path.name}", path
    attachments = source / "attachments"
    if attachments.is_dir():
        for path in sorted(attachments.rglob("*")):
            if not _portable_regular_file(path, excluded, count_excluded=False):
                continue
            rel = path.relative_to(source).as_posix()
            if not _safe_archive_name(rel):
                _excluded(excluded, "unsafe_path")
                continue
            yield rel, path
    _scan_exclusions(source, excluded)


def _portable_regular_file(
    path: Path, excluded: dict[str, int], *, count_excluded: bool = True
) -> bool:
    if path.is_symlink():
        if count_excluded:
            _excluded(excluded, "symlink")
        return False
    if not path.is_file():
        if count_excluded:
            _excluded(excluded, "non_regular")
        return False
    return True


def _scan_exclusions(source: Path, excluded: dict[str, int]) -> None:
    for path in source.rglob("*"):
        if path == source:
            continue
        rel = path.relative_to(source).as_posix()
        name = path.name
        category = _excluded_category(rel, name)
        if category:
            _excluded(excluded, category)
        elif path.is_symlink():
            _excluded(excluded, "symlink")
        elif path.is_dir():
            continue
        elif not _is_whitelisted(rel):
            _excluded(excluded, "not_portable")


def _excluded_category(rel: str, name: str) -> str | None:
    if rel in SECRET_FILES or (name.startswith("sidecar-") and name.endswith(".token")):
        return "secret"
    if rel in RUNTIME_FILES or rel == "haas.db" or rel.startswith("haas.db-"):
        return "runtime"
    if rel.startswith("logs/"):
        return "logs"
    if "__pycache__" in rel.split("/") or name.endswith(".pyc"):
        return "cache"
    if name.endswith(".tmp") or name.startswith(".tmp"):
        return "temporary"
    return None


def _is_whitelisted(rel: str) -> bool:
    if rel in SQLITE_FILES or rel in JSON_FILES:
        return True
    path = PurePosixPath(rel)
    if (
        len(path.parts) == 2
        and path.parts[0] == "conversations"
        and path.parts[1].endswith(".jsonl")
    ):
        return True
    return rel.startswith("attachments/")


def _excluded(excluded: dict[str, int], category: str) -> None:
    excluded[category] = excluded.get(category, 0) + 1


def _is_sqlite(path: Path) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read(16) == b"SQLite format 3\x00"
    except OSError:
        return False


def _copy_sqlite_snapshot(path: Path) -> Path:
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.snapshot-", suffix=".db")
    os.close(fd)
    temp = Path(temp_name)
    try:
        source = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            dest = sqlite3.connect(temp)
            try:
                source.backup(dest)
            finally:
                dest.close()
        finally:
            source.close()
    except BaseException:
        temp.unlink(missing_ok=True)
        raise
    return temp


def _read_manifest(zf: zipfile.ZipFile) -> dict[str, Any]:
    try:
        raw = zf.read("manifest.json")
    except KeyError as exc:
        raise BackupError("backup manifest is missing") from exc
    try:
        manifest = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise BackupError("backup manifest is invalid JSON") from exc
    if manifest.get("format") != FORMAT:
        raise BackupError("unsupported backup format")
    if manifest.get("version") != VERSION:
        raise BackupError("unsupported backup version")
    return manifest


def _validate_archive_entries(zf: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    infos = zf.infolist()
    if len(infos) > MAX_ARCHIVE_ENTRIES:
        raise BackupError("backup archive has too many entries")
    total = 0
    seen: set[str] = set()
    for info in infos:
        if info.is_dir():
            continue
        if info.filename in seen:
            raise BackupError(f"duplicate archive entry: {info.filename}")
        seen.add(info.filename)
        if not _safe_archive_name(info.filename):
            raise BackupError(f"unsafe archive entry: {info.filename}")
        mode = (info.external_attr >> 16) & 0o170000
        if mode == 0o120000:
            raise BackupError(f"symlink archive entry is not allowed: {info.filename}")
        if not _is_whitelisted(info.filename) and info.filename != "manifest.json":
            raise BackupError(f"unsupported archive entry: {info.filename}")
        total += info.file_size
        if total > MAX_ARCHIVE_BYTES:
            raise BackupError("backup archive is too large")
    return infos


def _safe_archive_name(name: str) -> bool:
    if not name or name.startswith("/") or name.startswith("\\"):
        return False
    path = PurePosixPath(name)
    return all(part not in {"", ".", ".."} for part in path.parts)


def _post_restore_safety(base: Path) -> None:
    _clear_session_grants(base / "coworker.db")
    _disable_automations(base / "automation.db")
    for rel in SECRET_FILES | RUNTIME_FILES:
        (base / rel).unlink(missing_ok=True)
    for path in base.glob("sidecar-*.token"):
        path.unlink(missing_ok=True)
    write_private_text(base / ".restore-notice.json", json.dumps(_restore_notice(), indent=2))


def _clear_session_grants(db_path: Path) -> None:
    if not db_path.is_file() or not _is_sqlite(db_path):
        return
    with sqlite3.connect(db_path) as conn:
        cols = _columns(conn, "sessions")
        if "grants" in cols:
            conn.execute("UPDATE sessions SET grants='{}'")
        if "bindings" in cols:
            rows = conn.execute("SELECT session_id, bindings FROM sessions").fetchall()
            for session_id, raw in rows:
                try:
                    bindings = json.loads(raw or "{}")
                except json.JSONDecodeError:
                    bindings = {}
                if isinstance(bindings, dict) and HAAS_DELEGATION_BINDING_KEY in bindings:
                    bindings.pop(HAAS_DELEGATION_BINDING_KEY, None)
                    conn.execute(
                        "UPDATE sessions SET bindings=? WHERE session_id=?",
                        (json.dumps(bindings), session_id),
                    )


def _disable_automations(db_path: Path) -> None:
    if not db_path.is_file() or not _is_sqlite(db_path):
        return
    now = time.time()
    with sqlite3.connect(db_path) as conn:
        if "scheduled_tasks" in _tables(conn):
            rows = conn.execute("SELECT id, data FROM scheduled_tasks").fetchall()
            for task_id, raw in rows:
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                data["enabled"] = False
                data["next_run"] = None
                data["last_status"] = "error"
                data["updated_at"] = now
                conn.execute(
                    "UPDATE scheduled_tasks SET enabled=0, next_run=NULL, data=? WHERE id=?",
                    (json.dumps(data), task_id),
                )
        if "task_runs" in _tables(conn):
            rows = conn.execute("SELECT run_id, data FROM task_runs").fetchall()
            for run_id, raw in rows:
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if data.get("status") == "running":
                    data["status"] = "error"
                    data["finished_at"] = data.get("finished_at") or now
                    data["error"] = "restore_required: imported running run cannot be resumed"
                    conn.execute(
                        "UPDATE task_runs SET data=? WHERE run_id=?",
                        (json.dumps(data), run_id),
                    )


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    if table not in _tables(conn):
        return set()
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _restore_notice() -> dict[str, Any]:
    return {
        "format": "openharness-restore-notice",
        "version": 1,
        "restoredAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "grantsCleared": True,
        "automationsDisabled": True,
    }


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False
