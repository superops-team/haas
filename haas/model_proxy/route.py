"""ModelRoute resolution + usage normalization (specs/model-proxy §5.2)."""

from __future__ import annotations

from typing import Any

from haas.model_proxy.models import ModelRoute, Usage
from haas.registry import validate_provider_config
from haas.stores import HarnessRecord, ProviderConfig


class ModelRouteError(Exception):
    """Raised when no usable provider route exists for a harness/model.

    ``code`` is the stable wire error code. It is set to
    ``haas_provider_not_configured`` when the harness has no provider bound to
    this session (the manager has not synced a delegated profile) — a
    deterministic, non-retryable operator/config condition, distinct from a
    generic credential/secret failure. It is left ``None`` for present-but-invalid
    providers so the session layer falls back to ``haas_provider_error``.
    """

    def __init__(self, message: str, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code


def resolve_model_route(
    harness: HarnessRecord,
    model: str | None = None,
    *,
    frozen_route: dict[str, Any] | None = None,
) -> ModelRoute:
    provider = _provider_from_frozen(frozen_route) if frozen_route is not None else harness.provider
    if provider is None or not provider.baseUrl:
        raise ModelRouteError(
            f"no model provider configured for harness {harness.id}",
            code="haas_provider_not_configured",
        )
    try:
        validate_provider_config(provider)
    except ValueError as exc:
        raise ModelRouteError(str(exc)) from exc
    return ModelRoute(
        provider=provider.name,
        providerId=provider.providerId,
        name=provider.name,
        baseUrl=provider.baseUrl,
        model=model
        or (str(frozen_route.get("model")) if frozen_route else None)
        or harness.defaultModel
        or "",
        wireApi=provider.wireApi,
        apiType=provider.apiType,
        credentialRef=provider.credentialRef,
        credentialFingerprint=provider.credentialFingerprint,
        allowlistRuleId=provider.allowlistRuleId,
    )


def _provider_from_frozen(route: dict[str, Any]) -> ProviderConfig:
    return ProviderConfig(
        providerId=str(route.get("providerId") or ""),
        name=str(route.get("name") or ""),
        baseUrl=str(route.get("baseUrl") or ""),
        wireApi=str(route.get("wireApi") or ""),
        apiType=str(route.get("apiType") or ""),
        credentialRef=str(route.get("credentialRef") or ""),
        credentialFingerprint=str(route.get("credentialFingerprint") or ""),
        allowlistRuleId=str(route.get("allowlistRuleId") or ""),
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
