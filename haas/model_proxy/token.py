"""Short-TTL runtime token issuance/validation (specs/model-proxy §6.2)."""

from __future__ import annotations

import base64
import binascii
import hashlib
import secrets as _secrets
import time
from dataclasses import replace

from haas.model_proxy.models import RuntimeTokenScope


class RuntimeTokenError(Exception):
    """Raised when a runtime token is invalid, expired, or revoked."""


MODEL_PROXY_TOKEN_PREFIX = "haas_mp_"


class RuntimeTokenManager:
    def __init__(self, ttl_seconds: int = 3600) -> None:
        self._ttl_seconds = ttl_seconds
        self._tokens: dict[str, RuntimeTokenScope] = {}

    @property
    def ttl_ms(self) -> int:
        return self._ttl_seconds * 1000

    def issue(self, scope: RuntimeTokenScope, *, expires_at_ms: int | None = None) -> str:
        if not scope.issuedAtMs:
            scope.issuedAtMs = int(time.time() * 1000)
        token = self._token_for(scope)
        scope.expiresAtMs = (
            expires_at_ms
            if expires_at_ms is not None
            else int(time.time() * 1000) + self._ttl_seconds * 1000
        )
        self._tokens[token] = scope
        return token

    @staticmethod
    def _token_for(scope: RuntimeTokenScope) -> str:
        session = base64.urlsafe_b64encode(scope.sessionId.encode()).decode().rstrip("=")
        generation = scope.generation or 1
        nonce = _secrets.token_urlsafe(24)
        return f"{MODEL_PROXY_TOKEN_PREFIX}{session}.{generation}.{nonce}"

    @staticmethod
    def fingerprint(token: str) -> str:
        return "sha256:" + hashlib.sha256(token.encode()).hexdigest()

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

    def renew(self, token: str, *, expires_at_ms: int | None = None) -> RuntimeTokenScope:
        scope = self.scope(token, allow_expired=True)
        scope.expiresAtMs = (
            expires_at_ms
            if expires_at_ms is not None
            else int(time.time() * 1000) + self._ttl_seconds * 1000
        )
        return scope

    def adopt(
        self,
        token: str,
        scope: RuntimeTokenScope,
        *,
        expires_at_ms: int | None = None,
    ) -> None:
        if not token.startswith(MODEL_PROXY_TOKEN_PREFIX):
            raise RuntimeTokenError("invalid_token")
        session_id = self.token_session_id(token)
        if session_id != scope.sessionId:
            raise RuntimeTokenError("invalid_token")
        if not scope.issuedAtMs:
            scope.issuedAtMs = int(time.time() * 1000)
        scope.expiresAtMs = (
            expires_at_ms
            if expires_at_ms is not None
            else int(time.time() * 1000) + self._ttl_seconds * 1000
        )
        self._tokens[token] = scope

    def revoke(self, token: str) -> None:
        self._tokens.pop(token, None)

    def revoke_invocation(self, session_id: str, invocation_id: str) -> None:
        for token, scope in list(self._tokens.items()):
            if (scope.sessionId, scope.invocationId) == (session_id, invocation_id):
                self._tokens.pop(token, None)

    def revoke_session(self, session_id: str) -> None:
        for token, scope in list(self._tokens.items()):
            if scope.sessionId == session_id:
                self._tokens.pop(token, None)

    def token_session_id(self, token: str) -> str | None:
        if not token.startswith(MODEL_PROXY_TOKEN_PREFIX):
            return None
        rest = token.removeprefix(MODEL_PROXY_TOKEN_PREFIX)
        encoded_session_id, sep, _suffix = rest.partition(".")
        if not sep or not encoded_session_id or any(
            char.isspace() for char in encoded_session_id
        ):
            return None
        padding = "=" * (-len(encoded_session_id) % 4)
        try:
            decoded = base64.b64decode(
                encoded_session_id + padding,
                altchars=b"-_",
                validate=True,
            )
            session_id = decoded.decode()
        except (binascii.Error, UnicodeDecodeError):
            return None
        if not session_id or any(char.isspace() for char in session_id):
            return None
        return session_id

    def revoke_all(self) -> None:
        self._tokens.clear()
