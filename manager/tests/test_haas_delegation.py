"""Manager -> HaaS delegation bridge tests."""

from __future__ import annotations

import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
from coworker.delegation import (
    HaasDelegationClient,
    HaasDelegationConfig,
    HaasDelegationError,
    LocalHaasSupervisor,
    adk_message_from_content,
    deterministic_decision,
    is_text_only_content,
    make_delegated_session_body,
)
from coworker.providers import AssistantTurn, ModelCapabilities, ProviderClient
from coworker.server import SessionManager, create_app
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


class ScriptedProvider(ProviderClient):
    def __init__(self) -> None:
        self.calls = 0

    def complete(self, *, model, messages, tools=None, **settings):
        self.calls += 1
        return AssistantTurn(text="local")

    def capabilities(self, model):
        return ModelCapabilities()


class FakeHaasClient:
    def __init__(self, config: HaasDelegationConfig) -> None:
        self.config = config
        self.created: list[dict[str, Any]] = []
        self.restored: list[str] = []
        self.runs: list[dict[str, Any]] = []

    async def create_delegated_session(self, body: dict[str, Any]) -> dict[str, Any]:
        self.created.append(body)
        return {
            "id": "dgsess_1",
            "managerSessionId": body["managerSessionId"],
            "haasSessionId": body["haasSessionId"],
            "haasUserId": body["haasUserId"],
            "harnessId": body["harnessId"],
            "harnessBase": body["harnessBase"],
            "image": body["image"],
            "provider": body["provider"],
            "mountManifest": body["mountManifest"],
            "delegationPolicySnapshot": body["delegationPolicySnapshot"],
            "runtime": {"status": "no_runtime", "containerGeneration": 0},
        }

    async def restore(self, delegated_session_id: str) -> dict[str, Any]:
        self.restored.append(delegated_session_id)
        return {
            "id": delegated_session_id,
            "runtime": {"status": "running", "containerGeneration": len(self.restored)},
        }

    async def run_sse(
        self,
        *,
        haas_session_id: str,
        message: dict[str, Any],
        harness_id: str | None = None,
        user_id: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        self.runs.append(
            {
                "haas_session_id": haas_session_id,
                "harness_id": harness_id,
                "user_id": user_id,
                "message": message,
            }
        )
        yield {"content": {"role": "model", "parts": [{"text": "remote"}]}, "actions": {}}
        yield {
            "content": {"role": "model", "parts": [{"text": " done"}]},
            "actions": {"stateDelta": {"status": "completed"}},
        }


class FailingHaasClient(FakeHaasClient):
    async def restore(self, delegated_session_id: str) -> dict[str, Any]:
        raise HaasDelegationError("backend unavailable")


class FailedTurnHaasClient(FakeHaasClient):
    async def run_sse(
        self,
        *,
        haas_session_id: str,
        message: dict[str, Any],
        harness_id: str | None = None,
        user_id: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        self.runs.append(
            {
                "haas_session_id": haas_session_id,
                "harness_id": harness_id,
                "user_id": user_id,
                "message": message,
            }
        )
        yield {
            "content": {"role": "model", "parts": []},
            "actions": {"stateDelta": {"status": "failed"}},
        }


class FakeLocalHaasSupervisor:
    def __init__(self) -> None:
        self.ensured: list[HaasDelegationConfig] = []
        self.stopped = 0

    def ensure(self, config: HaasDelegationConfig) -> dict[str, Any]:
        self.ensured.append(config)
        return {
            "enabled": config.local_autostart,
            "status": "running" if config.local_autostart and config.enabled else "disabled",
            "running": config.local_autostart and config.enabled,
            "managed": config.local_autostart and config.enabled,
            "pid": 123 if config.local_autostart and config.enabled else None,
            "url": config.base_url,
            "reason": None,
        }

    def status(self, config: HaasDelegationConfig) -> dict[str, Any]:
        return {
            "enabled": config.local_autostart,
            "status": "running" if config.local_autostart and config.enabled else "disabled",
            "running": config.local_autostart and config.enabled,
            "managed": config.local_autostart and config.enabled,
            "pid": 123 if config.local_autostart and config.enabled else None,
            "url": config.base_url,
            "reason": None,
        }

    def stop(self) -> None:
        self.stopped += 1


def _haas_config() -> HaasDelegationConfig:
    return HaasDelegationConfig(
        enabled=True,
        image_digest="sha256:test",
        trigger_keywords=["fix"],
    )


def test_deterministic_delegation_requires_trusted_workspace(tmp_path):
    cfg = _haas_config()
    assert deterministic_decision(
        config=cfg,
        session_id="s1",
        agent="code",
        workspace=str(tmp_path),
        workspace_trusted=False,
        content="fix this",
    ).backend == "local"
    assert deterministic_decision(
        config=cfg,
        session_id="s1",
        agent="code",
        workspace=str(tmp_path),
        workspace_trusted=True,
        content="fix this",
    ).use_haas
    assert deterministic_decision(
        config=cfg,
        session_id="s1",
        agent="code",
        workspace=str(tmp_path),
        workspace_trusted=True,
        content="hello",
    ).backend == "local"


def test_make_delegated_session_body_mounts_workspace_rw(tmp_path):
    body = make_delegated_session_body(
        config=_haas_config(),
        manager_session_id="s1",
        workspace=str(tmp_path),
        model="volcengine-ark:doubao-seed-2.1-turbo",
        extra_roots=[{"path": str(tmp_path), "writable": True}],
    )
    assert body["mountManifest"]["primaryWorkspace"] == {
        "hostPathCanonical": str(tmp_path.resolve()),
        "containerPath": "/workspace",
        "access": "rw",
    }
    assert body["mountManifest"]["extraMounts"][0]["access"] == "ro"
    assert body["provider"] == {
        "providerId": "volcengine-ark",
        "model": "doubao-seed-2.1-turbo",
        "credentialRef": "secret://provider/volcengine-ark",
    }


def test_adk_message_accepts_text_only_parts() -> None:
    assert adk_message_from_content("hello") == {
        "role": "user",
        "parts": [{"text": "hello"}],
    }
    assert is_text_only_content([{"type": "text", "text": "hello"}])
    assert not is_text_only_content(
        [{"type": "image_url", "image_url": {"url": "data:image/png;base64,aaa"}}]
    )


def test_ws_delegates_first_matching_turn_and_persists_binding(tmp_path, monkeypatch):
    monkeypatch.setattr(SessionManager, "_haas_config", lambda self, workspace: _haas_config())
    monkeypatch.setattr(SessionManager, "_maybe_autotitle", lambda self, session_id: None)
    clients: list[FakeHaasClient] = []

    def factory(config: HaasDelegationConfig) -> FakeHaasClient:
        client = FakeHaasClient(config)
        clients.append(client)
        return client

    provider = ScriptedProvider()
    manager = SessionManager(
        workspace=tmp_path, provider=provider, haas_client_factory=factory
    )
    manager.workspace_trust.set_trusted(tmp_path, True)
    client = TestClient(create_app(manager))

    with client.websocket_connect("/ws/session/s1?agent=code") as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"type": "user_message", "text": "fix this", "model": "volcengine-ark:m1"})
        events = []
        while True:
            event = ws.receive_json()
            events.append(event)
            if event["type"] == "turn_done":
                break

    assert provider.calls == 0
    assert [event["type"] for event in events] == [
        "turn_start",
        "assistant_delta",
        "assistant_delta",
        "assistant_message",
        "turn_end",
        "turn_done",
    ]
    assert clients[0].created[0]["managerSessionId"] == "s1"
    assert clients[0].created[0]["mountManifest"]["primaryWorkspace"]["access"] == "rw"
    assert clients[0].restored == ["dgsess_1"]
    assert clients[0].runs[0]["haas_session_id"] == "hsess_s1"
    assert clients[0].runs[0]["harness_id"] == "chrn_codex_default"
    assert clients[0].runs[0]["user_id"] == "manager"
    record = manager.session_store.load("s1")
    assert record is not None
    binding = record.bindings["haas_delegation"]
    assert binding["delegated_session_id"] == "dgsess_1"
    assert binding["haas_base_url"] == "http://127.0.0.1:8092"
    assert binding["runtime"]["status"] == "running"
    sessions = client.get("/v1/sessions").json()["sessions"]
    row = next(s for s in sessions if s["session_id"] == "s1")
    assert row["delegation"] == {
        "backend": "haas",
        "delegated_session_id": "dgsess_1",
        "runtime_status": "running",
        "container_generation": 1,
    }
    messages = client.get("/v1/sessions/s1/messages").json()["messages"]
    assert messages[-1]["role"] == "assistant"
    assert messages[-1]["content"] == "remote done"


