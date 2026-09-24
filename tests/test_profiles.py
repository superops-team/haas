"""Harness Profile public API contract tests."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from haas.api import build_app
from haas.identity import Principal
from haas.stores import SQLiteStore

TOKEN_A = "profile-a"
TOKEN_B = "profile-b"
AUTH_A = {"Authorization": f"Bearer {TOKEN_A}"}
AUTH_B = {"Authorization": f"Bearer {TOKEN_B}"}


def _client() -> TestClient:
    return TestClient(
        build_app(
            identity_tokens={
                TOKEN_A: Principal(principalId="p_a", tenantId="t_a"),
                TOKEN_B: Principal(principalId="p_b", tenantId="t_b"),
            }
        )
    )


def _profile_body(harness_id: str = "chrn_codex_default", **changes: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "harnessId": harness_id,
        "base": "codex",
        "name": "default profile",
        "provider": {
            "providerId": "openai",
            "name": "OpenAI",
            "model": "gpt-test",
            "credentialRef": "secret://tenant/provider/default",
            "wireApi": "responses",
            "apiType": "responses",
        },
    }
    body.update(changes)
    return body


def test_create_and_list_profile_revision() -> None:
    client = _client()
    created = client.post("/v1/haas/profiles", json=_profile_body(), headers=AUTH_A)
    assert created.status_code == 200, created.text
    profile = created.json()["data"]
    assert profile["id"].startswith("hprof_")
    assert profile["object"] == "harness_profile"
    assert profile["version"] == 1
    assert profile["status"] == "draft"
    assert profile["profileFingerprint"].startswith("sha256:")
    assert profile["validation"] is None

    listed = client.get(
        "/v1/haas/profiles", params={"harnessId": "chrn_codex_default"}, headers=AUTH_A
    )
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()["data"]] == [profile["id"]]
    assert listed.json()["nextCursor"] is None


def _create(client: TestClient, body: dict[str, Any] | None = None) -> dict[str, Any]:
    response = client.post("/v1/haas/profiles", json=body or _profile_body(), headers=AUTH_A)
    assert response.status_code == 200, response.text
    return response.json()["data"]


def test_validate_edit_invalidation_and_activation_lifecycle() -> None:
    client = _client()
    first = _create(client)

    validated = client.post(f"/v1/haas/profiles/{first['id']}/validate", headers=AUTH_A)
    assert validated.status_code == 200
    assert validated.json()["data"] == {
        "profileId": first["id"],
        "valid": True,
        "findings": [],
    }

    edited_body = _profile_body(name="edited before activation")
    edited = client.put(f"/v1/haas/profiles/{first['id']}", json=edited_body, headers=AUTH_A)
    assert edited.status_code == 200
    assert edited.json()["data"]["validation"] is None
    assert (
        client.post(f"/v1/haas/profiles/{first['id']}/activate", headers=AUTH_A).status_code == 409
    )

    client.post(f"/v1/haas/profiles/{first['id']}/validate", headers=AUTH_A)
    activated = client.post(f"/v1/haas/profiles/{first['id']}/activate", headers=AUTH_A)
    assert activated.status_code == 200
    assert activated.json()["data"]["status"] == "active"
    assert (
        client.post(f"/v1/haas/profiles/{first['id']}/activate", headers=AUTH_A).json()["data"][
            "id"
        ]
        == first["id"]
    )
    assert (
        client.put(f"/v1/haas/profiles/{first['id']}", json=edited_body, headers=AUTH_A).status_code
        == 409
    )

    second = _create(client, edited_body)
    assert second["version"] == 2
    assert second["provider"] == activated.json()["data"]["provider"]
    client.post(f"/v1/haas/profiles/{second['id']}/validate", headers=AUTH_A)
    assert (
        client.post(f"/v1/haas/profiles/{second['id']}/activate", headers=AUTH_A).status_code == 200
    )
    assert (
        client.get(f"/v1/haas/profiles/{first['id']}", headers=AUTH_A).json()["data"]["status"]
        == "retired"
    )
    assert (
        client.post(f"/v1/haas/profiles/{first['id']}/activate", headers=AUTH_A).status_code == 409
    )

    copied = _create(client, edited_body)
    assert copied["version"] == 3
    assert copied["provider"] == second["provider"]


def test_profile_scope_is_hidden_and_versions_are_per_harness() -> None:
    client = _client()
    profile = _create(client)
    assert client.get(f"/v1/haas/profiles/{profile['id']}", headers=AUTH_B).status_code == 404
    assert (
        client.put(
            f"/v1/haas/profiles/{profile['id']}", json=_profile_body(), headers=AUTH_B
        ).status_code
        == 404
    )
    assert client.get("/v1/haas/profiles", headers=AUTH_B).json()["data"] == []

    harness = client.post(
        "/v1/haas/harnesses", json={"base": "codex", "name": "other"}, headers=AUTH_A
    ).json()["data"]
    other = _create(client, _profile_body(harness["id"]))
    assert other["version"] == 1


def test_validation_reports_safe_static_findings() -> None:
    client = _client()
    body = _profile_body()
    body["provider"]["credentialRef"] = "plain-text-secret"
    profile = _create(client, body)
    result = client.post(f"/v1/haas/profiles/{profile['id']}/validate", headers=AUTH_A)
    assert result.status_code == 200
    assert result.json()["data"]["valid"] is False
    assert result.json()["data"]["findings"][0]["code"] == "haas_provider_invalid"
    assert (
        client.post(f"/v1/haas/profiles/{profile['id']}/activate", headers=AUTH_A).status_code
        == 409
    )


def test_validation_reports_provider_skill_and_agents_findings() -> None:
    client = _client()
    body = _profile_body(
        provider={
            "providerId": "",
            "name": "provider",
            "model": "model",
            "credentialRef": "secret://tenant/provider/default",
            "wireApi": "responses",
            "apiType": "chat_completions",
            "baseUrl": "http://127.0.0.1/v1",
        },
        skills=[{"id": "bad", "name": "bad", "enabled": True, "files": []}],
        agentsMd={
            "mode": "snapshot",
            "sources": [
                {
                    "scope": "workspace",
                    "path": "../AGENTS.md",
                    "contentRef": "file_agents",
                    "fingerprint": "sha256:agents",
                }
            ],
            "maxBytes": 1024,
        },
    )
    profile = _create(client, body)
    validation = client.post(f"/v1/haas/profiles/{profile['id']}/validate", headers=AUTH_A).json()[
        "data"
    ]
    codes = {finding["code"] for finding in validation["findings"]}
    assert codes == {
        "haas_provider_invalid",
        "haas_skill_source_invalid",
        "haas_agents_md_invalid",
    }


def test_invalid_profile_inputs_and_filters_fail_closed() -> None:
    client = _client()
    invalid_status = client.get("/v1/haas/profiles", params={"status": "deleted"}, headers=AUTH_A)
    assert invalid_status.status_code == 400
    assert (
        client.post(
            "/v1/haas/profiles", json={**_profile_body(), "unknown": True}, headers=AUTH_A
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/v1/haas/profiles", json={"harnessId": "chrn_codex_default"}, headers=AUTH_A
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/v1/haas/profiles", json=_profile_body("chrn_missing"), headers=AUTH_A
        ).status_code
        == 404
    )
    assert (
        client.post(
            "/v1/haas/profiles", json=_profile_body(base="fake"), headers=AUTH_A
        ).status_code
        == 422
    )

    profile = _create(client)
    harness = client.post(
        "/v1/haas/harnesses", json={"base": "codex", "name": "other"}, headers=AUTH_A
    ).json()["data"]
    assert (
        client.put(
            f"/v1/haas/profiles/{profile['id']}",
            json=_profile_body(harness["id"]),
            headers=AUTH_A,
        ).status_code
        == 409
    )


def test_profile_create_idempotency_and_sqlite_persistence(tmp_path: Any) -> None:
    path = tmp_path / "profiles.db"
    first_store = SQLiteStore(path)
    client = TestClient(build_app(store=first_store))
    headers = {"Authorization": "Bearer dev-token", "Idempotency-Key": "profile-1"}
    first = client.post("/v1/haas/profiles", json=_profile_body(), headers=headers)
    replay = client.post("/v1/haas/profiles", json=_profile_body(), headers=headers)
    assert first.status_code == replay.status_code == 200
    assert first.json()["data"]["id"] == replay.json()["data"]["id"]
    profile_id = first.json()["data"]["id"]
    first_store.close()

    reopened = SQLiteStore(path)
    persisted = TestClient(build_app(store=reopened)).get(
        f"/v1/haas/profiles/{profile_id}", headers={"Authorization": "Bearer dev-token"}
    )
    assert persisted.status_code == 200
    assert persisted.json()["data"]["version"] == 1
    reopened.close()


# === appended coverage ===


import pytest

from haas.identity import Principal
from haas.profiles import (
    HarnessProfileService,
    ProfileConflictError,
    ProfileNotFoundError,
    execution_intent_fingerprint,
)
from haas.registry import HarnessRegistry
from haas.stores import HarnessRecord, MemoryStore, ProfileRecord

PRINCIPAL = Principal(principalId="p", tenantId="t", workspaceId="w")


def _provider(**over: object) -> dict:
    base = {
        "providerId": "openai",
        "name": "openai",
        "model": "gpt-test",
        "credentialRef": "secret://tenant/provider/default",
        "wireApi": "responses",
        "apiType": "responses",
    }
    base.update(over)  # type: ignore[arg-type]
    return base


def _service() -> HarnessProfileService:
    store = MemoryStore()
    registry = HarnessRegistry(store=store)
    registry.save(HarnessRecord(id="chrn_1", name="codex", base="codex"))
    return HarnessProfileService(store=store, registry=registry)


def _make_profile(
    service: HarnessProfileService,
    *,
    version: int = 1,
    status: str = "draft",
    fingerprint: str = "sha256:real",
    content: dict | None = None,
) -> ProfileRecord:
    body = {
        "harnessId": "chrn_1",
        "base": "codex",
        "provider": _provider(),
    }
    if content is not None:
        body = content
    return service.store.save_profile(
        ProfileRecord(
            id=f"hprof_{version}",
            harnessId="chrn_1",
            base="codex",
            version=version,
            content=body,
            profileFingerprint=fingerprint,
            status=status,
            tenantId="t",
            workspaceId="w",
            createdAtMs=1,
        )
    )


# --- fingerprinting ---------------------------------------------------------


def test_execution_intent_fingerprint_strips_credentials() -> None:
    a = execution_intent_fingerprint(
        {"provider": {"model": "gpt", "credentialRef": "secret://x"}, "list": [{"credentialRef": "y"}]}
    )
    b = execution_intent_fingerprint(
        {"provider": {"model": "gpt"}, "list": [{}]}
    )
    assert a == b


# --- activate conflict paths ------------------------------------------------


def test_activate_rejects_fingerprint_drift() -> None:
    service = _service()
    _make_profile(service, fingerprint="sha256:stale")
    with pytest.raises(ProfileConflictError):
        service.activate(PRINCIPAL, "hprof_1")


def test_activate_rejects_lower_than_existing_active_version() -> None:
    service = _service()
    from haas.profiles import _fingerprint

    current_content = {"harnessId": "chrn_1", "base": "codex", "provider": _provider()}
    _make_profile(
        service,
        version=2,
        status="active",
        fingerprint=_fingerprint(current_content),
        content=current_content,
    )
    draft = _make_profile(
        service,
        version=1,
        status="draft",
        fingerprint=_fingerprint(current_content),
        content=current_content,
    )
    # Mark draft as validated.
    service.store.save_profile(
        type(draft)(**{**draft.__dict__, "validation": {"valid": True, "findings": []}})
    )
    with pytest.raises(ProfileConflictError):
        service.activate(PRINCIPAL, draft.id)


# --- resolve_ref / execution_snapshot ---------------------------------------


def test_resolve_ref_rejects_fingerprint_mismatch() -> None:
    service = _service()
    _make_profile(service, fingerprint="sha256:a")
    with pytest.raises(ProfileNotFoundError):
        service.resolve_ref(
            PRINCIPAL,
            "chrn_1",
            {"profileId": "hprof_1", "profileVersion": 1, "profileFingerprint": "sha256:different"},
        )


def test_execution_snapshot_rejects_unvalidated_profile() -> None:
    service = _service()
    profile = _make_profile(service, status="draft")
    with pytest.raises(ProfileConflictError):
        service.execution_snapshot(profile)


# --- static findings ----------------------------------------------------------


def test_findings_flag_openai_compatible_without_api_type() -> None:
    service = _service()
    findings = service._findings(
        {"harnessId": "chrn_1", "base": "codex", "provider": _provider(apiType="", wireApi="openai-compatible")}
    )
    assert any(f["code"] == "haas_provider_invalid" and f["field"] == "provider.apiType" for f in findings)


def test_findings_flag_unsupported_wire_api() -> None:
    service = _service()
    findings = service._findings(
        {"harnessId": "chrn_1", "base": "codex", "provider": _provider(wireApi="gpt-json")}
    )
    assert any(f["field"] == "provider.wireApi" for f in findings)


def test_findings_flag_non_https_base_url() -> None:
    service = _service()
    findings = service._findings(
        {"harnessId": "chrn_1", "base": "codex", "provider": _provider(baseUrl="http://insecure.example/v1")}
    )
    assert any(f["field"] == "provider.baseUrl" for f in findings)


def test_findings_flag_invalid_agents_md_mode() -> None:
    service = _service()
    findings = service._findings(
        {
            "harnessId": "chrn_1",
            "base": "codex",
            "provider": _provider(),
            "agentsMd": {"mode": "live"},
        }
    )
    assert any(f["code"] == "haas_agents_md_invalid" for f in findings)


def test_findings_flag_unsafe_agents_md_path() -> None:
    service = _service()
    findings = service._findings(
        {
            "harnessId": "chrn_1",
            "base": "codex",
            "provider": _provider(),
            "agentsMd": {"mode": "snapshot", "sources": [{"path": "../AGENTS.md"}]},
        }
    )
    assert any(f["field"] == "agentsMd.sources" for f in findings)