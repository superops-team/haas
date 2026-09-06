"""Gated real-Codex tests (stdio handshake + loopback app-server turn).

These require an explicit environment switch (``HAAS_E2E=1`` or
``HAAS_E2E_CODEX=1``) and a local ``codex`` binary; they are skipped by
default (roadmap §8.1).
"""
from __future__ import annotations

import asyncio
import contextlib
import os
from pathlib import Path

import pytest

from haas.harnesses.base import PrepareSessionRequest, StartTurnRequest
from haas.harnesses.codex_app_server.adapter import CodexAdapter
from haas.harnesses.codex_app_server.rpc import CodexJsonRpc
from haas.harnesses.codex_app_server.transport import CodexEndpoint


@pytest.fixture(autouse=True)
def _isolated_codex_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate Codex state DB from the real ~/.codex.

    Without this, Codex app-server fails to initialize its sqlite state
    runtime when the host ~/.codex is in use by another Codex process.
    """
    codex_home = tmp_path / ".codex"
    codex_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("CODEX_HOME", str(codex_home))


_requires_codex = pytest.mark.skipif(
    os.environ.get("HAAS_E2E") != "1" and os.environ.get("HAAS_E2E_CODEX") != "1",
    reason="HAAS_E2E=1 / HAAS_E2E_CODEX=1 required",
)


@pytest.mark.e2e
@_requires_codex
async def test_stdio_transport_handshake() -> None:
    endpoint = CodexEndpoint(transport="stdio", listen_url="stdio://")
    rpc = CodexJsonRpc(endpoint)
    await rpc.connect()
    try:
        assert rpc.initialized is True
        result = await rpc.request("model/list", {})
        assert isinstance(result, dict)
        assert "data" in result
    finally:
        await rpc.close()


@pytest.mark.e2e
@_requires_codex
async def test_codex_adapter_stdio_minimal_turn() -> None:
    adapter = CodexAdapter(CodexEndpoint(transport="stdio", listen_url="stdio://"))
    prepared = await adapter.prepare_session(
        PrepareSessionRequest(sessionId="hsess_stdio", appName="chrn_codex_default")
    )
    assert prepared.sessionId == "hsess_stdio"

    handle = await adapter.start_turn(
        StartTurnRequest(
            invocationId="inv_stdio",
            sessionId="hsess_stdio",
            turnId="turn_stdio",
            appName="chrn_codex_default",
            input=[{"type": "text", "text": "Reply with the single word: ok"}],
            timeoutSeconds=120,
        )
    )
    assert handle.turnId == "turn_stdio"

    events = [event async for event in adapter.stream_events(handle)]
    # A real turn may fail without a model provider, but the adapter must
    # always produce a terminal event (completed / failed / incomplete).
    assert events, "expected at least a terminal event"
    result = await adapter.finalize_turn(handle)
    assert result.status in {"completed", "failed", "incomplete", "cancelled"}


@pytest.mark.e2e
@_requires_codex
async def test_codex_adapter_loopback_websocket_real_turn() -> None:
    """Real loopback-WebSocket turn through the configured Codex model provider.

    Skipped when no model provider is available (the turn cannot complete).
    """
    import socket

    port = 19092
    proc = await asyncio.create_subprocess_exec(
        "codex",
        "app-server",
        "--listen",
        f"ws://127.0.0.1:{port}",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        # Wait for the listener to accept connections.
        for _ in range(100):
            with socket.socket() as s:
                s.settimeout(0.2)
                if s.connect_ex(("127.0.0.1", port)) == 0:
                    break
            await asyncio.sleep(0.1)
        else:
            pytest.skip("codex app-server ws listener did not start")

        adapter = CodexAdapter(
            CodexEndpoint(transport="loopback_websocket", listen_url=f"ws://127.0.0.1:{port}")
        )
        await adapter.prepare_session(
            PrepareSessionRequest(sessionId="hsess_ws", appName="chrn_codex_default")
        )
        handle = await adapter.start_turn(
            StartTurnRequest(
                invocationId="inv_ws",
                sessionId="hsess_ws",
                turnId="turn_ws",
                appName="chrn_codex_default",
                input=[{"type": "text", "text": "Reply with exactly: WS-LOOPBACK-OK"}],
                timeoutSeconds=120,
            )
        )
        text_parts: list[str] = []
        async for event in adapter.stream_events(handle):
            if event.type == "harness.text.delta":
                text_parts.extend(
                    p.get("text", "")
                    for p in event.content.get("parts", [])
                    if isinstance(p, dict)
                )
        result = await adapter.finalize_turn(handle)
        if result.status != "completed":
            pytest.skip("real turn did not complete (model provider unavailable?)")
        assert "".join(text_parts).strip() == "WS-LOOPBACK-OK"
    finally:
        proc.terminate()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(proc.wait(), timeout=5)
        if proc.returncode is None:
            proc.kill()
