"""Session Runtime: session/invocation/turn lifecycle (specs/session-runtime/)."""
from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

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


@dataclass
class SessionRuntime:
    store: MemoryStore
    registry: HarnessRegistry
    adapter: HarnessAdapter
    event_log: EventLog
    _active: dict[str, _ActiveTurn] = field(default_factory=dict)

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
            self.store.acquire_lease(key, holder)
        except LeaseConflictError as exc:
            raise SessionBusyError(session_id) from exc

        session = self.store.get_session(key)
        if session is None:
            session = SessionRecord(id=session_id, appName=app.id, userId=req.user_id)
        session = self.store.put_session(session)

        invocation = InvocationRecord(
            id=f"inv_{uuid.uuid4().hex[:16]}",
            sessionId=session_id,
            appName=app.id,
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

        try:
            await self.adapter.prepare_session(
                PrepareSessionRequest(sessionId=session_id, appName=app.id)
            )
            handle = await self.adapter.start_turn(
                StartTurnRequest(
                    invocationId=invocation.id,
                    sessionId=session_id,
                    turnId=turn.id,
                    appName=app.id,
                    input=[req.message],
                )
            )
            self._active[invocation.id] = _ActiveTurn(turn_id=turn.id, session_id=session_id)

            async for harness_event in self.adapter.stream_events(handle):
                event = self._append_harness_event(harness_event, app, invocation, turn)
                session = self._merge_actions(session, event)
                yield event

            result = await self.adapter.finalize_turn(handle)
            terminal = result.terminalEvent or self._terminal_event(
                result.status, app, invocation, turn
            )
            event = self._append_harness_event(terminal, app, invocation, turn)
            session = self._merge_actions(session, event)
            yield event

            invocation.status = result.status
            invocation.completedAtMs = event.observedAtMs
            turn.status = result.status
            turn.completedAtMs = event.observedAtMs
        finally:
            self._active.pop(invocation.id, None)
            if invocation.status == "running":
                invocation.status = "failed"
                turn.status = "failed"
            self.store.put_invocation(invocation)
            self.store.put_turn(turn)
            self.store.put_session(session)
            self.store.release_lease(key, holder)

    def _append_harness_event(
        self,
        harness_event: Any,
        app: HarnessRecord,
        invocation: InvocationRecord,
        turn: TurnRecord,
    ) -> CanonicalEventRecord:
        return self.event_log.append(
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
        self, status: str, app: HarnessRecord, invocation: InvocationRecord, turn: TurnRecord
    ) -> Any:
        return HarnessEvent(
            type=f"harness.turn.{status}",
            invocationId=invocation.id,
            sessionId=invocation.sessionId,
            turnId=turn.id,
            author=self.adapter.base,
            content={"role": "model", "parts": []},
            actions={"stateDelta": {"status": status}},
        )

    def _merge_actions(
        self, session: SessionRecord, event: CanonicalEventRecord
    ) -> SessionRecord:
        delta = event.actions.get("stateDelta")
        if isinstance(delta, dict):
            session.state = _deep_merge(session.state, delta)
            session = self.store.put_session(session)
        return session

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
