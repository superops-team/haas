"""Codex app-server transport endpoints (specs/codex-app-server-adapter §5.1).

Supports three transports behind one async interface:

- ``unix_websocket``: WebSocket-over-Unix-socket (production default).
- ``loopback_websocket``: loopback WebSocket for local debug / in-container.
- ``stdio``: subprocess with newline-delimited JSON (NDJSON) over
  stdin/stdout, for tests and minimal smoke.
"""
from __future__ import annotations

import asyncio
import contextlib
import os
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol
from urllib.parse import urlparse

from websockets.asyncio.client import ClientConnection, connect, unix_connect

# Codex app-server performs a standard WebSocket HTTP Upgrade over the Unix
# socket; this is the handshake URI it expects for that path.
UDS_WEBSOCKET_HANDSHAKE_URL = "ws://localhost/rpc"
WEBSOCKET_MAX_MESSAGE_SIZE = 128 << 20  # 128 MiB
DEFAULT_OPEN_TIMEOUT = 30.0
LOOPBACK_WEBSOCKET_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
CODEX_APP_SERVER_ENV_ALLOWLIST = frozenset(
    {
        "PATH",
        "HOME",
        "TMPDIR",
        "TMP",
        "TEMP",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "LC_MESSAGES",
    }
)


@dataclass
class CodexEndpoint:
    """Transport endpoint for a Codex app-server listener."""

    transport: str  # unix_websocket | loopback_websocket | stdio
    listen_url: str  # unix://PATH | ws://IP:PORT | stdio://
    auth: dict[str, Any] = field(default_factory=dict)
    schema_version: str | None = None


class CodexTransportError(Exception):
    """Raised when a Codex app-server transport cannot be established or is misconfigured."""


class CodexTransport(Protocol):
    """Minimal async message transport (send text, receive text/bytes, close)."""

    async def send(self, message: str) -> None: ...

    async def recv(self) -> Any: ...

    async def close(self) -> None: ...


def unix_socket_path(listen_url: str) -> str:
    """Extract the filesystem path from a ``unix://PATH`` URL."""
    prefix = "unix://"
    if not listen_url.startswith(prefix):
        raise CodexTransportError(f"invalid unix listen URL: {listen_url!r}")
    path = listen_url[len(prefix):]
    if not path:
        raise CodexTransportError("empty unix socket path")
    return path


def codex_app_server_env(source: Mapping[str, str] | None = None) -> dict[str, str]:
    """Build the explicit secretless environment for Codex app-server.

    This is an allowlist by design: Codex may inherit only process basics
    needed to locate the executable (PATH), resolve its isolated home (HOME),
    create temporary files (TMPDIR/TMP/TEMP), and keep locale/Unicode behavior
    stable (LANG/LC_*). Provider keys, cloud credentials, tokens, passwords,
    cookies, and similar credential variables are never passed through.
    """
    if source is None:
        source = os.environ
    env = {
        key: source[key]
        for key in CODEX_APP_SERVER_ENV_ALLOWLIST
        if key in source
    }
    env.setdefault("PATH", os.defpath)
    return env


def validate_loopback_websocket_url(listen_url: str) -> None:
    """Fail closed unless a loopback WebSocket endpoint targets loopback only."""
    parsed = urlparse(listen_url)
    host = parsed.hostname
    if parsed.scheme not in {"ws", "wss"} or host not in LOOPBACK_WEBSOCKET_HOSTS:
        raise CodexTransportError(
            "loopback_websocket listen_url must target 127.0.0.1, ::1, or localhost"
        )


class WebSocketTransport:
    """CodexTransport wrapper around a websockets ClientConnection."""

    def __init__(self, ws: ClientConnection) -> None:
        self._ws = ws

    async def send(self, message: str) -> None:
        await self._ws.send(message)

    async def recv(self) -> Any:
        return await self._ws.recv()

    async def close(self) -> None:
        await self._ws.close()


class StdioTransport:
    """CodexTransport over a ``codex app-server --listen stdio://`` subprocess (NDJSON)."""

    def __init__(self, proc: asyncio.subprocess.Process) -> None:
        self._proc = proc

    @classmethod
    async def start(cls, codex_bin: str) -> StdioTransport:
        proc = await asyncio.create_subprocess_exec(
            codex_bin,
            "app-server",
            "--listen",
            "stdio://",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=codex_app_server_env(),
        )
        return cls(proc)

    async def send(self, message: str) -> None:
        assert self._proc.stdin is not None
        self._proc.stdin.write((message + "\n").encode("utf-8"))
        await self._proc.stdin.drain()

    async def recv(self) -> Any:
        assert self._proc.stdout is not None
        line = await self._proc.stdout.readline()
        if not line:
            raise EOFError("codex app-server stdio closed")
        return line

    async def close(self) -> None:
        if self._proc.returncode is not None:
            return  # process already exited
        self._proc.terminate()
        with contextlib.suppress(ProcessLookupError, TimeoutError):
            await asyncio.wait_for(self._proc.wait(), timeout=5)
        if self._proc.returncode is None:
            self._proc.kill()
            with contextlib.suppress(ProcessLookupError):
                await self._proc.wait()


async def _connect_websocket(
    endpoint: CodexEndpoint, *, open_timeout: float
) -> WebSocketTransport:
    if endpoint.transport == "unix_websocket":
        path = unix_socket_path(endpoint.listen_url)
        ws = await unix_connect(
            path=path,
            uri=UDS_WEBSOCKET_HANDSHAKE_URL,
            max_size=WEBSOCKET_MAX_MESSAGE_SIZE,
            compression=None,
            open_timeout=open_timeout,
        )
    elif endpoint.transport == "loopback_websocket":
        validate_loopback_websocket_url(endpoint.listen_url)
        ws = await connect(
            endpoint.listen_url,
            max_size=WEBSOCKET_MAX_MESSAGE_SIZE,
            compression=None,
            open_timeout=open_timeout,
        )
    else:
        raise CodexTransportError(f"unsupported websocket transport: {endpoint.transport!r}")
    return WebSocketTransport(ws)


async def connect_endpoint(
    endpoint: CodexEndpoint,
    *,
    codex_bin: str = "codex",
    open_timeout: float = DEFAULT_OPEN_TIMEOUT,
) -> CodexTransport:
    """Open a transport for the given endpoint."""
    if endpoint.transport in {"unix_websocket", "loopback_websocket"}:
        return await _connect_websocket(endpoint, open_timeout=open_timeout)
    if endpoint.transport == "stdio":
        return await StdioTransport.start(codex_bin)
    raise CodexTransportError(f"unsupported transport: {endpoint.transport!r}")