def test_ws_reuses_existing_haas_binding_for_followup(tmp_path, monkeypatch):
    monkeypatch.setattr(SessionManager, "_haas_config", lambda self, workspace: _haas_config())
    monkeypatch.setattr(SessionManager, "_maybe_autotitle", lambda self, session_id: None)
    clients: list[FakeHaasClient] = []

    def factory(config: HaasDelegationConfig) -> FakeHaasClient:
        client = FakeHaasClient(config)
        clients.append(client)
        return client

    manager = SessionManager(
        workspace=tmp_path, provider=ScriptedProvider(), haas_client_factory=factory
    )
    manager.workspace_trust.set_trusted(tmp_path, True)
    client = TestClient(create_app(manager))

    with client.websocket_connect("/ws/session/s1?agent=code") as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"type": "user_message", "text": "fix this", "model": "volcengine-ark:m1"})
        while ws.receive_json()["type"] != "turn_done":
            pass
        ws.send_json({"type": "user_message", "text": "hello again"})
        while ws.receive_json()["type"] != "turn_done":
            pass

    assert sum(len(client.created) for client in clients) == 1
    assert [session for client in clients for session in client.restored] == [
        "dgsess_1",
        "dgsess_1",
    ]


def test_ws_unbound_attachment_request_stays_local(tmp_path, monkeypatch):
    monkeypatch.setattr(SessionManager, "_haas_config", lambda self, workspace: _haas_config())
    monkeypatch.setattr(SessionManager, "_maybe_autotitle", lambda self, session_id: None)
    provider = ScriptedProvider()
    manager = SessionManager(workspace=tmp_path, provider=provider)
    manager.workspace_trust.set_trusted(tmp_path, True)
    client = TestClient(create_app(manager))

    with client.websocket_connect("/ws/session/s1?agent=code") as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json(
            {
                "type": "user_message",
                "text": "fix this image",
                "model": "volcengine-ark:m1",
                "attachments": [
                    {
                        "kind": "image",
                        "data_url": "data:image/png;base64,aaa",
                    }
                ],
            }
        )
        while ws.receive_json()["type"] != "turn_done":
            pass

    assert provider.calls == 1


