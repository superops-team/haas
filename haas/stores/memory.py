"""In-memory store backend for S2.

Implements the store domain interfaces from specs/stores/README.md §5/§6 with
plain dict storage. Default tests must use this backend (offline, no disk).

The AdmissionStore window/quota semantics are defined together with the
Admission Control component (haas-55t.6); this module ships the stores needed
by session/event-log/idempotency/registry first.
"""

from __future__ import annotations

import contextlib
import re
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field, replace
from typing import Any

SessionKey = tuple[str, str, str]  # (appName, userId, sessionId)
InvocationEventKey = tuple[str, str, str, str]  # (appName, userId, sessionId, invocationId)
AccountKey = tuple[str | None, str | None]  # (tenantId, workspaceId)

#: Canonical session control states (specs/session-runtime/). Kept as a module
#: constant so the durable loader and the in-memory writer agree on the set.
KNOWN_CONTROL_STATES = frozenset(
    {
        "idle",
        "running",
        "pausing",
        "paused",
        "resuming",
        "cancelling",
        "cancelled",
        "control_degraded",
    }
)


def default_session_policy() -> dict[str, Any]:
    return {
        "workspace": {
            "mode": "workspace-write",
            "root": "/workspace",
            "writableRoots": ["/workspace"],
        },
        "network": {"defaultAction": "allow", "allow": []},
        "tools": {"disabled": [], "approvalMode": "on-request"},
    }


def _now_ms() -> int:
    return int(time.time() * 1000)


@dataclass
class ProviderConfig:
    """Model provider route configuration (specs/harness-registry §6.1)."""

    providerId: str = "openai"
    name: str = "openai-compatible"
    baseUrl: str = ""
    wireApi: str = "responses"
    apiType: str = "responses"
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
class ProfileRecord:
    """One immutable-or-draft harness profile revision."""

    id: str
    harnessId: str
    base: str
    version: int
    content: dict[str, Any]
    profileFingerprint: str
    status: str = "draft"
    validation: dict[str, Any] | None = None
    tenantId: str | None = None
    workspaceId: str | None = None
    object: str = "harness_profile"
    schemaVersion: int = 1
    createdAtMs: int = field(default_factory=_now_ms)
    updatedAtMs: int = field(default_factory=_now_ms)
    activatedAtMs: int | None = None

    def account_key(self) -> AccountKey:
        return (self.tenantId, self.workspaceId)


@dataclass
class SessionRecord:
    id: str
    appName: str
    userId: str
    status: str = "active"
    state: dict[str, Any] = field(default_factory=dict)
    nativeSessionRef: dict[str, Any] | None = None
    delegatedSessionRef: dict[str, Any] | None = None
    effectiveProfile: dict[str, Any] | None = None
    desiredPolicy: dict[str, Any] = field(default_factory=default_session_policy)
    appliedPolicy: dict[str, Any] = field(default_factory=default_session_policy)
    desiredRevision: int = 1
    appliedRevision: int = 1
    policyStatus: str = "applied"
    pendingPolicyUpdate: dict[str, Any] | None = None
    lastPolicyUpdateResult: dict[str, Any] | None = None
    controlState: str = "idle"
    supportsResume: bool = False
    resumableInvocationId: str | None = None
    schemaVersion: int = 1
    createdAtMs: int = field(default_factory=_now_ms)
    updatedAtMs: int = field(default_factory=_now_ms)
    expiresAtMs: int | None = None

    def __post_init__(self) -> None:
        # Lightweight boundary validation (P2-06): bare dataclasses otherwise
        # let typo'd control states flow through writes unchecked. The durable
        # loader still quarantines bad rows; this guards the trusted write path.
        if self.controlState not in KNOWN_CONTROL_STATES:
            raise ValueError(f"invalid session controlState: {self.controlState!r}")


