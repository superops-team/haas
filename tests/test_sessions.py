"""Session Runtime unit tests (specs/session-runtime/README.md)."""

import asyncio
import hashlib
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

from haas.events import EventLog
from haas.harnesses import FakeAdapter
from haas.harnesses.base import (
    AdapterTurnResult,
    CancelResult,
    CancelTurnRequest,
    HarnessEvent,
    PreparedSession,
    ResumeSessionRequest,
    StartTurnRequest,
    TurnHandle,
)
from haas.identity import Principal
from haas.registry import HarnessRegistry, seed_codex
from haas.sessions import (
    AdapterTurnError,
    InvocationNotRunningError,
    RunRequest,
    SessionBusyError,
    SessionNotFoundError,
    SessionRuntime,
)
from haas.stores import (
    ApprovalRecord,
    InputRequestRecord,
    InvocationRecord,
    LeaseFencingError,
    MemoryStore,
    SessionRecord,
    TurnRecord,
)


@pytest.fixture
def runtime() -> SessionRuntime:
    store = MemoryStore()
    registry = HarnessRegistry(store=store)
    seed_codex(registry)
    return SessionRuntime(
        store=store,
        registry=registry,
        adapter=FakeAdapter(),
        event_log=EventLog(store=store),
    )


async def test_run_creates_session_invocation_events(runtime: SessionRuntime) -> None:
    req = RunRequest(
        app=runtime.registry.resolve_default_app(Principal("p")),
        user_id="u_1",
        message={"role": "user", "parts": [{"text": "hi"}]},
    )
    result = await runtime.run(req)
    assert result.invocation.status == "completed"
    assert result.events
    assert result.events[-1].actions["stateDelta"]["status"] == "completed"
    assert result.session.id.startswith("hsess_")


async def test_terminal_state_is_persisted_before_terminal_event_append() -> None:
    class ObservingEventLog(EventLog):
        def append(self, **kwargs: Any):
            actions = kwargs.get("actions") or {}
            delta = actions.get("stateDelta") if isinstance(actions, dict) else None
            if isinstance(delta, dict) and delta.get("status") in {
                "completed",
                "failed",
                "incomplete",
                "interrupted",
                "cancelled",
            }:
                invocation = self.store.get_invocation(kwargs["invocation_id"])
                turn = self.store.get_turn(kwargs["turn_id"])
                assert invocation is not None
                assert turn is not None
                assert invocation.status == delta["status"]
                assert turn.status == delta["status"]
            return super().append(**kwargs)

    store = MemoryStore()
    registry = HarnessRegistry(store=store)
    app_record = seed_codex(registry)
    rt = SessionRuntime(
        store=store,
        registry=registry,
        adapter=FakeAdapter(),
        event_log=ObservingEventLog(store=store),
    )
    result = await rt.run(
        RunRequest(
            app=app_record,
            user_id="u_1",
            session_id="hsess_order",
            message={"role": "user", "parts": []},
        )
    )
    assert result.invocation.status == "completed"


async def test_terminal_state_closes_pending_interactions(runtime: SessionRuntime) -> None:
    class InteractionAdapter(FakeAdapter):
        async def stream_events(self, handle: TurnHandle) -> AsyncIterator[HarnessEvent]:
            runtime.store.put_approval(
                ApprovalRecord(
                    id="appr_1",
                    sessionId=handle.sessionId,
                    invocationId=handle.invocationId,
                    turnId=handle.turnId,
                )
            )
            runtime.store.put_input_request(
                InputRequestRecord(
                    id="inreq_1",
                    sessionId=handle.sessionId,
                    invocationId=handle.invocationId,
                    turnId=handle.turnId,
                    questions=[],
                    nativeRequestId=1,
                    adapterGeneration=1,
                )
            )
            yield HarnessEvent(
                type="harness.turn.failed",
                invocationId=handle.invocationId,
                sessionId=handle.sessionId,
                turnId=handle.turnId,
                author="fake",
                content={"role": "model", "parts": []},
                actions={"stateDelta": {"status": "failed"}},
            )

    runtime.adapter = InteractionAdapter()
    await runtime.run(
        RunRequest(
            app=runtime.registry.resolve_default_app(Principal("p")),
            user_id="u_1",
            session_id="hsess_close",
            message={"role": "user", "parts": [{"text": "hi"}]},
        )
    )

    approval = runtime.store.get_approval("appr_1")
    request = runtime.store.get_input_request("inreq_1")
    assert approval is not None and approval.status == "cancelled"
    assert request is not None and request.status == "cancelled"