def test_ws_uses_bound_haas_endpoint_after_config_change(tmp_path, monkeypatch):
    first = _haas_config()
    second = _haas_config()
    second.base_url = "http://127.0.0.1:9999"
    configs = [first, second]
    clients: list[FakeHaasClient] = []

    monkeypatch.setattr(
        SessionManager, "_haas_config", lambda self, workspace: configs.pop(0)
    )
    monkeypatch.setattr(SessionManager, "_maybe_autotitle", lambda self, session_id: None)

    def factory(config: HaasDelegationConfig) -> FakeHaasClient:
        client = FakeHaasClient(config)
        clients.append(client)
        return client

    manager = SessionManager(
        workspace=tmp_path, provider=ScriptedProvider(), haas_client_factory=factory
    )
    manager.workspace_trust.set_trusted(tmp_path, True)
    client = TestClient(create_app(manager))

    with client.websocket_connect("/ws/session/s1?agent=code") as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"type": "user_message", "text": "fix this", "model": "volcengine-ark:m1"})
        while ws.receive_json()["type"] != "turn_done":
            pass
        ws.send_json({"type": "user_message", "text": "hello again"})
        while ws.receive_json()["type"] != "turn_done":
            pass

    assert [client.config.base_url for client in clients] == [
        "http://127.0.0.1:8092",
        "http://127.0.0.1:8092",
    ]


