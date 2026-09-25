"""Session Runtime: session/invocation/turn lifecycle (specs/session-runtime/)."""

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from copy import deepcopy
from dataclasses import dataclass, field
from math import isfinite
from typing import Any, TypeVar

from haas.artifacts import ArtifactStore
from haas.events import EventLog
from haas.harnesses import (
    CancelTurnRequest,
    HarnessAdapter,
    HarnessEvent,
    ListArtifactsRequest,
    PrepareSessionRequest,
    ResumeSessionRequest,
    StartTurnRequest,
)
from haas.harnesses.base import AdapterTurnStartError
from haas.registry import HarnessRegistry
from haas.stores import (
    CanonicalEventRecord,
    HarnessRecord,
    InvocationRecord,
    LeaseConflictError,
    LeaseFencingError,
    MemoryStore,
    SessionRecord,
    TurnRecord,
)


class SessionNotFoundError(Exception):
    """session key not found -> 404 session_not_found."""


class SessionBusyError(Exception):
    """concurrent run on the same session -> 409 session_busy."""


class InvocationNotFoundError(Exception):
    """invocation id not found -> 404 haas_invocation_not_found."""


class InvocationNotRunningError(Exception):
    """invocation cannot be paused -> 409 haas_invocation_not_running."""


class InvocationNotResumableError(Exception):
    """invocation cannot be continued -> 409 haas_invocation_not_resumable."""


class ResumeRequiredError(Exception):
    """ordinary run cannot replace explicit continue -> 409 haas_resume_required."""


class PolicyRevisionConflictError(Exception):
    """policy mutation expected revision does not match desired revision."""


class PolicyUpdateInvalidError(Exception):
    """policy mutation contains an incomplete or invalid security domain."""


class AdapterTurnError(Exception):
    """Harness adapter raised during a turn -> 502 haas_adapter_error."""

    def __init__(self, invocation_id: str) -> None:
        super().__init__(invocation_id)
        self.invocation_id = invocation_id


class AdapterTurnTimeoutError(AdapterTurnError):
    """Harness adapter exceeded the runtime turn timeout -> 502 haas_adapter_error."""