@dataclass
class InvocationRecord:
    id: str
    sessionId: str
    appName: str
    turnId: str
    userId: str = ""
    status: str = "running"
    acceptedAtMs: int | None = None
    idempotencyKeyHash: str | None = None
    idempotencyExpiresAtMs: int | None = None
    schemaVersion: int = 1
    startedAtMs: int = field(default_factory=_now_ms)
    completedAtMs: int | None = None
    timeoutSeconds: float | None = None
    deadlineAtMs: int | None = None
    continuedFromInvocationId: str | None = None
    continuedFromTurnId: str | None = None
    executionContext: dict[str, Any] = field(default_factory=dict)
    nativeTurnRef: dict[str, Any] | None = None


@dataclass
class TurnRecord:
    id: str
    invocationId: str
    sessionId: str
    status: str = "running"
    schemaVersion: int = 1
    startedAtMs: int = field(default_factory=_now_ms)
    completedAtMs: int | None = None
    continuedFromInvocationId: str | None = None
    continuedFromTurnId: str | None = None
    nativeTurnRef: dict[str, Any] | None = None


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
    profileRef: dict[str, Any] = field(default_factory=dict)
    workspaceMode: str = "bind_mount"
    acceptedInvocationId: str | None = None
    bindingAcceptedAtMs: int | None = None
    bindingFailureSafeReason: str | None = None
    desiredRevision: int = 1
    appliedRevision: int = 1
    pendingPolicyUpdate: dict[str, Any] | None = None
    # Private, fully resolved candidate. It is durable but deliberately omitted
    # from the public projection because it may contain host mount paths and
    # other controller-only configuration.
    pendingPolicyTarget: dict[str, Any] | None = None
    lastPolicyUpdateResult: dict[str, Any] | None = None
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
            "profileRef": dict(self.profileRef),
            "provider": dict(self.provider),
            "workspaceMode": self.workspaceMode,
            "mountManifest": dict(self.mountManifest),
            "delegationPolicySnapshot": dict(self.delegationPolicySnapshot),
            "runtime": self.runtime.to_dict(),
            "acceptedInvocationId": self.acceptedInvocationId,
            "bindingAcceptedAtMs": self.bindingAcceptedAtMs,
            "bindingFailureSafeReason": self.bindingFailureSafeReason,
            "desiredRevision": self.desiredRevision,
            "appliedRevision": self.appliedRevision,
            "pendingPolicyUpdate": self.pendingPolicyUpdate,
            "lastPolicyUpdateResult": self.lastPolicyUpdateResult,
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
    nativeRequestId: str | int | None = None
    adapterGeneration: int | None = None
    expiresAtMs: int | None = None
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
            "expiresAtMs": self.expiresAtMs,
            "createdAtMs": self.createdAtMs,
            "resolvedAtMs": self.resolvedAtMs,
        }


@dataclass
class InputRequestRecord:
    id: str
    sessionId: str
    invocationId: str
    turnId: str
    questions: list[dict[str, Any]]
    nativeRequestId: str | int
    adapterGeneration: int
    status: str = "waiting"
    blocking: bool = True
    answers: dict[str, Any] | None = None
    expiresAtMs: int | None = None
    schemaVersion: int = 1
    createdAtMs: int = field(default_factory=_now_ms)
    resolvedAtMs: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "inputRequestId": self.id,
            "sessionId": self.sessionId,
            "invocationId": self.invocationId,
            "turnId": self.turnId,
            "status": self.status,
            "questions": [dict(question) for question in self.questions],
            "blocking": self.blocking,
            "expiresAtMs": self.expiresAtMs,
            "createdAtMs": self.createdAtMs,
            "resolvedAtMs": self.resolvedAtMs,
        }


