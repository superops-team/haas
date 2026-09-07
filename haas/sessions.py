"""Session Runtime: session/invocation/turn lifecycle (specs/session-runtime/)."""
from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, TypeVar

from haas.events import EventLog
from haas.harnesses import (
    CancelTurnRequest,
    HarnessAdapter,
    HarnessEvent,
    PrepareSessionRequest,
    StartTurnRequest,
)
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


@dataclass
class RunResult:
    session: SessionRecord
    invocation: InvocationRecord
    events: list[CanonicalEventRecord]


@dataclass
class _ActiveTurn:
    turn_id: str
    session_id: str


_T = TypeVar("_T")


@dataclass
class SessionRuntime:
    store: MemoryStore
    registry: HarnessRegistry
    adapter: HarnessAdapter
    event_log: EventLog
    lease_ttl_ms: int = 30_000
    lease_renew_interval_ms: int = 10_000
    turn_timeout_s: float = 900.0
    _active: dict[str, _ActiveTurn] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.lease_ttl_ms <= 0:
            raise ValueError("lease_ttl_ms must be positive")
        if self.lease_renew_interval_ms <= 0:
            raise ValueError("lease_renew_interval_ms must be positive")
        if self.lease_renew_interval_ms * 2 >= self.lease_ttl_ms:
            raise ValueError("lease_renew_interval_ms must be less than half of lease_ttl_ms")
        if self.turn_timeout_s <= self.lease_ttl_ms / 1000:
            raise ValueError("turn_timeout_s must be greater than lease_ttl_ms")

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

    async def run(self, req: RunRequest) -> RunResult:
        events = [event async for event in self._drive(req)]
        session = self.get_session(req.app.id, req.user_id, req.session_id or events[0].sessionId)
        invocation_id = events[-1].invocationId
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

        session = self.store.get_session(key)
        if session is None:
            session = SessionRecord(id=session_id, appName=app.id, userId=req.user_id)
        session = self.store.put_session(session)

        invocation = InvocationRecord(
            id=f"inv_{uuid.uuid4().hex[:16]}",
            sessionId=session_id,
            appName=app.id,
            userId=req.user_id,
            turnId=f"turn_{uuid.uuid4().hex[:16]}",
            status="running",
        )
        turn = TurnRecord(
            id=invocation.turnId,
            invocationId=invocation.id,
            sessionId=session_id,
            status="running",
        )
        self.store.put_invocation(invocation)
        self.store.put_turn(turn)

        renew_task = asyncio.create_task(self._renew_lease_until_done(key, holder, token))
        completed_cleanly = False
        try:
            async with asyncio.timeout(self.turn_timeout_s):
                await self._guarded_adapter_call(
                    key, holder, token,
                    lambda: self.adapter.prepare_session(
                        PrepareSessionRequest(sessionId=session_id, appName=app.id)
                    ),
                )
                handle = await self._guarded_adapter_call(
                    key, holder, token,
                    lambda: self.adapter.start_turn(
                        StartTurnRequest(
                            invocationId=invocation.id,
                            sessionId=session_id,
                            turnId=turn.id,
                            appName=app.id,
                            input=[req.message],
                            timeoutSeconds=self.turn_timeout_s,
                        )
                    ),
                )
                self._active[invocation.id] = _ActiveTurn(
                    turn_id=turn.id, session_id=session_id
                )

                async for harness_event in self.adapter.stream_events(handle):
                    self.store.assert_lease(key, holder, token)
                    event = self._append_harness_event(
                        harness_event, app, invocation, turn, key, holder, token
                    )
                    session = self._merge_actions(session, event, key, holder, token)
                    yield event

                result = await self._guarded_adapter_call(
                    key, holder, token, lambda: self.adapter.finalize_turn(handle)
                )
                terminal = result.terminalEvent or self._terminal_event(
                    result.status, app, invocation, turn
                )
                event, session = self._persist_terminal_event(
                    terminal, result.status, app, invocation, turn, session,
                    key, holder, token
                )
                yield event
                completed_cleanly = True
        except TimeoutError as exc:
            event = self._persist_failure_terminal(
                app, invocation, turn, session, key, holder, token, reason="timeout"
            )
            session = self.get_session(app.id, req.user_id, session_id)
            yield event
            raise AdapterTurnTimeoutError(invocation.id) from exc
        except LeaseFencingError as exc:
            raise AdapterTurnError(invocation.id) from exc
        except Exception as exc:
            event = self._persist_failure_terminal(
                app, invocation, turn, session, key, holder, token
            )
            session = self.get_session(app.id, req.user_id, session_id)
            yield event
            raise AdapterTurnError(invocation.id) from exc
        finally:
            self._active.pop(invocation.id, None)
            renew_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, LeaseFencingError):
                await renew_task
            if not completed_cleanly and invocation.status == "running":
                invocation.status = "failed"
                turn.status = "failed"
            with contextlib.suppress(LeaseFencingError):
                self.store.assert_lease(key, holder, token)
                self.store.put_invocation(invocation)
                self.store.put_turn(turn)
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
        return self.event_log.append(
            app_name=app.id,
            user_id=invocation.userId,
            invocation_id=invocation.id,
            session_id=invocation.sessionId,
            turn_id=turn.id,
            harness_id=app.id,
            adapter_id=self.adapter.adapter_id,
            author=harness_event.author,
            content=harness_event.content,
            actions=harness_event.actions,
        )

    def _terminal_event(
        self,
        status: str,
        app: HarnessRecord,
        invocation: InvocationRecord,
        turn: TurnRecord,
        *,
        reason: str | None = None,
    ) -> Any:
        state_delta: dict[str, Any] = {"status": status}
        if reason is not None:
            state_delta["reason"] = reason
        return HarnessEvent(
            type=f"harness.turn.{status}",
            invocationId=invocation.id,
            sessionId=invocation.sessionId,
            turnId=turn.id,
            author=self.adapter.base,
            content={"role": "model", "parts": []},
            actions={"stateDelta": state_delta},
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
            session = self.store.put_session(session)
        return session

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
    ) -> CanonicalEventRecord:
        terminal = self._terminal_event("failed", app, invocation, turn, reason=reason)
        event, _ = self._persist_terminal_event(
            terminal, "failed", app, invocation, turn, session, key, holder, token
        )
        return event

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
    ) -> tuple[CanonicalEventRecord, SessionRecord]:
        self.store.assert_lease(key, holder, token)
        delta = terminal.actions.get("stateDelta")
        if isinstance(delta, dict):
            session.state = _deep_merge(session.state, delta)
        terminal_at_ms = int(time.time() * 1000)
        invocation.status = status
        invocation.completedAtMs = terminal_at_ms
        turn.status = status
        turn.completedAtMs = terminal_at_ms
        self.store.put_invocation(invocation)
        self.store.put_turn(turn)
        session = self.store.put_session(session)

        event = self._append_harness_event(terminal, app, invocation, turn, key, holder, token)
        invocation.completedAtMs = event.observedAtMs
        turn.completedAtMs = event.observedAtMs
        self.store.put_invocation(invocation)
        self.store.put_turn(turn)
        return event, session

    async def cancel_invocation(
        self, session_id: str, invocation_id: str
    ) -> InvocationRecord:
        """Cancel a running invocation; idempotent for terminal invocations."""
        active = self._active.get(invocation_id)
        if active is None:
            invocation = self.store.get_invocation(invocation_id)
            if invocation is None:
                raise InvocationNotFoundError(invocation_id)
            return invocation
        await self.adapter.cancel_turn(
            CancelTurnRequest(
                turnId=active.turn_id,
                sessionId=session_id,
                invocationId=invocation_id,
            )
        )
        invocation = self.store.get_invocation(invocation_id)
        if invocation is None:
            raise InvocationNotFoundError(invocation_id)
        return invocation

    def _read_invocation(self, invocation_id: str) -> InvocationRecord:
        invocation = self.store.get_invocation(invocation_id)
        if invocation is None:
            raise RuntimeError(f"invocation not found: {invocation_id}")
        return invocation
