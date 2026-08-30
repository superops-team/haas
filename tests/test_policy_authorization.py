"""Authorization + post-delegation merge coverage for PolicyController.

Complements test_security_paths.py: that file pins the *rejection* side of
narrowing; this one pins the authorization decisions and the widening branches
that only run once a layer granted delegation.
"""
from __future__ import annotations

import pytest

from haas.policy.controller import PolicyController
from haas.policy.models import (
    ModelPolicy,
    NetworkPolicy,
    PolicyCompileInput,
    PolicyLayer,
    PolicyScope,
    ToolsPolicy,
    WorkspacePolicy,
)

_COUNTER = iter(range(1000))


def _layer(**kw) -> PolicyLayer:
    return PolicyLayer(name=f"L{next(_COUNTER)}", **kw)


def _compile(*layers: PolicyLayer):
    return PolicyController().compile(
        PolicyCompileInput(scope=PolicyScope(), layers=list(layers))
    )


# --- post-delegation widening ----------------------------------------------


def test_network_allow_and_default_action_widen_after_delegation() -> None:
    result = _compile(
        _layer(
            workspace=WorkspacePolicy(mode="read-only", writableRoots=["/w"]),
            network=NetworkPolicy(defaultAction="deny", allow=["a.com"]),
            delegation=True,
        ),
        _layer(network=NetworkPolicy(defaultAction="allow",
                                     allow=["a.com", "b.com"])),
    )
    assert result.network.defaultAction == "allow"
    assert "b.com" in result.network.allow


def test_model_allow_widens_after_delegation() -> None:
    result = _compile(
        _layer(
            workspace=WorkspacePolicy(mode="read-only", writableRoots=["/w"]),
            model=ModelPolicy(allowedModels=["m1"], fallbackModel=None),
            delegation=True,
        ),
        _layer(model=ModelPolicy(allowedModels=["m1", "m2"], fallbackModel="fb")),
    )
    assert result.model.allowedModels == ["m1", "m2"]
    assert result.model.fallbackModel == "fb"


def test_approval_relaxes_only_after_delegation() -> None:
    """`always` is the strictest mode; relaxing it requires delegation."""
    result = _compile(
        _layer(
            workspace=WorkspacePolicy(mode="read-only", writableRoots=["/w"]),
            tools=ToolsPolicy(disabled=[], approvalMode="always"),
            delegation=True,
        ),
        _layer(tools=ToolsPolicy(disabled=[], approvalMode="on-request")),
    )
    assert result.tools.approvalMode == "on-request"


# --- network authorization --------------------------------------------------


@pytest.fixture
def net_policy():
    return _compile(
        _layer(
            workspace=WorkspacePolicy(mode="read-only", writableRoots=["/w"]),
            network=NetworkPolicy(defaultAction="deny",
                                  allow=["https://api.example.com"]),
        )
    )


def test_authorize_network_allows_matching_entry(net_policy) -> None:
    decision = PolicyController().authorize_network(
        net_policy, "https://api.example.com/v1/responses"
    )
    assert decision.allowed is True


def test_authorize_network_rejects_non_http_scheme(net_policy) -> None:
    decision = PolicyController().authorize_network(net_policy, "file:///etc/passwd")
    assert decision.allowed is False
    assert decision.code == "haas_policy_denied"
    assert decision.safeReason == "url_scheme_not_allowed"


def test_authorize_network_rejects_missing_host(net_policy) -> None:
    decision = PolicyController().authorize_network(net_policy, "http:///nohost")
    assert decision.allowed is False
    assert decision.code == "haas_policy_denied"
    assert decision.safeReason == "url_host_missing"


def test_authorize_network_rejects_unlisted_host(net_policy) -> None:
    assert (
        PolicyController().authorize_network(net_policy, "https://evil.com/x").allowed
        is False
    )


def test_authorize_network_rejects_scheme_mismatch(net_policy) -> None:
    # Host matches the allowlist entry but the scheme does not.
    decision = PolicyController().authorize_network(
        net_policy, "http://api.example.com/x"
    )
    assert decision.allowed is False


def test_authorize_network_port_must_match_when_pinned() -> None:
    controller = PolicyController()
    ported = _compile(
        _layer(
            workspace=WorkspacePolicy(mode="read-only", writableRoots=["/w"]),
            network=NetworkPolicy(defaultAction="deny",
                                  allow=["https://api.example.com:443"]),
        )
    )
    assert controller.authorize_network(
        ported, "https://api.example.com:8443/x"
    ).allowed is False
    assert controller.authorize_network(
        ported, "https://api.example.com:443/x"
    ).allowed is True


def test_authorize_network_allow_all_when_default_action_allow() -> None:
    """`defaultAction=allow` widens the deny baseline, so it needs delegation."""
    policy = _compile(
        _layer(
            workspace=WorkspacePolicy(mode="read-only", writableRoots=["/w"]),
            network=NetworkPolicy(defaultAction="deny", allow=[]),
            delegation=True,
        ),
        _layer(network=NetworkPolicy(defaultAction="allow", allow=[])),
    )
    assert PolicyController().authorize_network(policy, "https://anything.dev/x").allowed


# --- workspace path authorization -------------------------------------------


@pytest.fixture
def ws_policy():
    return _compile(
        _layer(workspace=WorkspacePolicy(mode="workspace-write",
                                         writableRoots=["/w/project"]))
    )


def test_authorize_write_inside_writable_root(ws_policy) -> None:
    assert PolicyController().authorize_workspace_path(
        ws_policy, "/w/project/src/main.py", "write"
    ).allowed is True


def test_authorize_write_outside_writable_root_is_denied(ws_policy) -> None:
    assert PolicyController().authorize_workspace_path(
        ws_policy, "/etc/passwd", "write"
    ).allowed is False


def test_authorize_rejects_unknown_access_mode(ws_policy) -> None:
    decision = PolicyController().authorize_workspace_path(
        ws_policy, "/w/project/x", "execute"
    )
    assert decision.allowed is False
    assert decision.code == "haas_policy_denied"
    assert decision.safeReason == "invalid_access"


def test_authorize_path_traversal_is_denied(ws_policy) -> None:
    """`..` must be canonicalized before the containment check."""
    assert PolicyController().authorize_workspace_path(
        ws_policy, "/w/project/../../etc/shadow", "write"
    ).allowed is False


def test_authorize_read_includes_workspace_root(ws_policy) -> None:
    decision = PolicyController().authorize_workspace_path(
        ws_policy, ws_policy.workspace.root + "/readme.md", "read"
    )
    assert decision.allowed is True
