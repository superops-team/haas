"""Branch-coverage fill-in tests (file B): deeper error/edge paths in haas/api.py.

Targets the delegated-sessions routes, /run + /run_sse adapter/backpressure/replay
branches, session policy update conflicts, and artifact upload/download/limit paths.
Pure error-path and edge assertions; does not modify source.
"""

import pytest
from fastapi.testclient import TestClient

from haas.api import build_app
from haas.harnesses.base import AdapterTurnStartError, HarnessEvent, TurnHandle
from haas.harnesses.fake import FakeAdapter
from haas.identity import Principal

TOKEN = "test-token-b"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}
APP = "chrn_codex_default"


def make_client(**kwargs: object) -> TestClient:
    tokens = {TOKEN: Principal(principalId="p_1", tenantId="t1", userIds=frozenset({"u_1"}))}
    app = build_app(
        adapter=kwargs.get("adapter", FakeAdapter()),
        identity_tokens=tokens,
        run_quota=int(kwargs.get("run_quota", 20)),
        rate_limit=int(kwargs.get("rate_limit", 100)),
    )
    return TestClient(app)


def _code(resp) -> str:
    return resp.json()["haasError"]["code"]


def _run_body(session_id: str) -> dict[str, object]:
    return {
        "appName": APP,
        "userId": "u_1",
        "sessionId": session_id,
        "newMessage": {"role": "user", "parts": [{"text": "hi"}]},
    }


def _valid_delegated_body(harness_id: str = APP) -> dict[str, object]:
    return {
        "managerSessionId": "mgr_1",
        "haasSessionId": "hses_delegated",
        "haasUserId": "u_1",
        "harnessId": harness_id,
        "harnessBase": "codex",
        "image": {"reference": "ghcr.io/example/sandbox@sha256:" + "a" * 64,
                  "digest": "sha256:" + "a" * 64},
        "provider": {
            "providerId": "p1",
            "model": "m1",
            "credentialRef": "secret://vault/cred",
        },
        "mountManifest": {
            "version": 1,
            "primaryWorkspace": {
                "hostPathCanonical": "/tmp/haas-ws-1",
                "containerPath": "/workspace",
                "access": "rw",
            },
        },
    }


# --- Delegated-sessions routes ----------------------------------------------


def test_delegated_get_not_found_404() -> None:
    client = make_client()
    resp = client.get("/v1/haas/delegated-sessions/dg_nope", headers=HEADERS)
    assert resp.status_code == 404
    assert _code(resp) == "haas_delegated_session_not_found"


def test_delegated_create_non_object_body_400() -> None:
    client = make_client()
    resp = client.post(
        "/v1/haas/delegated-sessions",
        content="[1]",
        headers={**HEADERS, "Content-Type": "application/json"},
    )
    assert resp.status_code == 400
    assert _code(resp) == "invalid_input"


def test_delegated_create_missing_fields_400() -> None:
    client = make_client()
    resp = client.post("/v1/haas/delegated-sessions", json={}, headers=HEADERS)
    assert resp.status_code == 400
    assert _code(resp) == "invalid_input"


def test_delegated_create_unknown_harness_404() -> None:
    client = make_client()
    body = _valid_delegated_body(harness_id="chrn_nope")
    resp = client.post("/v1/haas/delegated-sessions", json=body, headers=HEADERS)
    assert resp.status_code == 404
    assert _code(resp) == "haas_harness_not_found"


def test_delegated_create_invalid_image_digest_400() -> None:
    client = make_client()
    body = _valid_delegated_body()
    body["image"] = {"reference": "ghcr.io/example/sandbox:latest"}  # unpinned, no digest
    resp = client.post("/v1/haas/delegated-sessions", json=body, headers=HEADERS)
    assert resp.status_code == 400
    assert _code(resp) == "invalid_input"


def test_delegated_create_bad_credential_ref_400() -> None:
    client = make_client()
    body = _valid_delegated_body()
    body["provider"]["credentialRef"] = "plaintext-key"
    resp = client.post("/v1/haas/delegated-sessions", json=body, headers=HEADERS)
    assert resp.status_code == 400
    assert _code(resp) == "invalid_input"


