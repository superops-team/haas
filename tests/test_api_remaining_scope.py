"""Remaining-scope contract tests: adapter assembly, session listing, skills.

Covers specs/config §5.1 (adapter assembly), specs/haas-protocol
`/v1/haas/sessions` (SessionListEnvelope + cursor pagination) and
specs/harness-registry §5.1 / specs/mcp-tool-skill-runtime §8 (skill bundle
files and path safety).
"""
from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from haas.api import build_app
from haas.config import AppConfig, create_app
from haas.identity import Principal
from haas.runtime import FakeDelegatedContainerRuntime
from haas.stores import ApprovalRecord, SessionRecord

pytestmark = pytest.mark.adk

TOKEN_A = "tok-a"
TOKEN_B = "tok-b"
AUTH_A = {"Authorization": f"Bearer {TOKEN_A}"}
AUTH_B = {"Authorization": f"Bearer {TOKEN_B}"}


def _client() -> TestClient:
    return TestClient(
        build_app(
            identity_tokens={
                TOKEN_A: Principal(
                    principalId="p_a", tenantId="t_a", userIds=frozenset({"u_1", "u_2"})
                ),
                TOKEN_B: Principal(principalId="p_b", tenantId="t_b"),
            }
        )
    )


def _run(client: TestClient, session_id: str, user_id: str = "u_1") -> None:
    resp = client.post(
        "/run",
        json={
            "appName": "chrn_codex_default",
            "userId": user_id,
            "sessionId": session_id,
            "newMessage": {"role": "user", "parts": [{"text": "hi"}]},
        },
        headers=AUTH_A,
    )
    assert resp.status_code == 200, resp.text


def _delegated_body(**overrides: Any) -> dict[str, Any]:
    body = {
        "managerSessionId": "mgr_1",
        "haasSessionId": "hsess_delegate",
        "haasUserId": "u_1",
        "harnessId": "chrn_codex_default",
        "harnessBase": "codex",
        "image": {"reference": "haas:local", "digest": "sha256:test"},
        "provider": {
            "providerId": "volcengine-ark",
            "model": "doubao-seed-2.1-turbo",
            "credentialRef": "secret://provider/volcengine",
        },
        "mountManifest": {
            "version": 1,
            "primaryWorkspace": {
                "hostPathCanonical": "/repo",
                "containerPath": "/workspace",
                "access": "rw",
            },
            "extraMounts": [
                {
                    "hostPathCanonical": "/shared",
                    "containerPath": "/mnt/extra/shared",
                    "access": "ro",
                }
            ],
        },
    }
    body.update(overrides)
    return body


# --- adapter assembly (specs/config §5.1) -----------------------------------


def test_create_app_defaults_to_codex_adapter() -> None:
    """The production entrypoint must not silently fall back to FakeAdapter."""
    app = create_app(AppConfig())
    adapter = app.state.runtime.adapter
    assert adapter.base == "codex"


def test_create_app_honours_fake_base_for_local_dev() -> None:
    config = AppConfig()
    config.adapters.default_base = "fake"
    app = create_app(config)
    assert app.state.runtime.adapter.base == "fake"


def test_create_app_does_not_require_live_harness() -> None:
    """Assembly must not block startup when Codex is not running."""
    config = AppConfig()
    config.adapters.codex.socket_path = "/tmp/haas-does-not-exist.sock"
    app = create_app(config)
    client = TestClient(app)
    # /health is process liveness only and must stay ok.
    assert client.get("/v1/haas/health").json()["data"]["status"] == "ok"
    # execution readiness must honestly report not_ready.
    response = client.get("/v1/haas/ready?scope=execution")
    assert response.status_code == 503
    body = response.json()
    assert body["haasError"]["code"] == "haas_adapter_unavailable"


def test_adapter_base_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    from haas.config import load_config

    monkeypatch.setenv("HAAS_ADAPTER_BASE", "fake")
    assert load_config().adapters.default_base == "fake"


