"""Model capability lifecycle tests (specs/model-proxy/README.md section 7)."""

from __future__ import annotations

import time
from typing import Any

import pytest

from haas.model_proxy.models import RuntimeTokenScope
from haas.model_proxy.runtime import RuntimeModelProxy
from haas.model_proxy.token import RuntimeTokenError, RuntimeTokenManager
from haas.stores import HarnessRecord, InvocationRecord, MemoryStore, SessionRecord


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


def _provider_scope_key(profile: dict[str, Any]) -> str:
    return RuntimeModelProxy.provider_scope_key(profile["provider"])


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
    invocation = _invocation(started_at_ms=started_at_ms)
    invocation.timeoutSeconds = 86_400
    credentials = await runtime.begin(_harness(), invocation, _profile())

    scope = runtime.tokens.validate(credentials["token"])
    assert scope.expiresAtMs == started_at_ms + 86_400_000 + 30_000
    assert scope.invocationId == ""
    assert scope.providerScopeKey == _provider_scope_key(_profile())
    assert scope.generation == 1
    assert scope.allowedModels == []


async def test_initial_capability_clamps_harness_timeout_to_24h() -> None:
    runtime = RuntimeModelProxy(
        registry=object(),  # type: ignore[arg-type]
        resolver=_Resolver(),  # type: ignore[arg-type]
        listen="127.0.0.1:0",
        cleanup_margin_seconds=30,
    )
    runtime.base_url = "http://127.0.0.1:18080/v1"

    started_at_ms = int(time.time() * 1000)
    invocation = _invocation(started_at_ms=started_at_ms)
    invocation.timeoutSeconds = 172_800
    credentials = await runtime.begin(
        _harness(timeout_seconds=172_800),
        invocation,
        _profile(),
    )

    scope = runtime.tokens.validate(credentials["token"])
    assert scope.expiresAtMs == started_at_ms + 86_400_000 + 30_000


async def test_refresh_preserves_exact_scope_until_replacement_is_applied() -> None:
    store = MemoryStore()
    store.put_session(SessionRecord(id="hsess_1", appName="chrn_codex_default", userId=""))
    runtime = RuntimeModelProxy(
        registry=object(),  # type: ignore[arg-type]
        resolver=_Resolver(),  # type: ignore[arg-type]
        listen="127.0.0.1:0",
        store=store,
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
        replacement_scope.harnessId,
        replacement_scope.providerScopeKey,
    ) == (
        original_scope.audience,
        original_scope.sessionId,
        original_scope.harnessId,
        original_scope.providerScopeKey,
    )
    assert replacement_scope.invocationId == ""
    assert replacement_scope.generation == original_scope.generation + 1
    assert replacement_scope.expiresAtMs >= original_scope.expiresAtMs
    assert replacement_scope is not original_scope
    assert replacement_scope.allowedModels is not original_scope.allowedModels
    assert runtime.tokens.validate(original_token) == original_scope
    with pytest.raises(RuntimeTokenError, match="refresh_already_attempted"):
        runtime.refresh(invocation, original_token)

    runtime.apply_refresh(invocation, original_token, replacement["token"])

    with pytest.raises(RuntimeTokenError, match="invalid_token"):
        runtime.tokens.validate(original_token)
    assert runtime.tokens.validate(replacement["token"]) == replacement_scope
    persisted = store.get_session(("chrn_codex_default", "", "hsess_1"))
    assert persisted is not None
    assert persisted.state["modelProxy"]["activeTokenFingerprint"] == runtime.tokens.fingerprint(
        replacement["token"]
    )


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
            harnessId="chrn_other",
            allowedModels=["other-model"],
            providerScopeKey=_provider_scope_key(_profile()),
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
        replacement_scope.harnessId,
        replacement_scope.providerScopeKey,
    ) == (
        original_scope.audience,
        original_scope.sessionId,
        original_scope.harnessId,
        original_scope.providerScopeKey,
    )
    assert replacement_scope.generation == original_scope.generation + 1


async def test_terminal_keeps_session_capability_until_session_revoke() -> None:
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

    route = runtime.route(invocation.sessionId)
    assert route is not None
    assert route["providerId"] == "openai"
    assert route["model"] == "gpt-test"
    assert route["credentialRef"] == "secret://provider/test"
    for token in (credentials["token"], replacement["token"]):
        assert runtime.tokens.validate(token).sessionId == invocation.sessionId

    runtime.revoke_session(invocation.sessionId)

    assert runtime.route(invocation.sessionId) is None
    for token in (credentials["token"], replacement["token"]):
        with pytest.raises(RuntimeTokenError, match="invalid_token"):
            runtime.tokens.validate(token)


