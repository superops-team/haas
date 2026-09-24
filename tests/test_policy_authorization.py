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
    return PolicyController().compile(PolicyCompileInput(scope=PolicyScope(), layers=list(layers)))


# --- post-delegation widening ----------------------------------------------


def test_network_allow_and_default_action_widen_after_delegation() -> None:
    result = _compile(
        _layer(
            workspace=WorkspacePolicy(mode="read-only", writableRoots=["/w"]),
            network=NetworkPolicy(defaultAction="deny", allow=["a.com"]),
            delegation=True,
        ),
        _layer(network=NetworkPolicy(defaultAction="allow", allow=["a.com", "b.com"])),
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
            network=NetworkPolicy(defaultAction="deny", allow=["https://api.example.com"]),
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
    assert PolicyController().authorize_network(net_policy, "https://evil.com/x").allowed is False


def test_authorize_network_rejects_scheme_mismatch(net_policy) -> None:
    # Host matches the allowlist entry but the scheme does not.
    decision = PolicyController().authorize_network(net_policy, "http://api.example.com/x")
    assert decision.allowed is False


def test_authorize_network_port_must_match_when_pinned() -> None:
    controller = PolicyController()
    ported = _compile(
        _layer(
            workspace=WorkspacePolicy(mode="read-only", writableRoots=["/w"]),
            network=NetworkPolicy(defaultAction="deny", allow=["https://api.example.com:443"]),
        )
    )
    assert controller.authorize_network(ported, "https://api.example.com:8443/x").allowed is False
    assert controller.authorize_network(ported, "https://api.example.com:443/x").allowed is True


def test_authorize_network_pinned_port_rejects_default_port_omission() -> None:
    controller = PolicyController()
    ported = _compile(
        _layer(
            workspace=WorkspacePolicy(mode="read-only", writableRoots=["/w"]),
            network=NetworkPolicy(defaultAction="deny", allow=["http://api.example.com:8080"]),
        )
    )
    assert controller.authorize_network(ported, "http://api.example.com/x").allowed is False
    assert controller.authorize_network(ported, "http://api.example.com:80/x").allowed is False
    assert controller.authorize_network(ported, "http://api.example.com:8080/x").allowed is True


def test_authorize_network_http_entry_without_port_rejects_non_default_port() -> None:
    controller = PolicyController()
    default_port_only = _compile(
        _layer(
            workspace=WorkspacePolicy(mode="read-only", writableRoots=["/w"]),
            network=NetworkPolicy(defaultAction="deny", allow=["http://api.example.com"]),
        )
    )
    assert (
        controller.authorize_network(default_port_only, "http://api.example.com/x").allowed is True
    )
    assert (
        controller.authorize_network(default_port_only, "http://api.example.com:80/x").allowed
        is True
    )
    assert (
        controller.authorize_network(default_port_only, "http://api.example.com:8080/x").allowed
        is False
    )


def test_authorize_network_https_entry_without_port_rejects_non_default_port() -> None:
    controller = PolicyController()
    default_port_only = _compile(
        _layer(
            workspace=WorkspacePolicy(mode="read-only", writableRoots=["/w"]),
            network=NetworkPolicy(defaultAction="deny", allow=["https://api.example.com"]),
        )
    )
    assert (
        controller.authorize_network(default_port_only, "https://api.example.com/x").allowed is True
    )
    assert (
        controller.authorize_network(default_port_only, "https://api.example.com:443/x").allowed
        is True
    )
    assert (
        controller.authorize_network(default_port_only, "https://api.example.com:8443/x").allowed
        is False
    )


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
        _layer(workspace=WorkspacePolicy(mode="workspace-write", writableRoots=["/w/project"]))
    )


def test_authorize_write_inside_writable_root(ws_policy) -> None:
    assert (
        PolicyController()
        .authorize_workspace_path(ws_policy, "/w/project/src/main.py", "write")
        .allowed
        is True
    )


def test_authorize_write_outside_writable_root_is_denied(ws_policy) -> None:
    assert (
        PolicyController().authorize_workspace_path(ws_policy, "/etc/passwd", "write").allowed
        is False
    )


def test_authorize_rejects_unknown_access_mode(ws_policy) -> None:
    decision = PolicyController().authorize_workspace_path(ws_policy, "/w/project/x", "execute")
    assert decision.allowed is False
    assert decision.code == "haas_policy_denied"
    assert decision.safeReason == "invalid_access"


def test_authorize_path_traversal_is_denied(ws_policy) -> None:
    """`..` must be canonicalized before the containment check."""
    assert (
        PolicyController()
        .authorize_workspace_path(ws_policy, "/w/project/../../etc/shadow", "write")
        .allowed
        is False
    )


