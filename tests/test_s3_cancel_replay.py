"""S3 cancel + session replay verification (functional cases)."""

import json

import pytest
from fastapi.testclient import TestClient

from haas.api import build_app
from haas.events import EventLog
from haas.harnesses.fake import SlowFakeAdapter
from haas.identity import Principal
from haas.registry import HarnessRegistry, seed_codex
from haas.sessions import InvocationNotFoundError, RunRequest, SessionRuntime
from haas.stores import (
    InvocationRecord,
    MemoryStore,
    SessionRecord,
    SQLiteStore,
    TurnRecord,
)

# Cancel + SSE replay are part of the ADK/HaaS protocol contract.
pytestmark = pytest.mark.adk

TOKEN = "s3-token"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}


def make_app() -> object:
    return build_app(
        adapter=SlowFakeAdapter(delay=0.0),
        identity_tokens={TOKEN: Principal(principalId="p_1", tenantId="t1")},
    )


async def test_session_runtime_cancel_running_invocation() -> None:
    store = MemoryStore()
    registry = HarnessRegistry(store=store)
    app = seed_codex(registry)
    runtime = SessionRuntime(
        store=store,
        registry=registry,
        adapter=SlowFakeAdapter(delay=0.05),
        event_log=EventLog(store=store),
    )
    req = RunRequest(
        app=app,
        user_id="u_1",
        session_id="hsess_c",
        message={"role": "user", "parts": [{"text": "hi"}]},
    )
    stream = runtime.run_stream(req)
    first = await stream.__anext__()
    invocation_id = first.invocationId

    await runtime.cancel_invocation("hsess_c", invocation_id)
    events = [first] + [event async for event in stream]

    assert events[-1].actions["stateDelta"]["status"] == "cancelled"
    assert store.get_invocation(invocation_id).status == "cancelled"


async def test_cancel_persisted_running_invocation_after_restart(tmp_path) -> None:
    path = tmp_path / "restart-cancel.db"
    with SQLiteStore(path) as first_store:
        first_registry = HarnessRegistry(store=first_store)
        app = seed_codex(first_registry)
        first_store.put_session(
            SessionRecord(id="hsess_restart", appName=app.id, userId="u_1")
        )
        first_store.put_invocation(
            InvocationRecord(
                id="inv_restart",
                sessionId="hsess_restart",
                appName=app.id,
                userId="u_1",
                turnId="turn_restart",
                status="running",
            )
        )
        first_store.put_turn(
            TurnRecord(
                id="turn_restart",
                invocationId="inv_restart",
                sessionId="hsess_restart",
                status="running",
            )
        )

    with SQLiteStore(path) as restarted_store:
        event_log = EventLog(store=restarted_store)
        restarted_runtime = SessionRuntime(
            store=restarted_store,
            registry=HarnessRegistry(store=restarted_store),
            adapter=SlowFakeAdapter(delay=0.0),
            event_log=event_log,
        )

        cancelled = await restarted_runtime.cancel_invocation(
            "hsess_restart", "inv_restart"
        )
        repeated = await restarted_runtime.cancel_invocation(
            "hsess_restart", "inv_restart"
        )

        assert cancelled.status == repeated.status == "cancelled"
        assert restarted_store.get_turn("turn_restart").status == "cancelled"
        assert restarted_runtime.get_session(
            app.id, "u_1", "hsess_restart"
        ).state["status"] == "cancelled"
        events = event_log.read_invocation(
            app.id, "u_1", "hsess_restart", "inv_restart"
        )
        assert [event.type for event in events] == ["haas.turn.cancelled"]


