"""Session Runtime unit tests (specs/session-runtime/README.md)."""

import asyncio
import hashlib
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

from haas.artifacts import ArtifactNotFoundError, ArtifactStore
from haas.events import EventLog
from haas.harnesses import FakeAdapter
from haas.harnesses.base import (
    AdapterTurnResult,
    ArtifactRef,
    CancelResult,
    CancelTurnRequest,
    HarnessEvent,
    ListArtifactsRequest,
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
    _ActiveTurn,
)
from haas.stores import (
    ApprovalRecord,
    CanonicalEventRecord,
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


async def test_terminal_is_persisted_before_artifact_event_is_yielded() -> None:
    class TerminalArtifactAdapter(FakeAdapter):
        async def stream_events(
            self, handle: TurnHandle
        ) -> AsyncIterator[HarnessEvent]:
            yield HarnessEvent(
                type="harness.turn.completed",
                invocationId=handle.invocationId,
                sessionId=handle.sessionId,
                turnId=handle.turnId,
                author="fake",
                content={"role": "model", "parts": []},
                actions={"stateDelta": {"status": "completed"}},
            )

        async def list_artifacts(
            self, request: ListArtifactsRequest
        ) -> list[ArtifactRef]:
            return [
                ArtifactRef(
                    name="report.md",
                    path="output/report.md",
                    content=b"# Report",
                )
            ]

    store = MemoryStore()
    registry = HarnessRegistry(store=store)
    app = seed_codex(registry)
    runtime = SessionRuntime(
        store=store,
        registry=registry,
        adapter=TerminalArtifactAdapter(),
        event_log=EventLog(store=store),
        artifacts=ArtifactStore(),
    )
    stream = runtime.run_stream(
        RunRequest(
            app=app,
            user_id="u_1",
            session_id="hsess_disconnect",
            message={"role": "user", "parts": []},
            principal_id="p_1",
        )
    )

    artifact_event = await anext(stream)
    invocation = store.get_invocation(artifact_event.invocationId)
    persisted = store.read_invocation(
        (app.id, "u_1", "hsess_disconnect"),
        artifact_event.invocationId,
    )
    assert artifact_event.type == "haas.artifact.registered"
    assert invocation is not None and invocation.status == "completed"
    assert [event.type for event in persisted[-2:]] == [
        "haas.artifact.registered",
        "haas.turn.completed",
    ]
    await stream.aclose()


async def test_followup_resumes_from_persisted_native_session_ref() -> None:
    store = MemoryStore()
    registry = HarnessRegistry(store=store)
    app = seed_codex(registry)
    adapter = _NativeRefAdapter()
    rt = SessionRuntime(
        store=store,
        registry=registry,
        adapter=adapter,
        event_log=EventLog(store=store),
    )

    first = await rt.run(
        RunRequest(
            app=app,
            user_id="u_1",
            session_id="hsess_native",
            message={"role": "user", "parts": [{"text": "first"}]},
        )
    )
    assert first.session.nativeSessionRef == {
        "adapterId": "fake-adapter",
        "threadId": "thr_persisted",
    }
    assert first.invocation.nativeTurnRef is not None
    assert first.invocation.nativeTurnRef["threadId"] == "thr_persisted"

    second = await rt.run(
        RunRequest(
            app=app,
            user_id="u_1",
            session_id="hsess_native",
            message={"role": "user", "parts": [{"text": "follow up"}]},
        )
    )
    assert adapter.prepares == 1
    assert adapter.resumes == [{"adapterId": "fake-adapter", "threadId": "thr_persisted"}]
    assert second.invocation.nativeTurnRef is not None
    assert second.invocation.nativeTurnRef["threadId"] == "thr_persisted"


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


def test_delete_session_makes_artifacts_unreachable() -> None:
    store = MemoryStore()
    registry = HarnessRegistry(store=store)
    app = seed_codex(registry)
    artifacts = ArtifactStore()
    runtime = SessionRuntime(
        store=store,
        registry=registry,
        adapter=FakeAdapter(),
        event_log=EventLog(store=store),
        artifacts=artifacts,
    )
    store.put_session(SessionRecord(id="hsess_artifacts", appName=app.id, userId="u_1"))
    record = artifacts.register(
        "hsess_artifacts",
        "output/report.md",
        b"report",
        owner_principal_id="p_1",
        app_name=app.id,
        user_id="u_1",
    )

    runtime.delete_session(app.id, "u_1", "hsess_artifacts")

    with pytest.raises(ArtifactNotFoundError):
        artifacts.read_content(record.id, owner_principal_id="p_1")


def test_delete_session_preserves_same_id_artifacts_in_another_adk_scope() -> None:
    store = MemoryStore()
    registry = HarnessRegistry(store=store)
    app = seed_codex(registry)
    artifacts = ArtifactStore()
    runtime = SessionRuntime(
        store=store,
        registry=registry,
        adapter=FakeAdapter(),
        event_log=EventLog(store=store),
        artifacts=artifacts,
    )
    for user_id in ("u_1", "u_2"):
        store.put_session(SessionRecord(id="shared", appName=app.id, userId=user_id))
    first = artifacts.register(
        "shared",
        "output/report.md",
        b"first",
        owner_principal_id="p_1",
        app_name=app.id,
        user_id="u_1",
    )
    second = artifacts.register(
        "shared",
        "output/report.md",
        b"second",
        owner_principal_id="p_1",
        app_name=app.id,
        user_id="u_2",
    )

    runtime.delete_session(app.id, "u_1", "shared")

    with pytest.raises(ArtifactNotFoundError):
        artifacts.read_content(first.id, owner_principal_id="p_1")
    assert artifacts.read_content(second.id, owner_principal_id="p_1") == b"second"


def test_delete_session_revokes_model_proxy_capability(runtime: SessionRuntime) -> None:
    class FakeModelProxy:
        def __init__(self) -> None:
            self.revoked: list[str] = []

        def revoke_session(self, session_id: str) -> None:
            self.revoked.append(session_id)

    model_proxy = FakeModelProxy()
    runtime.model_proxy = model_proxy
    runtime.store.put_session(
        SessionRecord(id="hsess_proxy_revoke", appName="chrn_codex_default", userId="u_1")
    )

    runtime.delete_session("chrn_codex_default", "u_1", "hsess_proxy_revoke")

    assert model_proxy.revoked == ["hsess_proxy_revoke"]


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


class _NativeRefAdapter(FakeAdapter):
    def __init__(self) -> None:
        self.prepares = 0
        self.resumes: list[dict[str, Any]] = []

    async def prepare_session(self, request):
        self.prepares += 1
        return PreparedSession(
            sessionId=request.sessionId,
            nativeRef={"adapterId": self.adapter_id, "threadId": "thr_persisted"},
        )

    async def resume_session(self, request: ResumeSessionRequest) -> PreparedSession:
        self.resumes.append(dict(request.opaque))
        return PreparedSession(sessionId=request.sessionId, nativeRef=dict(request.opaque))

    async def start_turn(self, request: StartTurnRequest) -> TurnHandle:
        thread_id = "thr_persisted"
        if self.resumes:
            thread_id = str(self.resumes[-1]["threadId"])
        return TurnHandle(
            turnId=request.turnId,
            sessionId=request.sessionId,
            invocationId=request.invocationId,
            opaque={"threadId": thread_id, "codexTurnId": f"codex_{request.turnId}"},
        )


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
        assert resp.status_code == 200
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
        "reason": "long_task_deadline_exceeded",
        "code": "haas_request_timeout",
        "retryable": True,
    }
    assert session["state"]["status"] == "failed"
    assert session["state"]["reason"] == "long_task_deadline_exceeded"
    assert session["state"]["code"] == "haas_request_timeout"
    assert session["state"]["retryable"] is True
    assert adapter.stream_cancelled is True


