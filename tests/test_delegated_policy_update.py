from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any

import httpx
from fastapi.testclient import TestClient

from haas.api import DEFAULT_TOKEN, build_app
from haas.runtime import FakeDelegatedContainerRuntime
from haas.runtime.reconciler import reconcile_delegated_policy

AUTH = {"Authorization": f"Bearer {DEFAULT_TOKEN}"}


def _body() -> dict[str, object]:
    return {
        "managerSessionId": "mgr_policy",
        "haasSessionId": "hsess_policy",
        "haasUserId": "anonymous",
        "harnessId": "chrn_codex_default",
        "image": {
            "reference": "example.invalid/haas",
            "digest": "sha256:" + "a" * 64,
            "variant": "lite",
            "platform": "linux/arm64",
        },
        "profileRef": {
            "profileId": "hprof_default",
            "profileVersion": 1,
            "profileFingerprint": "sha256:profile-1",
        },
        "provider": {
            "providerId": "openai",
            "name": "openai",
            "model": "test-model",
            "credentialRef": "secret://test/provider",
            "wireApi": "responses",
        },
        "workspaceMode": "bind_mount",
        "mountManifest": {
            "version": 1,
            "primaryWorkspace": {
                "hostPathCanonical": "/tmp/haas-policy-project",
                "containerPath": "/workspace",
                "access": "rw",
            },
            "extraMounts": [],
        },
    }


def _create(client: TestClient) -> dict[str, object]:
    response = client.post("/v1/haas/delegated-sessions", json=_body(), headers=AUTH)
    assert response.status_code == 200, response.text
    return response.json()["data"]


def test_policy_update_accepts_profile_only_and_advances_applied_revision() -> None:
    client = TestClient(build_app())
    created = _create(client)
    assert created["profileRef"] == _body()["profileRef"]
    assert created["workspaceMode"] == "bind_mount"

    response = client.post(
        f"/v1/haas/delegated-sessions/{created['id']}/policy",
        headers={**AUTH, "Idempotency-Key": "policy-profile-2"},
        json={
            "profileRef": {
                "profileId": "hprof_second",
                "profileVersion": 2,
                "profileFingerprint": "sha256:profile-2",
            }
        },
    )

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["desiredRevision"] == 2
    assert data["appliedRevision"] == 2
    assert data["profileRef"]["profileVersion"] == 2
    assert data["pendingPolicyUpdate"] is None
    assert data["lastPolicyUpdateResult"]["status"] == "applied"

    replay = client.post(
        f"/v1/haas/delegated-sessions/{created['id']}/policy",
        headers={**AUTH, "Idempotency-Key": "policy-profile-2"},
        json={
            "profileRef": {
                "profileId": "hprof_second",
                "profileVersion": 2,
                "profileFingerprint": "sha256:profile-2",
            }
        },
    )
    assert replay.status_code == 200
    assert replay.json()["data"]["desiredRevision"] == 2

    stale = client.post(
        f"/v1/haas/delegated-sessions/{created['id']}/policy",
        headers={**AUTH, "Idempotency-Key": "policy-profile-stale"},
        json={
            "expectedRevision": 1,
            "profileRef": {
                "profileId": "hprof_third",
                "profileVersion": 3,
                "profileFingerprint": "sha256:profile-3",
            },
        },
    )
    assert stale.status_code == 409
    assert stale.json()["haasError"]["code"] == "haas_policy_revision_conflict"


