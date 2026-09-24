"""Harness Registry CRUD + model catalog API contract tests.

Covers specs/harness-registry §5.1 / §5.1.1 / §5.1.2 / §5.1.3 / §10 and the
OpenAPI `Harness` / `HarnessEnvelope` / `HarnessListEnvelope` /
`ModelCatalogEnvelope` shapes.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from haas.api import build_app
from haas.identity import Principal

# /v1/haas/* control-plane surface is part of the published protocol contract.
pytestmark = pytest.mark.adk

TOKEN_A = "tok-a"
TOKEN_B = "tok-b"
AUTH_A = {"Authorization": f"Bearer {TOKEN_A}"}
AUTH_B = {"Authorization": f"Bearer {TOKEN_B}"}

SEEDED = "chrn_codex_default"


def _client() -> TestClient:
    """Two principals in different tenants, to prove scope isolation."""
    return TestClient(
        build_app(
            identity_tokens={
                TOKEN_A: Principal(principalId="p_a", tenantId="t_a"),
                TOKEN_B: Principal(principalId="p_b", tenantId="t_b"),
            }
        )
    )


def _create(client: TestClient, headers: dict[str, str], **body: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"base": "codex", "name": "my-codex"}
    payload.update(body)
    resp = client.post("/v1/haas/harnesses", json=payload, headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


# --- create -----------------------------------------------------------------


def test_create_returns_harness_envelope() -> None:
    data = _create(_client(), AUTH_A, defaultModel="gpt-5.6-terra")
    assert data["object"] == "harness"
    assert data["id"].startswith("chrn_")
    assert data["base"] == "codex"
    assert data["name"] == "my-codex"
    assert data["defaultModel"] == "gpt-5.6-terra"
    assert data["createdAtMs"] > 0
    assert data["updatedAtMs"] > 0


def test_create_requires_auth() -> None:
    resp = _client().post("/v1/haas/harnesses", json={"base": "codex"})
    assert resp.status_code == 401


def test_create_rejects_missing_base() -> None:
    resp = _client().post("/v1/haas/harnesses", json={"name": "no-base"}, headers=AUTH_A)
    assert resp.status_code == 400
    assert resp.json()["haasError"]["code"] == "invalid_input"


def test_create_rejects_unregistered_base() -> None:
    """spec §5.1.2: base must be a registered adapter, else 422."""
    resp = _client().post("/v1/haas/harnesses", json={"base": "not-a-harness"}, headers=AUTH_A)
    assert resp.status_code == 422
    assert resp.json()["haasError"]["code"] == "haas_unsupported_base"


def test_create_is_idempotent_with_key() -> None:
    client = _client()
    body = {"base": "codex", "name": "idem"}
    headers = {**AUTH_A, "Idempotency-Key": "hk-1"}
    first = client.post("/v1/haas/harnesses", json=body, headers=headers)
    second = client.post("/v1/haas/harnesses", json=body, headers=headers)
    assert first.status_code == second.status_code == 200
    # Must not create two harnesses.
    assert first.json()["data"]["id"] == second.json()["data"]["id"]


def test_create_conflicting_idempotency_key_is_409() -> None:
    client = _client()
    headers = {**AUTH_A, "Idempotency-Key": "hk-2"}
    client.post("/v1/haas/harnesses", json={"base": "codex", "name": "x"}, headers=headers)
    resp = client.post("/v1/haas/harnesses", json={"base": "codex", "name": "y"}, headers=headers)
    assert resp.status_code == 409
    assert resp.json()["haasError"]["code"] == "haas_idempotency_conflict"


def test_create_idempotency_key_is_scoped_to_principal() -> None:
    client = _client()
    key = {"Idempotency-Key": "shared-caller-key"}

    first = client.post(
        "/v1/haas/harnesses",
        json={"base": "codex", "name": "tenant-a"},
        headers={**AUTH_A, **key},
    )
    second = client.post(
        "/v1/haas/harnesses",
        json={"base": "codex", "name": "tenant-b"},
        headers={**AUTH_B, **key},
    )

    assert first.status_code == second.status_code == 200
    assert first.json()["data"]["id"] != second.json()["data"]["id"]


def test_create_does_not_echo_credential_material() -> None:
    """spec §8: only credentialRef/fingerprint, never a raw secret."""
    data = _create(
        _client(),
        AUTH_A,
        provider={
            "providerId": "openai",
            "name": "openai-compatible",
            "baseUrl": "https://provider.example.com/v1",
            "wireApi": "responses",
            "apiType": "responses",
            "credentialRef": "secret://tenant/provider/default",
        },
    )
    assert data["provider"]["credentialRef"] == "secret://tenant/provider/default"
    assert "apiKey" not in data["provider"]


# --- list / get -------------------------------------------------------------


def test_list_returns_seeded_and_created() -> None:
    client = _client()
    created = _create(client, AUTH_A, name="listed")
    resp = client.get("/v1/haas/harnesses", headers=AUTH_A)
    assert resp.status_code == 200
    body = resp.json()
    assert "traceId" in body
    ids = [h["id"] for h in body["data"]["harnesses"]]
    assert created["id"] in ids
    assert all(h["object"] == "harness" for h in body["data"]["harnesses"])


def test_get_one_harness() -> None:
    client = _client()
    created = _create(client, AUTH_A)
    resp = client.get(f"/v1/haas/harnesses/{created['id']}", headers=AUTH_A)
    assert resp.status_code == 200
    assert resp.json()["data"]["id"] == created["id"]


def test_get_unknown_harness_is_404() -> None:
    resp = _client().get("/v1/haas/harnesses/chrn_missing", headers=AUTH_A)
    assert resp.status_code == 404
    assert resp.json()["haasError"]["code"] == "haas_harness_not_found"


def test_get_cross_tenant_harness_is_404() -> None:
    """spec §5.1.3: cross-scope read is 404, not 403."""
    client = _client()
    created = _create(client, AUTH_A)
    resp = client.get(f"/v1/haas/harnesses/{created['id']}", headers=AUTH_B)
    assert resp.status_code == 404
    assert resp.json()["haasError"]["code"] == "haas_harness_not_found"


def _ids(client: TestClient, headers: dict[str, str]) -> list[str]:
    body = client.get("/v1/haas/harnesses", headers=headers).json()
    return [h["id"] for h in body["data"]["harnesses"]]


def test_list_is_scoped_per_tenant() -> None:
    client = _client()
    mine = _create(client, AUTH_A, name="mine")
    theirs = _create(client, AUTH_B, name="theirs")
    ids_a = _ids(client, AUTH_A)
    ids_b = _ids(client, AUTH_B)
    assert mine["id"] in ids_a and mine["id"] not in ids_b
    assert theirs["id"] in ids_b and theirs["id"] not in ids_a


def test_list_apps_is_scoped_too() -> None:
    """/list-apps must not leak another tenant's harness ids."""
    client = _client()
    theirs = _create(client, AUTH_B, name="theirs")
    apps = client.get("/list-apps", headers=AUTH_A).json()
    assert theirs["id"] not in apps


