import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient

from haas.api import build_app

AUTH = {"Authorization": "Bearer dev-token"}


def activate(client, model):
    body = {
        "harnessId": "chrn_codex_default",
        "base": "codex",
        "provider": {
            "providerId": "openai",
            "name": "OpenAI",
            "model": model,
            "wireApi": "responses",
            "apiType": "responses",
            "credentialRef": "secret://test/model",
            "baseUrl": "https://provider.example/v1",
        },
    }
    profile = client.post("/v1/haas/profiles", json=body, headers=AUTH).json()["data"]
    assert client.post(f"/v1/haas/profiles/{profile['id']}/validate", headers=AUTH).json()["data"][
        "valid"
    ]
    assert (
        client.post(f"/v1/haas/profiles/{profile['id']}/activate", headers=AUTH).status_code == 200
    )
    return profile


def test_profile_snapshot_rebind_and_isolation():
    app = build_app()
    client = TestClient(app)
    first = activate(client, "model-a")
    body = {
        "appName": "chrn_codex_default",
        "userId": "u_test",
        "sessionId": "hsess_test",
        "newMessage": {"role": "user", "parts": [{"text": "hello"}]},
        "haas": {"profileId": first["id"], "profileVersion": first["version"]},
    }
    assert client.post("/run", json=body, headers=AUTH).status_code == 200
    session = app.state.runtime.store.get_session(
        (body["appName"], body["userId"], body["sessionId"])
    )
    assert session.effectiveProfile["provider"]["model"] == "model-a"
    recovery = client.get(
        "/v1/haas/sessions/hsess_test/profile", headers=AUTH
    )
    assert recovery.status_code == 200, recovery.text
    assert set(recovery.json()["data"]) == {
        "profileId",
        "profileVersion",
        "profileFingerprint",
        "harnessId",
        "base",
        "executionIntentFingerprint",
    }
    assert recovery.json()["data"]["profileId"] == first["id"]
    assert recovery.json()["data"]["executionIntentFingerprint"].startswith("sha256:")
    assert "credentialRef" not in recovery.text
    assert "provider.example" not in recovery.text
    second = activate(client, "model-b")
    assert session.effectiveProfile["provider"]["model"] == "model-a"
    rebound = client.post(
        "/v1/haas/sessions/hsess_test/profile-rebind",
        json={"profileId": second["id"], "expectedProfileVersion": 1},
        headers=AUTH,
    )
    assert rebound.status_code == 200, rebound.text
    assert set(rebound.json()["data"]) == {
        "profileId",
        "profileVersion",
        "profileFingerprint",
        "harnessId",
        "base",
        "resolvedAtMs",
    }
    assert isinstance(rebound.json()["data"]["resolvedAtMs"], int)
    assert (
        client.post(
            "/run",
            json={**body, "haas": {"profileId": second["id"], "profileVersion": 2}},
            headers=AUTH,
        ).status_code
        == 200
    )
    current = app.state.runtime.store.get_session(
        (body["appName"], body["userId"], body["sessionId"])
    )
    assert current.id == session.id
    assert current.effectiveProfile["provider"]["model"] == "model-b"
    assert (
        client.post(
            "/v1/haas/sessions/hsess_test/profile-rebind",
            json={"profileId": first["id"]},
            headers=AUTH,
        ).status_code
        == 409
    )
    public = client.get("/apps/chrn_codex_default/users/u_test/sessions/hsess_test", headers=AUTH)
    assert "credentialRef" not in public.text
    assert "baseUrl" not in public.text


@pytest.mark.asyncio
async def test_rebind_waits_replays_and_rejects_conflicting_key():
    app = build_app()
    sync = TestClient(app)
    first = activate(sync, "a")
    sync.post(
        "/run",
        headers=AUTH,
        json={
            "appName": "chrn_codex_default",
            "userId": "u_test",
            "sessionId": "hsess_wait",
            "newMessage": {"role": "user", "parts": [{"text": "hello"}]},
        },
    ).raise_for_status()
    second = activate(sync, "b")
    store = app.state.runtime.store
    key = ("chrn_codex_default", "u_test", "hsess_wait")
    lease = store.acquire_lease(key, "running")
    url = "/v1/haas/sessions/hsess_wait/profile-rebind"
    body = {"profileId": second["id"], "expectedProfileVersion": first["version"]}
    headers = {**AUTH, "Idempotency-Key": "rebind-test"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        pending = asyncio.create_task(client.post(url, json=body, headers=headers))
        await asyncio.sleep(0.1)
        assert not pending.done()
        assert store.get_session(key).effectiveProfile["profileId"] == first["id"]
        store.release_lease(key, "running", lease.token)
        result = await asyncio.wait_for(pending, 2)
        assert result.status_code == 200
        third = activate(sync, "c")
        assert (
            await client.post(url, json={"profileId": third["id"]}, headers=AUTH)
        ).status_code == 200
        replay = await client.post(url, json=body, headers=headers)
        assert replay.json() == result.json()
        assert store.get_session(key).effectiveProfile["profileId"] == third["id"]
        conflict = await client.post(url, json={"profileId": third["id"]}, headers=headers)
        assert conflict.status_code == 409
        assert conflict.json()["haasError"]["code"] == "haas_idempotency_conflict"
        for invalid in (
            {"profileId": third["id"], "expectedProfileVersion": True},
            {"profileId": third["id"], "extra": 1},
        ):
            assert (await client.post(url, json=invalid, headers=AUTH)).status_code == 400
