from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from haas.harnesses.codex_app_server.adapter import CodexAdapter
from haas.harnesses.codex_app_server.transport import CodexEndpoint


class ProbeTransport:
    def __init__(self, *, initialize_error: bool = False) -> None:
        self.sent: list[dict[str, Any]] = []
        self.initialize_error = initialize_error
        self.closed = False
        self._recv_count = 0
        self._notification_sent = asyncio.Event()

    async def send(self, message: str) -> None:
        payload = json.loads(message)
        self.sent.append(payload)
        if payload.get("method") == "initialized":
            self._notification_sent.set()

    async def recv(self) -> str:
        self._recv_count += 1
        request = self.sent[0]
        if self._recv_count > 1:
            await self._notification_sent.wait()
            raise EOFError("readiness connection complete")
        if request["method"] == "initialize":
            if self.initialize_error:
                return json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": request["id"],
                        "error": {"code": -32000, "message": "not initialized"},
                    }
                )
            return json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": {}})
        raise AssertionError(f"unexpected request: {request}")

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_probe_requires_initialize_and_initialized(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = ProbeTransport()

    async def connect(*_args: Any, **_kwargs: Any) -> ProbeTransport:
        return transport

    import haas.harnesses.codex_app_server.adapter as adapter_module
    import haas.harnesses.codex_app_server.rpc as rpc_module

    monkeypatch.setattr(rpc_module, "connect_endpoint", connect)
    monkeypatch.setattr(adapter_module, "codex_cli_version", lambda _bin: "0.150.1")
    monkeypatch.setattr(adapter_module, "load_fixture", lambda _version: {})
    monkeypatch.setattr(adapter_module, "generate_schema_files", lambda _bin: {})
    monkeypatch.setattr(adapter_module, "schema_drift", lambda _fixture, _current: [])

    adapter = CodexAdapter(
        CodexEndpoint(transport="loopback_websocket", listen_url="ws://127.0.0.1:1")
    )
    result = await adapter.probe()

    assert result.status == "ready"
    assert [message["method"] for message in transport.sent] == [
        "initialize",
        "initialized",
    ]
    assert "id" not in transport.sent[1]
    assert transport.closed is True


@pytest.mark.asyncio
async def test_probe_fails_closed_when_initialize_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = ProbeTransport(initialize_error=True)

    async def connect(*_args: Any, **_kwargs: Any) -> ProbeTransport:
        return transport

    import haas.harnesses.codex_app_server.adapter as adapter_module
    import haas.harnesses.codex_app_server.rpc as rpc_module

    monkeypatch.setattr(rpc_module, "connect_endpoint", connect)
    monkeypatch.setattr(adapter_module, "codex_cli_version", lambda _bin: "0.150.1")
    monkeypatch.setattr(adapter_module, "load_fixture", lambda _version: {})
    monkeypatch.setattr(adapter_module, "generate_schema_files", lambda _bin: {})
    monkeypatch.setattr(adapter_module, "schema_drift", lambda _fixture, _current: [])

    adapter = CodexAdapter(
        CodexEndpoint(transport="loopback_websocket", listen_url="ws://127.0.0.1:1")
    )
    result = await adapter.probe()

    assert result.status == "unavailable"
    assert result.safeDetails["safeReason"] == "codex_readiness_probe_failed"
    assert transport.closed is True