def test_ws_retry_on_haas_bound_session_stays_delegated(tmp_path, monkeypatch):
    monkeypatch.setattr(SessionManager, "_haas_config", lambda self, workspace: _haas_config())
    monkeypatch.setattr(SessionManager, "_maybe_autotitle", lambda self, session_id: None)
    clients: list[FakeHaasClient] = []

    def factory(config: HaasDelegationConfig) -> FakeHaasClient:
        client = FakeHaasClient(config)
        clients.append(client)
        return client

    provider = ScriptedProvider()
    manager = SessionManager(
        workspace=tmp_path, provider=provider, haas_client_factory=factory
    )
    manager.workspace_trust.set_trusted(tmp_path, True)
    client = TestClient(create_app(manager))

    with client.websocket_connect("/ws/session/s1?agent=code") as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"type": "user_message", "text": "fix this", "model": "volcengine-ark:m1"})
        while ws.receive_json()["type"] != "turn_done":
            pass
        ws.send_json({"type": "retry"})
        while ws.receive_json()["type"] != "turn_done":
            pass

    assert provider.calls == 0
    assert sum(len(client.created) for client in clients) == 1
    assert [run["message"] for client in clients for run in client.runs] == [
        {"role": "user", "parts": [{"text": "fix this"}]},
        {"role": "user", "parts": [{"text": "fix this"}]},
    ]


def test_ws_bound_haas_session_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(SessionManager, "_haas_config", lambda self, workspace: _haas_config())
    monkeypatch.setattr(SessionManager, "_maybe_autotitle", lambda self, session_id: None)
    manager = SessionManager(
        workspace=tmp_path,
        provider=ScriptedProvider(),
        haas_client_factory=FailingHaasClient,
    )
    manager.workspace_trust.set_trusted(tmp_path, True)
    client = TestClient(create_app(manager))

    with client.websocket_connect("/ws/session/s1?agent=code") as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"type": "user_message", "text": "fix this", "model": "volcengine-ark:m1"})
        events = []
        while True:
            event = ws.receive_json()
            events.append(event)
            if event["type"] == "turn_done":
                break

    assert any(event["type"] == "error" for event in events)
    messages = client.get("/v1/sessions/s1/messages").json()["messages"]
    assert messages[-2]["role"] == "user"
    assert messages[-2]["content"] == "fix this"
    assert messages[-1]["role"] == "notice"
    assert messages[-1]["kind"] == "error"


def test_ws_delegated_failed_terminal_status_is_preserved(tmp_path, monkeypatch):
    monkeypatch.setattr(SessionManager, "_haas_config", lambda self, workspace: _haas_config())
    monkeypatch.setattr(SessionManager, "_maybe_autotitle", lambda self, session_id: None)
    manager = SessionManager(
        workspace=tmp_path,
        provider=ScriptedProvider(),
        haas_client_factory=FailedTurnHaasClient,
    )
    manager.workspace_trust.set_trusted(tmp_path, True)
    client = TestClient(create_app(manager))

    with client.websocket_connect("/ws/session/s1?agent=code") as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json(
            {"type": "user_message", "text": "fix this", "model": "volcengine-ark:m1"}
        )
        seen = []
        while True:
            event = ws.receive_json()
            seen.append(event)
            if event["type"] == "turn_done":
                break

    turn_end = next(event for event in seen if event["type"] == "turn_end")
    assert turn_end["data"]["status"] == "failed"


def test_ws_keeps_local_path_when_delegation_not_triggered(tmp_path, monkeypatch):
    monkeypatch.setattr(SessionManager, "_haas_config", lambda self, workspace: _haas_config())
    monkeypatch.setattr(SessionManager, "_maybe_autotitle", lambda self, session_id: None)
    provider = ScriptedProvider()
    manager = SessionManager(workspace=tmp_path, provider=provider)
    manager.workspace_trust.set_trusted(tmp_path, True)
    client = TestClient(create_app(manager))

    with client.websocket_connect("/ws/session/s1?agent=code") as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"type": "user_message", "text": "hello", "model": "volcengine-ark:m1"})
        while ws.receive_json()["type"] != "turn_done":
            pass

    assert provider.calls == 1