@dataclass
class CanonicalEventRecord:
    eventId: str
    invocationId: str | None
    sessionId: str
    turnId: str | None
    author: str
    sequenceNumber: int
    content: dict[str, Any]
    actions: dict[str, Any]
    type: str = "haas.adapter.event_unparsed"
    haas: dict[str, Any] = field(default_factory=dict)
    schemaVersion: int = 2
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
    accepted: bool = False
    invocationId: str | None = None
    acceptedAtMs: int | None = None
    completedAtMs: int | None = None
    expiresAtMs: int | None = None
    tombstone: bool = False
    schemaVersion: int = 1
    createdAtMs: int = field(default_factory=_now_ms)


@dataclass
class IdempotencyReservation:
    keyHash: str
    replay: bool
    result: Any | None


class IdempotencyConflictError(Exception):
    """Same idempotency key, different request hash: fail closed (409)."""


class IdempotencyExpiredError(Exception):
    """A completed execution key expired and remains reserved as a tombstone."""

    def __init__(self, key_hash: str, invocation_id: str | None, expires_at_ms: int) -> None:
        super().__init__(key_hash)
        self.key_hash = key_hash
        self.invocation_id = invocation_id
        self.expires_at_ms = expires_at_ms


class CursorNotFoundError(Exception):
    """SSE replay cursor is unknown -> 410 haas_offset_expired."""


class DelegatedSessionNotFoundError(Exception):
    """Delegated session id is unknown -> 404 haas_delegated_session_not_found."""


class ApprovalNotFoundError(Exception):
    """Approval id is unknown -> 404 haas_approval_not_found."""


class ApprovalStateConflictError(Exception):
    """Approval was already resolved -> 409 haas_approval_state_conflict."""


class InputRequestNotFoundError(Exception):
    """Input request id is unknown."""


