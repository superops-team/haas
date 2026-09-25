"""Branch-coverage fill-in tests (file C): targeted branch arcs in haas/api.py.

Targets specific missing branch conditions: helper function branches, Idempotency-Key
cleanup paths, SSE backpressure/disconnect branches, and route error branches.
"""

from fastapi.testclient import TestClient

from haas.api import _configured_scopes, _session_to_adk, build_app
from haas.harnesses import FakeAdapter
from haas.harnesses.base import AdapterTurnStartError
from haas.identity import Principal

TOKEN = "test-token"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}
APP = "chrn_codex_default"
USER = "u_1"


def make_client(**kwargs: object) -> TestClient:
    tokens = {TOKEN: Principal(principalId="p_1", tenantId="t1", userIds=frozenset({USER}))}
    app = build_app(
        adapter=kwargs.get("adapter", FakeAdapter()),
        identity_tokens=tokens,
        run_quota=int(kwargs.get("run_quota", 20)),
        rate_limit=int(kwargs.get("rate_limit", 100)),
    )
    return TestClient(app)


def _run_body(session_id: str = "s1", text: str = "hi") -> dict:
    return {
        "appName": APP,
        "userId": USER,
        "sessionId": session_id,
        "newMessage": {"role": "user", "parts": [{"text": text}]},
    }


def _code(resp) -> str:
    return resp.json()["haasError"]["code"]


# --- Helper function branches ------------------------------------------------


def test_configured_scopes_duplicate_keys_collapses() -> None:
    """_configured_scopes: duplicate (tenant, workspace) keys collapsed (125->123 False)."""

    class DupIdentity:
        @staticmethod
        def principals():
            return [
                Principal(principalId="a", tenantId="t", workspaceId="w"),
                Principal(principalId="b", tenantId="t", workspaceId="w"),
            ]

    scopes = _configured_scopes(DupIdentity())
    assert scopes == [("t", "w")]


def test_configured_scopes_none_returns_default() -> None:
    """_configured_scopes: principals=None -> [(None, None)] (120-121)."""

    class EmptyIdentity:
        principals = None

    assert _configured_scopes(EmptyIdentity()) == [(None, None)]


def test_session_to_adk_without_events() -> None:
    """_session_to_adk: include_events=False skips events (469->474 False)."""
    from haas.sessions import SessionRecord

    session = SessionRecord(
        id="s1", appName=APP, userId=USER, state={"status": "idle"},
        updatedAtMs=1000, controlState="idle",
    )

    class _EventLog:
        @staticmethod
        def read_session(*a, **kw):
            return []
        @staticmethod
        def project_adk(e):
            return e

    runtime = type("R", (), {"event_log": _EventLog()})()
    data = _session_to_adk(runtime, session, include_events=False)
    assert "events" not in data


def test_session_to_adk_with_events() -> None:
    """_session_to_adk: include_events=True includes events (469->474 True)."""
    from haas.sessions import SessionRecord

    session = SessionRecord(
        id="s2", appName=APP, userId=USER, state={"status": "idle"},
        updatedAtMs=1000, controlState="idle",
    )

    class _EventLog:
        @staticmethod
        def read_session(*a, **kw):
            return [{"id": "evt_1"}]
        @staticmethod
        def project_adk(e):
            return e

    runtime = type("R", (), {"event_log": _EventLog()})()
    data = _session_to_adk(runtime, session, include_events=True)
    assert "events" in data
    assert len(data["events"]) == 1


# --- /run with Idempotency-Key -----------------------------------------------


def test_run_idempotency_app_not_found() -> None:
    """/run + Idempotency-Key + app_not_found → key_hash cleanup."""
    client = make_client()
    body = {**_run_body(), "appName": "no_such_app"}
    resp = client.post("/run", json=body, headers={**HEADERS, "Idempotency-Key": "k-404"})
    assert resp.status_code == 404
    assert _code(resp) == "app_not_found"


def test_run_idempotency_replay_same_key() -> None:
    """Same Idempotency-Key twice → same result."""
    client = make_client()
    body = _run_body("s_replay")
    h = {**HEADERS, "Idempotency-Key": "replay-c"}
    r1 = client.post("/run", json=body, headers=h)
    r2 = client.post("/run", json=body, headers=h)
    assert r1.status_code == r2.status_code == 200
    assert r1.json() == r2.json()


def test_run_idempotency_conflict_different_body() -> None:
    """Same key + different body → 409."""
    client = make_client()
    h = {**HEADERS, "Idempotency-Key": "conflict-c"}
    client.post("/run", json=_run_body("s_conf", "first"), headers=h)
    r2 = client.post("/run", json=_run_body("s_conf", "second"), headers=h)
    assert r2.status_code == 409


def test_run_quota_zero() -> None:
    """/run with run_quota=0 → 429."""
    client = make_client(run_quota=0)
    resp = client.post("/run", json=_run_body("s_q"), headers=HEADERS)
    assert resp.status_code == 429


# --- /run adapter error ------------------------------------------------------


class _ErrorAdapter(FakeAdapter):
    async def start_turn(self, *a, **kw):
        raise AdapterTurnStartError(code="haas_adapter_crash", retryable=False)


