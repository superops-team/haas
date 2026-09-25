"""Provider-failure and lifecycle coverage for ModelProxy.

test_model_proxy_relay.py covers the happy path and authorization rejections;
this file covers what happens when the upstream provider misbehaves, plus the
client lifecycle branches. AGENTS.md puts proxy/credential paths behind a 95%
gate, and these are exactly the paths that run during an incident.
"""

from __future__ import annotations

import json
from typing import Any

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
    with pytest.raises(ModelProxyError) as excinfo:
        await _call(proxy, tokens)
    assert str(excinfo.value) == f'provider HTTP {status}: {{"message":"nope"}}'


async def test_provider_non_json_response_raises() -> None:
    proxy, tokens = _proxy(lambda _r: httpx.Response(200, text="<html>gateway timeout</html>"))
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
    assert message == 'provider HTTP 401: {"message":"bad key"}'
    # The status code must still be actionable for the caller.
    assert "provider HTTP 401" in message


async def test_recursive_redaction_handles_credential_with_json_special_chars() -> None:
    """Credential containing ``"`` and ``\\`` must not leak (S4-002).

    The old implementation did ``json.dumps(data).replace(credential, ...)``.
    When the credential contains a quote or backslash, ``json.dumps`` escapes
    them (``"`` -> ``\\"``), so the raw credential substring no longer appears in
    the serialized form and ``str.replace`` silently misses. Redacting the
    deserialized object recursively on the raw string values closes the gap.
    """
    credential = 'sk-real"key\\backslash'  # haas-secret-ignore - synthetic

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "resp_special",
                "nested": {"note": f"server recorded {credential} here"},
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    tokens = RuntimeTokenManager()
    proxy = ModelProxy(
        InMemorySecretResolver({"secret://tenant/provider": credential}),
        tokens,
        PolicyController(),
        client=client,
    )
    token = tokens.issue(RuntimeTokenScope(sessionId="s_1"))
    data, _usage = await proxy.proxy_responses(
        _route(), {"input": "hi"}, authorization=f"Bearer {token}", policy=_policy()
    )
    rendered = json.dumps(data)
    assert credential not in rendered
    assert "[REDACTED]" in rendered


