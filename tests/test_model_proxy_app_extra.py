"""Edge coverage for ProxyApp: non-object bodies, unsupported apiType, unmapped
error fail-closed, and the frozen-route / session-scope resolver arity branches."""

from __future__ import annotations

import httpx
from fastapi.testclient import TestClient

from haas.model_proxy import (
    InMemorySecretResolver,
    ModelProxy,
    ModelProxyError,
    RuntimeTokenManager,
    RuntimeTokenScope,
)
from haas.model_proxy.app import ProxyApp
from haas.model_proxy.runtime import RuntimeModelProxy
from haas.policy import (
    NetworkPolicy,
    PolicyCompileInput,
    PolicyController,
    PolicyLayer,
    PolicyScope,
)
from haas.registry import HarnessRegistry
from haas.stores import HarnessRecord, MemoryStore

PROVIDER_URL = "http://127.0.0.1:18080/v1"
REAL_KEY = "sk-real-provider-key"  # haas-secret-ignore
CREDENTIAL_REF = "secret://tenant/workspace/provider/default"


def _policy(allow: list[str]) -> object:
    return PolicyController().compile(
        PolicyCompileInput(
            scope=PolicyScope(tenantId="t1"),
            layers=[PolicyLayer("tenant", network=NetworkPolicy(allow=allow))],
        )
    )


def _harness() -> HarnessRecord:
    from haas.stores.memory import ProviderConfig

    return HarnessRecord(
        id="chrn_codex_default",
        name="codex-default",
        base="codex",
        status="active",
        defaultModel="gpt-5.6-terra",
        provider=ProviderConfig(
            name="openai-compatible",
            baseUrl=PROVIDER_URL,
            wireApi="responses",
            credentialRef=CREDENTIAL_REF,
        ),
    )


def _build(
    handler,
    *,
    allow: list[str] | None = None,
    frozen_route_resolver=None,
    session_scope_resolver=None,
):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    tokens = RuntimeTokenManager()
    proxy = ModelProxy(
        InMemorySecretResolver({CREDENTIAL_REF: REAL_KEY}),
        tokens,
        PolicyController(),
        client=client,
    )
    store = MemoryStore()
    registry = HarnessRegistry(store=store)
    registry.save(_harness())
    app = ProxyApp(
        proxy,
        registry,
        _policy(allow or ["http://127.0.0.1:18080"]),
        frozen_route_resolver=frozen_route_resolver,
        session_scope_resolver=session_scope_resolver,
    ).build()
    return TestClient(app), tokens


def _token(tokens: RuntimeTokenManager, **kw) -> str:
    scope = RuntimeTokenScope(sessionId="s_1", harnessId="chrn_codex_default", **kw)
    return f"Bearer {tokens.issue(scope)}"


def test_responses_rejects_non_object_body() -> None:
    client, tokens = _build(lambda r: httpx.Response(200, json={}))
    resp = client.post("/v1/responses", json=[1, 2, 3], headers={"Authorization": _token(tokens)})
    assert resp.status_code == 400
    assert resp.json()["error"] == "haas_model_proxy_invalid_input"


def test_responses_rejects_non_responses_api_type() -> None:
    frozen_url = "https://frozen.example.com/v1"
    frozen_route = {
        "providerId": "frozen",
        "name": "frozen",
        "baseUrl": frozen_url,
        "model": "gpt-5.6-terra",
        "wireApi": "openai-compatible",
        "apiType": "chat_completions",
        "credentialRef": CREDENTIAL_REF,
    }
    client, tokens = _build(
        lambda r: httpx.Response(200, json={}),
        allow=[frozen_url],
        frozen_route_resolver=lambda _session_id: frozen_route,
    )
    scope_key = RuntimeModelProxy.provider_scope_key(frozen_route)
    resp = client.post(
        "/v1/responses",
        json={"input": "hi"},
        headers={"Authorization": _token(tokens, providerScopeKey=scope_key)},
    )
    assert resp.status_code == 400
    assert resp.json()["error"] == "haas_model_proxy_api_unsupported"


def test_responses_unmapped_error_fails_closed_with_502() -> None:
    client, tokens = _build(lambda r: httpx.Response(200, json={}))
    # Reach into the built app and force an unmapped ModelProxyError.
    proxy_app = client.app.state.proxy_app  # type: ignore[attr-defined]

    async def boom(*_a, **_k):
        raise ModelProxyError("some_unmapped_internal_condition")

    proxy_app._proxy.proxy_responses = boom  # type: ignore[method-assign]
    resp = client.post(
        "/v1/responses", json={"input": "hi"}, headers={"Authorization": _token(tokens)}
    )
    assert resp.status_code == 502
    assert resp.json()["error"] == "haas_model_proxy_internal_error"


def test_frozen_route_resolver_with_two_params_receives_invocation_id() -> None:
    seen: list[tuple] = []

    def resolver(session_id: str, invocation_id: str) -> dict:
        seen.append((session_id, invocation_id))
        return {
            "providerId": "frozen",
            "name": "frozen",
            "baseUrl": "https://frozen.example.com/v1",
            "model": "gpt-5.6-terra",
            "wireApi": "responses",
            "apiType": "responses",
            "credentialRef": CREDENTIAL_REF,
        }

    client, tokens = _build(
        lambda r: httpx.Response(200, json={"id": "ok"}),
        allow=["https://frozen.example.com"],
        frozen_route_resolver=resolver,
    )
    resp = client.post(
        "/v1/responses", json={"input": "hi"}, headers={"Authorization": _token(tokens)}
    )
    assert resp.status_code == 200
    assert seen and seen[0][0] == "s_1"


def test_resolve_session_scope_is_none_without_resolver() -> None:
    proxy_app = ProxyApp(
        ModelProxy(InMemorySecretResolver({}), RuntimeTokenManager(), PolicyController()),
        HarnessRegistry(store=MemoryStore()),
        _policy(["http://127.0.0.1:18080"]),
        session_scope_resolver=None,
    )
    assert proxy_app._resolve_session_scope("s_1", "tok") is None


def test_resolve_session_scope_rejects_low_arity_resolver() -> None:
    proxy_app = ProxyApp(
        ModelProxy(InMemorySecretResolver({}), RuntimeTokenManager(), PolicyController()),
        HarnessRegistry(store=MemoryStore()),
        _policy(["http://127.0.0.1:18080"]),
        session_scope_resolver=lambda session_id: None,  # arity 1 -> rejected
    )
    assert proxy_app._resolve_session_scope("s_1", "tok") is None
