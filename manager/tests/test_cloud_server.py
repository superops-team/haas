"""Sidecar loopback routes for disabled OpenHarness cloud-account flows."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from coworker.server import SessionManager, create_app


def _allow_managed_state(state: str = "s") -> None:
    from coworker import cloud

    cloud._pending_managed_states[state] = cloud._now()


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    manager = SessionManager(workspace=tmp_path)
    app = create_app(manager)
    with TestClient(app) as c:
        c.manager = manager
        yield c


def test_cloud_status_signed_out(client):
    body = client.get("/v1/cloud/status").json()
    assert body == {
        "signed_in": False,
        "account": "",
        "user_id": "",
        "telemetry_enabled": False,
        "disabled": True,
    }


def test_cloud_login_is_disabled_and_does_not_open_browser(client, monkeypatch):
    import webbrowser

    opened = []
    monkeypatch.setattr(webbrowser, "open", lambda url: opened.append(url))
    body = client.post("/v1/cloud/login").json()
    assert body["ok"] is False
    assert body["disabled"] is True
    assert "does not use a cloud account" in body["error"]
    assert opened == []


def test_cloud_logout_is_disabled_noop(client):
    body = client.post("/v1/cloud/logout").json()
    assert body["ok"] is True
    assert body["disabled"] is True
    assert body["signed_in"] is False


def test_cloud_telemetry_is_disabled(client):
    body = client.post("/v1/cloud/telemetry", json={"enabled": True}).json()
    assert body == {"ok": False, "telemetry_enabled": False, "disabled": True}


def test_connect_managed_disabled(client):
    body = client.post("/v1/connectors/notion/connect-managed").json()
    assert not body["ok"]
    assert body["disabled"] is True
    assert "disabled" in body["error"]


def test_oauth_callback_disabled_does_not_write_profile(client):
    _allow_managed_state()
    resp = client.post(
        "/oauth/callback",
        data={
            "provider": "google",
            "connector": "gmail",
            "connection_id": "conn_9",
            "access_token": "ya29.tok",
            "refresh_token": "1//r",
            "expires_in": "3599",
            "scope": "gmail.readonly",
            "account": "a@b.c",
            "app_state": "s",
        },
    )
    assert resp.status_code == 400
    assert "Cloud connector login disabled" in resp.text
    assert "Served locally by OpenHarness" in resp.text
    assert client.manager.secrets.get("gmail:account:a@b.c") is None
    assert client.manager.secrets.get("gmail:default") is None


def test_oauth_callback_error_shows_failure_page(client):
    _allow_managed_state()
    resp = client.post(
        "/oauth/callback",
        data={"connector": "gmail", "error": "access_denied", "app_state": "s"},
    )
    assert resp.status_code == 400
    assert "Cloud connector login disabled" in resp.text
    assert client.manager.secrets.get("gmail:default") is None


def test_oauth_callback_rejects_unmanaged_connector(client):
    # telegram is manual-only (github gained a managed path with the App relay)
    _allow_managed_state()
    resp = client.post(
        "/oauth/callback",
        data={"connector": "telegram", "access_token": "x", "app_state": "s"},
    )
    assert resp.status_code == 400
    assert client.manager.secrets.get("telegram:default") is None


def test_oauth_callback_rejects_unknown_and_replayed_state(client):
    form = {
        "provider": "google",
        "connector": "gmail",
        "access_token": "token",
        "account": "a@b.c",
        "app_state": "once",
    }
    assert client.post("/oauth/callback", data=form).status_code == 400
    assert client.manager.secrets.get("gmail:default") is None

    _allow_managed_state("once")
    assert client.post("/oauth/callback", data=form).status_code == 400
    assert client.post("/oauth/callback", data=form).status_code == 400


def test_auth_callback_is_disabled(client):
    resp = client.get("/auth/callback", params={"code": "c", "state": "forged"})
    assert resp.status_code == 400
    assert "Cloud login disabled" in resp.text


def test_disconnect_works_signed_out(client):
    # manual profile, no cloud session: disconnect must not require the cloud
    client.manager.secrets.put("gmail:default", {"type": "oauth", "access_token": "t"})
    body = client.post("/v1/connectors/gmail/disconnect").json()
    assert body["ok"]
    assert client.manager.secrets.get("gmail:default") is None


SALES_MANIFEST = """---
id: sales
name: Sales Coworker
icon: chart
tagline: t
family: knowledge
workspace: deliverable
tools: [files, search, todo]
description: d
---
You are the Sales Coworker."""


def test_cloud_gallery_install_is_disabled_without_fetching(client, monkeypatch):
    from coworker import cloud

    calls = []
    monkeypatch.setattr(cloud, "gallery_manifest", lambda *a: calls.append(a))
    body = client.post("/v1/personas/install", json={"gallery_slug": "sales"}).json()
    assert not body["ok"]
    assert body["disabled"] is True
    assert calls == []


def test_local_persona_install_runs_consent_flow(client, tmp_path):
    persona_dir = tmp_path / "persona"
    persona_dir.mkdir()
    (persona_dir / "sales.md").write_text(SALES_MANIFEST)
    body = client.post("/v1/personas/install", json={"dir": str(persona_dir)}).json()
    assert body["ok"], body
    assert body["consent"][0]["id"] == "sales"
    installed = {p["id"]: p for p in body["personas"]}
    # lands disabled + unsurfaced pending explicit user approval (trust model)
    assert installed["sales"]["enabled"] is False


def test_gallery_install_disabled_before_hash_fetch(client, monkeypatch):
    body = client.post("/v1/personas/install", json={"gallery_slug": "sales"}).json()
    assert not body["ok"]
    assert body["disabled"] is True


def test_gallery_install_requires_sign_in(client, monkeypatch):
    body = client.post("/v1/personas/install", json={"gallery_slug": "sales"}).json()
    assert not body["ok"]
    assert body["disabled"] is True
    assert "disabled" in body["error"]


def test_cloud_gallery_endpoint_signed_out(client):
    body = client.get("/v1/cloud/gallery").json()
    assert not body["ok"]
    assert body["disabled"] is True
    assert body["personas"] == []


def test_delete_persona_after_gallery_install(client, tmp_path):
    persona_dir = tmp_path / "persona"
    persona_dir.mkdir()
    (persona_dir / "sales.md").write_text(SALES_MANIFEST)
    assert client.post("/v1/personas/install", json={"dir": str(persona_dir)}).json()[
        "ok"
    ]
    body = client.delete("/v1/personas/sales").json()
    assert body["ok"]
    assert "sales" not in {p["id"] for p in body["personas"]}


def test_delete_persona_refuses_builtin_and_unknown(client):
    body = client.delete("/v1/personas/cowork").json()
    assert not body["ok"] and "built-in" in body["error"]
    body = client.delete("/v1/personas/ghost").json()
    assert not body["ok"] and "unknown" in body["error"]