def test_delegated_create_base_mismatch_400() -> None:
    client = make_client()
    body = _valid_delegated_body()
    body["harnessBase"] = "pi"  # default harness base is codex
    resp = client.post("/v1/haas/delegated-sessions", json=body, headers=HEADERS)
    assert resp.status_code == 400
    assert _code(resp) == "invalid_input"


def test_delegated_restore_not_found_404() -> None:
    client = make_client()
    resp = client.post("/v1/haas/delegated-sessions/dg_nope/restore", headers=HEADERS)
    assert resp.status_code == 404
    assert _code(resp) == "haas_delegated_session_not_found"


# --- /run and /run_sse deeper branches --------------------------------------


def test_run_non_object_body_400() -> None:
    client = make_client()
    resp = client.post(
        "/run", content="[1]", headers={**HEADERS, "Content-Type": "application/json"}
    )
    assert resp.status_code == 400
    assert _code(resp) == "invalid_input"


def test_run_invalid_timeout_seconds_400() -> None:
    client = make_client()
    body = _run_body("hsess_timeout")
    body["haas"] = {"timeoutSeconds": "not-a-number"}
    resp = client.post("/run", json=body, headers=HEADERS)
    assert resp.status_code == 400
    assert _code(resp) == "invalid_input"


def test_run_invalid_session_id_type_400() -> None:
    client = make_client()
    body = _run_body(None)  # type: ignore[arg-type]
    body["sessionId"] = 12345  # not a string
    resp = client.post("/run", json=body, headers=HEADERS)
    assert resp.status_code == 400
    assert _code(resp) == "invalid_input"


class _RaisingStartTurnAdapter(FakeAdapter):
    """start_turn fails -> run_stream surfaces AdapterTurnError -> 502."""

    base = "fake-raising"
    adapter_id = "fake-raising"

    async def start_turn(self, request):  # type: ignore[override]
        raise AdapterTurnStartError(code="boom", detail="start failed", retryable=True)


def test_run_adapter_start_error_502() -> None:
    client = make_client(adapter=_RaisingStartTurnAdapter())
    resp = client.post("/run", json=_run_body("hsess_raising"), headers=HEADERS)
    assert resp.status_code == 502
    assert _code(resp) == "haas_adapter_error"


def test_run_sse_replay_requires_session_id_400() -> None:
    client = make_client()
    body = {"appName": APP, "userId": "u_1", "newMessage": {"role": "user", "parts": []}}
    resp = client.post(
        "/run_sse",
        json=body,
        headers={**HEADERS, "Last-Event-ID": "evt_unknown"},
    )
    assert resp.status_code == 400
    assert _code(resp) == "invalid_input"


def test_run_sse_replay_unknown_cursor_410() -> None:
    client = make_client()
    # establish a real session first
    with client.stream("POST", "/run_sse", json=_run_body("hsess_cur"), headers=HEADERS) as resp:
        resp.read()
    resp = client.post(
        "/run_sse",
        json=_run_body("hsess_cur"),
        headers={**HEADERS, "Last-Event-ID": "evt_does_not_exist"},
    )
    assert resp.status_code == 410
    assert _code(resp) == "haas_offset_expired"


def test_run_sse_backpressure_disconnects(monkeypatch: pytest.MonkeyPatch) -> None:
    import haas.api as api_mod

    monkeypatch.setattr(api_mod, "SSE_QUEUE_MAXSIZE", 1)

    class _Flood(FakeAdapter):
        base = "fake-flood-b"
        adapter_id = "fake-flood-b"

        async def stream_events(self, handle: TurnHandle):  # type: ignore[override]
            for i in range(500):
                yield HarnessEvent(
                    type="harness.text.delta",
                    nativeType="fake/text",
                    invocationId=handle.invocationId,
                    sessionId=handle.sessionId,
                    turnId=handle.turnId,
                    author=self.base,
                    content={"role": "model", "parts": [{"text": f"m-{i}"}]},
                    actions={"stateDelta": {"seq": i}},
                )

    client = make_client(adapter=_Flood())
    with client.stream(
        "POST", "/run_sse", json=_run_body("hsess_bp"), headers=HEADERS
    ) as resp:
        text = resp.read().decode()
    assert "haas_sse_backpressure" in text


