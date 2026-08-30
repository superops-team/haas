"""Offline Codex adapter/rpc/transport failure-path tests.

These cover the recovery and error semantics required by AGENTS.md 铁律 #8
(timeout / cancel / adapter crash / transport drop must be explainable) and
specs/codex-app-server-adapter §10, using an in-process fake transport so no
real Codex binary, socket or network is required.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from haas.harnesses.base import (
    CancelTurnRequest,
    InspectSessionRequest,
    ListArtifactsRequest,
    PrepareSessionRequest,
    ResumeSessionRequest,
    StartTurnRequest,
)
from haas.harnesses.codex_app_server import CodexAdapter
from haas.harnesses.codex_app_server.adapter import (
    _notification_thread_id,
    _notification_turn_id,
    _notification_type,
    _terminal_status,
)
from haas.harnesses.codex_app_server.rpc import (
    CodexConnectionError,
    CodexJsonRpc,
    CodexNotReadyError,
    CodexRequestTimeout,
    classify_message,
    decode_message,
)
from haas.harnesses.codex_app_server.transport import (
    CodexEndpoint,
    CodexTransportError,
    StdioTransport,
    connect_endpoint,
    unix_socket_path,
)

# --- fake transport ---------------------------------------------------------


class FakeTransport:
    """Scripted CodexTransport: maps method -> result, queues notifications."""

    def __init__(
        self,
        results: dict[str, Any] | None = None,
        *,
        errors: dict[str, Any] | None = None,
        send_error: Exception | None = None,
        recv_error: Exception | None = None,
        silent_methods: set[str] | None = None,
    ) -> None:
        self.results = results or {}
        self.errors = errors or {}
        self.send_error = send_error
        self.recv_error = recv_error
        self.silent_methods = silent_methods or set()
        self.sent: list[dict[str, Any]] = []
        self.closed = False
        self._inbox: asyncio.Queue[str] = asyncio.Queue()

    async def send(self, message: str) -> None:
        if self.send_error is not None:
            raise self.send_error
        msg = json.loads(message)
        self.sent.append(msg)
        method = msg.get("method")
        if msg.get("id") is None or method in self.silent_methods:
            return
        if method in self.errors:
            await self._inbox.put(
                json.dumps(
                    {"jsonrpc": "2.0", "id": msg["id"], "error": self.errors[method]}
                )
            )
            return
        await self._inbox.put(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": msg["id"],
                    "result": self.results.get(method, {}),
                }
            )
        )

    async def push(self, payload: dict[str, Any]) -> None:
        await self._inbox.put(json.dumps(payload))

    async def recv(self) -> Any:
        if self.recv_error is not None:
            raise self.recv_error
        return await self._inbox.get()

    async def close(self) -> None:
        self.closed = True

    def methods(self) -> list[str]:
        return [m.get("method", "") for m in self.sent]


@pytest.fixture(autouse=True)
def _isolate_connector(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure connector patching never leaks into other test modules.

    ``_adapter_with`` replaces the module-level ``connect_endpoint`` used by
    ``CodexJsonRpc.connect``. Registering it through ``monkeypatch`` here makes
    pytest restore the original after every test in this module.
    """
    import haas.harnesses.codex_app_server.rpc as rpc_mod

    monkeypatch.setattr(
        rpc_mod, "connect_endpoint", rpc_mod.connect_endpoint, raising=True
    )


def _adapter_with(transport: FakeTransport, **kwargs: Any) -> CodexAdapter:
    adapter = CodexAdapter(
        CodexEndpoint(transport="loopback_websocket", listen_url="ws://127.0.0.1:1"),
        **kwargs,
    )

    async def _fake_connect_endpoint(*_a: Any, **_kw: Any) -> FakeTransport:
        return transport

    adapter._rpc._transport = None
    # Restored by the autouse _isolate_connector fixture above.
    import haas.harnesses.codex_app_server.rpc as rpc_mod

    rpc_mod.connect_endpoint = _fake_connect_endpoint  # type: ignore[assignment]
    return adapter


def _default_results() -> dict[str, Any]:
    return {
        "initialize": {},
        "thread/start": {"thread": {"id": "thr_1"}},
        "thread/resume": {"thread": {"id": "thr_1"}},
        "turn/start": {"turn": {"id": "codex_turn_1"}},
        "turn/interrupt": {},
    }