async def test_session_busy(runtime: SessionRuntime) -> None:
    key = ("chrn_codex_default", "u_1", "hsess_1")
    runtime.store.acquire_lease(key, holder="other")
    req = RunRequest(
        app=runtime.registry.resolve_app(Principal("p"), "chrn_codex_default"),
        user_id="u_1",
        message={"role": "user", "parts": [{"text": "hi"}]},
        session_id="hsess_1",
    )
    with pytest.raises(SessionBusyError):
        await runtime.run(req)


def test_get_and_delete_session(runtime: SessionRuntime) -> None:
    with pytest.raises(SessionNotFoundError):
        runtime.get_session("chrn_codex_default", "u_1", "nope")

    runtime.store.put_session(
        SessionRecord(id="hsess_1", appName="chrn_codex_default", userId="u_1")
    )
    assert runtime.get_session("chrn_codex_default", "u_1", "hsess_1").id == "hsess_1"
    runtime.delete_session("chrn_codex_default", "u_1", "hsess_1")
    with pytest.raises(SessionNotFoundError):
        runtime.get_session("chrn_codex_default", "u_1", "hsess_1")


def test_apply_state_delta_deep_merge(runtime: SessionRuntime) -> None:
    runtime.store.put_session(
        SessionRecord(id="hsess_1", appName="chrn_codex_default", userId="u_1")
    )
    runtime.apply_state_delta("chrn_codex_default", "u_1", "hsess_1", {"a": {"b": 1}})
    session = runtime.apply_state_delta("chrn_codex_default", "u_1", "hsess_1", {"a": {"c": 2}})
    assert session.state == {"a": {"b": 1, "c": 2}}


class _OneBlockingEventAdapter(FakeAdapter):
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.cancelled = False

    async def stream_events(self, handle: TurnHandle) -> AsyncIterator[HarnessEvent]:
        self.started.set()
        await self.release.wait()
        yield HarnessEvent(
            type="harness.text.delta",
            invocationId=handle.invocationId,
            sessionId=handle.sessionId,
            turnId=handle.turnId,
            author=self.base,
            content={"role": "model", "parts": [{"text": "late"}]},
            actions={"stateDelta": {"last_text": "late"}},
        )

    async def finalize_turn(self, handle: TurnHandle) -> AdapterTurnResult:
        return AdapterTurnResult(
            status="completed",
            terminalEvent=HarnessEvent(
                type="harness.turn.completed",
                invocationId=handle.invocationId,
                sessionId=handle.sessionId,
                turnId=handle.turnId,
                author=self.base,
                content={"role": "model", "parts": []},
                actions={"stateDelta": {"status": "completed"}},
            ),
        )


class _NeverEndingAdapter(FakeAdapter):
    def __init__(self) -> None:
        self.stream_cancelled = False

    async def stream_events(self, handle: TurnHandle) -> AsyncIterator[HarnessEvent]:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.stream_cancelled = True
            raise
        if False:  # pragma: no cover - keeps this an async generator
            yield HarnessEvent("", "", "", "", "", {})


