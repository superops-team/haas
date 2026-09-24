"""Adapter probe / readiness / endpoint-reason and small branch coverage.

All offline: the codex CLI subprocess, schema generation and readiness RPC are
monkeypatched; no real Codex binary or socket is touched.
"""

from __future__ import annotations

import pytest

from haas.harnesses.codex_app_server import CodexAdapter
from haas.harnesses.codex_app_server.transport import CodexEndpoint


def _loopback() -> CodexEndpoint:
    return CodexEndpoint(transport="loopback_websocket", listen_url="ws://127.0.0.1:1")


# --- probe() ----------------------------------------------------------------


async def test_probe_unavailable_when_binary_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    import haas.harnesses.codex_app_server.adapter as ad

    def _boom() -> str:
        raise FileNotFoundError("no codex binary")

    monkeypatch.setattr(ad, "codex_cli_version", _boom)
    probe = await CodexAdapter(_loopback()).probe()
    assert probe.status == "unavailable"
    assert "codex_binary_unavailable" in probe.safeDetails["safeReason"]


async def test_probe_skips_schema_when_fixture_missing_then_fails_readiness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import haas.harnesses.codex_app_server.adapter as ad

    monkeypatch.setattr(ad, "codex_cli_version", lambda *_a: "0.150.1")

    def _no_fixture(*_a: object, **_k: object) -> object:
        raise FileNotFoundError("fixture not shipped")

    monkeypatch.setattr(ad, "load_fixture", _no_fixture)

    class _ReadinessRpc:
        def __init__(self, *_a: object, **_k: object) -> None:
            self.request_timeout = 60.0

        async def connect(self) -> None:
            raise OSError("connection refused")

        async def close(self) -> None:
            pass

    monkeypatch.setattr(ad, "CodexJsonRpc", _ReadinessRpc)
    probe = await CodexAdapter(_loopback()).probe()
    assert probe.status == "unavailable"
    assert probe.safeDetails["schemaCheck"] == "skipped"
    assert probe.safeDetails["safeReason"] == "codex_readiness_probe_failed"


async def test_probe_schema_generation_failure_is_reported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import haas.harnesses.codex_app_server.adapter as ad

    monkeypatch.setattr(ad, "codex_cli_version", lambda *_a: "0.150.1")
    monkeypatch.setattr(ad, "load_fixture", lambda *_a: {"files": {}})

    def _bad_gen(*_a: object, **_k: object) -> object:
        raise RuntimeError("generator blew up")

    monkeypatch.setattr(ad, "generate_schema_files", _bad_gen)
    probe = await CodexAdapter(_loopback()).probe()
    assert probe.status == "unavailable"
    assert "schema_probe_failed" in probe.safeDetails["safeReason"]


async def test_probe_drift_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    import haas.harnesses.codex_app_server.adapter as ad

    monkeypatch.setattr(ad, "codex_cli_version", lambda *_a: "0.150.1")
    monkeypatch.setattr(ad, "load_fixture", lambda *_a: {"files": {"a.json": {}}})
    monkeypatch.setattr(ad, "generate_schema_files", lambda *_a: {"a.json": {}, "b.json": {}})
    monkeypatch.setattr(ad, "schema_drift", lambda _f, _c: ["added: b.json"])

    class _ReadinessRpc:
        def __init__(self, *_a: object, **_k: object) -> None:
            self.request_timeout = 60.0

        async def connect(self) -> None:
            raise OSError("refused")

        async def close(self) -> None:
            pass

    monkeypatch.setattr(ad, "CodexJsonRpc", _ReadinessRpc)
    probe = await CodexAdapter(_loopback()).probe()
    assert probe.safeDetails["safeReason"] == "schema_mismatch"
    assert probe.safeDetails["schemaDrift"] == ["added: b.json"]


# --- probe_readiness() / endpoint reason ------------------------------------


async def test_probe_readiness_unix_socket_missing() -> None:
    endpoint = CodexEndpoint(
        transport="unix_websocket", listen_url="unix:///definitely/not/here.sock"
    )
    probe = await CodexAdapter(endpoint).probe_readiness()
    assert probe.status == "unavailable"
    assert probe.safeDetails["safeReason"] == "codex_socket_unavailable"


async def test_probe_readiness_unix_socket_unconfigured() -> None:
    endpoint = CodexEndpoint(transport="unix_websocket", listen_url="")
    probe = await CodexAdapter(endpoint).probe_readiness()
    assert probe.status == "unavailable"
    assert probe.safeDetails["safeReason"] == "codex_socket_not_configured"


async def test_probe_readiness_loopback_connect_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import haas.harnesses.codex_app_server.adapter as ad

    class _ReadinessRpc:
        def __init__(self, *_a: object, **_k: object) -> None:
            self.request_timeout = 60.0

        async def connect(self) -> None:
            raise OSError("refused")

        async def close(self) -> None:
            pass

    monkeypatch.setattr(ad, "CodexJsonRpc", _ReadinessRpc)
    probe = await CodexAdapter(_loopback()).probe_readiness()
    assert probe.status == "unavailable"
    assert probe.safeDetails["safeReason"] == "codex_readiness_probe_failed"
