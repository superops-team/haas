"""Model Proxy loopback app tests (specs/model-proxy §5.1/§8/§10).

Covers the harness-facing HTTP surface: health/ready, /v1/responses relay,
explicit chat-completions rejection, harness lookup failure and the
secretless assertion that a real provider key never reaches the harness.
"""

from __future__ import annotations

import json

import httpx
import pytest
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
    frozen_route_resolver: object | None = None,
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
        proxy,
        registry,
        _effective_policy(allow or ["http://127.0.0.1:18080"]),
        frozen_route_resolver=frozen_route_resolver,  # type: ignore[arg-type]
    ).build()
    return TestClient(app), tokens, seen


@pytest.mark.parametrize("mode", ["timeout", "redirect", "not_sse", "malformed", "failed", "echo"])
def test_stream_failures_close_and_redact(mode):
    def handler(request):
        if mode == "timeout":
            raise httpx.ReadTimeout("upstream timeout", request=request)
        if mode == "redirect":
            return httpx.Response(302, headers={"location": "https://other.example"})
        if mode == "not_sse":
            return httpx.Response(200, json={})
        payload = {
            "malformed": "data: []\n\n",
            "failed": (
                'data: {"type":"response.failed","response":{"error":{"message":"private"}}}\n\n'
            ),
            "echo": f'data: {{"type":"response.output_text.delta","delta":"{REAL_KEY}"}}\n\n',
        }[mode]
        return httpx.Response(200, text=payload, headers={"content-type": "text/event-stream"})

    client, tokens, _ = _build(handler)
    token = tokens.issue(RuntimeTokenScope(sessionId="s", harnessId="chrn_codex_default"))
    response = client.post(
        "/v1/responses", json={"stream": True}, headers={"Authorization": f"Bearer {token}"}
    )
    assert REAL_KEY not in response.text
    assert "private" not in response.text
    if mode in {"timeout", "redirect", "not_sse"}:
        assert response.status_code == 400
    elif mode == "echo":
        assert "[REDACTED]" in response.text
    else:
        assert "haas_provider_error" in response.text


