"""Model Proxy relay tests: auth, credential injection, URL policy, usage."""

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


def _policy(allow: list[str]) -> object:
    return PolicyController().compile(
        PolicyCompileInput(
            scope=PolicyScope(tenantId="t1"),
            layers=[
                PolicyLayer(
                    "tenant",
                    network=NetworkPolicy(defaultAction="deny", allow=allow),
                )
            ],
        )
    )


def _proxy(handler: object, *, allow: list[str]) -> tuple[ModelProxy, RuntimeTokenManager]:
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    tokens = RuntimeTokenManager()
    return (
        ModelProxy(
            InMemorySecretResolver({"secret://tenant/provider": "sk-real-key"}),
            tokens,
            PolicyController(),
            client=client,
        ),
        tokens,
    )


async def test_proxy_injects_credential_and_normalizes_usage() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["authorization"] = request.headers.get("authorization")
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"usage": {"input_tokens": 4, "output_tokens": 2}})

    proxy, tokens = _proxy(handler, allow=["http://127.0.0.1:18080"])
    token = tokens.issue(RuntimeTokenScope(sessionId="s_1"))
    route = ModelRoute(
        provider="openai-compatible",
        baseUrl="http://127.0.0.1:18080/v1",
        model="gpt-x",
        credentialRef="secret://tenant/provider",
    )
    data, usage = await proxy.proxy_responses(
        route,
        {"input": "hi"},
        authorization=f"Bearer {token}",
        policy=_policy(["http://127.0.0.1:18080"]),
    )
    assert seen["authorization"] == "Bearer sk-real-key"
    assert seen["url"].endswith("/responses")
    assert usage is not None and usage.inputTokens == 4


async def test_proxy_rejects_invalid_token() -> None:
    proxy, _ = _proxy(lambda r: httpx.Response(200, json={}), allow=["http://127.0.0.1:18080"])
    route = ModelRoute(
        provider="openai-compatible",
        baseUrl="http://127.0.0.1:18080/v1",
        model="gpt-x",
        credentialRef="secret://x",
    )
    with pytest.raises(ModelProxyError):
        await proxy.proxy_responses(
            route, {}, authorization="Bearer bogus", policy=_policy(["http://127.0.0.1:18080"])
        )


async def test_proxy_rejects_unlisted_provider_url() -> None:
    proxy, tokens = _proxy(lambda r: httpx.Response(200, json={}), allow=["https://api.openai.com"])
    token = tokens.issue(RuntimeTokenScope(sessionId="s_1"))
    route = ModelRoute(
        provider="openai-compatible",
        baseUrl="http://evil.com/v1",
        model="gpt-x",
        credentialRef="secret://x",
    )
    with pytest.raises(ModelProxyError, match="provider_url_not_allowed"):
        await proxy.proxy_responses(
            route, {}, authorization=f"Bearer {token}", policy=_policy(["https://api.openai.com"])
        )


async def test_proxy_rejects_model_outside_scope() -> None:
    proxy, tokens = _proxy(lambda r: httpx.Response(200, json={}), allow=["http://127.0.0.1:18080"])
    token = tokens.issue(RuntimeTokenScope(sessionId="s_1", allowedModels=["gpt-a"]))
    route = ModelRoute(
        provider="openai-compatible",
        baseUrl="http://127.0.0.1:18080/v1",
        model="gpt-b",
        credentialRef="secret://x",
    )
    with pytest.raises(ModelProxyError, match="model_not_allowed"):
        await proxy.proxy_responses(
            route, {}, authorization=f"Bearer {token}", policy=_policy(["http://127.0.0.1:18080"])
        )
