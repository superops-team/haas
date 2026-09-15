"""Model Proxy request relay (specs/model-proxy §5/§8).

The proxy authenticates a runtime token, resolves the provider credential,
validates the provider URL against policy (SSRF/allowlist), forwards the
request, and normalizes usage. Credentials never leave this component.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import urlsplit

import httpx

from haas.model_proxy.models import ModelRoute, RuntimeTokenScope, Usage
from haas.model_proxy.route import normalize_usage
from haas.model_proxy.secret import SecretResolver
from haas.model_proxy.token import RuntimeTokenError, RuntimeTokenManager
from haas.policy import EffectivePolicy, PolicyController, PolicyDecision
from haas.security.redact import safe_upstream_body

_PROVIDER_ERROR_FIELDS = ("code", "type", "param", "message")


class ModelProxyError(Exception):
    """Raised when a model proxy request is rejected or fails."""


def _provider_http_error(response: httpx.Response, credential: str) -> ModelProxyError:
    """Build a bounded diagnostic without relaying an arbitrary provider body."""
    prefix = f"provider HTTP {response.status_code}"
    try:
        payload = response.json()
    except (ValueError, RuntimeError):
        return ModelProxyError(prefix)
    if not isinstance(payload, dict):
        return ModelProxyError(prefix)

    raw_error = payload.get("error")
    if isinstance(raw_error, str):
        diagnostic: dict[str, str] = {"message": raw_error}
    elif isinstance(raw_error, dict):
        diagnostic = {
            field: str(raw_error[field])
            for field in _PROVIDER_ERROR_FIELDS
            if raw_error.get(field) is not None
            and isinstance(raw_error[field], (str, int, float, bool))
        }
    else:
        return ModelProxyError(prefix)
    if not diagnostic:
        return ModelProxyError(prefix)

    encoded = json.dumps(diagnostic, ensure_ascii=False, separators=(",", ":"))
    if credential:
        encoded = encoded.replace(credential, "[REDACTED]")
    return ModelProxyError(f"{prefix}: {safe_upstream_body(encoded)}")


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
            scope = self._tokens.validate(token)
            if scope.audience != "model_proxy":
                raise ModelProxyError("invalid_credential")
            return scope
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

        credential = await self._credential(route, scope)
        headers = {"Authorization": f"Bearer {credential}", "Content-Type": "application/json"}

        client = await self._client_ctx()
        try:
            resp = await client.post(
                f"{route.baseUrl.rstrip('/')}/responses",
                json=self._responses_body(route, body),
                headers=headers,
                follow_redirects=False,
                timeout=route.timeoutMs / 1000,
            )
        except httpx.HTTPError as exc:
            raise ModelProxyError("provider_unavailable") from exc
        if resp.status_code >= 300:
            raise _provider_http_error(resp, credential)
        try:
            data = resp.json()
        except ValueError as exc:
            raise ModelProxyError("provider returned non-JSON response") from exc
        if not isinstance(data, dict):
            raise ModelProxyError("provider returned invalid JSON object")
        data = json.loads(json.dumps(data).replace(credential, "[REDACTED]"))
        usage = normalize_usage(route.provider, data)
        return data, usage

    @staticmethod
    def _responses_body(route: ModelRoute, body: dict[str, Any]) -> dict[str, Any]:
        payload = {**body, "model": route.model}
        if route.providerId == "volcengine-ark":
            reasoning = payload.get("reasoning")
            if isinstance(reasoning, dict) and "summary" in reasoning:
                payload["reasoning"] = {
                    key: value for key, value in reasoning.items() if key != "summary"
                }
        items = payload.get("input")
        if route.providerId == "volcengine-ark" and isinstance(items, list):
            payload["input"] = [
                {**item, "status": "completed"}
                if isinstance(item, dict)
                and "status" not in item
                and (
                    item.get("type") == "reasoning"
                    or (item.get("type") == "message" and item.get("role") == "assistant")
                )
                else item
                for item in items
            ]
        return payload

    async def _credential(self, route: ModelRoute, scope: RuntimeTokenScope) -> str:
        resolve_for = getattr(self._resolver, "resolve_for", None)
        if resolve_for is not None:
            return str(await resolve_for(route, scope))
        return await self._resolver.resolve(route.credentialRef)

    async def stream_responses(
        self,
        route: ModelRoute,
        body: dict[str, Any],
        *,
        authorization: str,
        policy: EffectivePolicy,
    ) -> httpx.Response:
        scope = await self.authenticate(authorization)
        self._authorize_model(scope, route.model)
        self._authorize_url(policy, route.baseUrl)
        credential = await self._credential(route, scope)
        client = await self._client_ctx()
        request = client.build_request(
            "POST",
            f"{route.baseUrl.rstrip('/')}/responses",
            json=self._responses_body(route, body),
            headers={"Authorization": f"Bearer {credential}", "Accept": "text/event-stream"},
            timeout=httpx.Timeout(route.timeoutMs / 1000, read=route.streamIdleTimeoutMs / 1000),
        )
        try:
            response = await client.send(request, stream=True, follow_redirects=False)
        except httpx.HTTPError as exc:
            raise ModelProxyError("provider_unavailable") from exc
        if response.status_code >= 300:
            await response.aread()
            error = _provider_http_error(response, credential)
            await response.aclose()
            raise error
        if not response.headers.get("content-type", "").startswith("text/event-stream"):
            await response.aclose()
            raise ModelProxyError("provider_stream_invalid")
        response.extensions["haas_credential"] = credential
        return response

    async def relay_stream(self, response: httpx.Response) -> AsyncIterator[str]:
        credential = response.extensions.pop("haas_credential", "")
        try:
            async for line in response.aiter_lines():
                if len(line) > 4 * 1024 * 1024:
                    raise ValueError("provider frame too large")
                if line.startswith("data:") and line[5:].strip() != "[DONE]":
                    payload = json.loads(line[5:])
                    if not isinstance(payload, dict):
                        raise ValueError("provider frame invalid")
                    if payload.get("type") == "error":
                        payload = {
                            "type": "error",
                            "code": "haas_provider_error",
                            "message": "Provider request failed",
                        }
                    elif payload.get("type") == "response.failed":
                        payload = {
                            "type": "response.failed",
                            "response": {
                                "status": "failed",
                                "error": {
                                    "code": "haas_provider_error",
                                    "message": "Provider request failed",
                                },
                            },
                        }
                    line = "data: " + json.dumps(payload, separators=(",", ":"))
                if credential:
                    line = line.replace(credential, "[REDACTED]")
                yield line + "\n"
        except (httpx.HTTPError, ValueError):
            yield (
                'data: {"type":"error","code":"haas_provider_error",'
                '"message":"Provider stream failed"}\n\n'
            )
        finally:
            await response.aclose()

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
            self._active_client = httpx.AsyncClient(trust_env=False)
        return self._active_client

    def _authorize_model(self, scope: RuntimeTokenScope, model: str) -> None:
        if scope.allowedModels and model not in scope.allowedModels:
            raise ModelProxyError("model_not_allowed")

    def _authorize_url(self, policy: EffectivePolicy, url: str) -> None:
        parsed = urlsplit(url)
        if (
            parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ModelProxyError("provider_url_not_allowed")
        decision: PolicyDecision = self._policy.authorize_network(policy, url)
        if not decision.allowed:
            raise ModelProxyError(f"provider_url_not_allowed: {decision.safeReason}")
