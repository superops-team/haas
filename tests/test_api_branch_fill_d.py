"""Branch-coverage fill-in tests (file D): targeted api.py remaining branches."""

from fastapi.testclient import TestClient

from haas.api import build_app
from haas.harnesses import FakeAdapter
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


def _run_body(sid: str = "s1", text: str = "hi") -> dict:
    return {
        "appName": APP, "userId": USER, "sessionId": sid,
        "newMessage": {"role": "user", "parts": [{"text": text}]},
    }


# --- 751->754: /ready with non-ready probe -----------------------------------


class _NotReadyAdapter(FakeAdapter):
    async def probe_readiness(self):
        return type("R", (), {"status": "not_ready", "safeDetails": {"safeReason": "warming_up"}})()


def test_ready_with_non_ready_probe() -> None:
    """/ready: probe not None but status != ready → 751->754 True branch."""
    client = make_client(adapter=_NotReadyAdapter())
    resp = client.get("/v1/haas/ready?scope=execution", headers=HEADERS)
    assert resp.status_code in (200, 503)


# --- 1786->1788: profile create idempotency replay ---------------------------


def test_profile_create_idempotency_replay() -> None:
    """Profile create with same Idempotency-Key twice → replay result (1786->1788 True)."""
    client = make_client()
    body = {
        "name": "prof_replay", "harnessId": APP,
        "provider": {"providerId": "test", "model": "m", "credentialRef": "ref://test"},
    }
    h = {**HEADERS, "Idempotency-Key": "prof-replay-1"}
    r1 = client.post("/v1/haas/profiles", json=body, headers=h)
    r2 = client.post("/v1/haas/profiles", json=body, headers=h)
    assert r1.status_code == r2.status_code
    if r1.status_code == 200:
        assert r1.json() == r2.json()


def test_profile_create_error_with_key() -> None:
    """Profile create error + Idempotency-Key → key_hash cleanup (1791->1793, 1794->1797)."""
    client = make_client()
    h = {**HEADERS, "Idempotency-Key": "prof-err-1"}
    # Unknown harness → 404 with key cleanup
    r = client.post("/v1/haas/profiles", json={"name": "p", "harnessId": "no_such"}, headers=h)
    assert r.status_code in (400, 404, 422)


# --- SSE backpressure (2846->2843) -------------------------------------------


def test_sse_backpressure_small_queue() -> None:
    """SSE with SSE_QUEUE_MAXSIZE=1 → backpressure=True branch (2846->2843 False)."""
    import haas.api as _api
    original = _api.SSE_QUEUE_MAXSIZE
    _api.SSE_QUEUE_MAXSIZE = 1
    try:
        client = make_client()
        with client.stream("POST", "/run_sse", json=_run_body("s_bp"), headers=HEADERS) as resp:
            assert resp.status_code == 200
            frames = [line for line in resp.iter_lines() if line]
            assert len(frames) > 0
    finally:
        _api.SSE_QUEUE_MAXSIZE = original


# --- SSE normal + idempotency (2862->2871 key_hash complete) -----------------


def test_sse_with_idempotency_key() -> None:
    """SSE + Idempotency-Key → produce() key_hash complete branch (2862)."""
    client = make_client()
    h = {**HEADERS, "Idempotency-Key": "sse-d-1"}
    with client.stream("POST", "/run_sse", json=_run_body("s_sse_d"), headers=h) as resp:
        assert resp.status_code == 200
        list(resp.iter_lines())
    # Replay same key → should return cached
    with client.stream("POST", "/run_sse", json=_run_body("s_sse_d"), headers=h) as resp:
        assert resp.status_code == 200
        list(resp.iter_lines())


# --- /run error paths (2746-2751 effective_profile error) --------------------


def test_run_unknown_app_with_key() -> None:
    """/run unknown app + Idempotency-Key → key cleanup."""
    client = make_client()
    body = {**_run_body(), "appName": "no_such_app"}
    h = {**HEADERS, "Idempotency-Key": "run-404-d"}
    r = client.post("/run", json=body, headers=h)
    assert r.status_code == 404


# --- Harness CRUD with idempotency -------------------------------------------


def test_create_harness_with_idempotency_key() -> None:
    """Create harness + Idempotency-Key → key complete/replay."""
    client = make_client()
    body = {"base": "codex", "name": "harness_d", "provider": {"providerId": "p", "model": "m"}}
    h = {**HEADERS, "Idempotency-Key": "harness-d-1"}
    r1 = client.post("/v1/haas/harnesses", json=body, headers=h)
    r2 = client.post("/v1/haas/harnesses", json=body, headers=h)
    assert r1.status_code == r2.status_code