async def _started(
    transport: FakeTransport, **turn_kwargs: Any
) -> tuple[CodexAdapter, Any]:
    adapter = _adapter_with(transport)
    await adapter.prepare_session(
        PrepareSessionRequest(sessionId="hsess_1", appName="chrn_1")
    )
    handle = await adapter.start_turn(
        StartTurnRequest(
            invocationId="inv_1",
            sessionId="hsess_1",
            turnId="turn_1",
            appName="chrn_1",
            input=[{"text": "hi"}],
            **turn_kwargs,
        )
    )
    return adapter, handle


# --- stdio stubs ------------------------------------------------------------
#
# StdioTransport.start() always launches `<bin> app-server --listen stdio://`,
# so a stub must ignore argv. These write throwaway scripts under pytest's
# tmp area rather than relying on system binaries.

_STUB_DIR: list[str] = []


def _write_stub(name: str, body: str) -> str:
    import os
    import stat
    import tempfile

    if not _STUB_DIR:
        _STUB_DIR.append(tempfile.mkdtemp(prefix="haas-stdio-stub-"))
    path = os.path.join(_STUB_DIR[0], name)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body)
    os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return path


def _echo_stub() -> str:
    """Ignores argv, echoes each stdin line back unbuffered."""
    return _write_stub(
        "echo_stub.py",
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "for line in sys.stdin:\n"
        "    sys.stdout.write(line)\n"
        "    sys.stdout.flush()\n",
    )


def _exit_immediately_stub() -> str:
    return _write_stub(
        "exit_stub.py",
        "#!/usr/bin/env python3\nimport sys\nsys.exit(0)\n",
    )


def _ignore_sigterm_stub() -> str:
    """Ignores SIGTERM so close() must escalate to kill()."""
    return _write_stub(
        "stubborn_stub.py",
        "#!/usr/bin/env python3\n"
        "import signal, time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "while True:\n"
        "    time.sleep(0.1)\n",
    )


# --- pure helpers -----------------------------------------------------------


def test_notification_field_extraction_prefers_top_level() -> None:
    assert _notification_thread_id({"threadId": "a"}) == "a"
    assert _notification_thread_id({"thread_id": "b"}) == "b"
    assert _notification_thread_id({"params": {"threadId": "c"}}) == "c"
    assert _notification_thread_id({"params": {"thread_id": "d"}}) == "d"
    assert _notification_thread_id({}) == ""
    assert _notification_thread_id({"params": "not-a-dict"}) == ""


def test_notification_turn_id_extraction_all_shapes() -> None:
    assert _notification_turn_id({"turnId": "a"}) == "a"
    assert _notification_turn_id({"turn_id": "b"}) == "b"
    assert _notification_turn_id({"params": {"turnId": "c"}}) == "c"
    assert _notification_turn_id({"params": {"turn_id": "d"}}) == "d"
    assert _notification_turn_id({"params": {"turn": {"id": "e"}}}) == "e"
    assert _notification_turn_id({"params": {"turn": {}}}) == ""
    assert _notification_turn_id({}) == ""


def test_notification_type_falls_back_to_method() -> None:
    assert _notification_type({"type": "x"}) == "x"
    assert _notification_type({"params": {"type": "y"}}) == "y"
    assert _notification_type({"method": "z"}) == "z"
    assert _notification_type({}) == ""


def test_terminal_status_maps_turn_completed_variants() -> None:
    def completed(status: str) -> dict[str, Any]:
        return {
            "type": "turn/completed",
            "params": {"turn": {"id": "t", "status": status}},
        }

    assert _terminal_status(completed("completed")) == "completed"
    assert _terminal_status(completed("failed")) == "failed"
    assert _terminal_status(completed("cancelled")) == "cancelled"
    assert _terminal_status(completed("interrupted")) == "interrupted"
    # "stopped" is a normal stop; anything unrecognized must fail closed.
    assert _terminal_status(completed("stopped")) == "completed"
    assert _terminal_status(completed("weird-new-status")) == "failed"
    # Missing turn object defaults to completed.
    assert _terminal_status({"type": "turn/completed", "params": {}}) == "completed"
    assert _terminal_status({"type": "turn/failed"}) == "failed"
    assert _terminal_status({"type": "turn/cancelled"}) == "cancelled"
    assert _terminal_status({"type": "item/agentMessage/delta"}) is None


