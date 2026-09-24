"""Coverage for RuntimeTokenManager edge paths: expiry, refresh, adopt, revoke."""

from __future__ import annotations

import time

import pytest

from haas.model_proxy.models import RuntimeTokenScope
from haas.model_proxy.token import RuntimeTokenError, RuntimeTokenManager


def test_scope_rejects_expired_token_when_not_allow_expired() -> None:
    mgr = RuntimeTokenManager(ttl_seconds=-1)
    token = mgr.issue(RuntimeTokenScope(sessionId="s_1"))
    with pytest.raises(RuntimeTokenError, match="token_expired"):
        mgr.scope(token, allow_expired=False)


def test_refresh_issues_a_replacement_token() -> None:
    mgr = RuntimeTokenManager()
    token = mgr.issue(RuntimeTokenScope(sessionId="s_1", allowedModels=["gpt-a"]))
    replacement = mgr.refresh(token)
    assert replacement != token
    assert mgr.validate(replacement).sessionId == "s_1"
    # The original remains valid until the caller rotates it out.
    assert mgr.validate(token).sessionId == "s_1"


def test_adopt_rejects_non_prefixed_token() -> None:
    mgr = RuntimeTokenManager()
    with pytest.raises(RuntimeTokenError, match="invalid_token"):
        mgr.adopt("not-a-token", RuntimeTokenScope(sessionId="s_1"))


def test_adopt_rejects_session_id_mismatch() -> None:
    mgr = RuntimeTokenManager()
    token = mgr.issue(RuntimeTokenScope(sessionId="s_1"))
    with pytest.raises(RuntimeTokenError, match="invalid_token"):
        mgr.adopt(token, RuntimeTokenScope(sessionId="different-session"))


def test_adopt_sets_issued_at_when_missing() -> None:
    mgr = RuntimeTokenManager()
    token = mgr.issue(RuntimeTokenScope(sessionId="s_1"))
    scope = RuntimeTokenScope(sessionId="s_1")  # issuedAtMs defaults to 0
    assert scope.issuedAtMs == 0
    mgr.adopt(token, scope)
    assert scope.issuedAtMs > 0
    assert mgr.validate(token).sessionId == "s_1"


def test_revoke_invocation_only_drops_matching_invocation_token() -> None:
    mgr = RuntimeTokenManager()
    kept = mgr.issue(RuntimeTokenScope(sessionId="s_1", invocationId="inv_a"))
    dropped = mgr.issue(RuntimeTokenScope(sessionId="s_1", invocationId="inv_b"))
    mgr.revoke_invocation("s_1", "inv_b")
    assert mgr.validate(kept).invocationId == "inv_a"
    with pytest.raises(RuntimeTokenError):
        mgr.validate(dropped)


def test_revoke_session_drops_all_session_tokens() -> None:
    mgr = RuntimeTokenManager()
    t1 = mgr.issue(RuntimeTokenScope(sessionId="s_1"))
    t2 = mgr.issue(RuntimeTokenScope(sessionId="s_2"))
    mgr.revoke_session("s_1")
    with pytest.raises(RuntimeTokenError):
        mgr.validate(t1)
    assert mgr.validate(t2).sessionId == "s_2"


@pytest.mark.parametrize(
    "raw",
    [
        "haas_mp_.1.x",           # empty encoded session
        "haas_mp_@@@.1.x",        # not base64
        "haas_mp_cG9vci1zZXNzaW9u.1.x",  # valid base64 session below
    ],
)
def test_token_session_id_parses_or_rejects(raw: str) -> None:
    mgr = RuntimeTokenManager()
    # Smoke: any shape must not raise; valid base64 resolves, garbage returns None.
    result = mgr.token_session_id(raw)
    if raw.endswith("cG9vci1zZXNzaW9u.1.x"):
        assert result == "poor-session"
    else:
        assert result is None


def test_token_session_id_rejects_whitespace_in_session() -> None:
    mgr = RuntimeTokenManager()
    # base64 of "has space" (contains a space once decoded).
    import base64

    encoded = base64.urlsafe_b64encode("has space".encode()).decode().rstrip("=")
    assert mgr.token_session_id(f"haas_mp_{encoded}.1.x") is None
