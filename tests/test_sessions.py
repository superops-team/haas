"""Session Runtime unit tests (specs/session-runtime/README.md)."""
import asyncio
import hashlib
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

from haas.events import EventLog
from haas.harnesses import FakeAdapter
from haas.harnesses.base import AdapterTurnResult, HarnessEvent, TurnHandle
from haas.identity import Principal
from haas.registry import HarnessRegistry, seed_codex
from haas.sessions import RunRequest, SessionBusyError, SessionNotFoundError, SessionRuntime
from haas.stores import (
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
        user_id="u_1", message={"role": "user", "parts": [{"text": "hi"}]},
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
                "completed", "failed", "incomplete", "cancelled"
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
    result = await rt.run(RunRequest(
        app=app_record,
        user_id="u_1",
        session_id="hsess_order",
        message={"role": "user", "parts": []},
    ))
    assert result.invocation.status == "completed"


async def test_session_busy(runtime: SessionRuntime) -> None:
    key = ("chrn_codex_default", "u_1", "hsess_1")
    runtime.store.acquire_lease(key, holder="other")
    req = RunRequest(
        app=runtime.registry.resolve_app(Principal("p"), "chrn_codex_default"),
        user_id="u_1", message={"role": "user", "parts": [{"text": "hi"}]}, session_id="hsess_1",
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
            id="inv_fenced", sessionId="hsess_fenced", appName=app.id,
            userId="u_1", turnId="turn_fenced"
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
        identity_tokens={token: Principal(principalId="p_1", tenantId="t1", userIds=frozenset({"u_1"}))},
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
        session = (await client.get(
            "/apps/chrn_codex_default/users/u_1/sessions/hsess_timeout",
            headers={"Authorization": f"Bearer {token}"},
        )).json()
    assert session["events"][-1]["actions"]["stateDelta"] == {
        "status": "failed", "reason": "timeout"
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
    await rt.run(RunRequest(
        app=app_record,
        user_id="u_1",
        session_id="hsess_clean",
        message={"role": "user", "parts": []},
    ))
    await asyncio.sleep(0)
    leaked = [
        task for task in asyncio.all_tasks()
        if task not in before and "_renew_lease_until_done" in repr(task.get_coro())
    ]
    assert leaked == []
