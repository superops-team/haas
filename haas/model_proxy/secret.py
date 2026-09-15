"""Secret resolution for the Model Proxy (specs/model-proxy §8).

The proxy is the only component that resolves provider credential refs into
real keys. Harness never sees the key; it only gets a loopback URL + token.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
from typing import Protocol

from haas.model_proxy.models import ModelRoute, RuntimeTokenScope


class SecretResolutionError(Exception):
    """Raised when a credential ref cannot be resolved."""


class SecretResolver(Protocol):
    async def resolve(self, credential_ref: str) -> str: ...


class LocalCredentialResolver:
    def __init__(self, fd: int) -> None:
        self._socket = socket.socket(fileno=fd)
        os.set_inheritable(fd, False)
        self._socket.setblocking(False)
        self._lock = asyncio.Lock()
        self._sequence = 0
        self.available = True

    async def resolve(self, credential_ref: str) -> str:
        raise SecretResolutionError("credential scope required")

    async def resolve_for(self, route: ModelRoute, scope: RuntimeTokenScope) -> str:
        async with self._lock:
            self._sequence += 1
            request = {
                "sequence": self._sequence,
                "credentialRef": route.credentialRef,
                "harnessId": scope.harnessId,
                "sessionId": scope.sessionId,
                "model": route.model,
                "baseUrl": route.baseUrl,
            }
            loop = asyncio.get_running_loop()
            try:
                async with asyncio.timeout(5):
                    await loop.sock_sendall(self._socket, json.dumps(request).encode() + b"\n")
                    data = bytearray()
                    while not data.endswith(b"\n"):
                        part = await loop.sock_recv(self._socket, 16385 - len(data))
                        if not part or len(data) + len(part) > 16384:
                            raise ValueError("invalid credential frame")
                        data.extend(part)
                    response = json.loads(data)
                    if not isinstance(response, dict) or response.get("sequence") != self._sequence:
                        raise ValueError("invalid credential sequence")
                    value = response.get("value")
                    if not isinstance(value, str) or not value:
                        raise SecretResolutionError("credential unavailable")
                    return value
            except asyncio.CancelledError:
                self.close()
                raise
            except (OSError, ValueError, TimeoutError) as exc:
                self.close()
                raise SecretResolutionError("credential channel unavailable") from exc

    def close(self) -> None:
        self.available = False
        self._socket.close()


class InMemorySecretResolver:
    """Test/local resolver. Production wires a credential vault resolver."""

    def __init__(self, secrets: dict[str, str] | None = None) -> None:
        self._secrets = dict(secrets or {})

    def register(self, credential_ref: str, value: str) -> None:
        self._secrets[credential_ref] = value

    async def resolve(self, credential_ref: str) -> str:
        if credential_ref not in self._secrets:
            raise SecretResolutionError(f"credential ref not found: {credential_ref}")
        return self._secrets[credential_ref]
