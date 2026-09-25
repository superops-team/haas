from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi.testclient import TestClient

from haas.api import DEFAULT_TOKEN, build_app
from haas.harnesses import FakeAdapter
from haas.harnesses.base import HarnessEvent, TurnHandle
from haas.identity import Principal
from haas.stores import SessionRecord, SQLiteStore

pytestmark = pytest.mark.adk


AUTH = {"Authorization": f"Bearer {DEFAULT_TOKEN}"}


def _create_session(client: TestClient, session_id: str = "hsess_policy_local") -> None:
    response = client.post(
        "/run",
        headers={**AUTH, "Idempotency-Key": f"create:{session_id}"},
        json={
            "appName": "chrn_codex_default",
            "userId": "anonymous",
            "sessionId": session_id,
            "newMessage": {"role": "user", "parts": [{"text": "hello"}]},
        },
    )
    assert response.status_code == 200, response.text


def test_fresh_session_materializes_default_policy_and_revision() -> None:
    app = build_app()
    client = TestClient(app)
    _create_session(client)

    session = app.state.runtime.store.get_session(
        ("chrn_codex_default", "anonymous", "hsess_policy_local")
    )
    assert session is not None
    assert session.desiredRevision == session.appliedRevision == 1
    assert session.policyStatus == "applied"
    assert session.desiredPolicy == session.appliedPolicy == {
        "workspace": {
            "mode": "workspace-write",
            "root": "/workspace",
            "writableRoots": ["/workspace"],
        },
        "network": {"defaultAction": "allow", "allow": []},
        "tools": {"disabled": [], "approvalMode": "on-request"},
    }


def test_first_run_materializes_explicit_manager_policy_as_revision_one() -> None:
    app = build_app()
    client = TestClient(app)
    response = client.post(
        "/run",
        headers={**AUTH, "Idempotency-Key": "explicit-initial-policy"},
        json={
            "appName": "chrn_codex_default",
            "userId": "anonymous",
            "sessionId": "hsess_explicit_policy",
            "newMessage": {"role": "user", "parts": [{"text": "hello"}]},
            "sandbox": {
                "mode": "workspace-write",
                "workspaceRoot": "/workspace/project",
                "writableRoots": ["/workspace/project"],
            },
            "policy": {
                "approvalPolicy": "never",
                "network": {"defaultAction": "deny", "allow": []},
            },
        },
    )
    assert response.status_code == 200
    session = app.state.runtime.store.get_session(
        ("chrn_codex_default", "anonymous", "hsess_explicit_policy")
    )
    assert session is not None
    assert session.appliedRevision == 1
    assert session.appliedPolicy["workspace"]["root"] == "/workspace/project"
    assert session.appliedPolicy["network"]["defaultAction"] == "deny"
    assert session.appliedPolicy["tools"]["approvalMode"] == "never"


def test_existing_session_run_cannot_bypass_applied_policy_with_request_overrides() -> None:
    class RecordingAdapter(FakeAdapter):
        def __init__(self) -> None:
            self.requests = []

        async def start_turn(self, request):
            self.requests.append(request)
            return await super().start_turn(request)

    adapter = RecordingAdapter()
    app = build_app(adapter=adapter)
    client = TestClient(app)
    _create_session(client, "hsess_policy_no_bypass")

    response = client.post(
        "/run",
        headers={**AUTH, "Idempotency-Key": "attempt-policy-bypass"},
        json={
            "appName": "chrn_codex_default",
            "userId": "anonymous",
            "sessionId": "hsess_policy_no_bypass",
            "newMessage": {"role": "user", "parts": [{"text": "again"}]},
            "sandbox": {
                "mode": "danger-full-access",
                "workspaceRoot": "/",
                "writableRoots": ["/"],
            },
            "policy": {
                "approvalPolicy": "never",
                "network": {"defaultAction": "deny", "allow": []},
            },
        },
    )

    assert response.status_code == 200, response.text
    request = adapter.requests[-1]
    assert request.sandbox.model_dump(exclude_none=True) == {
        "mode": "workspace-write",
        "workspaceRoot": "/workspace",
        "writableRoots": ["/workspace"],
        "network": {"defaultAction": "allow", "allow": []},
    }
    assert request.policy["tools"]["approvalMode"] == "on-request"
    assert request.policy["approvalPolicy"] == "on-request"
    assert request.policy["network"]["defaultAction"] == "allow"