def test_adapter_config_from_yaml_file(tmp_path: Any) -> None:
    from haas.config import load_config

    path = tmp_path / "haas.yaml"
    path.write_text(
        "adapters:\n"
        "  default_base: fake\n"
        "  codex:\n"
        "    transport: stdio\n"
        "    socket_path: /tmp/custom.sock\n"
        "delegation:\n"
        "  idle_ttl_seconds: 60\n"
        "  max_container_lifetime_seconds: 3600\n",
        encoding="utf-8",
    )
    cfg = load_config(str(path))
    assert cfg.adapters.default_base == "fake"
    assert cfg.adapters.codex.transport == "stdio"
    assert cfg.adapters.codex.socket_path == "/tmp/custom.sock"
    assert cfg.delegation.idle_ttl_seconds == 60
    assert cfg.delegation.max_container_lifetime_seconds == 3600


def test_delegation_config_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    from haas.config import build_delegated_container_runtime, load_config
    from haas.runtime import DisabledDelegatedContainerRuntime, DockerDelegatedContainerRuntime

    monkeypatch.setenv("HAAS_DELEGATION_IDLE_TTL_SECONDS", "120")
    monkeypatch.setenv("HAAS_DELEGATION_MAX_CONTAINER_LIFETIME_SECONDS", "7200")
    monkeypatch.setenv("HAAS_DELEGATION_RW_WORKSPACE_CONCURRENCY", "single_writer")
    cfg = load_config()
    assert cfg.delegation.idle_ttl_seconds == 120
    assert cfg.delegation.max_container_lifetime_seconds == 7200
    assert cfg.delegation.rw_workspace_concurrency == "single_writer"
    assert isinstance(build_delegated_container_runtime(cfg), DisabledDelegatedContainerRuntime)

    cfg.delegation.container_backend = "docker"
    cfg.delegation.docker_bin = "docker-test"
    cfg.delegation.docker_network = "haas-net"
    runtime = build_delegated_container_runtime(cfg)
    assert isinstance(runtime, DockerDelegatedContainerRuntime)
    assert runtime.docker_bin == "docker-test"
    assert runtime.network == "haas-net"


# --- session listing --------------------------------------------------------


def test_list_sessions_returns_envelope_array() -> None:
    client = _client()
    _run(client, "hsess_1")
    resp = client.get("/v1/haas/sessions", headers=AUTH_A)
    assert resp.status_code == 200
    body = resp.json()
    # SessionListEnvelope: data is an ARRAY, nextCursor sits on the envelope.
    assert isinstance(body["data"], list)
    assert "traceId" in body
    assert "nextCursor" in body
    session = body["data"][0]
    for key in ("id", "appName", "userId", "state", "events", "lastUpdateTime"):
        assert key in session


def test_list_sessions_requires_auth() -> None:
    assert _client().get("/v1/haas/sessions").status_code == 401


def test_list_sessions_is_scoped_to_caller() -> None:
    client = _client()
    _run(client, "hsess_mine")
    assert client.get("/v1/haas/sessions", headers=AUTH_B).json()["data"] == []


def test_list_sessions_filters_by_app() -> None:
    client = _client()
    _run(client, "hsess_1")
    hit = client.get(
        "/v1/haas/sessions?app=chrn_codex_default", headers=AUTH_A
    ).json()
    miss = client.get("/v1/haas/sessions?app=chrn_other", headers=AUTH_A).json()
    assert len(hit["data"]) == 1
    assert miss["data"] == []


def test_list_sessions_paginates_with_cursor() -> None:
    client = _client()
    for index in range(5):
        _run(client, f"hsess_{index}")

    first = client.get("/v1/haas/sessions?limit=2", headers=AUTH_A).json()
    assert len(first["data"]) == 2
    assert first["nextCursor"]

    second = client.get(
        f"/v1/haas/sessions?limit=2&cursor={first['nextCursor']}", headers=AUTH_A
    ).json()
    assert len(second["data"]) == 2

    seen = {s["id"] for s in first["data"]} | {s["id"] for s in second["data"]}
    assert len(seen) == 4, "pages must not overlap"

    last = client.get(
        f"/v1/haas/sessions?limit=2&cursor={second['nextCursor']}", headers=AUTH_A
    ).json()
    assert len(last["data"]) == 1
    assert last["nextCursor"] is None, "exhausted page must report null cursor"