async def test_stream_relay_redacts_credential_with_json_special_chars() -> None:
    """Stream relay must redact before re-serializing, not string-replace after."""
    credential = 'sk-real"key\\backslash'  # haas-secret-ignore - synthetic
    payload = {
        "type": "response.output_text.delta",
        "delta": f"echoed {credential} back",
    }
    # Build a valid SSE frame via json.dumps so the quote/backslash are escaped
    # on the wire (as a real provider would emit).
    frame = "data: " + json.dumps(payload, separators=(",", ":")) + "\n\n"

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=frame, headers={"content-type": "text/event-stream"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    tokens = RuntimeTokenManager()
    proxy = ModelProxy(
        InMemorySecretResolver({"secret://tenant/provider": credential}),
        tokens,
        PolicyController(),
        client=client,
    )
    token = tokens.issue(RuntimeTokenScope(sessionId="s_1"))
    response = await proxy.stream_responses(
        _route(), {"input": "hi"}, authorization=f"Bearer {token}", policy=_policy()
    )
    chunks = [chunk async for chunk in proxy.relay_stream(response)]
    rendered = "".join(chunks)
    assert credential not in rendered
    assert "[REDACTED]" in rendered


async def test_empty_authorization_is_rejected() -> None:
    proxy, _ = _proxy(lambda _r: httpx.Response(200, json={}))
    with pytest.raises(ModelProxyError):
        await proxy.proxy_responses(_route(), {"input": "hi"}, authorization="", policy=_policy())


async def test_usage_absent_is_tolerated() -> None:
    """A provider may omit usage; that is not an error."""
    proxy, tokens = _proxy(lambda _r: httpx.Response(200, json={"output": []}))
    data, usage = await _call(proxy, tokens)
    assert data == {"output": []}
    assert usage is None


# --- audit events (P1-4) ----------------------------------------------------


class _RecordingLogger:
    """Captures StructuredLogger.event calls without writing to stdout."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def event(self, name: str, fields: dict[str, Any]) -> dict[str, Any]:
        self.events.append((name, dict(fields)))
        return {"event": name, **fields}


_ALLOWED_AUDIT_FIELDS = {
    "sessionId",
    "harnessId",
    "providerCode",
    "statusCode",
    "latencyMs",
}


async def test_auth_failure_emits_audit_event_without_secrets() -> None:
    """Invalid/expired token -> model_proxy_auth_failed, no credential/body."""
    logger = _RecordingLogger()
    tokens = RuntimeTokenManager()
    proxy = ModelProxy(
        InMemorySecretResolver({}),
        tokens,
        PolicyController(),
        client=httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _r: httpx.Response(200, json={}))
        ),
        logger=logger,  # type: ignore[arg-type]
    )
    with pytest.raises(ModelProxyError):
        await proxy.authenticate("Bearer not-a-real-token")

    assert logger.events, "expected an audit event on auth failure"
    name, fields = logger.events[0]
    assert name == "model_proxy_auth_failed"
    # Whitelist: only stable, non-sensitive identifiers may appear.
    assert set(fields) <= _ALLOWED_AUDIT_FIELDS
    # Reverse assertions: nothing secret may ever be recorded.
    for forbidden in (
        "credential", "token", "authorization", "body", "apiKey", "api_key", "secret"
    ):
        assert forbidden not in fields


async def test_url_denial_emits_audit_event() -> None:
    logger = _RecordingLogger()
    tokens = RuntimeTokenManager()
    proxy = ModelProxy(
        InMemorySecretResolver({"secret://tenant/provider": SECRET}),
        tokens,
        PolicyController(),
        client=httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _r: httpx.Response(200, json={}))
        ),
        logger=logger,  # type: ignore[arg-type]
    )
    token = tokens.issue(RuntimeTokenScope(sessionId="s_1"))
    route = ModelRoute(
        provider="openai-compatible",
        baseUrl="http://evil.example.com/v1",
        model="gpt-x",
        credentialRef="secret://tenant/provider",
    )
    deny_policy = PolicyController().compile(
        PolicyCompileInput(
            scope=PolicyScope(tenantId="t1"),
            layers=[
                PolicyLayer(
                    "tenant",
                    network=NetworkPolicy(
                        defaultAction="deny", allow=["http://127.0.0.1:18080"]
                    ),
                )
            ],
        )
    )
    with pytest.raises(ModelProxyError, match="provider_url_not_allowed"):
        await proxy.proxy_responses(
            route, {}, authorization=f"Bearer {token}", policy=deny_policy
        )
    assert any(name == "model_proxy_url_denied" for name, _ in logger.events)
    for _name, fields in logger.events:
        assert set(fields) <= _ALLOWED_AUDIT_FIELDS


async def test_provider_error_emits_audit_event() -> None:
    logger = _RecordingLogger()
    tokens = RuntimeTokenManager()
    proxy = ModelProxy(
        InMemorySecretResolver({"secret://tenant/provider": SECRET}),
        tokens,
        PolicyController(),
        client=httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _r: httpx.Response(502, json={"error": "x"}))
        ),
        logger=logger,  # type: ignore[arg-type]
    )
    token = tokens.issue(RuntimeTokenScope(sessionId="s_1"))
    with pytest.raises(ModelProxyError):
        await proxy.proxy_responses(
            _route(), {"input": "hi"}, authorization=f"Bearer {token}", policy=_policy()
        )
    provider_errors = [f for n, f in logger.events if n == "model_proxy_provider_error"]
    assert provider_errors
    assert provider_errors[0]["statusCode"] == 502
    assert set(provider_errors[0]) <= _ALLOWED_AUDIT_FIELDS


async def test_stream_interruption_emits_audit_event() -> None:
    logger = _RecordingLogger()
    tokens = RuntimeTokenManager()
    # 200 + SSE headers, then a malformed (non-object) frame -> relay hits
    # ValueError mid-stream -> stream-interruption audit event.
    frame = 'data: []\n\n'

    def handler(_r: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=frame, headers={"content-type": "text/event-stream"})

    proxy = ModelProxy(
        InMemorySecretResolver({"secret://tenant/provider": SECRET}),
        tokens,
        PolicyController(),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        logger=logger,  # type: ignore[arg-type]
    )
    token = tokens.issue(RuntimeTokenScope(sessionId="s_1"))
    response = await proxy.stream_responses(
        _route(), {"input": "hi"}, authorization=f"Bearer {token}", policy=_policy()
    )
    chunks = [chunk async for chunk in proxy.relay_stream(response)]
    assert any(name == "model_proxy_stream_interrupted" for name, _ in logger.events)
    assert any(SECRET not in chunk for chunk in chunks)


# --- client lifecycle -------------------------------------------------------


async def test_close_is_noop_for_injected_client() -> None:
    """An injected client is owned by the caller and must not be closed."""
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _r: httpx.Response(200, json={}))
    )
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
    proxy, tokens = _proxy(
        lambda _r: httpx.Response(500, json={"error": {"message": "x" * 10000}})
    )
    with pytest.raises(ModelProxyError) as excinfo:
        await _call(proxy, tokens)
    assert "<truncated>" in str(excinfo.value)
    assert len(str(excinfo.value)) < 1000


async def test_empty_provider_body_is_reported() -> None:
    proxy, tokens = _proxy(lambda _r: httpx.Response(502, text=""))
    with pytest.raises(ModelProxyError, match="^provider HTTP 502$"):
        await _call(proxy, tokens)


async def test_non_json_provider_error_body_is_not_relayed() -> None:
    proxy, tokens = _proxy(
        lambda _r: httpx.Response(502, text="<html>internal gateway detail</html>")
    )
    with pytest.raises(ModelProxyError, match="^provider HTTP 502$"):
        await _call(proxy, tokens)


async def test_structured_provider_error_keeps_only_diagnostic_fields() -> None:
    proxy, tokens = _proxy(
        lambda _r: httpx.Response(
            400,
            json={
                "error": {
                    "type": "invalid_request_error",
                    "code": "unsupported_parameter",
                    "param": "reasoning.effort",
                    "message": "reasoning.effort is unsupported",
                    "request_body": {"input": "must not escape"},
                },
                "request_id": "must-not-escape",
            },
        )
    )
    with pytest.raises(ModelProxyError) as excinfo:
        await _call(proxy, tokens)
    message = str(excinfo.value)
    assert message == (
        'provider HTTP 400: {"code":"unsupported_parameter",'
        '"type":"invalid_request_error","param":"reasoning.effort",'
        '"message":"reasoning.effort is unsupported"}'
    )
    assert "request_body" not in message
    assert "request_id" not in message


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
            _route(),
            {"input": "hi"},
            authorization=f"Bearer {token}",
            policy=_policy(),
        )


async def test_revoking_an_unknown_token_is_a_noop() -> None:
    tokens = RuntimeTokenManager()
    tokens.revoke("never-issued")
