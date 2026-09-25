"""Adapter interaction (approval / user-input) and misc helper coverage.

Drives the server-request -> HaaS event -> respond_interaction loop entirely
offline with a scripted FakeTransport (no real Codex).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from haas.harnesses.base import (
    PrepareSessionRequest,
    StartTurnRequest,
)
from haas.harnesses.codex_app_server import CodexAdapter
from haas.harnesses.codex_app_server.rpc import CodexConnectionError
from haas.harnesses.codex_app_server.transport import CodexEndpoint
from haas.stores import MemoryStore
from haas.stores.memory import ApprovalRecord, InputRequestRecord


@pytest.fixture(autouse=True)
def _isolate_connect_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never leak the stubbed connect_endpoint into sibling integration tests."""
    import haas.harnesses.codex_app_server.rpc as rpc_mod

    monkeypatch.setattr(rpc_mod, "connect_endpoint", rpc_mod.connect_endpoint, raising=True)


class FakeTransport:
    def __init__(
        self,
        results: dict[str, Any] | None = None,
        *,
        send_error: Exception | None = None,
    ) -> None:
        self.results = results or {}
        self.send_error = send_error
        self.sent: list[dict[str, Any]] = []
        self.closed = False
        self._inbox: asyncio.Queue[str] = asyncio.Queue()

    async def send(self, message: str) -> None:
        if self.send_error is not None:
            raise self.send_error
        msg = json.loads(message)
        self.sent.append(msg)
        method = msg.get("method")
        if msg.get("id") is None:
            return
        await self._inbox.put(
            json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": self.results.get(method, {})})
        )

    async def push(self, payload: dict[str, Any]) -> None:
        await self._inbox.put(json.dumps(payload))

    async def recv(self) -> Any:
        return await self._inbox.get()

    async def close(self) -> None:
        self.closed = True


def _default_results() -> dict[str, Any]:
    return {
        "initialize": {},
        "thread/start": {"thread": {"id": "thr_1"}},
        "thread/resume": {"thread": {"id": "thr_1"}},
        "turn/start": {"turn": {"id": "codex_turn_1"}},
        "turn/interrupt": {},
    }


def _adapter_with(transport: FakeTransport) -> CodexAdapter:
    adapter = CodexAdapter(
        CodexEndpoint(transport="loopback_websocket", listen_url="ws://127.0.0.1:1")
    )

    async def _fake_connect_endpoint(*_a: Any, **_kw: Any) -> FakeTransport:
        return transport

    import haas.harnesses.codex_app_server.rpc as rpc_mod

    rpc_mod.connect_endpoint = _fake_connect_endpoint  # type: ignore[assignment]
    return adapter


async def _started(transport: FakeTransport, **kw: Any) -> tuple[CodexAdapter, Any]:
    adapter = _adapter_with(transport)
    await adapter.prepare_session(PrepareSessionRequest(sessionId="hsess_1", appName="chrn_1"))
    handle = await adapter.start_turn(
        StartTurnRequest(
            invocationId="inv_1",
            sessionId="hsess_1",
            turnId="turn_1",
            appName="chrn_1",
            input=[{"text": "hi"}],
            **kw,
        )
    )
    return adapter, handle


async def _terminal() -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "method": "turn/completed",
        "params": {
            "threadId": "thr_1",
            "turnId": "codex_turn_1",
            "turn": {"id": "codex_turn_1", "status": "completed"},
        },
    }


# --- approval required flow -------------------------------------------------


async def test_command_approval_server_request_emits_approval_event() -> None:
    transport = FakeTransport(_default_results())
    adapter, handle = await _started(transport)
    adapter.bind_interaction_store(MemoryStore())

    await transport.push(
        {
            "jsonrpc": "2.0",
            "id": 500,
            "method": "item/commandExecution/requestApproval",
            "params": {"threadId": "thr_1", "turnId": "codex_turn_1"},
        }
    )
    await transport.push(await _terminal())

    events = [e async for e in adapter.stream_events(handle)]
    approval = [e for e in events if e.type == "haas.approval.required"]
    assert len(approval) == 1
    assert approval[0].actions["haas"]["kind"] == "command"
    assert approval[0].actions["haas"]["safeSummary"] == "Run command"

    approval_id = approval[0].actions["haas"]["approvalId"]
    record = ApprovalRecord(
        id=approval_id,
        sessionId="hsess_1",
        invocationId="inv_1",
        turnId="turn_1",
        nativeRequestId=500,
        adapterGeneration=adapter._generation,
    )
    await adapter.respond_interaction(record, {"decision": "approved"})
    respond = next(m for m in transport.sent if m.get("id") == 500)
    assert respond["result"] == {"decision": "accept"}
    assert approval_id not in adapter._pending_interactions


