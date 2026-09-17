"""SQLite-backed durable HaaS stores.

The implementation deliberately preserves the synchronous interface of the S2
``MemoryStore`` so existing runtimes can switch backends without an API rewrite.
SQLite transactions are used for idempotency reservation and completion, the
execution boundary where duplicate side effects must be prevented.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Callable
from dataclasses import MISSING, asdict, fields, is_dataclass
from pathlib import Path
from typing import Any

from haas.stores.memory import (
    ApprovalRecord,
    CanonicalEventRecord,
    DelegatedRuntimeRecord,
    DelegatedSessionRecord,
    HarnessRecord,
    IdempotencyExpiredError,
    IdempotencyRecord,
    IdempotencyReservation,
    InputRequestRecord,
    InvocationRecord,
    Lease,
    MemoryStore,
    ProfileRecord,
    ProviderConfig,
    SessionKey,
    SessionRecord,
    TurnRecord,
    WorkspaceLockResult,
    _now_ms,
)


class SQLiteStore(MemoryStore):
    """Durable store with forward-only schema migrations."""

    SCHEMA_VERSION = 1

    def __init__(
        self,
        path: str | Path,
        *,
        clock_ms: Callable[[], int] = _now_ms,
        idempotency_ttl_ms: int = 86_400_000,
    ) -> None:
        super().__init__(clock_ms=clock_ms, idempotency_ttl_ms=idempotency_ttl_ms)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode = WAL")
        self._db.execute("PRAGMA foreign_keys = ON")
        self._migrate()
        self._load()

    @property
    def schema_version(self) -> int:
        row = self._db.execute("PRAGMA user_version").fetchone()
        return int(row[0])

    def close(self) -> None:
        self._db.close()

    def __enter__(self) -> SQLiteStore:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def _migrate(self) -> None:
        version = self.schema_version
        if version > self.SCHEMA_VERSION:
            raise RuntimeError(
                f"store schema {version} is newer than supported {self.SCHEMA_VERSION}"
            )
        if version < 1:
            with self._db:
                self._db.execute(
                    """CREATE TABLE IF NOT EXISTS records (
                           namespace TEXT NOT NULL,
                           record_key TEXT NOT NULL,
                           payload TEXT NOT NULL,
                           PRIMARY KEY(namespace, record_key)
                       )"""
                )
                self._db.execute(
                    """CREATE TABLE IF NOT EXISTS idempotency (
                           key_hash TEXT PRIMARY KEY,
                           request_hash TEXT NOT NULL,
                           result_json TEXT,
                           released INTEGER NOT NULL DEFAULT 0,
                           accepted INTEGER NOT NULL DEFAULT 0,
                           invocation_id TEXT,
                           accepted_at_ms INTEGER,
                           completed_at_ms INTEGER,
                           expires_at_ms INTEGER,
                           tombstone INTEGER NOT NULL DEFAULT 0,
                           schema_version INTEGER NOT NULL DEFAULT 1,
                           created_at_ms INTEGER NOT NULL
                       )"""
                )
                self._db.execute(f"PRAGMA user_version = {self.SCHEMA_VERSION}")

    def _load(self) -> None:
        constructors = {
            "approval": ApprovalRecord,
            "input_request": InputRequestRecord,
            "invocation": InvocationRecord,
            "session": SessionRecord,
            "turn": TurnRecord,
            "event": CanonicalEventRecord,
            "lease": Lease,
            "lease_token": Lease,
            "workspace_lock": Lease,
        }
        for row in self._db.execute("SELECT namespace, record_key, payload FROM records"):
            namespace = str(row["namespace"])
            try:
                data = json.loads(row["payload"])
                if namespace == "harness":
                    data = _normalize_record_payload(HarnessRecord, data)
                    provider = data.get("provider")
                    data["provider"] = (
                        None
                        if provider is None
                        else _construct_record(ProviderConfig, provider)
                    )
                    harness = HarnessRecord(**data)
                    self._harnesses[harness.id] = harness
                    continue
                if namespace == "delegated_session":
                    data = _normalize_record_payload(DelegatedSessionRecord, data)
                    data["runtime"] = _construct_record(
                        DelegatedRuntimeRecord, data.get("runtime") or {}
                    )
                    delegated = DelegatedSessionRecord(**data)
                    self._delegated_sessions[delegated.id] = delegated
                    self._delegated_by_manager_session[delegated.managerSessionId] = delegated.id
                    self._delegated_by_haas_session[delegated.haasSessionId] = delegated.id
                    continue
                if namespace == "profile":
                    profile = _construct_record(ProfileRecord, data)
                    self._profiles[profile.id] = profile
                    continue
                constructor = constructors.get(namespace)
                if constructor is None:
                    continue
                value: Any = _construct_record(constructor, data)
                if namespace == "session" and not _valid_session_record(value):
                    continue
            except (TypeError, ValueError, KeyError):
                continue
            if namespace == "session":
                self._sessions[(value.appName, value.userId, value.id)] = value
            elif namespace == "invocation":
                self._invocations[value.id] = value
            elif namespace == "turn":
                self._turns[value.id] = value
            elif namespace == "event":
                super().append(value)
            elif namespace == "approval":
                self._approvals[value.id] = value
            elif namespace == "input_request":
                self._input_requests[value.id] = value
            elif namespace == "lease":
                parts = str(row["record_key"]).split("|", 2)
                key = (parts[0], parts[1], parts[2])
                self._leases[key] = value
                self._lease_tokens[key] = value.token
            elif namespace == "lease_token":
                parts = str(row["record_key"]).split("|", 2)
                key = (parts[0], parts[1], parts[2])
                self._lease_tokens[key] = value.token
            elif namespace == "workspace_lock":
                self._workspace_locks[str(row["record_key"])] = value
        for row in self._db.execute("SELECT * FROM idempotency"):
            result_json = row["result_json"]
            self._idempotency[str(row["key_hash"])] = IdempotencyRecord(
                keyHash=str(row["key_hash"]),
                requestHash=str(row["request_hash"]),
                result=None if result_json is None else json.loads(result_json),
                released=bool(row["released"]),
                accepted=bool(row["accepted"]),
                invocationId=row["invocation_id"],
                acceptedAtMs=row["accepted_at_ms"],
                completedAtMs=row["completed_at_ms"],
                expiresAtMs=row["expires_at_ms"],
                tombstone=bool(row["tombstone"]),
                schemaVersion=int(row["schema_version"]),
                createdAtMs=int(row["created_at_ms"]),
            )

    def _put_record(self, namespace: str, key: str, value: Any) -> None:
        payload = json.dumps(asdict(value), separators=(",", ":"), sort_keys=True)
        self._db.execute(
            "INSERT INTO records(namespace, record_key, payload) VALUES(?, ?, ?) "
            "ON CONFLICT(namespace, record_key) DO UPDATE SET payload=excluded.payload",
            (namespace, key, payload),
        )

    def put_session(self, session: SessionRecord) -> SessionRecord:
        with self._lock:
            record = super().put_session(session)
            self._put_record(
                "session", "|".join((record.appName, record.userId, record.id)), record
            )
            return record

    def save_harness(self, harness: HarnessRecord) -> HarnessRecord:
        with self._lock:
            record = super().save_harness(harness)
            self._put_record("harness", record.id, record)
            return record

    def save_profile(self, profile: ProfileRecord) -> ProfileRecord:
        with self._lock:
            record = super().save_profile(profile)
            self._put_record("profile", record.id, record)
            return record

    def delete_harness(self, harness_id: str) -> None:
        with self._lock:
            super().delete_harness(harness_id)
            self._db.execute(
                "DELETE FROM records WHERE namespace='harness' AND record_key=?",
                (harness_id,),
            )

    def delete_session(self, key: SessionKey) -> None:
        with self._lock:
            super().delete_session(key)
            self._db.execute(
                "DELETE FROM records WHERE namespace='session' AND record_key=?",
                ("|".join(key),),
            )

    def put_invocation(self, invocation: InvocationRecord) -> InvocationRecord:
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                record = super().put_invocation(invocation)
                self._put_record("invocation", record.id, record)
                if record.status in {"failed", "incomplete", "cancelled"}:
                    for approval in self._approvals.values():
                        if approval.invocationId == record.id:
                            self._put_record("approval", approval.id, approval)
                    for request in self._input_requests.values():
                        if request.invocationId == record.id:
                            self._put_record("input_request", request.id, request)
                self._db.execute("COMMIT")
                return record
            except BaseException:
                self._db.execute("ROLLBACK")
                raise

    def put_turn(self, turn: TurnRecord) -> TurnRecord:
        with self._lock:
            record = super().put_turn(turn)
            self._put_record("turn", record.id, record)
            return record

    def append(self, event: CanonicalEventRecord) -> None:
        with self._lock:
            super().append(event)
            self._put_record("event", event.eventId, event)

    def next_event_id(self) -> str:
        with self._lock:
            return super().next_event_id()

    def put_delegated_session(self, record: DelegatedSessionRecord) -> DelegatedSessionRecord:
        with self._lock:
            saved = super().put_delegated_session(record)
            self._put_record("delegated_session", saved.id, saved)
            return saved

    def put_approval(self, approval: ApprovalRecord) -> ApprovalRecord:
        with self._lock:
            record = super().put_approval(approval)
            self._put_record("approval", record.id, record)
            return record

    def resolve_approval(self, approval_id: str, decision: dict[str, Any]) -> ApprovalRecord:
        with self._lock:
            record = super().resolve_approval(approval_id, decision)
            self._put_record("approval", record.id, record)
            return record

    def close_pending_interactions(
        self, invocation_id: str, *, resolved_at_ms: int | None = None
    ) -> tuple[list[ApprovalRecord], list[InputRequestRecord]]:
        with self._lock:
            approvals, inputs = super().close_pending_interactions(
                invocation_id, resolved_at_ms=resolved_at_ms
            )
            for approval in approvals:
                self._put_record("approval", approval.id, approval)
            for request in inputs:
                self._put_record("input_request", request.id, request)
            return approvals, inputs

    def put_input_request(self, request: InputRequestRecord) -> InputRequestRecord:
        with self._lock:
            record = super().put_input_request(request)
            self._put_record("input_request", record.id, record)
            return record

    def resolve_input_request(self, request_id: str, answers: dict[str, Any]) -> InputRequestRecord:
        with self._lock:
            record = super().resolve_input_request(request_id, answers)
            self._put_record("input_request", record.id, record)
            return record

    def acquire_lease(self, key: SessionKey, holder: str, ttl_ms: int = 30_000) -> Lease:
        with self._lock:
            lease = super().acquire_lease(key, holder, ttl_ms)
            self._put_record("lease", "|".join(key), lease)
            self._put_record("lease_token", "|".join(key), lease)
            return lease

    def renew_lease(self, key: SessionKey, holder: str, token: int, ttl_ms: int = 30_000) -> Lease:
        with self._lock:
            lease = super().renew_lease(key, holder, token, ttl_ms)
            self._put_record("lease", "|".join(key), lease)
            return lease

    def release_lease(self, key: SessionKey, holder: str, token: int | None = None) -> None:
        with self._lock:
            existing = self._leases.get(key)
            super().release_lease(key, holder, token)
            if existing is not None and self._leases.get(key) is None:
                self._db.execute(
                    "DELETE FROM records WHERE namespace='lease' AND record_key=?",
                    ("|".join(key),),
                )

    def acquire_workspace_lock(
        self,
        canonical_workspace: str,
        delegated_session_id: str,
        access: str,
        ttl_ms: int = 30_000,
    ) -> WorkspaceLockResult:
        with self._lock:
            result = super().acquire_workspace_lock(
                canonical_workspace, delegated_session_id, access, ttl_ms
            )
            lease = self._workspace_locks.get(canonical_workspace)
            if access != "ro" and result.acquired and lease is not None:
                self._put_record("workspace_lock", canonical_workspace, lease)
            return result

    def release_workspace_lock(self, canonical_workspace: str, delegated_session_id: str) -> None:
        with self._lock:
            super().release_workspace_lock(canonical_workspace, delegated_session_id)
            if canonical_workspace not in self._workspace_locks:
                self._db.execute(
                    "DELETE FROM records WHERE namespace='workspace_lock' AND record_key=?",
                    (canonical_workspace,),
                )

    def reserve(self, key_hash: str, request_hash: str) -> IdempotencyReservation:
        with self._lock, self._db:
            self._reload_idempotency(key_hash)
            try:
                reservation = super().reserve(key_hash, request_hash)
            except IdempotencyExpiredError:
                self._write_idempotency(self._idempotency[key_hash])
                raise
            self._write_idempotency(self._idempotency[key_hash])
            return reservation

    def accept(
        self, key_hash: str, invocation_id: str, *, accepted_at_ms: int | None = None
    ) -> IdempotencyRecord:
        with self._lock, self._db:
            self._reload_idempotency(key_hash)
            record = super().accept(key_hash, invocation_id, accepted_at_ms=accepted_at_ms)
            self._write_idempotency(record)
            return record

    def complete(self, key_hash: str, result: Any, *, completed_at_ms: int | None = None) -> None:
        with self._lock, self._db:
            self._reload_idempotency(key_hash)
            super().complete(key_hash, result, completed_at_ms=completed_at_ms)
            record = self._idempotency.get(key_hash)
            if record is not None:
                self._write_idempotency(record)

    def replay(self, key_hash: str) -> Any | None:
        with self._lock, self._db:
            self._reload_idempotency(key_hash)
            result = super().replay(key_hash)
            record = self._idempotency.get(key_hash)
            if record is not None:
                self._write_idempotency(record)
            return result

    def release(self, key_hash: str) -> None:
        with self._lock, self._db:
            self._reload_idempotency(key_hash)
            super().release(key_hash)
            record = self._idempotency.get(key_hash)
            if record is not None:
                self._write_idempotency(record)

    def _reload_idempotency(self, key_hash: str) -> None:
        row = self._db.execute("SELECT * FROM idempotency WHERE key_hash=?", (key_hash,)).fetchone()
        if row is None:
            self._idempotency.pop(key_hash, None)
            return
        result_json = row["result_json"]
        self._idempotency[key_hash] = IdempotencyRecord(
            keyHash=key_hash,
            requestHash=str(row["request_hash"]),
            result=None if result_json is None else json.loads(result_json),
            released=bool(row["released"]),
            accepted=bool(row["accepted"]),
            invocationId=row["invocation_id"],
            acceptedAtMs=row["accepted_at_ms"],
            completedAtMs=row["completed_at_ms"],
            expiresAtMs=row["expires_at_ms"],
            tombstone=bool(row["tombstone"]),
            schemaVersion=int(row["schema_version"]),
            createdAtMs=int(row["created_at_ms"]),
        )

    def _write_idempotency(self, record: IdempotencyRecord) -> None:
        self._db.execute(
            """INSERT INTO idempotency(
                   key_hash, request_hash, result_json, released, accepted, invocation_id,
                   accepted_at_ms, completed_at_ms, expires_at_ms, tombstone,
                   schema_version, created_at_ms
               ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(key_hash) DO UPDATE SET
                   request_hash=excluded.request_hash, result_json=excluded.result_json,
                   released=excluded.released, accepted=excluded.accepted,
                   invocation_id=excluded.invocation_id, accepted_at_ms=excluded.accepted_at_ms,
                   completed_at_ms=excluded.completed_at_ms, expires_at_ms=excluded.expires_at_ms,
                   tombstone=excluded.tombstone, schema_version=excluded.schema_version""",
            (
                record.keyHash,
                record.requestHash,
                None if record.result is None else json.dumps(record.result, separators=(",", ":")),
                int(record.released),
                int(record.accepted),
                record.invocationId,
                record.acceptedAtMs,
                record.completedAtMs,
                record.expiresAtMs,
                int(record.tombstone),
                record.schemaVersion,
                record.createdAtMs,
            ),
        )


def _construct_record(constructor: type[Any], data: Any) -> Any:
    if not isinstance(data, dict):
        raise ValueError("record payload must be an object")
    normalized = _normalize_record_payload(constructor, data)
    for field in fields(constructor):
        if (
            field.default is MISSING
            and field.default_factory is MISSING
            and field.name not in normalized
        ):
            raise ValueError(f"record payload missing required field: {field.name}")
    return constructor(**normalized)


def _normalize_record_payload(constructor: type[Any], data: dict[str, Any]) -> dict[str, Any]:
    if constructor is SessionRecord and "controlState" not in data and "control_state" in data:
        data = {**data, "controlState": data["control_state"]}
    if not is_dataclass(constructor):
        return dict(data)
    allowed = {field.name for field in fields(constructor)}
    return {key: value for key, value in data.items() if key in allowed}


def _valid_session_record(record: Any) -> bool:
    return (
        isinstance(record.id, str)
        and bool(record.id)
        and isinstance(record.appName, str)
        and bool(record.appName)
        and isinstance(record.userId, str)
        and bool(record.userId)
        and record.controlState
        in {
            "idle",
            "running",
            "pausing",
            "paused",
            "resuming",
            "cancelling",
            "cancelled",
            "control_degraded",
        }
        and isinstance(record.supportsResume, bool)
        and (record.resumableInvocationId is None or isinstance(record.resumableInvocationId, str))
    )
