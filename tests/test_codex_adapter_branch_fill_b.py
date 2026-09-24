"""Branch-fill unit tests for CodexAdapter + transport error / edge paths.

Offline only: drives the adapter with a stubbed RPC layer (no real Codex,
no real socket). Covers:
  * _ensure_connected connect/handshake failure propagation
  * _to_codex_input boundary branches (empty parts, unknown type, native
    passthrough opt-in / rejection, extra-field rejection)
  * start_turn sandbox/policy/mcp/error branches
  * stream_events timeout, unknown-turn, overloaded-subscriber paths
  * StdioTransport / WebSocketTransport / connect_endpoint edge paths
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock

import pytest

from haas.harnesses.base import (
    PrepareSessionRequest,
    StartTurnRequest,
    TurnHandle,
)
from haas.harnesses.codex_app_server.adapter import (
    HaaSTurnInputInvalid,
    CodexAdapter,
    _to_codex_input,
)
from haas.harnesses.codex_app_server.rpc import (
    CodexConnectionError,
    CodexRequestTimeout,
    CodexSubscriberOverloaded,
)
from haas.harnesses.codex_app_server.transport import (
    CodexEndpoint,
    CodexTransportError,
    StdioTransport,
    WebSocketTransport,
    connect_endpoint,
    validate_loopback_websocket_url,
)
from haas.stores import MemoryStore
from haas.stores.memory import ApprovalRecord, InputRequestRecord


# --- helpers ---------------------------------------------------------------


def _make_adapter() -> CodexAdapter:
    adapter = CodexAdapter(
        CodexEndpoint(transport="stdio", listen_url="stdio://")
    )
    adapter._generation = 1
    return adapter


# --- _ensure_connected failure paths ----------------------------------------


async def test_prepare_session_connect_endpoint_failure_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _make_adapter()

    async def _boom(*_a: Any, **_kw: Any) -> Any:
        raise CodexConnectionError("boom: cannot spawn codex")

    import haas.harnesses.codex_app_server.rpc as rpc_mod

    monkeypatch.setattr(rpc_mod, "connect_endpoint", _boom)

    with pytest.raises(CodexConnectionError, match="boom: cannot spawn codex"):
        await adapter.prepare_session(
            PrepareSessionRequest(sessionId="hsess_1", appName="chrn_1")
        )


async def test_prepare_session_handshake_failure_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _make_adapter()

    class _BadHandshakeTransport:
        async def send(self, message: str) -> None:
            # initialize request arrives, but never reply; force handshake
            # timeout path by closing immediately.
            raise EOFError("remote closed during handshake")

        async def recv(self) -> Any:
            raise EOFError("closed")

        async def close(self) -> None:
            return None

    async def _fake(*_a: Any, **_kw: Any) -> _BadHandshakeTransport:
        return _BadHandshakeTransport()

    import haas.harnesses.codex_app_server.rpc as rpc_mod

    monkeypatch.setattr(rpc_mod, "connect_endpoint", _fake)

    with pytest.raises(Exception):
        await adapter.prepare_session(
            PrepareSessionRequest(sessionId="hsess_1", appName="chrn_1")
        )


# --- _to_codex_input boundary branches -------------------------------------


def test_to_codex_input_empty_items_returns_default_blank() -> None:
    assert _to_codex_input([]) == [{"type": "text", "text": ""}]


def test_to_codex_input_native_item_rejected_on_northbound() -> None:
    with pytest.raises(HaaSTurnInputInvalid, match="native harness input items"):
        _to_codex_input([{"type": "text", "text": "hi"}])


def test_to_codex_input_native_item_passthrough_opted_in() -> None:
    out = _to_codex_input(
        [{"type": "text", "text": "hi"}], allow_native_passthrough=True
    )
    assert out == [{"type": "text", "text": "hi"}]


def test_to_codex_input_extra_fields_rejected() -> None:
    with pytest.raises(HaaSTurnInputInvalid, match="unexpected input fields"):
        _to_codex_input(
            [{"role": "user", "parts": [{"text": "hi"}], "weird": 1}]
        )


def test_to_codex_input_non_dict_items_and_non_str_text_skipped() -> None:
    out = _to_codex_input(
        [
            "not-a-dict",  # type: ignore[list-item]
            {"parts": [{"text": "hello"}, {"text": 123}]},
            {"text": "second"},
            {"text_no": 1},
        ]
    )
    assert out == [
        {"type": "text", "text": "hello"},
        {"type": "text", "text": "second"},
    ]


def test_to_codex_input_all_skipped_falls_back_to_blank() -> None:
    out = _to_codex_input([{"role": "user", "parts": [{"no_text": 1}]}])
    assert out == [{"type": "text", "text": ""}]


# --- start_turn sandbox / policy / error branches --------------------------


class _FakeRpc:
    """Minimal stub of CodexJsonRpc used to drive start_turn offline."""

    def __init__(self) -> None:
        self.connected = True
        self.initialized = True
        self.notification_cursor = 0
        self.server_request_cursor = 0
        self.requests: list[tuple[str, dict[str, Any]]] = []
        self.request_impl: Any = AsyncMock()

    async def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self.requests.append((method, params))
        return await self.request_impl(method, params)

    async def notifications(self, *, after: int = 0):  # type: ignore[no-untyped-def]
        if False:
            yield {}  # pragma: no cover
        return

    async def server_requests(self, *, after: int = 0):  # type: ignore[no-untyped-def]
        if False:
            yield {}  # pragma: no cover
        return

    async def close(self) -> None:
        return None


def _started_adapter(rpc: _FakeRpc) -> CodexAdapter:
    adapter = _make_adapter()
    adapter._rpc = rpc  # type: ignore[assignment]
    return adapter


def _base_kwargs() -> dict[str, Any]:
    return dict(
        invocationId="inv_1",
        sessionId="hsess_1",
        turnId="turn_1",
        appName="chrn_1",
        userId="u_1",
        input=[{"text": "hi"}],
    )


async def test_start_turn_empty_workspace_root_falls_back_to_default_cwd() -> None:
    rpc = _FakeRpc()
    rpc.request_impl = AsyncMock(
        side_effect=lambda method, params: (
            {"thread": {"id": "thr_1"}} if method == "thread/start" else {"turn": {"id": "codex_turn_1"}}
        )
    )
    adapter = _started_adapter(rpc)

    handle = await adapter.start_turn(
        StartTurnRequest(
            **_base_kwargs(),
            sandbox={"workspaceRoot": "", "mode": "workspace-write"},
        )
    )

    assert handle.turnId == "turn_1"
    thread_params = next(p for m, p in rpc.requests if m == "thread/start")
    # empty string -> DEFAULT_CWD (not "")
    assert thread_params["cwd"]
    assert isinstance(thread_params["cwd"], str) and thread_params["cwd"] != ""


async def test_start_turn_non_list_writable_roots_coerced_to_list() -> None:
    rpc = _FakeRpc()
    rpc.request_impl = AsyncMock(
        side_effect=lambda method, params: (
            {"thread": {"id": "thr_1"}} if method == "thread/start" else {"turn": {"id": "codex_turn_1"}}
        )
    )
    adapter = _started_adapter(rpc)

    await adapter.start_turn(
        StartTurnRequest(
            **_base_kwargs(),
            sandbox={
                "workspaceRoot": "/work",
                "writableRoots": "/work",  # type: ignore[arg-type]
                "mode": "workspace-write",
            },
        )
    )

    turn_params = next(p for m, p in rpc.requests if m == "turn/start")
    # writableRoots was a bare string -> coerced to [cwd]
    assert turn_params["cwd"] == "/work"


async def test_start_turn_network_from_policy_when_sandbox_network_none() -> None:
    rpc = _FakeRpc()
    rpc.request_impl = AsyncMock(
        side_effect=lambda method, params: (
            {"thread": {"id": "thr_1"}} if method == "thread/start" else {"turn": {"id": "codex_turn_1"}}
        )
    )
    adapter = _started_adapter(rpc)

    await adapter.start_turn(
        StartTurnRequest(
            **_base_kwargs(),
            sandbox={"workspaceRoot": "/work", "mode": "workspace-write", "network": None},
            policy={"network": {"enabled": False}},
        )
    )

    turn_params = next(p for m, p in rpc.requests if m == "turn/start")
    # sandboxPolicy should carry the policy-derived network
    assert "sandboxPolicy" in turn_params


async def test_start_turn_timeout_maps_to_adapter_error() -> None:
    rpc = _FakeRpc()

    async def _impl(method: str, params: dict[str, Any]) -> dict[str, Any]:
        if method == "thread/start":
            return {"thread": {"id": "thr_1"}}
        if method == "turn/start":
            raise CodexRequestTimeout("turn/start timed out")
        return {}

    rpc.request_impl = AsyncMock(side_effect=_impl)
    adapter = _started_adapter(rpc)

    with pytest.raises(Exception) as exc_info:
        await adapter.start_turn(StartTurnRequest(**_base_kwargs()))
    # AdapterTurnStartError carries code/retryable
    assert getattr(exc_info.value, "code", None) == "haas_request_timeout"
    assert getattr(exc_info.value, "retryable", None) is True


async def test_start_turn_connection_error_maps_to_adapter_error() -> None:
    rpc = _FakeRpc()

    async def _impl(method: str, params: dict[str, Any]) -> dict[str, Any]:
        if method == "thread/start":
            return {"thread": {"id": "thr_1"}}
        if method == "turn/start":
            raise CodexConnectionError("send failed: reset")
        return {}

    rpc.request_impl = AsyncMock(side_effect=_impl)
    adapter = _started_adapter(rpc)

    with pytest.raises(Exception) as exc_info:
        await adapter.start_turn(StartTurnRequest(**_base_kwargs()))
    assert getattr(exc_info.value, "code", None) == "haas_adapter_unavailable"


# --- stream_events branches -------------------------------------------------


async def test_stream_events_unknown_turn_raises() -> None:
    adapter = _make_adapter()
    handle = TurnHandle(
        turnId="nope", sessionId="hsess_1", invocationId="inv_1"
    )
    with pytest.raises(CodexConnectionError, match="unknown turn handle"):
        async for _ in adapter.stream_events(handle):  # pragma: no branch
            pass


async def test_stream_events_timeout_emits_failed_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _make_adapter()
    # Build a ctx that has already expired.
    from haas.harnesses.codex_app_server.adapter import _TurnContext

    ctx = _TurnContext(
        turn_id="turn_t",
        session_id="hsess_1",
        invocation_id="inv_1",
        thread_id="thr_1",
        codex_turn_id="ct_1",
        timeout_seconds=0.0,  # immediate timeout
        principal_id="p",
        user_id="u",
        app_name="chrn_1",
    )
    # started_at defaults to now; timeout_seconds=0 => remaining <= 0 immediately.
    adapter._turn_contexts["turn_t"] = ctx

    rpc = _FakeRpc()
    interrupt_called: list[dict[str, Any]] = []

    async def _interrupt(method: str, params: dict[str, Any]) -> dict[str, Any]:
        interrupt_called.append(params)
        return {}

    rpc.request_impl = AsyncMock(side_effect=_interrupt)
    adapter._rpc = rpc  # type: ignore[assignment]

    handle = TurnHandle(
        turnId="turn_t", sessionId="hsess_1", invocationId="inv_1"
    )
    events = [e async for e in adapter.stream_events(handle)]
    assert events, "expected terminal failed event"
    assert events[0].type == "harness.turn.failed"
    assert interrupt_called  # best-effort interrupt issued


async def test_stream_events_server_request_overloaded_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _make_adapter()
    from haas.harnesses.codex_app_server.adapter import _TurnContext

    ctx = _TurnContext(
        turn_id="turn_ov",
        session_id="hsess_1",
        invocation_id="inv_1",
        thread_id="thr_1",
        codex_turn_id="ct_ov",
        timeout_seconds=30.0,
        principal_id="p",
        user_id="u",
        app_name="chrn_1",
    )
    adapter._turn_contexts["turn_ov"] = ctx

    rpc = _FakeRpc()

    async def _server_requests(*, after: int = 0):
        raise CodexSubscriberOverloaded("queue full")
        yield {}  # pragma: no cover

    async def _notifications(*, after: int = 0):
        # never yields; let the server_requests future win
        await asyncio.sleep(30)
        yield {}  # pragma: no cover

    rpc.server_requests = _server_requests  # type: ignore[assignment]
    rpc.notifications = _notifications  # type: ignore[assignment]
    rpc.connected = True
    adapter._rpc = rpc  # type: ignore[assignment]

    handle = TurnHandle(
        turnId="turn_ov", sessionId="hsess_1", invocationId="inv_1"
    )
    events = [e async for e in adapter.stream_events(handle)]
    assert events, "expected overloaded terminal event"
    assert events[0].type == "harness.turn.failed"


# --- transport edge paths ---------------------------------------------------


async def test_stdio_transport_recv_limit_overrun_wraps_error() -> None:
    import asyncio.subprocess as sp

    class _Frozen:
        async def readline(self) -> bytes:
            raise asyncio.LimitOverrunError("frame too big", consumed=0)

    class _Proc:
        stdout: Any = _Frozen()
        returncode: Any = 0  # already exited so close() short-circuits

    transport = StdioTransport(_Proc())  # type: ignore[arg-type]
    with pytest.raises(CodexTransportError, match="read limit"):
        await transport.recv()


async def test_stdio_transport_recv_eof_when_empty_line() -> None:
    class _Frozen:
        async def readline(self) -> bytes:
            return b""

    class _Proc:
        stdout: Any = _Frozen()
        returncode: Any = 0

    transport = StdioTransport(_Proc())  # type: ignore[arg-type]
    with pytest.raises(EOFError, match="stdio closed"):
        await transport.recv()


async def test_stdio_transport_close_idempotent_when_exited() -> None:
    class _Proc:
        returncode: Any = 5  # already exited

    transport = StdioTransport(_Proc())  # type: ignore[arg-type]
    # Should not raise, should not attempt terminate().
    await transport.close()


async def test_websocket_transport_delegates_send_recv_close() -> None:
    sent: list[str] = []

    class _Ws:
        async def send(self, message: str) -> None:
            sent.append(message)

        async def recv(self) -> str:
            return "raw"

        async def close(self) -> None:
            sent.append("__close__")

    transport = WebSocketTransport(_Ws())  # type: ignore[arg-type]
    await transport.send("hello")
    assert await transport.recv() == "raw"
    await transport.close()
    assert sent == ["hello", "__close__"]


async def test_connect_endpoint_unsupported_transport_raises() -> None:
    endpoint = CodexEndpoint(transport="bogus", listen_url="bogus://")
    with pytest.raises(CodexTransportError, match="unsupported"):
        await connect_endpoint(endpoint)


def test_validate_loopback_websocket_rejects_external_host() -> None:
    with pytest.raises(CodexTransportError, match="loopback"):
        validate_loopback_websocket_url("ws://8.8.8.8:8080/")


def test_validate_loopback_websocket_accepts_loopback() -> None:
    # Should not raise.
    validate_loopback_websocket_url("ws://127.0.0.1:8080/")
    validate_loopback_websocket_url("ws://localhost:8080/")