async def test_same_session_followup_reuses_capability_after_previous_terminal() -> None:
    runtime = RuntimeModelProxy(
        registry=object(),  # type: ignore[arg-type]
        resolver=_Resolver(),  # type: ignore[arg-type]
        listen="127.0.0.1:0",
    )
    runtime.base_url = "http://127.0.0.1:18080/v1"
    first = _invocation(invocation_id="inv_first")
    first_credentials = await runtime.begin(_harness(), first, _profile())
    first_scope = runtime.tokens.validate(first_credentials["token"])
    runtime.end(first, first_credentials)

    second = _invocation(invocation_id="inv_second")
    second_credentials = await runtime.begin(_harness(), second, _profile())

    assert second_credentials["token"] == first_credentials["token"]
    second_scope = runtime.tokens.validate(second_credentials["token"])
    assert second_scope.generation == first_scope.generation
    assert second_scope.invocationId == ""
    route = runtime.route("hsess_1")
    assert route is not None
    assert route["providerId"] == "openai"
    assert route["model"] == "gpt-test"
    assert route["credentialRef"] == "secret://provider/test"


async def test_same_session_followup_renews_expired_reused_token() -> None:
    runtime = RuntimeModelProxy(
        registry=object(),  # type: ignore[arg-type]
        resolver=_Resolver(),  # type: ignore[arg-type]
        listen="127.0.0.1:0",
    )
    runtime.base_url = "http://127.0.0.1:18080/v1"
    first = _invocation(invocation_id="inv_first")
    first.timeoutSeconds = 1
    credentials = await runtime.begin(_harness(), first, _profile())
    runtime.tokens.scope(credentials["token"], allow_expired=True).expiresAtMs = 1
    runtime.end(first, credentials)

    second = _invocation(invocation_id="inv_second")
    second.timeoutSeconds = 86_400
    reused = await runtime.begin(_harness(), second, _profile())

    assert reused["token"] == credentials["token"]
    assert runtime.tokens.validate(reused["token"]).expiresAtMs == (
        second.startedAtMs + 86_400_000 + 30_000
    )


async def test_same_session_model_change_with_same_provider_scope_keeps_token_usable() -> None:
    runtime = RuntimeModelProxy(
        registry=object(),  # type: ignore[arg-type]
        resolver=_Resolver(),  # type: ignore[arg-type]
        listen="127.0.0.1:0",
    )
    runtime.base_url = "http://127.0.0.1:18080/v1"
    first = _invocation(invocation_id="inv_first")
    first_credentials = await runtime.begin(_harness(), first, _profile())
    first_scope = runtime.tokens.validate(first_credentials["token"])
    runtime.end(first, first_credentials)
    next_profile = _profile()
    next_profile["provider"] = {**next_profile["provider"], "model": "gpt-next"}

    second = _invocation(invocation_id="inv_second")
    second_credentials = await runtime.begin(_harness(), second, next_profile)

    assert second_credentials["token"] == first_credentials["token"]
    assert runtime.tokens.validate(second_credentials["token"]) == first_scope
    assert runtime.route("hsess_1")["model"] == "gpt-next"


async def test_same_session_credential_rotation_keeps_token_usable() -> None:
    runtime = RuntimeModelProxy(
        registry=object(),  # type: ignore[arg-type]
        resolver=_Resolver(),  # type: ignore[arg-type]
        listen="127.0.0.1:0",
    )
    runtime.base_url = "http://127.0.0.1:18080/v1"
    first_profile = _profile()
    first_profile["provider"] = {
        **first_profile["provider"],
        "credentialFingerprint": "sha256:first",
    }
    first = _invocation(invocation_id="inv_first")
    first_credentials = await runtime.begin(_harness(), first, first_profile)
    first_scope = runtime.tokens.validate(first_credentials["token"])
    runtime.end(first, first_credentials)
    next_profile = _profile()
    next_profile["provider"] = {
        **next_profile["provider"],
        "credentialFingerprint": "sha256:rotated",
    }

    second = _invocation(invocation_id="inv_second")
    second_credentials = await runtime.begin(_harness(), second, next_profile)

    assert second_credentials["token"] == first_credentials["token"]
    assert runtime.tokens.validate(second_credentials["token"]) == first_scope
    assert runtime.route("hsess_1")["credentialFingerprint"] == "sha256:rotated"


