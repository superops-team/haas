"""API boundary and recovery coverage for public HaaS routes."""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from haas.api import build_app
from haas.harnesses import FakeAdapter
from haas.harnesses.base import AdapterProbe
from haas.identity import Principal
from haas.stores import MemoryStore

TOKEN_A = "api-coverage-a"
TOKEN_B = "api-coverage-b"
AUTH_A = {"Authorization": f"Bearer {TOKEN_A}"}
AUTH_B = {"Authorization": f"Bearer {TOKEN_B}"}


def _app(**kwargs: Any) -> Any:
    return build_app(
        adapter=kwargs.pop("adapter", FakeAdapter()),
        identity_tokens={
            TOKEN_A: Principal(
                principalId="principal-a", tenantId="tenant-a", userIds=frozenset({"user-a"})
            ),
            TOKEN_B: Principal(
                principalId="principal-b", tenantId="tenant-b", userIds=frozenset({"user-b"})
            ),
        },
        **kwargs,
    )


def _run_body(session_id: str = "hsess-api-coverage") -> dict[str, Any]:
    return {
        "appName": "chrn_codex_default",
        "userId": "user-a",
        "sessionId": session_id,
        "newMessage": {"role": "user", "parts": [{"text": "hello"}]},
    }


def _delegated_body() -> dict[str, Any]:
    return {
        "managerSessionId": "manager-api-coverage",
        "haasSessionId": "hsess-delegated-api-coverage",
        "haasUserId": "user-a",
        "harnessId": "chrn_codex_default",
        "harnessBase": "codex",
        "image": {
            "reference": "registry.invalid/haas",
            "digest": "sha256:" + "a" * 64,
            "variant": "lite",
            "platform": "linux/arm64",
        },
        "profileRef": {
            "profileId": "hprof-api-coverage",
            "profileVersion": 1,
            "profileFingerprint": "sha256:profile-one",
        },
        "provider": {
            "providerId": "openai",
            "name": "OpenAI",
            "model": "test-model",
            "credentialRef": "secret://provider/default",
            "wireApi": "responses",
        },
        "workspaceMode": "bind_mount",
        "mountManifest": {
            "version": 1,
            "primaryWorkspace": {
                "hostPathCanonical": "/tmp/haas-api-coverage",
                "containerPath": "/workspace",
                "access": "rw",
            },
            "extraMounts": [],
        },
    }


def _create_delegated(client: TestClient) -> dict[str, Any]:
    response = client.post("/v1/haas/delegated-sessions", json=_delegated_body(), headers=AUTH_A)
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _profile_body(**updates: Any) -> dict[str, Any]:
    body = {
        "harnessId": "chrn_codex_default",
        "base": "codex",
        "provider": {
            "providerId": "openai",
            "name": "OpenAI",
            "model": "test-model",
            "credentialRef": "secret://provider/default",
            "wireApi": "responses",
        },
    }
    body.update(updates)
    return body


def test_execution_expired_key_is_410_and_does_not_start_another_turn() -> None:
    now = [1_000]
    store = MemoryStore(clock_ms=lambda: now[0], idempotency_ttl_ms=10)
    app = _app(store=store)
    client = TestClient(app)
    headers = {**AUTH_A, "Idempotency-Key": "expires-after-acceptance"}

    first = client.post("/run", json=_run_body(), headers=headers)
    assert first.status_code == 200
    assert first.headers["Idempotency-Expires-At"] == "1010"
    first_invocation = first.json()[0]["invocationId"]

    now[0] = 1_010
    expired = client.post("/run", json=_run_body(), headers=headers)
    assert expired.status_code == 410
    assert expired.json()["haasError"]["code"] == "haas_idempotency_expired"
    assert store.get_invocation(first_invocation) is not None
    assert len(store._invocations) == 1


def test_execution_idempotency_key_is_scoped_to_principal() -> None:
    client = TestClient(_app())
    key = {"Idempotency-Key": "shared-execution-key"}
    first = client.post(
        "/run", json=_run_body("hsess-principal-a"), headers={**AUTH_A, **key}
    )
    second_body = {
        **_run_body("hsess-principal-b"),
        "appName": "chrn_codex_default_1",
        "userId": "user-b",
    }
    second = client.post("/run", json=second_body, headers={**AUTH_B, **key})

    assert first.status_code == second.status_code == 200
    assert first.json()[0]["invocationId"] != second.json()[0]["invocationId"]


