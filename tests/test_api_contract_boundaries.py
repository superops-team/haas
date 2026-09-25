"""Contract tests for API validation and compatibility boundaries."""

from __future__ import annotations

import json
import math
from typing import Any

import pytest
from fastapi.testclient import TestClient

from haas.api import build_app
from haas.harnesses import FakeAdapter
from haas.identity import Principal

pytestmark = pytest.mark.adk

TOKEN = "boundary-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def _client(**kwargs: Any) -> TestClient:
    app = build_app(
        identity_tokens={
            TOKEN: Principal(
                principalId="p_boundary",
                tenantId="t_boundary",
                userIds=frozenset({"u_boundary"}),
            )
        },
        **kwargs,
    )
    return TestClient(app)


def _run_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "appName": "chrn_codex_default",
        "userId": "u_boundary",
        "sessionId": "hsess_boundary",
        "newMessage": {"role": "user", "parts": [{"text": "hello"}]},
    }
    body.update(overrides)
    return body


def _delegated_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "managerSessionId": "mgr_boundary",
        "haasSessionId": "hsess_delegated_boundary",
        "haasUserId": "u_boundary",
        "harnessId": "chrn_codex_default",
        "harnessBase": "codex",
        "image": {
            "reference": "registry.invalid/haas",
            "digest": "sha256:" + "a" * 64,
            "variant": "lite",
            "platform": "linux/arm64",
        },
        "profileRef": {
            "profileId": "hprof_boundary",
            "profileVersion": 1,
            "profileFingerprint": "sha256:profile",
        },
        "provider": {
            "providerId": "openai",
            "name": "OpenAI",
            "model": "model-1",
            "credentialRef": "secret://provider/boundary",
            "wireApi": "responses",
        },
        "workspaceMode": "bind_mount",
        "mountManifest": {
            "version": 1,
            "primaryWorkspace": {
                "hostPathCanonical": "/tmp/haas-boundary",
                "containerPath": "/workspace",
                "access": "rw",
            },
            "extraMounts": [],
        },
    }
    body.update(overrides)
    return body


@pytest.mark.parametrize(
    "timeout",
    [True, "10", math.inf, -math.inf, math.nan, 0, -1],
)
def test_run_rejects_invalid_timeout_contract(timeout: object) -> None:
    body = json.dumps(_run_body(haas={"timeoutSeconds": timeout}), allow_nan=True)
    response = _client().post(
        "/run",
        content=body,
        headers={**AUTH, "Content-Type": "application/json"},
    )
    assert response.status_code == 400
    assert response.json()["haasError"]["code"] == "invalid_input"


def test_run_caps_timeout_at_protocol_maximum() -> None:
    class RecordingAdapter(FakeAdapter):
        def __init__(self) -> None:
            self.requests = []

        async def start_turn(self, request):
            self.requests.append(request)
            return await super().start_turn(request)

    adapter = RecordingAdapter()
    client = _client(adapter=adapter)
    response = client.post(
        "/run",
        json=_run_body(haas={"timeoutSeconds": 1_000_000}),
        headers=AUTH,
    )
    assert response.status_code == 200
    assert adapter.requests[-1].timeoutSeconds == 86_400


@pytest.mark.parametrize(
    "policy",
    [
        {"idleTtlSeconds": 0},
        {
            "idleTtlSeconds": 60,
            "maxContainerLifetimeSeconds": 3600,
            "rwWorkspaceConcurrency": "many_writers",
            "queuePolicy": "fifo",
            "restorePolicy": "fail_closed",
            "mountPolicy": "manager_approved",
        },
        {
            "idleTtlSeconds": 60,
            "maxContainerLifetimeSeconds": 3600,
            "rwWorkspaceConcurrency": "single_writer",
            "queuePolicy": "lifo",
            "restorePolicy": "fail_closed",
            "mountPolicy": "manager_approved",
        },
        {
            "idleTtlSeconds": 60,
            "maxContainerLifetimeSeconds": 3600,
            "rwWorkspaceConcurrency": "single_writer",
            "queuePolicy": "fifo",
            "restorePolicy": "fail_closed",
            "mountPolicy": "manager_approved",
            "network": {"defaultAction": "deny", "allow": [123]},
        },
        {
            "idleTtlSeconds": 60,
            "maxContainerLifetimeSeconds": 3600,
            "rwWorkspaceConcurrency": "single_writer",
            "queuePolicy": "fifo",
            "restorePolicy": "fail_closed",
            "mountPolicy": "manager_approved",
            "tools": {"approvalMode": "on-request", "disabled": [123]},
        },
    ],
)
def test_delegated_session_rejects_invalid_policy_contract(policy: dict[str, Any]) -> None:
    response = _client().post(
        "/v1/haas/delegated-sessions",
        json=_delegated_body(delegationPolicySnapshot=policy),
        headers=AUTH,
    )
    assert response.status_code == 400
    assert response.json()["haasError"]["code"] == "invalid_input"