def test_list_sessions_rejects_invalid_limit() -> None:
    client = _client()
    assert client.get("/v1/haas/sessions?limit=0", headers=AUTH_A).status_code == 400
    assert client.get("/v1/haas/sessions?limit=101", headers=AUTH_A).status_code == 400


def test_list_sessions_rejects_unknown_cursor() -> None:
    resp = _client().get("/v1/haas/sessions?cursor=bogus", headers=AUTH_A)
    assert resp.status_code == 400
    assert resp.json()["haasError"]["code"] == "invalid_input"


# --- skill bundle files -----------------------------------------------------


SKILL = {
    "id": "skill_repo_rules",
    "name": "repo-rules",
    "enabled": True,
    "files": [
        {"path": "SKILL.md", "content": "Skill instructions..."},
        {"path": "scripts/run.sh", "content": "echo hi"},
    ],
}


def _harness_with_skill(client: TestClient, skill: dict[str, Any]) -> str:
    resp = client.post(
        "/v1/haas/harnesses",
        json={"base": "codex", "name": "skilled", "skills": [skill]},
        headers=AUTH_A,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["id"]


def test_skill_files_round_trip() -> None:
    """spec §11: renaming/reading must not lose skill files."""
    client = _client()
    harness_id = _harness_with_skill(client, SKILL)
    resp = client.get(
        f"/v1/haas/harnesses/{harness_id}/skills/skill_repo_rules/files",
        headers=AUTH_A,
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["id"] == "skill_repo_rules"
    assert data["name"] == "repo-rules"
    paths = [f["path"] for f in data["files"]]
    assert paths == ["SKILL.md", "scripts/run.sh"]
    assert data["files"][0]["content"] == "Skill instructions..."


def test_skill_files_unknown_skill_is_404() -> None:
    client = _client()
    harness_id = _harness_with_skill(client, SKILL)
    resp = client.get(
        f"/v1/haas/harnesses/{harness_id}/skills/skill_nope/files", headers=AUTH_A
    )
    assert resp.status_code == 404


def test_skill_files_unknown_harness_is_404() -> None:
    resp = _client().get(
        "/v1/haas/harnesses/chrn_missing/skills/s/files", headers=AUTH_A
    )
    assert resp.status_code == 404


def test_skill_files_cross_tenant_is_404() -> None:
    client = _client()
    harness_id = _harness_with_skill(client, SKILL)
    resp = client.get(
        f"/v1/haas/harnesses/{harness_id}/skills/skill_repo_rules/files",
        headers=AUTH_B,
    )
    assert resp.status_code == 404


@pytest.mark.parametrize(
    "bad_path",
    ["../escape.md", "/etc/passwd", "nested/../../escape.md", "a\x00b.md"],
)
def test_skill_rejects_unsafe_paths(bad_path: str) -> None:
    """spec §8: skill paths cannot escape their skill root."""
    client = _client()
    skill = {
        "id": "s1",
        "name": "bad",
        "files": [{"path": "SKILL.md", "content": "x"}, {"path": bad_path, "content": "y"}],
    }
    resp = client.post(
        "/v1/haas/harnesses",
        json={"base": "codex", "name": "bad-skill", "skills": [skill]},
        headers=AUTH_A,
    )
    assert resp.status_code == 422
    assert resp.json()["haasError"]["code"] == "haas_skill_source_invalid"


# --- manager delegation ----------------------------------------------------


def test_delegated_session_create_get_and_session_link() -> None:
    client = _client()
    resp = client.post(
        "/v1/haas/delegated-sessions",
        json=_delegated_body(),
        headers={**AUTH_A, "Idempotency-Key": "delegate-create"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["managerSessionId"] == "mgr_1"
    assert data["haasSessionId"] == "hsess_delegate"
    assert data["binding"] == "haas_bound"
    assert data["mountManifest"]["primaryWorkspace"] == {
        "hostPathCanonical": "/repo",
        "containerPath": "/workspace",
        "access": "rw",
    }
    assert data["delegationPolicySnapshot"]["idleTtlSeconds"] == 1800

    delegated_id = data["id"]
    replay = client.post(
        "/v1/haas/delegated-sessions",
        json=_delegated_body(),
        headers={**AUTH_A, "Idempotency-Key": "delegate-create"},
    )
    assert replay.status_code == 200
    assert replay.json()["data"]["id"] == delegated_id

    get = client.get(f"/v1/haas/delegated-sessions/{delegated_id}", headers=AUTH_A)
    assert get.status_code == 200
    assert get.json()["data"]["id"] == delegated_id

    session = client.get(
        "/apps/chrn_codex_default/users/u_1/sessions/hsess_delegate",
        headers=AUTH_A,
    )
    assert session.status_code == 200
    assert session.json()["delegatedSessionRef"] == {
        "delegatedSessionId": delegated_id,
        "managerSessionId": "mgr_1",
        "containerGeneration": 0,
        "runtimeStatus": "no_runtime",
    }


def test_delegated_session_uses_configured_default_policy() -> None:
    config = AppConfig()
    config.delegation.idle_ttl_seconds = 90
    config.delegation.max_container_lifetime_seconds = 3600
    client = TestClient(
        build_app(
            config=config,
            identity_tokens={
                TOKEN_A: Principal(principalId="p_a", tenantId="t_a", userIds=frozenset({"u_1"}))
            },
        )
    )
    body = _delegated_body()
    body.pop("delegationPolicySnapshot", None)

    resp = client.post("/v1/haas/delegated-sessions", json=body, headers=AUTH_A)
    assert resp.status_code == 200, resp.text
    policy = resp.json()["data"]["delegationPolicySnapshot"]
    assert policy["idleTtlSeconds"] == 90
    assert policy["maxContainerLifetimeSeconds"] == 3600
    assert policy["policyChangeMode"] == "snapshot_per_session"


def test_delegated_session_reuses_existing_manager_binding() -> None:
    client = _client()
    first = client.post(
        "/v1/haas/delegated-sessions", json=_delegated_body(), headers=AUTH_A
    )
    assert first.status_code == 200
    delegated_id = first.json()["data"]["id"]

    second = client.post(
        "/v1/haas/delegated-sessions", json=_delegated_body(), headers=AUTH_A
    )
    assert second.status_code == 200
    assert second.json()["data"]["id"] == delegated_id


def test_delegated_session_rejects_conflicting_manager_binding() -> None:
    client = _client()
    assert client.post(
        "/v1/haas/delegated-sessions", json=_delegated_body(), headers=AUTH_A
    ).status_code == 200

    conflict = client.post(
        "/v1/haas/delegated-sessions",
        json=_delegated_body(haasSessionId="hsess_other"),
        headers=AUTH_A,
    )
    assert conflict.status_code == 409
    assert conflict.json()["haasError"]["code"] == "haas_delegated_session_conflict"


def test_delegated_session_rejects_extra_rw_mount() -> None:
    client = _client()
    body = _delegated_body()
    body["mountManifest"]["extraMounts"][0]["access"] = "rw"
    resp = client.post("/v1/haas/delegated-sessions", json=body, headers=AUTH_A)
    assert resp.status_code == 403
    assert resp.json()["haasError"]["code"] == "haas_delegation_mount_invalid"


def test_delegated_session_rejects_plaintext_credential_ref() -> None:
    client = _client()
    body = _delegated_body()
    body["provider"]["credentialRef"] = "sk-real-key"
    resp = client.post("/v1/haas/delegated-sessions", json=body, headers=AUTH_A)
    assert resp.status_code == 400
    assert resp.json()["haasError"]["code"] == "invalid_input"


def test_delegated_session_accepts_unpinned_local_image_only_with_override() -> None:
    body = _delegated_body()
    body["image"] = {"reference": "haas:local", "digest": ""}
    denied = _client().post("/v1/haas/delegated-sessions", json=body, headers=AUTH_A)
    assert denied.status_code == 400

    cfg = AppConfig()
    cfg.delegation.allow_unpinned_local_image = True
    client = TestClient(
        build_app(
            config=cfg,
            identity_tokens={
                TOKEN_A: Principal(
                    principalId="p_a", tenantId="t_a", userIds=frozenset({"u_1"})
                )
            },
        )
    )
    accepted = client.post("/v1/haas/delegated-sessions", json=body, headers=AUTH_A)
    assert accepted.status_code == 200, accepted.text


@pytest.mark.parametrize(
    "host_path",
    ["/", "/var/run/docker.sock", "/Users/bytedance/.ssh", "/repo/../etc"],
)
def test_delegated_session_rejects_unsafe_primary_mounts(host_path: str) -> None:
    client = _client()
    body = _delegated_body()
    body["mountManifest"]["primaryWorkspace"]["hostPathCanonical"] = host_path
    resp = client.post("/v1/haas/delegated-sessions", json=body, headers=AUTH_A)
    assert resp.status_code == 403
    assert resp.json()["haasError"]["code"] == "haas_delegation_mount_invalid"


def test_delegated_session_restore_fails_closed_until_container_runtime_exists() -> None:
    client = _client()
    created = client.post(
        "/v1/haas/delegated-sessions", json=_delegated_body(), headers=AUTH_A
    )
    delegated_id = created.json()["data"]["id"]

    restored = client.post(
        f"/v1/haas/delegated-sessions/{delegated_id}/restore", headers=AUTH_A
    )
    assert restored.status_code == 503
    body = restored.json()
    assert body["haasError"]["code"] == "haas_delegation_backend_unavailable"
    assert body["haasError"]["retryable"] is True


def test_delegated_session_restore_redacts_backend_failure_detail() -> None:
    class FailingDelegatedRuntime:
        async def restore(self, session):
            from haas.runtime import DelegatedContainerUnavailable

            raise DelegatedContainerUnavailable(
                'invalid mount config: bind source path does not exist: "/secret/path"'
            )

        async def destroy(self, session, *, reason: str):
            return session.runtime

    client = TestClient(
        build_app(
            delegated_containers=FailingDelegatedRuntime(),
            identity_tokens={
                TOKEN_A: Principal(principalId="p_a", tenantId="t_a", userIds=frozenset({"u_1"}))
            },
        )
    )
    created = client.post(
        "/v1/haas/delegated-sessions", json=_delegated_body(), headers=AUTH_A
    )
    delegated_id = created.json()["data"]["id"]

    restored = client.post(
        f"/v1/haas/delegated-sessions/{delegated_id}/restore", headers=AUTH_A
    )

    assert restored.status_code == 503
    body = restored.json()
    assert body["detail"] == "delegation_backend_unavailable"
    assert body["haasError"]["safeReason"] == "delegation_backend_unavailable"
    assert "/secret/path" not in restored.text


def test_delegated_session_restore_uses_injected_runtime() -> None:
    delegated_runtime = FakeDelegatedContainerRuntime()
    client = TestClient(
        build_app(
            delegated_containers=delegated_runtime,
            identity_tokens={
                TOKEN_A: Principal(principalId="p_a", tenantId="t_a", userIds=frozenset({"u_1"}))
            },
        )
    )
    created = client.post(
        "/v1/haas/delegated-sessions", json=_delegated_body(), headers=AUTH_A
    )
    delegated_id = created.json()["data"]["id"]

    restored = client.post(
        f"/v1/haas/delegated-sessions/{delegated_id}/restore", headers=AUTH_A
    )
    assert restored.status_code == 200, restored.text
    runtime = restored.json()["data"]["runtime"]
    assert runtime["status"] == "running"
    assert runtime["containerGeneration"] == 1
    assert runtime["containerId"] == f"fake-{delegated_id}-1"
    assert delegated_runtime.restored == [delegated_id]


def test_delete_session_destroys_delegated_runtime_handle() -> None:
    delegated_runtime = FakeDelegatedContainerRuntime()
    client = TestClient(
        build_app(
            delegated_containers=delegated_runtime,
            identity_tokens={
                TOKEN_A: Principal(principalId="p_a", tenantId="t_a", userIds=frozenset({"u_1"}))
            },
        )
    )
    created = client.post(
        "/v1/haas/delegated-sessions", json=_delegated_body(), headers=AUTH_A
    )
    delegated_id = created.json()["data"]["id"]
    client.post(f"/v1/haas/delegated-sessions/{delegated_id}/restore", headers=AUTH_A)

    deleted = client.delete(
        "/apps/chrn_codex_default/users/u_1/sessions/hsess_delegate",
        headers=AUTH_A,
    )
    assert deleted.status_code == 204
    assert delegated_runtime.destroyed == [f"{delegated_id}:session_deleted"]
    stored = client.app.state.runtime.store.get_delegated_session(delegated_id)
    assert stored.runtime.status == "destroyed"
    assert stored.runtime.containerId is None


def test_delegated_restore_enforces_single_rw_workspace_lock() -> None:
    delegated_runtime = FakeDelegatedContainerRuntime()
    client = TestClient(
        build_app(
            delegated_containers=delegated_runtime,
            identity_tokens={
                TOKEN_A: Principal(principalId="p_a", tenantId="t_a", userIds=frozenset({"u_1"}))
            },
        )
    )
    first = client.post(
        "/v1/haas/delegated-sessions", json=_delegated_body(), headers=AUTH_A
    ).json()["data"]["id"]
    second_body = _delegated_body(
        managerSessionId="mgr_2", haasSessionId="hsess_delegate_2"
    )
    second = client.post(
        "/v1/haas/delegated-sessions", json=second_body, headers=AUTH_A
    ).json()["data"]["id"]

    assert client.post(
        f"/v1/haas/delegated-sessions/{first}/restore", headers=AUTH_A
    ).status_code == 200
    blocked = client.post(
        f"/v1/haas/delegated-sessions/{second}/restore", headers=AUTH_A
    )
    assert blocked.status_code == 409
    assert blocked.json()["haasError"]["code"] == "haas_workspace_lock_busy"

    assert client.delete(
        "/apps/chrn_codex_default/users/u_1/sessions/hsess_delegate",
        headers=AUTH_A,
    ).status_code == 204
    unblocked = client.post(
        f"/v1/haas/delegated-sessions/{second}/restore", headers=AUTH_A
    )
    assert unblocked.status_code == 200


def test_delegated_session_policy_update_is_explicit() -> None:
    client = _client()
    created = client.post(
        "/v1/haas/delegated-sessions", json=_delegated_body(), headers=AUTH_A
    )
    delegated_id = created.json()["data"]["id"]

    policy = {
        "version": 1,
        "idleTtlSeconds": 60,
        "maxContainerLifetimeSeconds": 3600,
        "rwWorkspaceConcurrency": "single_writer",
        "queuePolicy": "fifo",
        "restorePolicy": "fail_closed",
        "mountPolicy": "project_rw_extra_ro",
    }
    updated = client.post(
        f"/v1/haas/delegated-sessions/{delegated_id}/policy",
        json={"delegationPolicySnapshot": policy},
        headers=AUTH_A,
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["data"]["delegationPolicySnapshot"] == policy


def test_delegated_session_policy_update_destroys_live_runtime() -> None:
    delegated_runtime = FakeDelegatedContainerRuntime()
    client = TestClient(
        build_app(
            delegated_containers=delegated_runtime,
            identity_tokens={
                TOKEN_A: Principal(principalId="p_a", tenantId="t_a", userIds=frozenset({"u_1"}))
            },
        )
    )
    created = client.post(
        "/v1/haas/delegated-sessions", json=_delegated_body(), headers=AUTH_A
    )
    delegated_id = created.json()["data"]["id"]
    restored = client.post(
        f"/v1/haas/delegated-sessions/{delegated_id}/restore", headers=AUTH_A
    )
    assert restored.status_code == 200, restored.text

    policy = {
        "version": 1,
        "idleTtlSeconds": 60,
        "maxContainerLifetimeSeconds": 3600,
        "rwWorkspaceConcurrency": "single_writer",
        "queuePolicy": "fifo",
        "restorePolicy": "fail_closed",
        "mountPolicy": "project_rw_extra_ro",
    }
    updated = client.post(
        f"/v1/haas/delegated-sessions/{delegated_id}/policy",
        json={"delegationPolicySnapshot": policy},
        headers=AUTH_A,
    )
    assert updated.status_code == 200, updated.text
    assert delegated_runtime.destroyed == [f"{delegated_id}:policy_updated"]
    assert updated.json()["data"]["runtime"]["status"] == "destroyed"
    session = client.get(
        "/apps/chrn_codex_default/users/u_1/sessions/hsess_delegate", headers=AUTH_A
    )
    assert session.status_code == 200
    assert session.json()["delegatedSessionRef"]["runtimeStatus"] == "destroyed"

    second_body = _delegated_body(
        managerSessionId="mgr_2", haasSessionId="hsess_delegate_2"
    )
    second = client.post(
        "/v1/haas/delegated-sessions", json=second_body, headers=AUTH_A
    ).json()["data"]["id"]
    assert client.post(
        f"/v1/haas/delegated-sessions/{second}/restore", headers=AUTH_A
    ).status_code == 200


def test_run_sse_for_delegated_session_uses_container_runtime() -> None:
    delegated_runtime = FakeDelegatedContainerRuntime()
    client = TestClient(
        build_app(
            delegated_containers=delegated_runtime,
            identity_tokens={
                TOKEN_A: Principal(
                    principalId="p_a", tenantId="t_a", userIds=frozenset({"u_1"})
                )
            },
        )
    )
    created = client.post(
        "/v1/haas/delegated-sessions", json=_delegated_body(), headers=AUTH_A
    )
    assert created.status_code == 200, created.text

    async def fail_stream():
        raise AssertionError("delegated /run_sse must not use the parent adapter")
        yield  # pragma: no cover

    def fail_run_stream(_req):
        return fail_stream()

    client.app.state.runtime.sessions.run_stream = fail_run_stream
    with client.stream(
        "POST",
        "/run_sse",
        json={
            "appName": "chrn_codex_default",
            "userId": "u_1",
            "sessionId": "hsess_delegate",
            "newMessage": {"role": "user", "parts": [{"text": "hi"}]},
        },
        headers=AUTH_A,
    ) as resp:
        assert resp.status_code == 200, resp.text
        lines = [line for line in resp.iter_lines() if line.startswith("data: ")]

    events = [json.loads(line[len("data: "):]) for line in lines]
    assert events[0]["content"]["parts"][0]["text"] == "delegated"
    assert events[-1]["actions"]["stateDelta"]["status"] == "completed"
    assert delegated_runtime.restored
    assert delegated_runtime.runs[0]["delegatedSessionId"] == created.json()["data"]["id"]


def test_run_for_delegated_session_uses_container_runtime() -> None:
    delegated_runtime = FakeDelegatedContainerRuntime()
    client = TestClient(
        build_app(
            delegated_containers=delegated_runtime,
            identity_tokens={
                TOKEN_A: Principal(
                    principalId="p_a", tenantId="t_a", userIds=frozenset({"u_1"})
                )
            },
        )
    )
    assert client.post(
        "/v1/haas/delegated-sessions", json=_delegated_body(), headers=AUTH_A
    ).status_code == 200
    resp = client.post(
        "/run",
        json={
            "appName": "chrn_codex_default",
            "userId": "u_1",
            "sessionId": "hsess_delegate",
            "newMessage": {"role": "user", "parts": [{"text": "hi"}]},
        },
        headers=AUTH_A,
    )

    assert resp.status_code == 200, resp.text
    events = resp.json()
    assert events[0]["content"]["parts"][0]["text"] == "delegated"
    assert events[-1]["actions"]["stateDelta"]["status"] == "completed"
    assert delegated_runtime.runs


def test_run_for_delegated_session_enforces_workspace_lock() -> None:
    delegated_runtime = FakeDelegatedContainerRuntime()
    client = TestClient(
        build_app(
            delegated_containers=delegated_runtime,
            identity_tokens={
                TOKEN_A: Principal(
                    principalId="p_a", tenantId="t_a", userIds=frozenset({"u_1"})
                )
            },
        )
    )
    first = client.post(
        "/v1/haas/delegated-sessions", json=_delegated_body(), headers=AUTH_A
    ).json()["data"]["id"]
    second_body = _delegated_body(
        managerSessionId="mgr_2", haasSessionId="hsess_delegate_2"
    )
    client.post("/v1/haas/delegated-sessions", json=second_body, headers=AUTH_A)

    assert client.post(
        "/run",
        json={
            "appName": "chrn_codex_default",
            "userId": "u_1",
            "sessionId": "hsess_delegate",
            "newMessage": {"role": "user", "parts": [{"text": "hi"}]},
        },
        headers=AUTH_A,
    ).status_code == 200
    blocked = client.post(
        "/run",
        json={
            "appName": "chrn_codex_default",
            "userId": "u_1",
            "sessionId": "hsess_delegate_2",
            "newMessage": {"role": "user", "parts": [{"text": "hi"}]},
        },
        headers=AUTH_A,
    )

    assert blocked.status_code == 409
    assert blocked.json()["haasError"]["code"] == "haas_workspace_lock_busy"
    assert delegated_runtime.runs[0]["delegatedSessionId"] == first


def test_approval_resolution_is_explicit_and_single_use() -> None:
    client = _client()
    client.app.state.runtime.store.put_session(
        SessionRecord(id="hsess_approval", appName="chrn_codex_default", userId="u_1")
    )
    client.app.state.runtime.store.put_approval(
        ApprovalRecord(
            id="appr_1",
            sessionId="hsess_approval",
            invocationId="inv_1",
            turnId="turn_1",
            request={"kind": "tool", "safeSummary": "Run command"},
        )
    )

    approved = client.post(
        "/v1/haas/sessions/hsess_approval/approvals/appr_1",
        json={"decision": "approved"},
        headers=AUTH_A,
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["data"]["status"] == "approved"

    denied = client.post(
        "/v1/haas/sessions/hsess_approval/approvals/appr_1",
        json={"decision": "denied"},
        headers=AUTH_A,
    )
    assert denied.status_code == 409
    assert denied.json()["haasError"]["code"] == "haas_approval_state_conflict"


def test_approval_resolution_cross_user_hidden() -> None:
    client = _client()
    client.app.state.runtime.store.put_session(
        SessionRecord(id="hsess_approval", appName="chrn_codex_default", userId="u_1")
    )
    client.app.state.runtime.store.put_approval(
        ApprovalRecord(
            id="appr_1",
            sessionId="hsess_approval",
            invocationId="inv_1",
            turnId="turn_1",
            request={"kind": "tool", "safeSummary": "Run command"},
        )
    )
    hidden = client.post(
        "/v1/haas/sessions/hsess_approval/approvals/appr_1",
        json={"decision": "approved"},
        headers=AUTH_B,
    )
    assert hidden.status_code == 404
    assert hidden.json()["haasError"]["code"] == "haas_approval_not_found"


def test_delegated_session_cross_user_hidden() -> None:
    client = _client()
    created = client.post(
        "/v1/haas/delegated-sessions", json=_delegated_body(), headers=AUTH_A
    )
    delegated_id = created.json()["data"]["id"]
    hidden = client.get(f"/v1/haas/delegated-sessions/{delegated_id}", headers=AUTH_B)
    assert hidden.status_code == 404
    assert hidden.json()["haasError"]["code"] == "haas_delegated_session_not_found"


def test_skill_requires_skill_md() -> None:
    """spec §10: an enabled skill bundle without SKILL.md fails validation."""
    client = _client()
    skill = {"id": "s1", "name": "no-md", "enabled": True,
             "files": [{"path": "notes.md", "content": "x"}]}
    resp = client.post(
        "/v1/haas/harnesses",
        json={"base": "codex", "name": "bad", "skills": [skill]},
        headers=AUTH_A,
    )
    assert resp.status_code == 422
    assert resp.json()["haasError"]["code"] == "haas_skill_source_invalid"


def test_skill_binary_content_is_preserved_byte_for_byte() -> None:
    """spec §4: binary content must survive round-trip via contentB64."""
    import base64

    client = _client()
    raw = bytes(range(256))
    skill = {
        "id": "s_bin",
        "name": "bin",
        "files": [
            {"path": "SKILL.md", "content": "doc"},
            {"path": "blob.bin", "contentB64": base64.b64encode(raw).decode()},
        ],
    }
    harness_id = _harness_with_skill(client, skill)
    data = client.get(
        f"/v1/haas/harnesses/{harness_id}/skills/s_bin/files", headers=AUTH_A
    ).json()["data"]
    blob = next(f for f in data["files"] if f["path"] == "blob.bin")
    assert base64.b64decode(blob["contentB64"]) == raw