def test_stream_setup_error_returns_bounded_structured_diagnostic() -> None:
    client, tokens, _ = _build(
        lambda _r: httpx.Response(
            400,
            json={
                "error": {
                    "code": "unsupported_parameter",
                    "param": "reasoning.effort",
                    "message": f"bad parameter; api_key={REAL_KEY}",
                    "input": "must not escape",
                }
            },
        )
    )
    token = tokens.issue(RuntimeTokenScope(sessionId="s", harnessId="chrn_codex_default"))
    response = client.post(
        "/v1/responses",
        json={"stream": True},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 400
    assert response.json()["error"].startswith(
        'provider HTTP 400: {"code":"unsupported_parameter"'
    )
    assert "reasoning.effort" in response.text
    assert REAL_KEY not in response.text
    assert "must not escape" not in response.text


def test_responses_streams_sse_and_sanitizes_errors() -> None:
    frames = (
        'data: {"type":"response.output_text.delta","delta":"hello"}\n\n'
        f'data: {{"type":"error","message":"{REAL_KEY}"}}\n\n'
    )
    client, tokens, seen = _build(
        lambda r: httpx.Response(200, text=frames, headers={"content-type": "text/event-stream"})
    )
    token = tokens.issue(RuntimeTokenScope(sessionId="s_1", harnessId="chrn_codex_default"))
    response = client.post(
        "/v1/responses",
        json={"model": "gpt-5.6-terra", "stream": True},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert '"delta":"hello"' in response.text
    assert REAL_KEY not in response.text
    assert seen["authorization"] == f"Bearer {REAL_KEY}"


@pytest.mark.parametrize("stream", [False, True])
def test_volcengine_ark_flattens_namespace_tools_for_responses(stream: bool) -> None:
    def handler(request):
        outbound = json.loads(request.content)
        tools = outbound["tools"]
        assert not any(tool.get("type") == "namespace" for tool in tools)
        assert tools == [
            {
                "type": "function",
                "name": "mcp__manager_cowork_recall__recall",
                "description": "Cowork memory\n\nRecall scoped context",
                "parameters": {"type": "object"},
            }
        ]
        assert outbound["input"][0]["name"] == "mcp__manager_cowork_recall__recall"
        assert "namespace" not in outbound["input"][0]
        if stream:
            return httpx.Response(
                200,
                text=(
                    'data: {"type":"response.output_item.added",'
                    '"item":{"type":"function_call",'
                    '"name":"mcp__manager_cowork_recall__recall"}}\n\n'
                    "data: [DONE]\n\n"
                ),
                headers={"content-type": "text/event-stream"},
            )
        return httpx.Response(
            200,
            json={
                "output": [
                    {
                        "type": "function_call",
                        "name": "mcp__manager_cowork_recall__recall",
                    }
                ]
            },
        )

    harness = _harness()
    harness.provider.providerId = "volcengine-ark"
    client, tokens, _ = _build(handler, harness=harness)
    token = tokens.issue(RuntimeTokenScope(sessionId="s_1", harnessId="chrn_codex_default"))
    response = client.post(
        "/v1/responses",
        json={
            "model": "gpt-5.6-terra",
            "stream": stream,
            "input": [
                {
                    "type": "function_call",
                    "namespace": "mcp__manager_cowork_recall",
                    "name": "recall",
                }
            ],
            "tools": [
                {
                    "type": "namespace",
                    "name": "mcp__manager_cowork_recall",
                    "description": "Cowork memory",
                    "tools": [
                        {
                            "type": "function",
                            "name": "recall",
                            "description": "Recall scoped context",
                            "parameters": {"type": "object"},
                        }
                    ],
                }
            ],
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    if stream:
        assert '"namespace":"mcp__manager_cowork_recall"' in response.text
        assert '"name":"recall"' in response.text
    else:
        output = response.json()["output"][0]
        assert output["namespace"] == "mcp__manager_cowork_recall"
        assert output["name"] == "recall"


def test_unknown_frozen_route_does_not_use_registry() -> None:
    client, tokens, seen = _build(
        lambda r: httpx.Response(200, json={}), frozen_route_resolver=lambda *_: None
    )
    token = tokens.issue(RuntimeTokenScope(sessionId="s_1", harnessId="chrn_codex_default"))
    response = client.post(
        "/v1/responses",
        json={"model": "gpt-5.6-terra"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code >= 400
    assert not seen


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("provider_id", ["volcengine-ark", "openai"])
def test_responses_history_status_compatibility(stream, provider_id):
    import copy
    import json

    items = [
        {"type": "message", "role": "user", "content": "hello"},
        {"type": "reasoning", "id": "r1", "summary": []},
        {"type": "message", "role": "assistant", "content": []},
        {"type": "message", "role": "assistant", "status": "incomplete", "content": []},
        {"type": "function_call_output", "call_id": "c1", "output": "ok"},
    ]
    expected = copy.deepcopy(items)
    if provider_id == "volcengine-ark":
        expected[1]["status"] = "completed"
        expected[2]["status"] = "completed"

    def handler(request):
        assert json.loads(request.content)["input"] == expected
        if stream:
            return httpx.Response(
                200, text="data: [DONE]\n\n", headers={"content-type": "text/event-stream"}
            )
        return httpx.Response(200, json={"status": "completed"})

    harness = _harness()
    harness.provider.providerId = provider_id
    client, tokens, _ = _build(handler, harness=harness)
    token = tokens.issue(RuntimeTokenScope(sessionId="s", harnessId=harness.id))
    assert (
        client.post(
            "/v1/responses",
            json={"input": items, "stream": stream},
            headers={"Authorization": f"Bearer {token}"},
        ).status_code
        == 200
    )
    assert "status" not in items[1] and "status" not in items[2]


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("provider_id", ["volcengine-ark", "openai"])
def test_responses_reasoning_summary_compatibility(stream, provider_id):
    import json

    reasoning = {"effort": "high", "summary": "auto"}

    def handler(request):
        outbound = json.loads(request.content)
        if provider_id == "volcengine-ark":
            assert outbound["reasoning"] == {"effort": "high"}
        else:
            assert outbound["reasoning"] == reasoning
        if stream:
            return httpx.Response(
                200, text="data: [DONE]\n\n", headers={"content-type": "text/event-stream"}
            )
        return httpx.Response(200, json={"status": "completed"})

    harness = _harness()
    harness.provider.providerId = provider_id
    client, tokens, _ = _build(handler, harness=harness)
    token = tokens.issue(RuntimeTokenScope(sessionId="s", harnessId=harness.id))
    assert (
        client.post(
            "/v1/responses",
            json={"input": "hello", "reasoning": reasoning, "stream": stream},
            headers={"Authorization": f"Bearer {token}"},
        ).status_code
        == 200
    )
    assert reasoning == {"effort": "high", "summary": "auto"}


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
    token = tokens.issue(RuntimeTokenScope(sessionId="s_1", harnessId="chrn_codex_default"))
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


def test_responses_uses_frozen_session_route_instead_of_live_registry() -> None:
    frozen_url = "https://frozen.example.com/v1"
    seen_scope: list[tuple[str, str]] = []

    def frozen_route(session_id: str, invocation_id: str) -> dict[str, str]:
        seen_scope.append((session_id, invocation_id))
        return {
            "providerId": "frozen",
            "name": "frozen",
            "baseUrl": frozen_url,
            "model": "frozen-model",
            "wireApi": "responses",
            "apiType": "responses",
            "credentialRef": CREDENTIAL_REF,
        }

    client, tokens, seen = _build(
        lambda r: httpx.Response(200, json={"id": "resp_1"}),
        allow=[frozen_url],
        frozen_route_resolver=frozen_route,
    )
    token = tokens.issue(
        RuntimeTokenScope(sessionId="hsess_1", invocationId="inv_1", harnessId="chrn_codex_default")
    )
    response = client.post(
        "/v1/responses",
        json={"input": "hi"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert seen_scope == [("hsess_1", "inv_1")]
    assert seen["url"].startswith(frozen_url)


def test_responses_never_leaks_provider_key_to_caller() -> None:
    """specs/model-proxy §8: harness must never observe the real key."""
    client, tokens, _ = _build(lambda r: httpx.Response(200, json={"id": "resp_1"}))
    token = tokens.issue(RuntimeTokenScope(sessionId="s_1", harnessId="chrn_codex_default"))
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
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "haas_model_proxy_token_invalid"
    assert REAL_KEY not in resp.text


def test_responses_distinguishes_expired_runtime_token() -> None:
    client, tokens, _ = _build(lambda r: httpx.Response(200, json={}))
    tokens._ttl_seconds = -1
    token = tokens.issue(RuntimeTokenScope(sessionId="s_1", harnessId="chrn_codex_default"))
    resp = client.post(
        "/v1/responses",
        json={"model": "gpt-5.6-terra"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "haas_model_proxy_token_expired"


def test_responses_rejects_missing_authorization() -> None:
    client, _, _ = _build(lambda r: httpx.Response(200, json={}))
    resp = client.post("/v1/responses", json={"model": "gpt-5.6-terra"})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "haas_model_proxy_token_invalid"
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
    token = tokens.issue(RuntimeTokenScope(sessionId="s_1", harnessId="chrn_codex_default"))
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
    token = tokens.issue(RuntimeTokenScope(sessionId="s_1", harnessId="chrn_codex_default"))
    resp = client.post(
        "/v1/responses",
        json={"model": "gpt-5.6-terra"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400
    assert "provider_url_not_allowed" in resp.json()["error"]


def test_responses_surfaces_upstream_error_without_body_leak() -> None:
    client, tokens, _ = _build(lambda r: httpx.Response(500, text="upstream exploded"))
    token = tokens.issue(RuntimeTokenScope(sessionId="s_1", harnessId="chrn_codex_default"))
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
    token = tokens.issue(RuntimeTokenScope(sessionId="s_1", harnessId="chrn_codex_default"))
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
    proxy = ModelProxy(InMemorySecretResolver({}), tokens, PolicyController(), client=client)
    store = MemoryStore()
    registry = HarnessRegistry(store=store)
    registry.save(_harness())
    app = ProxyApp(proxy, registry, _effective_policy(["http://127.0.0.1:18080"])).build()
    tc = TestClient(app)
    token = tokens.issue(RuntimeTokenScope(sessionId="s_1", harnessId="chrn_codex_default"))
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
    token = tokens.issue(RuntimeTokenScope(sessionId="s_1", harnessId="chrn_codex_default"))
    resp = client.post(
        "/v1/chat/completions",
        json={"model": "gpt-5.6-terra", "messages": []},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 501
    assert resp.json()["error"] == "chat_completions_not_implemented"