async def test_restart_reconciles_orphaned_running_invocation_as_incomplete(tmp_path) -> None:
    path = tmp_path / "restart-orphan.db"
    with SQLiteStore(path) as first_store:
        first_registry = HarnessRegistry(store=first_store)
        app = seed_codex(first_registry)
        session = first_store.put_session(
            SessionRecord(
                id="hsess_orphan",
                appName=app.id,
                userId="u_1",
                state={"status": "running"},
                controlState="running",
            )
        )
        invocation = first_store.put_invocation(
            InvocationRecord(
                id="inv_orphan",
                sessionId=session.id,
                appName=app.id,
                userId="u_1",
                turnId="turn_orphan",
                status="running",
            )
        )
        first_store.put_turn(
            TurnRecord(
                id=invocation.turnId,
                invocationId=invocation.id,
                sessionId=session.id,
                status="running",
            )
        )
        reservation = first_store.reserve("idem_orphan", "request_orphan")
        assert reservation.replay is False
        first_store.accept("idem_orphan", invocation.id)
        invocation.idempotencyKeyHash = "idem_orphan"
        first_store.put_invocation(invocation)

    with SQLiteStore(path) as restarted_store:
        event_log = EventLog(store=restarted_store)
        restarted_runtime = SessionRuntime(
            store=restarted_store,
            registry=HarnessRegistry(store=restarted_store),
            adapter=SlowFakeAdapter(delay=0.0),
            event_log=event_log,
        )

        recovered = restarted_runtime.reconcile_invocation_readback(
            "hsess_orphan", "inv_orphan"
        )
        repeated = restarted_runtime.reconcile_invocation_readback(
            "hsess_orphan", "inv_orphan"
        )

        assert recovered.status == repeated.status == "incomplete"
        assert restarted_store.get_turn("turn_orphan").status == "incomplete"
        restored_session = restarted_runtime.get_session(app.id, "u_1", "hsess_orphan")
        assert restored_session.state == {
            "status": "incomplete",
            "code": "sidecar_restart_execution_lost",
            "reason": "sidecar_restart_execution_lost",
            "retryable": True,
        }
        assert restored_session.controlState == "idle"
        assert restored_session.supportsResume is False
        events = event_log.read_invocation(
            app.id, "u_1", "hsess_orphan", "inv_orphan"
        )
        assert [event.type for event in events] == ["haas.turn.incomplete"]
        assert events[0].haas == {
            "status": "incomplete",
            "code": "sidecar_restart_execution_lost",
            "safeReason": "sidecar_restart_execution_lost",
            "retryable": True,
        }
        replay = restarted_store.replay("idem_orphan")
        assert replay["invocation_id"] == "inv_orphan"
        assert replay["events"][-1]["actions"]["stateDelta"]["status"] == "incomplete"

    with SQLiteStore(path) as reopened_store:
        assert reopened_store.get_invocation("inv_orphan").status == "incomplete"
        assert reopened_store.get_turn("turn_orphan").status == "incomplete"
        replay = reopened_store.replay("idem_orphan")
        assert replay["invocation_id"] == "inv_orphan"
        events = reopened_store.read_invocation(
            (app.id, "u_1", "hsess_orphan"), "inv_orphan"
        )
        assert [event.type for event in events] == ["haas.turn.incomplete"]


def test_invocation_api_reconciles_orphan_before_readback(tmp_path) -> None:
    path = tmp_path / "restart-orphan-api.db"
    store = SQLiteStore(path)
    app = build_app(
        store=store,
        adapter=SlowFakeAdapter(delay=0.0),
        identity_tokens={TOKEN: Principal(principalId="p_1", tenantId="t1")},
    )
    app_record = app.state.runtime.registry.get("chrn_codex_default")
    assert app_record is not None
    store.put_session(
        SessionRecord(
            id="hsess_orphan_api",
            appName=app_record.id,
            userId="u_1",
            state={"status": "running"},
            controlState="running",
        )
    )
    store.put_invocation(
        InvocationRecord(
            id="inv_orphan_api",
            sessionId="hsess_orphan_api",
            appName=app_record.id,
            userId="u_1",
            turnId="turn_orphan_api",
            status="running",
        )
    )
    store.put_turn(
        TurnRecord(
            id="turn_orphan_api",
            invocationId="inv_orphan_api",
            sessionId="hsess_orphan_api",
            status="running",
        )
    )

    with TestClient(app) as client:
        response = client.get(
            "/v1/haas/sessions/hsess_orphan_api/invocations/inv_orphan_api",
            headers=HEADERS,
        )
        repeated = client.get(
            "/v1/haas/sessions/hsess_orphan_api/invocations/inv_orphan_api",
            headers=HEADERS,
        )

    assert response.status_code == repeated.status_code == 200
    assert response.json()["data"]["status"] == "incomplete"
    assert response.json()["data"]["sessionControl"] == {
        "controlState": "idle",
        "supportsResume": False,
        "resumableInvocationId": None,
    }
    events = app.state.runtime.event_log.read_invocation(
        app_record.id, "u_1", "hsess_orphan_api", "inv_orphan_api"
    )
    assert [event.type for event in events] == ["haas.turn.incomplete"]
    store.close()


