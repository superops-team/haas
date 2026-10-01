"""Durable command receipts and follow-up queue for the Manager conversation surface."""

from __future__ import annotations

import json
import os
import sqlite3
import stat
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

CommandStatus = Literal["accepted", "duplicate", "rejected"]
CommandDisposition = Literal["running", "queued", "terminal"]
DeliveryIntent = Literal["start_now", "enqueue", "interrupt_then_start"]

_SQLITE_SIDECAR_SUFFIXES = ("-wal", "-shm", "-journal")


def _make_private_directory(path: Path) -> None:
    """Create only this store directory privately; preserve an existing parent mode."""

    existed = path.exists()
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name == "posix" and not existed:
        os.chmod(path, 0o700)


def _secure_owned_regular_file(path: Path, *, create: bool) -> None:
    """Validate and chmod an owned SQLite file without following links."""

    if os.name != "posix":
        return
    flags = os.O_RDWR | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    if create:
        try:
            descriptor = os.open(path, flags | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            descriptor = os.open(path, flags)
    else:
        try:
            descriptor = os.open(path, flags)
        except FileNotFoundError:
            return
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise OSError(f"conversation command store file is not regular: {path.name}")
        if metadata.st_uid != os.getuid():
            raise PermissionError(
                f"conversation command store file is not owned by this user: {path.name}"
            )
        if metadata.st_nlink != 1:
            raise OSError(f"conversation command store file has multiple links: {path.name}")
        os.fchmod(descriptor, 0o600)
    finally:
        os.close(descriptor)


def _prepare_private_sqlite_store(path: Path) -> None:
    _make_private_directory(path.parent)
    if os.name != "posix":
        return
    _secure_owned_regular_file(path, create=True)
    for suffix in _SQLITE_SIDECAR_SUFFIXES:
        _secure_owned_regular_file(Path(f"{path}{suffix}"), create=False)


def _verify_private_sqlite_sidecars(path: Path) -> None:
    if os.name != "posix":
        return
    for suffix in _SQLITE_SIDECAR_SUFFIXES:
        _secure_owned_regular_file(Path(f"{path}{suffix}"), create=False)


class ConversationCommandConflict(ValueError):
    """The same client command identity was reused with different intent."""


@dataclass(frozen=True)
class CommandReceipt:
    client_command_id: str
    status: CommandStatus
    disposition: CommandDisposition
    turn_id: str | None
    queue_item_id: str | None
    outcome_ref: str | None = None
    execution_ref: str | None = None


@dataclass(frozen=True)
class RunningCommand:
    """A checkpointed command that still claims an active execution."""

    session_id: str
    client_command_id: str
    turn_id: str
    execution_ref: str | None


class ConversationCommandStore:
    """SQLite authority for accepted GUI commands and their queued payloads."""

    def __init__(self, db_path: str | Path) -> None:
        self.path = Path(db_path).expanduser()
        _prepare_private_sqlite_store(self.path)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        try:
            self._conn.execute("PRAGMA journal_mode=WAL")
            # Newly created sidecars inherit the private database mode. Revalidate them
            # before any command payload or schema content is written.
            _verify_private_sqlite_sidecars(self.path)
        except Exception:
            self._conn.close()
            raise
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS conversation_commands (
                session_id TEXT NOT NULL,
                client_command_id TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                status TEXT NOT NULL,
                disposition TEXT NOT NULL,
                turn_id TEXT,
                queue_item_id TEXT,
                outcome_ref TEXT,
                execution_ref TEXT,
                payload_json TEXT NOT NULL,
                checkpointed_at_ms INTEGER,
                created_at_ms INTEGER NOT NULL,
                updated_at_ms INTEGER NOT NULL,
                PRIMARY KEY (session_id, client_command_id),
                UNIQUE (session_id, idempotency_key)
            );
            CREATE TABLE IF NOT EXISTS conversation_queue (
                session_id TEXT NOT NULL,
                queue_item_id TEXT NOT NULL,
                client_command_id TEXT NOT NULL,
                position INTEGER NOT NULL,
                state TEXT NOT NULL,
                delivery TEXT NOT NULL,
                revision INTEGER NOT NULL DEFAULT 1,
                created_at_ms INTEGER NOT NULL,
                PRIMARY KEY (session_id, queue_item_id),
                UNIQUE (session_id, client_command_id)
            );
            CREATE TABLE IF NOT EXISTS conversation_queue_mutations (
                session_id TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                kind TEXT NOT NULL,
                queue_item_id TEXT NOT NULL,
                result_json TEXT NOT NULL,
                created_at_ms INTEGER NOT NULL,
                PRIMARY KEY (session_id, idempotency_key)
            );
            CREATE TABLE IF NOT EXISTS conversation_queue_state (
                session_id TEXT PRIMARY KEY,
                paused INTEGER NOT NULL DEFAULT 0,
                reason TEXT,
                updated_at_ms INTEGER NOT NULL
            );
            """
        )
        try:
            self._conn.execute(
                "ALTER TABLE conversation_commands ADD COLUMN checkpointed_at_ms INTEGER"
            )
        except sqlite3.OperationalError as exc:
            if "duplicate column name" not in str(exc).lower():
                raise
        try:
            self._conn.execute(
                "ALTER TABLE conversation_commands ADD COLUMN execution_ref TEXT"
            )
        except sqlite3.OperationalError as exc:
            if "duplicate column name" not in str(exc).lower():
                raise
        # A process can stop after atomically claiming an item but before its task is
        # observably running. Never replay that uncertain work on startup. Put it back
        # at the head and require an explicit resume from the user.
        uncertain = self._conn.execute(
            """
            SELECT DISTINCT q.session_id
            FROM conversation_queue AS q
            JOIN conversation_commands AS c
              ON c.session_id = q.session_id
             AND c.client_command_id = q.client_command_id
            WHERE q.state = 'dispatching' AND c.checkpointed_at_ms IS NULL
            """
        ).fetchall()
        now_ms = int(time.time() * 1000)
        for row in uncertain:
            session_id = str(row["session_id"])
            self._conn.execute(
                """
                UPDATE conversation_queue
                SET state = 'queued', revision = revision + 1
                WHERE session_id = ? AND state = 'dispatching'
                  AND client_command_id IN (
                    SELECT client_command_id FROM conversation_commands
                    WHERE session_id = ? AND checkpointed_at_ms IS NULL
                  )
                """,
                (session_id, session_id),
            )
            self._conn.execute(
                """
                UPDATE conversation_commands
                SET disposition = 'queued', turn_id = NULL, updated_at_ms = ?
                WHERE session_id = ? AND queue_item_id IN (
                    SELECT queue_item_id FROM conversation_queue WHERE session_id = ?
                ) AND checkpointed_at_ms IS NULL
                """,
                (now_ms, session_id, session_id),
            )
            self._set_queue_pause(session_id, True, "restart_uncertain", now_ms=now_ms)
        self._conn.commit()

    @staticmethod
    def _receipt(row: sqlite3.Row, *, duplicate: bool = False) -> CommandReceipt:
        return CommandReceipt(
            client_command_id=str(row["client_command_id"]),
            status="duplicate" if duplicate else str(row["status"]),  # type: ignore[arg-type]
            disposition=str(row["disposition"]),  # type: ignore[arg-type]
            turn_id=row["turn_id"],
            queue_item_id=row["queue_item_id"],
            outcome_ref=row["outcome_ref"],
            execution_ref=row["execution_ref"],
        )

    def find_by_idempotency(self, session_id: str, idempotency_key: str) -> CommandReceipt | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT * FROM conversation_commands
                WHERE session_id = ? AND idempotency_key = ?
                """,
                (session_id, idempotency_key),
            ).fetchone()
        return self._receipt(row, duplicate=True) if row is not None else None

    def accept(
        self,
        *,
        session_id: str,
        client_command_id: str,
        idempotency_key: str,
        delivery: DeliveryIntent,
        payload: dict[str, Any],
        busy: bool,
    ) -> CommandReceipt:
        now_ms = int(time.time() * 1000)
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                existing = self._conn.execute(
                    """
                    SELECT * FROM conversation_commands
                    WHERE session_id = ? AND idempotency_key = ?
                    """,
                    (session_id, idempotency_key),
                ).fetchone()
                if existing is not None:
                    self._conn.commit()
                    return self._receipt(existing, duplicate=True)

                reused_id = self._conn.execute(
                    """
                    SELECT idempotency_key FROM conversation_commands
                    WHERE session_id = ? AND client_command_id = ?
                    """,
                    (session_id, client_command_id),
                ).fetchone()
                if reused_id is not None:
                    raise ConversationCommandConflict("client command id was reused")

                should_queue = busy and delivery == "enqueue"
                if busy and not should_queue:
                    raise ConversationCommandConflict("session is busy")
                disposition: CommandDisposition = "queued" if should_queue else "running"
                turn_id = None if should_queue else f"turn_{uuid.uuid4().hex}"
                queue_item_id = f"queue_{uuid.uuid4().hex}" if should_queue else None
                self._conn.execute(
                    """
                    INSERT INTO conversation_commands (
                        session_id, client_command_id, idempotency_key, status, disposition,
                        turn_id, queue_item_id, outcome_ref, payload_json,
                        created_at_ms, updated_at_ms
                    ) VALUES (?, ?, ?, 'accepted', ?, ?, ?, NULL, ?, ?, ?)
                    """,
                    (
                        session_id,
                        client_command_id,
                        idempotency_key,
                        disposition,
                        turn_id,
                        queue_item_id,
                        json.dumps(payload, separators=(",", ":")),
                        now_ms,
                        now_ms,
                    ),
                )
                if queue_item_id is not None:
                    next_position = self._conn.execute(
                        """
                        SELECT COALESCE(MAX(position), 0) + 1
                        FROM conversation_queue WHERE session_id = ?
                        """,
                        (session_id,),
                    ).fetchone()[0]
                    self._conn.execute(
                        """
                        INSERT INTO conversation_queue (
                            session_id, queue_item_id, client_command_id, position, state,
                            delivery, revision, created_at_ms
                        ) VALUES (?, ?, ?, ?, 'queued', ?, 1, ?)
                        """,
                        (
                            session_id,
                            queue_item_id,
                            client_command_id,
                            int(next_position),
                            delivery,
                            now_ms,
                        ),
                    )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        return CommandReceipt(
            client_command_id=client_command_id,
            status="accepted",
            disposition=disposition,
            turn_id=turn_id,
            queue_item_id=queue_item_id,
        )

    def payload(self, client_command_id: str, *, session_id: str) -> dict[str, Any]:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT payload_json FROM conversation_commands
                WHERE session_id = ? AND client_command_id = ?
                """,
                (session_id, client_command_id),
            ).fetchone()
        if row is None:
            raise KeyError(client_command_id)
        value = json.loads(row["payload_json"])
        if not isinstance(value, dict):
            raise ValueError("stored conversation command payload is not an object")
        return value

    def uncheckpointed_running(self) -> list[tuple[str, str]]:
        """List accepted immediate commands that have no durable turn checkpoint."""

        with self._lock:
            rows = self._conn.execute(
                """
                SELECT session_id, turn_id
                FROM conversation_commands
                WHERE disposition = 'running'
                  AND queue_item_id IS NULL
                  AND turn_id IS NOT NULL
                  AND checkpointed_at_ms IS NULL
                ORDER BY created_at_ms, session_id, client_command_id
                """
            ).fetchall()
        return [(str(row["session_id"]), str(row["turn_id"])) for row in rows]

    def mark_checkpointed(self, session_id: str, turn_id: str) -> bool:
        """Record that the matching turn_start is durably present in the transcript."""

        return self.checkpoint_execution(session_id, turn_id, execution_ref=None)

    def checkpoint_execution(
        self,
        session_id: str,
        turn_id: str,
        *,
        execution_ref: str | None,
        allow_rebind: bool = False,
    ) -> bool:
        """Checkpoint a turn and bind its latest accepted HaaS invocation."""

        with self._lock:
            row = self._conn.execute(
                """
                SELECT execution_ref
                FROM conversation_commands
                WHERE session_id = ? AND turn_id = ? AND disposition = 'running'
                """,
                (session_id, turn_id),
            ).fetchone()
            if row is None:
                return False
            current_ref = row["execution_ref"]
            if current_ref and execution_ref and current_ref != execution_ref and not allow_rebind:
                raise ConversationCommandConflict(
                    "conversation command is already bound to another execution"
                )
            resolved_ref = (
                execution_ref
                if allow_rebind and execution_ref
                else current_ref or execution_ref
            )
            result = self._conn.execute(
                """
                UPDATE conversation_commands
                SET checkpointed_at_ms = COALESCE(checkpointed_at_ms, ?),
                    execution_ref = ?,
                    updated_at_ms = ?
                WHERE session_id = ? AND turn_id = ?
                  AND disposition = 'running'
                  AND (checkpointed_at_ms IS NULL OR execution_ref IS NOT ?)
                """,
                (
                    int(time.time() * 1000),
                    resolved_ref,
                    int(time.time() * 1000),
                    session_id,
                    turn_id,
                    resolved_ref,
                ),
            )
            self._conn.commit()
        return result.rowcount > 0

    def checkpointed_running(self, session_id: str | None = None) -> list[RunningCommand]:
        """List durable running claims in stable acceptance order for startup recovery."""

        with self._lock:
            if session_id is None:
                rows = self._conn.execute(
                    """
                SELECT session_id, client_command_id, turn_id, execution_ref
                FROM conversation_commands
                WHERE disposition = 'running'
                  AND turn_id IS NOT NULL
                  AND checkpointed_at_ms IS NOT NULL
                ORDER BY created_at_ms, session_id, client_command_id
                """
                ).fetchall()
            else:
                rows = self._conn.execute(
                    """
                    SELECT session_id, client_command_id, turn_id, execution_ref
                    FROM conversation_commands
                    WHERE session_id = ?
                      AND disposition = 'running'
                      AND turn_id IS NOT NULL
                      AND checkpointed_at_ms IS NOT NULL
                    ORDER BY created_at_ms, client_command_id
                    """,
                    (session_id,),
                ).fetchall()
        return [
            RunningCommand(
                session_id=str(row["session_id"]),
                client_command_id=str(row["client_command_id"]),
                turn_id=str(row["turn_id"]),
                execution_ref=(
                    str(row["execution_ref"]) if row["execution_ref"] is not None else None
                ),
            )
            for row in rows
        ]

    def resolve_startup_execution(
        self, session_id: str, execution_ref: str
    ) -> tuple[RunningCommand, list[RunningCommand]] | None:
        """Resolve one current execution and any legacy stale predecessors atomically."""

        with self._lock:
            commands = self.checkpointed_running(session_id)
            exact = [item for item in commands if item.execution_ref == execution_ref]
            if len(exact) == 1 and all(
                item.execution_ref in {None, execution_ref} for item in commands
            ):
                return exact[0], [item for item in commands if item != exact[0]]
            if not commands or any(item.execution_ref is not None for item in commands):
                return None
            current = commands[-1]
            result = self._conn.execute(
                """
                UPDATE conversation_commands
                SET execution_ref = ?, updated_at_ms = ?
                WHERE session_id = ? AND turn_id = ?
                  AND disposition = 'running' AND execution_ref IS NULL
                """,
                (execution_ref, int(time.time() * 1000), session_id, current.turn_id),
            )
            self._conn.commit()
            if result.rowcount != 1:
                return None
            return (
                RunningCommand(
                    session_id=current.session_id,
                    client_command_id=current.client_command_id,
                    turn_id=current.turn_id,
                    execution_ref=execution_ref,
                ),
                commands[:-1],
            )

    def recover_uncheckpointed(self, session_id: str, turn_id: str) -> bool:
        """Move uncertain accepted input to a paused queue without replaying it."""

        now_ms = int(time.time() * 1000)
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                row = self._conn.execute(
                    """
                    SELECT client_command_id, created_at_ms
                    FROM conversation_commands
                    WHERE session_id = ? AND turn_id = ?
                      AND disposition = 'running'
                      AND queue_item_id IS NULL
                      AND checkpointed_at_ms IS NULL
                    """,
                    (session_id, turn_id),
                ).fetchone()
                if row is None:
                    self._conn.commit()
                    return False
                queue_item_id = f"queue_{uuid.uuid4().hex}"
                next_position = self._conn.execute(
                    """
                    SELECT COALESCE(MAX(position), 0) + 1
                    FROM conversation_queue WHERE session_id = ?
                    """,
                    (session_id,),
                ).fetchone()[0]
                self._conn.execute(
                    """
                    INSERT INTO conversation_queue (
                        session_id, queue_item_id, client_command_id, position, state,
                        delivery, revision, created_at_ms
                    ) VALUES (?, ?, ?, ?, 'queued', 'start_now', 1, ?)
                    """,
                    (
                        session_id,
                        queue_item_id,
                        row["client_command_id"],
                        int(next_position),
                        row["created_at_ms"],
                    ),
                )
                self._conn.execute(
                    """
                    UPDATE conversation_commands
                    SET disposition = 'queued', turn_id = NULL, queue_item_id = ?,
                        updated_at_ms = ?
                    WHERE session_id = ? AND client_command_id = ?
                    """,
                    (queue_item_id, now_ms, session_id, row["client_command_id"]),
                )
                self._set_queue_pause(session_id, True, "restart_uncertain", now_ms=now_ms)
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        return True

    def mark_terminal(self, session_id: str, turn_id: str | None, *, outcome_ref: str) -> bool:
        if not turn_id:
            return False
        now_ms = int(time.time() * 1000)
        with self._lock:
            result = self._conn.execute(
                """
                UPDATE conversation_commands
                SET disposition = 'terminal', outcome_ref = ?, updated_at_ms = ?
                WHERE session_id = ? AND turn_id = ?
                """,
                (outcome_ref, now_ms, session_id, turn_id),
            )
            self._conn.execute(
                """
                DELETE FROM conversation_queue
                WHERE session_id = ? AND client_command_id IN (
                    SELECT client_command_id FROM conversation_commands
                    WHERE session_id = ? AND turn_id = ?
                )
                """,
                (session_id, session_id, turn_id),
            )
            self._normalize_queue_positions(session_id)
            self._clear_queue_pause_if_empty(session_id)
            self._conn.commit()
        return result.rowcount > 0

    def mark_execution_terminal(
        self, session_id: str, execution_ref: str, *, outcome_ref: str
    ) -> bool:
        """Finish only the running command bound to an authoritative execution."""

        now_ms = int(time.time() * 1000)
        with self._lock:
            result = self._conn.execute(
                """
                UPDATE conversation_commands
                SET disposition = 'terminal', outcome_ref = ?, updated_at_ms = ?
                WHERE session_id = ? AND execution_ref = ? AND disposition = 'running'
                """,
                (outcome_ref, now_ms, session_id, execution_ref),
            )
            self._conn.execute(
                """
                DELETE FROM conversation_queue
                WHERE session_id = ? AND client_command_id IN (
                    SELECT client_command_id FROM conversation_commands
                    WHERE session_id = ? AND execution_ref = ?
                )
                """,
                (session_id, session_id, execution_ref),
            )
            self._normalize_queue_positions(session_id)
            self._clear_queue_pause_if_empty(session_id)
            self._conn.commit()
        return result.rowcount > 0

    def _set_queue_pause(
        self, session_id: str, paused: bool, reason: str | None, *, now_ms: int | None = None
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO conversation_queue_state (session_id, paused, reason, updated_at_ms)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                paused = excluded.paused,
                reason = excluded.reason,
                updated_at_ms = excluded.updated_at_ms
            """,
            (session_id, int(paused), reason, now_ms or int(time.time() * 1000)),
        )

    def _clear_queue_pause_if_empty(self, session_id: str) -> None:
        remaining = self._conn.execute(
            "SELECT 1 FROM conversation_queue WHERE session_id = ? LIMIT 1",
            (session_id,),
        ).fetchone()
        if remaining is None:
            self._conn.execute(
                "DELETE FROM conversation_queue_state WHERE session_id = ?",
                (session_id,),
            )

    def queue_status(self, session_id: str) -> dict[str, Any]:
        with self._lock:
            row = self._conn.execute(
                "SELECT paused, reason FROM conversation_queue_state WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        return {
            "paused": bool(row["paused"]) if row is not None else False,
            "reason": row["reason"] if row is not None else None,
        }

    def pause_queue(
        self, session_id: str, reason: str, *, restore_dispatching: bool = False
    ) -> None:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                if restore_dispatching:
                    self._conn.execute(
                        """
                        UPDATE conversation_queue
                        SET state = 'queued', revision = revision + 1
                        WHERE session_id = ? AND state = 'dispatching'
                        """,
                        (session_id,),
                    )
                    self._conn.execute(
                        """
                        UPDATE conversation_commands
                        SET disposition = 'queued', turn_id = NULL, updated_at_ms = ?
                        WHERE session_id = ? AND queue_item_id IN (
                            SELECT queue_item_id FROM conversation_queue
                            WHERE session_id = ? AND state = 'queued'
                        )
                        """,
                        (int(time.time() * 1000), session_id, session_id),
                    )
                self._set_queue_pause(session_id, True, reason)
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def resume_queue(self, session_id: str, *, idempotency_key: str | None = None) -> bool:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                existing = self._existing_mutation(
                    session_id,
                    idempotency_key,
                    kind="queue_resume",
                    queue_item_id="__queue__",
                )
                if existing is not None:
                    self._conn.commit()
                    return False
                state = self._conn.execute(
                    "SELECT paused FROM conversation_queue_state WHERE session_id = ?",
                    (session_id,),
                ).fetchone()
                resumed = bool(state is not None and state["paused"])
                self._set_queue_pause(session_id, False, None)
                self._record_mutation(
                    session_id,
                    idempotency_key,
                    kind="queue_resume",
                    queue_item_id="__queue__",
                    result={"resumed": resumed},
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        return resumed

    def queue_snapshot(self, session_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT q.queue_item_id, q.client_command_id, q.position, q.state,
                       q.delivery, q.revision, q.created_at_ms, c.payload_json
                FROM conversation_queue AS q
                JOIN conversation_commands AS c
                  ON c.session_id = q.session_id
                 AND c.client_command_id = q.client_command_id
                WHERE q.session_id = ? AND q.state IN ('queued', 'dispatching')
                ORDER BY q.position, q.created_at_ms, q.queue_item_id
                """,
                (session_id,),
            ).fetchall()
        items: list[dict[str, Any]] = []
        for row in rows:
            payload = json.loads(row["payload_json"])
            text = str(payload.get("display") or payload.get("text") or "")
            attachments = payload.get("attachments")
            items.append(
                {
                    "queueItemId": row["queue_item_id"],
                    "clientCommandId": row["client_command_id"],
                    "position": row["position"],
                    "state": row["state"],
                    "requestedDelivery": row["delivery"],
                    "revision": row["revision"],
                    "safePreview": text[:160],
                    "attachmentCount": len(attachments) if isinstance(attachments, list) else 0,
                    "contextCount": len(payload.get("contextRefs", []))
                    if isinstance(payload.get("contextRefs"), list)
                    else 0,
                    "createdAtMs": row["created_at_ms"],
                }
            )
        return items

    def claim_next(self, session_id: str) -> tuple[CommandReceipt, dict[str, Any]] | None:
        now_ms = int(time.time() * 1000)
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                state = self._conn.execute(
                    "SELECT paused FROM conversation_queue_state WHERE session_id = ?",
                    (session_id,),
                ).fetchone()
                if state is not None and bool(state["paused"]):
                    self._conn.commit()
                    return None
                row = self._conn.execute(
                    """
                    SELECT q.queue_item_id, q.client_command_id, c.payload_json
                    FROM conversation_queue AS q
                    JOIN conversation_commands AS c
                      ON c.session_id = q.session_id
                     AND c.client_command_id = q.client_command_id
                    WHERE q.session_id = ? AND q.state = 'queued'
                    ORDER BY q.position, q.created_at_ms, q.queue_item_id
                    LIMIT 1
                    """,
                    (session_id,),
                ).fetchone()
                if row is None:
                    self._conn.commit()
                    return None
                turn_id = f"turn_{uuid.uuid4().hex}"
                self._conn.execute(
                    """
                    UPDATE conversation_commands
                    SET disposition = 'running', turn_id = ?, updated_at_ms = ?
                    WHERE session_id = ? AND client_command_id = ?
                    """,
                    (turn_id, now_ms, session_id, row["client_command_id"]),
                )
                self._conn.execute(
                    """
                    UPDATE conversation_queue
                    SET state = 'dispatching', revision = revision + 1
                    WHERE session_id = ? AND queue_item_id = ?
                    """,
                    (session_id, row["queue_item_id"]),
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        payload = json.loads(row["payload_json"])
        if not isinstance(payload, dict):
            raise ValueError("stored conversation command payload is not an object")
        return (
            CommandReceipt(
                client_command_id=str(row["client_command_id"]),
                status="accepted",
                disposition="running",
                turn_id=turn_id,
                queue_item_id=str(row["queue_item_id"]),
            ),
            payload,
        )

    def _queued_row(
        self, session_id: str, queue_item_id: str, expected_revision: int
    ) -> sqlite3.Row:
        row = self._conn.execute(
            """
            SELECT q.*, c.payload_json
            FROM conversation_queue AS q
            JOIN conversation_commands AS c
              ON c.session_id = q.session_id
             AND c.client_command_id = q.client_command_id
            WHERE q.session_id = ? AND q.queue_item_id = ?
            """,
            (session_id, queue_item_id),
        ).fetchone()
        if row is None:
            raise KeyError(queue_item_id)
        if row["state"] != "queued" or int(row["revision"]) != expected_revision:
            raise ConversationCommandConflict("queue item revision is stale")
        return row

    def _normalize_queue_positions(self, session_id: str) -> None:
        rows = self._conn.execute(
            """
            SELECT queue_item_id, position
            FROM conversation_queue
            WHERE session_id = ? AND state = 'queued'
            ORDER BY position, created_at_ms, queue_item_id
            """,
            (session_id,),
        ).fetchall()
        for position, row in enumerate(rows, start=1):
            if int(row["position"]) == position:
                continue
            self._conn.execute(
                """
                UPDATE conversation_queue
                SET position = ?, revision = revision + 1
                WHERE session_id = ? AND queue_item_id = ?
                """,
                (position, session_id, row["queue_item_id"]),
            )

    def _existing_mutation(
        self,
        session_id: str,
        idempotency_key: str | None,
        *,
        kind: str,
        queue_item_id: str,
    ) -> dict[str, Any] | None:
        if not idempotency_key:
            return None
        row = self._conn.execute(
            """
            SELECT kind, queue_item_id, result_json
            FROM conversation_queue_mutations
            WHERE session_id = ? AND idempotency_key = ?
            """,
            (session_id, idempotency_key),
        ).fetchone()
        if row is None:
            return None
        if row["kind"] != kind or row["queue_item_id"] != queue_item_id:
            raise ConversationCommandConflict("queue mutation idempotency key was reused")
        result = json.loads(row["result_json"])
        if not isinstance(result, dict):
            raise ValueError("stored queue mutation result is not an object")
        return result

    def _record_mutation(
        self,
        session_id: str,
        idempotency_key: str | None,
        *,
        kind: str,
        queue_item_id: str,
        result: dict[str, Any],
    ) -> None:
        if not idempotency_key:
            return
        self._conn.execute(
            """
            INSERT INTO conversation_queue_mutations (
                session_id, idempotency_key, kind, queue_item_id,
                result_json, created_at_ms
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                idempotency_key,
                kind,
                queue_item_id,
                json.dumps(result, separators=(",", ":")),
                int(time.time() * 1000),
            ),
        )

    def delete_queue_item(
        self,
        session_id: str,
        queue_item_id: str,
        *,
        expected_revision: int,
        idempotency_key: str | None = None,
    ) -> bool:
        now_ms = int(time.time() * 1000)
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                existing = self._existing_mutation(
                    session_id,
                    idempotency_key,
                    kind="queue_delete",
                    queue_item_id=queue_item_id,
                )
                if existing is not None:
                    self._conn.commit()
                    return bool(existing.get("deleted"))
                row = self._queued_row(session_id, queue_item_id, expected_revision)
                self._conn.execute(
                    "DELETE FROM conversation_queue WHERE session_id = ? AND queue_item_id = ?",
                    (session_id, queue_item_id),
                )
                self._normalize_queue_positions(session_id)
                self._clear_queue_pause_if_empty(session_id)
                self._conn.execute(
                    """
                    UPDATE conversation_commands
                    SET disposition = 'terminal', outcome_ref = 'queue_deleted', updated_at_ms = ?
                    WHERE session_id = ? AND client_command_id = ?
                    """,
                    (now_ms, session_id, row["client_command_id"]),
                )
                self._record_mutation(
                    session_id,
                    idempotency_key,
                    kind="queue_delete",
                    queue_item_id=queue_item_id,
                    result={"deleted": True},
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        return True

    def restore_queue_item(
        self,
        session_id: str,
        queue_item_id: str,
        *,
        expected_revision: int,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                existing = self._existing_mutation(
                    session_id,
                    idempotency_key,
                    kind="queue_edit",
                    queue_item_id=queue_item_id,
                )
                if existing is not None:
                    self._conn.commit()
                    payload = existing.get("payload")
                    if not isinstance(payload, dict):
                        raise ValueError("stored queue edit payload is not an object")
                    return payload
                row = self._queued_row(session_id, queue_item_id, expected_revision)
                payload = json.loads(row["payload_json"])
                self._conn.execute(
                    "DELETE FROM conversation_queue WHERE session_id = ? AND queue_item_id = ?",
                    (session_id, queue_item_id),
                )
                self._normalize_queue_positions(session_id)
                self._clear_queue_pause_if_empty(session_id)
                self._conn.execute(
                    """
                    UPDATE conversation_commands
                    SET disposition = 'terminal', outcome_ref = 'queue_restored', updated_at_ms = ?
                    WHERE session_id = ? AND client_command_id = ?
                    """,
                    (int(time.time() * 1000), session_id, row["client_command_id"]),
                )
                self._record_mutation(
                    session_id,
                    idempotency_key,
                    kind="queue_edit",
                    queue_item_id=queue_item_id,
                    result={"payload": payload},
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        if not isinstance(payload, dict):
            raise ValueError("stored conversation command payload is not an object")
        return payload

    def prioritize_queue_item(
        self,
        session_id: str,
        queue_item_id: str,
        *,
        expected_revision: int,
        idempotency_key: str | None = None,
    ) -> bool:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                existing = self._existing_mutation(
                    session_id,
                    idempotency_key,
                    kind="queue_send_now",
                    queue_item_id=queue_item_id,
                )
                if existing is not None:
                    self._conn.commit()
                    return False
                self._queued_row(session_id, queue_item_id, expected_revision)
                self._conn.execute(
                    """
                    UPDATE conversation_queue
                    SET position = position + 1, revision = revision + 1
                    WHERE session_id = ? AND state = 'queued'
                    """,
                    (session_id,),
                )
                self._conn.execute(
                    """
                    UPDATE conversation_queue
                    SET position = 1
                    WHERE session_id = ? AND queue_item_id = ?
                    """,
                    (session_id, queue_item_id),
                )
                self._set_queue_pause(session_id, False, None)
                self._record_mutation(
                    session_id,
                    idempotency_key,
                    kind="queue_send_now",
                    queue_item_id=queue_item_id,
                    result={"prioritized": True},
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        return True

    def move_queue_item(
        self,
        session_id: str,
        queue_item_id: str,
        *,
        expected_revision: int,
        target_position: int,
        idempotency_key: str | None = None,
    ) -> None:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                existing = self._existing_mutation(
                    session_id,
                    idempotency_key,
                    kind="queue_move",
                    queue_item_id=queue_item_id,
                )
                if existing is not None:
                    self._conn.commit()
                    return
                self._queued_row(session_id, queue_item_id, expected_revision)
                rows = self._conn.execute(
                    """
                    SELECT queue_item_id, position
                    FROM conversation_queue
                    WHERE session_id = ? AND state = 'queued'
                    ORDER BY position, created_at_ms, queue_item_id
                    """,
                    (session_id,),
                ).fetchall()
                ordered = [str(row["queue_item_id"]) for row in rows]
                ordered.remove(queue_item_id)
                destination = max(1, min(int(target_position), len(ordered) + 1))
                ordered.insert(destination - 1, queue_item_id)
                positions = {str(row["queue_item_id"]): int(row["position"]) for row in rows}
                for position, item_id in enumerate(ordered, start=1):
                    if positions[item_id] == position:
                        continue
                    self._conn.execute(
                        """
                        UPDATE conversation_queue
                        SET position = ?, revision = revision + 1
                        WHERE session_id = ? AND queue_item_id = ?
                        """,
                        (position, session_id, item_id),
                    )
                self._record_mutation(
                    session_id,
                    idempotency_key,
                    kind="queue_move",
                    queue_item_id=queue_item_id,
                    result={"position": destination},
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def delete_session(self, session_id: str) -> None:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._conn.execute(
                    "DELETE FROM conversation_queue WHERE session_id = ?", (session_id,)
                )
                self._conn.execute(
                    "DELETE FROM conversation_commands WHERE session_id = ?", (session_id,)
                )
                self._conn.execute(
                    "DELETE FROM conversation_queue_mutations WHERE session_id = ?",
                    (session_id,),
                )
                self._conn.execute(
                    "DELETE FROM conversation_queue_state WHERE session_id = ?", (session_id,)
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def close(self) -> None:
        with self._lock:
            self._conn.close()