def test_settings_exposes_haas_delegation_without_token(tmp_path, monkeypatch):
    cfg = _haas_config()
    cfg.api_token = "secret-token"
    monkeypatch.setattr(SessionManager, "_haas_config", lambda self, workspace: cfg)
    manager = SessionManager(workspace=tmp_path, provider=ScriptedProvider())
    client = TestClient(create_app(manager))

    settings = client.get("/v1/settings").json()

    assert settings["haas_delegation"]["enabled"] is True
    assert settings["haas_delegation"]["base_url"] == "http://127.0.0.1:8092"
    assert settings["haas_delegation"]["image_digest_configured"] is True
    assert "api_token" not in settings["haas_delegation"]
    assert "secret-token" not in str(settings)


def test_haas_delegation_settings_rest_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    data_dir = tmp_path / "data"
    manager = SessionManager(data_dir=data_dir, provider=ScriptedProvider())
    manager._haas_supervisor = FakeLocalHaasSupervisor()
    client = TestClient(create_app(manager))

    before = client.get("/v1/settings/haas-delegation").json()
    assert before["enabled"] is False
    assert before["has_api_token"] is False

    after = client.post(
        "/v1/settings/haas-delegation",
        json={
            "enabled": True,
            "base_url": "http://127.0.0.1:8092/",
            "api_token": "secret-token",
            "user_id": "u_manager",
            "harness_id": "chrn_codex_default",
            "image": "haas:local",
            "image_digest": "sha256:test",
            "local_autostart": True,
            "trigger_keywords": ["ship"],
            "agent_allowlist": ["code"],
        },
    ).json()
    assert after["ok"] is True
    assert after["enabled"] is True
    assert after["base_url"] == "http://127.0.0.1:8092"
    assert after["has_api_token"] is True
    assert after["local_autostart"] is True
    assert after["local_status"]["status"] == "running"
    assert "secret-token" not in str(after)

    reborn_manager = SessionManager(data_dir=data_dir, provider=ScriptedProvider())
    reborn_manager._haas_supervisor = FakeLocalHaasSupervisor()
    reborn = TestClient(create_app(reborn_manager))
    restored = reborn.get("/v1/settings/haas-delegation").json()
    assert restored["enabled"] is True
    assert restored["has_api_token"] is True
    assert restored["local_autostart"] is True
    assert restored["trigger_keywords"] == ["ship"]
    assert "secret-token" not in str(restored)

    cleared = reborn.post(
        "/v1/settings/haas-delegation", json={"clear_api_token": True}
    ).json()
    assert cleared["has_api_token"] is False


def test_haas_delegation_settings_autostart_calls_supervisor(tmp_path, monkeypatch):
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    manager = SessionManager(data_dir=tmp_path / "data", provider=ScriptedProvider())
    supervisor = FakeLocalHaasSupervisor()
    manager._haas_supervisor = supervisor
    client = TestClient(create_app(manager))

    after = client.post(
        "/v1/settings/haas-delegation",
        json={
            "enabled": True,
            "local_autostart": True,
            "image_digest": "sha256:test",
        },
    ).json()

    assert after["local_status"]["running"] is True
    assert supervisor.ensured[-1].enabled is True
    assert supervisor.ensured[-1].local_autostart is True


def test_local_haas_supervisor_rejects_non_loopback_url(tmp_path):
    cfg = _haas_config()
    cfg.base_url = "https://haas.example.com"
    cfg.local_autostart = True
    supervisor = LocalHaasSupervisor(tmp_path)

    status = supervisor.ensure(cfg)

    assert status["status"] == "blocked"
    assert status["reason"] == "local_autostart_requires_loopback"


def test_local_haas_supervisor_process_env_omits_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-real-secret")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-secret")
    monkeypatch.setenv("PATH", "/usr/bin")
    supervisor = LocalHaasSupervisor(tmp_path)
    env = supervisor._process_env(tmp_path / "haas.yaml")

    assert env["PATH"] == "/usr/bin"
    assert env["HAAS_CONFIG"] == str(tmp_path / "haas.yaml")
    assert "OPENAI_API_KEY" not in env
    assert "ANTHROPIC_API_KEY" not in env
    assert "local-haas-token" not in str(env)