async def test_adapter_timeout_without_idempotency_key_returns_terminal_events() -> None:
    from haas.api import build_app

    token = "timeout-token"
    app = build_app(
        adapter=_NeverEndingAdapter(),
        identity_tokens={
            token: Principal(principalId="p_1", tenantId="t1", userIds=frozenset({"u_1"}))
        },
        session_lease_ttl_ms=50,
        session_lease_renew_interval_ms=10,
        session_turn_timeout_s=0.12,
    )
    body = {
        "appName": "chrn_codex_default",
        "userId": "u_1",
        "sessionId": "hsess_timeout_no_key",
        "newMessage": {"role": "user", "parts": []},
    }

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/run", json=body, headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    assert resp.headers["X-HaaS-Session-ID"] == "hsess_timeout_no_key"
    assert resp.headers["X-HaaS-Invocation-ID"].startswith("inv_")
    events = resp.json()
    assert events[-1]["actions"]["stateDelta"]["code"] == "haas_request_timeout"
    assert events[-1]["actions"]["stateDelta"]["reason"] == "long_task_deadline_exceeded"


def test_session_runtime_defaults_to_24h_turn_deadline(runtime: SessionRuntime) -> None:
    assert runtime.turn_timeout_s == 86_400


def test_session_runtime_clamps_direct_timeout_to_24h() -> None:
    store = MemoryStore()
    runtime = SessionRuntime(
        store=store,
        registry=HarnessRegistry(store=store),
        adapter=FakeAdapter(),
        event_log=EventLog(store=store),
        turn_timeout_s=172_800,
    )

    assert runtime.turn_timeout_s == 86_400


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


