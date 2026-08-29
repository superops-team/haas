"""Secretless reverse assertions for the Codex adapter.

Verifies the adapter never injects credentials (Authorization, bearer tokens,
API keys) into the JSON-RPC requests it sends to Codex app-server. The model
provider credential path (S6 model proxy) is the only allowed secret channel.
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from haas.harnesses.base import PrepareSessionRequest, StartTurnRequest
from haas.harnesses.codex_app_server.adapter import CodexAdapter
from haas.harnesses.codex_app_server.transport import CodexEndpoint

_SENSITIVE_TOKENS = ("authorization", "bearer ", "api_key", "apikey", "sk-", "cookie")


def _contains_secret(message: dict[str, Any]) -> bool:
    text = json.dumps(message).lower()
    return any(token in text for token in _SENSITIVE_TOKENS)


@pytest.mark.integration
async def test_adapter_requests_are_secretless() -> None:
    captured: list[dict[str, Any]] = []

    async def handler(ws: Any) -> None:
        async for raw in ws:
            msg = json.loads(raw)
            captured.append(msg)
            method = msg.get("method")
            if method == "initialize":
                await ws.send(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": {}}))
            elif method == "initialized":
                continue
            elif method == "thread/start":
                thread_resp = {
                    "jsonrpc": "2.0",
                    "id": msg["id"],
                    "result": {"thread": {"id": "thr_1"}},
                }
                await ws.send(json.dumps(thread_resp))
            elif method == "turn/start":
                turn_resp = {
                    "jsonrpc": "2.0",
                    "id": msg["id"],
                    "result": {"turn": {"id": "codex_turn_1"}},
                }
                await ws.send(json.dumps(turn_resp))
                await ws.send(
                    json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "method": "turn/completed",
                            "params": {
                                "type": "turn/completed",
                                "threadId": "thr_1",
                                "turnId": "codex_turn_1",
                                "turn": {"id": "codex_turn_1", "status": "completed"},
                            },
                        }
                    )
                )
            elif method == "turn/interrupt":
                await ws.send(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": {}}))

    from websockets.asyncio.server import serve

    async with serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        adapter = CodexAdapter(
            CodexEndpoint(transport="loopback_websocket", listen_url=f"ws://127.0.0.1:{port}")
        )
        await adapter.prepare_session(PrepareSessionRequest(sessionId="hsess_1", appName="chrn_1"))
        handle = await adapter.start_turn(
            StartTurnRequest(
                invocationId="inv_1",
                sessionId="hsess_1",
                turnId="turn_1",
                appName="chrn_1",
                input=[{"text": "hi"}],
            )
        )
        _ = [event async for event in adapter.stream_events(handle)]

    for message in captured:
        assert not _contains_secret(
            message
        ), f"secret leaked in request: {json.dumps(message)[:200]}"