class InputRequestStateConflictError(Exception):
    """Input request is resolved or cannot resume this adapter generation."""


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

    def __init__(
        self,
        *,
        clock_ms: Callable[[], int] = _now_ms,
        idempotency_ttl_ms: int = 86_400_000,
    ) -> None:
        self._clock_ms = clock_ms
        self._idempotency_ttl_ms = idempotency_ttl_ms
        self._harnesses: dict[str, HarnessRecord] = {}
        self._profiles: dict[str, ProfileRecord] = {}
        self._sessions: dict[SessionKey, SessionRecord] = {}
        self._invocations: dict[str, InvocationRecord] = {}
        self._turns: dict[str, TurnRecord] = {}
        self._events_by_invocation: dict[InvocationEventKey, list[CanonicalEventRecord]] = {}
        self._events_by_session: dict[SessionKey, list[CanonicalEventRecord]] = {}
        self._next_event_number = 0
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
        self._input_requests: dict[str, InputRequestRecord] = {}

    @contextlib.contextmanager
    def transaction(self) -> Iterator[None]:
        """No-op transaction boundary for the in-memory backend.

        The durable SQLite backend overrides this with a real ``BEGIN
        IMMEDIATE``/``COMMIT``/``ROLLBACK`` so multi-record writes (e.g. terminal
        persistence) are atomic. Memory writes are already atomic with respect to
        the process, so this is just a reentrancy-safe context.
        """
        yield None

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

    # --- ProfileStore ---------------------------------------------------

    def save_profile(self, profile: ProfileRecord) -> ProfileRecord:
        existing = self._profiles.get(profile.id)
        created_at = existing.createdAtMs if existing is not None else profile.createdAtMs
        record = replace(profile, createdAtMs=created_at, updatedAtMs=self._clock_ms())
        self._profiles[record.id] = record
        return record

    def get_profile(self, profile_id: str) -> ProfileRecord | None:
        return self._profiles.get(profile_id)

    def list_profiles(
        self,
        account: AccountKey | None = None,
        *,
        harness_id: str | None = None,
        status: str | None = None,
    ) -> list[ProfileRecord]:
        records = list(self._profiles.values())
        if account is not None:
            records = [record for record in records if record.account_key() == account]
        if harness_id is not None:
            records = [record for record in records if record.harnessId == harness_id]
        if status is not None:
            records = [record for record in records if record.status == status]
        records.sort(key=lambda record: (record.harnessId, record.version), reverse=True)
        return records

    # --- SessionStore ---------------------------------------------------

    def get_session(self, key: SessionKey) -> SessionRecord | None:
        return self._sessions.get(key)

    def put_session(self, session: SessionRecord) -> SessionRecord:
        record = replace(session, updatedAtMs=_now_ms())
        self._sessions[(record.appName, record.userId, record.id)] = record
        return record

    def delete_session(self, key: SessionKey) -> None:
        removed = self._sessions.pop(key, None)
        if removed is None:
            return
        session_id = key[2]
        # Cascade every record owned by the session so deletion cannot leave
        # orphaned events, invocations, turns, interactions, or idempotency rows
        # resident in memory (P1-2 / S2-001).
        invocation_ids = {
            inv.id for inv in self._invocations.values() if inv.sessionId == session_id
        }
        turn_ids = {
            turn.id for turn in self._turns.values() if turn.sessionId == session_id
        }
        self._events_by_session.pop(key, None)
        for invocation_id in invocation_ids:
            self._events_by_invocation.pop((*key, invocation_id), None)
        for invocation_id in invocation_ids:
            self._invocations.pop(invocation_id, None)
        for turn_id in turn_ids:
            self._turns.pop(turn_id, None)
        for approval_id in [
            record.id for record in self._approvals.values() if record.sessionId == session_id
        ]:
            self._approvals.pop(approval_id, None)
        for request_id in [
            record.id
            for record in self._input_requests.values()
            if record.sessionId == session_id
        ]:
            self._input_requests.pop(request_id, None)
        for key_hash, record in list(self._idempotency.items()):
            if record.invocationId in invocation_ids:
                self._idempotency.pop(key_hash, None)

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
        if invocation.status in {"failed", "incomplete", "interrupted", "cancelled"}:
            self._close_pending_interactions_in_memory(
                invocation.id, resolved_at_ms=invocation.completedAtMs or self._clock_ms()
            )
        return invocation

    def put_turn(self, turn: TurnRecord) -> TurnRecord:
        self._turns[turn.id] = turn
        return turn

    def get_invocation(self, invocation_id: str) -> InvocationRecord | None:
        return self._invocations.get(invocation_id)

    def get_turn(self, turn_id: str) -> TurnRecord | None:
        return self._turns.get(turn_id)

    # --- DelegationStore -----------------------------------------------

    def put_delegated_session(self, record: DelegatedSessionRecord) -> DelegatedSessionRecord:
        existing = self._delegated_sessions.get(record.id)
        created_at = existing.createdAtMs if existing is not None else record.createdAtMs
        saved = replace(record, createdAtMs=created_at, updatedAtMs=_now_ms())
        self._delegated_sessions[saved.id] = saved
        self._delegated_by_manager_session[saved.managerSessionId] = saved.id
        self._delegated_by_haas_session[saved.haasSessionId] = saved.id
        return saved

    def get_delegated_session(self, delegated_session_id: str) -> DelegatedSessionRecord | None:
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
        if existing is not None and existing.nativeRequestId is not None:
            if (
                existing.nativeRequestId == approval.nativeRequestId
                and existing.adapterGeneration == approval.adapterGeneration
                and existing.invocationId == approval.invocationId
            ):
                return existing
            raise ApprovalStateConflictError(approval.id)
        created_at = existing.createdAtMs if existing is not None else approval.createdAtMs
        record = replace(approval, createdAtMs=created_at)
        self._approvals[record.id] = record
        return record

    def get_approval(self, approval_id: str) -> ApprovalRecord | None:
        return self._approvals.get(approval_id)

    def list_approvals(self, session_id: str, *, status: str | None = None) -> list[ApprovalRecord]:
        records = [record for record in self._approvals.values() if record.sessionId == session_id]
        if status is not None:
            records = [record for record in records if record.status == status]
        return sorted(records, key=lambda record: (record.createdAtMs, record.id))

    def resolve_approval(self, approval_id: str, decision: dict[str, Any]) -> ApprovalRecord:
        record = self.get_approval(approval_id)
        if record is None:
            raise ApprovalNotFoundError(approval_id)
        if record.status != "waiting":
            if record.status == decision.get("decision") and record.decision == decision:
                return record
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

    def close_pending_interactions(
        self, invocation_id: str, *, resolved_at_ms: int | None = None
    ) -> tuple[list[ApprovalRecord], list[InputRequestRecord]]:
        return self._close_pending_interactions_in_memory(
            invocation_id,
            resolved_at_ms=self._clock_ms() if resolved_at_ms is None else resolved_at_ms,
        )

    def _close_pending_interactions_in_memory(
        self, invocation_id: str, *, resolved_at_ms: int
    ) -> tuple[list[ApprovalRecord], list[InputRequestRecord]]:
        approvals: list[ApprovalRecord] = []
        inputs: list[InputRequestRecord] = []
        for approval_id, approval in list(self._approvals.items()):
            if approval.invocationId == invocation_id and approval.status == "waiting":
                closed_approval = replace(
                    approval, status="cancelled", resolvedAtMs=resolved_at_ms
                )
                self._approvals[approval_id] = closed_approval
                approvals.append(closed_approval)
        for request_id, request in list(self._input_requests.items()):
            if request.invocationId == invocation_id and request.status == "waiting":
                closed_request = replace(
                    request, status="cancelled", resolvedAtMs=resolved_at_ms
                )
                self._input_requests[request_id] = closed_request
                inputs.append(closed_request)
        return approvals, inputs

    def reconcile_pending_interactions(self, session_id: str) -> None:
        invocation_ids = {
            record.invocationId
            for records in (self._approvals.values(), self._input_requests.values())
            for record in records
            if record.sessionId == session_id and record.status == "waiting"
        }
        for invocation_id in invocation_ids:
            invocation = self.get_invocation(invocation_id)
            if invocation is not None and invocation.status in {
                "failed",
                "incomplete",
                "interrupted",
                "cancelled",
            }:
                self.close_pending_interactions(
                    invocation_id,
                    resolved_at_ms=invocation.completedAtMs or self._clock_ms(),
                )

    def put_input_request(self, request: InputRequestRecord) -> InputRequestRecord:
        existing = self._input_requests.get(request.id)
        if existing is not None:
            if existing == request:
                return existing
            raise InputRequestStateConflictError(request.id)
        self._input_requests[request.id] = request
        return request

    def get_input_request(self, request_id: str) -> InputRequestRecord | None:
        return self._input_requests.get(request_id)

    def list_input_requests(
        self, session_id: str, *, status: str | None = None
    ) -> list[InputRequestRecord]:
        records = [
            record for record in self._input_requests.values() if record.sessionId == session_id
        ]
        if status is not None:
            records = [record for record in records if record.status == status]
        return sorted(records, key=lambda record: (record.createdAtMs, record.id))

    def resolve_input_request(self, request_id: str, answers: dict[str, Any]) -> InputRequestRecord:
        record = self.get_input_request(request_id)
        if record is None:
            raise InputRequestNotFoundError(request_id)
        if record.status != "waiting":
            if record.status == "answered" and record.answers == answers:
                return record
            raise InputRequestStateConflictError(request_id)
        resolved = replace(
            record, status="answered", answers=answers, resolvedAtMs=self._clock_ms()
        )
        self._input_requests[request_id] = resolved
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

    def release_workspace_lock(self, canonical_workspace: str, delegated_session_id: str) -> None:
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

    def renew_lease(self, key: SessionKey, holder: str, token: int, ttl_ms: int = 30_000) -> Lease:
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

    def next_event_id(self) -> str:
        event_id = f"evt_{self._next_event_number:013d}"
        self._next_event_number += 1
        return event_id

    def append(self, event: CanonicalEventRecord) -> None:
        match = re.fullmatch(r"evt_(\d+)", event.eventId)
        if match is not None:
            self._next_event_number = max(self._next_event_number, int(match.group(1)) + 1)
        session_key = (event.appName, event.userId, event.sessionId)
        if event.invocationId is not None:
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
            e for e in sorted(events, key=lambda e: e.sequenceNumber) if e.sequenceNumber > after
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
            self._expire_idempotency_result(existing)
            if existing.tombstone:
                assert existing.expiresAtMs is not None
                raise IdempotencyExpiredError(key_hash, existing.invocationId, existing.expiresAtMs)
            if existing.requestHash != request_hash:
                raise IdempotencyConflictError(key_hash)
            return IdempotencyReservation(keyHash=key_hash, replay=True, result=existing.result)
        # Fresh slot: lazily garbage-collect long-expired tombstones so the
        # idempotency dict cannot grow unboundedly over a long-running sidecar
        # (P1-2 / S2-010). The tombstone protecting `key_hash` itself is handled
        # by the branch above, so sweeping here cannot reject the current key.
        self.sweep_expired()
        record = IdempotencyRecord(
            keyHash=key_hash, requestHash=request_hash, createdAtMs=self._clock_ms()
        )
        self._idempotency[key_hash] = record
        return IdempotencyReservation(keyHash=key_hash, replay=False, result=None)

    def accept(
        self, key_hash: str, invocation_id: str, *, accepted_at_ms: int | None = None
    ) -> IdempotencyRecord:
        record = self._idempotency.get(key_hash)
        if record is None or record.released:
            raise KeyError(key_hash)
        accepted_at = self._clock_ms() if accepted_at_ms is None else accepted_at_ms
        record.accepted = True
        record.invocationId = invocation_id
        record.acceptedAtMs = accepted_at
        record.expiresAtMs = accepted_at + self._idempotency_ttl_ms
        return record

    def complete(self, key_hash: str, result: Any, *, completed_at_ms: int | None = None) -> None:
        record = self._idempotency.get(key_hash)
        if record is not None:
            record.result = result
            record.completedAtMs = self._clock_ms() if completed_at_ms is None else completed_at_ms

    def replay(self, key_hash: str) -> Any | None:
        record = self._idempotency.get(key_hash)
        if record is None or record.released:
            return None
        self._expire_idempotency_result(record)
        if record.tombstone:
            return None
        return record.result

    def is_pending(self, key_hash: str) -> bool:
        """True while a reservation is in-flight (not completed, not released)."""
        record = self._idempotency.get(key_hash)
        return (
            record is not None
            and not record.released
            and not record.tombstone
            and record.result is None
        )

    def release(self, key_hash: str) -> None:
        record = self._idempotency.get(key_hash)
        if record is not None:
            record.released = True

    def _expire_idempotency_result(self, record: IdempotencyRecord) -> None:
        if (
            record.accepted
            and record.result is not None
            and record.expiresAtMs is not None
            and self._clock_ms() >= record.expiresAtMs
        ):
            record.result = None
            record.tombstone = True

    def sweep_expired(self, now_ms: int | None = None) -> int:
        """Remove tombstones whose TTL has fully elapsed. Returns the count removed.

        A tombstone marks a completed execution whose cached result has already
        been evicted. Once its ``expiresAtMs`` is in the past it no longer needs
        to be retained (a retry may claim a fresh slot); sweeping bounds the
        in-memory idempotency table for long-running deployments.
        """
        now = self._clock_ms() if now_ms is None else now_ms
        removed = 0
        for key_hash, record in list(self._idempotency.items()):
            if (
                record.tombstone
                and record.expiresAtMs is not None
                and now >= record.expiresAtMs
            ):
                del self._idempotency[key_hash]
                removed += 1
        return removed