async def test_stream_deadline_survives_consumer_task_handoff() -> None:
    """The HTTP first read and producer reads must share a deadline, not a task."""

    class WaitingApprovalAdapter(FakeAdapter):
        async def stream_events(self, handle: TurnHandle) -> AsyncIterator[HarnessEvent]:
            store.put_approval(
                ApprovalRecord(
                    id="appr_deadline",
                    sessionId=handle.sessionId,
                    invocationId=handle.invocationId,
                    turnId=handle.turnId,
                )
            )
            yield HarnessEvent(
                type="harness.turn.started",
                invocationId=handle.invocationId,
                sessionId=handle.sessionId,
                turnId=handle.turnId,
                author="fake",
                content={"role": "model", "parts": []},
                actions={},
            )
            await asyncio.Event().wait()

    store = MemoryStore()
    registry = HarnessRegistry(store=store)
    app = seed_codex(registry)
    runtime = SessionRuntime(
        store=store,
        registry=registry,
        adapter=WaitingApprovalAdapter(),
        event_log=EventLog(store=store),
        lease_ttl_ms=50,
        lease_renew_interval_ms=10,
        turn_timeout_s=0.15,
    )
    stream = runtime.run_stream(
        RunRequest(
            app=app,
            user_id="u_1",
            session_id="hsess_deadline",
            message={"parts": []},
        )
    )
    owner = asyncio.current_task()
    initial_cancels = owner.cancelling()
    first = await anext(stream)

    async def consume():
        events = []
        try:
            async for event in stream:
                events.append(event)
        except AdapterTurnError:
            pass
        return events

    task = asyncio.create_task(consume())
    try:
        events = await asyncio.wait_for(asyncio.shield(task), 1)
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await stream.aclose()
    assert owner.cancelling() == initial_cancels, "execution deadline cancelled HTTP owner"
    assert [event.type for event in events] == ["haas.turn.failed"]
    assert store.get_invocation(first.invocationId).status == "failed"
    assert store.get_approval("appr_deadline").status == "cancelled"
    assert events[0].haas["safeReason"] == "long_task_deadline_exceeded"
    assert events[0].haas["code"] == "haas_request_timeout"
    assert events[0].haas["retryable"] is True


async def test_adapter_turn_start_error_maps_to_terminal(runtime: SessionRuntime) -> None:
    from haas.harnesses.base import AdapterTurnStartError

    class BoomStartAdapter(FakeAdapter):
        async def start_turn(self, request: StartTurnRequest) -> TurnHandle:
            raise AdapterTurnStartError(
                code="haas_request_timeout", retryable=True, detail="boom"
            )

    runtime.adapter = BoomStartAdapter()
    stream = runtime.run_stream(
        RunRequest(
            app=runtime.registry.resolve_default_app(Principal("p")),
            user_id="u_1",
            message={"role": "user", "parts": [{"text": "hi"}]},
        )
    )
    seen: list[CanonicalEventRecord] = []
    with pytest.raises(AdapterTurnError):
        async for event in stream:
            seen.append(event)

    assert seen, "expected a failure terminal event before the raise"
    terminal = seen[-1]
    delta = terminal.actions["stateDelta"]
    assert delta["status"] == "failed"
    assert delta["code"] == "haas_request_timeout"
    assert delta["retryable"] is True


async def test_unmapped_start_exception_emits_structured_code(runtime: SessionRuntime) -> None:
    """A non-AdapterTurnStartError escaping start_turn MUST converge on a terminal
    event with a structured haas_* code (not the legacy bare code="failed")."""

    class GenericStartError(Exception):
        pass

    class FailingStartAdapter(FakeAdapter):
        async def start_turn(self, request: StartTurnRequest) -> TurnHandle:
            raise GenericStartError("raw prompt or internal detail must not leak")

    runtime.adapter = FailingStartAdapter()
    stream = runtime.run_stream(
        RunRequest(
            app=runtime.registry.resolve_default_app(Principal("p")),
            user_id="u_1",
            message={"role": "user", "parts": [{"text": "hi"}]},
        )
    )
    seen: list[CanonicalEventRecord] = []
    with pytest.raises(AdapterTurnError):
        async for event in stream:
            seen.append(event)
    assert seen, "expected a failure terminal event"
    terminal = seen[-1]
    delta = terminal.actions["stateDelta"]
    assert delta["status"] == "failed"
    assert delta["code"] == "haas_adapter_error"
    # reason is the safe exception class name, not the raw message
    assert delta["reason"] == "GenericStartError"
    assert "raw prompt" not in str(terminal.actions)