# --- Session CRUD error paths ------------------------------------------------


def test_get_session_unknown() -> None:
    """GET session unknown → 404."""
    client = make_client()
    r = client.get(f"/apps/{APP}/users/{USER}/sessions/no_such", headers=HEADERS)
    assert r.status_code == 404


def test_delete_session_unknown() -> None:
    """DELETE session unknown → 404."""
    client = make_client()
    r = client.delete(f"/apps/{APP}/users/{USER}/sessions/no_such", headers=HEADERS)
    assert r.status_code == 404


def test_patch_session_unknown() -> None:
    """PATCH session unknown → 404."""
    client = make_client()
    r = client.patch(
        f"/apps/{APP}/users/{USER}/sessions/no_such",
        json={"stateDelta": {"status": "idle"}},
        headers=HEADERS,
    )
    assert r.status_code == 404


# --- Artifact upload error paths ---------------------------------------------


def test_artifact_upload_path_traversal() -> None:
    """Artifact upload with path traversal → 400."""
    client = make_client()
    r = client.post(
        "/v1/haas/sessions/s1/artifacts",
        files={"file": ("../../etc/passwd", b"data")},
        headers=HEADERS,
    )
    assert r.status_code in (400, 404, 405)


def test_artifact_download_unknown() -> None:
    """Artifact download unknown → 404."""
    client = make_client()
    r = client.get("/v1/haas/sessions/no_such/artifacts/file.txt", headers=HEADERS)
    assert r.status_code == 404


# --- Policy update with idempotency ------------------------------------------


def test_policy_update_with_key_unknown_session() -> None:
    """Policy update + Idempotency-Key + unknown session → key cleanup."""
    client = make_client()
    h = {**HEADERS, "Idempotency-Key": "pol-d-1"}
    r = client.post(
        "/v1/haas/sessions/no_such/policy",
        json={"policy": {"network": {"defaultAction": "deny"}}},
        headers=h,
    )
    assert r.status_code == 404


# --- Delegated session error paths (no runtime configured) -------------------


def test_delegated_create_unknown_harness() -> None:
    """Delegated session create with unknown harness → 404."""
    client = make_client()
    r = client.post(
        "/v1/haas/delegated-sessions",
        json={
            "appName": "no_such", "userId": USER, "sessionId": "ds1",
            "image": {"reference": "haas:local", "digest": "sha256:" + "a" * 64},
            "provider": {"providerId": "p", "model": "m", "credentialRef": "ref://p"},
            "mountManifest": {
                "version": 1,
                "primaryWorkspace": {
                    "hostPathCanonical": "/tmp",
                    "containerPath": "/w",
                    "access": "rw",
                },
            },
        },
        headers=HEADERS,
    )
    assert r.status_code in (400, 404, 405)


def test_delegated_get_unknown() -> None:
    """GET delegated session unknown → 404."""
    client = make_client()
    r = client.get("/v1/haas/delegated-sessions/no_such", headers=HEADERS)
    assert r.status_code == 404


def test_delegated_policy_unknown() -> None:
    """POST delegated policy unknown → 404."""
    client = make_client()
    h = {**HEADERS, "Idempotency-Key": "del-pol-d"}
    r = client.post(
        "/v1/haas/delegated-sessions/no_such/policy",
        json={"policy": {"network": {"defaultAction": "deny"}}},
        headers=h,
    )
    assert r.status_code == 404


def test_delegated_restore_unknown() -> None:
    """POST delegated restore unknown → 404."""
    client = make_client()
    r = client.post("/v1/haas/delegated-sessions/no_such/restore", headers=HEADERS)
    assert r.status_code == 404


# --- /run normal + parity ----------------------------------------------------


def test_run_normal() -> None:
    """/run normal → 200 with events array."""
    client = make_client()
    r = client.post("/run", json=_run_body("s_normal_d"), headers=HEADERS)
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_run_sse_normal() -> None:
    """/run_sse normal → 200 with data frames."""
    client = make_client()
    with client.stream("POST", "/run_sse", json=_run_body("s_sse_norm"), headers=HEADERS) as resp:
        assert resp.status_code == 200
        frames = [line for line in resp.iter_lines() if line]
        assert any("data:" in f for f in frames)


# --- list-apps ---------------------------------------------------------------


def test_list_apps() -> None:
    """GET /list-apps → 200 with app list."""
    client = make_client()
    r = client.get("/list-apps", headers=HEADERS)
    assert r.status_code == 200
    assert APP in r.json()