def test_restart_reconciliation_does_not_overwrite_live_owner(tmp_path) -> None:
    path = tmp_path / "restart-live-owner.db"
    with SQLiteStore(path) as store:
        registry = HarnessRegistry(store=store)
        app = seed_codex(registry)
        store.put_session(SessionRecord(id="hsess_live", appName=app.id, userId="u_1"))
        store.put_invocation(
            InvocationRecord(
                id="inv_live",
                sessionId="hsess_live",
                appName=app.id,
                userId="u_1",
                turnId="turn_live",
                status="running",
            )
        )
        store.put_turn(
            TurnRecord(
                id="turn_live",
                invocationId="inv_live",
                sessionId="hsess_live",
                status="running",
            )
        )
        lease = store.acquire_lease((app.id, "u_1", "hsess_live"), "live-owner")
        runtime = SessionRuntime(
            store=store,
            registry=registry,
            adapter=SlowFakeAdapter(delay=0.0),
            event_log=EventLog(store=store),
        )

        current = runtime.reconcile_invocation_readback("hsess_live", "inv_live")

        assert current.status == "running"
        assert store.get_turn("turn_live").status == "running"
        assert store.read_invocation((app.id, "u_1", "hsess_live"), "inv_live") == []
        store.release_lease((app.id, "u_1", "hsess_live"), "live-owner", lease.token)


def test_terminal_readback_repairs_pending_idempotency_after_crash(tmp_path) -> None:
    path = tmp_path / "restart-terminal-idempotency.db"
    with SQLiteStore(path) as store:
        registry = HarnessRegistry(store=store)
        app = seed_codex(registry)
        store.put_session(SessionRecord(id="hsess_terminal", appName=app.id, userId="u_1"))
        invocation = store.put_invocation(
            InvocationRecord(
                id="inv_terminal",
                sessionId="hsess_terminal",
                appName=app.id,
                userId="u_1",
                turnId="turn_terminal",
                status="incomplete",
                idempotencyKeyHash="idem_terminal",
            )
        )
        store.put_turn(
            TurnRecord(
                id=invocation.turnId,
                invocationId=invocation.id,
                sessionId=invocation.sessionId,
                status="incomplete",
            )
        )
        store.reserve("idem_terminal", "request_terminal")
        store.accept("idem_terminal", invocation.id)
        event_log = EventLog(store=store)
        event_log.append_typed(
            type_="haas.turn.incomplete",
            app_name=app.id,
            user_id="u_1",
            invocation_id=invocation.id,
            session_id=invocation.sessionId,
            turn_id=invocation.turnId,
            harness_id=app.id,
            adapter_id="fake",
            author="fake",
            content={"role": "model", "parts": []},
            actions={"stateDelta": {"status": "incomplete"}},
            haas={
                "status": "incomplete",
                "code": "execution_lost",
                "safeReason": "execution_lost",
                "retryable": True,
            },
        )
        runtime = SessionRuntime(
            store=store,
            registry=registry,
            adapter=SlowFakeAdapter(delay=0.0),
            event_log=event_log,
        )

        runtime.reconcile_invocation_readback("hsess_terminal", invocation.id)

        assert store.replay("idem_terminal")["events"][-1]["id"]


async def test_cancel_old_orphan_does_not_regress_newer_session_terminal_state(
    tmp_path,
) -> None:
    path = tmp_path / "superseded-cancel.db"
    with SQLiteStore(path) as first_store:
        first_registry = HarnessRegistry(store=first_store)
        app = seed_codex(first_registry)
        first_store.put_session(
            SessionRecord(
                id="hsess_superseded",
                appName=app.id,
                userId="u_1",
                state={"status": "completed", "answer": "newer"},
            )
        )
        first_store.put_invocation(
            InvocationRecord(
                id="inv_old_orphan",
                sessionId="hsess_superseded",
                appName=app.id,
                userId="u_1",
                turnId="turn_old_orphan",
                status="running",
            )
        )
        first_store.put_turn(
            TurnRecord(
                id="turn_old_orphan",
                invocationId="inv_old_orphan",
                sessionId="hsess_superseded",
                status="running",
            )
        )

    with SQLiteStore(path) as restarted_store:
        runtime = SessionRuntime(
            store=restarted_store,
            registry=HarnessRegistry(store=restarted_store),
            adapter=SlowFakeAdapter(delay=0.0),
            event_log=EventLog(store=restarted_store),
        )

        cancelled = await runtime.cancel_invocation(
            "hsess_superseded", "inv_old_orphan"
        )

        assert cancelled.status == "cancelled"
        assert runtime.get_session(app.id, "u_1", "hsess_superseded").state == {
            "status": "completed",
            "answer": "newer",
        }