# --- __post_init__ validation / timeout normalization --------------------


def test_post_init_rejects_nonpositive_lease_ttl() -> None:
    store = MemoryStore()
    with pytest.raises(ValueError, match="lease_ttl_ms must be positive"):
        SessionRuntime(
            store=store,
            registry=HarnessRegistry(store=store),
            adapter=FakeAdapter(),
            event_log=EventLog(store=store),
            lease_ttl_ms=0,
        )


def test_post_init_rejects_nonpositive_lease_renew_interval() -> None:
    store = MemoryStore()
    with pytest.raises(ValueError, match="lease_renew_interval_ms must be positive"):
        SessionRuntime(
            store=store,
            registry=HarnessRegistry(store=store),
            adapter=FakeAdapter(),
            event_log=EventLog(store=store),
            lease_renew_interval_ms=0,
        )


def test_post_init_rejects_renew_interval_too_close_to_ttl() -> None:
    store = MemoryStore()
    with pytest.raises(
        ValueError, match="lease_renew_interval_ms must be less than half"
    ):
        SessionRuntime(
            store=store,
            registry=HarnessRegistry(store=store),
            adapter=FakeAdapter(),
            event_log=EventLog(store=store),
            lease_ttl_ms=100,
            lease_renew_interval_ms=60,
        )


def test_post_init_rejects_turn_timeout_shorter_than_lease() -> None:
    store = MemoryStore()
    with pytest.raises(ValueError, match="turn_timeout_s must be greater"):
        SessionRuntime(
            store=store,
            registry=HarnessRegistry(store=store),
            adapter=FakeAdapter(),
            event_log=EventLog(store=store),
            lease_ttl_ms=10_000,
            lease_renew_interval_ms=1_000,
            turn_timeout_s=5.0,
        )


def test_effective_timeout_rejects_non_finite_and_non_positive() -> None:
    with pytest.raises(ValueError, match="must be finite"):
        SessionRuntime._effective_timeout_seconds(float("nan"), 1.0)
    with pytest.raises(ValueError, match="must be positive"):
        SessionRuntime._effective_timeout_seconds(-3.0, 1.0)


# --- update_policy / domain validation / apply_desired_policy -----------


_WS = {"mode": "workspace-write", "root": "/workspace", "writableRoots": ["/workspace"]}
_NET = {"defaultAction": "deny", "allow": ["example.com"]}
_TOOLS = {"disabled": [], "approvalMode": "on-request"}


def test_update_policy_rejects_revision_conflict(runtime: SessionRuntime) -> None:
    from haas.sessions import PolicyRevisionConflictError

    s = SessionRecord(id="hs", appName="chrn_codex_default", userId="u_1")
    runtime.store.put_session(s)
    with pytest.raises(PolicyRevisionConflictError):
        runtime.update_policy(s, expected_revision=999, delta={"workspace": _WS})


def test_update_policy_rejects_empty_or_unknown_domain(runtime: SessionRuntime) -> None:
    from haas.sessions import PolicyUpdateInvalidError

    s = SessionRecord(id="hs", appName="chrn_codex_default", userId="u_1")
    runtime.store.put_session(s)
    with pytest.raises(PolicyUpdateInvalidError):
        runtime.update_policy(s, expected_revision=1, delta={})
    with pytest.raises(PolicyUpdateInvalidError):
        runtime.update_policy(s, expected_revision=1, delta={"hax": {}})


def test_update_policy_idle_session_applies_instantly(runtime: SessionRuntime) -> None:
    s = SessionRecord(id="hs", appName="chrn_codex_default", userId="u_1")
    runtime.store.put_session(s)
    out = runtime.update_policy(s, expected_revision=1, delta={"workspace": _WS})
    assert out.desiredRevision == 2
    assert out.appliedRevision == 2
    assert out.policyStatus == "applied"
    assert out.pendingPolicyUpdate is None
    assert out.lastPolicyUpdateResult == {"revision": 2, "status": "applied"}