async def test_runtime_scope_recovers_only_matching_persisted_token_fingerprint() -> None:
    store = MemoryStore()
    store.put_session(
        SessionRecord(id="hsess_1", appName="chrn_codex_default", userId="")
    )
    runtime = RuntimeModelProxy(
        registry=object(),  # type: ignore[arg-type]
        resolver=_Resolver(),  # type: ignore[arg-type]
        listen="127.0.0.1:0",
        store=store,
    )
    runtime.base_url = "http://127.0.0.1:18080/v1"
    invocation = _invocation(invocation_id="inv_first")
    credentials = await runtime.begin(_harness(), invocation, _profile())
    original_token = credentials["token"]
    runtime.tokens.revoke_all()

    restored_scope = runtime.scope(invocation.sessionId, original_token)

    assert restored_scope is not None
    assert restored_scope.sessionId == invocation.sessionId
    assert runtime.scope(invocation.sessionId, "haas_mp_aHNlc3NfMQ.1.forged") is None


async def test_runtime_rebinds_previous_process_token_from_persisted_fingerprint() -> None:
    store = MemoryStore()
    store.put_session(
        SessionRecord(id="hsess_1", appName="chrn_codex_default", userId="")
    )
    first_runtime = RuntimeModelProxy(
        registry=object(),  # type: ignore[arg-type]
        resolver=_Resolver(),  # type: ignore[arg-type]
        listen="127.0.0.1:0",
        store=store,
    )
    first_runtime.base_url = "http://127.0.0.1:18080/v1"
    first_invocation = _invocation(invocation_id="inv_first")
    first_credentials = await first_runtime.begin(_harness(), first_invocation, _profile())
    old_token = first_credentials["token"]

    restarted_runtime = RuntimeModelProxy(
        registry=object(),  # type: ignore[arg-type]
        resolver=_Resolver(),  # type: ignore[arg-type]
        listen="127.0.0.1:0",
        store=store,
    )
    restarted_runtime.base_url = "http://127.0.0.1:18080/v1"
    second_invocation = _invocation(invocation_id="inv_second")
    second_credentials = await restarted_runtime.begin(_harness(), second_invocation, _profile())

    assert second_credentials["token"] != old_token
    assert restarted_runtime.scope("hsess_1", old_token) is not None
    assert restarted_runtime.scope("hsess_1", "haas_mp_aHNlc3NfMQ.1.forged") is None


async def test_concurrent_sessions_keep_distinct_capability_routes() -> None:
    runtime = RuntimeModelProxy(
        registry=object(),  # type: ignore[arg-type]
        resolver=_Resolver(),  # type: ignore[arg-type]
        listen="127.0.0.1:0",
    )
    runtime.base_url = "http://127.0.0.1:18080/v1"
    first_profile = _profile()
    second_profile = _profile()
    second_profile["provider"] = {**second_profile["provider"], "model": "gpt-other"}

    first_credentials = await runtime.begin(_harness(), _invocation(), first_profile)
    second_invocation = InvocationRecord(
        id="inv_2",
        sessionId="hsess_2",
        appName="chrn_codex_default",
        turnId="turn_2",
        startedAtMs=int(time.time() * 1000),
    )
    second_credentials = await runtime.begin(_harness(), second_invocation, second_profile)

    first_scope = runtime.tokens.validate(first_credentials["token"])
    second_scope = runtime.tokens.validate(second_credentials["token"])
    assert first_credentials["token"] != second_credentials["token"]
    assert first_scope.sessionId == "hsess_1"
    assert second_scope.sessionId == "hsess_2"
    assert runtime.route("hsess_1")["model"] == "gpt-test"
    assert runtime.route("hsess_2")["model"] == "gpt-other"


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


def test_invalid_listen_address_rejected_at_construction() -> None:
    """P2-13: malformed listen config fails fast, not deep in lifespan."""
    for bad in ("noport", "127.0.0.1:notaport", "0.0.0.0:18080", "127.0.0.1:70000"):
        with pytest.raises(ValueError):
            RuntimeModelProxy(
                registry=object(),  # type: ignore[arg-type]
                resolver=_Resolver(),  # type: ignore[arg-type]
                listen=bad,
            )
    # valid loopback listen still constructs
    runtime = RuntimeModelProxy(
        registry=object(),  # type: ignore[arg-type]
        resolver=_Resolver(),  # type: ignore[arg-type]
        listen="127.0.0.1:0",
    )
    assert runtime.listen == "127.0.0.1:0"