async def test_file_approval_and_input_request_flows() -> None:
    transport = FakeTransport(_default_results())
    adapter, handle = await _started(transport)
    adapter.bind_interaction_store(MemoryStore())

    await transport.push(
        {
            "jsonrpc": "2.0",
            "id": 600,
            "method": "item/fileChange/requestApproval",
            "params": {"threadId": "thr_1", "turnId": "codex_turn_1"},
        }
    )
    await transport.push(
        {
            "jsonrpc": "2.0",
            "id": 601,
            "method": "item/tool/requestUserInput",
            "params": {
                "threadId": "thr_1",
                "turnId": "codex_turn_1",
                "questions": [
                    {"id": "q1", "header": "Where?", "question": "which dir?", "isSecret": False},
                    "not-a-dict",
                ],
                "isBlocking": False,
            },
        }
    )
    await transport.push(await _terminal())

    events = [e async for e in adapter.stream_events(handle)]
    approval = next(e for e in events if e.type == "haas.approval.required")
    assert approval.actions["haas"]["safeSummary"] == "Change files"

    inreq = next(e for e in events if e.type == "haas.input.required")
    questions = inreq.actions["haas"]["questions"]
    assert len(questions) == 1  # non-dict question skipped
    assert questions[0]["header"] == "Where?"
    assert inreq.actions["haas"]["blocking"] is False

    input_id = inreq.actions["haas"]["inputRequestId"]
    input_record = InputRequestRecord(
        id=input_id,
        sessionId="hsess_1",
        invocationId="inv_1",
        turnId="turn_1",
        questions=[],
        nativeRequestId=601,
        adapterGeneration=adapter._generation,
    )
    await adapter.respond_interaction(
        input_record,
        {"answers": {"q1": {"values": ["/tmp"]}, "bad": "x"}},
    )
    respond = next(m for m in transport.sent if m.get("id") == 601)
    assert respond["result"] == {"answers": {"q1": {"answers": ["/tmp"]}}}


# --- respond_interaction error paths ----------------------------------------


async def test_respond_interaction_stale_rejected_and_bad_decision() -> None:
    transport = FakeTransport(_default_results())
    adapter, _ = await _started(transport)
    adapter.bind_interaction_store(MemoryStore())

    record = ApprovalRecord(
        id="appr_x",
        sessionId="hsess_1",
        invocationId="inv_1",
        turnId="turn_1",
        nativeRequestId=1,
        adapterGeneration=adapter._generation + 99,
    )
    with pytest.raises(CodexConnectionError, match="stale"):
        await adapter.respond_interaction(record, {"decision": "approved"})

    record.adapterGeneration = adapter._generation
    with pytest.raises(CodexConnectionError, match="not pending"):
        await adapter.respond_interaction(record, {"decision": "approved"})

    adapter._pending_interactions.add("appr_x")
    with pytest.raises(CodexConnectionError, match="invalid_interaction_decision"):
        await adapter.respond_interaction(record, {"decision": "maybe"})


async def test_persist_server_request_without_store_is_noop() -> None:
    transport = FakeTransport(_default_results())
    adapter, handle = await _started(transport)
    await transport.push(
        {
            "jsonrpc": "2.0",
            "id": 700,
            "method": "item/commandExecution/requestApproval",
            "params": {"threadId": "thr_1", "turnId": "codex_turn_1"},
        }
    )
    await transport.push(await _terminal())
    events = [e async for e in adapter.stream_events(handle)]
    assert not any(e.type == "haas.approval.required" for e in events)


# --- close() -----------------------------------------------------------------


async def test_close_clears_turn_state() -> None:
    transport = FakeTransport(_default_results())
    adapter, handle = await _started(transport)
    assert adapter._turn_contexts
    await adapter.close()
    assert not adapter._turn_contexts
    assert not adapter._turn_index
    assert transport.closed is True