def test_update_policy_running_session_marks_pending(runtime: SessionRuntime) -> None:
    s = SessionRecord(id="hs", appName="chrn_codex_default", userId="u_1")
    s.controlState = "running"
    runtime.store.put_session(s)
    out = runtime.update_policy(s, expected_revision=1, delta={"network": _NET})
    assert out.desiredRevision == 2
    assert out.appliedRevision == 1
    assert out.policyStatus == "pending"
    assert out.pendingPolicyUpdate is not None


@pytest.mark.parametrize(
    "domain, value",
    [
        ("workspace", "notadict"),
        ("workspace", {"mode": "bogus", "root": "/x", "writableRoots": []}),
        ("workspace", {"mode": "read-only", "root": "relative", "writableRoots": []}),
        ("workspace", {"mode": "read-only", "root": "/x", "writableRoots": ["relative"]}),
        ("network", {"defaultAction": "deny"}),
        ("network", {"defaultAction": "bogus", "allow": []}),
        ("tools", {"disabled": []}),
        ("tools", {"disabled": [], "approvalMode": "bogus"}),
    ],
)
def test_validate_policy_domain_rejects_bad_shapes(domain: str, value: Any) -> None:
    from haas.sessions import PolicyUpdateInvalidError

    with pytest.raises(PolicyUpdateInvalidError):
        SessionRuntime._validate_policy_domain(domain, value)


def test_validate_policy_domain_accepts_valid_network_and_tools() -> None:
    SessionRuntime._validate_policy_domain("network", _NET)
    SessionRuntime._validate_policy_domain("tools", _TOOLS)


# --- _drive early validation paths --------------------------------------


async def test_run_rejects_session_with_pending_config_update(runtime: SessionRuntime) -> None:
    from haas.sessions import SessionBusyError

    s = SessionRecord(id="hs_pending", appName="chrn_codex_default", userId="u_1")
    s.appliedRevision = 1
    s.desiredRevision = 2
    runtime.store.put_session(s)
    req = RunRequest(
        app=runtime.registry.resolve_app(Principal("p"), "chrn_codex_default"),
        user_id="u_1",
        message={"role": "user", "parts": []},
        session_id="hs_pending",
    )
    with pytest.raises(SessionBusyError, match="configuration_update_pending"):
        await runtime.run(req)


async def test_run_requires_resume_for_paused_session(runtime: SessionRuntime) -> None:
    from haas.sessions import ResumeRequiredError

    s = SessionRecord(id="hs_paused", appName="chrn_codex_default", userId="u_1")
    s.controlState = "paused"
    runtime.store.put_session(s)
    req = RunRequest(
        app=runtime.registry.resolve_app(Principal("p"), "chrn_codex_default"),
        user_id="u_1",
        message={"role": "user", "parts": []},
        session_id="hs_paused",
    )
    with pytest.raises(ResumeRequiredError):
        await runtime.run(req)


async def test_run_rejects_continue_from_non_resumable(runtime: SessionRuntime) -> None:
    from haas.sessions import InvocationNotResumableError

    s = SessionRecord(id="hs_idle", appName="chrn_codex_default", userId="u_1")
    runtime.store.put_session(s)
    req = RunRequest(
        app=runtime.registry.resolve_app(Principal("p"), "chrn_codex_default"),
        user_id="u_1",
        message={"role": "user", "parts": []},
        session_id="hs_idle",
        continued_from_invocation_id="inv_ghost",
    )
    with pytest.raises(InvocationNotResumableError):
        await runtime.run(req)


async def test_run_materials_effective_profile(runtime: SessionRuntime) -> None:
    result = await runtime.run(
        RunRequest(
            app=runtime.registry.resolve_default_app(Principal("p")),
            user_id="u_1",
            message={"role": "user", "parts": []},
            effective_profile={"provider": {"model": "gpt-x"}},
        )
    )
    assert result.session.effectiveProfile == {"provider": {"model": "gpt-x"}}


# --- model proxy begin/end -----------------------------------------------


class _RecordingModelProxy:
    def __init__(self) -> None:
        self.began: list[str] = []
        self.ended: list[str] = []

    async def begin(self, app: Any, invocation: Any, effective: Any) -> dict[str, str]:
        self.began.append(invocation.id)
        return {"K": "v"}

    def end(self, invocation: Any, credentials: dict[str, str]) -> None:
        self.ended.append(invocation.id)


async def test_model_proxy_begin_and_end_are_invoked(runtime: SessionRuntime) -> None:
    proxy = _RecordingModelProxy()
    runtime.model_proxy = proxy
    result = await runtime.run(
        RunRequest(
            app=runtime.registry.resolve_default_app(Principal("p")),
            user_id="u_1",
            message={"role": "user", "parts": []},
        )
    )
    assert proxy.began == [result.invocation.id]
    assert proxy.ended == [result.invocation.id]