def test_run_adapter_error() -> None:
    """/run: adapter.start_turn raises → error path."""
    client = make_client(adapter=_ErrorAdapter())
    resp = client.post("/run", json=_run_body("s_err"), headers=HEADERS)
    assert resp.status_code in (500, 502)


def test_run_sse_adapter_error() -> None:
    """/run_sse: adapter.start_turn raises → SSE error path."""
    client = make_client(adapter=_ErrorAdapter())
    with client.stream("POST", "/run_sse", json=_run_body("s_sse_err"), headers=HEADERS) as resp:
        assert resp.status_code == 200
        frames = [line for line in resp.iter_lines() if line]
        assert any("failed" in f.lower() or "error" in f.lower() for f in frames)


# --- SSE backpressure --------------------------------------------------------


def test_run_sse_normal() -> None:
    """/run_sse normal flow → data frames + completion."""
    client = make_client()
    with client.stream("POST", "/run_sse", json=_run_body("s_sse_ok"), headers=HEADERS) as resp:
        assert resp.status_code == 200
        frames = [line for line in resp.iter_lines() if line]
        assert any("data:" in f for f in frames)


def test_run_sse_idempotency_key() -> None:
    """/run_sse + Idempotency-Key → produce path with key_hash completion."""
    client = make_client()
    h = {**HEADERS, "Idempotency-Key": "sse-key-c"}
    with client.stream("POST", "/run_sse", json=_run_body("s_sse_key"), headers=h) as resp:
        assert resp.status_code == 200
        frames = [line for line in resp.iter_lines() if line]
        assert len(frames) > 0


# --- Session events replay ---------------------------------------------------


def test_session_events_unknown() -> None:
    """GET session events unknown → 404."""
    client = make_client()
    resp = client.get(f"/apps/{APP}/users/{USER}/sessions/no_such/events", headers=HEADERS)
    assert resp.status_code == 404


def test_session_events_replay_after_id() -> None:
    """GET session events with after_event_id → replay or 410."""
    client = make_client()
    client.post("/run", json=_run_body("s_ev"), headers=HEADERS)
    resp = client.get(
        f"/apps/{APP}/users/{USER}/sessions/s_ev/events?after_event_id=evt_0000000000000",
        headers=HEADERS,
    )
    assert resp.status_code in (200, 404, 410)


# --- Artifact branches -------------------------------------------------------


def test_artifact_list_unknown_session() -> None:
    """GET artifacts unknown session → 404."""
    client = make_client()
    resp = client.get("/v1/haas/sessions/no_such/artifacts", headers=HEADERS)
    assert resp.status_code == 404


def test_artifact_archive_unknown_session() -> None:
    """GET artifacts/archive unknown session → 404."""
    client = make_client()
    resp = client.get("/v1/haas/sessions/no_such/artifacts/archive", headers=HEADERS)
    assert resp.status_code == 404


# --- Policy branches ---------------------------------------------------------


def test_policy_update_unknown_session() -> None:
    """POST policy unknown session → 404."""
    client = make_client()
    resp = client.post(
        "/v1/haas/sessions/no_such/policy",
        json={"policy": {"network": {"defaultAction": "deny"}}},
        headers={**HEADERS, "Idempotency-Key": "pol-404"},
    )
    assert resp.status_code == 404


def test_policy_update_missing_key() -> None:
    """POST policy without Idempotency-Key → 400."""
    client = make_client()
    resp = client.post(
        "/v1/haas/sessions/s1/policy",
        json={"policy": {"network": {"defaultAction": "deny"}}},
        headers=HEADERS,
    )
    assert resp.status_code == 400


# --- Delegated session branches ----------------------------------------------


def test_delegated_get_unknown() -> None:
    """GET delegated-session unknown → 404."""
    client = make_client()
    resp = client.get("/v1/haas/delegated-sessions/no_such", headers=HEADERS)
    assert resp.status_code == 404


def test_delegated_create_non_object_body() -> None:
    """POST delegated-sessions non-object → 400."""
    client = make_client()
    resp = client.post(
        "/v1/haas/delegated-sessions",
        content="[1,2,3]",
        headers={**HEADERS, "Content-Type": "application/json"},
    )
    assert resp.status_code == 400


def test_delegated_create_missing_fields() -> None:
    """POST delegated-sessions missing fields → 400."""
    client = make_client()
    resp = client.post("/v1/haas/delegated-sessions", json={"appName": APP}, headers=HEADERS)
    assert resp.status_code == 400


def test_delegated_policy_unknown() -> None:
    """POST delegated-sessions/{id}/policy unknown → 404."""
    client = make_client()
    resp = client.post(
        "/v1/haas/delegated-sessions/no_such/policy",
        json={"policy": {"network": {"defaultAction": "deny"}}},
        headers={**HEADERS, "Idempotency-Key": "del-pol-404"},
    )
    assert resp.status_code == 404


def test_delegated_restore_unknown() -> None:
    """POST delegated-sessions/{id}/restore unknown → 404."""
    client = make_client()
    resp = client.post("/v1/haas/delegated-sessions/no_such/restore", headers=HEADERS)
    assert resp.status_code == 404
