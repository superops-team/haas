"""Idempotency and retry compatibility contracts for HaaS API mutations."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from haas.api import build_app
from haas.identity import Principal
from haas.stores import ApprovalRecord, InputRequestRecord, SessionRecord

pytestmark = pytest.mark.adk

TOKEN = "idempotency-contract-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def _client() -> TestClient:
    return TestClient(
        build_app(
            identity_tokens={
                TOKEN: Principal(
                    principalId="p_idempotency",
                    tenantId="t_idempotency",
                    userIds=frozenset({"u_idempotency"}),
                )
            }
        )
    )


def _seed_session(client: TestClient, sid: str) -> None:
    client.app.state.runtime.store.put_session(
        SessionRecord(id=sid, appName="chrn_codex_default", userId="u_idempotency")
    )


def test_mutation_idempotency_conflict_does_not_replay_another_body() -> None:
    client = _client()
    first = client.post(
        "/v1/haas/harnesses",
        json={"base": "codex", "name": "first"},
        headers={**AUTH, "Idempotency-Key": "same-key"},
    )
    assert first.status_code == 200
    conflict = client.post(
        "/v1/haas/harnesses",
        json={"base": "codex", "name": "different"},
        headers={**AUTH, "Idempotency-Key": "same-key"},
    )
    assert conflict.status_code == 409
    assert conflict.json()["haasError"]["code"] == "haas_idempotency_conflict"


def test_harness_failure_releases_key_for_corrected_retry() -> None:
    client = _client()
    headers = {**AUTH, "Idempotency-Key": "repair-harness"}
    bad = client.post(
        "/v1/haas/harnesses",
        json={"base": "unsupported", "name": "bad"},
        headers=headers,
    )
    assert bad.status_code == 422
    good = client.post(
        "/v1/haas/harnesses",
        json={"base": "codex", "name": "good"},
        headers=headers,
    )
    assert good.status_code == 200


def test_profile_failure_releases_key_for_corrected_retry() -> None:
    client = _client()
    headers = {**AUTH, "Idempotency-Key": "repair-profile"}
    bad = client.post("/v1/haas/profiles", json={"harnessId": 7}, headers=headers)
    assert bad.status_code == 422
    good = client.post(
        "/v1/haas/profiles",
        json={
            "harnessId": "chrn_codex_default",
            "base": "codex",
            "provider": {
                "providerId": "openai",
                "name": "OpenAI",
                "model": "m1",
                "credentialRef": "secret://provider/idempotency",
                "wireApi": "responses",
            },
        },
        headers=headers,
    )
    assert good.status_code == 200


def test_policy_failure_releases_key_for_corrected_retry() -> None:
    client = _client()
    sid = "hsess_policy_retry"
    _seed_session(client, sid)
    headers = {**AUTH, "Idempotency-Key": "repair-policy"}
    bad = client.post(
        f"/v1/haas/sessions/{sid}/policy",
        json={"expectedRevision": 0, "policy": {}},
        headers=headers,
    )
    assert bad.status_code == 400
    good = client.post(
        f"/v1/haas/sessions/{sid}/policy",
        json={"expectedRevision": 1, "policy": {"network": {"defaultAction": "deny", "allow": []}}},
        headers=headers,
    )
    assert good.status_code == 200


def test_approval_resolution_is_idempotent_for_identical_decision() -> None:
    client = _client()
    sid = "hsess_approval_replay"
    _seed_session(client, sid)
    store = client.app.state.runtime.store
    store.put_approval(
        ApprovalRecord(
            id="appr_replay",
            sessionId=sid,
            invocationId="inv_replay",
            turnId="turn_replay",
        )
    )
    headers = {**AUTH, "Idempotency-Key": "approval-replay"}
    body = {"decision": "approved", "scope": "action"}
    first = client.post(
        f"/v1/haas/sessions/{sid}/approvals/appr_replay",
        json=body,
        headers=headers,
    )
    assert first.status_code == 200
    second = client.post(
        f"/v1/haas/sessions/{sid}/approvals/appr_replay",
        json=body,
        headers=headers,
    )
    assert second.status_code == 200
    assert second.json()["data"] == first.json()["data"]


def test_approval_conflicting_second_decision_is_not_replayed() -> None:
    client = _client()
    sid = "hsess_approval_conflict"
    _seed_session(client, sid)
    client.app.state.runtime.store.put_approval(
        ApprovalRecord(
            id="appr_conflict",
            sessionId=sid,
            invocationId="inv_conflict",
            turnId="turn_conflict",
        )
    )
    first = client.post(
        f"/v1/haas/sessions/{sid}/approvals/appr_conflict",
        json={"decision": "approved", "scope": "action"},
        headers={**AUTH, "Idempotency-Key": "approval-first"},
    )
    assert first.status_code == 200
    second = client.post(
        f"/v1/haas/sessions/{sid}/approvals/appr_conflict",
        json={"decision": "denied", "scope": "action"},
        headers={**AUTH, "Idempotency-Key": "approval-second"},
    )
    assert second.status_code == 409
    assert second.json()["haasError"]["code"] == "haas_approval_state_conflict"


def test_input_resolution_is_idempotent_and_conflicts_on_changed_answer() -> None:
    client = _client()
    sid = "hsess_input_replay"
    _seed_session(client, sid)
    client.app.state.runtime.store.put_input_request(
        InputRequestRecord(
            id="inreq_replay",
            sessionId=sid,
            invocationId="inv_input",
            turnId="turn_input",
            questions=[{"id": "q1", "question": "?", "secret": False}],
            nativeRequestId=1,
            adapterGeneration=1,
        )
    )
    body = {"answers": {"q1": {"values": ["yes"]}}}
    first = client.post(
        f"/v1/haas/sessions/{sid}/input-requests/inreq_replay",
        json=body,
        headers={**AUTH, "Idempotency-Key": "input-replay"},
    )
    assert first.status_code == 200
    replay = client.post(
        f"/v1/haas/sessions/{sid}/input-requests/inreq_replay",
        json=body,
        headers={**AUTH, "Idempotency-Key": "input-replay"},
    )
    assert replay.status_code == 200
    assert replay.json()["data"] == first.json()["data"]

    conflict = client.post(
        f"/v1/haas/sessions/{sid}/input-requests/inreq_replay",
        json={"answers": {"q1": {"values": ["no"]}}},
        headers={**AUTH, "Idempotency-Key": "input-conflict"},
    )
    assert conflict.status_code == 409
    assert conflict.json()["haasError"]["code"] == "haas_input_request_state_conflict"


def test_session_listing_cursor_and_visibility_contract() -> None:
    client = _client()
    for sid in ("hsess_page_a", "hsess_page_b", "hsess_page_c"):
        _seed_session(client, sid)
    first = client.get("/v1/haas/sessions?limit=2", headers=AUTH)
    assert first.status_code == 200
    data = first.json()
    assert len(data["data"]) == 2
    assert data["nextCursor"] is not None
    second = client.get(
        f"/v1/haas/sessions?limit=2&cursor={data['nextCursor']}",
        headers=AUTH,
    )
    assert second.status_code == 200
    assert {item["id"] for item in first.json()["data"]}.isdisjoint(
        {item["id"] for item in second.json()["data"]}
    )
    bad = client.get("/v1/haas/sessions?cursor=missing", headers=AUTH)
    assert bad.status_code == 400
    hidden_app = client.get("/v1/haas/sessions?app=chrn_hidden", headers=AUTH)
    assert hidden_app.status_code == 200
    assert hidden_app.json()["data"] == []
