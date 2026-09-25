"""Coverage for PolicyController merge (narrow-only) and authorization helpers."""

from __future__ import annotations

from urllib.parse import urlparse

import pytest

from haas.policy import (
    EffectivePolicy,
    ModelPolicy,
    NetworkPolicy,
    PolicyCompileInput,
    PolicyController,
    PolicyInvalid,
    PolicyLayer,
    PolicyScope,
    PolicyWideningRejected,
    ToolsPolicy,
    WorkspacePolicy,
)
from haas.policy.controller import (
    _default_port_for_scheme,
    _effective_port,
    _is_same_or_within,
)


def _compile(layers: list[PolicyLayer]) -> EffectivePolicy:
    return PolicyController().compile(
        PolicyCompileInput(scope=PolicyScope(tenantId="t"), layers=layers)
    )


# --- merge: workspace --------------------------------------------------------


def test_compile_rejects_unknown_workspace_mode() -> None:
    with pytest.raises(PolicyInvalid, match="unknown workspace mode"):
        _compile([PolicyLayer("tenant", workspace=WorkspacePolicy(mode="bogus"))])


def test_compile_rejects_workspace_mode_widening_without_delegation() -> None:
    layers = [
        PolicyLayer("tenant", workspace=WorkspacePolicy(mode="read-only")),
        PolicyLayer("delegate", workspace=WorkspacePolicy(mode="danger-full-access")),
    ]
    with pytest.raises(PolicyWideningRejected, match="workspace mode widened"):
        _compile(layers)


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
    policy = _compile(layers)
    assert policy.workspace.mode == "workspace-write"
    assert policy.workspace.root.endswith("/workspace/child")


def test_compile_narrows_writable_roots_when_no_widening() -> None:
    layers = [
        PolicyLayer(
            "tenant", workspace=WorkspacePolicy(writableRoots=["/workspace", "/tmp"])
        ),
        PolicyLayer("app", workspace=WorkspacePolicy(writableRoots=["/workspace"])),
    ]
    policy = _compile(layers)
    assert policy.workspace.writableRoots == ["/workspace"]


# --- merge: network ----------------------------------------------------------


def test_compile_rejects_unknown_network_default_action() -> None:
    with pytest.raises(PolicyInvalid, match="unknown network defaultAction"):
        _compile([PolicyLayer("tenant", network=NetworkPolicy(defaultAction="bogus"))])


def test_compile_rejects_network_deny_to_allow_widening() -> None:
    layers = [
        PolicyLayer("tenant", network=NetworkPolicy(defaultAction="deny")),
        PolicyLayer("app", network=NetworkPolicy(defaultAction="allow")),
    ]
    with pytest.raises(PolicyWideningRejected, match="network defaultAction widened"):
        _compile(layers)


def test_compile_rejects_new_network_allow_entry_without_delegation() -> None:
    layers = [
        PolicyLayer("tenant", network=NetworkPolicy(defaultAction="deny", allow=["https://a"])),
        PolicyLayer("app", network=NetworkPolicy(defaultAction="deny", allow=["https://b"])),
    ]
    with pytest.raises(PolicyWideningRejected, match="network allow widened"):
        _compile(layers)


def test_compile_narrows_network_allow_list() -> None:
    layers = [
        PolicyLayer("tenant", network=NetworkPolicy(defaultAction="deny", allow=["https://a", "https://b"])),
        PolicyLayer("app", network=NetworkPolicy(defaultAction="deny", allow=["https://a"])),
    ]
    policy = _compile(layers)
    assert policy.network.allow == ["https://a"]


# --- merge: tools / model ----------------------------------------------------


def test_compile_rejects_unknown_approval_mode() -> None:
    with pytest.raises(PolicyInvalid, match="unknown approvalMode"):
        _compile([PolicyLayer("tenant", tools=ToolsPolicy(approvalMode="yolo"))])


def test_compile_appends_new_disabled_tool() -> None:
    layers = [
        PolicyLayer("tenant", tools=ToolsPolicy(disabled=["bash"], approvalMode="on-request")),
        PolicyLayer("app", tools=ToolsPolicy(disabled=["net"], approvalMode="on-request")),
    ]
    policy = _compile(layers)
    assert set(policy.tools.disabled) == {"bash", "net"}


def test_compile_rejects_approval_mode_widening() -> None:
    layers = [
        PolicyLayer("tenant", tools=ToolsPolicy(approvalMode="always")),
        PolicyLayer("app", tools=ToolsPolicy(approvalMode="on-request")),
    ]
    with pytest.raises(PolicyWideningRejected, match="approvalMode widened"):
        _compile(layers)


def test_compile_rejects_model_allow_widening() -> None:
    layers = [
        PolicyLayer("tenant", model=ModelPolicy(allowedModels=["gpt-a"])),
        PolicyLayer("app", model=ModelPolicy(allowedModels=["gpt-a", "gpt-b"])),
    ]
    with pytest.raises(PolicyWideningRejected, match="model allow widened"):
        _compile(layers)


def test_compile_narrows_model_allow_list() -> None:
    layers = [
        PolicyLayer("tenant", model=ModelPolicy(allowedModels=["gpt-a", "gpt-b"])),
        PolicyLayer("app", model=ModelPolicy(allowedModels=["gpt-a"])),
    ]
    policy = _compile(layers)
    assert policy.model.allowedModels == ["gpt-a"]


# --- authorization helpers ----------------------------------------------------


def test_authorize_network_rejects_invalid_port() -> None:
    controller = PolicyController()
    policy = _compile([PolicyLayer("tenant", network=NetworkPolicy(defaultAction="allow"))])
    decision = controller.authorize_network(policy, "http://example.com:999999/")
    assert decision.allowed is False
    assert decision.safeReason == "url_port_invalid"


def test_allowlist_matches_bare_host_entry() -> None:
    controller = PolicyController()
    policy = _compile(
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
        _ = parsed.port  # sanity: stdlib raises
    assert _effective_port(parsed) is None


def test_default_port_for_unknown_scheme_is_none() -> None:
    assert _default_port_for_scheme("gopher") is None


# --- deeper merge / allowlist branches ---------------------------------------


def test_compile_appends_writable_root_after_delegation() -> None:
    layers = [
        PolicyLayer(
            "tenant", workspace=WorkspacePolicy(root="/workspace", writableRoots=["/workspace"])
        ),
        PolicyLayer("delegate", delegation=True),
        PolicyLayer("app", workspace=WorkspacePolicy(writableRoots=["/workspace", "/extra"])),
    ]
    policy = _compile(layers)
    assert "/extra" in policy.workspace.writableRoots


def test_compile_widens_workspace_root_after_delegation() -> None:
    layers = [
        PolicyLayer("tenant", workspace=WorkspacePolicy(root="/workspace")),
        PolicyLayer("delegate", delegation=True),
        PolicyLayer("app", workspace=WorkspacePolicy(root="/other-root")),
    ]
    policy = _compile(layers)
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