def test_run_quota_exceeded_429() -> None:
    client = make_client(run_quota=0)
    resp = client.post("/run", json=_run_body("hsess_quota"), headers=HEADERS)
    assert resp.status_code == 429
    assert _code(resp) in {"haas_rate_limited", "haas_quota_exceeded"}


def test_run_idempotency_conflict_409() -> None:
    client = make_client()
    key = "idem-key-1"
    h1 = {**HEADERS, "Idempotency-Key": key, "X-Haas-Nonce": "1"}
    h2 = {**HEADERS, "Idempotency-Key": key, "X-Haas-Nonce": "2"}
    # same key, different request body hash -> conflict on the second
    client.post("/run", json=_run_body("hsess_idem"), headers=h1)
    body = _run_body("hsess_idem")
    body["newMessage"] = {"role": "user", "parts": [{"text": "different"}]}
    resp = client.post("/run", json=body, headers=h2)
    assert resp.status_code == 409
    assert _code(resp) == "haas_idempotency_conflict"


# --- Session policy update routes -------------------------------------------


def test_policy_unknown_session_404() -> None:
    client = make_client()
    resp = client.post(
        "/v1/haas/sessions/hsess_nope/policy",
        json={"expectedRevision": 1, "policy": {}},
        headers={**HEADERS, "Idempotency-Key": "pk-1"},
    )
    assert resp.status_code == 404


def _make_session(client: TestClient, sid: str) -> None:
    with client.stream("POST", "/run_sse", json=_run_body(sid), headers=HEADERS) as resp:
        resp.read()


def test_policy_invalid_expected_revision_400() -> None:
    client = make_client()
    _make_session(client, "hsess_polbad")
    resp = client.post(
        "/v1/haas/sessions/hsess_polbad/policy",
        json={"expectedRevision": "not-int", "policy": {}},
        headers={**HEADERS, "Idempotency-Key": "pk-2"},
    )
    assert resp.status_code == 400
    assert _code(resp) == "invalid_input"


def test_policy_invalid_policy_payload_400() -> None:
    client = make_client()
    _make_session(client, "hsess_polbad2")
    resp = client.post(
        "/v1/haas/sessions/hsess_polbad2/policy",
        json={"expectedRevision": 1, "policy": "not-a-dict"},
        headers={**HEADERS, "Idempotency-Key": "pk-3"},
    )
    assert resp.status_code == 400
    assert _code(resp) == "invalid_input"


def test_policy_revision_conflict_409() -> None:
    client = make_client()
    # create a real session via a successful run
    with client.stream(
        "POST", "/run_sse", json=_run_body("hsess_pol"), headers=HEADERS
    ) as resp:
        resp.read()
    resp = client.post(
        "/v1/haas/sessions/hsess_pol/policy",
        json={"expectedRevision": 999999, "policy": {"network": {"defaultAction": "deny"}}},
        headers={**HEADERS, "Idempotency-Key": "pk-4"},
    )
    assert resp.status_code == 409
    assert _code(resp) == "haas_policy_revision_conflict"


# --- Artifact routes ---------------------------------------------------------


def test_upload_file_too_large_413(monkeypatch: pytest.MonkeyPatch) -> None:

    # shrink the artifact policy max size via the app's artifact store
    client = make_client()
    client.app.state.runtime.artifacts.policy.maxFileBytes = 4
    resp = client.post(
        "/v1/haas/files",
        files={"file": ("big.txt", b"a bit more than four bytes", "text/plain")},
        headers=HEADERS,
    )
    assert resp.status_code == 413
    assert _code(resp) == "haas_file_too_large"