def _deep_merge(base: dict[str, Any], delta: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in delta.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


@dataclass
class RunRequest:
    app: HarnessRecord
    user_id: str
    message: dict[str, Any]
    session_id: str | None = None
    timeout_seconds: float | None = None
    sandbox: dict[str, Any] = field(default_factory=dict)
    policy: dict[str, Any] = field(default_factory=dict)
    effective_profile: dict[str, Any] | None = None
    principal_id: str = ""
    continued_from_invocation_id: str | None = None
    continued_from_turn_id: str | None = None


@dataclass
class RunResult:
    session: SessionRecord
    invocation: InvocationRecord
    events: list[CanonicalEventRecord]


@dataclass
class _ActiveTurn:
    turn_id: str
    session_id: str
    terminal_status: asyncio.Future[str]
    control_intent: str | None = None
    interrupt_dispatched: bool = False
    control_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


_T = TypeVar("_T")


@dataclass
class SessionRuntime:
    store: MemoryStore
    registry: HarnessRegistry
    adapter: HarnessAdapter
    event_log: EventLog
    lease_ttl_ms: int = 30_000
    lease_renew_interval_ms: int = 10_000
    turn_timeout_s: float = 86_400
    _active: dict[str, _ActiveTurn] = field(default_factory=dict)
    model_proxy: Any = None
    artifacts: ArtifactStore | None = None

    def __post_init__(self) -> None:
        if self.lease_ttl_ms <= 0:
            raise ValueError("lease_ttl_ms must be positive")
        if self.lease_renew_interval_ms <= 0:
            raise ValueError("lease_renew_interval_ms must be positive")
        self.turn_timeout_s = self._effective_timeout_seconds(self.turn_timeout_s, 86_400)
        if self.lease_renew_interval_ms * 2 >= self.lease_ttl_ms:
            raise ValueError("lease_renew_interval_ms must be less than half of lease_ttl_ms")
        if self.turn_timeout_s <= self.lease_ttl_ms / 1000:
            raise ValueError("turn_timeout_s must be greater than lease_ttl_ms")

    @staticmethod
    def _effective_timeout_seconds(value: float | None, default: float) -> float:
        if value is None:
            return default
        if not isfinite(value):
            raise ValueError("timeout_seconds must be finite")
        if value <= 0:
            raise ValueError("timeout_seconds must be positive")
        return min(value, 86_400)

    def _key(self, app_name: str, user_id: str, session_id: str) -> tuple[str, str, str]:
        return (app_name, user_id, session_id)

    def get_session(self, app_name: str, user_id: str, session_id: str) -> SessionRecord:
        session = self.store.get_session(self._key(app_name, user_id, session_id))
        if session is None:
            raise SessionNotFoundError(session_id)
        return session

    def apply_state_delta(
        self, app_name: str, user_id: str, session_id: str, delta: dict[str, Any]
    ) -> SessionRecord:
        session = self.get_session(app_name, user_id, session_id)
        session.state = _deep_merge(session.state, delta)
        return self.store.put_session(session)

    def delete_session(self, app_name: str, user_id: str, session_id: str) -> None:
        key = self._key(app_name, user_id, session_id)
        self.get_session(app_name, user_id, session_id)
        self.store.delete_session(key)
        if self.artifacts is not None:
            self.artifacts.delete_session(
                session_id, app_name=app_name, user_id=user_id
            )
        if self.model_proxy is not None and hasattr(self.model_proxy, "revoke_session"):
            self.model_proxy.revoke_session(session_id)

    def update_policy(
        self, session: SessionRecord, *, expected_revision: int, delta: dict[str, Any]
    ) -> SessionRecord:
        if expected_revision != session.desiredRevision:
            raise PolicyRevisionConflictError(session.id)
        if not delta or not set(delta).issubset({"workspace", "network", "tools"}):
            raise PolicyUpdateInvalidError(session.id)
        candidate = deepcopy(session.desiredPolicy)
        for domain, value in delta.items():
            self._validate_policy_domain(domain, value)
            candidate[domain] = deepcopy(value)
        revision = session.desiredRevision + 1
        session.desiredRevision = revision
        session.desiredPolicy = candidate
        session.pendingPolicyUpdate = {
            "revision": revision,
            "fields": sorted(delta),
            "requestedAtMs": int(time.time() * 1000),
        }
        if session.controlState in {"running", "pausing", "cancelling", "resuming"}:
            session.policyStatus = "pending"
        else:
            self._apply_desired_policy(session)
        return self.store.put_session(session)

    @staticmethod
    def _validate_policy_domain(domain: str, value: Any) -> None:
        if not isinstance(value, dict):
            raise PolicyUpdateInvalidError(domain)
        if domain == "workspace":
            if (
                set(value) != {"mode", "root", "writableRoots"}
                or value.get("mode") not in {"read-only", "workspace-write", "danger-full-access"}
                or not isinstance(value.get("root"), str)
                or not value["root"].startswith("/")
                or not isinstance(value.get("writableRoots"), list)
                or not all(
                    isinstance(item, str) and item.startswith("/")
                    for item in value["writableRoots"]
                )
            ):
                raise PolicyUpdateInvalidError(domain)
        elif domain == "network":
            if (
                set(value) != {"defaultAction", "allow"}
                or value.get("defaultAction") not in {"deny", "allow"}
                or not isinstance(value.get("allow"), list)
                or not all(isinstance(item, str) for item in value["allow"])
            ):
                raise PolicyUpdateInvalidError(domain)
        elif domain == "tools" and (
            set(value) != {"disabled", "approvalMode"}
            or value.get("approvalMode") not in {"never", "on-request", "always"}
            or not isinstance(value.get("disabled"), list)
            or not all(isinstance(item, str) for item in value["disabled"])
        ):
            raise PolicyUpdateInvalidError(domain)

    @staticmethod
    def _apply_desired_policy(session: SessionRecord) -> None:
        session.appliedPolicy = deepcopy(session.desiredPolicy)
        session.appliedRevision = session.desiredRevision
        session.policyStatus = "applied"
        session.lastPolicyUpdateResult = {
            "revision": session.appliedRevision,
            "status": "applied",
        }
        session.pendingPolicyUpdate = None

    def _refresh_policy_state(
        self, session: SessionRecord, key: tuple[str, str, str]
    ) -> SessionRecord:
        current = self.store.get_session(key)
        if current is None or current is session:
            return session
        session.desiredPolicy = deepcopy(current.desiredPolicy)
        session.appliedPolicy = deepcopy(current.appliedPolicy)
        session.desiredRevision = current.desiredRevision
        session.appliedRevision = current.appliedRevision
        session.policyStatus = current.policyStatus
        session.pendingPolicyUpdate = deepcopy(current.pendingPolicyUpdate)
        session.lastPolicyUpdateResult = deepcopy(current.lastPolicyUpdateResult)
        return session

    @classmethod
    def _initial_policy(cls, req: RunRequest) -> dict[str, Any]:
        policy = SessionRecord(id="", appName="", userId="").appliedPolicy
        sandbox = req.sandbox
        workspace = {
            "mode": sandbox.get("mode", policy["workspace"]["mode"]),
            "root": sandbox.get("workspaceRoot", policy["workspace"]["root"]),
            "writableRoots": sandbox.get(
                "writableRoots", policy["workspace"]["writableRoots"]
            ),
        }
        network = req.policy.get("network", sandbox.get("network", policy["network"]))
        tools = req.policy.get("tools", policy["tools"])
        legacy_approval = req.policy.get("approvalPolicy")
        if legacy_approval in {"never", "on-request", "always"}:
            tools = {**tools, "approvalMode": legacy_approval}
        candidate = {"workspace": workspace, "network": network, "tools": tools}
        for domain, value in candidate.items():
            cls._validate_policy_domain(domain, value)
        return deepcopy(candidate)

    async def run(self, req: RunRequest) -> RunResult:
        events = [event async for event in self._drive(req)]
        if not events:
            raise RuntimeError(
                "haas_empty_events: turn completed without producing any canonical event"
            )
        session = self.get_session(req.app.id, req.user_id, req.session_id or events[0].sessionId)
        invocation_id = events[-1].invocationId
        if invocation_id is None:
            raise RuntimeError("run completed without an invocation-scoped event")
        return RunResult(
            session=session,
            invocation=self._read_invocation(invocation_id),
            events=events,
        )

    def run_stream(self, req: RunRequest) -> AsyncIterator[CanonicalEventRecord]:
        return self._drive(req)

    async def _drive(self, req: RunRequest) -> AsyncIterator[CanonicalEventRecord]:
        app = req.app
        session_id = req.session_id or f"hsess_{uuid.uuid4().hex[:16]}"
        key = self._key(app.id, req.user_id, session_id)
        holder = f"run_{uuid.uuid4().hex[:8]}"

        try:
            lease = self.store.acquire_lease(key, holder, ttl_ms=self.lease_ttl_ms)
        except LeaseConflictError as exc:
            raise SessionBusyError(session_id) from exc
        token = lease.token

        stored_session = self.store.get_session(key)
        if stored_session is None:
            session = SessionRecord(id=session_id, appName=app.id, userId=req.user_id)
            initial_policy = self._initial_policy(req)
            session.desiredPolicy = deepcopy(initial_policy)
            session.appliedPolicy = deepcopy(initial_policy)
        else:
            session = stored_session
            if session.appliedRevision < session.desiredRevision:
                self.store.release_lease(key, holder, token)
                raise SessionBusyError("configuration_update_pending")
            if session.controlState == "paused" and req.continued_from_invocation_id is None:
                self.store.release_lease(key, holder, token)
                raise ResumeRequiredError(session_id)
        if req.continued_from_invocation_id is not None:
            if (
                session.controlState != "paused"
                or not session.supportsResume
                or session.resumableInvocationId != req.continued_from_invocation_id
            ):
                self.store.release_lease(key, holder, token)
                raise InvocationNotResumableError(req.continued_from_invocation_id)
            session.controlState = "resuming"
        else:
            session.controlState = "running"
            session.supportsResume = False
            session.resumableInvocationId = None
        if session.effectiveProfile is None and req.effective_profile is not None:
            session.effectiveProfile = req.effective_profile
        session = self.store.put_session(session)
        effective = session.effectiveProfile or {}

        # Request-level policy and sandbox fields materialize revision 1 only. Once
        # the session exists, the revisioned policy endpoint is the sole mutation
        # path; merging later run bodies here would bypass its authorization and
        # desired/applied barrier.
        effective_policy = deepcopy(session.appliedPolicy)
        effective_sandbox = {
            "mode": effective_policy["workspace"]["mode"],
            "workspaceRoot": effective_policy["workspace"]["root"],
            "writableRoots": effective_policy["workspace"]["writableRoots"],
            "network": effective_policy["network"],
        }
        adapter_policy = deepcopy(effective_policy)
        adapter_policy["approvalPolicy"] = effective_policy["tools"]["approvalMode"]
        invocation = InvocationRecord(
            id=f"inv_{uuid.uuid4().hex[:16]}",
            sessionId=session_id,
            appName=app.id,
            userId=req.user_id,
            turnId=f"turn_{uuid.uuid4().hex[:16]}",
            status="running",
            continuedFromInvocationId=req.continued_from_invocation_id,
            continuedFromTurnId=req.continued_from_turn_id,
            executionContext=deepcopy(
                {
                    "sandbox": effective_sandbox,
                    "policy": adapter_policy,
                    "policyRevision": session.appliedRevision,
                    "principalId": req.principal_id,
                }
            ),
        )
        turn = TurnRecord(
            id=invocation.turnId,
            invocationId=invocation.id,
            sessionId=session_id,
            status="running",
            continuedFromInvocationId=req.continued_from_invocation_id,
            continuedFromTurnId=req.continued_from_turn_id,
        )
        self.store.put_invocation(invocation)
        self.store.put_turn(turn)

        renew_task = asyncio.create_task(self._renew_lease_until_done(key, holder, token))
        completed_cleanly = False
        streamed_terminal: CanonicalEventRecord | None = None
        credentials: dict[str, str] = {}
        timeout_seconds = self._effective_timeout_seconds(
            req.timeout_seconds, self.turn_timeout_s
        )
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        invocation.timeoutSeconds = timeout_seconds
        invocation.deadlineAtMs = invocation.startedAtMs + int(timeout_seconds * 1000)
        self.store.put_invocation(invocation)
        try:
            async with asyncio.timeout_at(deadline):
                if self.model_proxy is not None:
                    credentials = await self.model_proxy.begin(app, invocation, effective)
                if (
                    req.continued_from_invocation_id is None
                    and self._has_native_resume_ref(session.nativeSessionRef)
                ):
                    prepared = await self._guarded_adapter_call(
                        key,
                        holder,
                        token,
                        lambda: self.adapter.resume_session(
                            ResumeSessionRequest(
                                sessionId=session_id,
                                opaque=deepcopy(session.nativeSessionRef or {}),
                                appName=app.id,
                                userId=req.user_id,
                            )
                        ),
                    )
                elif req.continued_from_invocation_id is None:
                    prepared = await self._guarded_adapter_call(
                        key,
                        holder,
                        token,
                        lambda: self.adapter.prepare_session(
                            PrepareSessionRequest(sessionId=session_id, appName=app.id)
                        ),
                    )
                else:
                    prepared = await self._guarded_adapter_call(
                        key,
                        holder,
                        token,
                        lambda: self.adapter.resume_session(
                            ResumeSessionRequest(
                                sessionId=session_id,
                                opaque=deepcopy(session.nativeSessionRef or {}),
                                appName=app.id,
                                userId=req.user_id,
                            )
                        ),
                    )
                session = self._store_native_session_ref(
                    session, prepared.nativeRef, key, holder, token
                )
                handle = await self._guarded_adapter_call(
                    key,
                    holder,
                    token,
                    lambda: self.adapter.start_turn(
                        StartTurnRequest(
                            invocationId=invocation.id,
                            sessionId=session_id,
                            turnId=turn.id,
                            appName=app.id,
                            input=[req.message],
                            model=effective.get("provider", {}).get("model") or app.defaultModel,
                            instructions=app.systemPrompt or None,
                            timeoutSeconds=timeout_seconds,
                            sandbox=deepcopy(effective_sandbox),
                            policy=deepcopy(adapter_policy),
                            credentials=credentials,
                            mcpServers=deepcopy(effective.get("mcpServers") or []),
                            principalId=req.principal_id,
                            userId=req.user_id,
                        )
                    ),
                )
                invocation.nativeTurnRef = deepcopy(handle.opaque)
                turn.nativeTurnRef = deepcopy(handle.opaque)
                thread_id = (
                    handle.opaque.get("threadId") if isinstance(handle.opaque, dict) else None
                )
                if isinstance(thread_id, str) and thread_id:
                    prior_native_ref = deepcopy(session.nativeSessionRef) or {}
                    prior_native_ref.pop("nonResumable", None)
                    native_ref = {
                        **prior_native_ref,
                        "threadId": thread_id,
                        "adapterId": self.adapter.adapter_id,
                    }
                    session = self._store_native_session_ref(
                        session, native_ref, key, holder, token
                    )
                self.store.put_invocation(invocation)
                self.store.put_turn(turn)
            self._active[invocation.id] = _ActiveTurn(
                turn_id=turn.id,
                session_id=session_id,
                terminal_status=asyncio.get_running_loop().create_future(),
            )

            # Each timeout belongs to the task advancing this generator. Never
            # keep a task-bound timeout open across yield (HTTP -> SSE producer).
            adapter_events = self.adapter.stream_events(handle)
            while True:
                try:
                    async with asyncio.timeout_at(deadline):
                        harness_event = await anext(adapter_events)
                except StopAsyncIteration:
                    break
                self.store.assert_lease(key, holder, token)
                status = harness_event.type.removeprefix("harness.turn.")
                if harness_event.type.startswith("harness.turn.") and status in {
                    "completed",
                    "failed",
                    "incomplete",
                    "interrupted",
                    "cancelled",
                }:
                    # Native transports may replay a terminal while closing. The
                    # canonical invocation contract permits exactly one terminal.
                    if streamed_terminal is not None:
                        continue
                    artifact_events = await self._publish_turn_artifacts(
                        app, invocation, turn, key, holder, token
                    )
                    active = self._active.get(invocation.id)
                    if (
                        status == "interrupted"
                        and active is not None
                        and active.control_intent == "cancel"
                    ):
                        status = "cancelled"
                        harness_event = self._terminal_event(status, app, invocation, turn)
                    event, session = self._persist_terminal_event(
                        harness_event,
                        status,
                        app,
                        invocation,
                        turn,
                        session,
                        key,
                        holder,
                        token,
                    )
                    streamed_terminal = event
                    for artifact_event in artifact_events:
                        yield artifact_event
                else:
                    event = self._append_harness_event(
                        harness_event, app, invocation, turn, key, holder, token
                    )
                    session = self._merge_actions(session, event, key, holder, token)
                yield event

            async with asyncio.timeout_at(deadline):
                result = await self._guarded_adapter_call(
                    key, holder, token, lambda: self.adapter.finalize_turn(handle)
                )
            if streamed_terminal is None:
                terminal = result.terminalEvent or self._terminal_event(
                    result.status, app, invocation, turn
                )
                artifact_events = await self._publish_turn_artifacts(
                    app, invocation, turn, key, holder, token
                )
                event, session = self._persist_terminal_event(
                    terminal,
                    result.status,
                    app,
                    invocation,
                    turn,
                    session,
                    key,
                    holder,
                    token,
                )
                for artifact_event in artifact_events:
                    yield artifact_event
                yield event
            completed_cleanly = True
        except TimeoutError as exc:
            if streamed_terminal is not None:
                # Cleanup/finalization cannot rewrite an already committed
                # authoritative terminal. The caller has already received it.
                completed_cleanly = True
            else:
                event = self._persist_failure_terminal(
                    app,
                    invocation,
                    turn,
                    session,
                    key,
                    holder,
                    token,
                    reason="long_task_deadline_exceeded",
                    code="haas_request_timeout",
                    retryable=True,
                )
                session = self.get_session(app.id, req.user_id, session_id)
                yield event
                raise AdapterTurnTimeoutError(invocation.id) from exc
        except AdapterTurnStartError as exc:
            if streamed_terminal is not None:
                completed_cleanly = True
            else:
                # Structured start-turn failure (P0-4 / S3-001): surface the
                # adapter-supplied code and retryability instead of a bare failed.
                event = self._persist_failure_terminal(
                    app,
                    invocation,
                    turn,
                    session,
                    key,
                    holder,
                    token,
                    reason=exc.detail or "adapter_turn_start_failed",
                    code=exc.code,
                    retryable=exc.retryable,
                )
                session = self.get_session(app.id, req.user_id, session_id)
                yield event
                raise AdapterTurnError(invocation.id) from exc
        except LeaseFencingError as exc:
            raise AdapterTurnError(invocation.id) from exc
        except Exception as exc:
            if streamed_terminal is not None:
                completed_cleanly = True
            else:
                # Residual start-path failures (adapter RPC errors not wrapped
                # upstream, model-proxy/provider config errors, unexpected bugs).
                # Never emit the legacy bare code="failed": surface a stable code
                # and a safe reason (exception class name). The raw exception
                # message may contain prompts/params/credentials, so it is not
                # propagated to the wire.
                code = getattr(exc, "code", None)
                if not isinstance(code, str) or not code:
                    if type(exc).__name__ in {
                        "ModelRouteError",
                        "SecretResolutionError",
                        "RuntimeTokenError",
                    }:
                        code = "haas_provider_error"
                    else:
                        code = "haas_adapter_error"
                event = self._persist_failure_terminal(
                    app,
                    invocation,
                    turn,
                    session,
                    key,
                    holder,
                    token,
                    reason=type(exc).__name__,
                    code=code,
                    retryable=False,
                )
                session = self.get_session(app.id, req.user_id, session_id)
                yield event
                raise AdapterTurnError(invocation.id) from exc
        finally:
            if self.model_proxy is not None:
                self.model_proxy.end(invocation, credentials)
            active = self._active.pop(invocation.id, None)
            if active is not None and not active.terminal_status.done():
                # Wake Pause callers even when the driving task is cancelled or
                # fenced before it can commit a canonical terminal. A plain
                # sentinel avoids an unobserved Future exception on non-Pause
                # paths; pause_invocation converts it to the stable adapter error.
                active.terminal_status.set_result("control_failed")
            renew_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, LeaseFencingError):
                await renew_task
            if not completed_cleanly and invocation.status == "running":
                invocation.status = "failed"
                turn.status = "failed"
                if session.controlState in {"running", "pausing", "cancelling", "resuming"}:
                    session.controlState = "idle"
                    session.supportsResume = False
                    session.resumableInvocationId = None
            with contextlib.suppress(LeaseFencingError):
                self.store.assert_lease(key, holder, token)
                self.store.put_invocation(invocation)
                self.store.put_turn(turn)
                session = self._refresh_policy_state(session, key)
                self.store.put_session(session)
            self.store.release_lease(key, holder, token)

    async def _guarded_adapter_call(
        self,
        key: tuple[str, str, str],
        holder: str,
        token: int,
        call: Callable[[], Awaitable[_T]],
    ) -> _T:
        self.store.assert_lease(key, holder, token)
        result = await call()
        self.store.assert_lease(key, holder, token)
        return result

    async def _renew_lease_until_done(
        self, key: tuple[str, str, str], holder: str, token: int
    ) -> None:
        interval_s = self.lease_renew_interval_ms / 1000
        while True:
            await asyncio.sleep(interval_s)
            self.store.renew_lease(key, holder, token, ttl_ms=self.lease_ttl_ms)

    def _append_harness_event(
        self,
        harness_event: Any,
        app: HarnessRecord,
        invocation: InvocationRecord,
        turn: TurnRecord,
        key: tuple[str, str, str],
        holder: str,
        token: int,
    ) -> CanonicalEventRecord:
        self.store.assert_lease(key, holder, token)
        return self.event_log.append_harness_event(
            harness_event,
            app_name=app.id,
            user_id=invocation.userId,
            harness_id=app.id,
            adapter_id=self.adapter.adapter_id,
        )

    def _terminal_event(
        self,
        status: str,
        app: HarnessRecord,
        invocation: InvocationRecord,
        turn: TurnRecord,
        *,
        reason: str | None = None,
        code: str | None = None,
        retryable: bool | None = None,
    ) -> Any:
        state_delta: dict[str, Any] = {"status": status}
        if reason is not None:
            state_delta["reason"] = reason
        if code is not None:
            state_delta["code"] = code
        if retryable is not None:
            state_delta["retryable"] = retryable
        return HarnessEvent(
            type=f"harness.turn.{status}",
            invocationId=invocation.id,
            sessionId=invocation.sessionId,
            turnId=turn.id,
            author=self.adapter.base,
            content={"role": "model", "parts": []},
            actions={"stateDelta": state_delta},
        )

    def reconcile_invocation_readback(
        self, session_id: str, invocation_id: str
    ) -> InvocationRecord:
        """Fail closed a durable running invocation whose process owner is gone."""
        invocation = self.store.get_invocation(invocation_id)
        if invocation is None or invocation.sessionId != session_id:
            raise InvocationNotFoundError(invocation_id)
        if invocation.status != "running":
            self._complete_idempotency_from_history(invocation)
            return invocation
        if invocation_id in self._active:
            return invocation

        key = self._key(invocation.appName, invocation.userId, session_id)
        holder = f"readback_{uuid.uuid4().hex[:8]}"
        try:
            lease = self.store.acquire_lease(key, holder, ttl_ms=self.lease_ttl_ms)
        except LeaseConflictError:
            return invocation

        try:
            invocation = self.store.get_invocation(invocation_id)
            if invocation is None or invocation.sessionId != session_id:
                raise InvocationNotFoundError(invocation_id)
            if invocation.status != "running":
                self._complete_idempotency_from_history(invocation)
                return invocation
            if invocation_id in self._active:
                return invocation

            turn = self.store.get_turn(invocation.turnId)
            session = self.store.get_session(key)
            app = self.registry.get(invocation.appName)
            if (
                turn is None
                or turn.invocationId != invocation.id
                or turn.sessionId != session_id
                or session is None
                or app is None
            ):
                raise InvocationNotFoundError(invocation_id)

            reason = "sidecar_restart_execution_lost"
            terminal = self._terminal_event(
                "incomplete",
                app,
                invocation,
                turn,
                reason=reason,
                code=reason,
                retryable=True,
            )
            session_was_superseded = session.state.get("status") in {
                "completed",
                "failed",
                "incomplete",
                "interrupted",
                "cancelled",
            }
            self._persist_terminal_event(
                terminal,
                "incomplete",
                app,
                invocation,
                turn,
                session,
                key,
                holder,
                lease.token,
                merge_session_state=not session_was_superseded,
            )
            self._complete_idempotency_from_history(invocation)
            return invocation
        finally:
            self.store.release_lease(key, holder, lease.token)

    def _complete_idempotency_from_history(self, invocation: InvocationRecord) -> None:
        key_hash = invocation.idempotencyKeyHash
        if key_hash is None or not self.store.is_pending(key_hash):
            return
        events = [
            self.event_log.project_adk(event)
            for event in self.event_log.read_invocation(
                invocation.appName,
                invocation.userId,
                invocation.sessionId,
                invocation.id,
            )
        ]
        self.store.complete(
            key_hash,
            {
                "events": events,
                "idempotency_expires_at_ms": invocation.idempotencyExpiresAtMs,
                "invocation_id": invocation.id,
                "session_id": invocation.sessionId,
            },
        )

    def _merge_actions(
        self,
        session: SessionRecord,
        event: CanonicalEventRecord,
        key: tuple[str, str, str],
        holder: str,
        token: int,
    ) -> SessionRecord:
        self.store.assert_lease(key, holder, token)
        delta = event.actions.get("stateDelta")
        if isinstance(delta, dict):
            session.state = _deep_merge(session.state, delta)
            session = self._refresh_policy_state(session, key)
            session = self.store.put_session(session)
        return session

    def _store_native_session_ref(
        self,
        session: SessionRecord,
        native_ref: dict[str, Any],
        key: tuple[str, str, str],
        holder: str,
        token: int,
    ) -> SessionRecord:
        if native_ref:
            self.store.assert_lease(key, holder, token)
            session.nativeSessionRef = deepcopy(native_ref)
            session = self._refresh_policy_state(session, key)
            session = self.store.put_session(session)
        return session

    @staticmethod
    def _has_native_resume_ref(native_ref: dict[str, Any] | None) -> bool:
        if not native_ref or native_ref.get("nonResumable") is True:
            return False
        return any(
            isinstance(native_ref.get(key), str) and native_ref[key]
            for key in ("threadId",)
        )

    def _persist_failure_terminal(
        self,
        app: HarnessRecord,
        invocation: InvocationRecord,
        turn: TurnRecord,
        session: SessionRecord,
        key: tuple[str, str, str],
        holder: str,
        token: int,
        *,
        reason: str | None = None,
        code: str | None = None,
        retryable: bool | None = None,
    ) -> CanonicalEventRecord:
        terminal = self._terminal_event(
            "failed",
            app,
            invocation,
            turn,
            reason=reason,
            code=code,
            retryable=retryable,
        )
        event, _ = self._persist_terminal_event(
            terminal, "failed", app, invocation, turn, session, key, holder, token
        )
        return event

    async def _publish_turn_artifacts(
        self,
        app: HarnessRecord,
        invocation: InvocationRecord,
        turn: TurnRecord,
        key: tuple[str, str, str],
        holder: str,
        token: int,
    ) -> list[CanonicalEventRecord]:
        if self.artifacts is None:
            return []
        self.store.assert_lease(key, holder, token)
        try:
            async with asyncio.timeout(5):
                refs = await self._guarded_adapter_call(
                    key,
                    holder,
                    token,
                    lambda: self.adapter.list_artifacts(
                        ListArtifactsRequest(
                            sessionId=invocation.sessionId,
                            appName=invocation.appName,
                            userId=invocation.userId,
                        )
                    ),
                )
        except LeaseFencingError:
            raise
        except Exception:
            return []
        published: list[CanonicalEventRecord] = []
        for ref in refs:
            content = getattr(ref, "content", None)
            if not isinstance(content, bytes):
                continue
            try:
                self.store.assert_lease(key, holder, token)
                record, created = self.artifacts.register_produced(
                    invocation.sessionId,
                    str(ref.path),
                    content,
                    invocation_id=invocation.id,
                    owner_principal_id=str(
                        invocation.executionContext.get("principalId") or ""
                    ),
                    media_type=(
                        ref.mediaType
                        if isinstance(getattr(ref, "mediaType", None), str)
                        and ref.mediaType
                        else None
                    ),
                    app_name=invocation.appName,
                    user_id=invocation.userId,
                )
                if not created:
                    continue
                published.append(
                    self.event_log.append_typed(
                        type_="haas.artifact.registered",
                        app_name=app.id,
                        user_id=invocation.userId,
                        invocation_id=invocation.id,
                        session_id=invocation.sessionId,
                        turn_id=turn.id,
                        harness_id=app.id,
                        adapter_id=self.adapter.adapter_id,
                        author="haas",
                        content={"role": "model", "parts": []},
                        actions={"artifactDelta": {}},
                        haas={
                            "fileId": record.id,
                            "relativePath": record.relativePath,
                            "mediaType": record.mediaType,
                            "bytes": record.bytes,
                            "invocationId": invocation.id,
                            "previewStatus": record.previewStatus,
                            "downloadStatus": record.downloadStatus,
                        },
                    )
                )
            except LeaseFencingError:
                raise
            except Exception:
                # Artifact publication is best-effort. A malformed or unreadable
                # candidate, storage failure, or event-log failure must not rewrite
                # an otherwise successful harness turn.
                continue
        return published

    def _persist_terminal_event(
        self,
        terminal: HarnessEvent,
        status: str,
        app: HarnessRecord,
        invocation: InvocationRecord,
        turn: TurnRecord,
        session: SessionRecord,
        key: tuple[str, str, str],
        holder: str,
        token: int,
        *,
        merge_session_state: bool = True,
    ) -> tuple[CanonicalEventRecord, SessionRecord]:
        self.store.assert_lease(key, holder, token)
        delta = terminal.actions.get("stateDelta")
        if merge_session_state and isinstance(delta, dict):
            session.state = _deep_merge(session.state, delta)
        terminal_at_ms = int(time.time() * 1000)
        invocation.status = status
        invocation.completedAtMs = terminal_at_ms
        turn.status = status
        turn.completedAtMs = terminal_at_ms

        # Compute the final session state in memory before flushing so the whole
        # terminal record set (invocation, turn, session, event) is written once,
        # atomically, instead of across 7+ independent autocommit commits
        # (P0-3 / S2-005). A crash mid-way used to leave a half-committed terminal.
        session = self._refresh_policy_state(session, key)
        active = self._active.get(invocation.id)
        if status == "interrupted" and active is not None and active.control_intent == "pause":
            session.controlState = "paused"
            session.supportsResume = True
            session.resumableInvocationId = invocation.id
        else:
            session.controlState = "idle"
            session.supportsResume = False
            session.resumableInvocationId = None
        latest_session = self.store.get_session(key)
        if (
            latest_session is not None
            and latest_session.desiredRevision > latest_session.appliedRevision
        ):
            self._apply_desired_policy(latest_session)
            session = latest_session

        with self.store.transaction():
            self.store.put_invocation(invocation)
            self.store.put_turn(turn)
            session = self.store.put_session(session)
            event = self._append_harness_event(
                terminal, app, invocation, turn, key, holder, token
            )
            invocation.completedAtMs = event.observedAtMs
            turn.completedAtMs = event.observedAtMs
            self.store.put_invocation(invocation)
            self.store.put_turn(turn)
        if active is not None and not active.terminal_status.done():
            active.terminal_status.set_result(status)
        return event, session

    async def pause_invocation(self, session_id: str, invocation_id: str) -> InvocationRecord:
        """Request a resumable interrupt and wait for its authoritative terminal."""
        invocation = self.store.get_invocation(invocation_id)
        if invocation is None or invocation.sessionId != session_id:
            raise InvocationNotFoundError(invocation_id)
        if invocation.status == "interrupted":
            return invocation
        if invocation.status != "running":
            raise InvocationNotRunningError(invocation_id)

        active = self._active.get(invocation_id)
        if active is None:
            raise InvocationNotRunningError(invocation_id)
        session = self.store.get_session(
            self._key(invocation.appName, invocation.userId, session_id)
        )
        if session is None:
            raise InvocationNotFoundError(invocation_id)

        async with active.control_lock:
            if active.control_intent == "cancel":
                raise InvocationNotRunningError(invocation_id)
            active.control_intent = "pause"
            session.controlState = "pausing"
            session.supportsResume = False
            session.resumableInvocationId = None
            self.store.put_session(session)
            if not active.interrupt_dispatched:
                active.interrupt_dispatched = True
                try:
                    result = await self.adapter.cancel_turn(
                        CancelTurnRequest(
                            turnId=active.turn_id,
                            sessionId=session_id,
                            invocationId=invocation_id,
                        )
                    )
                    if result.status == "unsupported":
                        raise RuntimeError("native interrupt unsupported")
                except Exception as exc:
                    active.interrupt_dispatched = False
                    active.control_intent = None
                    current_session = self.store.get_session(
                        self._key(invocation.appName, invocation.userId, session_id)
                    )
                    if current_session is not None and current_session.controlState == "pausing":
                        current_session.controlState = "running"
                        self.store.put_session(current_session)
                    raise AdapterTurnError(invocation_id) from exc
        terminal_status = await asyncio.shield(active.terminal_status)
        current = self.store.get_invocation(invocation_id)
        if current is None:
            raise InvocationNotFoundError(invocation_id)
        if terminal_status != "interrupted":
            if terminal_status == "control_failed":
                raise AdapterTurnError(invocation_id)
            raise InvocationNotRunningError(invocation_id)
        return current

    async def continue_invocation(
        self, session_id: str, invocation_id: str, *, instruction: str | None = None
    ) -> RunResult:
        """Continue the latest interrupted source as a new linked turn."""
        events = [
            event
            async for event in self.continue_stream(
                session_id, invocation_id, instruction=instruction
            )
        ]
        if not events or events[-1].invocationId is None:
            raise RuntimeError("continued run completed without invocation events")
        continued = self._read_invocation(events[-1].invocationId)
        session = self.get_session(continued.appName, continued.userId, session_id)
        return RunResult(session=session, invocation=continued, events=events)

    def continue_stream(
        self, session_id: str, invocation_id: str, *, instruction: str | None = None
    ) -> AsyncIterator[CanonicalEventRecord]:
        """Validate a resumable source and stream its new linked invocation."""
        source = self.store.get_invocation(invocation_id)
        if source is None or source.sessionId != session_id:
            raise InvocationNotFoundError(invocation_id)
        session = self.store.get_session(
            self._key(source.appName, source.userId, session_id)
        )
        if (
            source.status != "interrupted"
            or session is None
            or session.controlState != "paused"
            or not session.supportsResume
            or session.resumableInvocationId != source.id
        ):
            raise InvocationNotResumableError(invocation_id)
        app = self.registry.get(source.appName)
        if app is None:
            raise InvocationNotFoundError(invocation_id)
        execution_context = deepcopy(source.executionContext)
        return self.run_stream(
            RunRequest(
                app=app,
                user_id=source.userId,
                session_id=session_id,
                message={
                    "role": "user",
                    "parts": [] if instruction is None else [{"text": instruction}],
                },
                continued_from_invocation_id=source.id,
                continued_from_turn_id=source.turnId,
                sandbox=dict(execution_context.get("sandbox") or {}),
                policy=dict(execution_context.get("policy") or {}),
                principal_id=str(execution_context.get("principalId") or ""),
            )
        )

    async def cancel_invocation(self, session_id: str, invocation_id: str) -> InvocationRecord:
        """Cancel a running invocation; idempotent for terminal invocations."""
        invocation = self.store.get_invocation(invocation_id)
        if invocation is None or invocation.sessionId != session_id:
            raise InvocationNotFoundError(invocation_id)
        if invocation.status == "interrupted":
            key = self._key(invocation.appName, invocation.userId, session_id)
            session = self.store.get_session(key)
            if (
                session is not None
                and session.supportsResume
                and session.resumableInvocationId == invocation_id
            ):
                session.controlState = "cancelled"
                session.supportsResume = False
                session.resumableInvocationId = None
                self.store.put_session(session)
            return invocation
        if invocation.status != "running":
            return invocation

        active = self._active.get(invocation_id)
        if active is not None:
            async with active.control_lock:
                active.control_intent = "cancel"
                session = self.store.get_session(
                    self._key(invocation.appName, invocation.userId, session_id)
                )
                if session is not None:
                    session.controlState = "cancelling"
                    session.supportsResume = False
                    session.resumableInvocationId = None
                    self.store.put_session(session)
                if not active.interrupt_dispatched:
                    active.interrupt_dispatched = True
                    try:
                        result = await self.adapter.cancel_turn(
                            CancelTurnRequest(
                                turnId=active.turn_id,
                                sessionId=session_id,
                                invocationId=invocation_id,
                            )
                        )
                        if result.status == "unsupported":
                            raise RuntimeError("native interrupt unsupported")
                    except Exception as exc:
                        active.interrupt_dispatched = False
                        active.control_intent = None
                        current_session = self.store.get_session(
                            self._key(invocation.appName, invocation.userId, session_id)
                        )
                        if (
                            current_session is not None
                            and current_session.controlState == "cancelling"
                        ):
                            current_session.controlState = "running"
                            self.store.put_session(current_session)
                        raise AdapterTurnError(invocation_id) from exc
            current = self.store.get_invocation(invocation_id)
            if current is None:
                raise InvocationNotFoundError(invocation_id)
            return current

        holder = f"cancel_{uuid.uuid4().hex[:8]}"
        key = self._key(invocation.appName, invocation.userId, session_id)
        try:
            lease = self.store.acquire_lease(key, holder, ttl_ms=self.lease_ttl_ms)
        except LeaseConflictError as exc:
            raise SessionBusyError(session_id) from exc

        try:
            invocation = self.store.get_invocation(invocation_id)
            if invocation is None or invocation.sessionId != session_id:
                raise InvocationNotFoundError(invocation_id)
            if invocation.status != "running":
                return invocation

            turn = self.store.get_turn(invocation.turnId)
            session = self.store.get_session(key)
            app = self.registry.get(invocation.appName)
            if (
                turn is None
                or turn.invocationId != invocation.id
                or turn.sessionId != session_id
                or session is None
                or app is None
            ):
                raise InvocationNotFoundError(invocation_id)

            terminal = self._terminal_event("cancelled", app, invocation, turn)
            session_was_superseded = session.state.get("status") in {
                "completed",
                "failed",
                "incomplete",
                "cancelled",
            }
            self._persist_terminal_event(
                terminal,
                "cancelled",
                app,
                invocation,
                turn,
                session,
                key,
                holder,
                lease.token,
                merge_session_state=not session_was_superseded,
            )
            return invocation
        finally:
            self.store.release_lease(key, holder, lease.token)

    def _read_invocation(self, invocation_id: str) -> InvocationRecord:
        invocation = self.store.get_invocation(invocation_id)
        if invocation is None:
            raise RuntimeError(f"invocation not found: {invocation_id}")
        return invocation
