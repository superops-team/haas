"""Secret resolution for the Model Proxy (specs/model-proxy §8).

The proxy is the only component that resolves provider credential refs into
real keys. Harness never sees the key; it only gets a loopback URL + token.
"""
from __future__ import annotations

from typing import Protocol


class SecretResolutionError(Exception):
    """Raised when a credential ref cannot be resolved."""


class SecretResolver(Protocol):
    async def resolve(self, credential_ref: str) -> str: ...


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
