"""Model Proxy loopback app tests (specs/model-proxy §5.1/§8/§10).

Covers the harness-facing HTTP surface: health/ready, /v1/responses relay,
explicit chat-completions rejection, harness lookup failure and the
secretless assertion that a real provider key never reaches the harness.
"""
from __future__ import annotations

import httpx
from fastapi.testclient import TestClient

from haas.model_proxy import (
    InMemorySecretResolver,
    ModelProxy,
    RuntimeTokenManager,
    RuntimeTokenScope,
)
from haas.model_proxy.app import ProxyApp
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


def _effective_policy(allow: list[str]) -> object:
    return PolicyController().compile(
        PolicyCompileInput(
            scope=PolicyScope(tenantId="t1"),
            layers=[PolicyLayer("tenant", network=NetworkPolicy(allow=allow))],
        )
    )


def _harness(*, with_provider: bool = True, status: str = "active") -> HarnessRecord:
    from haas.stores.memory import ProviderConfig

    provider = (
        ProviderConfig(
            name="openai-compatible",
            baseUrl=PROVIDER_URL,
            wireApi="responses",
            credentialRef=CREDENTIAL_REF,
        )
        if with_provider
        else None
    )
    return HarnessRecord(
        id="chrn_codex_default",
        name="codex-default",
        base="codex",
        status=status,
        defaultModel="gpt-5.6-terra",
        provider=provider,
    )


def _build(
    handler: object,
    *,
    allow: list[str] | None = None,
    harness: HarnessRecord | None = None,
) -> tuple[TestClient, RuntimeTokenManager, dict]:
    seen: dict = {}

    def _wrapped(request: httpx.Request) -> httpx.Response:
        seen["authorization"] = request.headers.get("authorization")
        seen["url"] = str(request.url)
        return handler(request)  # type: ignore[operator]

    client = httpx.AsyncClient(transport=httpx.MockTransport(_wrapped))
    tokens = RuntimeTokenManager()
    proxy = ModelProxy(
        InMemorySecretResolver({CREDENTIAL_REF: REAL_KEY}),
        tokens,
        PolicyController(),
        client=client,
    )
    store = MemoryStore()
    registry = HarnessRegistry(store=store)
    registry.save(harness if harness is not None else _harness())
    app = ProxyApp(
        proxy, registry, _effective_policy(allow or ["http://127.0.0.1:18080"])
    ).build()
    return TestClient(app), tokens, seen


# --- liveness ---------------------------------------------------------------


def test_health_and_ready() -> None:
    client, _, _ = _build(lambda r: httpx.Response(200, json={}))
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/ready").json() == {"status": "ready"}


# --- responses relay --------------------------------------------------------


