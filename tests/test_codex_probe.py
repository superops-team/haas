"""Tests for CodexAdapter probe / resume / cleanup edge paths."""

from __future__ import annotations

import json
from typing import Any

import pytest

from haas.harnesses.base import (
    CleanupSessionRequest,
    PrepareSessionRequest,
    ResumeSessionRequest,
)
from haas.harnesses.codex_app_server.adapter import CodexAdapter
from haas.harnesses.codex_app_server.transport import CodexEndpoint


async def test_probe_binary_unavailable() -> None:
    adapter = CodexAdapter(
        CodexEndpoint(transport="loopback_websocket", listen_url="ws://127.0.0.1:1"),
        codex_bin="/nonexistent/codex",
    )
    probe = await adapter.probe()
    assert probe.status == "unavailable"
    assert "safeReason" in probe.safeDetails


@pytest.mark.integration
async def test_resume_session_reports_non_resumable() -> None:
    async def handler(ws: Any) -> None:
        async for raw in ws:
            msg = json.loads(raw)
            method = msg.get("method")
            if method == "initialize":
                await ws.send(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": {}}))
            elif method == "initialized":
                continue
            elif method == "thread/resume":
                await ws.send(
                    json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": msg["id"],
                            "error": {"code": -32000, "message": "thread not found"},
                        }
                    )
                )
            else:
                await ws.send(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": {}}))

    from websockets.asyncio.server import serve

    async with serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        adapter = CodexAdapter(
            CodexEndpoint(transport="loopback_websocket", listen_url=f"ws://127.0.0.1:{port}")
        )
        prepared = await adapter.resume_session(
            ResumeSessionRequest(sessionId="hsess_1", opaque={"threadId": "thr_1"})
        )
        assert prepared.nativeRef.get("nonResumable") is True


@pytest.mark.integration
async def test_cleanup_session() -> None:
    async def handler(ws: Any) -> None:
        async for raw in ws:
            msg = json.loads(raw)
            if msg.get("method") == "initialize":
                await ws.send(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": {}}))

    from websockets.asyncio.server import serve

    async with serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        adapter = CodexAdapter(
            CodexEndpoint(transport="loopback_websocket", listen_url=f"ws://127.0.0.1:{port}")
        )
        await adapter.prepare_session(PrepareSessionRequest(sessionId="hsess_1", appName="chrn_1"))
        result = await adapter.cleanup_session(CleanupSessionRequest(sessionId="hsess_1"))
        assert result.status == "cleaned"