def test_local_policy_update_is_revisioned_idempotent_and_rejects_stale_revision() -> None:
    app = build_app()
    client = TestClient(app)
    _create_session(client)
    body = {
        "expectedRevision": 1,
        "policy": {
            "network": {"defaultAction": "deny", "allow": ["https://example.com"]}
        },
    }
    headers = {**AUTH, "Idempotency-Key": "local-policy-2"}

    first = client.post(
        "/v1/haas/sessions/hsess_policy_local/policy", json=body, headers=headers
    )
    assert first.status_code == 200, first.text
    data = first.json()["data"]
    assert data["desiredRevision"] == data["appliedRevision"] == 2
    assert data["status"] == "applied"
    assert data["desiredPolicy"] == data["appliedPolicy"]
    assert data["appliedPolicy"]["network"] == body["policy"]["network"]

    replay = client.post(
        "/v1/haas/sessions/hsess_policy_local/policy", json=body, headers=headers
    )
    assert replay.status_code == 200
    assert replay.json() == first.json()

    stale = client.post(
        "/v1/haas/sessions/hsess_policy_local/policy",
        json={
            "expectedRevision": 1,
            "policy": {
                "tools": {"disabled": [], "approvalMode": "always"}
            },
        },
        headers={**AUTH, "Idempotency-Key": "local-policy-stale"},
    )
    assert stale.status_code == 409
    assert stale.json()["haasError"]["code"] == "haas_policy_revision_conflict"


def test_policy_idempotency_key_cannot_replay_across_principals() -> None:
    app = build_app(
        identity_tokens={
            "tok-a": Principal(principalId="p_a", userIds=frozenset({"u_1"})),
            "tok-b": Principal(principalId="p_b", userIds=frozenset({"u_2"})),
        }
    )
    client = TestClient(app)
    session = SessionRecord(
        id="hsess_private_policy", appName="chrn_codex_default", userId="u_1"
    )
    app.state.runtime.store.put_session(session)
    body = {
        "expectedRevision": 1,
        "policy": {"tools": {"disabled": [], "approvalMode": "always"}},
    }
    key = "shared-client-generated-key"
    updated = client.post(
        "/v1/haas/sessions/hsess_private_policy/policy",
        json=body,
        headers={"Authorization": "Bearer tok-a", "Idempotency-Key": key},
    )
    assert updated.status_code == 200

    hidden = client.post(
        "/v1/haas/sessions/hsess_private_policy/policy",
        json=body,
        headers={"Authorization": "Bearer tok-b", "Idempotency-Key": key},
    )
    assert hidden.status_code == 404
    assert hidden.json()["haasError"]["code"] == "session_not_found"


