"""Tests for Codex app-server transport + JSON-RPC client.

Offline unit tests cover URL parsing, message decoding and inbound message
classification. Loopback integration tests exercise the full JSON-RPC
handshake against an in-process fake app-server (no real Codex, no external
network).
"""
from __future__ import annotations

import asyncio
import contextlib
import json
from pathlib import Path
from typing import Any

import pytest

from haas.harnesses.codex_app_server.rpc import (
    CodexConnectionError,
    CodexJsonRpc,
    classify_message,
    decode_message,
    message_id,
)
from haas.harnesses.codex_app_server.transport import (
    CodexEndpoint,
    CodexTransportError,
    unix_socket_path,
)

# --- offline: URL parsing --------------------------------------------------


def test_unix_socket_path_valid() -> None:
    assert unix_socket_path("unix:///tmp/haas/codex.sock") == "/tmp/haas/codex.sock"


def test_unix_socket_path_rejects_wrong_scheme() -> None:
    with pytest.raises(CodexTransportError, match="unix"):
        unix_socket_path("ws://127.0.0.1:9000")


def test_unix_socket_path_rejects_empty() -> None:
    with pytest.raises(CodexTransportError, match="empty"):
        unix_socket_path("unix://")


# --- offline: message decoding ---------------------------------------------


def test_decode_message_str() -> None:
    assert decode_message('{"method":"x","params":{}}') == {"method": "x", "params": {}}


def test_decode_message_bytes() -> None:
    assert decode_message(b'{"method":"x"}') == {"method": "x"}


def test_decode_message_skips_non_json() -> None:
    assert decode_message("not-json") is None


def test_decode_message_skips_non_object() -> None:
    assert decode_message("[1,2,3]") is None


def test_message_id_absent() -> None:
    assert message_id({"method": "x"}) is None


def test_message_id_present() -> None:
    assert message_id({"id": 7, "method": "x"}) == 7


# --- offline: inbound classification ---------------------------------------


def test_classify_response() -> None:
    assert classify_message({"id": 1, "result": {}}, {1}) == "response"


def test_classify_server_request() -> None:
    assert classify_message({"id": 9, "method": "approval"}, {1}) == "server_request"


def test_classify_notification() -> None:
    assert classify_message({"method": "turn/completed", "params": {}}, set()) == "notification"


def test_classify_invalid() -> None:
    assert classify_message({"id": 2, "result": {}}, {1}) == "invalid"
    assert classify_message({}, set()) == "invalid"


# --- loopback integration --------------------------------------------------


@pytest.mark.integration
async def test_codex_json_rpc_handshake_and_request() -> None:
    async def handler(ws: Any) -> None:
        async for raw in ws:
            msg = json.loads(raw)
            if msg.get("method") == "initialize":
                await ws.send(
                    json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": {"ok": True}})
                )
            elif msg.get("method") == "initialized":
                continue
            elif msg.get("method") == "model/list":
                await ws.send(
                    json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": {"models": []}})
                )
            else:
                await ws.send(
                    json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": msg.get("id"),
                            "error": {"code": -32601, "message": "method not found"},
                        }
                    )
                )

    from websockets.asyncio.server import serve

    async with serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        endpoint = CodexEndpoint(
            transport="loopback_websocket",
            listen_url=f"ws://127.0.0.1:{port}",
        )
        rpc = CodexJsonRpc(endpoint)
        await rpc.connect()
        try:
            assert rpc.initialized is True
            result = await rpc.request("model/list", {})
            assert result == {"models": []}
        finally:
            await rpc.close()