class _StreamingTerminalAdapter(FakeAdapter):
    async def stream_events(self, handle: TurnHandle) -> AsyncIterator[HarnessEvent]:
        yield HarnessEvent(
            type="harness.turn.completed",
            invocationId=handle.invocationId,
            sessionId=handle.sessionId,
            turnId=handle.turnId,
            author=self.base,
            content={"role": "model", "parts": []},
            actions={"stateDelta": {"status": "completed"}},
        )

    async def finalize_turn(self, handle: TurnHandle) -> AdapterTurnResult:
        return AdapterTurnResult(status="completed")


class _TerminalThenFinalizeFailureAdapter(_StreamingTerminalAdapter):
    async def finalize_turn(self, handle: TurnHandle) -> AdapterTurnResult:
        del handle
        raise RuntimeError("finalize failed after terminal")


class _DuplicateTerminalAdapter(_StreamingTerminalAdapter):
    async def stream_events(self, handle: TurnHandle) -> AsyncIterator[HarnessEvent]:
        async for event in super().stream_events(handle):
            yield event
            yield event


class _NaturalInterruptedAdapter(_StreamingTerminalAdapter):
    async def stream_events(self, handle: TurnHandle) -> AsyncIterator[HarnessEvent]:
        yield HarnessEvent(
            type="harness.turn.interrupted",
            invocationId=handle.invocationId,
            sessionId=handle.sessionId,
            turnId=handle.turnId,
            author=self.base,
            content={"role": "model", "parts": []},
            actions={"stateDelta": {"status": "interrupted"}},
        )

    async def finalize_turn(self, handle: TurnHandle) -> AdapterTurnResult:
        del handle
        return AdapterTurnResult(status="interrupted")


class _AcknowledgedInterruptAdapter(FakeAdapter):
    """Acknowledges interrupt before releasing the authoritative terminal."""

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.interrupt_acknowledged = asyncio.Event()
        self.release_terminal = asyncio.Event()

    async def stream_events(self, handle: TurnHandle) -> AsyncIterator[HarnessEvent]:
        self.started.set()
        await self.release_terminal.wait()
        yield HarnessEvent(
            type="harness.turn.interrupted",
            invocationId=handle.invocationId,
            sessionId=handle.sessionId,
            turnId=handle.turnId,
            author=self.base,
            content={"role": "model", "parts": []},
            actions={"stateDelta": {"status": "interrupted"}},
        )

    async def cancel_turn(self, request: CancelTurnRequest) -> CancelResult:
        self.interrupt_acknowledged.set()
        return CancelResult(status="accepted")

    async def finalize_turn(self, handle: TurnHandle) -> AdapterTurnResult:
        return AdapterTurnResult(status="interrupted")


class _RejectedInterruptAdapter(_AcknowledgedInterruptAdapter):
    async def cancel_turn(self, request: CancelTurnRequest) -> CancelResult:
        del request
        self.interrupt_acknowledged.set()
        raise RuntimeError("native interrupt rejected")

    async def stream_events(self, handle: TurnHandle) -> AsyncIterator[HarnessEvent]:
        self.started.set()
        await self.release_terminal.wait()
        yield HarnessEvent(
            type="harness.turn.completed",
            invocationId=handle.invocationId,
            sessionId=handle.sessionId,
            turnId=handle.turnId,
            author=self.base,
            content={"role": "model", "parts": []},
            actions={"stateDelta": {"status": "completed"}},
        )

    async def finalize_turn(self, handle: TurnHandle) -> AdapterTurnResult:
        return AdapterTurnResult(status="completed")


