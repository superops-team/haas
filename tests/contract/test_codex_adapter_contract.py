"""HarnessAdapter contract for CodexAdapter (loopback fake app-server).

Every concrete adapter must satisfy the same interface contract
(specs/harness-adapter §11). CodexAdapter is driven here through a loopback
fake app-server; no real Codex or external network is required.
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from haas.harnesses.base import (
    CancelTurnRequest,
    CleanupSessionRequest,
    InspectSessionRequest,
    ListArtifactsRequest,
    PrepareSessionRequest,
    ResumeSessionRequest,
    StartTurnRequest,
)
from haas.harnesses.codex_app_server.adapter import CodexAdapter
from haas.harnesses.codex_app_server.transport import CodexEndpoint


@pytest.fixture
async def fake_server_port() -> Any:
    async def handler(ws: Any) -> None:
        async for raw in ws:
            msg = json.loads(raw)
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
            elif method == "thread/resume":
                resume_resp = {
                    "jsonrpc": "2.0",
                    "id": msg["id"],
                    "result": {"thread": {"id": msg["params"]["threadId"]}},
                }
                await ws.send(json.dumps(resume_resp))
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
                            "method": "item/agentMessage/delta",
                            "params": {
                                "type": "item/agentMessage/delta",
                                "threadId": "thr_1",
                                "turnId": "codex_turn_1",
                                "delta": "hello",
                            },
                        }
                    )
                )
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
            else:
                await ws.send(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": {}}))

    from websockets.asyncio.server import serve

    async with serve(handler, "127.0.0.1", 0) as server:
        yield server.sockets[0].getsockname()[1]


@pytest.fixture
def adapter(fake_server_port: int) -> CodexAdapter:
    return CodexAdapter(
        CodexEndpoint(transport="loopback_websocket", listen_url=f"ws://127.0.0.1:{fake_server_port}")
    )


@pytest.mark.integration
async def test_contract_probe_structure(adapter: CodexAdapter) -> None:
    probe = await adapter.probe()
    assert probe.adapterId == "codex-app-server"
    assert probe.base == "codex"
    assert probe.status in {"ready", "unavailable"}
    assert probe.capabilities["streaming"] is True
    assert probe.capabilities["cancellation"] in {"hard", "best_effort", "unsupported"}


@pytest.mark.integration
async def test_contract_prepare_and_start(adapter: CodexAdapter) -> None:
    prepared = await adapter.prepare_session(
        PrepareSessionRequest(sessionId="hsess_1", appName="chrn_1")
    )
    assert prepared.sessionId == "hsess_1"

    handle = await adapter.start_turn(
        StartTurnRequest(
            invocationId="inv_1", sessionId="hsess_1", turnId="turn_1",
            appName="chrn_1", input=[{"text": "hi"}],
        )
    )
    assert handle.turnId == "turn_1"


@pytest.mark.integration
async def test_contract_stream_and_terminal(adapter: CodexAdapter) -> None:
    handle = await adapter.start_turn(
        StartTurnRequest(
            invocationId="inv_1", sessionId="hsess_1", turnId="turn_1",
            appName="chrn_1", input=[{"text": "hi"}],
        )
    )
    events = [event async for event in adapter.stream_events(handle)]
    assert events
    assert all(event.author == "codex" for event in events)
    assert all("parts" in event.content for event in events)

    result = await adapter.finalize_turn(handle)
    assert result.status in {"completed", "cancelled", "failed", "incomplete"}


@pytest.mark.integration
async def test_contract_cancel(adapter: CodexAdapter) -> None:
    result = await adapter.cancel_turn(
        CancelTurnRequest(turnId="turn_1", sessionId="hsess_1", invocationId="inv_1")
    )
    assert result.status in {"accepted", "cancelled", "unsupported"}


@pytest.mark.integration
async def test_contract_remaining_methods(adapter: CodexAdapter) -> None:
    decl = adapter.sandbox_declaration()
    assert decl.cwd
    assert decl.approvalMode in {"never", "on-request"}

    inspection = await adapter.inspect_session(InspectSessionRequest(sessionId="hsess_1"))
    assert inspection.status in {"active", "unknown", "non_resumable"}

    resumed = await adapter.resume_session(ResumeSessionRequest(sessionId="hsess_1"))
    assert resumed.sessionId == "hsess_1"

    artifacts = await adapter.list_artifacts(ListArtifactsRequest(sessionId="hsess_1"))
    assert artifacts == []

    cleanup = await adapter.cleanup_session(CleanupSessionRequest(sessionId="hsess_1"))
    assert cleanup.status == "cleaned"