# --- adapter error mapping ------------------------------------------------


class _BoomStartTurnAdapter(FakeAdapter):
    async def start_turn(self, request: StartTurnRequest) -> TurnHandle:
        raise RuntimeError("start blew up")


async def test_generic_start_turn_error_persists_failure_and_idles_session(
    runtime: SessionRuntime,
) -> None:
    runtime.adapter = _BoomStartTurnAdapter()
    stream = runtime.run_stream(
        RunRequest(
            app=runtime.registry.resolve_default_app(Principal("p")),
            user_id="u_1",
            session_id="hs_boom",
            message={"role": "user", "parts": []},
        )
    )
    seen: list[CanonicalEventRecord] = []
    with pytest.raises(AdapterTurnError):
        async for event in stream:
            seen.append(event)
    assert seen
    session = runtime.get_session("chrn_codex_default", "u_1", "hs_boom")
    assert session.controlState == "idle"
    inv = runtime.store.get_invocation(seen[-1].invocationId)
    assert inv is not None and inv.status == "failed"


class _FencePrepareAdapter(FakeAdapter):
    async def prepare_session(self, request):
        raise LeaseFencingError()


async def test_fencing_during_adapter_call_maps_to_adapter_error(
    runtime: SessionRuntime,
) -> None:
    runtime.adapter = _FencePrepareAdapter()
    stream = runtime.run_stream(
        RunRequest(
            app=runtime.registry.resolve_default_app(Principal("p")),
            user_id="u_1",
            session_id="hs_fence",
            message={"role": "user", "parts": []},
        )
    )
    with pytest.raises(AdapterTurnError):
        async for _ in stream:
            pass


# --- finalize-returned terminal publishes artifacts (no streamed terminal)


class _ArtifactListingAdapter(FakeAdapter):
    async def list_artifacts(self, request: ListArtifactsRequest) -> list[ArtifactRef]:
        return [
            ArtifactRef(
                name="report.md",
                path="output/report.md",
                content=b"# Report",
                mediaType="text/markdown",
            )
        ]


async def test_finalize_terminal_publishes_artifacts_when_no_streamed_terminal() -> None:
    store = MemoryStore()
    registry = HarnessRegistry(store=store)
    app = seed_codex(registry)
    rt = SessionRuntime(
        store=store,
        registry=registry,
        adapter=_ArtifactListingAdapter(),
        event_log=EventLog(store=store),
        artifacts=ArtifactStore(),
    )
    result = await rt.run(
        RunRequest(
            app=app,
            user_id="u_1",
            session_id="hs_art",
            message={"role": "user", "parts": []},
        )
    )
    types = [e.type for e in result.events]
    assert "haas.artifact.registered" in types
    assert result.events[-1].type == "haas.turn.completed"


# --- reconcile_invocation_readback ---------------------------------------


def _seed_durable_running(runtime: SessionRuntime, app_id: str = "chrn_codex_default"):
    session = SessionRecord(id="hs_durable", appName=app_id, userId="u_1")
    runtime.store.put_session(session)
    inv = runtime.store.put_invocation(
        InvocationRecord(
            id="inv_durable",
            sessionId="hs_durable",
            appName=app_id,
            userId="u_1",
            turnId="turn_durable",
            status="running",
        )
    )
    turn = runtime.store.put_turn(
        TurnRecord(id="turn_durable", invocationId=inv.id, sessionId="hs_durable")
    )
    return session, inv, turn


def test_reconcile_readback_unknown_invocation_raises(runtime: SessionRuntime) -> None:
    from haas.sessions import InvocationNotFoundError

    with pytest.raises(InvocationNotFoundError):
        runtime.reconcile_invocation_readback("hs", "inv_missing")


def test_reconcile_readback_terminal_invocation_is_returned(runtime: SessionRuntime) -> None:
    session, inv, turn = _seed_durable_running(runtime)
    inv.status = "completed"
    runtime.store.put_invocation(inv)
    out = runtime.reconcile_invocation_readback(session.id, inv.id)
    assert out.status == "completed"


async def test_reconcile_readback_active_invocation_is_untouched(runtime: SessionRuntime) -> None:
    session, inv, turn = _seed_durable_running(runtime)
    runtime._active[inv.id] = _make_active(inv.id, session.id)
    out = runtime.reconcile_invocation_readback(session.id, inv.id)
    assert out.status == "running"
    del runtime._active[inv.id]