def test_sse_idempotency_replay_keeps_expiry_header_and_event_sequence() -> None:
    client = TestClient(_app())
    headers = {**AUTH_A, "Idempotency-Key": "sse-replay-key"}
    with client.stream(
        "POST", "/run_sse", json=_run_body("hsess-sse-replay"), headers=headers
    ) as first:
        first_payloads = [line for line in first.iter_lines() if line.startswith("data: ")]
        first_expiry = first.headers["Idempotency-Expires-At"]
    with client.stream(
        "POST", "/run_sse", json=_run_body("hsess-sse-replay"), headers=headers
    ) as replay:
        replay_payloads = [line for line in replay.iter_lines() if line.startswith("data: ")]
        assert replay.headers["Idempotency-Expires-At"] == first_expiry

    assert replay_payloads == first_payloads


def test_native_event_routes_project_canonical_events_and_reject_expired_cursor() -> None:
    client = TestClient(_app())
    run = client.post("/run", json=_run_body("hsess-native-routes"), headers=AUTH_A)
    assert run.status_code == 200
    invocation_id = run.json()[0]["invocationId"]

    with client.stream(
        "GET",
        f"/v1/haas/sessions/hsess-native-routes/invocations/{invocation_id}/events",
        headers=AUTH_A,
    ) as response:
        payloads = [
            json.loads(line[6:]) for line in response.iter_lines() if line.startswith("data: ")
        ]
    assert [event["sequenceNumber"] for event in payloads] == [0, 1, 2]
    assert payloads[-1]["type"] == "haas.turn.completed"
    assert "adapterId" not in payloads[0]
    assert "userId" not in payloads[0]

    expired = client.get(
        "/v1/haas/sessions/hsess-native-routes/events",
        params={"after_event_id": "evt-no-longer-retained"},
        headers=AUTH_A,
    )
    assert expired.status_code == 410
    assert expired.json()["haasError"]["code"] == "haas_offset_expired"


def test_native_events_page_is_bounded_and_cursor_driven() -> None:
    client = TestClient(_app())
    run = client.post("/run", json=_run_body("hsess-events-page"), headers=AUTH_A)
    events = run.json()
    first = client.get(
        "/v1/haas/sessions/hsess-events-page/events-page",
        params={"limit": 1},
        headers=AUTH_A,
    )
    assert first.status_code == 200
    assert [event["eventId"] for event in first.json()["data"]] == [events[0]["id"]]
    assert first.json()["nextCursor"] == events[0]["id"]

    remainder = client.get(
        "/v1/haas/sessions/hsess-events-page/events-page",
        params={"after_event_id": first.json()["nextCursor"], "limit": 10},
        headers=AUTH_A,
    )
    assert [event["eventId"] for event in remainder.json()["data"]] == [
        event["id"] for event in events[1:]
    ]
    assert remainder.json()["nextCursor"] is None
    assert (
        client.get(
            "/v1/haas/sessions/hsess-events-page/events-page",
            params={"limit": 1001},
            headers=AUTH_A,
        ).status_code
        == 400
    )
    assert (
        client.get(
            "/v1/haas/sessions/hsess-events-page/events-page",
            params={"after_event_id": "expired"},
            headers=AUTH_A,
        ).status_code
        == 410
    )


def test_run_sse_last_event_id_replays_without_creating_invocation() -> None:
    app = _app()
    client = TestClient(app)
    body = _run_body("hsess-last-event-id")
    original = client.post("/run", json=body, headers=AUTH_A).json()
    invocation_count = len(app.state.runtime.store._invocations)

    with client.stream(
        "POST",
        "/run_sse",
        json=body,
        headers={**AUTH_A, "Last-Event-ID": original[0]["id"]},
    ) as response:
        replay = [
            json.loads(line[6:]) for line in response.iter_lines() if line.startswith("data: ")
        ]
    assert [event["id"] for event in replay] == [event["id"] for event in original[1:]]
    assert len(app.state.runtime.store._invocations) == invocation_count

    expired = client.post("/run_sse", json=body, headers={**AUTH_A, "Last-Event-ID": "expired"})
    assert expired.status_code == 410


