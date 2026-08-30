"""Model Proxy request relay (specs/model-proxy §5/§8).

The proxy authenticates a runtime token, resolves the provider credential,
validates the provider URL against policy (SSRF/allowlist), forwards the
request, and normalizes usage. Credentials never leave this component.
"""
from __future__ import annotations

from typing import Any

import httpx

from haas.model_proxy.models import ModelRoute, RuntimeTokenScope, Usage
from haas.model_proxy.route import normalize_usage
from haas.model_proxy.secret import SecretResolver
from haas.model_proxy.token import RuntimeTokenError, RuntimeTokenManager
from haas.policy import EffectivePolicy, PolicyController, PolicyDecision
from haas.security.redact import safe_upstream_body


class ModelProxyError(Exception):
    """Raised when a model proxy request is rejected or fails."""


class ModelProxy:
    def __init__(
        self,
        resolver: SecretResolver,
        tokens: RuntimeTokenManager,
        policy: PolicyController,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._resolver = resolver
        self._tokens = tokens
        self._policy = policy
        self._client = client
        self._owns_client = client is None
        self._active_client: httpx.AsyncClient | None = None

    async def authenticate(self, authorization: str) -> RuntimeTokenScope:
        token = authorization.removeprefix("Bearer ").strip() if authorization else ""
        try:
            return self._tokens.validate(token)
        except RuntimeTokenError as exc:
            raise ModelProxyError(str(exc)) from exc

    async def proxy_responses(
        self,
        route: ModelRoute,
        body: dict[str, Any],
        *,
        authorization: str,
        policy: EffectivePolicy,
    ) -> tuple[dict[str, Any], Usage | None]:
        scope = await self.authenticate(authorization)
        self._authorize_model(scope, route.model)
        self._authorize_url(policy, route.baseUrl)

        credential = await self._resolver.resolve(route.credentialRef)
        headers = {"Authorization": f"Bearer {credential}", "Content-Type": "application/json"}

        client = await self._client_ctx()
        resp = await client.post(
            f"{route.baseUrl.rstrip('/')}/responses", json=body, headers=headers
        )
        if resp.status_code >= 400:
            # The provider body may echo our injected Authorization header
            # or other credentials, and this message reaches callers and
            # logs, so it is a secret surface (AGENTS.md 铁律 7).
            raise ModelProxyError(
                f"provider HTTP {resp.status_code}: {safe_upstream_body(resp.text)}"
            )
        try:
            data = resp.json()
        except ValueError as exc:
            raise ModelProxyError("provider returned non-JSON response") from exc
        usage = normalize_usage(route.provider, data)
        return data, usage

    async def close(self) -> None:
        """Release the lazily created client; an injected one is the caller's."""
        # _client is the injected instance (never ours to close); the
        # lazily created one lives in _active_client.
        if self._active_client is not None:
            await self._active_client.aclose()
            self._active_client = None

    async def _client_ctx(self) -> httpx.AsyncClient:
        if self._client is not None:
            return self._client
        if self._active_client is None:
            self._active_client = httpx.AsyncClient()
        return self._active_client

    def _authorize_model(self, scope: RuntimeTokenScope, model: str) -> None:
        if scope.allowedModels and model not in scope.allowedModels:
            raise ModelProxyError("model_not_allowed")

    def _authorize_url(self, policy: EffectivePolicy, url: str) -> None:
        decision: PolicyDecision = self._policy.authorize_network(policy, url)
        if not decision.allowed:
            raise ModelProxyError(f"provider_url_not_allowed: {decision.safeReason}")