def test_upload_file_empty_filename_400() -> None:
    client = make_client()
    resp = client.post(
        "/v1/haas/files",
        files={"file": ("", b"data", "text/plain")},
        headers=HEADERS,
    )
    # FastAPI multipart rejects an empty filename before our validation.
    assert resp.status_code == 422


def test_upload_file_absolute_path_rejected_400() -> None:
    client = make_client()
    resp = client.post(
        "/v1/haas/files",
        files={"file": ("/etc/passwd", b"data", "text/plain")},
        headers=HEADERS,
    )
    assert resp.status_code == 400
    assert _code(resp) == "invalid_input"


def test_upload_then_download_roundtrip() -> None:
    client = make_client()
    up = client.post(
        "/v1/haas/files",
        files={"file": ("note.txt", b"hello-b", "text/plain")},
        headers=HEADERS,
    )
    assert up.status_code == 200
    fid = up.json()["data"]["id"]
    dl = client.get(f"/v1/haas/files/{fid}/content", headers=HEADERS)
    assert dl.status_code == 200
    assert dl.content == b"hello-b"
    assert dl.headers["X-Content-Type-Options"] == "nosniff"


def test_preview_pdf_unavailable_501() -> None:
    client = make_client()
    up = client.post(
        "/v1/haas/files",
        files={"file": ("note2.txt", b"abc", "text/plain")},
        headers=HEADERS,
    )
    fid = up.json()["data"]["id"]
    resp = client.get(f"/v1/haas/files/{fid}/pdf", headers=HEADERS)
    assert resp.status_code == 501
    assert _code(resp) == "haas_preview_unavailable"


# --- Session events / invocations ------------------------------------------


def test_session_events_unknown_session_404() -> None:
    client = make_client()
    resp = client.get("/v1/haas/sessions/hsess_nope/events", headers=HEADERS)
    assert resp.status_code == 404


def test_get_invocation_unknown_session_404() -> None:
    client = make_client()
    resp = client.get(
        "/v1/haas/sessions/hsess_nope/invocations/inv_nope", headers=HEADERS
    )
    assert resp.status_code == 404


def test_list_session_artifacts_unknown_session_404() -> None:
    client = make_client()
    resp = client.get("/v1/haas/sessions/hsess_nope/artifacts", headers=HEADERS)
    assert resp.status_code == 404


def test_run_successful_returns_completed_event() -> None:
    client = make_client()
    resp = client.post("/run", json=_run_body("hsess_ok"), headers=HEADERS)
    assert resp.status_code == 200
    events = resp.json()
    assert isinstance(events, list) and len(events) >= 1


# --- Extra edge paths -------------------------------------------------------


def test_delegated_create_success_envelope() -> None:
    client = make_client()
    resp = client.post(
        "/v1/haas/delegated-sessions", json=_valid_delegated_body(), headers=HEADERS
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["haasSessionId"] == "hses_delegated"
    # round-trip via GET
    got = client.get(f"/v1/haas/delegated-sessions/{data['id']}", headers=HEADERS)
    assert got.status_code == 200
    assert got.json()["data"]["id"] == data["id"]


def test_list_apps_includes_default() -> None:
    client = make_client()
    resp = client.get("/list-apps", headers=HEADERS)
    assert resp.status_code == 200
    assert APP in str(resp.json())


def test_cancel_invocation_unknown_404() -> None:
    client = make_client()
    resp = client.post(
        "/v1/haas/sessions/hsess_nope/invocations/inv_nope/cancel", headers=HEADERS
    )
    assert resp.status_code == 404
    assert _code(resp) == "haas_invocation_not_found"


def test_run_sse_emits_data_and_heartbeat() -> None:
    client = make_client()
    with client.stream(
        "POST", "/run_sse", json=_run_body("hsess_hb"), headers=HEADERS
    ) as resp:
        assert resp.status_code == 200
        text = resp.read().decode()
    assert "data: " in text
    assert "hello" in text and "world" in text