def test_native_invocation_and_rebind_visibility_fail_closed() -> None:
    client = TestClient(_app())
    run = client.post("/run", json=_run_body("hsess-visible"), headers=AUTH_A)
    invocation_id = run.json()[0]["invocationId"]

    wrong_session = client.get(
        f"/v1/haas/sessions/wrong/invocations/{invocation_id}", headers=AUTH_A
    )
    cross_tenant = client.get(
        f"/v1/haas/sessions/hsess-visible/invocations/{invocation_id}", headers=AUTH_B
    )
    unknown_rebind = client.post(
        "/v1/haas/sessions/unknown/profile-rebind", json={}, headers=AUTH_A
    )
    cross_tenant_profile = client.get(
        "/v1/haas/sessions/hsess-visible/profile", headers=AUTH_B
    )
    unknown_profile = client.get("/v1/haas/sessions/unknown/profile", headers=AUTH_A)
    assert wrong_session.status_code == 404
    assert cross_tenant.status_code == 404
    assert unknown_rebind.status_code == 404
    assert cross_tenant_profile.status_code == 404
    assert cross_tenant_profile.json()["haasError"]["code"] == "session_not_found"
    assert unknown_profile.status_code == 404
    assert unknown_profile.json()["haasError"]["code"] == "session_not_found"


def test_delegated_profile_recovery_read_hides_cross_tenant_existence() -> None:
    client = TestClient(_app())
    delegated = _create_delegated(client)

    response = client.get(
        f"/v1/haas/sessions/{delegated['haasSessionId']}/profile", headers=AUTH_B
    )

    assert response.status_code == 404
    assert response.json()["haasError"]["code"] == "session_not_found"


@pytest.mark.parametrize(
    ("path", "method", "expected_code"),
    [
        ("/v1/haas/profiles?limit=0", "get", "invalid_input"),
        ("/v1/haas/profiles?cursor=unsupported", "get", "invalid_input"),
        ("/v1/haas/profiles?status=unknown", "get", "invalid_input"),
        ("/v1/haas/profiles/missing", "get", "haas_profile_not_found"),
        ("/v1/haas/profiles/missing/validate", "post", "haas_profile_not_found"),
        ("/v1/haas/profiles/missing/activate", "post", "haas_profile_not_found"),
    ],
)
def test_profile_route_errors_are_structured(path: str, method: str, expected_code: str) -> None:
    response = getattr(TestClient(_app()), method)(path, headers=AUTH_A)
    assert response.status_code in {400, 404}
    assert response.json()["haasError"]["code"] == expected_code


def test_profile_create_and_update_validation_errors_release_idempotency() -> None:
    client = TestClient(_app())
    headers = {**AUTH_A, "Idempotency-Key": "profile-create-released"}
    invalid = client.post("/v1/haas/profiles", json={"unknown": True}, headers=headers)
    assert invalid.status_code == 422

    created = client.post("/v1/haas/profiles", json=_profile_body(), headers=headers)
    assert created.status_code == 200, created.text
    profile_id = created.json()["data"]["id"]
    malformed = client.put(f"/v1/haas/profiles/{profile_id}", json={"harnessId": 3}, headers=AUTH_A)
    assert malformed.status_code == 422
    assert malformed.json()["haasError"]["code"] == "invalid_input"