def test_running_session_policy_update_stays_pending_and_gates_new_work() -> None:
    app = build_app()
    client = TestClient(app)
    _create_session(client)
    key = ("chrn_codex_default", "anonymous", "hsess_policy_local")
    session = app.state.runtime.store.get_session(key)
    assert session is not None
    session.controlState = "running"
    app.state.runtime.store.put_session(session)

    update = client.post(
        "/v1/haas/sessions/hsess_policy_local/policy",
        json={
            "expectedRevision": 1,
            "policy": {
                "workspace": {
                    "mode": "read-only",
                    "root": "/workspace",
                    "writableRoots": [],
                }
            },
        },
        headers={**AUTH, "Idempotency-Key": "local-policy-pending"},
    )
    assert update.status_code == 202, update.text
    data = update.json()["data"]
    assert data["desiredRevision"] == 2
    assert data["appliedRevision"] == 1
    assert data["status"] == "pending"
    assert data["desiredPolicy"]["workspace"]["mode"] == "read-only"
    assert data["appliedPolicy"]["workspace"]["mode"] == "workspace-write"

    blocked = client.post(
        "/run",
        headers={**AUTH, "Idempotency-Key": "blocked-local-policy"},
        json={
            "appName": "chrn_codex_default",
            "userId": "anonymous",
            "sessionId": "hsess_policy_local",
            "newMessage": {"role": "user", "parts": [{"text": "wait"}]},
        },
    )
    assert blocked.status_code == 409
    assert blocked.json()["haasError"]["safeReason"] == "configuration_update_pending"


def test_local_policy_revision_survives_sqlite_restart(tmp_path) -> None:
    path = tmp_path / "haas.db"
    store = SQLiteStore(path)
    app = build_app(store=store)
    client = TestClient(app)
    _create_session(client)
    response = client.post(
        "/v1/haas/sessions/hsess_policy_local/policy",
        json={
            "expectedRevision": 1,
            "policy": {
                "tools": {"disabled": ["shell"], "approvalMode": "on-request"}
            },
        },
        headers={**AUTH, "Idempotency-Key": "persist-local-policy"},
    )
    assert response.status_code == 200
    store.close()

    reopened = SQLiteStore(path)
    session = reopened.get_session(
        ("chrn_codex_default", "anonymous", "hsess_policy_local")
    )
    assert session is not None
    assert session.desiredRevision == session.appliedRevision == 2
    assert session.appliedPolicy["tools"]["disabled"] == ["shell"]
    reopened.close()


async def test_live_policy_update_preserves_frozen_invocation_and_applies_after_terminal() -> None:
    entered = asyncio.Event()
    release = asyncio.Event()

    class BlockingAdapter(FakeAdapter):
        async def stream_events(self, handle: TurnHandle) -> AsyncIterator[HarnessEvent]:
            entered.set()
            await release.wait()
            yield HarnessEvent(
                type="harness.turn.completed",
                invocationId=handle.invocationId,
                sessionId=handle.sessionId,
                turnId=handle.turnId,
                author=self.base,
                content={"role": "model", "parts": []},
                actions={"stateDelta": {"status": "completed"}},
            )

    app = build_app(adapter=BlockingAdapter())
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        run_task = asyncio.create_task(
            client.post(
                "/run",
                headers={**AUTH, "Idempotency-Key": "live-policy-run"},
                json={
                    "appName": "chrn_codex_default",
                    "userId": "anonymous",
                    "sessionId": "hsess_policy_live",
                    "newMessage": {"role": "user", "parts": [{"text": "wait"}]},
                },
            )
        )
        await asyncio.wait_for(entered.wait(), timeout=2)
        update = await client.post(
            "/v1/haas/sessions/hsess_policy_live/policy",
            headers={**AUTH, "Idempotency-Key": "live-policy-update"},
            json={
                "expectedRevision": 1,
                "policy": {
                    "tools": {"disabled": [], "approvalMode": "always"}
                },
            },
        )
        assert update.status_code == 202
        invocation = next(iter(app.state.runtime.store._invocations.values()))
        assert invocation.executionContext["policyRevision"] == 1
        assert invocation.executionContext["policy"]["approvalPolicy"] == "on-request"
        release.set()
        assert (await run_task).status_code == 200

    session = app.state.runtime.store.get_session(
        ("chrn_codex_default", "anonymous", "hsess_policy_live")
    )
    assert session is not None
    assert session.desiredRevision == session.appliedRevision == 2
    assert session.appliedPolicy["tools"]["approvalMode"] == "always"
    assert session.policyStatus == "applied"