def _make_active(invocation_id: str, session_id: str) -> "_ActiveTurn":
    import asyncio

    return _ActiveTurn(
        turn_id="turn_durable",
        session_id=session_id,
        terminal_status=asyncio.get_running_loop().create_future(),
    )


def test_reconcile_readback_sidecar_restart_terminates_incomplete(runtime: SessionRuntime) -> None:
    session, inv, turn = _seed_durable_running(runtime)
    out = runtime.reconcile_invocation_readback(session.id, inv.id)
    assert out.status == "incomplete"
    refreshed = runtime.get_session(session.appName, "u_1", session.id)
    assert refreshed.controlState == "idle"


def test_reconcile_readback_lease_conflict_returns_running(runtime: SessionRuntime) -> None:
    session, inv, turn = _seed_durable_running(runtime)
    runtime.store.acquire_lease((session.appName, "u_1", session.id), holder="other")
    out = runtime.reconcile_invocation_readback(session.id, inv.id)
    assert out.status == "running"


def test_reconcile_readback_completes_idempotency_from_history(runtime: SessionRuntime) -> None:
    session, inv, turn = _seed_durable_running(runtime)
    key_hash = "kh_test"
    inv.idempotencyKeyHash = key_hash
    inv.idempotencyExpiresAtMs = 9_999_999_999_999
    runtime.store.put_invocation(inv)
    runtime.store.reserve(key_hash, "reqhash")
    out = runtime.reconcile_invocation_readback(session.id, inv.id)
    assert out.status == "incomplete"
    assert runtime.store.is_pending(key_hash) is False


# --- pause_invocation edge cases -----------------------------------------


async def test_pause_unknown_invocation_raises(runtime: SessionRuntime) -> None:
    from haas.sessions import InvocationNotFoundError

    with pytest.raises(InvocationNotFoundError):
        await runtime.pause_invocation("hs", "inv_missing")


async def test_pause_interrupted_invocation_is_idempotent(runtime: SessionRuntime) -> None:
    session, inv, turn = _seed_durable_running(runtime)
    inv.status = "interrupted"
    runtime.store.put_invocation(inv)
    out = await runtime.pause_invocation(session.id, inv.id)
    assert out.status == "interrupted"


async def test_pause_completed_invocation_not_running(runtime: SessionRuntime) -> None:
    from haas.sessions import InvocationNotRunningError

    session, inv, turn = _seed_durable_running(runtime)
    inv.status = "completed"
    runtime.store.put_invocation(inv)
    with pytest.raises(InvocationNotRunningError):
        await runtime.pause_invocation(session.id, inv.id)


async def test_pause_running_invocation_without_active_turn_not_running(
    runtime: SessionRuntime,
) -> None:
    from haas.sessions import InvocationNotRunningError

    session, inv, turn = _seed_durable_running(runtime)
    # invocation stays "running" but is not in _active
    with pytest.raises(InvocationNotRunningError):
        await runtime.pause_invocation(session.id, inv.id)


async def test_pause_rejects_when_control_intent_already_cancel(
    runtime: SessionRuntime,
) -> None:
    from haas.sessions import InvocationNotRunningError

    session, inv, turn = _seed_durable_running(runtime)
    active = _make_active(inv.id, session.id)
    active.control_intent = "cancel"
    runtime._active[inv.id] = active
    try:
        with pytest.raises(InvocationNotRunningError):
            await runtime.pause_invocation(session.id, inv.id)
    finally:
        del runtime._active[inv.id]


# --- continue_stream edge cases ------------------------------------------


async def test_continue_unknown_source_raises(runtime: SessionRuntime) -> None:
    from haas.sessions import InvocationNotFoundError

    with pytest.raises(InvocationNotFoundError):
        async for _ in runtime.continue_stream("hs", "inv_missing"):
            pass


async def test_continue_non_resumable_source_raises(runtime: SessionRuntime) -> None:
    from haas.sessions import InvocationNotResumableError

    session, inv, turn = _seed_durable_running(runtime)
    # status running (not interrupted), session idle
    with pytest.raises(InvocationNotResumableError):
        async for _ in runtime.continue_stream(session.id, inv.id):
            pass


# --- cancel_invocation edge cases ----------------------------------------


async def test_cancel_unknown_invocation_raises(runtime: SessionRuntime) -> None:
    from haas.sessions import InvocationNotFoundError

    with pytest.raises(InvocationNotFoundError):
        await runtime.cancel_invocation("hs", "inv_missing")


async def test_cancel_completed_invocation_returns_as_is(runtime: SessionRuntime) -> None:
    session, inv, turn = _seed_durable_running(runtime)
    inv.status = "completed"
    runtime.store.put_invocation(inv)
    out = await runtime.cancel_invocation(session.id, inv.id)
    assert out.status == "completed"


