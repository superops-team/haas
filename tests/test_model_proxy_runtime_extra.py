"""Coverage for RuntimeModelProxy capability lifecycle rejection/cleanup paths."""

from __future__ import annotations

import time
from typing import Any

import pytest

from haas.model_proxy.models import RuntimeTokenScope
from haas.model_proxy.runtime import RuntimeModelProxy
from haas.model_proxy.token import RuntimeTokenError
from haas.stores import HarnessRecord, InvocationRecord, MemoryStore, SessionRecord


class _Resolver:
    available = True

    async def resolve_for(self, route: object, scope: RuntimeTokenScope) -> str:
        return "fixture-key"

    def close(self) -> None:
        self.available = False


def _harness() -> HarnessRecord:
    return HarnessRecord(id="chrn_codex_default", name="codex", base="codex", defaultModel="gpt-test")


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


def _invocation(invocation_id: str = "inv_1", session_id: str = "hsess_1") -> InvocationRecord:
    return InvocationRecord(
        id=invocation_id,
        sessionId=session_id,
        appName="chrn_codex_default",
        turnId="turn_1",
        startedAtMs=int(time.time() * 1000),
    )


def _runtime(*, store: MemoryStore | None = None) -> RuntimeModelProxy:
    rt = RuntimeModelProxy(
        registry=object(),  # type: ignore[arg-type]
        resolver=_Resolver(),  # type: ignore[arg-type]
        listen="127.0.0.1:0",
        store=store,
    )
    rt.base_url = "http://127.0.0.1:18080/v1"
    return rt


async def test_scope_returns_none_for_unknown_session() -> None:
    rt = _runtime()
    assert rt.scope("never-seen", "haas_mp_whatever.1.x") is None


async def test_begin_rebinds_when_provider_scope_differs_for_same_session() -> None:
    rt = _runtime()
    first = _invocation("inv_first")
    await rt.begin(_harness(), first, _profile())

    rotated = _profile()
    rotated["provider"] = {**rotated["provider"], "credentialFingerprint": "sha256:rotated"}
    second = _invocation("inv_second")
    credentials = await rt.begin(_harness(), second, rotated)
    # Scope key differs -> previous capability revoked, fresh token issued.
    assert rt.tokens.validate(credentials["token"]).generation == 1


async def test_reject_refresh_when_invocation_session_mismatch() -> None:
    rt = _runtime()
    invocation = _invocation()
    credentials = await rt.begin(_harness(), invocation, _profile())
    other = _invocation("inv_other", session_id="hsess_other")
    with pytest.raises(RuntimeTokenError, match="invalid_token"):
        rt.refresh(other, credentials["token"])


async def test_reject_refresh_when_route_missing() -> None:
    rt = _runtime()
    invocation = _invocation()
    credentials = await rt.begin(_harness(), invocation, _profile())
    rt.routes.pop(invocation.sessionId, None)
    with pytest.raises(RuntimeTokenError, match="invalid_token"):
        rt.refresh(invocation, credentials["token"])


async def test_apply_refresh_rejects_identical_token() -> None:
    rt = _runtime()
    invocation = _invocation()
    credentials = await rt.begin(_harness(), invocation, _profile())
    with pytest.raises(RuntimeTokenError, match="invalid_token"):
        rt.apply_refresh(invocation, credentials["token"], credentials["token"])


async def test_apply_refresh_rejects_when_capability_absent() -> None:
    rt = _runtime()
    invocation = _invocation()
    credentials = await rt.begin(_harness(), invocation, _profile())
    replacement = rt.refresh(invocation, credentials["token"])
    rt._capabilities.pop(invocation.sessionId, None)
    with pytest.raises(RuntimeTokenError, match="invalid_token"):
        rt.apply_refresh(invocation, credentials["token"], replacement["token"])


async def test_revoke_session_clears_refresh_attempts() -> None:
    rt = _runtime()
    invocation = _invocation()
    credentials = await rt.begin(_harness(), invocation, _profile())
    rt.refresh(invocation, credentials["token"])
    assert rt._refresh_attempted
    rt.revoke_session(invocation.sessionId)
    assert rt._refresh_attempted == set()
    assert rt.route(invocation.sessionId) is None


async def test_persist_capability_summary_skips_unknown_session() -> None:
    store = MemoryStore()
    rt = _runtime(store=store)
    # No session recorded -> no-op.
    rt._persist_capability_summary_for_session(
        "hsess_missing", provider_scope_key="k", token="tok", generation=1
    )


async def test_stored_token_fingerprint_skips_unrelated_sessions() -> None:
    store = MemoryStore()
    store.put_session(SessionRecord(id="hsess_other", appName="chrn_codex_default", userId=""))
    rt = _runtime(store=store)
    assert rt._stored_token_fingerprints("hsess_target") == set()


async def test_clear_capability_summary_removes_model_proxy_state() -> None:
    store = MemoryStore()
    store.put_session(SessionRecord(id="hsess_1", appName="chrn_1", userId=""))
    rt = _runtime(store=store)
    invocation = _invocation()
    await rt.begin(_harness(), invocation, _profile())
    persisted = store.get_session(("chrn_1", "", "hsess_1"))
    assert persisted is not None
    rt._clear_capability_summary("hsess_1")
    cleared = store.get_session(("chrn_1", "", "hsess_1"))
    assert cleared is not None
    assert "modelProxy" not in cleared.state
