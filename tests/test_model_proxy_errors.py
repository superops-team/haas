"""Provider-failure and lifecycle coverage for ModelProxy.

test_model_proxy_relay.py covers the happy path and authorization rejections;
this file covers what happens when the upstream provider misbehaves, plus the
client lifecycle branches. AGENTS.md puts proxy/credential paths behind a 95%
gate, and these are exactly the paths that run during an incident.
"""
from __future__ import annotations

import httpx
import pytest

from haas.model_proxy import (
    InMemorySecretResolver,
    ModelRoute,
    RuntimeTokenManager,
    RuntimeTokenScope,
)
from haas.model_proxy.proxy import ModelProxy, ModelProxyError
from haas.policy import (
    NetworkPolicy,
    PolicyCompileInput,
    PolicyController,
    PolicyLayer,
    PolicyScope,
)

ALLOW = ["http://127.0.0.1:18080"]
# Realistic length/shape: the redaction patterns require >=20 chars after
# `sk-`, so a short toy value would pass the test without exercising them.
SECRET = "sk-proj-" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4"  # haas-secret-ignore - synthetic fixture


def _policy(allow: list[str] = ALLOW) -> object:
    return PolicyController().compile(
        PolicyCompileInput(
            scope=PolicyScope(tenantId="t1"),
            layers=[PolicyLayer("tenant", network=NetworkPolicy(allow=allow))],
        )
    )


def _route() -> ModelRoute:
    return ModelRoute(
        provider="openai-compatible",
        baseUrl="http://127.0.0.1:18080/v1",
        model="gpt-x",
        credentialRef="secret://tenant/provider",
    )


def _proxy(handler, *, client: httpx.AsyncClient | None = None):
    if client is None:
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    tokens = RuntimeTokenManager()
    proxy = ModelProxy(
        InMemorySecretResolver({"secret://tenant/provider": SECRET}),
        tokens,
        PolicyController(),
        client=client,
    )
    return proxy, tokens


async def _call(proxy: ModelProxy, tokens: RuntimeTokenManager):
    token = tokens.issue(RuntimeTokenScope(sessionId="s_1"))
    return await proxy.proxy_responses(
        _route(), {"input": "hi"}, authorization=f"Bearer {token}", policy=_policy()
    )


# --- provider failures ------------------------------------------------------


@pytest.mark.parametrize("status", [400, 401, 429, 500, 503])
async def test_provider_http_error_raises(status: int) -> None:
    proxy, tokens = _proxy(lambda _r: httpx.Response(status, json={"error": "nope"}))
    with pytest.raises(ModelProxyError, match=f"provider HTTP {status}"):
        await _call(proxy, tokens)


async def test_provider_non_json_response_raises() -> None:
    proxy, tokens = _proxy(
        lambda _r: httpx.Response(200, text="<html>gateway timeout</html>")
    )
    with pytest.raises(ModelProxyError, match="non-JSON"):
        await _call(proxy, tokens)


async def test_provider_error_body_does_not_leak_credentials() -> None:
    """A provider that echoes our Authorization header must not leak it.

    The error message is surfaced to callers and logs, so it is a secret
    surface (AGENTS.md 铁律 7).
    """
    def handler(request: httpx.Request) -> httpx.Response:
        # Hostile/naive provider echoing the inbound auth header verbatim.
        return httpx.Response(
            401, json={"error": "bad key", "seen": request.headers.get("authorization")}
        )

    proxy, tokens = _proxy(handler)
    with pytest.raises(ModelProxyError) as excinfo:
        await _call(proxy, tokens)
    message = str(excinfo.value)
    assert SECRET not in message
    assert "[REDACTED]" in message
    # The status code must still be actionable for the caller.
    assert "provider HTTP 401" in message


async def test_empty_authorization_is_rejected() -> None:
    proxy, _ = _proxy(lambda _r: httpx.Response(200, json={}))
    with pytest.raises(ModelProxyError):
        await proxy.proxy_responses(
            _route(), {"input": "hi"}, authorization="", policy=_policy()
        )


async def test_usage_absent_is_tolerated() -> None:
    """A provider may omit usage; that is not an error."""
    proxy, tokens = _proxy(lambda _r: httpx.Response(200, json={"output": []}))
    data, usage = await _call(proxy, tokens)
    assert data == {"output": []}
    assert usage is None


# --- client lifecycle -------------------------------------------------------


async def test_close_is_noop_for_injected_client() -> None:
    """An injected client is owned by the caller and must not be closed."""
    client = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda _r: httpx.Response(200, json={})
    ))
    proxy, tokens = _proxy(None, client=client)
    await _call(proxy, tokens)
    await proxy.close()
    assert client.is_closed is False
    await client.aclose()


async def test_proxy_creates_and_reuses_its_own_client() -> None:
    """Without an injected client the proxy lazily creates one and reuses it."""
    proxy = ModelProxy(
        InMemorySecretResolver({"secret://tenant/provider": SECRET}),
        RuntimeTokenManager(),
        PolicyController(),
    )
    first = await proxy._client_ctx()
    second = await proxy._client_ctx()
    assert first is second
    await first.aclose()


async def test_huge_provider_body_is_truncated() -> None:
    """A multi-megabyte provider body must not be inlined into an error."""
    proxy, tokens = _proxy(lambda _r: httpx.Response(500, text="x" * 10000))
    with pytest.raises(ModelProxyError) as excinfo:
        await _call(proxy, tokens)
    assert "<truncated>" in str(excinfo.value)
    assert len(str(excinfo.value)) < 1000


async def test_empty_provider_body_is_reported() -> None:
    proxy, tokens = _proxy(lambda _r: httpx.Response(502, text=""))
    with pytest.raises(ModelProxyError, match="<empty>"):
        await _call(proxy, tokens)

async def test_close_releases_the_lazily_created_client() -> None:
    """Regression: close() checked _client, but the owned client lives in
    _active_client, so a self-created client was never closed (socket leak)."""
    proxy = ModelProxy(
        InMemorySecretResolver({"secret://tenant/provider": SECRET}),
        RuntimeTokenManager(),
        PolicyController(),
    )
    owned = await proxy._client_ctx()
    await proxy.close()
    assert owned.is_closed is True
    # close() must stay idempotent.
    await proxy.close()


async def test_revoked_runtime_token_is_rejected() -> None:
    proxy, tokens = _proxy(lambda _r: httpx.Response(200, json={}))
    token = tokens.issue(RuntimeTokenScope(sessionId="s_1"))
    tokens.revoke(token)
    with pytest.raises(ModelProxyError):
        await proxy.proxy_responses(
            _route(), {"input": "hi"},
            authorization=f"Bearer {token}", policy=_policy(),
        )


async def test_revoking_an_unknown_token_is_a_noop() -> None:
    tokens = RuntimeTokenManager()
    tokens.revoke("never-issued")