def test_policy_update_while_running_returns_pending_and_preserves_runtime() -> None:
    app = build_app()
    client = TestClient(app)
    created = _create(client)
    record = app.state.runtime.store.get_delegated_session(created["id"])
    assert record is not None
    app.state.runtime.store.update_delegated_runtime(
        record.id,
        replace(record.runtime, status="running", containerId="container-running"),
    )

    response = client.post(
        f"/v1/haas/delegated-sessions/{record.id}/policy",
        headers={**AUTH, "Idempotency-Key": "policy-running-2"},
        json={
            "delegationPolicySnapshot": {
                **record.delegationPolicySnapshot,
                "idleTtlSeconds": 60,
            }
        },
    )

    assert response.status_code == 202, response.text
    data = response.json()["data"]
    assert data["desiredRevision"] == 2
    assert data["appliedRevision"] == 1
    assert data["pendingPolicyUpdate"]["revision"] == 2
    assert data["runtime"]["containerId"] == "container-running"
    assert data["delegationPolicySnapshot"]["idleTtlSeconds"] == 1800
    pending_record = app.state.runtime.store.get_delegated_session(record.id)
    assert pending_record is not None
    assert pending_record.pendingPolicyTarget is not None
    assert pending_record.pendingPolicyTarget["delegationPolicySnapshot"]["idleTtlSeconds"] == 60

    blocked_run = client.post(
        "/run",
        headers={**AUTH, "Idempotency-Key": "blocked-before-applied"},
        json={
            "appName": "chrn_codex_default",
            "userId": "anonymous",
            "sessionId": "hsess_policy",
            "newMessage": {"role": "user", "parts": [{"text": "wait"}]},
        },
    )
    assert blocked_run.status_code == 409
    assert blocked_run.json()["haasError"]["code"] == "session_busy"

    app.state.runtime.store.update_delegated_runtime(
        record.id,
        replace(record.runtime, status="idle", containerId="container-running"),
    )
    reconciled = reconcile_delegated_policy(app.state.runtime.store, record.id)

    assert reconciled is not None
    assert reconciled.desiredRevision == 2
    assert reconciled.appliedRevision == 2
    assert reconciled.pendingPolicyUpdate is None
    assert reconciled.lastPolicyUpdateResult is not None
    assert reconciled.lastPolicyUpdateResult["status"] == "applied"
    assert reconciled.delegationPolicySnapshot["idleTtlSeconds"] == 60
    assert reconciled.runtime.containerId == "container-running"


def test_policy_update_failure_preserves_applied_config_and_restart_can_reconcile() -> None:
    app = build_app()
    client = TestClient(app)
    created = _create(client)
    record = app.state.runtime.store.get_delegated_session(created["id"])
    assert record is not None
    app.state.runtime.store.update_delegated_runtime(
        record.id, replace(record.runtime, status="running", containerId="running")
    )
    response = client.post(
        f"/v1/haas/delegated-sessions/{record.id}/policy",
        headers={**AUTH, "Idempotency-Key": "policy-failure-2"},
        json={"image": {**record.image, "reference": "example.invalid/new-haas"}},
    )
    assert response.status_code == 202
    app.state.runtime.store.update_delegated_runtime(
        record.id, replace(record.runtime, status="idle", containerId="running")
    )

    failed = reconcile_delegated_policy(
        app.state.runtime.store,
        record.id,
        apply=lambda current, target: (_ for _ in ()).throw(RuntimeError("materialize")),
    )
    assert failed is not None
    assert failed.appliedRevision == 1
    assert failed.desiredRevision == 2
    assert failed.image["reference"] == record.image["reference"]
    assert failed.lastPolicyUpdateResult == {
        "updateId": response.json()["data"]["pendingPolicyUpdate"]["updateId"],
        "revision": 2,
        "status": "failed",
        "code": "haas_internal_error",
        "safeReason": "policy_apply_failed",
    }

    corrected = client.post(
        f"/v1/haas/delegated-sessions/{record.id}/policy",
        headers={**AUTH, "Idempotency-Key": "policy-corrected-3"},
        json={
            "delegationPolicySnapshot": {**record.delegationPolicySnapshot, "idleTtlSeconds": 90}
        },
    )
    assert corrected.status_code == 200
    assert corrected.json()["data"]["desiredRevision"] == 3
    assert corrected.json()["data"]["appliedRevision"] == 3


