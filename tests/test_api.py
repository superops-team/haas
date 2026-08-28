"""S2 ADK API integration tests (functional verification cases C1-C10)."""
import json

from fastapi.testclient import TestClient

from haas.api import build_app
from haas.harnesses import FakeAdapter
from haas.identity import Principal

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
    assert client.get("/v1/haas/ready?scope=execution").json()["data"]["status"] == "not_ready"


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


def test_c9_admission_quota_429() -> None:
    client = make_client(run_quota=1)
    body = {"appName": "chrn_codex_default", "userId": "u_1",
            "newMessage": {"role": "user", "parts": []}}
    first = client.post("/run", json={**body, "sessionId": "hsess_a"}, headers=HEADERS)
    assert first.status_code == 200
    second = client.post("/run", json={**body, "sessionId": "hsess_b"}, headers=HEADERS)
    assert second.status_code == 429
    assert second.json()["haasError"]["code"] == "haas_quota_exceeded"