# --- update -----------------------------------------------------------------


def test_update_replaces_mutable_fields() -> None:
    client = _client()
    created = _create(client, AUTH_A, name="before")
    resp = client.put(
        f"/v1/haas/harnesses/{created['id']}",
        json={"base": "codex", "name": "after", "defaultModel": "gpt-x"},
        headers=AUTH_A,
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["name"] == "after"
    assert data["defaultModel"] == "gpt-x"
    assert data["id"] == created["id"]
    assert data["createdAtMs"] == created["createdAtMs"]


def test_update_accepts_matching_immutable_fields() -> None:
    """spec §5.1.1: read-modify-write round-trip must stay idempotent."""
    client = _client()
    created = _create(client, AUTH_A)
    echoed = dict(created)
    echoed["name"] = "renamed"
    resp = client.put(f"/v1/haas/harnesses/{created['id']}", json=echoed, headers=AUTH_A)
    assert resp.status_code == 200
    assert resp.json()["data"]["name"] == "renamed"


@pytest.mark.parametrize("field", ["id", "base", "createdAtMs"])
def test_update_rejects_conflicting_immutable_field(field: str) -> None:
    client = _client()
    created = _create(client, AUTH_A)
    conflicting: dict[str, Any] = {"base": "codex", "name": "n"}
    conflicting[field] = "chrn_other" if field != "createdAtMs" else 1
    if field == "base":
        conflicting[field] = "fake"
    resp = client.put(f"/v1/haas/harnesses/{created['id']}", json=conflicting, headers=AUTH_A)
    assert resp.status_code == 400
    assert resp.json()["haasError"]["code"] == "invalid_input"
    # Must not partially apply.
    after = client.get(f"/v1/haas/harnesses/{created['id']}", headers=AUTH_A).json()["data"]
    assert after["name"] == created["name"]


def test_update_ignores_caller_supplied_updated_at() -> None:
    client = _client()
    created = _create(client, AUTH_A)
    resp = client.put(
        f"/v1/haas/harnesses/{created['id']}",
        json={"base": "codex", "name": "n", "updatedAtMs": 1},
        headers=AUTH_A,
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["updatedAtMs"] != 1


def test_update_unknown_is_404() -> None:
    resp = _client().put("/v1/haas/harnesses/chrn_missing", json={"base": "codex"}, headers=AUTH_A)
    assert resp.status_code == 404
    assert resp.json()["haasError"]["code"] == "haas_harness_not_found"


def test_update_cross_tenant_is_404() -> None:
    client = _client()
    created = _create(client, AUTH_A)
    resp = client.put(
        f"/v1/haas/harnesses/{created['id']}",
        json={"base": "codex", "name": "hijacked"},
        headers=AUTH_B,
    )
    assert resp.status_code == 404


# --- delete -----------------------------------------------------------------


def test_delete_removes_from_resolution_but_keeps_sessions() -> None:
    """spec §10: deleting a harness must not delete historical sessions."""
    client = _client()
    created = _create(client, AUTH_A, name="doomed")
    run = client.post(
        "/run",
        json={
            "appName": created["id"],
            "userId": "u_1",
            "sessionId": "hsess_keep",
            "newMessage": {"role": "user", "parts": [{"text": "hi"}]},
        },
        headers=AUTH_A,
    )
    assert run.status_code == 200

    assert client.delete(f"/v1/haas/harnesses/{created['id']}", headers=AUTH_A).status_code == 200
    # No longer selectable for new runs.
    assert client.get(f"/v1/haas/harnesses/{created['id']}", headers=AUTH_A).status_code == 404
    resp = client.post(
        "/run",
        json={
            "appName": created["id"],
            "userId": "u_1",
            "newMessage": {"role": "user", "parts": []},
        },
        headers=AUTH_A,
    )
    assert resp.status_code == 404
    assert resp.json()["haasError"]["code"] == "app_not_found"
    # Historical session is still readable.
    session = client.get(f"/apps/{created['id']}/users/u_1/sessions/hsess_keep", headers=AUTH_A)
    assert session.status_code == 200
    assert session.json()["events"]


def test_delete_unknown_is_404() -> None:
    resp = _client().delete("/v1/haas/harnesses/chrn_missing", headers=AUTH_A)
    assert resp.status_code == 404


def test_delete_cross_tenant_is_404() -> None:
    client = _client()
    created = _create(client, AUTH_A)
    assert client.delete(f"/v1/haas/harnesses/{created['id']}", headers=AUTH_B).status_code == 404
    # Still alive for the real owner.
    assert client.get(f"/v1/haas/harnesses/{created['id']}", headers=AUTH_A).status_code == 200


# --- run resolution ---------------------------------------------------------


def test_run_resolves_created_harness_by_name() -> None:
    """spec §4: appName resolves by id first, then by name."""
    client = _client()
    _create(client, AUTH_A, name="by-name")
    resp = client.post(
        "/run",
        json={
            "appName": "by-name",
            "userId": "u_1",
            "newMessage": {"role": "user", "parts": [{"text": "hi"}]},
        },
        headers=AUTH_A,
    )
    assert resp.status_code == 200


def test_run_cannot_use_other_tenant_harness() -> None:
    client = _client()
    theirs = _create(client, AUTH_B, name="theirs")
    resp = client.post(
        "/run",
        json={
            "appName": theirs["id"],
            "userId": "u_1",
            "newMessage": {"role": "user", "parts": []},
        },
        headers=AUTH_A,
    )
    assert resp.status_code == 404
    assert resp.json()["haasError"]["code"] == "app_not_found"


# --- model catalog ----------------------------------------------------------


def test_models_catalog_groups_by_backend() -> None:
    resp = _client().get("/v1/haas/models", headers=AUTH_A)
    assert resp.status_code == 200
    body = resp.json()
    assert "traceId" in body
    backends = body["data"]["backends"]
    assert "codex" in backends
    entry = backends["codex"]
    assert "default" in entry
    assert isinstance(entry["models"], list)


def test_models_requires_auth() -> None:
    assert _client().get("/v1/haas/models").status_code == 401


# --- response_model hardening (P1-1 Stage 2) --------------------------------

_FORBIDDEN_KEY_SUBSTRINGS = ("apikey", "api_key", "secret", "rawtoken", "password", "privatekey")


def _walk_forbidden(obj: Any, path: str = "") -> list[str]:
    found: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            lk = str(k).lower()
            if any(bad in lk for bad in _FORBIDDEN_KEY_SUBSTRINGS):
                found.append(f"{path}.{k}")
            found.extend(_walk_forbidden(v, f"{path}.{k}"))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            found.extend(_walk_forbidden(v, f"{path}[{i}]"))
    return found


def test_harness_get_response_model_strips_raw_secret_material() -> None:
    client = _client()
    created = _create(client, AUTH_A, provider={
        "providerId": "openai",
        "name": "openai-compatible",
        "baseUrl": "https://provider.example.com/v1",
        "wireApi": "responses",
        "apiType": "responses",
        "credentialRef": "secret://tenant/provider/default",
        "apiKey": "sk-leak-me-not",  # must never round-trip
    })
    resp = client.get(f"/v1/haas/harnesses/{created['id']}", headers=AUTH_A)
    assert resp.status_code == 200
    body = resp.json()
    assert not _walk_forbidden(body), f"raw secret key leaked: {_walk_forbidden(body)}"
    # credentialRef (the public reference) is still present per spec section 8.
    assert body["data"]["provider"]["credentialRef"] == "secret://tenant/provider/default"


def test_harness_list_response_model_strips_raw_secret_material() -> None:
    client = _client()
    _create(client, AUTH_A, name="listed-prot")
    resp = client.get("/v1/haas/harnesses", headers=AUTH_A)
    assert resp.status_code == 200
    assert not _walk_forbidden(resp.json()), f"raw secret key leaked: {_walk_forbidden(resp.json())}"
