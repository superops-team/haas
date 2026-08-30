"""JSON-RPC 2.0 client for Codex app-server (specs/codex-app-server-adapter §5.2).

One :class:`CodexJsonRpc` instance owns one transport connection. The
connection is initialized exactly once (``initialize`` request followed by an
``initialized`` notification) before any thread/turn method may be called.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator
from typing import Any

from websockets.exceptions import ConnectionClosed, WebSocketException

from haas.harnesses.codex_app_server.transport import (
    CodexEndpoint,
    CodexTransport,
    connect_endpoint,
)

JSONRPC_VERSION = "2.0"
DEFAULT_REQUEST_TIMEOUT = 60.0
DEFAULT_NOTIFY_QUEUE_SIZE = 1000

JsonObject = dict[str, Any]


class CodexConnectionError(Exception):
    """Raised when the JSON-RPC connection to Codex app-server fails."""


class CodexNotReadyError(CodexConnectionError):
    """Raised when a request is attempted before connect/initialize completes."""


class CodexRequestTimeout(CodexConnectionError):
    """Raised when a JSON-RPC request receives no response within its timeout."""


def message_id(msg: JsonObject) -> Any:
    """Return the JSON-RPC ``id`` value, if present."""
    return msg.get("id")


def classify_message(msg: JsonObject, pending_ids: set[Any]) -> str:
    """Classify an inbound JSON-RPC message.

    Returns one of ``response``, ``server_request``, ``notification``, or
    ``invalid``.
    """
    msg_id = message_id(msg)
    if msg_id is not None:
        if msg_id in pending_ids:
            return "response"
        if "method" in msg:
            return "server_request"
        return "invalid"
    if "method" in msg:
        return "notification"
    return "invalid"


def decode_message(raw: Any) -> JsonObject | None:
    """Decode a transport frame into a JSON-RPC object.

    Returns ``None`` for non-JSON or non-object payloads (best-effort skip).
    """
    if isinstance(raw, str):
        text = raw
    elif isinstance(raw, (bytes, bytearray)):
        text = bytes(raw).decode("utf-8", errors="replace")
    else:
        text = str(raw)
    text = text.strip()
    if not text:
        return None
    try:
        decoded = json.loads(text)
    except json.JSONDecodeError:
        return None
    return decoded if isinstance(decoded, dict) else None


class CodexJsonRpc:
    """Bidirectional JSON-RPC 2.0 client bound to one Codex app-server connection."""

    def __init__(
        self,
        endpoint: CodexEndpoint,
        *,
        codex_bin: str = "codex",
        request_timeout: float = DEFAULT_REQUEST_TIMEOUT,
        notify_queue_size: int = DEFAULT_NOTIFY_QUEUE_SIZE,
    ) -> None:
        self._endpoint = endpoint
        self._codex_bin = codex_bin
        self._request_timeout = request_timeout
        self._transport: CodexTransport | None = None
        self._request_id = 0
        self._connected = False
        self._initialized = False
        self._pending_requests: dict[Any, asyncio.Future[JsonObject]] = {}
        self._notifications: asyncio.Queue[JsonObject] = asyncio.Queue(maxsize=notify_queue_size)
        self._server_requests: asyncio.Queue[JsonObject] = asyncio.Queue(maxsize=notify_queue_size)
        self._notification_task: asyncio.Task[None] | None = None

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def initialized(self) -> bool:
        return self._initialized

    async def connect(self) -> None:
        """Connect and run the initialize/initialized handshake."""
        if self._connected and self._initialized:
            return
        await self.close()

        self._transport = await connect_endpoint(self._endpoint, codex_bin=self._codex_bin)
        self._connected = True
        self._notification_task = asyncio.create_task(self._notification_loop())
        try:
            await self._handshake()
        except BaseException:
            await self.close()
            raise

    async def _handshake(self) -> None:
        await self.request(
            "initialize",
            {
                "clientInfo": {"name": "haas", "version": "0.1.0"},
                "capabilities": {"streaming": True},
            },
        )
        await self.notify("initialized", {})
        self._initialized = True

    async def close(self) -> None:
        """Close the connection and release all pending resources."""
        self._connected = False
        self._initialized = False
        if self._notification_task is not None:
            self._notification_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, asyncio.TimeoutError):
                await asyncio.wait_for(self._notification_task, timeout=2)
            self._notification_task = None
        if self._transport is not None:
            with contextlib.suppress(Exception):
                await self._transport.close()
        self._transport = None
        for future in self._pending_requests.values():
            if not future.done():
                future.set_exception(CodexConnectionError("connection closed"))
        self._pending_requests.clear()

    def _next_request_id(self) -> int:
        self._request_id += 1
        return self._request_id

    async def request(self, method: str, params: JsonObject) -> JsonObject:
        """Send a JSON-RPC request and await its ``result``."""
        if not self._connected or self._transport is None:
            raise CodexNotReadyError("codex app-server is not connected")
        req_id = self._next_request_id()
        future: asyncio.Future[JsonObject] = asyncio.get_running_loop().create_future()
        self._pending_requests[req_id] = future
        message = json.dumps(
            {"jsonrpc": JSONRPC_VERSION, "id": req_id, "method": method, "params": params}
        )
        try:
            await self._transport.send(message)
        except (OSError, ConnectionError, WebSocketException, EOFError) as exc:
            # The future is dropped before anyone awaits it, so do not set an
            # exception on it: that would only surface as a spurious
            # "Future exception was never retrieved" warning. The caller gets
            # the failure from the raise below.
            self._pending_requests.pop(req_id, None)
            future.cancel()
            self._connected = False
            raise CodexConnectionError(f"send failed: {exc}") from exc

        try:
            async with asyncio.timeout(self._request_timeout):
                response = await future
        except TimeoutError:
            self._pending_requests.pop(req_id, None)
            raise CodexRequestTimeout(f"request {method} timed out") from None

        if "error" in response:
            error = response["error"]
            if isinstance(error, dict):
                msg = str(error.get("message", "unknown error"))
            else:
                msg = str(error)
            raise CodexConnectionError(f"JSON-RPC error ({method}): {msg}")
        result = response.get("result", {})
        return result if isinstance(result, dict) else {}

    async def notify(self, method: str, params: JsonObject) -> None:
        """Send a JSON-RPC notification (no id, no response expected)."""
        if not self._connected or self._transport is None:
            raise CodexNotReadyError("codex app-server is not connected")
        message = json.dumps({"jsonrpc": JSONRPC_VERSION, "method": method, "params": params})
        try:
            await self._transport.send(message)
        except (OSError, ConnectionError, WebSocketException, EOFError) as exc:
            self._connected = False
            raise CodexConnectionError(f"send notification failed: {exc}") from exc

    def notifications(self) -> AsyncIterator[JsonObject]:
        """Iterate inbound server notifications."""
        return self._queue_iterator(self._notifications)

    def server_requests(self) -> AsyncIterator[JsonObject]:
        """Iterate inbound server-initiated requests (e.g. approval requests)."""
        return self._queue_iterator(self._server_requests)

    async def _queue_iterator(self, queue: asyncio.Queue[JsonObject]) -> AsyncIterator[JsonObject]:
        while True:
            yield await queue.get()

    async def _notification_loop(self) -> None:
        assert self._transport is not None
        try:
            while self._connected:
                try:
                    raw = await self._transport.recv()
                except (ConnectionClosed, ConnectionResetError, BrokenPipeError, OSError, EOFError):
                    break
                msg = decode_message(raw)
                if msg is None:
                    continue
                kind = classify_message(msg, set(self._pending_requests.keys()))
                if kind == "response":
                    req_id = message_id(msg)
                    future = self._pending_requests.pop(req_id, None)
                    if future is not None and not future.done():
                        future.set_result(msg)
                elif kind == "server_request":
                    await self._server_requests.put(msg)
                elif kind == "notification":
                    await self._notifications.put(msg)
                # "invalid" is silently dropped (best effort).
        except asyncio.CancelledError:
            pass
        except (TimeoutError, OSError, ConnectionError, WebSocketException, EOFError):
            self._connected = False
        finally:
            self._connected = False
            for future in self._pending_requests.values():
                if not future.done():
                    future.set_exception(CodexConnectionError("connection lost"))
            self._pending_requests.clear()