# --- rpc message classification --------------------------------------------


def test_decode_message_variants() -> None:
    assert decode_message('{"a":1}') == {"a": 1}
    assert decode_message(b'{"a":2}') == {"a": 2}
    assert decode_message(bytearray(b'{"a":3}')) == {"a": 3}
    assert decode_message("   ") is None
    assert decode_message("not json") is None
    assert decode_message("[1,2]") is None  # non-object JSON
    assert decode_message(12345) is None


def test_classify_message_kinds() -> None:
    assert classify_message({"id": 1, "result": {}}, {1}) == "response"
    assert classify_message({"id": 9, "method": "ask"}, {1}) == "server_request"
    assert classify_message({"id": 9}, {1}) == "invalid"
    assert classify_message({"method": "note"}, set()) == "notification"
    assert classify_message({}, set()) == "invalid"


# --- transport --------------------------------------------------------------


def test_unix_socket_path_parsing_and_errors() -> None:
    assert unix_socket_path("unix:///tmp/codex.sock") == "/tmp/codex.sock"
    with pytest.raises(CodexTransportError, match="invalid unix listen URL"):
        unix_socket_path("ws://127.0.0.1:1")
    with pytest.raises(CodexTransportError, match="empty unix socket path"):
        unix_socket_path("unix://")


async def test_connect_endpoint_rejects_unknown_transport() -> None:
    with pytest.raises(CodexTransportError, match="unsupported transport"):
        await connect_endpoint(CodexEndpoint(transport="carrier-pigeon", listen_url="x"))


async def test_stdio_transport_roundtrip_and_close() -> None:
    """Drive StdioTransport against a stub that ignores Codex's CLI args.

    ``StdioTransport.start`` always appends ``app-server --listen stdio://``,
    so the stub must accept and ignore argv and simply echo stdin lines.
    """
    transport = await StdioTransport.start(_echo_stub())
    await transport.send('{"jsonrpc":"2.0","method":"ping"}')
    line = await transport.recv()
    assert json.loads(line)["method"] == "ping"
    await transport.close()
    # close() is idempotent once the process has exited.
    await transport.close()


async def test_stdio_transport_close_kills_unresponsive_process() -> None:
    """A process ignoring SIGTERM must still be reaped without hanging."""
    transport = await StdioTransport.start(_ignore_sigterm_stub())
    await asyncio.sleep(0.2)
    await asyncio.wait_for(transport.close(), timeout=15)


async def test_stdio_transport_recv_raises_on_eof() -> None:
    transport = await StdioTransport.start(_exit_immediately_stub())
    with pytest.raises(EOFError, match="stdio closed"):
        for _ in range(50):
            await transport.recv()
    await transport.close()


# --- rpc lifecycle ----------------------------------------------------------


async def test_request_before_connect_is_not_ready() -> None:
    rpc = CodexJsonRpc(
        CodexEndpoint(transport="loopback_websocket", listen_url="ws://127.0.0.1:1")
    )
    with pytest.raises(CodexNotReadyError):
        await rpc.request("thread/start", {})
    with pytest.raises(CodexNotReadyError):
        await rpc.notify("initialized", {})


async def test_rpc_surfaces_jsonrpc_error_payloads() -> None:
    transport = FakeTransport(
        _default_results(), errors={"thread/start": {"message": "boom"}}
    )
    adapter = _adapter_with(transport)
    await adapter.prepare_session(
        PrepareSessionRequest(sessionId="hsess_1", appName="chrn_1")
    )
    with pytest.raises(CodexConnectionError, match="boom"):
        await adapter._rpc.request("thread/start", {})


async def test_rpc_surfaces_non_dict_error_payload() -> None:
    transport = FakeTransport(_default_results(), errors={"thread/start": "plain-error"})
    adapter = _adapter_with(transport)
    await adapter.prepare_session(
        PrepareSessionRequest(sessionId="hsess_1", appName="chrn_1")
    )
    with pytest.raises(CodexConnectionError, match="plain-error"):
        await adapter._rpc.request("thread/start", {})


async def test_rpc_request_times_out_without_response() -> None:
    transport = FakeTransport(_default_results(), silent_methods={"thread/start"})
    adapter = _adapter_with(transport)
    await adapter.prepare_session(
        PrepareSessionRequest(sessionId="hsess_1", appName="chrn_1")
    )
    adapter._rpc._request_timeout = 0.05
    with pytest.raises(CodexRequestTimeout, match="timed out"):
        await adapter._rpc.request("thread/start", {})


