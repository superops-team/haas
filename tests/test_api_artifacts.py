"""S6 artifact API wiring contract tests.

Covers specs/artifact-store/README.md §5.1/§6.1/§6.1.1/§6.1.2 and the
OpenAPI `File`/`FileEnvelope`/`HaasEnvelope` shapes in
specs/haas-protocol/haas-2026-09-10.openapi.yaml.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient

from haas.api import DEFAULT_TOKEN, build_app
from haas.identity import Principal

# /v1/haas/* control-plane surface is part of the published protocol contract.
pytestmark = pytest.mark.adk

AUTH = {"Authorization": f"Bearer {DEFAULT_TOKEN}"}
OTHER_TOKEN = "other-token"
OPENAPI_PATH = Path("specs/haas-protocol/haas-2026-09-10.openapi.yaml")


def _client() -> TestClient:
    app = build_app(
        identity_tokens={
            DEFAULT_TOKEN: Principal(principalId="p_dev"),
            OTHER_TOKEN: Principal(principalId="p_other"),
        }
    )
    return TestClient(app)


def _upload(client: TestClient, name: str = "report.md", body: bytes = b"hello") -> dict:
    resp = client.post(
        "/v1/haas/files",
        files={"file": (name, body, "text/markdown")},
        headers=AUTH,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _openapi() -> dict[str, Any]:
    with OPENAPI_PATH.open() as handle:
        return yaml.safe_load(handle)


def test_openapi_profile_contract_is_published() -> None:
    schema = _openapi()
    paths = schema["paths"]
    components = schema["components"]["schemas"]

    for path in [
        "/v1/haas/profiles",
        "/v1/haas/profiles/{profile_id}",
        "/v1/haas/profiles/{profile_id}/validate",
        "/v1/haas/profiles/{profile_id}/activate",
        "/v1/haas/sessions/{session_id}/profile",
        "/v1/haas/sessions/{session_id}/profile-rebind",
    ]:
        assert path in paths

    for name in [
        "HarnessProfile",
        "HarnessProfileCreate",
        "ProviderRoute",
        "McpServerConfig",
        "SkillBundle",
        "AgentsMdConfig",
        "EffectiveHarnessProfile",
        "ProfileRebindRequest",
    ]:
        assert name in components

    error_codes = set(
        components["ErrorDetail"]["properties"]["haasError"]["properties"]["code"]["enum"]
    )
    assert {
        "haas_profile_not_found",
        "haas_profile_conflict",
        "haas_profile_rebind_required",
        "haas_profile_rebind_unsupported",
        "haas_agents_md_invalid",
    } <= error_codes


def test_openapi_tool_activity_metadata_is_additive_and_bounded() -> None:
    components = _openapi()["components"]["schemas"]
    assert components["ToolActivityKind"]["enum"] == [
        "command",
        "read",
        "search",
        "edit",
        "tool",
    ]
    preview = components["ToolOutputPreview"]
    assert preview["maxLength"] == 4096
    assert preview["x-haas-max-utf8-bytes"] == 4096
    assert preview["x-haas-max-logical-lines"] == 20
    for schema_name in (
        "ToolStartedEventMetadata",
        "ToolOutputEventMetadata",
        "ToolCompletedEventMetadata",
        "ToolFailedEventMetadata",
    ):
        schema = components[schema_name]
        assert "activityKind" in schema["properties"]
        assert "activityKind" not in schema["required"]


def test_openapi_profile_rebind_excludes_delegated_sessions() -> None:
    schema = _openapi()
    rebind = schema["paths"]["/v1/haas/sessions/{session_id}/profile-rebind"]["post"]
    description = rebind["description"]
    assert "haas_profile_rebind_unsupported" in description
    assert "delegated-sessions" in description


def test_openapi_profile_config_boundaries_are_explicit() -> None:
    components = _openapi()["components"]["schemas"]

    harness_create = components["HarnessCreate"]
    assert "Dynamic runtime configuration belongs" in harness_create["description"]
    assert harness_create["properties"]["provider"]["deprecated"] is True
    assert harness_create["properties"]["mcpServers"]["deprecated"] is True
    assert harness_create["properties"]["skills"]["deprecated"] is True

    agents_path = components["AgentsMdSource"]["properties"]["path"]
    assert "Absolute paths" in agents_path["description"]
    assert "symlink escapes" in agents_path["description"]

    delegated_provider = components["DelegatedProvider"]["properties"]
    assert "providerFingerprint" in delegated_provider
    assert {"providerId", "name", "wireApi"} <= set(components["ProviderRoute"]["required"])
    assert "apiType" in delegated_provider


def test_openapi_delegated_policy_update_barrier_is_published() -> None:
    schema = _openapi()
    components = schema["components"]["schemas"]
    update = components["DelegatedSessionPolicyUpdate"]
    required_domains = {tuple(branch["required"]) for branch in update["anyOf"]}
    assert required_domains == {
        ("delegationPolicySnapshot",),
        ("mountManifest",),
        ("profileRef",),
        ("image",),
    }

    contract = components["DelegatedSessionContract"]
    assert {
        "desiredRevision",
        "appliedRevision",
        "pendingPolicyUpdate",
        "lastPolicyUpdateResult",
    } <= set(contract["required"])
    assert "PolicyUpdateResult" in components

    event_text = repr(components["CanonicalHaasEvent"])
    assert "haas.delegation.policy_update_pending" in event_text
    assert "haas.delegation.policy_update_applied" in event_text
    assert "haas.delegation.policy_update_failed" in event_text


def test_openapi_local_policy_update_and_action_approval_are_published() -> None:
    schema = _openapi()
    components = schema["components"]["schemas"]
    operation = schema["paths"]["/v1/haas/sessions/{session_id}/policy"]["post"]
    idempotency = next(p for p in operation["parameters"] if p.get("name") == "Idempotency-Key")
    assert idempotency["required"] is True
    revision = components["SessionPolicyRevision"]
    assert {
        "desiredPolicy",
        "appliedPolicy",
        "desiredRevision",
        "appliedRevision",
    } <= set(revision["required"])
    assert components["ApprovalDecision"]["properties"]["scope"]["const"] == "action"
    codes = components["ErrorDetail"]["properties"]["haasError"]["properties"]["code"]["enum"]
    assert "haas_policy_revision_conflict" in codes


def test_openapi_execution_replay_expiry_is_explicit() -> None:
    schema = _openapi()
    components = schema["components"]
    paths = schema["paths"]

    assert "IdempotencyExpiresAt" in components["headers"]
    for path in ["/run", "/run_sse"]:
        operation = paths[path]["post"]
        assert "410" in operation["responses"]
        assert (
            operation["responses"]["200"]["headers"]["Idempotency-Expires-At"]["$ref"]
            == "#/components/headers/IdempotencyExpiresAt"
        )

    invocation = components["schemas"]["Invocation"]["properties"]
    assert "idempotencyExpiresAtMs" in invocation
    error_codes = set(
        components["schemas"]["ErrorDetail"]["properties"]["haasError"]["properties"]["code"][
            "enum"
        ]
    )
    assert "haas_idempotency_expired" in error_codes


def test_openapi_snapshot_content_and_mcp_transport_are_bounded() -> None:
    components = _openapi()["components"]["schemas"]
    transport_values = components["McpServerConfig"]["properties"]["transport"]
    assert transport_values["enum"] == ["http", "sse"]

    skill_file = components["SkillFile"]
    assert "contentRef" in skill_file["properties"]
    assert {tuple(branch["required"]) for branch in skill_file["oneOf"]} == {
        ("content",),
        ("contentB64",),
        ("contentRef",),
    }
    assert components["AgentsMdSource"]["properties"]["contentRef"]["pattern"] == "^file_"


# --- upload -----------------------------------------------------------------


def test_upload_returns_file_envelope() -> None:
    payload = _upload(_client())
    assert "traceId" in payload
    data = payload["data"]
    assert data["object"] == "file"
    assert data["id"].startswith("file_")
    assert data["filename"] == "report.md"
    assert data["bytes"] == 5
    assert data["sha256"]
    assert data["mediaType"] == "text/markdown"


def test_upload_requires_auth() -> None:
    resp = _client().post("/v1/haas/files", files={"file": ("a.txt", b"x", "text/plain")})
    assert resp.status_code == 401
    assert resp.json()["haasError"]["code"] == "missing_credential"


def test_upload_rejects_oversized_file() -> None:
    app = build_app(max_file_bytes=4)
    client = TestClient(app)
    resp = client.post(
        "/v1/haas/files",
        files={"file": ("big.bin", b"123456", "application/octet-stream")},
        headers=AUTH,
    )
    assert resp.status_code == 413
    assert resp.json()["haasError"]["code"] == "haas_file_too_large"


def test_upload_rejects_traversal_filename() -> None:
    resp = _client().post(
        "/v1/haas/files",
        files={"file": ("../../etc/passwd", b"x", "text/plain")},
        headers=AUTH,
    )
    assert resp.status_code == 400
    assert resp.json()["haasError"]["code"] == "invalid_input"


@pytest.mark.parametrize("filename", ["nested/report.md", r"nested\\report.md", "..", "."])
def test_upload_rejects_non_basename_filename(filename: str) -> None:
    resp = _client().post(
        "/v1/haas/files",
        files={"file": (filename, b"x", "text/plain")},
        headers=AUTH,
    )
    assert resp.status_code == 400
    assert resp.json()["haasError"]["code"] == "invalid_input"


# --- download ---------------------------------------------------------------


def test_download_returns_bytes_with_nosniff() -> None:
    client = _client()
    file_id = _upload(client, body=b"payload-bytes")["data"]["id"]
    resp = client.get(f"/v1/haas/files/{file_id}/content", headers=AUTH)
    assert resp.status_code == 200
    assert resp.content == b"payload-bytes"
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["content-disposition"].startswith("attachment")


def test_download_unknown_file_returns_404() -> None:
    resp = _client().get("/v1/haas/files/file_missing/content", headers=AUTH)
    assert resp.status_code == 404
    assert resp.json()["haasError"]["code"] == "haas_file_not_found"


def test_download_cross_principal_returns_404() -> None:
    """Security Boundary §8.3: cross-scope read is 404, never 403."""
    client = _client()
    file_id = _upload(client)["data"]["id"]
    resp = client.get(
        f"/v1/haas/files/{file_id}/content",
        headers={"Authorization": f"Bearer {OTHER_TOKEN}"},
    )
    assert resp.status_code == 404
    assert resp.json()["haasError"]["code"] == "haas_file_not_found"


def test_pdf_preview_not_implemented() -> None:
    client = _client()
    file_id = _upload(client)["data"]["id"]
    resp = client.get(f"/v1/haas/files/{file_id}/pdf", headers=AUTH)
    assert resp.status_code == 501
    assert resp.json()["haasError"]["code"] == "haas_preview_unavailable"


# --- session artifact listing ----------------------------------------------


def test_list_session_artifacts_empty() -> None:
    resp = _client().get("/v1/haas/sessions/hsess_none/artifacts", headers=AUTH)
    assert resp.status_code == 200
    body = resp.json()
    assert body["data"]["artifacts"] == []
    assert "traceId" in body


def test_list_session_artifacts_after_attach() -> None:
    app = build_app()
    client = TestClient(app)
    store = app.state.runtime.artifacts
    store.register("hsess_1", "output/a.md", b"aa", owner_principal_id="p_dev")
    resp = client.get("/v1/haas/sessions/hsess_1/artifacts", headers=AUTH)
    assert resp.status_code == 200
    artifacts = resp.json()["data"]["artifacts"]
    assert len(artifacts) == 1
    assert artifacts[0]["relativePath"] == "output/a.md"
    assert artifacts[0]["object"] == "file"


def test_list_session_artifacts_scoped_by_principal() -> None:
    app = build_app(
        identity_tokens={
            DEFAULT_TOKEN: Principal(principalId="p_dev"),
            OTHER_TOKEN: Principal(principalId="p_other"),
        }
    )
    client = TestClient(app)
    app.state.runtime.artifacts.register(
        "hsess_1", "output/a.md", b"aa", owner_principal_id="p_dev"
    )
    resp = client.get(
        "/v1/haas/sessions/hsess_1/artifacts",
        headers={"Authorization": f"Bearer {OTHER_TOKEN}"},
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["artifacts"] == []


# --- archive ----------------------------------------------------------------


def test_archive_returns_zip() -> None:
    app = build_app()
    client = TestClient(app)
    app.state.runtime.artifacts.register(
        "hsess_1", "output/a.md", b"alpha", owner_principal_id="p_dev"
    )
    app.state.runtime.artifacts.register(
        "hsess_1", "output/b.txt", b"beta", owner_principal_id="p_dev"
    )
    resp = client.get("/v1/haas/sessions/hsess_1/artifacts/archive", headers=AUTH)
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"
    assert resp.headers["x-content-type-options"] == "nosniff"
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        assert sorted(zf.namelist()) == ["output/a.md", "output/b.txt"]
        assert zf.read("output/a.md") == b"alpha"


def test_archive_empty_session_returns_404() -> None:
    resp = _client().get("/v1/haas/sessions/hsess_none/artifacts/archive", headers=AUTH)
    assert resp.status_code == 404
    assert resp.json()["haasError"]["code"] == "haas_file_not_found"


# --- status / diagnostics ---------------------------------------------------


def test_status_returns_snapshot() -> None:
    resp = _client().get("/v1/haas/status", headers=AUTH)
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["status"] in {"ok", "degraded"}
    assert data["protocol"]["adkProtocol"] == "2.0"
    assert isinstance(data["adapters"], list)


def test_diagnostics_is_redacted() -> None:
    resp = _client().get("/v1/haas/diagnostics", headers=AUTH)
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["partial"] is False
    # Diagnostics must never carry raw credential material.
    assert DEFAULT_TOKEN not in resp.text
