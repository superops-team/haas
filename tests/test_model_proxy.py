"""Model Proxy core tests: route resolution, token lifecycle, usage normalization, secrets."""

from __future__ import annotations

import pytest

from haas.model_proxy import (
    InMemorySecretResolver,
    ModelRouteError,
    RuntimeTokenError,
    RuntimeTokenManager,
    RuntimeTokenScope,
    SecretResolutionError,
    normalize_usage,
    resolve_model_route,
)
from haas.stores import HarnessRecord, ProviderConfig


def _harness(provider: ProviderConfig | None) -> HarnessRecord:
    return HarnessRecord(
        id="chrn_codex_default",
        name="codex-default",
        base="codex",
        defaultModel="gpt-5.6-terra",
        provider=provider,
    )


# --- route resolution -------------------------------------------------------


def test_resolve_model_route() -> None:
    provider = ProviderConfig(
        providerId="openai",
        name="openai-compatible",
        baseUrl="http://127.0.0.1:18080/v1",
        wireApi="responses",
        credentialRef="secret://tenant/provider",
    )
    route = resolve_model_route(_harness(provider), model="gpt-x")
    assert route.provider == "openai-compatible"
    assert route.providerId == "openai"
    assert route.name == "openai-compatible"
    assert route.apiType == "responses"
    assert route.baseUrl == "http://127.0.0.1:18080/v1"
    assert route.model == "gpt-x"
    assert route.credentialRef == "secret://tenant/provider"


def test_resolve_model_route_defaults_to_harness_model() -> None:
    provider = ProviderConfig(baseUrl="http://127.0.0.1:18080/v1")
    route = resolve_model_route(_harness(provider))
    assert route.model == "gpt-5.6-terra"


def test_resolve_model_route_requires_provider() -> None:
    with pytest.raises(ModelRouteError):
        resolve_model_route(_harness(None))


def test_resolve_model_route_requires_base_url() -> None:
    with pytest.raises(ModelRouteError):
        resolve_model_route(_harness(ProviderConfig(baseUrl="")))


@pytest.mark.parametrize(
    ("provider", "reason"),
    [
        (ProviderConfig(providerId="", baseUrl="https://example.com"), "providerId"),
        (ProviderConfig(name="", baseUrl="https://example.com"), "name"),
        (
            ProviderConfig(providerId="openai", baseUrl="https://example.com", wireApi="unknown"),
            "wireApi",
        ),
        (
            ProviderConfig(
                providerId="openai",
                baseUrl="https://example.com",
                wireApi="openai-compatible",
                apiType="",
            ),
            "apiType",
        ),
        (
            ProviderConfig(
                providerId="openai",
                baseUrl="https://example.com",
                wireApi="responses",
                apiType="chat_completions",
            ),
            "apiType",
        ),
    ],
)
def test_resolve_model_route_validates_contract(provider: ProviderConfig, reason: str) -> None:
    with pytest.raises(ModelRouteError, match=reason):
        resolve_model_route(_harness(provider))


def test_resolve_model_route_prefers_frozen_session_route() -> None:
    live = ProviderConfig(
        providerId="live",
        name="live",
        baseUrl="https://live.example.com/v1",
        wireApi="responses",
        apiType="responses",
        credentialRef="secret://live",
    )
    frozen = {
        "providerId": "frozen",
        "name": "frozen",
        "baseUrl": "https://frozen.example.com/v1",
        "model": "frozen-model",
        "wireApi": "openai-compatible",
        "apiType": "responses",
        "credentialRef": "secret://frozen",
    }
    route = resolve_model_route(_harness(live), frozen_route=frozen)
    assert route.providerId == "frozen"
    assert route.model == "frozen-model"
    assert route.credentialRef == "secret://frozen"


# --- runtime token ----------------------------------------------------------


async def test_token_issue_and_validate() -> None:
    mgr = RuntimeTokenManager()
    scope = RuntimeTokenScope(sessionId="s_1", harnessId="chrn_1")
    token = mgr.issue(scope)
    validated = mgr.validate(token)
    assert validated.sessionId == "s_1"


async def test_token_rejects_invalid() -> None:
    with pytest.raises(RuntimeTokenError, match="invalid_token"):
        RuntimeTokenManager().validate("bogus")


async def test_token_expires() -> None:
    mgr = RuntimeTokenManager(ttl_seconds=-1)  # already expired
    token = mgr.issue(RuntimeTokenScope(sessionId="s_1"))
    with pytest.raises(RuntimeTokenError, match="token_expired"):
        mgr.validate(token)


# --- usage normalization ----------------------------------------------------


def test_normalize_usage_openai_responses() -> None:
    usage = normalize_usage("openai", {"usage": {"input_tokens": 10, "output_tokens": 5}})
    assert usage is not None
    assert usage.inputTokens == 10
    assert usage.outputTokens == 5
    assert usage.totalTokens == 15


def test_normalize_usage_chat_completions() -> None:
    usage = normalize_usage(
        "openai", {"usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}}
    )
    assert usage is not None
    assert usage.inputTokens == 3
    assert usage.outputTokens == 2
    assert usage.totalTokens == 5


def test_normalize_usage_unknown_returns_none() -> None:
    assert normalize_usage("openai", {"no_usage": True}) is None
    assert normalize_usage("openai", {}) is None


# --- secret resolver --------------------------------------------------------


async def test_secret_resolver_resolves_registered_ref() -> None:
    resolver = InMemorySecretResolver({"secret://tenant/provider": "sk-test"})
    assert await resolver.resolve("secret://tenant/provider") == "sk-test"


async def test_secret_resolver_rejects_unknown_ref() -> None:
    with pytest.raises(SecretResolutionError):
        await InMemorySecretResolver().resolve("secret://missing")