async def test_rpc_send_failure_marks_disconnected() -> None:
    transport = FakeTransport(_default_results())
    adapter = _adapter_with(transport)
    await adapter.prepare_session(
        PrepareSessionRequest(sessionId="hsess_1", appName="chrn_1")
    )
    transport.send_error = BrokenPipeError("pipe gone")
    with pytest.raises(CodexConnectionError, match="send failed"):
        await adapter._rpc.request("thread/start", {})
    assert adapter._rpc.connected is False


async def test_rpc_notify_failure_marks_disconnected() -> None:
    transport = FakeTransport(_default_results())
    adapter = _adapter_with(transport)
    await adapter.prepare_session(
        PrepareSessionRequest(sessionId="hsess_1", appName="chrn_1")
    )
    transport.send_error = OSError("socket dead")
    with pytest.raises(CodexConnectionError, match="send notification failed"):
        await adapter._rpc.notify("some/note", {})
    assert adapter._rpc.connected is False


async def test_rpc_routes_server_requests_separately() -> None:
    transport = FakeTransport(_default_results())
    adapter = _adapter_with(transport)
    await adapter.prepare_session(
        PrepareSessionRequest(sessionId="hsess_1", appName="chrn_1")
    )
    await transport.push({"jsonrpc": "2.0", "id": 999, "method": "approval/request"})
    requests = adapter._rpc.server_requests()
    async with asyncio.timeout(2):
        received = await anext(requests)
    assert received["method"] == "approval/request"


async def test_rpc_drops_invalid_and_unparsable_frames() -> None:
    transport = FakeTransport(_default_results())
    adapter = _adapter_with(transport)
    await adapter.prepare_session(
        PrepareSessionRequest(sessionId="hsess_1", appName="chrn_1")
    )
    await transport._inbox.put("this is not json")
    await transport._inbox.put(json.dumps({"id": 4242}))  # invalid: id, no method
    await transport.push({"jsonrpc": "2.0", "method": "kept/note", "params": {}})
    notifications = adapter._rpc.notifications()
    async with asyncio.timeout(2):
        received = await anext(notifications)
    assert received["method"] == "kept/note"


async def test_close_fails_pending_requests() -> None:
    transport = FakeTransport(_default_results(), silent_methods={"thread/start"})
    adapter = _adapter_with(transport)
    await adapter.prepare_session(
        PrepareSessionRequest(sessionId="hsess_1", appName="chrn_1")
    )
    task = asyncio.create_task(adapter._rpc.request("thread/start", {}))
    await asyncio.sleep(0.05)
    await adapter._rpc.close()
    with pytest.raises(CodexConnectionError):
        await task
    assert transport.closed is True


# --- adapter turn lifecycle ------------------------------------------------


async def test_stream_events_rejects_unknown_handle() -> None:
    transport = FakeTransport(_default_results())
    adapter, handle = await _started(transport)
    from haas.harnesses.base import TurnHandle

    bogus = TurnHandle(turnId="nope", sessionId="hsess_1", invocationId="inv_1")
    with pytest.raises(CodexConnectionError, match="unknown turn handle"):
        [e async for e in adapter.stream_events(bogus)]