@pytest.mark.integration
async def test_codex_json_rpc_notification_and_server_request() -> None:
    async def handler(ws: Any) -> None:
        async for raw in ws:
            msg = json.loads(raw)
            if msg.get("method") == "initialize":
                await ws.send(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": {}}))
            elif msg.get("method") == "initialized":
                # Emit a notification and a server request after the handshake.
                notification = {
                    "jsonrpc": "2.0",
                    "method": "turn/completed",
                    "params": {"turn": {"id": "t1"}},
                }
                await ws.send(json.dumps(notification))
                server_request = {
                    "jsonrpc": "2.0",
                    "id": 100,
                    "method": "item/commandExecution/requestApproval",
                    "params": {},
                }
                await ws.send(json.dumps(server_request))
            else:
                await ws.send(json.dumps({"jsonrpc": "2.0", "id": msg.get("id"), "result": {}}))

    from websockets.asyncio.server import serve

    async with serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        endpoint = CodexEndpoint(transport="loopback_websocket", listen_url=f"ws://127.0.0.1:{port}")
        rpc = CodexJsonRpc(endpoint)
        await rpc.connect()
        try:
            notification = await asyncio.wait_for(anext(rpc.notifications()), timeout=5)
            assert notification["method"] == "turn/completed"
            server_request = await asyncio.wait_for(anext(rpc.server_requests()), timeout=5)
            assert server_request["method"] == "item/commandExecution/requestApproval"
            assert server_request["id"] == 100
        finally:
            await rpc.close()


@pytest.mark.integration
async def test_codex_json_rpc_unknown_method_error() -> None:
    async def handler(ws: Any) -> None:
        async for raw in ws:
            msg = json.loads(raw)
            if msg.get("method") == "initialize":
                await ws.send(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": {}}))
            else:
                await ws.send(
                    json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": msg.get("id"),
                            "error": {"code": -32601, "message": "method not found"},
                        }
                    )
                )

    from websockets.asyncio.server import serve

    async with serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        endpoint = CodexEndpoint(transport="loopback_websocket", listen_url=f"ws://127.0.0.1:{port}")
        rpc = CodexJsonRpc(endpoint)
        await rpc.connect()
        try:
            with pytest.raises(CodexConnectionError, match="method not found"):
                await rpc.request("bogus/method", {})
        finally:
            await rpc.close()


@pytest.mark.integration
async def test_codex_json_rpc_unix_websocket() -> None:
    async def handler(ws: Any) -> None:
        async for raw in ws:
            msg = json.loads(raw)
            if msg.get("method") == "initialize":
                await ws.send(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": {}}))
            elif msg.get("method") == "initialized":
                continue
            else:
                await ws.send(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": {}}))

    import os
    import tempfile

    from websockets.asyncio.server import unix_serve

    # macOS limits AF_UNIX path length; keep the path short.
    sock_path = Path(tempfile.gettempdir()) / f"haas_codex_{os.getpid()}.sock"
    try:
        async with unix_serve(handler, str(sock_path)):
            endpoint = CodexEndpoint(transport="unix_websocket", listen_url=f"unix://{sock_path}")
            rpc = CodexJsonRpc(endpoint)
            await rpc.connect()
            try:
                assert rpc.initialized is True
                result = await rpc.request("model/list", {})
                assert isinstance(result, dict)
            finally:
                await rpc.close()
    finally:
        with contextlib.suppress(OSError):
            sock_path.unlink()


def test_request_before_connect_raises_not_ready() -> None:
    from haas.harnesses.codex_app_server.rpc import CodexNotReadyError

    rpc = CodexJsonRpc(CodexEndpoint(transport="loopback_websocket", listen_url="ws://127.0.0.1:1"))
    with pytest.raises(CodexNotReadyError):
        _ = asyncio.run(rpc.request("model/list", {}))


@pytest.mark.integration
async def test_codex_json_rpc_request_timeout() -> None:
    async def handler(ws: Any) -> None:
        async for raw in ws:
            msg = json.loads(raw)
            if msg.get("method") == "initialize":
                await ws.send(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": {}}))
            # deliberately do not respond to any other request

    from websockets.asyncio.server import serve

    from haas.harnesses.codex_app_server.rpc import CodexRequestTimeout

    async with serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        rpc = CodexJsonRpc(
            CodexEndpoint(transport="loopback_websocket", listen_url=f"ws://127.0.0.1:{port}"),
            request_timeout=1.0,
        )
        await rpc.connect()
        try:
            with pytest.raises(CodexRequestTimeout):
                await rpc.request("model/list", {})
        finally:
            await rpc.close()
