"""Model capability lifecycle tests (specs/model-proxy/README.md section 7)."""

from __future__ import annotations

import time
from typing import Any

import pytest

from haas.model_proxy.models import RuntimeTokenScope
from haas.model_proxy.runtime import RuntimeModelProxy
from haas.model_proxy.token import RuntimeTokenError, RuntimeTokenManager
from haas.stores import HarnessRecord, InvocationRecord


class _Resolver:
    available = True

    async def resolve_for(self, route: object, scope: RuntimeTokenScope) -> str:
        return "fixture-provider-key"

    def close(self) -> None:
        self.available = False


def _harness(*, timeout_seconds: int = 120) -> HarnessRecord:
    return HarnessRecord(
        id="chrn_codex_default",
        name="codex-default",
        base="codex",
        defaultModel="gpt-test",
        timeoutSeconds=timeout_seconds,
    )


def _profile() -> dict[str, Any]:
    return {
        "provider": {
            "providerId": "openai",
            "name": "openai",
            "baseUrl": "https://provider.example/v1",
            "model": "gpt-test",
            "wireApi": "responses",
            "apiType": "responses",
            "credentialRef": "secret://provider/test",
        }
    }


def _invocation(
    *, invocation_id: str = "inv_1", started_at_ms: int | None = None
) -> InvocationRecord:
    return InvocationRecord(
        id=invocation_id,
        sessionId="hsess_1",
        appName="chrn_codex_default",
        turnId="turn_1",
        startedAtMs=started_at_ms or int(time.time() * 1000),
    )


async def test_initial_capability_outlives_invocation_deadline_and_cleanup_margin() -> None:
    runtime = RuntimeModelProxy(
        registry=object(),  # type: ignore[arg-type]
        resolver=_Resolver(),  # type: ignore[arg-type]
        listen="127.0.0.1:0",
        cleanup_margin_seconds=30,
    )
    runtime.base_url = "http://127.0.0.1:18080/v1"

    started_at_ms = int(time.time() * 1000)
    credentials = await runtime.begin(
        _harness(), _invocation(started_at_ms=started_at_ms), _profile()
    )

    scope = runtime.tokens.validate(credentials["token"])
    assert scope.expiresAtMs == started_at_ms + 120_000 + 30_000


async def test_refresh_preserves_exact_scope_until_replacement_is_applied() -> None:
    runtime = RuntimeModelProxy(
        registry=object(),  # type: ignore[arg-type]
        resolver=_Resolver(),  # type: ignore[arg-type]
        listen="127.0.0.1:0",
    )
    runtime.base_url = "http://127.0.0.1:18080/v1"
    invocation = _invocation()
    credentials = await runtime.begin(_harness(), invocation, _profile())
    original_token = credentials["token"]
    original_scope = runtime.tokens.validate(original_token)

    replacement = runtime.refresh(invocation, original_token)

    assert replacement["token"] != original_token
    replacement_scope = runtime.tokens.validate(replacement["token"])
    assert (
        replacement_scope.audience,
        replacement_scope.sessionId,
        replacement_scope.invocationId,
        replacement_scope.harnessId,
        replacement_scope.allowedModels,
    ) == (
        original_scope.audience,
        original_scope.sessionId,
        original_scope.invocationId,
        original_scope.harnessId,
        original_scope.allowedModels,
    )
    assert replacement_scope.expiresAtMs >= original_scope.expiresAtMs
    assert replacement_scope is not original_scope
    assert replacement_scope.allowedModels is not original_scope.allowedModels
    assert runtime.tokens.validate(original_token) == original_scope
    with pytest.raises(RuntimeTokenError, match="refresh_already_attempted"):
        runtime.refresh(invocation, original_token)
    with pytest.raises(RuntimeTokenError, match="invalid_token"):
        runtime.refresh(_invocation(invocation_id="inv_other"), replacement["token"])

    runtime.apply_refresh(invocation, original_token, replacement["token"])

    with pytest.raises(RuntimeTokenError, match="invalid_token"):
        runtime.tokens.validate(original_token)
    assert runtime.tokens.validate(replacement["token"]) == replacement_scope


async def test_apply_refresh_rejects_a_different_scope() -> None:
    runtime = RuntimeModelProxy(
        registry=object(),  # type: ignore[arg-type]
        resolver=_Resolver(),  # type: ignore[arg-type]
        listen="127.0.0.1:0",
    )
    runtime.base_url = "http://127.0.0.1:18080/v1"
    invocation = _invocation()
    credentials = await runtime.begin(_harness(), invocation, _profile())
    wrong_scope = runtime.tokens.issue(
        RuntimeTokenScope(
            sessionId=invocation.sessionId,
            invocationId=invocation.id,
            harnessId="chrn_other",
            allowedModels=["other-model"],
        )
    )

    with pytest.raises(RuntimeTokenError, match="invalid_token"):
        runtime.apply_refresh(invocation, credentials["token"], wrong_scope)
    assert runtime.tokens.validate(credentials["token"]).harnessId == invocation.appName


async def test_expired_active_capability_can_refresh_once_without_scope_widening() -> None:
    runtime = RuntimeModelProxy(
        registry=object(),  # type: ignore[arg-type]
        resolver=_Resolver(),  # type: ignore[arg-type]
        listen="127.0.0.1:0",
    )
    runtime.base_url = "http://127.0.0.1:18080/v1"
    invocation = _invocation()
    credentials = await runtime.begin(_harness(), invocation, _profile())
    original_scope = runtime.tokens.scope(credentials["token"], allow_expired=True)
    original_scope.expiresAtMs = int(time.time() * 1000) - 1

    replacement = runtime.refresh(invocation, credentials["token"])

    replacement_scope = runtime.tokens.validate(replacement["token"])
    assert replacement_scope.expiresAtMs > original_scope.expiresAtMs
    assert (
        replacement_scope.audience,
        replacement_scope.sessionId,
        replacement_scope.invocationId,
        replacement_scope.harnessId,
        replacement_scope.allowedModels,
    ) == (
        original_scope.audience,
        original_scope.sessionId,
        original_scope.invocationId,
        original_scope.harnessId,
        original_scope.allowedModels,
    )


async def test_terminal_revokes_all_invocation_tokens_and_route() -> None:
    runtime = RuntimeModelProxy(
        registry=object(),  # type: ignore[arg-type]
        resolver=_Resolver(),  # type: ignore[arg-type]
        listen="127.0.0.1:0",
    )
    runtime.base_url = "http://127.0.0.1:18080/v1"
    invocation = _invocation()
    credentials = await runtime.begin(_harness(), invocation, _profile())
    replacement = runtime.refresh(invocation, credentials["token"])

    runtime.end(invocation, credentials)

    assert runtime.route(invocation.sessionId, invocation.id) is None
    for token in (credentials["token"], replacement["token"]):
        with pytest.raises(RuntimeTokenError, match="invalid_token"):
            runtime.tokens.validate(token)


def test_expired_and_invalid_tokens_remain_distinguishable() -> None:
    tokens = RuntimeTokenManager(ttl_seconds=-1)
    expired = tokens.issue(RuntimeTokenScope(sessionId="hsess_1"))

    for _ in range(2):
        with pytest.raises(RuntimeTokenError, match="token_expired"):
            tokens.validate(expired)
    with pytest.raises(RuntimeTokenError, match="invalid_token"):
        tokens.validate("never-issued")

    tokens.revoke(expired)
    with pytest.raises(RuntimeTokenError, match="invalid_token"):
        tokens.validate(expired)
