"""Short-TTL runtime token issuance/validation (specs/model-proxy §6.2)."""

from __future__ import annotations

import secrets as _secrets
import time
from dataclasses import replace

from haas.model_proxy.models import RuntimeTokenScope


class RuntimeTokenError(Exception):
    """Raised when a runtime token is invalid, expired, or revoked."""


class RuntimeTokenManager:
    def __init__(self, ttl_seconds: int = 3600) -> None:
        self._ttl_seconds = ttl_seconds
        self._tokens: dict[str, RuntimeTokenScope] = {}

    @property
    def ttl_ms(self) -> int:
        return self._ttl_seconds * 1000

    def issue(self, scope: RuntimeTokenScope, *, expires_at_ms: int | None = None) -> str:
        token = _secrets.token_urlsafe(32)
        scope.expiresAtMs = (
            expires_at_ms
            if expires_at_ms is not None
            else int(time.time() * 1000) + self._ttl_seconds * 1000
        )
        self._tokens[token] = scope
        return token

    def validate(self, token: str) -> RuntimeTokenScope:
        scope = self.scope(token, allow_expired=True)
        if scope.expiresAtMs and int(time.time() * 1000) > scope.expiresAtMs:
            raise RuntimeTokenError("token_expired")
        return scope

    def scope(self, token: str, *, allow_expired: bool = False) -> RuntimeTokenScope:
        scope = self._tokens.get(token)
        if scope is None:
            raise RuntimeTokenError("invalid_token")
        if not allow_expired and scope.expiresAtMs and int(time.time() * 1000) > scope.expiresAtMs:
            raise RuntimeTokenError("token_expired")
        return scope

    def refresh(self, token: str, *, expires_at_ms: int | None = None) -> str:
        scope = self.scope(token, allow_expired=True)
        replacement = replace(scope, allowedModels=list(scope.allowedModels))
        return self.issue(replacement, expires_at_ms=expires_at_ms)

    def revoke(self, token: str) -> None:
        self._tokens.pop(token, None)

    def revoke_invocation(self, session_id: str, invocation_id: str) -> None:
        for token, scope in list(self._tokens.items()):
            if (scope.sessionId, scope.invocationId) == (session_id, invocation_id):
                self._tokens.pop(token, None)

    def revoke_all(self) -> None:
        self._tokens.clear()
