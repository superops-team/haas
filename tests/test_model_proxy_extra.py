"""Targeted coverage for ModelProxy edge paths.

Complements test_model_proxy_relay.py / _errors.py by exercising the branches
the happy-path suites skip: credential redaction on empty input, provider error
shapes that are not objects, wrong-audience tokens, namespace tool bridging
conflicts, stream retry exhaustion, oversized frames, and URL authorization
rejections. Kept offline via httpx.MockTransport.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from haas.model_proxy import (
    InMemorySecretResolver,
    ModelRoute,
    RuntimeTokenManager,
    RuntimeTokenScope,
)
from haas.model_proxy.proxy import (
    ModelProxy,
    ModelProxyError,
    _flatten_input_function_calls,
    _flatten_namespace_tools,
    _NamespacedToolName,
    _NamespaceToolBridge,
    _provider_http_error,
    _redact_credential,
    _restore_namespace_tool_calls,
    model_proxy_retry_delay_seconds,
)
from haas.policy import (
    NetworkPolicy,
    PolicyCompileInput,
    PolicyController,
    PolicyLayer,
    PolicyScope,
)

ALLOW = "http://127.0.0.1:18080"
SECRET = "sk-proj-" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4"  # haas-secret-ignore - synthetic fixture


def _policy(allow: list[str] | None = None) -> object:
    allow = allow or [ALLOW]
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


def _route(**kw: Any) -> ModelRoute:
    base = dict(
        provider="openai-compatible",
        baseUrl=f"{ALLOW}/v1",
        model="gpt-x",
        credentialRef="secret://tenant/provider",
    )
    base.update(kw)
    return ModelRoute(**base)


def _proxy(handler, *, credential: str = SECRET) -> tuple[ModelProxy, RuntimeTokenManager]:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    tokens = RuntimeTokenManager()
    proxy = ModelProxy(
        InMemorySecretResolver({"secret://tenant/provider": credential}),
        tokens,
        PolicyController(),
        client=client,
    )
    return proxy, tokens


def _auth(tokens: RuntimeTokenManager, **kw: Any) -> str:
    scope = RuntimeTokenScope(sessionId="s_1", **kw)
    return f"Bearer {tokens.issue(scope)}"


# --- credential redaction helper -------------------------------------------


def test_redact_credential_is_noop_when_credential_empty() -> None:
    payload = {"note": "nothing to redact"}
    assert _redact_credential(payload, "") is payload


# --- provider http error shaping -------------------------------------------


def test_provider_error_when_json_body_is_not_an_object() -> None:
    resp = httpx.Response(500, json=[1, 2, 3])
    err = _provider_http_error(resp, SECRET)
    assert str(err) == "provider HTTP 500"


def test_provider_error_when_error_field_is_neither_string_nor_object() -> None:
    for bad in (None, 123, ["x"]):
        resp = httpx.Response(400, json={"error": bad})
        err = _provider_http_error(resp, SECRET)
        assert str(err) == "provider HTTP 400"


def test_provider_error_when_error_object_has_no_diagnostic_fields() -> None:
    resp = httpx.Response(400, json={"error": {"unknown": "value"}})
    err = _provider_http_error(resp, SECRET)
    assert str(err) == "provider HTTP 400"


def test_provider_error_redacts_credential_in_diagnostic_message() -> None:
    resp = httpx.Response(400, json={"error": {"message": f"bad key {SECRET}"}})
    err = _provider_http_error(resp, SECRET)
    assert SECRET not in str(err)
    assert "[REDACTED]" in str(err)


# --- authentication surface -------------------------------------------------


async def test_authenticate_rejects_valid_token_when_audience_is_wrong() -> None:
    proxy, tokens = _proxy(lambda _r: httpx.Response(200, json={}))
    auth = _auth(tokens, audience="model_runtime")
    with pytest.raises(ModelProxyError, match="invalid_credential"):
        await proxy.authenticate(auth)


async def test_recover_expired_scope_wraps_token_error() -> None:
    proxy, _tokens = _proxy(lambda _r: httpx.Response(200, json={}))
    with pytest.raises(ModelProxyError):
        proxy.recover_expired_scope("haas_mp_bogus.1.x")


async def test_adopt_token_wraps_token_error() -> None:
    proxy, tokens = _proxy(lambda _r: httpx.Response(200, json={}))
    with pytest.raises(ModelProxyError):
        proxy.adopt_token("not-a-haas-token", RuntimeTokenScope(sessionId="s_1"))


# --- request shaping / provider response validation -------------------------


async def test_proxy_response_requires_json_object_from_provider() -> None:
    proxy, tokens = _proxy(lambda _r: httpx.Response(200, json=[1, 2, 3]))
    with pytest.raises(ModelProxyError, match="invalid JSON object"):
        await proxy.proxy_responses(
            _route(), {"input": "hi"}, authorization=_auth(tokens), policy=_policy()
        )


async def test_proxy_uses_resolve_for_when_resolver_supports_it() -> None:
    seen: dict[str, Any] = {}

    class _Resolver:
        available = True

        async def resolve_for(self, route: ModelRoute, scope: RuntimeTokenScope) -> str:
            seen["route"] = route.model
            seen["scope"] = scope.sessionId
            return "rotated-key"

    def handler(request: httpx.Request) -> httpx.Response:
        seen["authorization"] = request.headers.get("authorization")
        return httpx.Response(200, json={"id": "ok"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    tokens = RuntimeTokenManager()
    proxy = ModelProxy(_Resolver(), tokens, PolicyController(), client=client)  # type: ignore[arg-type]
    await proxy.proxy_responses(
        _route(), {"input": "hi"}, authorization=_auth(tokens), policy=_policy()
    )
    assert seen["authorization"] == "Bearer rotated-key"
    assert seen["route"] == "gpt-x"
    assert seen["scope"] == "s_1"


async def test_proxy_post_retries_exhausted_on_connect_timeout() -> None:
    def handler(_r: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out", request=_r)

    proxy, tokens = _proxy(handler)
    with pytest.raises(ModelProxyError, match="provider_unavailable"):
        await proxy.proxy_responses(
            _route(), {"input": "hi"}, authorization=_auth(tokens), policy=_policy()
        )


async def test_proxy_post_unavailable_on_non_timeout_http_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    proxy, tokens = _proxy(handler)
    with pytest.raises(ModelProxyError, match="provider_unavailable"):
        await proxy.proxy_responses(
            _route(), {"input": "hi"}, authorization=_auth(tokens), policy=_policy()
        )


# --- URL authorization ------------------------------------------------------


async def test_proxy_rejects_url_containing_credentials_or_query() -> None:
    proxy, tokens = _proxy(lambda _r: httpx.Response(200, json={}))
    for bad in (
        "http://user:pass@127.0.0.1:18080/v1",
        "http://127.0.0.1:18080/v1?x=1",
        "http://127.0.0.1:18080/v1#frag",
    ):
        with pytest.raises(ModelProxyError, match="provider_url_not_allowed"):
            await proxy.proxy_responses(
                _route(baseUrl=bad),
                {"input": "hi"},
                authorization=_auth(tokens),
                policy=_policy([ALLOW]),
            )


# --- namespace tool bridging (pure helper) ----------------------------------


def test_flatten_tools_skips_non_dict_and_reserves_plain_names() -> None:
    bridge = _NamespaceToolBridge()
    out = _flatten_namespace_tools(["raw-string", {"type": "function", "name": "plain"}], bridge)
    assert out[0] == "raw-string"
    assert "plain" in bridge.reserved_tool_names


def test_flatten_tools_skips_namespace_with_bad_shape() -> None:
    bridge = _NamespaceToolBridge()
    out = _flatten_namespace_tools(
        [{"type": "namespace", "name": 123, "tools": "not-a-list"}], bridge
    )
    assert out == []


def test_flatten_tools_skips_non_function_children_and_blank_names() -> None:
    bridge = _NamespaceToolBridge()
    out = _flatten_namespace_tools(
        [
            {
                "type": "namespace",
                "name": "ns",
                "tools": [
                    {"type": "other", "name": "ignored"},
                    {"type": "function", "name": ""},
                    {"type": "function", "name": "real", "description": "does the thing"},
                ],
            }
        ],
        bridge,
    )
    assert [t["name"] for t in out if isinstance(t, dict)] == ["ns__real"]
    assert out[0]["description"] == "does the thing"


def test_flatten_tools_conflicts_when_flat_name_already_used() -> None:
    bridge = _NamespaceToolBridge()
    tools = [
        {"type": "namespace", "name": "ns", "tools": [{"type": "function", "name": "foo"}]},
        {"type": "namespace", "name": "ns", "tools": [{"type": "function", "name": "foo"}]},
    ]
    with pytest.raises(ValueError, match="namespace_tool_bridge_conflict"):
        _flatten_namespace_tools(tools, bridge)


def test_flatten_input_function_call_flattens_namespaced_name() -> None:
    bridge = _NamespaceToolBridge()
    out = _flatten_input_function_calls(
        [{"type": "function_call", "namespace": "ns", "name": "foo", "arguments": {"a": 1}}],
        bridge,
    )
    assert out[0]["name"] == "ns__foo"
    assert "namespace" not in out[0]


def test_flatten_input_function_call_conflicts_on_different_target() -> None:
    bridge = _NamespaceToolBridge()
    bridge.flat_to_namespaced["ns__foo"] = _NamespacedToolName(namespace="other", name="bar")
    with pytest.raises(ValueError, match="namespace_tool_bridge_conflict"):
        _flatten_input_function_calls(
            [{"type": "function_call", "namespace": "ns", "name": "foo"}], bridge
        )


def test_flatten_input_function_call_conflicts_when_name_reserved() -> None:
    bridge = _NamespaceToolBridge()
    bridge.reserved_tool_names.add("ns__foo")
    with pytest.raises(ValueError, match="namespace_tool_bridge_conflict"):
        _flatten_input_function_calls(
            [{"type": "function_call", "namespace": "ns", "name": "foo"}], bridge
        )


def test_restore_namespace_tool_call_expands_flat_name_back() -> None:
    bridge = _NamespaceToolBridge()
    bridge.flat_to_namespaced["ns__foo"] = _NamespacedToolName(namespace="ns", name="foo")
    restored = _restore_namespace_tool_calls(
        {"type": "function_call", "name": "ns__foo", "arguments": {}}, bridge
    )
    assert restored["namespace"] == "ns"
    assert restored["name"] == "foo"


# --- retry delay ------------------------------------------------------------


def test_retry_delay_honors_retry_after_header() -> None:
    resp = httpx.Response(200, headers={"Retry-After": "1.5"})
    assert model_proxy_retry_delay_seconds(resp, retry_index=0) == 1.5


def test_retry_delay_falls_back_when_retry_after_unparseable() -> None:
    resp = httpx.Response(200, headers={"Retry-After": "garbage"})
    delay = model_proxy_retry_delay_seconds(resp, retry_index=0, random_value=0.0)
    assert delay == 0.05  # base backoff, no jitter


# --- streaming ---------------------------------------------------------------


async def test_stream_rejects_url_denied_with_audit() -> None:
    proxy, tokens = _proxy(lambda _r: httpx.Response(200, json={}))
    route = _route(baseUrl="http://evil.example.com/v1")
    deny = _policy([ALLOW])
    with pytest.raises(ModelProxyError, match="provider_url_not_allowed"):
        await proxy.stream_responses(
            route, {"input": "hi"}, authorization=_auth(tokens), policy=deny
        )


async def test_stream_retries_exhausted_on_connect_timeout() -> None:
    def handler(_r: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("stream timed out", request=_r)

    proxy, tokens = _proxy(handler)
    with pytest.raises(ModelProxyError, match="provider_unavailable"):
        await proxy.stream_responses(
            _route(), {"input": "hi"}, authorization=_auth(tokens), policy=_policy()
        )


async def test_stream_unavailable_on_non_timeout_http_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    proxy, tokens = _proxy(handler)
    with pytest.raises(ModelProxyError, match="provider_unavailable"):
        await proxy.stream_responses(
            _route(), {"input": "hi"}, authorization=_auth(tokens), policy=_policy()
        )


async def test_stream_returns_retryable_status_on_final_attempt() -> None:
    def handler(_r: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": "overloaded"})

    proxy, tokens = _proxy(handler)
    with pytest.raises(ModelProxyError, match="provider HTTP 503"):
        await proxy.stream_responses(
            _route(), {"input": "hi"}, authorization=_auth(tokens), policy=_policy()
        )


async def test_stream_relay_rejects_oversized_frame() -> None:
    big = "x" * (5 * 1024 * 1024)
    frame = f"data: {{\"delta\": \"{big}\"}}\n\n"

    def handler(_r: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=frame, headers={"content-type": "text/event-stream"})

    proxy, tokens = _proxy(handler)
    response = await proxy.stream_responses(
        _route(), {"input": "hi"}, authorization=_auth(tokens), policy=_policy()
    )
    chunks = [chunk async for chunk in proxy.relay_stream(response)]
    assert any("Provider stream failed" in chunk for chunk in chunks)


async def test_stream_relay_redacts_credential_on_non_data_lines() -> None:
    frame = (
        f": debug token={SECRET}\n\n"
        f'data: {{"type":"output.text.delta","delta":"hi"}}\n\n'
    )

    def handler(_r: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=frame, headers={"content-type": "text/event-stream"})

    proxy, tokens = _proxy(handler)
    response = await proxy.stream_responses(
        _route(), {"input": "hi"}, authorization=_auth(tokens), policy=_policy()
    )
    rendered = "".join([chunk async for chunk in proxy.relay_stream(response)])
    assert SECRET not in rendered
    assert "[REDACTED]" in rendered


_CONFLICT_TOOLS = [
    {"type": "namespace", "name": "ns", "tools": [{"type": "function", "name": "foo"}]},
    {"type": "function", "name": "ns__foo"},
]


async def test_proxy_rejects_namespace_tool_conflict_in_body() -> None:
    proxy, tokens = _proxy(lambda _r: httpx.Response(200, json={}))
    route = _route(providerId="volcengine-ark")
    with pytest.raises(ModelProxyError, match="namespace_tool_bridge_conflict"):
        await proxy.proxy_responses(
            route, {"tools": _CONFLICT_TOOLS}, authorization=_auth(tokens), policy=_policy()
        )


async def test_stream_rejects_namespace_tool_conflict_in_body() -> None:
    proxy, tokens = _proxy(lambda _r: httpx.Response(200, json={}))
    route = _route(providerId="volcengine-ark")
    with pytest.raises(ModelProxyError, match="namespace_tool_bridge_conflict"):
        await proxy.stream_responses(
            route, {"tools": _CONFLICT_TOOLS}, authorization=_auth(tokens), policy=_policy()
        )
