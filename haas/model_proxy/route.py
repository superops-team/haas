"""ModelRoute resolution + usage normalization (specs/model-proxy §5.2)."""
from __future__ import annotations

from typing import Any

from haas.model_proxy.models import ModelRoute, Usage
from haas.stores import HarnessRecord


class ModelRouteError(Exception):
    """Raised when no usable provider route exists for a harness/model."""


def resolve_model_route(harness: HarnessRecord, model: str | None = None) -> ModelRoute:
    provider = harness.provider
    if provider is None or not provider.baseUrl:
        raise ModelRouteError(f"no model provider configured for harness {harness.id}")
    return ModelRoute(
        provider=provider.name,
        baseUrl=provider.baseUrl,
        model=model or harness.defaultModel or "",
        wireApi=provider.wireApi,
        credentialRef=provider.credentialRef,
        credentialFingerprint=provider.credentialFingerprint,
        allowlistRuleId=provider.allowlistRuleId,
    )


def normalize_usage(provider: str, body: Any) -> Usage | None:
    """Extract canonical token usage from a provider response body.

    Returns ``None`` when usage is unknown (never fabricates zero, spec §6.3).
    """
    if not isinstance(body, dict):
        return None
    usage = body.get("usage")
    if not isinstance(usage, dict):
        return None

    input_tokens = _first_int(usage, "input_tokens", "prompt_tokens", "inputTokens")
    output_tokens = _first_int(usage, "output_tokens", "completion_tokens", "outputTokens")
    total_tokens = _first_int(usage, "total_tokens", "totalTokens")
    cache_read = _first_int(usage, "input_tokens_details.cached_tokens", "cache_read_input_tokens")
    cache_write = _first_int(usage, "cache_creation", "cache_write_tokens")

    if input_tokens is None and output_tokens is None and total_tokens is None:
        return None
    if total_tokens is None and input_tokens is not None and output_tokens is not None:
        total_tokens = input_tokens + output_tokens

    return Usage(
        inputTokens=input_tokens or 0,
        outputTokens=output_tokens or 0,
        totalTokens=total_tokens or 0,
        cacheReadTokens=cache_read or 0,
        cacheWriteTokens=cache_write or 0,
    )


def _first_int(usage: dict[str, Any], *keys: str) -> int | None:
    for key in keys:
        # support dotted paths like "input_tokens_details.cached_tokens"
        value: Any = usage
        for part in key.split("."):
            if not isinstance(value, dict):
                value = None
                break
            value = value.get(part)
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            return value
    return None
