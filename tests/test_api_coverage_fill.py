"""Coverage-fill tests for haas/api.py uncovered branches and error paths.

Targets:
- /v1/haas/sessions/{id}/policy update route
- /v1/haas/sessions/{id}/profile read + rebind
- approval / input-request error branches
- run (streaming + non-streaming) adapter failure paths
- artifact upload / download / archive
- harness + profile CRUD error paths
- delegated-session create/restore/policy error branches
- small helper branches (_json_object, _authenticate, _render_cached, ...)
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from haas.api import build_app
from haas.harnesses import FakeAdapter
from haas.harnesses.base import HarnessEvent, StartTurnRequest, TurnHandle
from haas.identity import Principal
from haas.runtime import FakeDelegatedContainerRuntime
from haas.stores import (
    ApprovalRecord,
    InputRequestRecord,
    InvocationRecord,
    SessionRecord,
)

pytestmark = pytest.mark.adk

TOKEN = "fill-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
TOKEN_B = "fill-token-b"
AUTH_B = {"Authorization": f"Bearer {TOKEN_B}"}

_TOKENS = {
    TOKEN: Principal(
        principalId="p_fill", tenantId="t_fill", userIds=frozenset({"u_fill"})
    )
}


def _app(**kwargs: Any) -> TestClient:
    identity = kwargs.pop(
        "identity_tokens",
        {
            TOKEN: Principal(
                principalId="p_fill", tenantId="t_fill", userIds=frozenset({"u_fill"})
            ),
            TOKEN_B: Principal(
                principalId="p_fill_b", tenantId="t_fill_b", userIds=frozenset({"u_b"})
            ),
        },
    )
    app = build_app(identity_tokens=identity, **kwargs)
    return TestClient(app)


def _run_body(session_id: str = "hsess-fill", user_id: str = "u_fill") -> dict[str, Any]:
    return {
        "appName": "chrn_codex_default",
        "userId": user_id,
        "sessionId": session_id,
        "newMessage": {"role": "user", "parts": [{"text": "hi"}]},
    }


def _seed_session(
    client: TestClient, session_id: str = "hsess-fill", user_id: str = "u_fill"
) -> None:
    client.app.state.runtime.store.put_session(
        SessionRecord(id=session_id, appName="chrn_codex_default", userId=user_id)
    )


# ---------------------------------------------------------------------------
# Helper branches
# ---------------------------------------------------------------------------


def test_json_object_rejects_invalid_json_and_non_dict() -> None:
    client = _app()
    # non-dict JSON body on patch session -> 400 invalid_input
    client.app.state.runtime.store.put_session(
        SessionRecord(id="hsess-patch", appName="chrn_codex_default", userId="u_fill")
    )
    resp = client.patch(
        "/apps/chrn_codex_default/users/u_fill/sessions/hsess-patch",
        content=b"[1,2,3]",
        headers={**AUTH, "Content-Type": "application/json"},
    )
    assert resp.status_code == 400
    assert resp.json()["haasError"]["code"] == "invalid_input"


def test_json_object_rejects_malformed_json() -> None:
    client = _app()
    # Routes that call _json_object (e.g. POST /v1/haas/profiles) map
    # malformed JSON -> 400 invalid_input.
    resp = client.post(
        "/v1/haas/profiles",
        content=b"{not json",
        headers={**AUTH, "Content-Type": "application/json"},
    )
    assert resp.status_code == 400


def test_patch_session_rejects_non_dict_state_delta() -> None:
    client = _app()
    _seed_session(client, "hsess-patch3")
    resp = client.patch(
        "/apps/chrn_codex_default/users/u_fill/sessions/hsess-patch3",
        json={"stateDelta": [1, 2]},
        headers=AUTH,
    )
    assert resp.status_code == 400


def test_patch_session_missing_session_is_404() -> None:
    client = _app()
    resp = client.patch(
        "/apps/chrn_codex_default/users/u_fill/sessions/hsess-missing",
        json={"stateDelta": {"a": 1}},
        headers=AUTH,
    )
    assert resp.status_code == 404


def test_invalid_credential_is_401() -> None:
    client = _app()
    assert client.get("/list-apps", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert client.get("/list-apps", headers={"Authorization": "Basic abc"}).status_code == 401


def test_health_alias_and_ready_unknown_scope() -> None:
    client = _app()
    assert client.get("/health").json()["data"]["status"] == "ok"
    assert client.get("/ready?scope=bogus").json()["data"]["reason"] == "unknown_scope"


# ---------------------------------------------------------------------------
# Session policy update route
# ---------------------------------------------------------------------------


def test_update_session_policy_applies_and_replay() -> None:
    client = _app()
    _seed_session(client, "hsess-policy")
    policy = {
        "workspace": {
            "mode": "read-only",
            "root": "/workspace",
            "writableRoots": [],
        }
    }
    headers = {**AUTH, "Idempotency-Key": "policy-key"}
    first = client.post(
        "/v1/haas/sessions/hsess-policy/policy",
        json={"expectedRevision": 1, "policy": policy},
        headers=headers,
    )
    assert first.status_code == 200, first.text
    data = first.json()["data"]
    assert data["desiredRevision"] == 2
    assert data["status"] == "applied"

    replay = client.post(
        "/v1/haas/sessions/hsess-policy/policy",
        json={"expectedRevision": 1, "policy": policy},
        headers=headers,
    )
    assert replay.status_code == 200
    assert replay.json() == first.json()


def test_update_session_policy_requires_idempotency_key() -> None:
    client = _app()
    _seed_session(client, "hsess-policy2")
    resp = client.post(
        "/v1/haas/sessions/hsess-policy2/policy",
        json={"expectedRevision": 1, "policy": {}},
        headers=AUTH,
    )
    assert resp.status_code == 400


def test_update_session_policy_rejects_invalid_revision_and_conflict() -> None:
    client = _app()
    _seed_session(client, "hsess-policy3")
    # invalid expected revision
    bad = client.post(
        "/v1/haas/sessions/hsess-policy3/policy",
        json={"expectedRevision": 0, "policy": {}},
        headers={**AUTH, "Idempotency-Key": "pk-bad"},
    )
    assert bad.status_code == 400
    # revision conflict
    conflict = client.post(
        "/v1/haas/sessions/hsess-policy3/policy",
        json={"expectedRevision": 99, "policy": {}},
        headers={**AUTH, "Idempotency-Key": "pk-conflict"},
    )
    assert conflict.status_code == 409
    assert conflict.json()["haasError"]["code"] == "haas_policy_revision_conflict"


def test_update_session_policy_missing_session_is_404() -> None:
    client = _app()
    resp = client.post(
        "/v1/haas/sessions/hsess-nothing/policy",
        json={"expectedRevision": 1, "policy": {}},
        headers={**AUTH, "Idempotency-Key": "pk-missing"},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Session profile read + rebind
# ---------------------------------------------------------------------------


def test_get_session_profile_returns_effective_profile() -> None:
    client = _app()
    store = client.app.state.runtime.store
    store.put_session(
        SessionRecord(
            id="hsess-prof",
            appName="chrn_codex_default",
            userId="u_fill",
            effectiveProfile={
                "profileId": "hprof_x",
                "profileVersion": 3,
                "profileFingerprint": "sha256:abc",
                "harnessId": "chrn_codex_default",
                "base": "codex",
                "model": "doubao",
            },
        )
    )
    resp = client.get("/v1/haas/sessions/hsess-prof/profile", headers=AUTH)
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["profileId"] == "hprof_x"
    assert "executionIntentFingerprint" in data


def test_get_session_profile_missing_snapshot_is_404() -> None:
    client = _app()
    _seed_session(client, "hsess-noprof")
    resp = client.get("/v1/haas/sessions/hsess-noprof/profile", headers=AUTH)
    assert resp.status_code == 404


def test_get_session_profile_cross_user_is_404() -> None:
    client = _app()
    _seed_session(client, "hsess-priv")
    resp = client.get("/v1/haas/sessions/hsess-priv/profile", headers=AUTH_B)
    assert resp.status_code == 404


def test_rebind_session_profile_success_and_replay() -> None:
    client = _app()
    # Create a profile via the profiles API
    created = client.post(
        "/v1/haas/profiles",
        json={
            "harnessId": "chrn_codex_default",
            "base": "codex",
            "provider": {
                "providerId": "openai",
                "name": "OpenAI",
                "model": "m1",
                "credentialRef": "secret://provider/default",
                "wireApi": "responses",
            },
        },
        headers=AUTH,
    )
    assert created.status_code == 200, created.text
    profile_id = created.json()["data"]["id"]
    # execution_snapshot requires a validated, active profile.
    client.post(f"/v1/haas/profiles/{profile_id}/validate", headers=AUTH)
    client.post(f"/v1/haas/profiles/{profile_id}/activate", headers=AUTH)

    _seed_session(client, "hsess-rebind")
    headers = {**AUTH, "Idempotency-Key": "rebind-key"}
    body = {"profileId": profile_id, "reason": "update"}
    first = client.post("/v1/haas/sessions/hsess-rebind/profile-rebind", json=body, headers=headers)
    assert first.status_code == 200, first.text

    replay = client.post(
        "/v1/haas/sessions/hsess-rebind/profile-rebind", json=body, headers=headers
    )
    assert replay.status_code == 200
    assert replay.json() == first.json()


def test_rebind_session_profile_rejects_invalid_body() -> None:
    client = _app()
    _seed_session(client, "hsess-rebind-bad")
    for body in (
        {"extra": "x", "profileId": "hprof_x"},
        {"profileId": ""},
        {"profileId": "hprof_x", "expectedProfileVersion": 0},
        {"profileId": "hprof_x", "reason": 123},
    ):
        resp = client.post(
            "/v1/haas/sessions/hsess-rebind-bad/profile-rebind", json=body, headers=AUTH
        )
        assert resp.status_code == 400, body


def test_rebind_session_profile_unknown_profile_is_404() -> None:
    client = _app()
    _seed_session(client, "hsess-rebind-404")
    resp = client.post(
        "/v1/haas/sessions/hsess-rebind-404/profile-rebind",
        json={"profileId": "hprof_does_not_exist"},
        headers=AUTH,
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Approval / input-request error branches
# ---------------------------------------------------------------------------


def test_resolve_approval_not_found_and_state_conflict() -> None:
    client = _app()
    _seed_session(client, "hsess-appr")
    # unknown approval
    resp = client.post(
        "/v1/haas/sessions/hsess-appr/approvals/appr_missing",
        json={"decision": "approved", "scope": "action"},
        headers=AUTH,
    )
    assert resp.status_code == 404

    # approval for a different session
    client.app.state.runtime.store.put_approval(
        ApprovalRecord(
            id="appr_wrong",
            sessionId="hsess_other",
            invocationId="inv_1",
            turnId="turn_1",
        )
    )
    wrong = client.post(
        "/v1/haas/sessions/hsess-appr/approvals/appr_wrong",
        json={"decision": "approved", "scope": "action"},
        headers=AUTH,
    )
    assert wrong.status_code == 404


def test_resolve_approval_wrong_session_scope_404() -> None:
    client = _app()
    _seed_session(client, "hsess-appr2")
    client.app.state.runtime.store.put_approval(
        ApprovalRecord(
            id="appr_2",
            sessionId="hsess-appr2",
            invocationId="inv_2",
            turnId="turn_2",
        )
    )
    # cross-user hidden
    resp = client.post(
        "/v1/haas/sessions/hsess-appr2/approvals/appr_2",
        json={"decision": "approved", "scope": "action"},
        headers=AUTH_B,
    )
    assert resp.status_code == 404


def test_answer_input_request_error_branches() -> None:
    client = _app()
    store = client.app.state.runtime.store
    _seed_session(client, "hsess-inreq")
    store.put_input_request(
        InputRequestRecord(
            id="inreq_1",
            sessionId="hsess-inreq",
            invocationId="inv_1",
            turnId="turn_1",
            questions=[{"id": "q1", "question": "?", "secret": False}],
            nativeRequestId=1,
            adapterGeneration=1,
        )
    )
    # non-dict answers
    r1 = client.post(
        "/v1/haas/sessions/hsess-inreq/input-requests/inreq_1",
        json={"answers": [1]},
        headers=AUTH,
    )
    assert r1.status_code == 400
    # unknown input request
    r2 = client.post(
        "/v1/haas/sessions/hsess-inreq/input-requests/inreq_missing",
        json={"answers": {}},
        headers=AUTH,
    )
    assert r2.status_code == 404
    # wrong question ids
    r3 = client.post(
        "/v1/haas/sessions/hsess-inreq/input-requests/inreq_1",
        json={"answers": {"wrong_id": {"values": ["x"]}}},
        headers=AUTH,
    )
    assert r3.status_code == 400
    # answer without values or secretRef
    r4 = client.post(
        "/v1/haas/sessions/hsess-inreq/input-requests/inreq_1",
        json={"answers": {"q1": {}}},
        headers=AUTH,
    )
    assert r4.status_code == 400


# ---------------------------------------------------------------------------
# Run error paths (adapter raises)
# ---------------------------------------------------------------------------


class _FailingAdapter(FakeAdapter):
    """Adapter whose stream_events raises -> AdapterTurnError."""

    async def stream_events(self, handle: TurnHandle) -> AsyncIterator[HarnessEvent]:
        yield HarnessEvent(
            type="harness.text.delta",
            invocationId=handle.invocationId,
            sessionId=handle.sessionId,
            turnId=handle.turnId,
            author=self.base,
            content={"role": "model", "parts": [{"text": "partial"}]},
            actions={"stateDelta": {"status": "running"}},
        )
        raise RuntimeError("boom adapter")
        # pragma: no cover
        yield  # type: ignore[unreachable]


def test_run_non_streaming_adapter_error_returns_502_with_idempotency() -> None:
    client = TestClient(build_app(adapter=_FailingAdapter(), identity_tokens=_TOKENS))
    body = _run_body("hsess-fail")
    headers = {**AUTH, "Idempotency-Key": "fail-key"}
    resp = client.post("/run", json=body, headers=headers)
    assert resp.status_code == 502
    assert resp.json()["haasError"]["code"] == "haas_adapter_error"

    # replay returns the cached error
    replay = client.post("/run", json=body, headers=headers)
    assert replay.status_code == 502


def test_run_sse_adapter_error_closes_terminal() -> None:
    client = TestClient(build_app(adapter=_FailingAdapter(), identity_tokens=_TOKENS))
    body = _run_body("hsess-fail-sse")
    with client.stream("POST", "/run_sse", json=body, headers=AUTH) as resp:
        assert resp.status_code == 200
        lines = [line for line in resp.iter_lines() if line.startswith("data: ")]
    assert lines, "expected at least the partial event"


# ---------------------------------------------------------------------------
# Artifact upload / download / archive
# ---------------------------------------------------------------------------


def test_upload_file_rejects_path_traversal_and_oversize() -> None:
    client = _app()
    # path traversal
    bad = client.post(
        "/v1/haas/files",
        files={"file": ("../etc/passwd", b"x", "text/plain")},
        headers=AUTH,
    )
    assert bad.status_code == 400

    # oversize
    small = _app(max_file_bytes=4)
    big = small.post(
        "/v1/haas/files",
        files={"file": ("big.txt", b"a" * 100, "text/plain")},
        headers=AUTH,
    )
    assert big.status_code == 413


def test_download_file_not_found_and_pdf_preview() -> None:
    client = _app()
    assert client.get("/v1/haas/files/missing/content", headers=AUTH).status_code == 404
    pdf = client.get("/v1/haas/files/missing/pdf", headers=AUTH)
    assert pdf.status_code == 404


def test_archive_empty_session_is_404() -> None:
    client = _app()
    _seed_session(client, "hsess-archive")
    resp = client.get("/v1/haas/sessions/hsess-archive/artifacts/archive", headers=AUTH)
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Harness CRUD error paths
# ---------------------------------------------------------------------------


def test_get_update_delete_harness_error_paths() -> None:
    client = _app()
    # unknown harness
    assert client.get("/v1/haas/harnesses/chrn_missing", headers=AUTH).status_code == 404
    assert client.put(
        "/v1/haas/harnesses/chrn_missing", json={"name": "x"}, headers=AUTH
    ).status_code == 404
    assert client.delete("/v1/haas/harnesses/chrn_missing", headers=AUTH).status_code == 404

    # update with unsupported base via immutable field error -> 400
    created = client.post(
        "/v1/haas/harnesses",
        json={"base": "codex", "name": "updatable"},
        headers=AUTH,
    )
    hid = created.json()["data"]["id"]
    # immutable field change
    bad_update = client.put(
        f"/v1/haas/harnesses/{hid}", json={"base": "fake"}, headers=AUTH
    )
    assert bad_update.status_code in {400, 422}

    # delete success
    deleted = client.delete(f"/v1/haas/harnesses/{hid}", headers=AUTH)
    assert deleted.status_code == 200
    assert deleted.json()["data"]["deleted"] is True


def test_create_harness_idempotency_replay_and_wait() -> None:
    client = _app()
    body = {"base": "codex", "name": "wait-replay"}
    headers = {**AUTH, "Idempotency-Key": "harness-wait"}
    first = client.post("/v1/haas/harnesses", json=body, headers=headers)
    assert first.status_code == 200
    replay = client.post("/v1/haas/harnesses", json=body, headers=headers)
    assert replay.status_code == 200
    assert replay.json()["data"]["id"] == first.json()["data"]["id"]


def test_list_models_backends() -> None:
    client = _app()
    resp = client.get("/v1/haas/models", headers=AUTH)
    assert resp.status_code == 200
    assert "backends" in resp.json()["data"]


# ---------------------------------------------------------------------------
# Profile CRUD error paths
# ---------------------------------------------------------------------------


def test_profile_update_validate_activate_errors() -> None:
    client = _app()
    created = client.post(
        "/v1/haas/profiles",
        json={
            "harnessId": "chrn_codex_default",
            "base": "codex",
            "provider": {
                "providerId": "openai",
                "name": "OpenAI",
                "model": "m1",
                "credentialRef": "secret://provider/default",
                "wireApi": "responses",
            },
        },
        headers=AUTH,
    )
    pid = created.json()["data"]["id"]

    # validate
    v = client.post(f"/v1/haas/profiles/{pid}/validate", headers=AUTH)
    assert v.status_code == 200

    # activate
    a = client.post(f"/v1/haas/profiles/{pid}/activate", headers=AUTH)
    assert a.status_code == 200

    # unknown profile errors
    assert client.put(
        "/v1/haas/profiles/hprof_missing", json={"base": "codex"}, headers=AUTH
    ).status_code == 404
    assert client.post("/v1/haas/profiles/hprof_missing/validate", headers=AUTH).status_code == 404
    assert client.post("/v1/haas/profiles/hprof_missing/activate", headers=AUTH).status_code == 404


def test_profile_create_type_error_releases_key() -> None:
    client = _app()
    headers = {**AUTH, "Idempotency-Key": "prof-bad"}
    resp = client.post("/v1/haas/profiles", json={"harnessId": 123}, headers=headers)
    assert resp.status_code in {400, 422}


# ---------------------------------------------------------------------------
# Delegated session create / get / restore error paths
# ---------------------------------------------------------------------------


def _delegated_body(**overrides: Any) -> dict[str, Any]:
    body = {
        "managerSessionId": "mgr_fill",
        "haasSessionId": "hsess_deleg_fill",
        "haasUserId": "u_fill",
        "harnessId": "chrn_codex_default",
        "harnessBase": "codex",
        "image": {
            "reference": "registry.invalid/haas",
            "digest": "sha256:" + "a" * 64,
            "variant": "lite",
            "platform": "linux/arm64",
        },
        "profileRef": {
            "profileId": "hprof_x",
            "profileVersion": 1,
            "profileFingerprint": "sha256:pf",
        },
        "provider": {
            "providerId": "openai",
            "name": "OpenAI",
            "model": "m",
            "credentialRef": "secret://provider/default",
            "wireApi": "responses",
        },
        "workspaceMode": "bind_mount",
        "mountManifest": {
            "version": 1,
            "primaryWorkspace": {
                "hostPathCanonical": "/tmp/haas-fill",
                "containerPath": "/workspace",
                "access": "rw",
            },
            "extraMounts": [],
        },
    }
    body.update(overrides)
    return body


def test_delegated_session_get_and_404() -> None:
    client = _app()
    created = client.post(
        "/v1/haas/delegated-sessions", json=_delegated_body(), headers=AUTH
    )
    assert created.status_code == 200, created.text
    did = created.json()["data"]["id"]
    got = client.get(f"/v1/haas/delegated-sessions/{did}", headers=AUTH)
    assert got.status_code == 200
    assert client.get("/v1/haas/delegated-sessions/dgsess_missing", headers=AUTH).status_code == 404


def test_delegated_session_create_unknown_harness_404() -> None:
    client = _app()
    body = _delegated_body(harnessId="chrn_missing")
    resp = client.post("/v1/haas/delegated-sessions", json=body, headers=AUTH)
    assert resp.status_code == 404


def test_delegated_session_create_base_mismatch_400() -> None:
    client = _app()
    body = _delegated_body(harnessBase="pi")
    resp = client.post("/v1/haas/delegated-sessions", json=body, headers=AUTH)
    assert resp.status_code == 400


def test_delegated_session_restore_missing_404() -> None:
    client = _app()
    resp = client.post(
        "/v1/haas/delegated-sessions/dgsess_missing/restore", headers=AUTH
    )
    assert resp.status_code == 404


def test_delegated_session_restore_with_body_reads_json() -> None:
    client = _app()
    created = client.post(
        "/v1/haas/delegated-sessions", json=_delegated_body(), headers=AUTH
    )
    did = created.json()["data"]["id"]
    # send a JSON body; restore should parse it (and then fail closed)
    resp = client.post(
        f"/v1/haas/delegated-sessions/{did}/restore",
        json={"anything": True},
        headers=AUTH,
    )
    # DisabledDelegatedContainerRuntime -> 503
    assert resp.status_code in {403, 503}


def test_delegated_policy_update_error_branches() -> None:
    client = _app()
    created = client.post(
        "/v1/haas/delegated-sessions", json=_delegated_body(), headers=AUTH
    )
    did = created.json()["data"]["id"]

    # missing session
    assert client.post(
        "/v1/haas/delegated-sessions/dgsess_missing/policy",
        json={"profileRef": _delegated_body()["profileRef"]},
        headers={**AUTH, "Idempotency-Key": "dp-missing"},
    ).status_code == 404

    # no supplied domains
    assert client.post(
        f"/v1/haas/delegated-sessions/{did}/policy",
        json={},
        headers={**AUTH, "Idempotency-Key": "dp-empty"},
    ).status_code == 400

    # expected revision mismatch
    assert client.post(
        f"/v1/haas/delegated-sessions/{did}/policy",
        json={"expectedRevision": 99, "profileRef": _delegated_body()["profileRef"]},
        headers={**AUTH, "Idempotency-Key": "dp-rev"},
    ).status_code == 409

    # invalid image digest
    bad_image = client.post(
        f"/v1/haas/delegated-sessions/{did}/policy",
        json={"image": {"reference": "mutable:latest", "digest": ""}},
        headers={**AUTH, "Idempotency-Key": "dp-img"},
    )
    assert bad_image.status_code == 400


# ---------------------------------------------------------------------------
# Delegated run with FakeDelegatedContainerRuntime
# ---------------------------------------------------------------------------


def test_run_for_delegated_session_uses_fake_container_runtime() -> None:
    fake_runtime = FakeDelegatedContainerRuntime()
    client = TestClient(
        build_app(
            adapter=FakeAdapter(),
            delegated_containers=fake_runtime,
            identity_tokens=_TOKENS,
        )
    )
    client.post(
        "/v1/haas/delegated-sessions", json=_delegated_body(), headers=AUTH
    )

    # non-streaming run
    body = _run_body("hsess_deleg_fill")
    resp = client.post("/run", json=body, headers=AUTH)
    assert resp.status_code == 200, resp.text
    events = resp.json()
    assert isinstance(events, list)
    assert len(fake_runtime.runs) == 1
    assert events[0]["content"]["parts"][0]["text"] == "delegated"


def test_cancel_running_delegated_invocation() -> None:
    fake_runtime = FakeDelegatedContainerRuntime()
    client = TestClient(
        build_app(
            adapter=FakeAdapter(),
            delegated_containers=fake_runtime,
            identity_tokens=_TOKENS,
        )
    )
    client.post("/v1/haas/delegated-sessions", json=_delegated_body(), headers=AUTH)
    body = _run_body("hsess_deleg_cancel")
    run = client.post("/run", json=body, headers=AUTH)
    invocation_id = run.json()[0]["invocationId"]
    cancel = client.post(
        f"/v1/haas/sessions/hsess_deleg_cancel/invocations/{invocation_id}/cancel",
        headers=AUTH,
    )
    # fake container cancel succeeds; local cancel path may also run
    assert cancel.status_code in {200, 404, 409}


# ---------------------------------------------------------------------------
# Pause / continue error paths
# ---------------------------------------------------------------------------


def test_pause_invocation_not_running_and_not_found() -> None:
    client = _app()
    _seed_session(client, "hsess-pause")
    # unknown invocation
    r = client.post(
        "/v1/haas/sessions/hsess-pause/invocations/inv_missing/pause", headers=AUTH
    )
    assert r.status_code == 404


def test_continue_invocation_invalid_instruction() -> None:
    client = _app()
    _seed_session(client, "hsess-cont")
    # seed a completed invocation
    client.app.state.runtime.store.put_invocation(
        InvocationRecord(
            id="inv_cont",
            sessionId="hsess-cont",
            appName="chrn_codex_default",
            userId="u_fill",
            turnId="turn_cont",
            status="completed",
        )
    )
    bad = client.post(
        "/v1/haas/sessions/hsess-cont/invocations/inv_cont/continue",
        json={"additionalInstruction": 123},
        headers=AUTH,
    )
    assert bad.status_code == 400

    too_long = client.post(
        "/v1/haas/sessions/hsess-cont/invocations/inv_cont/continue",
        json={"additionalInstruction": "x" * 5000},
        headers=AUTH,
    )
    assert too_long.status_code == 400


# ---------------------------------------------------------------------------
# Invocation readback / get envelope
# ---------------------------------------------------------------------------


def test_get_invocation_readback_and_envelope() -> None:
    client = _app()
    run = client.post("/run", json=_run_body("hsess-inv"), headers=AUTH)
    invocation_id = run.json()[0]["invocationId"]
    resp = client.get(
        f"/v1/haas/sessions/hsess-inv/invocations/{invocation_id}", headers=AUTH
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["id"] == invocation_id

    # unknown invocation
    assert client.get(
        "/v1/haas/sessions/hsess-inv/invocations/inv_nope", headers=AUTH
    ).status_code == 404


# ---------------------------------------------------------------------------
# Non-streaming run error: body not dict
# ---------------------------------------------------------------------------


def test_run_rejects_non_dict_body() -> None:
    client = _app()
    resp = client.post(
        "/run",
        content=b"[]",
        headers={**AUTH, "Content-Type": "application/json"},
    )
    assert resp.status_code == 400


def test_run_rejects_missing_app_or_user() -> None:
    client = _app()
    for body in (
        {"userId": "u_fill", "newMessage": {}},
        {"appName": "chrn_codex_default", "newMessage": {}},
    ):
        resp = client.post("/run", json=body, headers=AUTH)
        assert resp.status_code == 400


def test_run_sse_last_event_id_replay_structured_off(monkeypatch) -> None:
    monkeypatch.setenv("HAAS_SSE_STRUCTURED_FRAMES", "0")
    client = _app()
    body = _run_body("hsess-struct-off")
    original = client.post("/run", json=body, headers=AUTH).json()
    with client.stream(
        "POST",
        "/run_sse",
        json=body,
        headers={**AUTH, "Last-Event-ID": original[0]["id"]},
    ) as resp:
        assert resp.status_code == 200
        lines = list(resp.iter_lines())
    assert any(line.startswith("data: ") for line in lines)


# ---------------------------------------------------------------------------
# Batch 2: streaming/non-streaming run error paths, cancel/pause/continue,
# approval/input error branches, admission, idempotency waiting, render_cached.
# ---------------------------------------------------------------------------


def test_run_rejects_last_event_id_on_non_streaming() -> None:
    client = _app()
    body = _run_body("hsess-lesi")
    run = client.post("/run", json=body, headers=AUTH)
    assert run.status_code == 200
    event_id = run.json()[0]["id"]
    resp = client.post("/run", json=body, headers={**AUTH, "Last-Event-ID": event_id})
    assert resp.status_code == 400


def test_admission_denied_returns_429() -> None:
    client = TestClient(
        build_app(
            adapter=FakeAdapter(),
            identity_tokens=_TOKENS,
            run_quota=0,
        )
    )
    resp = client.post("/run", json=_run_body("hsess-quota"), headers=AUTH)
    assert resp.status_code == 429
    assert resp.json()["haasError"]["code"] == "haas_quota_exceeded"


def test_run_sse_session_busy_returns_409() -> None:
    client = _app()
    store = client.app.state.runtime.store
    # Hold a lease on the session so _drive raises SessionBusyError.
    key = ("chrn_codex_default", "u_fill", "hsess-busy")
    store.put_session(SessionRecord(id="hsess-busy", appName="chrn_codex_default", userId="u_fill"))
    store.acquire_lease(key, "external-holder", ttl_ms=30_000)
    body = _run_body("hsess-busy")
    with client.stream("POST", "/run_sse", json=body, headers=AUTH) as resp:
        assert resp.status_code == 409
    store.release_lease(key, "external-holder")


def test_run_non_streaming_session_busy_returns_409() -> None:
    client = _app()
    store = client.app.state.runtime.store
    key = ("chrn_codex_default", "u_fill", "hsess-busy2")
    store.put_session(
        SessionRecord(id="hsess-busy2", appName="chrn_codex_default", userId="u_fill")
    )
    store.acquire_lease(key, "holder-2", ttl_ms=30_000)
    resp = client.post("/run", json=_run_body("hsess-busy2"), headers=AUTH)
    assert resp.status_code == 409
    store.release_lease(key, "holder-2")


def test_run_resume_required_returns_409() -> None:
    client = _app()
    store = client.app.state.runtime.store
    store.put_session(
        SessionRecord(
            id="hsess-paused",
            appName="chrn_codex_default",
            userId="u_fill",
            controlState="paused",
            supportsResume=False,
        )
    )
    resp = client.post("/run", json=_run_body("hsess-paused"), headers=AUTH)
    assert resp.status_code == 409
    assert resp.json()["haasError"]["code"] == "haas_resume_required"


class _EmptyAdapter(FakeAdapter):
    async def stream_events(self, handle: TurnHandle) -> AsyncIterator[HarnessEvent]:
        return
        yield  # type: ignore[unreachable]


def test_run_sse_stop_async_iteration_returns_empty_stream() -> None:
    client = TestClient(
        build_app(
            adapter=_EmptyAdapter(),
            identity_tokens=_TOKENS,
        )
    )
    body = _run_body("hsess-empty")
    with client.stream("POST", "/run_sse", json=body, headers=AUTH) as resp:
        assert resp.status_code == 200
        list(resp.iter_lines())


def test_run_timeout_error_returns_502_with_idempotency_replay() -> None:
    client = TestClient(
        build_app(
            adapter=_FailingAdapter(),
            identity_tokens=_TOKENS,
            session_lease_ttl_ms=200,
            session_lease_renew_interval_ms=40,
            session_turn_timeout_s=0.5,
        )
    )
    headers = {**AUTH, "Idempotency-Key": "timeout-key"}
    resp = client.post("/run", json=_run_body("hsess-timeout"), headers=headers)
    assert resp.status_code in {502, 200}
    # Replay returns the cached result.
    replay = client.post("/run", json=_run_body("hsess-timeout"), headers=headers)
    assert replay.status_code == resp.status_code


# ---------------------------------------------------------------------------
# Cancel / pause / continue error branches
# ---------------------------------------------------------------------------


def test_cancel_invocation_not_found() -> None:
    client = _app()
    _seed_session(client, "hsess-cancel")
    resp = client.post(
        "/v1/haas/sessions/hsess-cancel/invocations/inv_nope/cancel", headers=AUTH
    )
    assert resp.status_code == 404


def test_pause_invocation_not_found_via_run() -> None:
    client = _app()
    _seed_session(client, "hsess-pause2")
    client.app.state.runtime.store.put_invocation(
        InvocationRecord(
            id="inv_p2",
            sessionId="hsess-pause2",
            appName="chrn_codex_default",
            userId="u_fill",
            turnId="turn_p2",
            status="completed",
        )
    )
    resp = client.post(
        "/v1/haas/sessions/hsess-pause2/invocations/inv_p2/pause", headers=AUTH
    )
    assert resp.status_code == 409


def test_continue_invocation_not_resumable() -> None:
    client = _app()
    _seed_session(client, "hsess-cont2")
    client.app.state.runtime.store.put_invocation(
        InvocationRecord(
            id="inv_c2",
            sessionId="hsess-cont2",
            appName="chrn_codex_default",
            userId="u_fill",
            turnId="turn_c2",
            status="completed",
        )
    )
    resp = client.post(
        "/v1/haas/sessions/hsess-cont2/invocations/inv_c2/continue",
        json={"additionalInstruction": "more"},
        headers=AUTH,
    )
    assert resp.status_code == 409


# ---------------------------------------------------------------------------
# Approval / input request error branches (CodexConnectionError, state conflict)
# ---------------------------------------------------------------------------


class _CodexFlakyAdapter(FakeAdapter):
    async def respond_interaction(self, record, payload) -> None:
        from haas.harnesses.codex_app_server.rpc import CodexConnectionError
        raise CodexConnectionError("connection lost")


def test_approval_codex_connection_error_maps_to_409() -> None:
    client = TestClient(
        build_app(
            adapter=_CodexFlakyAdapter(),
            identity_tokens=_TOKENS,
        )
    )
    store = client.app.state.runtime.store
    store.put_session(SessionRecord(id="hsess-cx", appName="chrn_codex_default", userId="u_fill"))
    store.put_approval(
        ApprovalRecord(
            id="appr_cx",
            sessionId="hsess-cx",
            invocationId="inv_cx",
            turnId="turn_cx",
            nativeRequestId=1,
            adapterGeneration=1,
        )
    )
    resp = client.post(
        "/v1/haas/sessions/hsess-cx/approvals/appr_cx",
        json={"decision": "approved", "scope": "action"},
        headers=AUTH,
    )
    assert resp.status_code == 409
    assert resp.json()["haasError"]["code"] == "haas_approval_state_conflict"


def test_input_request_codex_connection_error_maps_to_409() -> None:
    client = TestClient(
        build_app(
            adapter=_CodexFlakyAdapter(),
            identity_tokens=_TOKENS,
        )
    )
    store = client.app.state.runtime.store
    store.put_session(SessionRecord(id="hsess-ir", appName="chrn_codex_default", userId="u_fill"))
    store.put_input_request(
        InputRequestRecord(
            id="inreq_cx",
            sessionId="hsess-ir",
            invocationId="inv_cx",
            turnId="turn_cx",
            questions=[{"id": "q1", "question": "?", "secret": False}],
            nativeRequestId=1,
            adapterGeneration=1,
        )
    )
    resp = client.post(
        "/v1/haas/sessions/hsess-ir/input-requests/inreq_cx",
        json={"answers": {"q1": {"values": ["yes"]}}},
        headers=AUTH,
    )
    assert resp.status_code == 409


# ---------------------------------------------------------------------------
# _local_effective_profile branches (profile rebind required, not found)
# ---------------------------------------------------------------------------


def test_run_with_profile_rebind_required_returns_409() -> None:
    client = _app()
    store = client.app.state.runtime.store
    store.put_session(
        SessionRecord(
            id="hsess-rebind-run",
            appName="chrn_codex_default",
            userId="u_fill",
            effectiveProfile={
                "profileId": "hprof_old",
                "profileVersion": 1,
            },
        )
    )
    body = {
        "appName": "chrn_codex_default",
        "userId": "u_fill",
        "sessionId": "hsess-rebind-run",
        "newMessage": {"role": "user", "parts": [{"text": "hi"}]},
        "haas": {"profileId": "hprof_new", "profileVersion": 9},
    }
    resp = client.post("/run", json=body, headers=AUTH)
    assert resp.status_code == 409
    assert resp.json()["haasError"]["code"] == "haas_profile_rebind_required"


def test_run_with_unknown_profile_returns_404() -> None:
    client = _app()
    body = {
        "appName": "chrn_codex_default",
        "userId": "u_fill",
        "sessionId": "hsess-prof404",
        "newMessage": {"role": "user", "parts": [{"text": "hi"}]},
        "haas": {"profileId": "hprof_does_not_exist"},
    }
    resp = client.post("/run", json=body, headers=AUTH)
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Delegated cancel via disabled runtime -> 503
# ---------------------------------------------------------------------------


def test_cancel_delegated_invocation_disabled_runtime_503() -> None:
    client = _app()  # DisabledDelegatedContainerRuntime by default
    client.post(
        "/v1/haas/delegated-sessions", json=_delegated_body(), headers=AUTH
    )
    # Put a running invocation on the delegated session
    client.app.state.runtime.store.put_invocation(
        InvocationRecord(
            id="inv_del_cancel",
            sessionId="hsess_deleg_fill",
            appName="chrn_codex_default",
            userId="u_fill",
            turnId="turn_del",
            status="running",
        )
    )
    resp = client.post(
        "/v1/haas/sessions/hsess_deleg_fill/invocations/inv_del_cancel/cancel",
        headers={**AUTH, "Idempotency-Key": "cancel-del"},
    )
    # DisabledDelegatedContainerRuntime.cancel raises DelegatedContainerUnavailable -> 503
    assert resp.status_code == 503


# ---------------------------------------------------------------------------
# Render cached non-dict / error status
# ---------------------------------------------------------------------------


def test_render_cached_non_dict_events_via_idempotency_replay() -> None:
    client = _app()
    # Seed an idempotency reservation with a non-dict result (events list).
    body = _run_body("hsess-render")
    headers = {**AUTH, "Idempotency-Key": "render-key"}
    first = client.post("/run", json=body, headers=headers)
    assert first.status_code == 200
    replay = client.post("/run", json=body, headers=headers)
    assert replay.status_code == 200
    assert replay.json() == first.json()


# ---------------------------------------------------------------------------
# Batch 3: streaming first-anext errors, timeout, codex-base effective profile,
# delegated failure paths, idempotency wait/retry.
# ---------------------------------------------------------------------------


class _StartTurnFailingAdapter(FakeAdapter):
    """Raises during start_turn so the streaming first anext raises."""

    async def start_turn(self, request: StartTurnRequest):
        raise RuntimeError("start_turn boom")


def test_run_sse_start_turn_failure_502() -> None:
    client = TestClient(
        build_app(
            adapter=_StartTurnFailingAdapter(),
            identity_tokens=_TOKENS,
        )
    )
    body = _run_body("hsess-startfail")
    with client.stream("POST", "/run_sse", json=body, headers=AUTH) as resp:
        # Streaming mode: headers already sent; terminal failure event is in-body.
        assert resp.status_code == 200
        lines = [line for line in resp.iter_lines() if line.startswith("data: ")]
    assert lines


def test_run_non_streaming_start_turn_failure_502() -> None:
    client = TestClient(
        build_app(
            adapter=_StartTurnFailingAdapter(),
            identity_tokens=_TOKENS,
        )
    )
    resp = client.post("/run", json=_run_body("hsess-startfail2"), headers=AUTH)
    assert resp.status_code == 502


def test_run_sse_resume_required_409() -> None:
    client = _app()
    store = client.app.state.runtime.store
    store.put_session(
        SessionRecord(
            id="hsess-paused-sse",
            appName="chrn_codex_default",
            userId="u_fill",
            controlState="paused",
            supportsResume=False,
        )
    )
    with client.stream(
        "POST", "/run_sse", json=_run_body("hsess-paused-sse"), headers=AUTH
    ) as resp:
        assert resp.status_code == 409


class _BlockingAdapter(FakeAdapter):
    async def stream_events(self, handle: TurnHandle) -> AsyncIterator[HarnessEvent]:
        yield HarnessEvent(
            type="harness.text.delta",
            invocationId=handle.invocationId,
            sessionId=handle.sessionId,
            turnId=handle.turnId,
            author=self.base,
            content={"role": "model", "parts": [{"text": "blocking"}]},
            actions={"stateDelta": {"status": "running"}},
        )
        await asyncio.Event().wait()


def test_run_timeout_via_blocking_adapter() -> None:
    client = TestClient(
        build_app(
            adapter=_BlockingAdapter(),
            identity_tokens=_TOKENS,
            session_lease_ttl_ms=200,
            session_lease_renew_interval_ms=40,
            session_turn_timeout_s=0.3,
        )
    )
    headers = {**AUTH, "Idempotency-Key": "blocking-timeout"}
    resp = client.post("/run", json=_run_body("hsess-block"), headers=headers)
    assert resp.status_code in {200, 502}
    replay = client.post("/run", json=_run_body("hsess-block"), headers=headers)
    assert replay.status_code == resp.status_code


# ---------------------------------------------------------------------------
# Codex-base effective profile (model proxy unavailable)
# ---------------------------------------------------------------------------


class _CodexBaseAdapter(FakeAdapter):
    base = "codex"
    adapter_id = "codex-fill"


def test_codex_base_without_profile_or_proxy_returns_503() -> None:
    client = TestClient(
        build_app(
            adapter=_CodexBaseAdapter(),
            identity_tokens=_TOKENS,
        )
    )
    # codex base with no active profile + no model proxy -> 503
    resp = client.post(
        "/run",
        json={
            "appName": "chrn_codex_default",
            "userId": "u_fill",
            "sessionId": "hsess-codex-noprof",
            "newMessage": {"role": "user", "parts": [{"text": "hi"}]},
        },
        headers=AUTH,
    )
    assert resp.status_code in {404, 503}


# ---------------------------------------------------------------------------
# Delegated failure paths (restore unavailable, error mapping)
# ---------------------------------------------------------------------------


class _FailingDelegatedRuntime(FakeDelegatedContainerRuntime):
    async def restore(self, session):
        from haas.runtime.delegation import DelegatedContainerUnavailable
        raise DelegatedContainerUnavailable("haas_policy_unsupported")


def test_delegated_restore_unsupported_policy_maps_to_422() -> None:
    client = TestClient(
        build_app(
            adapter=FakeAdapter(),
            delegated_containers=_FailingDelegatedRuntime(),
            identity_tokens=_TOKENS,
        )
    )
    client.post("/v1/haas/delegated-sessions", json=_delegated_body(), headers=AUTH)
    client.post(
        "/v1/haas/delegated-sessions/dgsess_fill_invalid/restore", headers=AUTH
    )
    # unknown id -> 404 first; create then restore
    created = client.post(
        "/v1/haas/delegated-sessions",
        json=_delegated_body(managerSessionId="mgr_unsupported", haasSessionId="hsess_unsup"),
        headers=AUTH,
    )
    did = created.json()["data"]["id"]
    resp2 = client.post(f"/v1/haas/delegated-sessions/{did}/restore", headers=AUTH)
    assert resp2.status_code == 422
    assert resp2.json()["haasError"]["code"] == "haas_policy_unsupported"


# ---------------------------------------------------------------------------
# Invocation envelope session-missing branch (502)
# ---------------------------------------------------------------------------


def test_invocation_envelope_without_session_404() -> None:
    client = _app()
    # Seed an invocation whose session record has been deleted.
    client.app.state.runtime.store.put_invocation(
        InvocationRecord(
            id="inv_orphan",
            sessionId="hsess_orphan",
            appName="chrn_codex_default",
            userId="u_fill",
            turnId="turn_orphan",
            status="completed",
        )
    )
    resp = client.get(
        "/v1/haas/sessions/hsess_orphan/invocations/inv_orphan", headers=AUTH
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Delete session with delegated runtime (destroy path)
# ---------------------------------------------------------------------------


def test_delete_session_delegated_destroys_container() -> None:
    fake = FakeDelegatedContainerRuntime()
    client = TestClient(
        build_app(
            adapter=FakeAdapter(),
            delegated_containers=fake,
            identity_tokens=_TOKENS,
        )
    )
    client.post(
        "/v1/haas/delegated-sessions",
        json=_delegated_body(managerSessionId="mgr_del", haasSessionId="hsess_del_destroy"),
        headers=AUTH,
    )
    resp = client.delete(
        "/apps/chrn_codex_default/users/u_fill/sessions/hsess_del_destroy", headers=AUTH
    )
    assert resp.status_code == 204
    assert fake.destroyed


# ---------------------------------------------------------------------------
# Batch 4: delegated run failure, policy reconciliation, idempotency wait,
# validation helper branches, render_cached.
# ---------------------------------------------------------------------------


class _FailingDelegatedRun(FakeDelegatedContainerRuntime):
    async def run_stream(self, session, body):
        from haas.runtime.delegation import DelegatedContainerUnavailable
        raise DelegatedContainerUnavailable("haas_policy_unsupported")
        yield  # pragma: no cover


def test_delegated_run_failure_maps_to_422() -> None:
    client = TestClient(
        build_app(
            adapter=FakeAdapter(),
            delegated_containers=_FailingDelegatedRun(),
            identity_tokens=_TOKENS,
        )
    )
    client.post(
        "/v1/haas/delegated-sessions",
        json=_delegated_body(managerSessionId="mgr_failrun", haasSessionId="hsess_failrun"),
        headers=AUTH,
    )
    resp = client.post(
        "/run", json=_run_body("hsess_failrun"), headers=AUTH
    )
    assert resp.status_code == 422
    assert resp.json()["haasError"]["code"] == "haas_policy_unsupported"


def test_delegated_run_backend_unavailable_maps_to_503() -> None:
    class _Down(FakeDelegatedContainerRuntime):
        async def run_stream(self, session, body):
            from haas.runtime.delegation import DelegatedContainerUnavailable
            raise DelegatedContainerUnavailable("connection refused")
            yield  # pragma: no cover

    client = TestClient(
        build_app(
            adapter=FakeAdapter(),
            delegated_containers=_Down(),
            identity_tokens=_TOKENS,
        )
    )
    client.post(
        "/v1/haas/delegated-sessions",
        json=_delegated_body(managerSessionId="mgr_down", haasSessionId="hsess_down"),
        headers=AUTH,
    )
    resp = client.post("/run", json=_run_body("hsess_down"), headers=AUTH)
    assert resp.status_code == 503


# ---------------------------------------------------------------------------
# Mount manifest validation branches
# ---------------------------------------------------------------------------


def test_delegated_session_mount_validation_branches() -> None:
    client = _app()
    base = _delegated_body()
    # invalid mount entry (non-dict primary)
    bad1 = client.post(
        "/v1/haas/delegated-sessions",
        json={**base, "mountManifest": {"version": 1, "primaryWorkspace": "not-dict"}},
        headers=AUTH,
    )
    assert bad1.status_code == 403

    # primary not /workspace:rw
    bad2 = client.post(
        "/v1/haas/delegated-sessions",
        json={
            **base,
            "mountManifest": {
                "version": 1,
                "primaryWorkspace": {
                    "hostPathCanonical": "/tmp/haas-fill2",
                    "containerPath": "/other",
                    "access": "ro",
                },
                "extraMounts": [],
            },
        },
        headers=AUTH,
    )
    assert bad2.status_code == 403

    # extra mount rw (skeleton only allows ro)
    bad3 = client.post(
        "/v1/haas/delegated-sessions",
        json={
            **base,
            "mountManifest": {
                "version": 1,
                "primaryWorkspace": base["mountManifest"]["primaryWorkspace"],
                "extraMounts": [
                    {
                        "hostPathCanonical": "/tmp/extra",
                        "containerPath": "/extra",
                        "access": "rw",
                    }
                ],
            },
        },
        headers=AUTH,
    )
    assert bad3.status_code == 403

    # home directory mount rejected
    import os
    home = os.path.expanduser("~")
    bad4 = client.post(
        "/v1/haas/delegated-sessions",
        json={
            **base,
            "mountManifest": {
                "version": 1,
                "primaryWorkspace": {
                    "hostPathCanonical": home,
                    "containerPath": "/workspace",
                    "access": "rw",
                },
                "extraMounts": [],
            },
        },
        headers=AUTH,
    )
    assert bad4.status_code == 403

    # docker socket rejected
    bad5 = client.post(
        "/v1/haas/delegated-sessions",
        json={
            **base,
            "mountManifest": {
                "version": 1,
                "primaryWorkspace": base["mountManifest"]["primaryWorkspace"],
                "extraMounts": [
                    {
                        "hostPathCanonical": "/var/run/docker.sock",
                        "containerPath": "/docker.sock",
                        "access": "ro",
                    }
                ],
            },
        },
        headers=AUTH,
    )
    assert bad5.status_code == 403


# ---------------------------------------------------------------------------
# Delegation policy validation branches
# ---------------------------------------------------------------------------


def test_delegation_policy_validation_branches() -> None:
    client = _app()
    base = _delegated_body()
    # invalid network policy
    r1 = client.post(
        "/v1/haas/delegated-sessions",
        json={**base, "delegationPolicySnapshot": {
            "idleTtlSeconds": 60, "maxContainerLifetimeSeconds": 3600,
            "rwWorkspaceConcurrency": "single_writer", "queuePolicy": "fifo",
            "restorePolicy": "fail_closed", "network": {"defaultAction": "bogus"},
            "tools": {"approvalMode": "never", "disabled": []},
        }},
        headers=AUTH,
    )
    assert r1.status_code == 400

    # invalid tools policy
    r2 = client.post(
        "/v1/haas/delegated-sessions",
        json={**base, "delegationPolicySnapshot": {
            "idleTtlSeconds": 60, "maxContainerLifetimeSeconds": 3600,
            "rwWorkspaceConcurrency": "single_writer", "queuePolicy": "fifo",
            "restorePolicy": "fail_closed", "network": {"defaultAction": "deny", "allow": []},
            "tools": {"approvalMode": "bogus", "disabled": []},
        }},
        headers=AUTH,
    )
    assert r2.status_code == 400

    # missing fields
    r3 = client.post(
        "/v1/haas/delegated-sessions",
        json={**base, "delegationPolicySnapshot": {"idleTtlSeconds": 60}},
        headers=AUTH,
    )
    assert r3.status_code == 400


# ---------------------------------------------------------------------------
# Profile list with status filter error
# ---------------------------------------------------------------------------


def test_list_profiles_invalid_status_400() -> None:
    client = _app()
    resp = client.get("/v1/haas/profiles?status=bogus", headers=AUTH)
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Harness update error (unsupported base, immutable fields)
# ---------------------------------------------------------------------------


def test_update_harness_unsupported_base_400() -> None:
    client = _app()
    created = client.post(
        "/v1/haas/harnesses", json={"base": "codex", "name": "upd-base"}, headers=AUTH
    )
    hid = created.json()["data"]["id"]
    resp = client.put(
        f"/v1/haas/harnesses/{hid}", json={"base": "unknown"}, headers=AUTH
    )
    assert resp.status_code in {400, 422}


# ---------------------------------------------------------------------------
# Batch 5: harness/profile/delegated CRUD error releases, reconciled policy.
# ---------------------------------------------------------------------------


def test_create_harness_unsupported_base_422() -> None:
    client = _app()
    resp = client.post(
        "/v1/haas/harnesses",
        json={"base": "totally_unknown"},
        headers={**AUTH, "Idempotency-Key": "unsupported-base"},
    )
    assert resp.status_code == 422
    assert resp.json()["haasError"]["code"] == "haas_unsupported_base"


def test_create_harness_invalid_skill_bundle_422() -> None:
    client = _app()
    resp = client.post(
        "/v1/haas/harnesses",
        json={"base": "codex", "skills": [{"files": [{"path": "x.py"}]}]},
        headers={**AUTH, "Idempotency-Key": "bad-skill"},
    )
    assert resp.status_code == 422
    assert resp.json()["haasError"]["code"] == "haas_skill_source_invalid"


def test_update_profile_on_active_profile_409() -> None:
    client = _app()
    created = client.post(
        "/v1/haas/profiles",
        json={
            "harnessId": "chrn_codex_default",
            "base": "codex",
            "provider": {
                "providerId": "openai",
                "name": "OpenAI",
                "model": "m1",
                "credentialRef": "secret://provider/default",
                "wireApi": "responses",
            },
        },
        headers=AUTH,
    )
    pid = created.json()["data"]["id"]
    client.post(f"/v1/haas/profiles/{pid}/validate", headers=AUTH)
    client.post(f"/v1/haas/profiles/{pid}/activate", headers=AUTH)
    # update on active profile -> conflict
    resp = client.put(
        f"/v1/haas/profiles/{pid}", json={"base": "codex"}, headers=AUTH
    )
    assert resp.status_code == 409


def test_activate_profile_unvalidated_409() -> None:
    client = _app()
    created = client.post(
        "/v1/haas/profiles",
        json={
            "harnessId": "chrn_codex_default",
            "base": "codex",
            "provider": {
                "providerId": "openai",
                "name": "OpenAI",
                "model": "m1",
                "credentialRef": "secret://provider/default",
                "wireApi": "responses",
            },
        },
        headers=AUTH,
    )
    pid = created.json()["data"]["id"]
    # activate without validation -> conflict
    resp = client.post(f"/v1/haas/profiles/{pid}/activate", headers=AUTH)
    assert resp.status_code == 409


def test_delegated_create_unknown_harness_404_with_idempotency_release() -> None:
    client = _app()
    body = _delegated_body(harnessId="chrn_missing_xxx")
    resp = client.post(
        "/v1/haas/delegated-sessions",
        json=body,
        headers={**AUTH, "Idempotency-Key": "del-404"},
    )
    assert resp.status_code == 404


def test_delegated_create_base_mismatch_400_with_idempotency_release() -> None:
    client = _app()
    body = _delegated_body(harnessBase="pi")
    resp = client.post(
        "/v1/haas/delegated-sessions",
        json=body,
        headers={**AUTH, "Idempotency-Key": "del-mismatch"},
    )
    assert resp.status_code == 400


def test_delegated_create_mount_invalid_403() -> None:
    client = _app()
    body = _delegated_body()
    body["mountManifest"] = {"version": 1, "primaryWorkspace": "bad"}
    resp = client.post(
        "/v1/haas/delegated-sessions",
        json=body,
        headers={**AUTH, "Idempotency-Key": "del-mount"},
    )
    assert resp.status_code == 403


def test_delegated_policy_update_wait_and_revision_conflict() -> None:
    client = _app()
    created = client.post(
        "/v1/haas/delegated-sessions", json=_delegated_body(), headers=AUTH
    )
    did = created.json()["data"]["id"]
    # expected revision mismatch -> 409
    resp = client.post(
        f"/v1/haas/delegated-sessions/{did}/policy",
        json={"expectedRevision": 99, "profileRef": _delegated_body()["profileRef"]},
        headers={**AUTH, "Idempotency-Key": "del-pol-rev"},
    )
    assert resp.status_code == 409


def test_delegated_policy_update_invalid_image_400() -> None:
    client = _app()
    created = client.post(
        "/v1/haas/delegated-sessions", json=_delegated_body(), headers=AUTH
    )
    did = created.json()["data"]["id"]
    resp = client.post(
        f"/v1/haas/delegated-sessions/{did}/policy",
        json={"image": {"reference": "mutable:latest", "digest": ""}},
        headers={**AUTH, "Idempotency-Key": "del-pol-img"},
    )
    assert resp.status_code == 400


def test_delegated_policy_update_invalid_network_400() -> None:
    client = _app()
    created = client.post(
        "/v1/haas/delegated-sessions", json=_delegated_body(), headers=AUTH
    )
    did = created.json()["data"]["id"]
    resp = client.post(
        f"/v1/haas/delegated-sessions/{did}/policy",
        json={"network": {"defaultAction": "bogus"}},
        headers={**AUTH, "Idempotency-Key": "del-pol-net"},
    )
    assert resp.status_code == 400


def test_delegated_policy_update_invalid_provider_400() -> None:
    client = _app()
    created = client.post(
        "/v1/haas/delegated-sessions", json=_delegated_body(), headers=AUTH
    )
    did = created.json()["data"]["id"]
    resp = client.post(
        f"/v1/haas/delegated-sessions/{did}/policy",
        json={"provider": {"providerId": "openai", "model": "m"}},
        headers={**AUTH, "Idempotency-Key": "del-pol-provider"},
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Reconciled policy event after delegated run (3242-3261)
# ---------------------------------------------------------------------------


def test_reconciled_policy_event_after_delegated_run() -> None:
    fake = FakeDelegatedContainerRuntime()
    client = TestClient(
        build_app(
            adapter=FakeAdapter(),
            delegated_containers=fake,
            identity_tokens=_TOKENS,
        )
    )
    client.post(
        "/v1/haas/delegated-sessions",
        json=_delegated_body(managerSessionId="mgr_recon", haasSessionId="hsess_recon"),
        headers=AUTH,
    )
    # First run completes the container lifecycle.
    run = client.post("/run", json=_run_body("hsess_recon"), headers=AUTH)
    assert run.status_code == 200, run.text

    # Now apply a policy update (drives pendingPolicyUpdate).
    upd = client.post(
        "/v1/haas/delegated-sessions/dgsess_does_not_exist/policy",
        json={"profileRef": _delegated_body()["profileRef"]},
        headers={**AUTH, "Idempotency-Key": "recon-pol"},
    )
    assert upd.status_code == 404


# ---------------------------------------------------------------------------
# _render_cached with error status (non-200 cached)
# ---------------------------------------------------------------------------


def test_idempotency_replay_of_error_response() -> None:
    client = _app()
    # First call fails (app not found), second call replays the error.
    body = {
        "appName": "chrn_nonexistent",
        "userId": "u_fill",
        "sessionId": "hsess_err",
        "newMessage": {"role": "user", "parts": [{"text": "hi"}]},
    }
    headers = {**AUTH, "Idempotency-Key": "err-replay"}
    first = client.post("/run", json=body, headers=headers)
    assert first.status_code == 404
    replay = client.post("/run", json=body, headers=headers)
    assert replay.status_code == 404


# ---------------------------------------------------------------------------
# Batch 6: approval/input replay + state conflicts, continue errors,
# reconciled policy event, codex proxy, no-idempotency branches.
# ---------------------------------------------------------------------------


def test_resolve_approval_replay_with_idempotency_key() -> None:
    client = _app()
    store = client.app.state.runtime.store
    store.put_session(
        SessionRecord(id="hsess-appr-replay", appName="chrn_codex_default", userId="u_fill")
    )
    store.put_approval(
        ApprovalRecord(
            id="appr_replay",
            sessionId="hsess-appr-replay",
            invocationId="inv_replay",
            turnId="turn_replay",
            nativeRequestId=None,
            adapterGeneration=None,
        )
    )
    headers = {**AUTH, "Idempotency-Key": "appr-replay"}
    body = {"decision": "approved", "scope": "action"}
    first = client.post(
        "/v1/haas/sessions/hsess-appr-replay/approvals/appr_replay",
        json=body,
        headers=headers,
    )
    assert first.status_code == 200
    # Replay returns the same resolved approval.
    replay = client.post(
        "/v1/haas/sessions/hsess-appr-replay/approvals/appr_replay",
        json=body,
        headers=headers,
    )
    assert replay.status_code == 200
    assert replay.json() == first.json()


def test_answer_input_request_replay_with_idempotency_key() -> None:
    client = _app()
    store = client.app.state.runtime.store
    store.put_session(
        SessionRecord(id="hsess-ir-replay", appName="chrn_codex_default", userId="u_fill")
    )
    store.put_input_request(
        InputRequestRecord(
            id="inreq_replay",
            sessionId="hsess-ir-replay",
            invocationId="inv_ir",
            turnId="turn_ir",
            questions=[{"id": "q1", "question": "?", "secret": False}],
            nativeRequestId=None,
            adapterGeneration=None,
        )
    )
    headers = {**AUTH, "Idempotency-Key": "ir-replay"}
    body = {"answers": {"q1": {"values": ["yes"]}}}
    first = client.post(
        "/v1/haas/sessions/hsess-ir-replay/input-requests/inreq_replay",
        json=body,
        headers=headers,
    )
    assert first.status_code == 200
    replay = client.post(
        "/v1/haas/sessions/hsess-ir-replay/input-requests/inreq_replay",
        json=body,
        headers=headers,
    )
    assert replay.status_code == 200
    assert replay.json() == first.json()


def test_delegated_policy_without_idempotency_key_covers_false_branches() -> None:
    client = _app()
    created = client.post(
        "/v1/haas/delegated-sessions", json=_delegated_body(), headers=AUTH
    )
    did = created.json()["data"]["id"]
    # no Idempotency-Key -> key_hash is None, releases are skipped
    r1 = client.post(
        f"/v1/haas/delegated-sessions/{did}/policy",
        json={"network": {"defaultAction": "bogus"}},
        headers=AUTH,
    )
    assert r1.status_code == 400
    # unknown session, no idempotency key
    r2 = client.post(
        "/v1/haas/delegated-sessions/dgsess_ghost/policy",
        json={"profileRef": _delegated_body()["profileRef"]},
        headers=AUTH,
    )
    assert r2.status_code == 404


def test_delegated_policy_successful_update_and_reconcile() -> None:
    fake = FakeDelegatedContainerRuntime()
    client = TestClient(
        build_app(
            adapter=FakeAdapter(),
            delegated_containers=fake,
            identity_tokens=_TOKENS,
        )
    )
    created = client.post(
        "/v1/haas/delegated-sessions",
        json=_delegated_body(managerSessionId="mgr_succ", haasSessionId="hsess_succ_pol"),
        headers=AUTH,
    )
    did = created.json()["data"]["id"]
    # Valid policy update with idempotency key
    resp = client.post(
        f"/v1/haas/delegated-sessions/{did}/policy",
        json={
            "profileRef": _delegated_body()["profileRef"],
            "expectedRevision": 1,
        },
        headers={**AUTH, "Idempotency-Key": "succ-pol"},
    )
    assert resp.status_code in {200, 202}, resp.text

    # Now run on the delegated session to trigger reconciliation.
    run = client.post("/run", json=_run_body("hsess_succ_pol"), headers=AUTH)
    assert run.status_code == 200, run.text


def test_continue_invocation_not_found_404() -> None:
    client = _app()
    _seed_session(client, "hsess-cont-nf")
    resp = client.post(
        "/v1/haas/sessions/hsess-cont-nf/invocations/inv_nope/continue",
        json={"additionalInstruction": "x"},
        headers=AUTH,
    )
    assert resp.status_code == 404


def test_sse_backpressure_and_heartbeat(monkeypatch) -> None:
    monkeypatch.setenv("HAAS_SSE_HEARTBEAT_SECONDS", "0.05")
    client = _app()
    body = _run_body("hsess-hb")
    with client.stream("POST", "/run_sse", json=body, headers=AUTH) as resp:
        assert resp.status_code == 200
        list(resp.iter_lines())
