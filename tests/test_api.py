"""S2 ADK API integration tests (functional verification cases C1-C10)."""
import json

import pytest
from fastapi.testclient import TestClient

from haas.api import build_app
from haas.harnesses import FakeAdapter
from haas.identity import Principal

# ADK 2.0 northbound protocol surface: also run under `make adk-compat`.
pytestmark = pytest.mark.adk

TOKEN = "test-token"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}


def make_client(**kwargs: object) -> TestClient:
    tokens = {TOKEN: Principal(principalId="p_1", tenantId="t1", userIds=frozenset({"u_1"}))}
    app = build_app(
        adapter=FakeAdapter(), identity_tokens=tokens,
        run_quota=int(kwargs.get("run_quota", 20)),
        rate_limit=int(kwargs.get("rate_limit", 100)),
    )
    return TestClient(app)


def test_c1_health_ready() -> None:
    client = make_client()
    assert client.get("/v1/haas/health").json()["data"]["status"] == "ok"
    assert client.get("/v1/haas/ready?scope=control").json()["data"]["status"] == "ready"
    # execution readiness now reflects adapter.probe(); FakeAdapter is always ready
    assert client.get("/v1/haas/ready?scope=execution").json()["data"]["status"] == "ready"


def test_c2_list_apps_requires_auth() -> None:
    client = make_client()
    assert client.get("/list-apps").status_code == 401
    resp = client.get("/list-apps", headers=HEADERS)
    assert resp.status_code == 200
    assert resp.json() == ["chrn_codex_default"]


def test_c3_run_non_streaming() -> None:
    client = make_client()
    resp = client.post(
        "/run",
        json={"appName": "chrn_codex_default", "userId": "u_1",
              "newMessage": {"role": "user", "parts": [{"text": "hi"}]}},
        headers=HEADERS,
    )
    assert resp.status_code == 200
    events = resp.json()
    assert isinstance(events, list)
    assert events[0]["content"]["parts"][0]["text"] == "hello"
    assert events[-1]["content"]["role"] == "model"


def test_c4_run_sse_and_parity() -> None:
    client = make_client()
    body = {"appName": "chrn_codex_default", "userId": "u_1",
            "newMessage": {"role": "user", "parts": [{"text": "hi"}]}}
    with client.stream("POST", "/run_sse", json=body, headers=HEADERS) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        lines = [line for line in resp.iter_lines() if line.startswith("data: ")]
    sse_events = [json.loads(line[len("data: "):]) for line in lines]

    run_events = client.post("/run", json=body, headers=HEADERS).json()
    assert [e["content"] for e in sse_events] == [e["content"] for e in run_events]


def test_c5_session_get_read_back() -> None:
    client = make_client()
    resp = client.post(
        "/run", json={"appName": "chrn_codex_default", "userId": "u_1",
                      "sessionId": "hsess_1", "newMessage": {"role": "user", "parts": []}},
        headers=HEADERS,
    )
    assert resp.status_code == 200
    session = client.get(
        "/apps/chrn_codex_default/users/u_1/sessions/hsess_1", headers=HEADERS
    ).json()
    assert session["state"]["status"] == "completed"
    assert session["state"]["last_text"] == "world"
    assert len(session["events"]) == 3


def test_c6_patch_state_delta_deep_merge() -> None:
    client = make_client()
    client.post(
        "/run", json={"appName": "chrn_codex_default", "userId": "u_1",
                      "sessionId": "hsess_1", "newMessage": {"role": "user", "parts": []}},
        headers=HEADERS,
    )
    client.patch(
        "/apps/chrn_codex_default/users/u_1/sessions/hsess_1",
        json={"stateDelta": {"a": {"b": 1}}}, headers=HEADERS,
    )
    client.patch(
        "/apps/chrn_codex_default/users/u_1/sessions/hsess_1",
        json={"stateDelta": {"a": {"c": 2}}}, headers=HEADERS,
    )
    session = client.get(
        "/apps/chrn_codex_default/users/u_1/sessions/hsess_1", headers=HEADERS
    ).json()
    assert session["state"]["a"] == {"b": 1, "c": 2}


def test_c7_delete_session() -> None:
    client = make_client()
    client.post(
        "/run", json={"appName": "chrn_codex_default", "userId": "u_1",
                      "sessionId": "hsess_1", "newMessage": {"role": "user", "parts": []}},
        headers=HEADERS,
    )
    assert client.delete(
        "/apps/chrn_codex_default/users/u_1/sessions/hsess_1", headers=HEADERS
    ).status_code == 204
    assert client.get(
        "/apps/chrn_codex_default/users/u_1/sessions/hsess_1", headers=HEADERS
    ).status_code == 404


def test_c8_error_envelope() -> None:
    client = make_client()
    # 401 missing credential
    assert client.get("/list-apps").status_code == 401
    # 404 app_not_found
    resp = client.post(
        "/run", json={"appName": "nope", "userId": "u_1",
                      "newMessage": {"role": "user", "parts": []}}, headers=HEADERS
    )
    assert resp.status_code == 404
    assert resp.json()["haasError"]["code"] == "app_not_found"
    # 409 idempotency conflict
    body = {"appName": "chrn_codex_default", "userId": "u_1", "sessionId": "hsess_x",
            "newMessage": {"role": "user", "parts": [{"text": "a"}]}}
    client.post("/run", json=body, headers={**HEADERS, "Idempotency-Key": "k1"})
    conflict = client.post(
        "/run", json={**body, "newMessage": {"role": "user", "parts": [{"text": "b"}]}},
        headers={**HEADERS, "Idempotency-Key": "k1"},
    )
    assert conflict.status_code == 409
    assert conflict.json()["haasError"]["code"] == "haas_idempotency_conflict"


