"""S3 cancel + session replay verification (functional cases)."""
import json

import pytest

from haas.api import build_app
from haas.events import EventLog
from haas.harnesses.fake import SlowFakeAdapter
from haas.identity import Principal
from haas.registry import HarnessRegistry, seed_codex
from haas.sessions import InvocationNotFoundError, RunRequest, SessionRuntime
from haas.stores import MemoryStore

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
        store=store, registry=registry, adapter=SlowFakeAdapter(delay=0.05),
        event_log=EventLog(store=store),
    )
    req = RunRequest(
        app=app, user_id="u_1", session_id="hsess_c",
        message={"role": "user", "parts": [{"text": "hi"}]},
    )
    stream = runtime.run_stream(req)
    first = await stream.__anext__()
    invocation_id = first.invocationId

    await runtime.cancel_invocation("hsess_c", invocation_id)
    events = [first] + [event async for event in stream]

    assert events[-1].actions["stateDelta"]["status"] == "cancelled"
    assert store.get_invocation(invocation_id).status == "cancelled"


async def test_cancel_unknown_invocation_404() -> None:
    store = MemoryStore()
    registry = HarnessRegistry(store=store)
    runtime = SessionRuntime(
        store=store, registry=registry, adapter=SlowFakeAdapter(),
        event_log=EventLog(store=store),
    )
    with pytest.raises(InvocationNotFoundError):
        await runtime.cancel_invocation("hsess_x", "inv_missing")


def test_api_cancel_unknown_invocation_404() -> None:
    from fastapi.testclient import TestClient

    client = TestClient(make_app())
    resp = client.post(
        "/v1/haas/sessions/hsess_c/invocations/inv_missing/cancel", headers=HEADERS
    )
    assert resp.status_code == 404
    assert resp.json()["haasError"]["code"] == "haas_invocation_not_found"


def test_api_cancel_completed_invocation_idempotent() -> None:
    from fastapi.testclient import TestClient

    client = TestClient(make_app())
    run = client.post(
        "/run", json={"appName": "chrn_codex_default", "userId": "u_1",
                      "sessionId": "hsess_c", "newMessage": {"role": "user", "parts": []}},
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


def test_session_events_replay_with_cursor() -> None:
    from fastapi.testclient import TestClient

    app = build_app(adapter=SlowFakeAdapter(delay=0.0),
                    identity_tokens={TOKEN: Principal(principalId="p_1", tenantId="t1")})
    client = TestClient(app)
    client.post(
        "/run", json={"appName": "chrn_codex_default", "userId": "u_1",
                      "sessionId": "hsess_r", "newMessage": {"role": "user", "parts": []}},
        headers=HEADERS,
    )
    with client.stream(
        "GET", "/v1/haas/sessions/hsess_r/events", headers=HEADERS
    ) as resp:
        lines = [line for line in resp.iter_lines() if line.startswith("data: ")]
    assert len(lines) == 3

    first_id = json.loads(lines[0][len("data: "):])["eventId"]
    with client.stream(
        "GET", f"/v1/haas/sessions/hsess_r/events?after_event_id={first_id}",
        headers=HEADERS,
    ) as resp:
        tail = [line for line in resp.iter_lines() if line.startswith("data: ")]
    assert len(tail) == 2
