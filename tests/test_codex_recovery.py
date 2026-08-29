"""Tests for Codex adapter recovery semantics (specs/codex-app-server-adapter §7/§10).

Loopback integration tests simulate app-server restart (thread loss) and
verify generation bumping, thread/resume validation, and non_resumable.
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from haas.harnesses.base import (
    InspectSessionRequest,
    PrepareSessionRequest,
    ResumeSessionRequest,
    StartTurnRequest,
)
from haas.harnesses.codex_app_server.adapter import CodexAdapter
from haas.harnesses.codex_app_server.transport import CodexEndpoint


def _adapter(port: int) -> CodexAdapter:
    return CodexAdapter(
        CodexEndpoint(transport="loopback_websocket", listen_url=f"ws://127.0.0.1:{port}")
    )


@pytest.mark.integration
async def test_thread_not_found_recreates_thread() -> None:
    live_threads: set[str] = set()
    thread_count = 0
    calls: list[str] = []

    async def handler(ws: Any) -> None:
        nonlocal thread_count
        async for raw in ws:
            msg = json.loads(raw)
            method = msg.get("method")
            if method == "initialize":
                await ws.send(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": {}}))
            elif method == "initialized":
                continue
            elif method == "thread/start":
                calls.append("thread/start")
                thread_count += 1
                live_threads.add(f"thr_{thread_count}")
                thread_resp = {
                    "jsonrpc": "2.0",
                    "id": msg["id"],
                    "result": {"thread": {"id": f"thr_{thread_count}"}},
                }
                await ws.send(json.dumps(thread_resp))
            elif method == "thread/resume":
                calls.append("thread/resume")
                tid = msg["params"]["threadId"]
                if tid in live_threads:
                    resume_resp = {
                        "jsonrpc": "2.0",
                        "id": msg["id"],
                        "result": {"thread": {"id": tid}},
                    }
                    await ws.send(json.dumps(resume_resp))
                else:
                    await ws.send(
                        json.dumps(
                            {
                                "jsonrpc": "2.0",
                                "id": msg["id"],
                                "error": {"code": -32000, "message": "thread not found"},
                            }
                        )
                    )
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
                                "threadId": msg["params"]["threadId"],
                                "turnId": "codex_turn_1",
                                "turn": {"id": "codex_turn_1", "status": "completed"},
                            },
                        }
                    )
                )

    from websockets.asyncio.server import serve

    async with serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        adapter = _adapter(port)
        await adapter.prepare_session(PrepareSessionRequest(sessionId="hsess_1", appName="chrn_1"))

        # First turn creates thr_1.
        handle = await adapter.start_turn(
            StartTurnRequest(
                invocationId="inv_1", sessionId="hsess_1", turnId="turn_1",
                appName="chrn_1", input=[{"text": "hi"}],
            )
        )
        _ = [event async for event in adapter.stream_events(handle)]
        assert "thread/start" in calls

        # Simulate app-server restart: thr_1 is lost.
        live_threads.clear()

        # Second turn must thread/resume (not found) then re-create via thread/start.
        handle2 = await adapter.start_turn(
            StartTurnRequest(
                invocationId="inv_2", sessionId="hsess_1", turnId="turn_2",
                appName="chrn_1", input=[{"text": "hi"}],
            )
        )
        _ = [event async for event in adapter.stream_events(handle2)]
        assert calls.count("thread/resume") >= 1
        assert calls.count("thread/start") == 2


@pytest.mark.integration
async def test_inspect_session_reports_non_resumable() -> None:
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
        adapter = _adapter(port)
        await adapter.prepare_session(PrepareSessionRequest(sessionId="hsess_1", appName="chrn_1"))
        await adapter.start_turn(
            StartTurnRequest(
                invocationId="inv_1", sessionId="hsess_1", turnId="turn_1",
                appName="chrn_1", input=[{"text": "hi"}],
            )
        )
        inspection = await adapter.inspect_session(InspectSessionRequest(sessionId="hsess_1"))
        assert inspection.status == "non_resumable"


@pytest.mark.integration
async def test_resume_session_restores_thread() -> None:
    async def handler(ws: Any) -> None:
        async for raw in ws:
            msg = json.loads(raw)
            method = msg.get("method")
            if method == "initialize":
                await ws.send(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": {}}))
            elif method == "initialized":
                continue
            elif method == "thread/resume":
                resume_resp = {
                    "jsonrpc": "2.0",
                    "id": msg["id"],
                    "result": {"thread": {"id": msg["params"]["threadId"]}},
                }
                await ws.send(json.dumps(resume_resp))
            else:
                await ws.send(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": {}}))

    from websockets.asyncio.server import serve

    async with serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        adapter = _adapter(port)
        prepared = await adapter.resume_session(
            ResumeSessionRequest(sessionId="hsess_1", opaque={"threadId": "thr_1"})
        )
        assert prepared.nativeRef.get("threadId") == "thr_1"