def test_authorize_read_includes_workspace_root(ws_policy) -> None:
    decision = PolicyController().authorize_workspace_path(
        ws_policy, ws_policy.workspace.root + "/readme.md", "read"
    )
    assert decision.allowed is True


# === appended coverage ===


from urllib.parse import urlparse

import pytest

from haas.policy import (
    EffectivePolicy,
    ModelPolicy,
    NetworkPolicy,
    PolicyCompileInput,
    PolicyController,
    PolicyLayer,
    PolicyScope,
    PolicyWideningRejected,
    PolicyInvalid,
    ToolsPolicy,
    WorkspacePolicy,
)
from haas.policy.controller import (
    _default_port_for_scheme,
    _effective_port,
    _is_same_or_within,
)


def _do_compile(layers: list[PolicyLayer]) -> EffectivePolicy:
    return PolicyController().compile(
        PolicyCompileInput(scope=PolicyScope(tenantId="t"), layers=layers)
    )


# --- merge: workspace --------------------------------------------------------


def test_compile_rejects_unknown_workspace_mode() -> None:
    with pytest.raises(PolicyInvalid, match="unknown workspace mode"):
        _do_compile([PolicyLayer("tenant", workspace=WorkspacePolicy(mode="bogus"))])


def test_compile_rejects_workspace_mode_widening_without_delegation() -> None:
    layers = [
        PolicyLayer("tenant", workspace=WorkspacePolicy(mode="read-only")),
        PolicyLayer("delegate", workspace=WorkspacePolicy(mode="danger-full-access")),
    ]
    with pytest.raises(PolicyWideningRejected, match="workspace mode widened"):
        _do_compile(layers)


def test_compile_allows_workspace_widening_after_delegation() -> None:
    layers = [
        PolicyLayer("tenant", workspace=WorkspacePolicy(mode="read-only")),
        PolicyLayer("delegate", delegation=True),
        PolicyLayer(
            "app",
            workspace=WorkspacePolicy(
                mode="workspace-write",
                root="/workspace/child",
                writableRoots=["/workspace/child"],
            ),
        ),
    ]
    policy = _do_compile(layers)
    assert policy.workspace.mode == "workspace-write"
    assert policy.workspace.root.endswith("/workspace/child")


def test_compile_narrows_writable_roots_when_no_widening() -> None:
    layers = [
        PolicyLayer(
            "tenant", workspace=WorkspacePolicy(writableRoots=["/workspace", "/tmp"])
        ),
        PolicyLayer("app", workspace=WorkspacePolicy(writableRoots=["/workspace"])),
    ]
    policy = _do_compile(layers)
    assert policy.workspace.writableRoots == ["/workspace"]


# --- merge: network ----------------------------------------------------------


def test_compile_rejects_unknown_network_default_action() -> None:
    with pytest.raises(PolicyInvalid, match="unknown network defaultAction"):
        _do_compile([PolicyLayer("tenant", network=NetworkPolicy(defaultAction="bogus"))])


def test_compile_rejects_network_deny_to_allow_widening() -> None:
    layers = [
        PolicyLayer("tenant", network=NetworkPolicy(defaultAction="deny")),
        PolicyLayer("app", network=NetworkPolicy(defaultAction="allow")),
    ]
    with pytest.raises(PolicyWideningRejected, match="network defaultAction widened"):
        _do_compile(layers)


def test_compile_rejects_new_network_allow_entry_without_delegation() -> None:
    layers = [
        PolicyLayer("tenant", network=NetworkPolicy(defaultAction="deny", allow=["https://a"])),
        PolicyLayer("app", network=NetworkPolicy(defaultAction="deny", allow=["https://b"])),
    ]
    with pytest.raises(PolicyWideningRejected, match="network allow widened"):
        _do_compile(layers)


def test_compile_narrows_network_allow_list() -> None:
    layers = [
        PolicyLayer("tenant", network=NetworkPolicy(defaultAction="deny", allow=["https://a", "https://b"])),
        PolicyLayer("app", network=NetworkPolicy(defaultAction="deny", allow=["https://a"])),
    ]
    policy = _do_compile(layers)
    assert policy.network.allow == ["https://a"]


# --- merge: tools / model ----------------------------------------------------


def test_compile_rejects_unknown_approval_mode() -> None:
    with pytest.raises(PolicyInvalid, match="unknown approvalMode"):
        _do_compile([PolicyLayer("tenant", tools=ToolsPolicy(approvalMode="yolo"))])


