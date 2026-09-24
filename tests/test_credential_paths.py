"""Remaining credential-path coverage: route resolution, usage, secrets.

These sit on the model-proxy credential path, which AGENTS.md puts behind a
95% gate. Usage normalization is billing-relevant: spec 6.3 forbids
fabricating zero when a provider does not report usage.
"""

from __future__ import annotations

import pytest

from haas.model_proxy.route import ModelRouteError, normalize_usage, resolve_model_route
from haas.model_proxy.secret import InMemorySecretResolver, SecretResolutionError
from haas.stores import HarnessRecord

KEY = "sk-proj-" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4"  # haas-secret-ignore - synthetic


class _Provider:
    def __init__(self, **kw):
        self.providerId = kw.get("providerId", "openai")
        self.name = kw.get("name", "openai-compatible")
        self.baseUrl = kw.get("baseUrl", "https://api.example.com/v1")
        self.wireApi = kw.get("wireApi", "responses")
        self.apiType = kw.get("apiType", "responses")
        self.credentialRef = kw.get("credentialRef", "secret://t/p")
        self.credentialFingerprint = kw.get("credentialFingerprint", "fp_1")
        self.allowlistRuleId = kw.get("allowlistRuleId", "rule_1")


def _harness(provider=None, default_model: str | None = "gpt-default") -> HarnessRecord:
    record = HarnessRecord(id="chrn_x", base="codex", name="x")
    record.provider = provider
    record.defaultModel = default_model
    return record


# --- route resolution -------------------------------------------------------


def test_route_uses_explicit_model_over_default() -> None:
    route = resolve_model_route(_harness(_Provider()), "gpt-explicit")
    assert route.model == "gpt-explicit"
    assert route.credentialRef == "secret://t/p"
    assert route.allowlistRuleId == "rule_1"


def test_route_falls_back_to_harness_default_model() -> None:
    assert resolve_model_route(_harness(_Provider())).model == "gpt-default"


def test_route_model_is_empty_when_nothing_declared() -> None:
    assert resolve_model_route(_harness(_Provider(), default_model=None)).model == ""


def test_route_without_provider_raises() -> None:
    with pytest.raises(ModelRouteError, match="no model provider"):
        resolve_model_route(_harness(None))


def test_route_with_blank_base_url_raises() -> None:
    """A provider entry with no baseUrl is unusable, not a silent default."""
    with pytest.raises(ModelRouteError, match="no model provider"):
        resolve_model_route(_harness(_Provider(baseUrl="")))


# --- usage normalization ----------------------------------------------------


@pytest.mark.parametrize("body", [None, "text", 42, [], {"usage": "nope"}, {"usage": []}])
def test_usage_none_for_unusable_bodies(body: object) -> None:
    assert normalize_usage("openai-compatible", body) is None


def test_usage_none_when_provider_reports_nothing() -> None:
    """Never fabricate zero usage (spec 6.3) - it would understate billing."""
    assert normalize_usage("openai-compatible", {"usage": {}}) is None
    assert normalize_usage("openai-compatible", {"usage": {"other": 5}}) is None


def test_usage_openai_snake_case() -> None:
    usage = normalize_usage(
        "openai-compatible",
        {"usage": {"input_tokens": 10, "output_tokens": 4, "total_tokens": 14}},
    )
    assert (usage.inputTokens, usage.outputTokens, usage.totalTokens) == (10, 4, 14)


def test_usage_chat_completions_aliases() -> None:
    usage = normalize_usage(
        "openai-compatible", {"usage": {"prompt_tokens": 7, "completion_tokens": 3}}
    )
    assert (usage.inputTokens, usage.outputTokens) == (7, 3)


def test_usage_camel_case_aliases() -> None:
    usage = normalize_usage(
        "anthropic", {"usage": {"inputTokens": 5, "outputTokens": 6, "totalTokens": 11}}
    )
    assert usage.totalTokens == 11


def test_total_is_derived_when_provider_omits_it() -> None:
    usage = normalize_usage("openai-compatible", {"usage": {"input_tokens": 8, "output_tokens": 5}})
    assert usage.totalTokens == 13


def test_total_is_not_derived_from_a_single_side() -> None:
    """With only one side known the total stays 0 rather than being guessed."""
    usage = normalize_usage("openai-compatible", {"usage": {"input_tokens": 8}})
    assert usage.inputTokens == 8
    assert usage.outputTokens == 0
    assert usage.totalTokens == 0


def test_usage_reads_dotted_cache_path() -> None:
    usage = normalize_usage(
        "openai-compatible",
        {
            "usage": {
                "input_tokens": 1,
                "output_tokens": 1,
                "input_tokens_details": {"cached_tokens": 9},
            }
        },
    )
    assert usage.cacheReadTokens == 9


def test_usage_dotted_path_survives_non_dict_midway() -> None:
    usage = normalize_usage(
        "openai-compatible",
        {"usage": {"input_tokens": 1, "output_tokens": 1, "input_tokens_details": 5}},
    )
    assert usage.cacheReadTokens == 0


def test_usage_cache_write_aliases() -> None:
    usage = normalize_usage(
        "anthropic",
        {"usage": {"input_tokens": 1, "output_tokens": 1, "cache_creation": 3}},
    )
    assert usage.cacheWriteTokens == 3


def test_bool_is_not_accepted_as_token_count() -> None:
    """bool is a subclass of int; True must not become 1 token."""
    usage = normalize_usage(
        "openai-compatible",
        {"usage": {"input_tokens": True, "prompt_tokens": 6, "output_tokens": 2}},
    )
    assert usage.inputTokens == 6


def test_usage_ignores_non_int_values() -> None:
    assert (
        normalize_usage(
            "openai-compatible", {"usage": {"input_tokens": "12", "output_tokens": None}}
        )
        is None
    )


# --- secret resolution ------------------------------------------------------


async def test_secret_resolver_returns_registered_value() -> None:
    resolver = InMemorySecretResolver({"secret://t/p": KEY})
    assert await resolver.resolve("secret://t/p") == KEY


async def test_secret_resolver_register_after_construction() -> None:
    resolver = InMemorySecretResolver()
    resolver.register("secret://t/p", KEY)
    assert await resolver.resolve("secret://t/p") == KEY


async def test_unknown_credential_ref_raises() -> None:
    resolver = InMemorySecretResolver()
    with pytest.raises(SecretResolutionError, match="credential ref not found") as excinfo:
        await resolver.resolve("secret://t/missing")
    # P2-14: the internal credential ref must not be echoed into the message.
    assert "secret://t/missing" not in str(excinfo.value)


async def test_resolver_error_does_not_leak_other_secrets() -> None:
    """The failure message must not expose the values it holds."""
    resolver = InMemorySecretResolver({"secret://t/p": KEY})
    with pytest.raises(SecretResolutionError) as excinfo:
        await resolver.resolve("secret://t/other")
    assert KEY not in str(excinfo.value)


async def test_resolver_copies_input_mapping() -> None:
    """Mutating the caller's dict afterwards must not change the resolver."""
    source = {"secret://t/p": KEY}
    resolver = InMemorySecretResolver(source)
    source["secret://t/p"] = "tampered"
    assert await resolver.resolve("secret://t/p") == KEY