def test_local_haas_supervisor_writes_private_token_file(tmp_path):
    cfg = _haas_config()
    cfg.api_token = "local-haas-token"
    supervisor = LocalHaasSupervisor(tmp_path)

    config_path = supervisor._write_config(cfg, host="127.0.0.1", port=58092)
    token_path = tmp_path / "haas-token"

    assert token_path.read_text().strip() == "local-haas-token"
    assert token_path.stat().st_mode & 0o777 == 0o600
    config_text = config_path.read_text()
    assert "static_token_file" in config_text
    assert "allow_unpinned_local_image: false" in config_text
    assert "local-haas-token" not in config_text


def test_local_haas_supervisor_can_write_local_image_dev_switch(tmp_path):
    cfg = _haas_config()
    cfg.allow_unpinned_local_image = True
    supervisor = LocalHaasSupervisor(tmp_path)

    config_path = supervisor._write_config(cfg, host="127.0.0.1", port=58092)

    assert "allow_unpinned_local_image: true" in config_path.read_text()


async def test_startup_and_shutdown_manage_local_haas(tmp_path, monkeypatch):
    cfg = _haas_config()
    cfg.local_autostart = True
    monkeypatch.setattr(SessionManager, "_haas_config", lambda self, workspace: cfg)
    manager = SessionManager(data_dir=tmp_path / "data", provider=ScriptedProvider())
    supervisor = FakeLocalHaasSupervisor()
    manager._haas_supervisor = supervisor

    await manager.start_gateway()
    await manager.aclose()

    assert supervisor.ensured
    assert supervisor.stopped == 1


async def test_haas_client_wraps_network_errors(monkeypatch):
    async def boom(*args, **kwargs):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx.AsyncClient, "post", boom)
    client = HaasDelegationClient(_haas_config())

    try:
        await client.restore("dgsess_1")
    except HaasDelegationError as exc:
        assert "unreachable" in str(exc)
    else:
        raise AssertionError("expected HaasDelegationError")


def test_ws_delegates_to_real_haas_asgi_app(tmp_path, monkeypatch):
    from haas.api import build_app as build_haas_app
    from haas.harnesses import FakeAdapter
    from haas.identity import Principal
    from haas.runtime import FakeDelegatedContainerRuntime

    cfg = _haas_config()
    cfg.api_token = "haas-token"
    monkeypatch.setattr(SessionManager, "_haas_config", lambda self, workspace: cfg)
    monkeypatch.setattr(SessionManager, "_maybe_autotitle", lambda self, session_id: None)

    haas_app = build_haas_app(
        adapter=FakeAdapter(),
        delegated_containers=FakeDelegatedContainerRuntime(),
        identity_tokens={
            "haas-token": Principal(
                principalId="p_manager",
                tenantId="t_manager",
                userIds=frozenset({"manager"}),
            )
        },
    )

    def factory(config: HaasDelegationConfig) -> HaasDelegationClient:
        return HaasDelegationClient(
            config,
            transport=httpx.ASGITransport(app=haas_app),
        )

    manager = SessionManager(
        workspace=tmp_path,
        provider=ScriptedProvider(),
        haas_client_factory=factory,
    )
    manager.workspace_trust.set_trusted(tmp_path, True)
    client = TestClient(create_app(manager))

    with client.websocket_connect("/ws/session/s1?agent=code") as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json(
            {"type": "user_message", "text": "fix this", "model": "volcengine-ark:m1"}
        )
        seen = []
        while True:
            event = ws.receive_json()
            seen.append(event)
            if event["type"] == "turn_done":
                break

    assert [event["type"] for event in seen] == [
        "turn_start",
        "assistant_delta",
        "assistant_message",
        "turn_end",
        "turn_done",
    ]
    messages = client.get("/v1/sessions/s1/messages").json()["messages"]
    assert messages[-1]["role"] == "assistant"
    assert messages[-1]["content"] == "delegated"
    binding = manager.session_store.load("s1").bindings["haas_delegation"]
    assert binding["delegated_session_id"].startswith("dgsess_")
    assert binding["runtime"]["status"] == "running"