def test_compile_appends_new_disabled_tool() -> None:
    layers = [
        PolicyLayer("tenant", tools=ToolsPolicy(disabled=["bash"], approvalMode="on-request")),
        PolicyLayer("app", tools=ToolsPolicy(disabled=["net"], approvalMode="on-request")),
    ]
    policy = _do_compile(layers)
    assert set(policy.tools.disabled) == {"bash", "net"}


def test_compile_rejects_approval_mode_widening() -> None:
    layers = [
        PolicyLayer("tenant", tools=ToolsPolicy(approvalMode="always")),
        PolicyLayer("app", tools=ToolsPolicy(approvalMode="on-request")),
    ]
    with pytest.raises(PolicyWideningRejected, match="approvalMode widened"):
        _do_compile(layers)


def test_compile_rejects_model_allow_widening() -> None:
    layers = [
        PolicyLayer("tenant", model=ModelPolicy(allowedModels=["gpt-a"])),
        PolicyLayer("app", model=ModelPolicy(allowedModels=["gpt-a", "gpt-b"])),
    ]
    with pytest.raises(PolicyWideningRejected, match="model allow widened"):
        _do_compile(layers)


def test_compile_narrows_model_allow_list() -> None:
    layers = [
        PolicyLayer("tenant", model=ModelPolicy(allowedModels=["gpt-a", "gpt-b"])),
        PolicyLayer("app", model=ModelPolicy(allowedModels=["gpt-a"])),
    ]
    policy = _do_compile(layers)
    assert policy.model.allowedModels == ["gpt-a"]


# --- authorization helpers ----------------------------------------------------


def test_authorize_network_rejects_invalid_port() -> None:
    controller = PolicyController()
    policy = _do_compile([PolicyLayer("tenant", network=NetworkPolicy(defaultAction="allow"))])
    decision = controller.authorize_network(policy, "http://example.com:999999/")
    assert decision.allowed is False
    assert decision.safeReason == "url_port_invalid"


def test_allowlist_matches_bare_host_entry() -> None:
    controller = PolicyController()
    policy = _do_compile(
        [PolicyLayer("tenant", network=NetworkPolicy(defaultAction="deny", allow=["example.com"]))]
    )
    decision = controller.authorize_network(policy, "http://example.com/")
    assert decision.allowed is True


def test_allowlist_matches_rejects_when_entry_port_unparseable() -> None:
    controller = PolicyController()
    parsed = urlparse("http://example.com/")
    # An entry whose scheme carries no default port -> ports differ -> False.
    assert controller._allowlist_matches("ftp://example.com", parsed) is False


def test_effective_port_returns_none_on_invalid_literal() -> None:
    parsed = urlparse("http://example.com:999999/")
    with pytest.raises(ValueError):
        parsed.port  # sanity: stdlib raises
    assert _effective_port(parsed) is None


def test_default_port_for_unknown_scheme_is_none() -> None:
    assert _default_port_for_scheme("gopher") is None


# --- deeper merge / allowlist branches ---------------------------------------


def test_compile_appends_writable_root_after_delegation() -> None:
    layers = [
        PolicyLayer("tenant", workspace=WorkspacePolicy(root="/workspace", writableRoots=["/workspace"])),
        PolicyLayer("delegate", delegation=True),
        PolicyLayer("app", workspace=WorkspacePolicy(writableRoots=["/workspace", "/extra"])),
    ]
    policy = _do_compile(layers)
    assert "/extra" in policy.workspace.writableRoots


def test_compile_widens_workspace_root_after_delegation() -> None:
    layers = [
        PolicyLayer("tenant", workspace=WorkspacePolicy(root="/workspace")),
        PolicyLayer("delegate", delegation=True),
        PolicyLayer("app", workspace=WorkspacePolicy(root="/other-root")),
    ]
    policy = _do_compile(layers)
    assert policy.workspace.root == "/other-root"


def test_merge_tools_rejects_unknown_current_approval_mode() -> None:
    controller = PolicyController()
    current = ToolsPolicy(disabled=[], approvalMode="bogus-current")
    nxt = ToolsPolicy(disabled=[], approvalMode="on-request")
    with pytest.raises(PolicyInvalid, match="unknown approvalMode"):
        controller._merge_tools(current, nxt, allow_widening=True)


def test_allowlist_matches_bare_host_with_explicit_port() -> None:
    controller = PolicyController()
    parsed = urlparse("http://example.com:8080/v1")
    assert controller._allowlist_matches("example.com:8080", parsed) is True
    assert controller._allowlist_matches("example.com:9090", parsed) is False


def test_is_same_or_within_returns_false_for_outside_path() -> None:
    assert _is_same_or_within("/etc/passwd", "/workspace") is False
    assert _is_same_or_within("/workspace/a", "/workspace") is True