"""Regression tests for S3 review fixes: adapter errors, cursor expiry, idempotency concurrency."""
import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient

from haas.api import build_app
from haas.harnesses import FakeAdapter
from haas.harnesses.base import StartTurnRequest, TurnHandle
from haas.harnesses.fake import BlockingFakeAdapter
from haas.identity import Principal

# Error envelopes, cursor expiry and idempotency are protocol-surface contracts.
pytestmark = pytest.mark.adk

TOKEN = "fix-token"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}
BODY = {"appName": "chrn_codex_default", "userId": "u_1",
        "newMessage": {"role": "user", "parts": [{"text": "hi"}]}}


class RaisingAdapter(FakeAdapter):
    """Fails at turn start to exercise the adapter-error terminal path."""

    async def start_turn(self, request: StartTurnRequest) -> TurnHandle:
        raise RuntimeError("adapter exploded")


def make_client(**kwargs: object) -> TestClient:
    adapter = kwargs.pop("adapter", None) or FakeAdapter()
    app = build_app(
        adapter=adapter,
        identity_tokens={TOKEN: Principal(principalId="p_1", tenantId="t1",
                                          userIds=frozenset({"u_1"}))},
        run_quota=int(kwargs.get("run_quota", 20)),
        rate_limit=int(kwargs.get("rate_limit", 100)),
    )
    return TestClient(app)


def test_adapter_error_returns_502_with_terminal_failed_event() -> None:
    client = make_client(adapter=RaisingAdapter())
    resp = client.post(
        "/run", json={**BODY, "sessionId": "hsess_err"}, headers=HEADERS
    )
    assert resp.status_code == 502
    assert resp.json()["haasError"]["code"] == "haas_adapter_error"

    session = client.get(
        "/apps/chrn_codex_default/users/u_1/sessions/hsess_err", headers=HEADERS
    ).json()
    assert session["state"]["status"] == "failed"
    assert session["events"][-1]["actions"]["stateDelta"]["status"] == "failed"


def test_idempotency_replay_preserves_adapter_error_http_status() -> None:
    client = make_client(adapter=RaisingAdapter())
    headers = {**HEADERS, "Idempotency-Key": "adapter-fails-once"}
    body = {**BODY, "sessionId": "hsess_idem_err"}

    first = client.post("/run", json=body, headers=headers)
    replay = client.post("/run", json=body, headers=headers)

    assert first.status_code == 502
    assert replay.status_code == 502
    assert first.json()["haasError"]["code"] == "haas_adapter_error"
    assert replay.json()["haasError"]["code"] == "haas_adapter_error"

    session = client.get(
        "/apps/chrn_codex_default/users/u_1/sessions/hsess_idem_err",
        headers=HEADERS,
    ).json()
    assert len(session["events"]) == 1
    assert session["events"][0]["actions"]["stateDelta"]["status"] == "failed"


def test_session_events_unknown_cursor_410() -> None:
    client = make_client()
    client.post("/run", json={**BODY, "sessionId": "hsess_cur"}, headers=HEADERS)
    resp = client.get(
        "/v1/haas/sessions/hsess_cur/events?after_event_id=evt_missing",
        headers=HEADERS,
    )
    assert resp.status_code == 410
    assert resp.json()["haasError"]["code"] == "haas_offset_expired"


def test_patch_session_invalid_delta_400() -> None:
    client = make_client()
    client.post("/run", json={**BODY, "sessionId": "hsess_patch"}, headers=HEADERS)
    resp = client.patch(
        "/apps/chrn_codex_default/users/u_1/sessions/hsess_patch",
        json={"stateDelta": "not-a-dict"}, headers=HEADERS,
    )
    assert resp.status_code == 400
    assert resp.json()["haasError"]["code"] == "invalid_input"


async def test_idempotency_in_flight_replays_first_result() -> None:
    adapter = BlockingFakeAdapter()
    app = build_app(
        adapter=adapter,
        identity_tokens={TOKEN: Principal(principalId="p_1", tenantId="t1",
                                          userIds=frozenset({"u_1"}))},
        run_quota=20, rate_limit=100,
    )
    transport = httpx.ASGITransport(app=app)
    headers = {**HEADERS, "Idempotency-Key": "dup-key"}
    body = {**BODY, "sessionId": "hsess_dup"}

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        task_a = asyncio.create_task(client.post("/run", json=body, headers=headers))
        for _ in range(100):
            if adapter._barriers:
                break
            await asyncio.sleep(0.01)
        task_b = asyncio.create_task(client.post("/run", json=body, headers=headers))
        await asyncio.sleep(0.05)
        for barrier in adapter._barriers.values():
            barrier.set()

        resp_a = await asyncio.wait_for(task_a, timeout=5)
        resp_b = await asyncio.wait_for(task_b, timeout=5)

        # Exactly one invocation ran: session history must not contain duplicates.
        session_resp = await client.get(
            "/apps/chrn_codex_default/users/u_1/sessions/hsess_dup", headers=HEADERS
        )

    assert resp_a.status_code == 200
    assert resp_b.status_code == 200
    ids_a = [e["id"] for e in resp_a.json()]
    ids_b = [e["id"] for e in resp_b.json()]
    assert ids_a == ids_b
    assert len(session_resp.json()["events"]) == len(ids_a)
