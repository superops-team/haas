"""Policy Controller tests: precedence merge, widening rejection, network + path authz."""
from __future__ import annotations

import pytest

from haas.policy import (
    NetworkPolicy,
    PolicyCompileInput,
    PolicyController,
    PolicyLayer,
    PolicyScope,
    PolicyWideningRejected,
    ToolsPolicy,
    WorkspacePolicy,
)


def _compile(*layers: PolicyLayer) -> object:
    return PolicyController().compile(
        PolicyCompileInput(scope=PolicyScope(tenantId="t1", workspaceId="w1"), layers=list(layers))
    )


# --- precedence merge -------------------------------------------------------


def test_compile_narrows_workspace_mode() -> None:
    policy = _compile(
        PolicyLayer("tenant", workspace=WorkspacePolicy(mode="workspace-write", root="/workspace")),
        PolicyLayer("workspace", workspace=WorkspacePolicy(mode="read-only", root="/workspace")),
    )
    assert policy.workspace.mode == "read-only"


def test_compile_defaults_writable_root_to_workspace_root() -> None:
    policy = _compile(
        PolicyLayer("tenant", workspace=WorkspacePolicy(mode="workspace-write", root="/workspace")),
    )
    assert policy.workspace.writableRoots == ["/workspace"]


def test_compile_merges_network_allowlist_narrowing() -> None:
    policy = _compile(
        PolicyLayer("tenant", network=NetworkPolicy(allow=["https://a.com", "https://b.com"])),
        PolicyLayer("workspace", network=NetworkPolicy(allow=["https://a.com"])),
    )
    assert policy.network.allow == ["https://a.com"]


def test_compile_adds_disabled_tools() -> None:
    policy = _compile(
        PolicyLayer("tenant", tools=ToolsPolicy(disabled=["web_search"])),
        PolicyLayer("harness", tools=ToolsPolicy(disabled=["browser"])),
    )
    assert set(policy.tools.disabled) == {"web_search", "browser"}


# --- widening rejection -----------------------------------------------------


def test_compile_rejects_widening_workspace_mode() -> None:
    with pytest.raises(PolicyWideningRejected):
        _compile(
            PolicyLayer("tenant", workspace=WorkspacePolicy(mode="read-only", root="/workspace")),
            PolicyLayer(
                "workspace", workspace=WorkspacePolicy(mode="workspace-write", root="/workspace")
            ),
        )


def test_compile_rejects_widening_writable_root() -> None:
    with pytest.raises(PolicyWideningRejected):
        _compile(
            PolicyLayer(
                "tenant",
                workspace=WorkspacePolicy(
                    mode="workspace-write", root="/workspace", writableRoots=["/workspace"]
                ),
            ),
            PolicyLayer(
                "workspace",
                workspace=WorkspacePolicy(
                    mode="workspace-write",
                    root="/workspace",
                    writableRoots=["/workspace", "/data"],
                ),
            ),
        )


def test_compile_rejects_widening_network_allow() -> None:
    with pytest.raises(PolicyWideningRejected):
        _compile(
            PolicyLayer("tenant", network=NetworkPolicy(allow=["https://a.com"])),
            PolicyLayer("workspace", network=NetworkPolicy(allow=["https://a.com", "https://evil.com"])),
        )


def test_compile_allows_widening_with_delegation() -> None:
    policy = _compile(
        PolicyLayer(
            "tenant",
            workspace=WorkspacePolicy(mode="read-only", root="/workspace"),
            delegation=True,
        ),
        PolicyLayer(
            "workspace", workspace=WorkspacePolicy(mode="workspace-write", root="/workspace")
        ),
    )
    assert policy.workspace.mode == "workspace-write"


# --- network authorization --------------------------------------------------


def test_authorize_network_allows_listed_url() -> None:
    policy = _compile(PolicyLayer("tenant", network=NetworkPolicy(allow=["https://api.openai.com"])))
    decision = PolicyController().authorize_network(policy, "https://api.openai.com/v1/chat")
    assert decision.allowed is True


def test_authorize_network_denies_unlisted_url() -> None:
    policy = _compile(PolicyLayer("tenant", network=NetworkPolicy(allow=["https://api.openai.com"])))
    decision = PolicyController().authorize_network(policy, "https://evil.com")
    assert decision.allowed is False
    assert decision.code == "haas_policy_denied"


def test_authorize_network_rejects_non_http_scheme() -> None:
    policy = _compile(PolicyLayer("tenant", network=NetworkPolicy(allow=["file:///etc"])))
    decision = PolicyController().authorize_network(policy, "file:///etc/passwd")
    assert decision.allowed is False


def test_authorize_network_blocks_private_ip() -> None:
    policy = _compile(PolicyLayer("tenant", network=NetworkPolicy(allow=["https://10.0.0.5"])))
    decision = PolicyController().authorize_network(policy, "http://169.254.169.254/latest/meta-data")
    assert decision.allowed is False


def test_authorize_network_blocks_localhost_without_allowlist() -> None:
    policy = _compile(PolicyLayer("tenant", network=NetworkPolicy(allow=["https://api.openai.com"])))
    decision = PolicyController().authorize_network(policy, "http://127.0.0.1:9000/")
    assert decision.allowed is False


def test_authorize_network_allows_loopback_when_listed() -> None:
    policy = _compile(PolicyLayer("tenant", network=NetworkPolicy(allow=["http://127.0.0.1:18080"])))
    decision = PolicyController().authorize_network(policy, "http://127.0.0.1:18080/v1")
    assert decision.allowed is True


# --- workspace path authorization ------------------------------------------


def test_authorize_workspace_write_within_root() -> None:
    policy = _compile(
        PolicyLayer("tenant", workspace=WorkspacePolicy(mode="workspace-write", root="/workspace"))
    )
    decision = PolicyController().authorize_workspace_path(
        policy, "/workspace/out.txt", "write"
    )
    assert decision.allowed


def test_authorize_workspace_write_outside_root() -> None:
    policy = _compile(
        PolicyLayer("tenant", workspace=WorkspacePolicy(mode="workspace-write", root="/workspace"))
    )
    decision = PolicyController().authorize_workspace_path(policy, "/etc/passwd", "write")
    assert decision.allowed is False


def test_authorize_workspace_path_blocks_traversal() -> None:
    policy = _compile(
        PolicyLayer("tenant", workspace=WorkspacePolicy(mode="workspace-write", root="/workspace"))
    )
    decision = PolicyController().authorize_workspace_path(
        policy, "/workspace/../../etc/passwd", "write"
    )
    assert decision.allowed is False