@pytest.mark.parametrize(
    "mount",
    [
        {
            "version": 0,
            "primaryWorkspace": {
                "hostPathCanonical": "/tmp/haas-boundary",
                "containerPath": "/workspace",
                "access": "rw",
            },
        },
        {
            "version": 1,
            "primaryWorkspace": {
                "hostPathCanonical": "relative",
                "containerPath": "/workspace",
                "access": "rw",
            },
        },
        {
            "version": 1,
            "primaryWorkspace": {
                "hostPathCanonical": "/tmp/../tmp/haas-boundary",
                "containerPath": "/workspace",
                "access": "rw",
            },
        },
        {
            "version": 1,
            "primaryWorkspace": {
                "hostPathCanonical": "/tmp/haas-boundary",
                "containerPath": "/workspace",
                "access": "execute",
            },
        },
        {
            "version": 1,
            "primaryWorkspace": {
                "hostPathCanonical": "/tmp/haas-boundary",
                "containerPath": "/workspace",
                "access": "rw",
            },
            "extraMounts": [
                {
                    "hostPathCanonical": "/tmp/.ssh/key",
                    "containerPath": "/secrets",
                    "access": "ro",
                }
            ],
        },
    ],
)
def test_delegated_session_rejects_invalid_mount_contract(mount: dict[str, Any]) -> None:
    response = _client().post(
        "/v1/haas/delegated-sessions",
        json=_delegated_body(mountManifest=mount),
        headers=AUTH,
    )
    assert response.status_code == 403
    assert response.json()["haasError"]["code"] == "haas_delegation_mount_invalid"


@pytest.mark.parametrize(
    "overrides",
    [
        {"managerSessionId": ""},
        {"image": "not-an-object"},
        {"image": {"reference": "registry.invalid/haas", "digest": 7}},
        {"provider": {"providerId": "openai", "model": "m"}},
        {"provider": {"providerId": "openai", "model": "m", "credentialRef": "raw"}},
        {"profileRef": "not-an-object"},
        {
            "profileRef": {
                "profileId": "",
                "profileVersion": 0,
                "profileFingerprint": "",
            }
        },
        {"workspaceMode": ""},
    ],
)
def test_delegated_session_rejects_invalid_resource_contract(
    overrides: dict[str, Any],
) -> None:
    response = _client().post(
        "/v1/haas/delegated-sessions",
        json=_delegated_body(**overrides),
        headers=AUTH,
    )
    assert response.status_code in {400, 403}
    assert response.json()["haasError"]["code"] in {
        "invalid_input",
        "haas_mount_rejected",
    }


def test_capabilities_maps_unknown_adapter_mode_fail_closed() -> None:
    class UnknownCapabilityAdapter:
        base = "fake"
        adapter_id = "fake-capability"
        version = "1"

        async def probe(self):
            from haas.harnesses.base import AdapterProbe

            return AdapterProbe(
                adapterId=self.adapter_id,
                base=self.base,
                status="ready",
                runtimeVersion="1",
                transport="memory",
                capabilities={
                    "streaming": "future_unknown_mode",
                    "cancellation": "hard",
                    "approval": "unavailable",
                    "input": False,
                },
            )

    client = _client(adapter=UnknownCapabilityAdapter())
    response = client.get("/v1/haas/capabilities", headers=AUTH)
    assert response.status_code == 200
    harness = response.json()["data"]["harnesses"][0]
    assert harness["capabilities"]["streaming"]["status"] == "unsupported"
    assert harness["capabilities"]["streaming"]["mode"] == "none"
    assert harness["capabilities"]["cancellation"] == {
        "status": "available",
        "mode": "native",
        "enforcement": "hard",
    }
    assert harness["capabilities"]["approval"]["status"] == "unavailable"