async def test_running_invocation_auto_reconciles_pending_policy_and_emits_events() -> None:
    entered = asyncio.Event()
    release = asyncio.Event()

    class BlockingRuntime(FakeDelegatedContainerRuntime):
        async def run_stream(self, session: Any, body: dict[str, object]) -> Any:
            entered.set()
            await release.wait()
            async for event in super().run_stream(session, body):
                yield event

    app = build_app(delegated_containers=BlockingRuntime())
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        created_response = await client.post(
            "/v1/haas/delegated-sessions", json=_body(), headers=AUTH
        )
        created = created_response.json()["data"]
        run_task = asyncio.create_task(
            client.post(
                "/run",
                headers={**AUTH, "Idempotency-Key": "running-turn"},
                json={
                    "appName": "chrn_codex_default",
                    "userId": "anonymous",
                    "sessionId": "hsess_policy",
                    "newMessage": {"role": "user", "parts": [{"text": "wait"}]},
                },
            )
        )
        await asyncio.wait_for(entered.wait(), timeout=2)
        update = await client.post(
            f"/v1/haas/delegated-sessions/{created['id']}/policy",
            headers={**AUTH, "Idempotency-Key": "during-running"},
            json={
                "delegationPolicySnapshot": {
                    **_body().get("delegationPolicySnapshot", {}),
                    "version": 1,
                    "idleTtlSeconds": 60,
                    "maxContainerLifetimeSeconds": 28800,
                    "rwWorkspaceConcurrency": "single_writer",
                    "queuePolicy": "fifo",
                    "restorePolicy": "fail_closed",
                    "mountPolicy": "project_rw_extra_ro",
                }
            },
        )
        assert update.status_code == 202
        assert update.json()["data"]["appliedRevision"] == 1
        release.set()
        assert (await run_task).status_code == 200

        applied = app.state.runtime.store.get_delegated_session(created["id"])
        assert applied is not None
        assert applied.runtime.status == "idle"
        assert applied.appliedRevision == applied.desiredRevision == 2
        events = app.state.runtime.event_log.read_session(
            "chrn_codex_default", "anonymous", "hsess_policy"
        )
        policy_types = [event.type for event in events if "policy_update" in event.type]
        assert policy_types == [
            "haas.delegation.policy_update_pending",
            "haas.delegation.policy_update_applied",
        ]


def test_pending_policy_target_survives_sqlite_restart(tmp_path) -> None:
    from haas.stores import SQLiteStore

    store = SQLiteStore(tmp_path / "haas.db")
    # Reuse an API-produced record so the persisted form matches production.
    app = build_app(store=store)
    client = TestClient(app)
    created = _create(client)
    current = store.get_delegated_session(created["id"])
    assert current is not None
    store.update_delegated_runtime(
        current.id, replace(current.runtime, status="running", containerId="running")
    )
    response = client.post(
        f"/v1/haas/delegated-sessions/{current.id}/policy",
        headers={**AUTH, "Idempotency-Key": "policy-restart-2"},
        json={
            "profileRef": {
                "profileId": "hprof_next",
                "profileVersion": 2,
                "profileFingerprint": "sha256:next",
            }
        },
    )
    assert response.status_code == 202
    store.close()

    reopened = SQLiteStore(tmp_path / "haas.db")
    pending = reopened.get_delegated_session(current.id)
    assert pending is not None and pending.pendingPolicyTarget is not None
    reopened.update_delegated_runtime(current.id, replace(pending.runtime, status="idle"))
    applied = reconcile_delegated_policy(reopened, current.id)
    assert applied is not None
    assert applied.appliedRevision == 2
    assert applied.profileRef["profileId"] == "hprof_next"
    reopened.close()


def test_policy_update_rejects_reason_only_payload() -> None:
    client = TestClient(build_app())
    created = _create(client)

    response = client.post(
        f"/v1/haas/delegated-sessions/{created['id']}/policy",
        headers={**AUTH, "Idempotency-Key": "policy-empty"},
        json={"reason": "no change"},
    )

    assert response.status_code == 400
    assert response.json()["haasError"]["code"] == "invalid_input"


def test_delegated_profile_rebind_remains_rejected() -> None:
    app = build_app()
    client = TestClient(app)
    created = _create(client)
    # The delegated mapping is authoritative even before/without a native
    # SessionRecord, as can happen around prepared binding recovery.
    app.state.runtime.store._sessions.clear()

    response = client.post(
        f"/v1/haas/sessions/{created['haasSessionId']}/profile-rebind",
        headers={**AUTH, "Idempotency-Key": "forbidden-rebind"},
        json={"profileId": "hprof_second"},
    )

    assert response.status_code == 409
    assert response.json()["haasError"]["code"] == "haas_profile_rebind_unsupported"
    recovery = client.get(
        f"/v1/haas/sessions/{created['haasSessionId']}/profile", headers=AUTH
    )
    assert recovery.status_code == 409
    assert recovery.json()["haasError"]["code"] == "haas_profile_rebind_unsupported"