def test_c9_run_quota_released_after_completion() -> None:
    client = make_client(run_quota=1)
    body = {"appName": "chrn_codex_default", "userId": "u_1",
            "newMessage": {"role": "user", "parts": []}}
    for index in range(3):
        resp = client.post(
            "/run", json={**body, "sessionId": f"hsess_q{index}"}, headers=HEADERS
        )
        assert resp.status_code == 200, f"run {index} should not leak admission quota"


def _texts(events: list[dict[str, object]]) -> list[str]:
    texts: list[str] = []
    for event in events:
        content = event.get("content")
        if not isinstance(content, dict):
            continue
        parts = content.get("parts")
        if not isinstance(parts, list):
            continue
        for part in parts:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                texts.append(part["text"])
    return texts


def _sse_payloads(resp: object) -> list[dict[str, object]]:
    # TestClient response/stream objects both expose iter_lines().
    return [
        json.loads(line[len("data: "):])
        for line in resp.iter_lines()
        if line.startswith("data: ")
    ]


def test_events_are_isolated_when_different_users_share_session_id() -> None:
    tokens = {
        "tok-a": Principal(principalId="p_a", tenantId="t1", userIds=frozenset({"u_a"})),
        "tok-b": Principal(principalId="p_b", tenantId="t1", userIds=frozenset({"u_b"})),
    }
    client = TestClient(build_app(adapter=FakeAdapter(), identity_tokens=tokens))
    shared_session = "hsess_shared_user"

    assert client.post(
        "/run",
        json={
            "appName": "chrn_codex_default",
            "userId": "u_a",
            "sessionId": shared_session,
            "newMessage": {"role": "user", "parts": [{"text": "a"}]},
        },
        headers={"Authorization": "Bearer tok-a"},
    ).status_code == 200
    assert client.post(
        "/run",
        json={
            "appName": "chrn_codex_default",
            "userId": "u_b",
            "sessionId": shared_session,
            "newMessage": {"role": "user", "parts": [{"text": "b"}]},
        },
        headers={"Authorization": "Bearer tok-b"},
    ).status_code == 200

    session_a = client.get(
        f"/apps/chrn_codex_default/users/u_a/sessions/{shared_session}",
        headers={"Authorization": "Bearer tok-a"},
    ).json()
    session_b = client.get(
        f"/apps/chrn_codex_default/users/u_b/sessions/{shared_session}",
        headers={"Authorization": "Bearer tok-b"},
    ).json()

    assert len(session_a["events"]) == 3
    assert len(session_b["events"]) == 3
    assert {event["invocationId"] for event in session_a["events"]}.isdisjoint(
        {event["invocationId"] for event in session_b["events"]}
    )


def test_events_are_isolated_when_different_apps_share_session_id() -> None:
    client = make_client()
    created = client.post(
        "/v1/haas/harnesses",
        json={"base": "codex", "name": "second-app"},
        headers=HEADERS,
    )
    assert created.status_code == 200, created.text
    app_2 = created.json()["data"]["id"]
    shared_session = "hsess_shared_app"

    assert client.post(
        "/run",
        json={
            "appName": "chrn_codex_default",
            "userId": "u_1",
            "sessionId": shared_session,
            "newMessage": {"role": "user", "parts": []},
        },
        headers=HEADERS,
    ).status_code == 200
    assert client.post(
        "/run",
        json={
            "appName": app_2,
            "userId": "u_1",
            "sessionId": shared_session,
            "newMessage": {"role": "user", "parts": []},
        },
        headers=HEADERS,
    ).status_code == 200

    session_1 = client.get(
        f"/apps/chrn_codex_default/users/u_1/sessions/{shared_session}",
        headers=HEADERS,
    ).json()
    session_2 = client.get(
        f"/apps/{app_2}/users/u_1/sessions/{shared_session}",
        headers=HEADERS,
    ).json()

    assert len(session_1["events"]) == 3
    assert len(session_2["events"]) == 3
    assert {event["invocationId"] for event in session_1["events"]}.isdisjoint(
        {event["invocationId"] for event in session_2["events"]}
    )


def test_session_events_endpoint_returns_own_events_when_scope_unambiguous() -> None:
    client = make_client()
    session_id = "hsess_native_own"
    run = client.post(
        "/run",
        json={
            "appName": "chrn_codex_default",
            "userId": "u_1",
            "sessionId": session_id,
            "newMessage": {"role": "user", "parts": []},
        },
        headers=HEADERS,
    )
    assert run.status_code == 200
    with client.stream("GET", f"/v1/haas/sessions/{session_id}/events", headers=HEADERS) as resp:
        assert resp.status_code == 200
        events = _sse_payloads(resp)

    assert len(events) == 3
    assert _texts(events) == ["hello", "world"]


def test_session_events_endpoint_rejects_cross_user_access() -> None:
    tokens = {
        "tok-a": Principal(principalId="p_a", tenantId="t1", userIds=frozenset({"u_a"})),
        "tok-b": Principal(principalId="p_b", tenantId="t1", userIds=frozenset({"u_b"})),
    }
    client = TestClient(build_app(adapter=FakeAdapter(), identity_tokens=tokens))
    session_id = "hsess_private"
    assert client.post(
        "/run",
        json={
            "appName": "chrn_codex_default",
            "userId": "u_a",
            "sessionId": session_id,
            "newMessage": {"role": "user", "parts": []},
        },
        headers={"Authorization": "Bearer tok-a"},
    ).status_code == 200

    denied = client.get(
        f"/v1/haas/sessions/{session_id}/events",
        headers={"Authorization": "Bearer tok-b"},
    )
    assert denied.status_code == 404
    assert denied.json()["haasError"]["code"] == "session_not_found"