async def test_cancel_unknown_invocation_404() -> None:
    store = MemoryStore()
    registry = HarnessRegistry(store=store)
    runtime = SessionRuntime(
        store=store,
        registry=registry,
        adapter=SlowFakeAdapter(),
        event_log=EventLog(store=store),
    )
    with pytest.raises(InvocationNotFoundError):
        await runtime.cancel_invocation("hsess_x", "inv_missing")


def test_api_cancel_unknown_invocation_404() -> None:
    from fastapi.testclient import TestClient

    client = TestClient(make_app())
    resp = client.post("/v1/haas/sessions/hsess_c/invocations/inv_missing/cancel", headers=HEADERS)
    assert resp.status_code == 404
    assert resp.json()["haasError"]["code"] == "haas_invocation_not_found"


def test_api_cancel_completed_invocation_idempotent() -> None:
    from fastapi.testclient import TestClient

    client = TestClient(make_app())
    run = client.post(
        "/run",
        json={
            "appName": "chrn_codex_default",
            "userId": "u_1",
            "sessionId": "hsess_c",
            "newMessage": {"role": "user", "parts": []},
        },
        headers=HEADERS,
    )
    assert run.status_code == 200
    invocation_id = run.json()[-1]["invocationId"]

    cancel = client.post(
        f"/v1/haas/sessions/hsess_c/invocations/{invocation_id}/cancel",
        headers=HEADERS,
    )
    assert cancel.status_code == 200
    assert cancel.json()["data"]["status"] == "completed"


def test_api_cancel_rejects_invocation_from_another_session() -> None:
    from fastapi.testclient import TestClient

    client = TestClient(make_app())
    run = client.post(
        "/run",
        json={
            "appName": "chrn_codex_default",
            "userId": "u_1",
            "sessionId": "hsess_owned",
            "newMessage": {"role": "user", "parts": []},
        },
        headers=HEADERS,
    )
    assert run.status_code == 200
    invocation_id = run.json()[-1]["invocationId"]

    cancel = client.post(
        f"/v1/haas/sessions/hsess_other/invocations/{invocation_id}/cancel",
        headers=HEADERS,
    )

    assert cancel.status_code == 404
    assert cancel.json()["haasError"]["code"] == "haas_invocation_not_found"


def test_session_events_replay_with_cursor() -> None:
    from fastapi.testclient import TestClient

    app = build_app(
        adapter=SlowFakeAdapter(delay=0.0),
        identity_tokens={TOKEN: Principal(principalId="p_1", tenantId="t1")},
    )
    client = TestClient(app)
    client.post(
        "/run",
        json={
            "appName": "chrn_codex_default",
            "userId": "u_1",
            "sessionId": "hsess_r",
            "newMessage": {"role": "user", "parts": []},
        },
        headers=HEADERS,
    )
    with client.stream("GET", "/v1/haas/sessions/hsess_r/events", headers=HEADERS) as resp:
        lines = [line for line in resp.iter_lines() if line.startswith("data: ")]
    assert len(lines) == 3

    first_id = json.loads(lines[0][len("data: ") :])["eventId"]
    with client.stream(
        "GET",
        f"/v1/haas/sessions/hsess_r/events?after_event_id={first_id}",
        headers=HEADERS,
    ) as resp:
        tail = [line for line in resp.iter_lines() if line.startswith("data: ")]
    assert len(tail) == 2


def test_api_run_sse_uses_streaming_runtime_path() -> None:
    from fastapi.testclient import TestClient

    app = make_app()

    async def fail_run(_req):
        raise AssertionError("/run_sse must not call run() and batch-replay")

    app.state.runtime.sessions.run = fail_run
    client = TestClient(app)
    with client.stream(
        "POST",
        "/run_sse",
        json={
            "appName": "chrn_codex_default",
            "userId": "u_1",
            "sessionId": "hsess_live",
            "newMessage": {"role": "user", "parts": [{"text": "hi"}]},
        },
        headers=HEADERS,
    ) as resp:
        assert resp.status_code == 200
        lines = [line for line in resp.iter_lines() if line.startswith("data: ")]
    assert json.loads(lines[0][len("data: ") :])["content"]["parts"][0]["text"] == "hello"
