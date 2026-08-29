"""Tests for the Codex app-server adapter.

Offline unit tests cover sandbox projection. Loopback integration tests drive
the full turn lifecycle against an in-process fake app-server (no real Codex).
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from haas.harnesses.base import (
    CancelTurnRequest,
    PrepareSessionRequest,
    StartTurnRequest,
)
from haas.harnesses.codex_app_server.adapter import CodexAdapter
from haas.harnesses.codex_app_server.sandbox import (
    SandboxPolicyError,
    to_thread_sandbox_mode,
    to_turn_sandbox_policy,
)
from haas.harnesses.codex_app_server.transport import CodexEndpoint

# --- offline: sandbox projection -------------------------------------------


def test_thread_sandbox_mode_mapping() -> None:
    assert to_thread_sandbox_mode("workspace-write") == "workspace-write"
    assert to_thread_sandbox_mode("workspace_write") == "workspace-write"
    assert to_thread_sandbox_mode("read-only") == "read-only"
    assert to_thread_sandbox_mode("danger-full-access") == "danger-full-access"


def test_thread_sandbox_mode_rejects_unknown() -> None:
    with pytest.raises(SandboxPolicyError):
        to_thread_sandbox_mode("no-isolation")


def test_turn_sandbox_policy_workspace_write() -> None:
    policy = to_turn_sandbox_policy("workspace-write", ["/workspace"])
    assert policy["type"] == "workspaceWrite"
    assert policy["writableRoots"] == ["/workspace"]


def test_turn_sandbox_policy_read_only() -> None:
    policy = to_turn_sandbox_policy("read-only", [])
    assert policy["type"] == "readOnly"


def test_turn_sandbox_policy_danger_full_access() -> None:
    policy = to_turn_sandbox_policy("danger-full-access", [])
    assert policy["type"] == "dangerFullAccess"


def test_turn_sandbox_policy_rejects_broad_root() -> None:
    with pytest.raises(SandboxPolicyError, match="too_broad"):
        to_turn_sandbox_policy("workspace-write", ["/"])


def test_turn_sandbox_policy_rejects_relative_root() -> None:
    with pytest.raises(SandboxPolicyError, match="not_absolute"):
        to_turn_sandbox_policy("workspace-write", ["workspace"])


# --- loopback integration: full turn lifecycle -----------------------------


@pytest.mark.integration
async def test_codex_adapter_full_turn() -> None:
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
            elif method == "turn/start":
                turn_resp = {
                    "jsonrpc": "2.0",
                    "id": msg["id"],
                    "result": {"turn": {"id": "codex_turn_1"}},
                }
                await ws.send(json.dumps(turn_resp))
                delta = {
                    "jsonrpc": "2.0",
                    "method": "item/agentMessage/delta",
                    "params": {
                        "type": "item/agentMessage/delta",
                        "threadId": "thr_1",
                        "turnId": "codex_turn_1",
                        "delta": "hello",
                    },
                }
                await ws.send(json.dumps(delta))
                completed = {
                    "jsonrpc": "2.0",
                    "method": "turn/completed",
                    "params": {
                        "type": "turn/completed",
                        "threadId": "thr_1",
                        "turnId": "codex_turn_1",
                        "turn": {"id": "codex_turn_1", "status": "completed"},
                    },
                }
                await ws.send(json.dumps(completed))
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
        events = [event async for event in adapter.stream_events(handle)]
        assert events, "expected at least one event"
        assert any(event.type == "harness.text.delta" for event in events)
        result = await adapter.finalize_turn(handle)
        assert result.status == "completed"


@pytest.mark.integration
async def test_codex_adapter_cancel() -> None:
    interrupt_sent: list[dict[str, Any]] = []

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
            elif method == "turn/start":
                turn_resp = {
                    "jsonrpc": "2.0",
                    "id": msg["id"],
                    "result": {"turn": {"id": "codex_turn_1"}},
                }
                await ws.send(json.dumps(turn_resp))
            elif method == "turn/interrupt":
                interrupt_sent.append(msg["params"])
                await ws.send(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": {}}))

    from websockets.asyncio.server import serve

    async with serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        adapter = CodexAdapter(
            CodexEndpoint(transport="loopback_websocket", listen_url=f"ws://127.0.0.1:{port}")
        )
        await adapter.prepare_session(PrepareSessionRequest(sessionId="hsess_1", appName="chrn_1"))
        await adapter.start_turn(
            StartTurnRequest(
                invocationId="inv_1",
                sessionId="hsess_1",
                turnId="turn_1",
                appName="chrn_1",
                input=[{"text": "hi"}],
            )
        )
        result = await adapter.cancel_turn(
            CancelTurnRequest(turnId="turn_1", sessionId="hsess_1", invocationId="inv_1")
        )
        assert result.status == "cancelled"
        assert interrupt_sent == [{"threadId": "thr_1", "turnId": "codex_turn_1"}]


@pytest.mark.integration
async def test_codex_adapter_timeout_fails_turn() -> None:
    """A stuck turn (no terminal notification) must time out, not hang forever."""

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
            elif method == "turn/start":
                turn_resp = {
                    "jsonrpc": "2.0",
                    "id": msg["id"],
                    "result": {"turn": {"id": "codex_turn_1"}},
                }
                await ws.send(json.dumps(turn_resp))
                # Deliberately emit no notification: the turn is stuck.
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
                timeoutSeconds=1.0,
            )
        )
        events = [event async for event in adapter.stream_events(handle)]
        assert events, "expected a terminal event"
        result = await adapter.finalize_turn(handle)
        assert result.status == "failed"