async def test_cancel_interrupted_resumable_session_marks_cancelled(
    runtime: SessionRuntime,
) -> None:
    session, inv, turn = _seed_durable_running(runtime)
    inv.status = "interrupted"
    runtime.store.put_invocation(inv)
    session.controlState = "paused"
    session.supportsResume = True
    session.resumableInvocationId = inv.id
    runtime.store.put_session(session)
    out = await runtime.cancel_invocation(session.id, inv.id)
    assert out.status == "interrupted"
    refreshed = runtime.get_session(session.appName, "u_1", session.id)
    assert refreshed.controlState == "cancelled"
    assert refreshed.supportsResume is False


async def test_cancel_durable_running_invocation_terminalizes_without_active(
    runtime: SessionRuntime,
) -> None:
    session, inv, turn = _seed_durable_running(runtime)
    out = await runtime.cancel_invocation(session.id, inv.id)
    assert out.status == "cancelled"
    refreshed = runtime.get_session(session.appName, "u_1", session.id)
    assert refreshed.controlState == "idle"


# --- _read_invocation missing --------------------------------------------


def test_read_invocation_missing_raises(runtime: SessionRuntime) -> None:
    with pytest.raises(RuntimeError, match="invocation not found"):
        runtime._read_invocation("inv_nope")


# --- artifact publication resilience paths -------------------------------


class _StreamingTerminalWithArtifacts(_StreamingTerminalAdapter):
    def __init__(self, refs: list[ArtifactRef]) -> None:
        self._refs = refs

    async def list_artifacts(self, request: ListArtifactsRequest) -> list[ArtifactRef]:
        return self._refs


async def test_artifact_publication_skips_non_bytes_and_bad_paths(runtime: SessionRuntime) -> None:
    runtime.artifacts = ArtifactStore()
    runtime.adapter = _StreamingTerminalWithArtifacts(
        [
            ArtifactRef(name="no", path="output/none.bin", content=None),
            ArtifactRef(name="bad", path="../etc/passwd", content=b"x"),
            ArtifactRef(name="ok", path="output/a.md", content=b"# a"),
            ArtifactRef(name="ok2", path="output/a.md", content=b"# a"),  # duplicate
        ]
    )
    result = await runtime.run(
        RunRequest(
            app=runtime.registry.resolve_default_app(Principal("p")),
            user_id="u_1",
            session_id="hs_artresilient",
            message={"role": "user", "parts": []},
        )
    )
    assert result.invocation.status == "completed"
    registered = [e for e in result.events if e.type == "haas.artifact.registered"]
    assert len(registered) == 1


async def test_artifact_listing_failure_is_best_effort(runtime: SessionRuntime) -> None:
    runtime.artifacts = ArtifactStore()

    class Boom(_StreamingTerminalAdapter):
        async def list_artifacts(self, request: ListArtifactsRequest) -> list[ArtifactRef]:
            raise RuntimeError("list failed")

    runtime.adapter = Boom()
    result = await runtime.run(
        RunRequest(
            app=runtime.registry.resolve_default_app(Principal("p")),
            user_id="u_1",
            session_id="hs_artboom",
            message={"role": "user", "parts": []},
        )
    )
    assert result.invocation.status == "completed"


# --- unsupported native interrupt ----------------------------------------


class _UnsupportedInterruptAdapter(_AcknowledgedInterruptAdapter):
    async def cancel_turn(self, request: CancelTurnRequest) -> CancelResult:
        self.interrupt_acknowledged.set()
        return CancelResult(status="unsupported")


async def test_pause_unsupported_native_interrupt_maps_to_adapter_error() -> None:
    store = MemoryStore()
    registry = HarnessRegistry(store=store)
    app = seed_codex(registry)
    adapter = _UnsupportedInterruptAdapter()
    runtime = SessionRuntime(
        store=store, registry=registry, adapter=adapter, event_log=EventLog(store=store)
    )
    session_id = "hs_unsupported"
    run_task = asyncio.create_task(
        runtime.run(
            RunRequest(
                app=app, user_id="u_1", session_id=session_id, message={"role": "user", "parts": []}
            )
        )
    )
    await asyncio.wait_for(adapter.started.wait(), timeout=0.5)
    invocation_id = next(iter(runtime._active))
    with pytest.raises(AdapterTurnError):
        await runtime.pause_invocation(session_id, invocation_id)
    adapter.release_terminal.set()
    await asyncio.wait_for(run_task, timeout=0.5)
