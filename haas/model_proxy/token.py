"""Short-TTL runtime token issuance/validation (specs/model-proxy §6.2)."""
from __future__ import annotations

import secrets as _secrets
import time

from haas.model_proxy.models import RuntimeTokenScope


class RuntimeTokenError(Exception):
    """Raised when a runtime token is invalid, expired, or revoked."""


class RuntimeTokenManager:
    def __init__(self, ttl_seconds: int = 3600) -> None:
        self._ttl_seconds = ttl_seconds
        self._tokens: dict[str, RuntimeTokenScope] = {}

    def issue(self, scope: RuntimeTokenScope) -> str:
        token = _secrets.token_urlsafe(32)
        scope.expiresAtMs = int(time.time() * 1000) + self._ttl_seconds * 1000
        self._tokens[token] = scope
        return token

    def validate(self, token: str) -> RuntimeTokenScope:
        scope = self._tokens.get(token)
        if scope is None:
            raise RuntimeTokenError("invalid_token")
        if scope.expiresAtMs and int(time.time() * 1000) > scope.expiresAtMs:
            self._tokens.pop(token, None)
            raise RuntimeTokenError("token_expired")
        return scope

    def revoke(self, token: str) -> None:
        self._tokens.pop(token, None)