def test_responses_relays_and_injects_credential() -> None:
    client, tokens, seen = _build(
        lambda r: httpx.Response(
            200,
            json={"id": "resp_1", "usage": {"input_tokens": 7, "output_tokens": 3}},
        )
    )
    token = tokens.issue(
        RuntimeTokenScope(sessionId="s_1", harnessId="chrn_codex_default")
    )
    resp = client.post(
        "/v1/responses",
        json={"model": "gpt-5.6-terra", "input": "hi"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["id"] == "resp_1"
    # Real key goes upstream only.
    assert seen["authorization"] == f"Bearer {REAL_KEY}"
    assert seen["url"].endswith("/responses")


def test_responses_never_leaks_provider_key_to_caller() -> None:
    """specs/model-proxy §8: harness must never observe the real key."""
    client, tokens, _ = _build(
        lambda r: httpx.Response(200, json={"id": "resp_1"})
    )
    token = tokens.issue(
        RuntimeTokenScope(sessionId="s_1", harnessId="chrn_codex_default")
    )
    resp = client.post(
        "/v1/responses",
        json={"model": "gpt-5.6-terra", "input": "hi"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert REAL_KEY not in resp.text
    assert CREDENTIAL_REF not in resp.text
    for value in resp.headers.values():
        assert REAL_KEY not in value


def test_responses_rejects_invalid_token() -> None:
    client, _, _ = _build(lambda r: httpx.Response(200, json={}))
    resp = client.post(
        "/v1/responses",
        json={"model": "gpt-5.6-terra"},
        headers={"Authorization": "Bearer bogus"},
    )
    assert resp.status_code == 400
    assert "invalid_token" in resp.json()["error"]
    assert REAL_KEY not in resp.text


def test_responses_rejects_missing_authorization() -> None:
    client, _, _ = _build(lambda r: httpx.Response(200, json={}))
    resp = client.post("/v1/responses", json={"model": "gpt-5.6-terra"})
    assert resp.status_code == 400
    assert REAL_KEY not in resp.text


def test_responses_rejects_unknown_harness() -> None:
    client, tokens, _ = _build(lambda r: httpx.Response(200, json={}))
    token = tokens.issue(RuntimeTokenScope(sessionId="s_1", harnessId="chrn_missing"))
    resp = client.post(
        "/v1/responses",
        json={"model": "gpt-5.6-terra"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400
    assert resp.json()["error"] == "harness_not_found"


def test_responses_rejects_inactive_harness() -> None:
    client, tokens, _ = _build(
        lambda r: httpx.Response(200, json={}),
        harness=_harness(status="disabled"),
    )
    token = tokens.issue(
        RuntimeTokenScope(sessionId="s_1", harnessId="chrn_codex_default")
    )
    resp = client.post(
        "/v1/responses",
        json={"model": "gpt-5.6-terra"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400
    assert resp.json()["error"] == "harness_not_found"


def test_responses_rejects_disallowed_provider_url() -> None:
    """Provider URL outside the allowlist fails closed (SSRF guard)."""
    client, tokens, _ = _build(
        lambda r: httpx.Response(200, json={}),
        allow=["https://api.openai.com"],
    )
    token = tokens.issue(
        RuntimeTokenScope(sessionId="s_1", harnessId="chrn_codex_default")
    )
    resp = client.post(
        "/v1/responses",
        json={"model": "gpt-5.6-terra"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400
    assert "provider_url_not_allowed" in resp.json()["error"]


def test_responses_surfaces_upstream_error_without_body_leak() -> None:
    client, tokens, _ = _build(
        lambda r: httpx.Response(500, text="upstream exploded")
    )
    token = tokens.issue(
        RuntimeTokenScope(sessionId="s_1", harnessId="chrn_codex_default")
    )
    resp = client.post(
        "/v1/responses",
        json={"model": "gpt-5.6-terra"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400
    assert "provider HTTP 500" in resp.json()["error"]
    assert REAL_KEY not in resp.text


def test_responses_rejects_harness_without_provider_route() -> None:
    """No provider route must fail closed as 502, not a bare 500 (spec §10)."""
    client, tokens, _ = _build(
        lambda r: httpx.Response(200, json={}),
        harness=_harness(with_provider=False),
    )
    token = tokens.issue(
        RuntimeTokenScope(sessionId="s_1", harnessId="chrn_codex_default")
    )
    resp = client.post(
        "/v1/responses",
        json={"model": "gpt-5.6-terra"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 502
    assert resp.json()["error"] == "haas_provider_error"
    # A safe reason only: no harness id, credential ref or traceback.
    assert CREDENTIAL_REF not in resp.text
    assert "Traceback" not in resp.text


def test_responses_maps_unresolvable_credential_to_provider_error() -> None:
    """A missing credential must not surface the ref or a 500."""
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={}))
    )
    tokens = RuntimeTokenManager()
    # Resolver deliberately has no entry for CREDENTIAL_REF.
    proxy = ModelProxy(
        InMemorySecretResolver({}), tokens, PolicyController(), client=client
    )
    store = MemoryStore()
    registry = HarnessRegistry(store=store)
    registry.save(_harness())
    app = ProxyApp(
        proxy, registry, _effective_policy(["http://127.0.0.1:18080"])
    ).build()
    tc = TestClient(app)
    token = tokens.issue(
        RuntimeTokenScope(sessionId="s_1", harnessId="chrn_codex_default")
    )
    resp = tc.post(
        "/v1/responses",
        json={"model": "gpt-5.6-terra"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 502
    assert resp.json()["error"] == "haas_provider_error"
    assert CREDENTIAL_REF not in resp.text


# --- chat completions -------------------------------------------------------


def test_chat_completions_is_explicitly_not_implemented() -> None:
    """spec §4: never silently corrupt an unsupported wire shape."""
    client, tokens, _ = _build(lambda r: httpx.Response(200, json={}))
    token = tokens.issue(
        RuntimeTokenScope(sessionId="s_1", harnessId="chrn_codex_default")
    )
    resp = client.post(
        "/v1/chat/completions",
        json={"model": "gpt-5.6-terra", "messages": []},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 501
    assert resp.json()["error"] == "chat_completions_not_implemented"