class _PauseThenContinueAdapter(_AcknowledgedInterruptAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.first_invocation_id: str | None = None
        self.resume_called = False
        self.start_requests: list[StartTurnRequest] = []

    async def resume_session(self, request: ResumeSessionRequest) -> PreparedSession:
        self.resume_called = True
        return PreparedSession(sessionId=request.sessionId, nativeRef={"resumed": True})

    async def start_turn(self, request: StartTurnRequest) -> TurnHandle:
        self.start_requests.append(request)
        if self.first_invocation_id is None:
            self.first_invocation_id = request.invocationId
        else:
            assert self.resume_called is True
        return await super().start_turn(request)

    async def stream_events(self, handle: TurnHandle) -> AsyncIterator[HarnessEvent]:
        if handle.invocationId == self.first_invocation_id:
            async for event in super().stream_events(handle):
                yield event
            return
        yield HarnessEvent(
            type="harness.turn.completed",
            invocationId=handle.invocationId,
            sessionId=handle.sessionId,
            turnId=handle.turnId,
            author=self.base,
            content={"role": "model", "parts": [{"text": "continued"}]},
            actions={"stateDelta": {"status": "completed"}},
        )

    async def finalize_turn(self, handle: TurnHandle) -> AdapterTurnResult:
        if handle.invocationId == self.first_invocation_id:
            return await super().finalize_turn(handle)
        return AdapterTurnResult(status="completed")


async def test_adapter_stream_terminal_is_persisted_exactly_once() -> None:
    store = MemoryStore()
    registry = HarnessRegistry(store=store)
    app_record = seed_codex(registry)
    runtime = SessionRuntime(
        store=store,
        registry=registry,
        adapter=_StreamingTerminalAdapter(),
        event_log=EventLog(store=store),
    )

    result = await runtime.run(
        RunRequest(
            app=app_record,
            user_id="u_1",
            session_id="hsess_terminal_once",
            message={"role": "user", "parts": []},
        )
    )

    terminal = [event for event in result.events if event.type.startswith("haas.turn.")]
    assert [event.type for event in terminal] == ["haas.turn.completed"]


@pytest.mark.parametrize(
    "adapter_type", [_TerminalThenFinalizeFailureAdapter, _DuplicateTerminalAdapter]
)
async def test_adapter_cannot_persist_a_second_terminal(adapter_type) -> None:
    store = MemoryStore()
    registry = HarnessRegistry(store=store)
    app_record = seed_codex(registry)
    runtime = SessionRuntime(
        store=store,
        registry=registry,
        adapter=adapter_type(),
        event_log=EventLog(store=store),
    )
    request = RunRequest(
        app=app_record,
        user_id="u_1",
        session_id="hsess_terminal_guard",
        message={"role": "user", "parts": []},
    )

    result = await runtime.run(request)
    assert result.invocation.status == "completed"

    invocation = next(iter(store._invocations.values()))
    terminal = [
        event
        for event in store.read_invocation(
            (app_record.id, "u_1", "hsess_terminal_guard"),
            invocation.id,
            after=-1,
        )
        if event.type.startswith("haas.turn.")
    ]
    assert [event.type for event in terminal] == ["haas.turn.completed"]


async def test_unrequested_interrupted_terminal_returns_session_to_idle(
    runtime: SessionRuntime,
) -> None:
    runtime.adapter = _NaturalInterruptedAdapter()

    result = await runtime.run(
        RunRequest(
            app=runtime.registry.resolve_default_app(Principal("p")),
            user_id="u_1",
            session_id="hsess_natural_interrupted",
            message={"role": "user", "parts": []},
        )
    )

    assert result.invocation.status == "interrupted"
    assert result.session.controlState == "idle"
    assert result.session.supportsResume is False
    assert result.session.resumableInvocationId is None


async def test_pause_waits_for_interrupted_terminal_before_marking_session_resumable() -> None:
    store = MemoryStore()
    registry = HarnessRegistry(store=store)
    app_record = seed_codex(registry)
    adapter = _AcknowledgedInterruptAdapter()
    runtime = SessionRuntime(
        store=store,
        registry=registry,
        adapter=adapter,
        event_log=EventLog(store=store),
    )
    session_id = "hsess_pause_barrier"
    run_task = asyncio.create_task(
        runtime.run(
            RunRequest(
                app=app_record,
                user_id="u_1",
                session_id=session_id,
                message={"role": "user", "parts": []},
                sandbox={
                    "mode": "workspace-write",
                    "workspaceRoot": "/workspace/project",
                    "writableRoots": ["/workspace/project"],
                },
                policy={
                    "approvalPolicy": "on-request",
                    "network": {"defaultAction": "deny", "allow": ["example.com"]},
                },
                principal_id="p_original",
            )
        )
    )
    await asyncio.wait_for(adapter.started.wait(), timeout=0.5)
    invocation_id = next(iter(runtime._active))

    pause_task = asyncio.create_task(runtime.pause_invocation(session_id, invocation_id))
    await asyncio.wait_for(adapter.interrupt_acknowledged.wait(), timeout=0.5)
    await asyncio.sleep(0)

    assert pause_task.done() is False
    pausing = runtime.get_session(app_record.id, "u_1", session_id)
    assert pausing.controlState == "pausing"
    assert pausing.supportsResume is False
    assert pausing.resumableInvocationId is None

    adapter.release_terminal.set()
    paused_invocation = await asyncio.wait_for(pause_task, timeout=0.5)
    result = await asyncio.wait_for(run_task, timeout=0.5)

    assert paused_invocation.status == "interrupted"
    assert result.invocation.status == "interrupted"
    assert result.session.controlState == "paused"
    assert result.session.supportsResume is True
    assert result.session.resumableInvocationId == invocation_id
    assert [event.type for event in result.events if event.type.startswith("haas.turn.")] == [
        "haas.turn.interrupted"
    ]


async def test_pause_waiter_fails_when_active_stream_is_cancelled() -> None:
    store = MemoryStore()
    registry = HarnessRegistry(store=store)
    app_record = seed_codex(registry)
    adapter = _AcknowledgedInterruptAdapter()
    runtime = SessionRuntime(
        store=store, registry=registry, adapter=adapter, event_log=EventLog(store=store)
    )
    session_id = "hsess_cancelled_stream"
    run_task = asyncio.create_task(
        runtime.run(
            RunRequest(
                app=app_record,
                user_id="u_1",
                session_id=session_id,
                message={"role": "user", "parts": []},
            )
        )
    )
    await asyncio.wait_for(adapter.started.wait(), timeout=0.5)
    invocation_id = next(iter(runtime._active))
    pause_task = asyncio.create_task(runtime.pause_invocation(session_id, invocation_id))
    await asyncio.wait_for(adapter.interrupt_acknowledged.wait(), timeout=0.5)

    run_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await run_task
    with pytest.raises(AdapterTurnError):
        await asyncio.wait_for(pause_task, timeout=0.5)

    session = runtime.get_session(app_record.id, "u_1", session_id)
    assert session.controlState == "idle"


@pytest.mark.parametrize("control", ["pause", "cancel"])
async def test_rejected_native_interrupt_restores_running_session(control: str) -> None:
    store = MemoryStore()
    registry = HarnessRegistry(store=store)
    app_record = seed_codex(registry)
    adapter = _RejectedInterruptAdapter()
    runtime = SessionRuntime(
        store=store, registry=registry, adapter=adapter, event_log=EventLog(store=store)
    )
    session_id = f"hsess_rejected_{control}"
    run_task = asyncio.create_task(
        runtime.run(
            RunRequest(
                app=app_record,
                user_id="u_1",
                session_id=session_id,
                message={"role": "user", "parts": []},
            )
        )
    )
    await asyncio.wait_for(adapter.started.wait(), timeout=0.5)
    invocation_id = next(iter(runtime._active))

    with pytest.raises(AdapterTurnError):
        if control == "pause":
            await runtime.pause_invocation(session_id, invocation_id)
        else:
            await runtime.cancel_invocation(session_id, invocation_id)

    session = runtime.get_session(app_record.id, "u_1", session_id)
    assert session.controlState == "running"
    assert runtime._active[invocation_id].control_intent is None
    adapter.release_terminal.set()
    result = await asyncio.wait_for(run_task, timeout=0.5)
    assert result.invocation.status == "completed"


async def test_pause_stop_race_sends_one_interrupt_and_stop_wins() -> None:
    store = MemoryStore()
    registry = HarnessRegistry(store=store)
    app_record = seed_codex(registry)
    adapter = _AcknowledgedInterruptAdapter()
    calls = 0
    original_cancel = adapter.cancel_turn

    async def counted_cancel(request: CancelTurnRequest) -> CancelResult:
        nonlocal calls
        calls += 1
        return await original_cancel(request)

    adapter.cancel_turn = counted_cancel  # type: ignore[method-assign]
    runtime = SessionRuntime(
        store=store, registry=registry, adapter=adapter, event_log=EventLog(store=store)
    )
    session_id = "hsess_pause_stop_race"
    run_task = asyncio.create_task(
        runtime.run(
            RunRequest(
                app=app_record,
                user_id="u_1",
                session_id=session_id,
                message={"role": "user", "parts": []},
            )
        )
    )
    await asyncio.wait_for(adapter.started.wait(), timeout=0.5)
    invocation_id = next(iter(runtime._active))
    pause_task = asyncio.create_task(runtime.pause_invocation(session_id, invocation_id))
    await asyncio.wait_for(adapter.interrupt_acknowledged.wait(), timeout=0.5)

    await runtime.cancel_invocation(session_id, invocation_id)
    adapter.release_terminal.set()
    result = await asyncio.wait_for(run_task, timeout=0.5)
    with pytest.raises(InvocationNotRunningError):
        await pause_task

    assert calls == 1
    assert result.invocation.status == "cancelled"


async def test_stop_maps_native_interrupted_to_non_resumable_cancelled() -> None:
    store = MemoryStore()
    registry = HarnessRegistry(store=store)
    app_record = seed_codex(registry)
    adapter = _AcknowledgedInterruptAdapter()
    runtime = SessionRuntime(
        store=store,
        registry=registry,
        adapter=adapter,
        event_log=EventLog(store=store),
    )
    session_id = "hsess_stop_intent"
    run_task = asyncio.create_task(
        runtime.run(
            RunRequest(
                app=app_record,
                user_id="u_1",
                session_id=session_id,
                message={"role": "user", "parts": []},
            )
        )
    )
    await asyncio.wait_for(adapter.started.wait(), timeout=0.5)
    invocation_id = next(iter(runtime._active))

    acknowledged = await runtime.cancel_invocation(session_id, invocation_id)
    assert acknowledged.status == "running"
    adapter.release_terminal.set()
    result = await asyncio.wait_for(run_task, timeout=0.5)

    assert result.invocation.status == "cancelled"
    assert result.session.controlState == "idle"
    assert result.session.supportsResume is False
    assert result.session.resumableInvocationId is None
    assert [event.type for event in result.events if event.type.startswith("haas.turn.")] == [
        "haas.turn.cancelled"
    ]


async def test_continue_creates_linked_turn_on_resumed_native_session() -> None:
    store = MemoryStore()
    registry = HarnessRegistry(store=store)
    app_record = seed_codex(registry)
    adapter = _PauseThenContinueAdapter()
    runtime = SessionRuntime(
        store=store,
        registry=registry,
        adapter=adapter,
        event_log=EventLog(store=store),
    )
    session_id = "hsess_continue"
    first_run = asyncio.create_task(
        runtime.run(
            RunRequest(
                app=app_record,
                user_id="u_1",
                session_id=session_id,
                message={"role": "user", "parts": []},
                sandbox={
                    "mode": "workspace-write",
                    "workspaceRoot": "/workspace/project",
                    "writableRoots": ["/workspace/project"],
                },
                policy={
                    "approvalPolicy": "on-request",
                    "network": {"defaultAction": "deny", "allow": ["example.com"]},
                },
                principal_id="p_original",
            )
        )
    )
    await asyncio.wait_for(adapter.started.wait(), timeout=0.5)
    source_id = next(iter(runtime._active))
    pause = asyncio.create_task(runtime.pause_invocation(session_id, source_id))
    await asyncio.wait_for(adapter.interrupt_acknowledged.wait(), timeout=0.5)
    adapter.release_terminal.set()
    source = await asyncio.wait_for(pause, timeout=0.5)
    await asyncio.wait_for(first_run, timeout=0.5)
    source_turn_id = source.turnId
    source_completed_at = source.completedAtMs

    continued = await runtime.continue_invocation(
        session_id, source_id, instruction="Continue the task"
    )

    assert adapter.resume_called is True
    assert continued.invocation.id != source_id
    assert continued.invocation.turnId != source_turn_id
    assert continued.invocation.continuedFromInvocationId == source_id
    assert continued.invocation.continuedFromTurnId == source_turn_id
    assert adapter.start_requests[1].sandbox == adapter.start_requests[0].sandbox
    assert adapter.start_requests[1].policy == adapter.start_requests[0].policy
    assert adapter.start_requests[1].principalId == "p_original"
    assert "credentials" not in continued.invocation.executionContext
    assert continued.invocation.executionContext == store.get_invocation(source_id).executionContext
    stored_source = store.get_invocation(source_id)
    assert stored_source is not None
    assert stored_source.status == "interrupted"
    assert stored_source.completedAtMs == source_completed_at
    assert continued.session.controlState == "idle"
    assert continued.session.supportsResume is False
    assert continued.session.resumableInvocationId is None


async def test_stop_on_paused_session_revokes_resume_without_rewriting_source() -> None:
    store = MemoryStore()
    registry = HarnessRegistry(store=store)
    app_record = seed_codex(registry)
    adapter = _AcknowledgedInterruptAdapter()
    runtime = SessionRuntime(
        store=store,
        registry=registry,
        adapter=adapter,
        event_log=EventLog(store=store),
    )
    session_id = "hsess_paused_stop"
    run_task = asyncio.create_task(
        runtime.run(
            RunRequest(
                app=app_record,
                user_id="u_1",
                session_id=session_id,
                message={"role": "user", "parts": []},
            )
        )
    )
    await asyncio.wait_for(adapter.started.wait(), timeout=0.5)
    source_id = next(iter(runtime._active))
    pause_task = asyncio.create_task(runtime.pause_invocation(session_id, source_id))
    await asyncio.wait_for(adapter.interrupt_acknowledged.wait(), timeout=0.5)
    adapter.release_terminal.set()
    source = await asyncio.wait_for(pause_task, timeout=0.5)
    await asyncio.wait_for(run_task, timeout=0.5)
    terminal_events_before = store.read_invocation(
        (app_record.id, "u_1", session_id), source_id, after=-1
    )

    stopped = await runtime.cancel_invocation(session_id, source_id)

    session = runtime.get_session(app_record.id, "u_1", session_id)
    terminal_events_after = store.read_invocation(
        (app_record.id, "u_1", session_id), source_id, after=-1
    )
    assert stopped.status == source.status == "interrupted"
    assert session.controlState == "cancelled"
    assert session.supportsResume is False
    assert session.resumableInvocationId is None
    assert terminal_events_after == terminal_events_before


async def test_long_turn_renews_lease_and_blocks_second_request() -> None:
    store = MemoryStore()
    registry = HarnessRegistry(store=store)
    app_record = seed_codex(registry)
    adapter = _OneBlockingEventAdapter()
    rt = SessionRuntime(
        store=store,
        registry=registry,
        adapter=adapter,
        event_log=EventLog(store=store),
        lease_ttl_ms=80,
        lease_renew_interval_ms=20,
        turn_timeout_s=1.0,
    )
    req = RunRequest(
        app=app_record,
        user_id="u_1",
        session_id="hsess_long",
        message={"role": "user", "parts": []},
    )
    task = asyncio.create_task(rt.run(req))
    await asyncio.wait_for(adapter.started.wait(), timeout=0.3)
    await asyncio.sleep(0.12)  # > lease TTL, but renew should keep it alive.

    with pytest.raises(SessionBusyError):
        await rt.run(req)

    adapter.release.set()
    result = await asyncio.wait_for(task, timeout=0.5)
    assert result.invocation.status == "completed"


async def test_stale_holder_write_is_rejected_by_fencing(runtime: SessionRuntime) -> None:
    key = ("chrn_codex_default", "u_1", "hsess_fenced")
    first = runtime.store.acquire_lease(key, holder="run_a", ttl_ms=30)
    runtime.store._leases[key].expiresAtMs = 0  # type: ignore[attr-defined]
    runtime.store.acquire_lease(key, holder="run_b", ttl_ms=30)
    app = runtime.registry.resolve_app(Principal("p"), "chrn_codex_default")
    inv = runtime.store.put_invocation(
        InvocationRecord(
            id="inv_fenced",
            sessionId="hsess_fenced",
            appName=app.id,
            userId="u_1",
            turnId="turn_fenced",
        )
    )
    turn = runtime.store.put_turn(
        TurnRecord(id="turn_fenced", invocationId=inv.id, sessionId="hsess_fenced")
    )
    event = HarnessEvent(
        type="harness.text.delta",
        invocationId=inv.id,
        sessionId=inv.sessionId,
        turnId=turn.id,
        author=runtime.adapter.base,
        content={"role": "model", "parts": []},
        actions={},
    )
    with pytest.raises(LeaseFencingError):
        runtime._append_harness_event(event, app, inv, turn, key, "run_a", first.token)


async def test_adapter_timeout_terminalizes_without_pending_or_quota_leak() -> None:
    from haas.api import build_app

    token = "timeout-token"
    adapter = _NeverEndingAdapter()
    app = build_app(
        adapter=adapter,
        identity_tokens={
            token: Principal(principalId="p_1", tenantId="t1", userIds=frozenset({"u_1"}))
        },
        run_quota=1,
        session_lease_ttl_ms=50,
        session_lease_renew_interval_ms=10,
        session_turn_timeout_s=0.12,
    )
    runtime_api = app.state.runtime
    body = {
        "appName": "chrn_codex_default",
        "userId": "u_1",
        "sessionId": "hsess_timeout",
        "newMessage": {"role": "user", "parts": []},
    }
    headers = {"Authorization": f"Bearer {token}", "Idempotency-Key": "timeout-key"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/run", json=body, headers=headers)
        assert resp.status_code == 502
        assert resp.json()["haasError"]["code"] == "haas_adapter_error"
        assert runtime_api.store.quota_count("run:t1") == 0
        key_hash = hashlib.sha256(b"timeout-key").hexdigest()
        assert runtime_api.store.is_pending(key_hash) is False
        session = (
            await client.get(
                "/apps/chrn_codex_default/users/u_1/sessions/hsess_timeout",
                headers={"Authorization": f"Bearer {token}"},
            )
        ).json()
    assert session["events"][-1]["actions"]["stateDelta"] == {
        "status": "failed",
        "reason": "timeout",
    }
    assert session["state"]["status"] == "failed"
    assert session["state"]["reason"] == "timeout"
    assert adapter.stream_cancelled is True


async def test_renew_task_is_cancelled_after_normal_turn() -> None:
    store = MemoryStore()
    registry = HarnessRegistry(store=store)
    app_record = seed_codex(registry)
    rt = SessionRuntime(
        store=store,
        registry=registry,
        adapter=FakeAdapter(),
        event_log=EventLog(store=store),
        lease_ttl_ms=80,
        lease_renew_interval_ms=20,
        turn_timeout_s=1.0,
    )
    before = asyncio.all_tasks()
    await rt.run(
        RunRequest(
            app=app_record,
            user_id="u_1",
            session_id="hsess_clean",
            message={"role": "user", "parts": []},
        )
    )
    await asyncio.sleep(0)
    leaked = [
        task
        for task in asyncio.all_tasks()
        if task not in before and "_renew_lease_until_done" in repr(task.get_coro())
    ]
    assert leaked == []