@pytest.mark.parametrize(
    "update",
    [
        {"delegationPolicySnapshot": {"idleTtlSeconds": 30}},
        {
            "mountManifest": {
                "version": 1,
                "primaryWorkspace": {
                    "hostPathCanonical": "/tmp/haas-api-coverage-next",
                    "containerPath": "/workspace",
                    "access": "rw",
                },
                "extraMounts": [],
            }
        },
        {
            "image": {
                "reference": "registry.invalid/haas-next",
                "digest": "sha256:" + "b" * 64,
                "variant": "aio",
                "platform": "linux/amd64",
            }
        },
    ],
)
def test_delegated_policy_accepts_each_runtime_domain(update: dict[str, Any]) -> None:
    app = _app()
    client = TestClient(app)
    created = _create_delegated(client)
    if "delegationPolicySnapshot" in update:
        record = app.state.runtime.store.get_delegated_session(created["id"])
        assert record is not None
        update["delegationPolicySnapshot"] = {
            **record.delegationPolicySnapshot,
            **update["delegationPolicySnapshot"],
        }

    response = client.post(
        f"/v1/haas/delegated-sessions/{created['id']}/policy", json=update, headers=AUTH_A
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["desiredRevision"] == 2


@pytest.mark.parametrize(
    ("update", "status", "code"),
    [
        ({"profileRef": []}, 400, "invalid_input"),
        ({"delegationPolicySnapshot": []}, 400, "invalid_input"),
        ({"image": {"reference": "mutable:latest", "digest": ""}}, 400, "invalid_input"),
        (
            {
                "mountManifest": {
                    "version": 1,
                    "primaryWorkspace": {
                        "hostPathCanonical": "/tmp/haas-api-coverage",
                        "containerPath": "/workspace",
                        "access": "ro",
                    },
                }
            },
            403,
            "haas_delegation_mount_invalid",
        ),
    ],
)
def test_delegated_policy_rejects_invalid_domains_and_releases_key(
    update: dict[str, Any], status: int, code: str
) -> None:
    client = TestClient(_app())
    created = _create_delegated(client)
    headers = {**AUTH_A, "Idempotency-Key": "policy-validation-release"}
    rejected = client.post(
        f"/v1/haas/delegated-sessions/{created['id']}/policy", json=update, headers=headers
    )
    assert rejected.status_code == status
    assert rejected.json()["haasError"]["code"] == code

    accepted = client.post(
        f"/v1/haas/delegated-sessions/{created['id']}/policy",
        json={"profileRef": _delegated_body()["profileRef"]},
        headers=headers,
    )
    assert accepted.status_code == 200, accepted.text


def test_delegated_policy_idempotency_conflict_pending_replay_and_access_errors() -> None:
    app = _app()
    client = TestClient(app)
    created = _create_delegated(client)
    record = app.state.runtime.store.get_delegated_session(created["id"])
    assert record is not None
    app.state.runtime.store.update_delegated_runtime(
        record.id, replace(record.runtime, status="running", containerId="container-running")
    )
    headers = {**AUTH_A, "Idempotency-Key": "pending-policy-replay"}
    update = {"profileRef": _delegated_body()["profileRef"]}

    first = client.post(
        f"/v1/haas/delegated-sessions/{created['id']}/policy", json=update, headers=headers
    )
    replay = client.post(
        f"/v1/haas/delegated-sessions/{created['id']}/policy", json=update, headers=headers
    )
    conflict = client.post(
        f"/v1/haas/delegated-sessions/{created['id']}/policy",
        json={"profileRef": {**update["profileRef"], "profileVersion": 2}},
        headers=headers,
    )
    hidden = client.post(
        f"/v1/haas/delegated-sessions/{created['id']}/policy",
        json=update,
        headers={**AUTH_B, "Idempotency-Key": "cross-tenant-policy"},
    )
    missing = client.post(
        "/v1/haas/delegated-sessions/missing/policy",
        json=update,
        headers={**AUTH_A, "Idempotency-Key": "missing-policy"},
    )

    assert first.status_code == replay.status_code == 202
    assert replay.json() == first.json()
    assert conflict.status_code == 409
    assert conflict.json()["haasError"]["code"] == "haas_idempotency_conflict"
    assert hidden.status_code == missing.status_code == 404


class _ReadinessProbeAdapter(FakeAdapter):
    def __init__(self, *, raises: bool = False) -> None:
        self.raises = raises
        self.calls = 0

    async def probe_readiness(self) -> AdapterProbe:
        self.calls += 1
        if self.raises:
            raise RuntimeError("safe probe failure")
        return AdapterProbe(
            adapterId=self.adapter_id,
            base=self.base,
            status="not_ready",
            runtimeVersion=self.version,
            transport="in_process",
            safeDetails={"safeReason": "runtime_config_missing"},
        )


def test_readiness_uses_specialized_probe_cache_and_safe_reason() -> None:
    adapter = _ReadinessProbeAdapter()
    client = TestClient(_app(adapter=adapter))
    for path in ("/ready?scope=execution", "/v1/haas/status"):
        response = client.get(path, headers=AUTH_A)
        assert response.status_code == (503 if path.startswith("/ready") else 200)
    assert adapter.calls == 1
    assert client.get("/v1/haas/ready?scope=unknown").json()["data"]["reason"] == "unknown_scope"


def test_readiness_probe_exception_is_reported_as_safe_unavailability() -> None:
    client = TestClient(_app(adapter=_ReadinessProbeAdapter(raises=True)))
    response = client.get("/v1/haas/ready?scope=execution")
    assert response.status_code == 503
    # P0-1 secretless boundary: raw str(exc) ("safe probe failure") must not
    # leak northbound; only the fixed safe code is surfaced.
    assert response.json()["haasError"]["safeReason"] == "adapter_probe_failed"
    assert "safe probe failure" not in response.text
