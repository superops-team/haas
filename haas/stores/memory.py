"""In-memory store backend for S2.

Implements the store domain interfaces from specs/stores/README.md §5/§6 with
plain dict storage. Default tests must use this backend (offline, no disk).

The AdmissionStore window/quota semantics are defined together with the
Admission Control component (haas-55t.6); this module ships the stores needed
by session/event-log/idempotency/registry first.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field, replace
from typing import Any

SessionKey = tuple[str, str, str]  # (appName, userId, sessionId)
InvocationEventKey = tuple[str, str, str, str]  # (appName, userId, sessionId, invocationId)
AccountKey = tuple[str | None, str | None]  # (tenantId, workspaceId)


def _now_ms() -> int:
    return int(time.time() * 1000)


@dataclass
class ProviderConfig:
    """Model provider route configuration (specs/harness-registry §6.1)."""

    name: str = "openai-compatible"
    baseUrl: str = ""
    wireApi: str = "responses"
    credentialRef: str = ""
    credentialFingerprint: str = ""
    allowlistRuleId: str = ""


@dataclass
class HarnessRecord:
    id: str
    name: str
    base: str
    status: str = "active"
    defaultModel: str | None = None
    systemPrompt: str = ""
    provider: ProviderConfig | None = None
    # Scope binding (specs/harness-registry §5.1.3). None means "unbound",
    # visible only to equally unbound principals.
    tenantId: str | None = None
    workspaceId: str | None = None
    mcpServers: list[dict[str, Any]] = field(default_factory=list)
    skills: list[dict[str, Any]] = field(default_factory=list)
    disabledTools: list[str] = field(default_factory=list)
    maxStep: int | None = None
    timeoutSeconds: int | None = None
    schemaVersion: int = 1
    createdAtMs: int = field(default_factory=_now_ms)
    updatedAtMs: int = field(default_factory=_now_ms)

    def account_key(self) -> AccountKey:
        return (self.tenantId, self.workspaceId)


@dataclass
class SessionRecord:
    id: str
    appName: str
    userId: str
    status: str = "active"
    state: dict[str, Any] = field(default_factory=dict)
    delegatedSessionRef: dict[str, Any] | None = None
    schemaVersion: int = 1
    createdAtMs: int = field(default_factory=_now_ms)
    updatedAtMs: int = field(default_factory=_now_ms)
    expiresAtMs: int | None = None


@dataclass
class InvocationRecord:
    id: str
    sessionId: str
    appName: str
    turnId: str
    userId: str = ""
    status: str = "running"
    schemaVersion: int = 1
    startedAtMs: int = field(default_factory=_now_ms)
    completedAtMs: int | None = None


@dataclass
class TurnRecord:
    id: str
    invocationId: str
    sessionId: str
    status: str = "running"
    schemaVersion: int = 1
    startedAtMs: int = field(default_factory=_now_ms)
    completedAtMs: int | None = None


@dataclass
class DelegatedRuntimeRecord:
    status: str = "no_runtime"
    containerId: str | None = None
    containerGeneration: int = 0
    lastStartedAtMs: int | None = None
    lastActiveAtMs: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "containerId": self.containerId,
            "containerGeneration": self.containerGeneration,
            "lastStartedAtMs": self.lastStartedAtMs,
            "lastActiveAtMs": self.lastActiveAtMs,
        }


@dataclass
class DelegatedSessionRecord:
    id: str
    managerSessionId: str
    haasSessionId: str
    haasUserId: str
    harnessId: str
    image: dict[str, Any]
    provider: dict[str, Any]
    mountManifest: dict[str, Any]
    delegationPolicySnapshot: dict[str, Any]
    harnessBase: str = "codex"
    binding: str = "haas_bound"
    runtime: DelegatedRuntimeRecord = field(default_factory=DelegatedRuntimeRecord)
    object: str = "delegated_session"
    schemaVersion: int = 1
    createdAtMs: int = field(default_factory=_now_ms)
    updatedAtMs: int = field(default_factory=_now_ms)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "object": self.object,
            "managerSessionId": self.managerSessionId,
            "haasSessionId": self.haasSessionId,
            "haasUserId": self.haasUserId,
            "harnessId": self.harnessId,
            "harnessBase": self.harnessBase,
            "binding": self.binding,
            "image": dict(self.image),
            "provider": dict(self.provider),
            "mountManifest": dict(self.mountManifest),
            "delegationPolicySnapshot": dict(self.delegationPolicySnapshot),
            "runtime": self.runtime.to_dict(),
            "createdAtMs": self.createdAtMs,
            "updatedAtMs": self.updatedAtMs,
        }


@dataclass
class ApprovalRecord:
    id: str
    sessionId: str
    invocationId: str
    turnId: str
    status: str = "waiting"
    request: dict[str, Any] = field(default_factory=dict)
    decision: dict[str, Any] | None = None
    schemaVersion: int = 1
    createdAtMs: int = field(default_factory=_now_ms)
    resolvedAtMs: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "approvalId": self.id,
            "sessionId": self.sessionId,
            "invocationId": self.invocationId,
            "turnId": self.turnId,
            "status": self.status,
            "request": dict(self.request),
            "decision": None if self.decision is None else dict(self.decision),
            "createdAtMs": self.createdAtMs,
            "resolvedAtMs": self.resolvedAtMs,
        }


@dataclass
class CanonicalEventRecord:
    eventId: str
    invocationId: str
    sessionId: str
    turnId: str
    author: str
    sequenceNumber: int
    content: dict[str, Any]
    actions: dict[str, Any]
    schemaVersion: int = 1
    observedAtMs: int = field(default_factory=_now_ms)
    redactionApplied: bool = True
    harnessId: str = ""
    adapterId: str = ""
    appName: str = ""
    userId: str = ""


@dataclass
class IdempotencyRecord:
    keyHash: str
    requestHash: str
    result: Any | None = None
    released: bool = False
    schemaVersion: int = 1
    createdAtMs: int = field(default_factory=_now_ms)


@dataclass
class IdempotencyReservation:
    keyHash: str
    replay: bool
    result: Any | None


class IdempotencyConflictError(Exception):
    """Same idempotency key, different request hash: fail closed (409)."""


class CursorNotFoundError(Exception):
    """SSE replay cursor is unknown -> 410 haas_offset_expired."""


class DelegatedSessionNotFoundError(Exception):
    """Delegated session id is unknown -> 404 haas_delegated_session_not_found."""


class ApprovalNotFoundError(Exception):
    """Approval id is unknown -> 404 haas_approval_not_found."""


class ApprovalStateConflictError(Exception):
    """Approval was already resolved -> 409 haas_approval_state_conflict."""


@dataclass
class Lease:
    holder: str
    expiresAtMs: int
    token: int


class LeaseConflictError(Exception):
    """Another holder owns the active lease (maps to 409 session_busy)."""


class LeaseFencingError(Exception):
    """Holder/token no longer owns the lease; stale writes must fail closed."""


@dataclass
class WorkspaceLockResult:
    acquired: bool
    holder: str | None = None
    expiresAtMs: int | None = None


class MemoryStore:
    """Single-process in-memory backend behind the Stores domain interface."""

    def __init__(self) -> None:
        self._harnesses: dict[str, HarnessRecord] = {}
        self._sessions: dict[SessionKey, SessionRecord] = {}
        self._invocations: dict[str, InvocationRecord] = {}
        self._turns: dict[str, TurnRecord] = {}
        self._events_by_invocation: dict[
            InvocationEventKey, list[CanonicalEventRecord]
        ] = {}
        self._events_by_session: dict[SessionKey, list[CanonicalEventRecord]] = {}
        self._idempotency: dict[str, IdempotencyRecord] = {}
        self._leases: dict[SessionKey, Lease] = {}
        self._lease_tokens: dict[SessionKey, int] = {}
        self._workspace_locks: dict[str, Lease] = {}
        self._windows: dict[str, list[int]] = {}
        self._quotas: dict[str, int] = {}
        self._delegated_sessions: dict[str, DelegatedSessionRecord] = {}
        self._delegated_by_manager_session: dict[str, str] = {}
        self._delegated_by_haas_session: dict[str, str] = {}
        self._approvals: dict[str, ApprovalRecord] = {}

    # --- RegistryStore --------------------------------------------------

    def save_harness(self, harness: HarnessRecord) -> HarnessRecord:
        record = replace(harness, updatedAtMs=_now_ms())
        self._harnesses[record.id] = record
        return record

    def get_harness(self, harness_id: str) -> HarnessRecord | None:
        return self._harnesses.get(harness_id)

    def list_harnesses(self, account: AccountKey | None = None) -> list[HarnessRecord]:
        """List harnesses, optionally filtered to one account scope.

        Store-level equality filtering only; authorization semantics live in
        Harness Registry (specs/harness-registry §5.1.3).
        """
        records = list(self._harnesses.values())
        if account is None:
            return records
        return [r for r in records if r.account_key() == account]

    def delete_harness(self, harness_id: str) -> None:
        self._harnesses.pop(harness_id, None)

    # --- SessionStore ---------------------------------------------------

    def get_session(self, key: SessionKey) -> SessionRecord | None:
        return self._sessions.get(key)

    def put_session(self, session: SessionRecord) -> SessionRecord:
        record = replace(session, updatedAtMs=_now_ms())
        self._sessions[(record.appName, record.userId, record.id)] = record
        return record

    def delete_session(self, key: SessionKey) -> None:
        self._sessions.pop(key, None)

    def count_sessions(self) -> int:
        """Low-cardinality aggregate for observability status only."""
        return len(self._sessions)

    def list_sessions(
        self, *, app_name: str | None = None, user_ids: frozenset[str] | None = None
    ) -> list[SessionRecord]:
        """List sessions, newest first, optionally filtered by app and users.

        Ordering is stable (createdAtMs then id) so cursor pagination cannot
        skip or repeat entries between pages.
        """
        records = list(self._sessions.values())
        if app_name is not None:
            records = [r for r in records if r.appName == app_name]
        if user_ids is not None:
            records = [r for r in records if r.userId in user_ids]
        records.sort(key=lambda r: (r.createdAtMs, r.id), reverse=True)
        return records

    def put_invocation(self, invocation: InvocationRecord) -> InvocationRecord:
        self._invocations[invocation.id] = invocation
        return invocation

    def put_turn(self, turn: TurnRecord) -> TurnRecord:
        self._turns[turn.id] = turn
        return turn

    def get_invocation(self, invocation_id: str) -> InvocationRecord | None:
        return self._invocations.get(invocation_id)

    def get_turn(self, turn_id: str) -> TurnRecord | None:
        return self._turns.get(turn_id)

    # --- DelegationStore -----------------------------------------------

    def put_delegated_session(
        self, record: DelegatedSessionRecord
    ) -> DelegatedSessionRecord:
        existing = self._delegated_sessions.get(record.id)
        created_at = existing.createdAtMs if existing is not None else record.createdAtMs
        saved = replace(record, createdAtMs=created_at, updatedAtMs=_now_ms())
        self._delegated_sessions[saved.id] = saved
        self._delegated_by_manager_session[saved.managerSessionId] = saved.id
        self._delegated_by_haas_session[saved.haasSessionId] = saved.id
        return saved

    def get_delegated_session(
        self, delegated_session_id: str
    ) -> DelegatedSessionRecord | None:
        return self._delegated_sessions.get(delegated_session_id)

    def get_delegated_session_by_manager(
        self, manager_session_id: str
    ) -> DelegatedSessionRecord | None:
        delegated_id = self._delegated_by_manager_session.get(manager_session_id)
        if delegated_id is None:
            return None
        return self.get_delegated_session(delegated_id)

    def get_delegated_session_by_haas_session(
        self, haas_session_id: str
    ) -> DelegatedSessionRecord | None:
        delegated_id = self._delegated_by_haas_session.get(haas_session_id)
        if delegated_id is None:
            return None
        return self.get_delegated_session(delegated_id)

    def update_delegated_runtime(
        self, delegated_session_id: str, runtime: DelegatedRuntimeRecord
    ) -> DelegatedSessionRecord:
        record = self.get_delegated_session(delegated_session_id)
        if record is None:
            raise DelegatedSessionNotFoundError(delegated_session_id)
        return self.put_delegated_session(replace(record, runtime=runtime))

    def put_approval(self, approval: ApprovalRecord) -> ApprovalRecord:
        existing = self._approvals.get(approval.id)
        created_at = existing.createdAtMs if existing is not None else approval.createdAtMs
        record = replace(approval, createdAtMs=created_at)
        self._approvals[record.id] = record
        return record

    def get_approval(self, approval_id: str) -> ApprovalRecord | None:
        return self._approvals.get(approval_id)

    def resolve_approval(
        self, approval_id: str, decision: dict[str, Any]
    ) -> ApprovalRecord:
        record = self.get_approval(approval_id)
        if record is None:
            raise ApprovalNotFoundError(approval_id)
        if record.status != "waiting":
            raise ApprovalStateConflictError(approval_id)
        status = str(decision.get("decision") or "")
        resolved = replace(
            record,
            status=status,
            decision=decision,
            resolvedAtMs=_now_ms(),
        )
        self._approvals[approval_id] = resolved
        return resolved

    def acquire_workspace_lock(
        self,
        canonical_workspace: str,
        delegated_session_id: str,
        access: str,
        ttl_ms: int = 30_000,
    ) -> WorkspaceLockResult:
        if access == "ro":
            return WorkspaceLockResult(acquired=True, holder=delegated_session_id)
        now = _now_ms()
        existing = self._workspace_locks.get(canonical_workspace)
        if (
            existing is not None
            and existing.expiresAtMs > now
            and existing.holder != delegated_session_id
        ):
            return WorkspaceLockResult(
                acquired=False,
                holder=existing.holder,
                expiresAtMs=existing.expiresAtMs,
            )
        lease = Lease(holder=delegated_session_id, expiresAtMs=now + ttl_ms, token=0)
        self._workspace_locks[canonical_workspace] = lease
        return WorkspaceLockResult(
            acquired=True,
            holder=delegated_session_id,
            expiresAtMs=lease.expiresAtMs,
        )

    def release_workspace_lock(
        self, canonical_workspace: str, delegated_session_id: str
    ) -> None:
        existing = self._workspace_locks.get(canonical_workspace)
        if existing is not None and existing.holder == delegated_session_id:
            self._workspace_locks.pop(canonical_workspace, None)

    def acquire_lease(self, key: SessionKey, holder: str, ttl_ms: int = 30_000) -> Lease:
        now = _now_ms()
        existing = self._leases.get(key)
        if existing is not None and existing.expiresAtMs > now and existing.holder != holder:
            raise LeaseConflictError(
                f"session busy: held by {existing.holder!r} until {existing.expiresAtMs}"
            )
        token = self._lease_tokens.get(key, 0) + 1
        self._lease_tokens[key] = token
        lease = Lease(holder=holder, expiresAtMs=now + ttl_ms, token=token)
        self._leases[key] = lease
        return lease

    def renew_lease(
        self, key: SessionKey, holder: str, token: int, ttl_ms: int = 30_000
    ) -> Lease:
        self.assert_lease(key, holder, token)
        lease = Lease(holder=holder, expiresAtMs=_now_ms() + ttl_ms, token=token)
        self._leases[key] = lease
        return lease

    def assert_lease(self, key: SessionKey, holder: str, token: int) -> None:
        now = _now_ms()
        existing = self._leases.get(key)
        if (
            existing is None
            or existing.expiresAtMs <= now
            or existing.holder != holder
            or existing.token != token
        ):
            raise LeaseFencingError("stale session lease holder")

    def release_lease(self, key: SessionKey, holder: str, token: int | None = None) -> None:
        existing = self._leases.get(key)
        if (
            existing is not None
            and existing.holder == holder
            and (token is None or existing.token == token)
        ):
            self._leases.pop(key, None)

    # --- EventLogStore --------------------------------------------------

    def append(self, event: CanonicalEventRecord) -> None:
        session_key = (event.appName, event.userId, event.sessionId)
        invocation_key = (*session_key, event.invocationId)
        self._events_by_invocation.setdefault(invocation_key, []).append(event)
        self._events_by_session.setdefault(session_key, []).append(event)

    def read_invocation(
        self,
        key: SessionKey,
        invocation_id: str,
        after: int = -1,
    ) -> list[CanonicalEventRecord]:
        events = self._events_by_invocation.get((*key, invocation_id), [])
        return [
            e
            for e in sorted(events, key=lambda e: e.sequenceNumber)
            if e.sequenceNumber > after
        ]

    def read_session(
        self, key: SessionKey, after_cursor: str | None = None
    ) -> list[CanonicalEventRecord]:
        events = self._events_by_session.get(key, [])
        if after_cursor is None:
            return list(events)
        index = next((i for i, e in enumerate(events) if e.eventId == after_cursor), None)
        if index is None:
            raise CursorNotFoundError(after_cursor)
        return events[index + 1 :]

    # --- AdmissionStore -------------------------------------------------

    def incr_window(self, bucket: str, now_ms: int, window_ms: int) -> int:
        """Record a request and return the count inside the trailing window."""
        cutoff = now_ms - window_ms
        times = [t for t in self._windows.setdefault(bucket, []) if t >= cutoff]
        times.append(now_ms)
        self._windows[bucket] = times
        return len(times)

    def acquire_quota(self, bucket: str, limit: int) -> bool:
        current = self._quotas.get(bucket, 0)
        if current >= limit:
            return False
        self._quotas[bucket] = current + 1
        return True

    def release_quota(self, bucket: str) -> None:
        self._quotas[bucket] = max(0, self._quotas.get(bucket, 0) - 1)

    def quota_count(self, bucket: str) -> int:
        return self._quotas.get(bucket, 0)

    # --- IdempotencyStore ----------------------------------------------

    def reserve(self, key_hash: str, request_hash: str) -> IdempotencyReservation:
        existing = self._idempotency.get(key_hash)
        if existing is not None and not existing.released:
            if existing.requestHash != request_hash:
                raise IdempotencyConflictError(key_hash)
            return IdempotencyReservation(keyHash=key_hash, replay=True, result=existing.result)
        record = IdempotencyRecord(keyHash=key_hash, requestHash=request_hash)
        self._idempotency[key_hash] = record
        return IdempotencyReservation(keyHash=key_hash, replay=False, result=None)

    def complete(self, key_hash: str, result: Any) -> None:
        record = self._idempotency.get(key_hash)
        if record is not None:
            record.result = result

    def replay(self, key_hash: str) -> Any | None:
        record = self._idempotency.get(key_hash)
        if record is None or record.released:
            return None
        return record.result

    def is_pending(self, key_hash: str) -> bool:
        """True while a reservation is in-flight (not completed, not released)."""
        record = self._idempotency.get(key_hash)
        return record is not None and not record.released and record.result is None

    def release(self, key_hash: str) -> None:
        record = self._idempotency.get(key_hash)
        if record is not None:
            record.released = True