async def test_stream_events_ignores_other_turns() -> None:
    transport = FakeTransport(_default_results())
    adapter, handle = await _started(transport)
    # Foreign thread and foreign turn must both be filtered out.
    await transport.push(
        {
            "jsonrpc": "2.0",
            "method": "item/agentMessage/delta",
            "params": {
                "type": "item/agentMessage/delta",
                "threadId": "other_thread",
                "turnId": "codex_turn_1",
                "delta": "ignored",
            },
        }
    )
    await transport.push(
        {
            "jsonrpc": "2.0",
            "method": "item/agentMessage/delta",
            "params": {
                "type": "item/agentMessage/delta",
                "threadId": "thr_1",
                "turnId": "other_turn",
                "delta": "ignored",
            },
        }
    )
    await transport.push(
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
    events = [e async for e in adapter.stream_events(handle)]
    texts = [e.content.get("parts") for e in events]
    assert not any("ignored" in json.dumps(t) for t in texts)
    result = await adapter.finalize_turn(handle)
    assert result.status == "completed"


async def test_interrupted_status_is_reported_as_cancelled() -> None:
    transport = FakeTransport(_default_results())
    adapter, handle = await _started(transport)
    await transport.push(
        {
            "jsonrpc": "2.0",
            "method": "turn/interrupted",
            "params": {
                "type": "turn/interrupted",
                "threadId": "thr_1",
                "turnId": "codex_turn_1",
            },
        }
    )
    events = [e async for e in adapter.stream_events(handle)]
    assert events[-1].type == "harness.turn.cancelled"
    assert (await adapter.finalize_turn(handle)).status == "cancelled"


async def test_stream_ends_without_terminal_yields_incomplete() -> None:
    """Transport drop mid-turn must produce an explainable incomplete status."""
    transport = FakeTransport(_default_results())
    adapter, handle = await _started(transport)
    # Simulate the notification loop observing a closed connection.
    adapter._rpc._connected = False
    events = [e async for e in adapter.stream_events(handle)]
    assert events[-1].type == "harness.turn.incomplete"
    assert (await adapter.finalize_turn(handle)).status == "incomplete"


async def test_finalize_unknown_turn_is_incomplete() -> None:
    transport = FakeTransport(_default_results())
    adapter, _ = await _started(transport)
    from haas.harnesses.base import TurnHandle

    result = await adapter.finalize_turn(
        TurnHandle(turnId="ghost", sessionId="hsess_1", invocationId="inv_1")
    )
    assert result.status == "incomplete"


async def test_cancel_unknown_turn_is_accepted() -> None:
    transport = FakeTransport(_default_results())
    adapter, _ = await _started(transport)
    result = await adapter.cancel_turn(
        CancelTurnRequest(turnId="ghost", sessionId="hsess_1", invocationId="inv_1")
    )
    assert result.status == "accepted"


async def test_cancel_falls_back_to_accepted_when_interrupt_fails() -> None:
    transport = FakeTransport(
        _default_results(), errors={"turn/interrupt": {"message": "already gone"}}
    )
    adapter, _ = await _started(transport)
    result = await adapter.cancel_turn(
        CancelTurnRequest(turnId="turn_1", sessionId="hsess_1", invocationId="inv_1")
    )
    assert result.status == "accepted"


async def test_start_turn_fails_when_thread_start_returns_no_id() -> None:
    transport = FakeTransport({**_default_results(), "thread/start": {"thread": {}}})
    adapter = _adapter_with(transport)
    await adapter.prepare_session(
        PrepareSessionRequest(sessionId="hsess_1", appName="chrn_1")
    )
    with pytest.raises(CodexConnectionError, match="no thread id"):
        await adapter.start_turn(
            StartTurnRequest(
                invocationId="inv_1",
                sessionId="hsess_1",
                turnId="turn_1",
                appName="chrn_1",
                input=[{"text": "hi"}],
            )
        )


async def test_start_turn_recreates_thread_when_not_found() -> None:
    """thread/resume reporting 'thread not found' must transparently restart."""
    transport = FakeTransport(
        _default_results(), errors={"thread/resume": {"message": "thread not found"}}
    )
    adapter, _ = await _started(transport)
    # Second turn on the same session resumes, gets not-found, and restarts.
    handle2 = await adapter.start_turn(
        StartTurnRequest(
            invocationId="inv_2",
            sessionId="hsess_1",
            turnId="turn_2",
            appName="chrn_1",
            input=[{"text": "again"}],
        )
    )
    assert handle2.opaque["threadId"] == "thr_1"
    assert transport.methods().count("thread/start") == 2


async def test_start_turn_propagates_unexpected_resume_error() -> None:
    transport = FakeTransport(
        _default_results(), errors={"thread/resume": {"message": "internal explosion"}}
    )
    adapter, _ = await _started(transport)
    with pytest.raises(CodexConnectionError, match="internal explosion"):
        await adapter.start_turn(
            StartTurnRequest(
                invocationId="inv_2",
                sessionId="hsess_1",
                turnId="turn_2",
                appName="chrn_1",
                input=[{"text": "again"}],
            )
        )


async def test_start_turn_passes_model_and_sandbox_policy() -> None:
    transport = FakeTransport(_default_results())
    adapter = _adapter_with(transport)
    await adapter.prepare_session(
        PrepareSessionRequest(sessionId="hsess_1", appName="chrn_1")
    )
    await adapter.start_turn(
        StartTurnRequest(
            invocationId="inv_1",
            sessionId="hsess_1",
            turnId="turn_1",
            appName="chrn_1",
            input=[{"text": "hi"}],
            model="gpt-5.6-terra",
            sandbox={"mode": "read-only", "workspaceRoot": "/ws"},
        )
    )
    thread_start = next(m for m in transport.sent if m.get("method") == "thread/start")
    turn_start = next(m for m in transport.sent if m.get("method") == "turn/start")
    assert thread_start["params"]["model"] == "gpt-5.6-terra"
    assert thread_start["params"]["cwd"] == "/ws"
    assert turn_start["params"]["sandboxPolicy"]["type"] == "readOnly"
    assert turn_start["params"]["model"] == "gpt-5.6-terra"


async def test_start_turn_defaults_writable_roots_when_malformed() -> None:
    transport = FakeTransport(_default_results())
    adapter = _adapter_with(transport)
    await adapter.prepare_session(
        PrepareSessionRequest(sessionId="hsess_1", appName="chrn_1")
    )
    await adapter.start_turn(
        StartTurnRequest(
            invocationId="inv_1",
            sessionId="hsess_1",
            turnId="turn_1",
            appName="chrn_1",
            input=[{"text": "hi"}],
            sandbox={"mode": "workspace-write", "writableRoots": "not-a-list"},
        )
    )
    turn_start = next(m for m in transport.sent if m.get("method") == "turn/start")
    assert turn_start["params"]["sandboxPolicy"]["writableRoots"] == ["/workspace"]


# --- session inspection / resume -------------------------------------------


async def test_inspect_unknown_session_is_unknown() -> None:
    transport = FakeTransport(_default_results())
    adapter = _adapter_with(transport)
    inspection = await adapter.inspect_session(
        InspectSessionRequest(sessionId="never-seen")
    )
    assert inspection.status == "unknown"


async def test_inspect_active_session_reports_thread() -> None:
    transport = FakeTransport(_default_results())
    adapter, _ = await _started(transport)
    inspection = await adapter.inspect_session(
        InspectSessionRequest(sessionId="hsess_1")
    )
    assert inspection.status == "active"
    assert inspection.nativeRef["threadId"] == "thr_1"


async def test_resume_session_uses_opaque_thread_id() -> None:
    transport = FakeTransport(_default_results())
    adapter = _adapter_with(transport)
    prepared = await adapter.resume_session(
        ResumeSessionRequest(sessionId="hsess_9", opaque={"threadId": "thr_from_opaque"})
    )
    assert prepared.nativeRef.get("threadId") == "thr_1"


async def test_resume_session_without_any_thread_is_non_resumable() -> None:
    transport = FakeTransport(_default_results())
    adapter = _adapter_with(transport)
    prepared = await adapter.resume_session(ResumeSessionRequest(sessionId="hsess_9"))
    assert prepared.nativeRef.get("nonResumable") is True


async def test_cleanup_session_forgets_thread() -> None:
    transport = FakeTransport(_default_results())
    adapter, _ = await _started(transport)
    from haas.harnesses.base import CleanupSessionRequest

    result = await adapter.cleanup_session(CleanupSessionRequest(sessionId="hsess_1"))
    assert result.status == "cleaned"
    assert (
        await adapter.inspect_session(InspectSessionRequest(sessionId="hsess_1"))
    ).status == "unknown"


# --- declarations -----------------------------------------------------------


async def test_list_artifacts_is_unsupported_and_empty() -> None:
    transport = FakeTransport(_default_results())
    adapter = _adapter_with(transport)
    assert await adapter.list_artifacts(ListArtifactsRequest(sessionId="s")) == []


def test_sandbox_declaration_and_capabilities_are_honest() -> None:
    adapter = CodexAdapter(
        CodexEndpoint(transport="loopback_websocket", listen_url="ws://127.0.0.1:1")
    )
    decl = adapter.sandbox_declaration()
    assert decl.cwd == "/workspace"
    caps = adapter._capabilities()
    # spec §4: advisory tool restriction must not be advertised as hard block.
    assert caps["toolRestriction"] == "advisory"
    assert caps["mcp"] == "unsupported"
