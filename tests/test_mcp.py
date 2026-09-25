"""MCP / Tool / Skill runtime tests."""

from __future__ import annotations

import pytest

from haas.mcp import (
    McpServerConfig,
    SkillBundle,
    SkillFile,
    SkillMaterializationError,
    enforce_disabled_tools,
    materialize_skills,
    validate_mcp_server,
)
from haas.policy.models import (
    EffectivePolicy,
    ModelPolicy,
    NetworkPolicy,
    PolicyScope,
    ToolsPolicy,
    WorkspacePolicy,
)


def test_validate_mcp_server_valid() -> None:
    assert (
        validate_mcp_server(McpServerConfig(name="repo", url="https://mcp.example.com/mcp")) == []
    )


def test_validate_mcp_server_rejects_bad_scheme() -> None:
    errors = validate_mcp_server(McpServerConfig(name="repo", url="file:///etc"))
    assert "url_scheme_invalid" in errors


def test_validate_mcp_server_requires_secret_ref() -> None:
    errors = validate_mcp_server(
        McpServerConfig(name="repo", url="https://mcp.example.com", authType="secret_ref")
    )
    assert "auth_ref_required" in errors


def test_validate_mcp_server_rejects_metadata_endpoint_by_default() -> None:
    errors = validate_mcp_server(
        McpServerConfig(name="repo", url="http://169.254.169.254/latest/meta-data")
    )
    assert "url_private_network_blocked" in errors


def test_validate_mcp_server_rejects_loopback_by_default() -> None:
    errors = validate_mcp_server(McpServerConfig(name="repo", url="http://localhost:18080/mcp"))
    assert "url_private_network_blocked" in errors


def test_validate_mcp_server_allows_private_host_when_policy_allowlists_it() -> None:
    policy = EffectivePolicy(
        policyId="pol_test",
        version=1,
        scope=PolicyScope(),
        workspace=WorkspacePolicy(),
        network=NetworkPolicy(defaultAction="deny", allow=["http://10.0.0.1:8080"]),
        tools=ToolsPolicy(),
        model=ModelPolicy(),
    )
    assert (
        validate_mcp_server(
            McpServerConfig(name="repo", url="http://10.0.0.1:8080/mcp"),
            policy=policy,
        )
        == []
    )


def test_materialize_skills_valid() -> None:
    bundle = SkillBundle(
        id="s1",
        name="repo-rules",
        files=[
            SkillFile(path="SKILL.md", content=b"x"),
            SkillFile(path="references/p.md", content=b"y"),
        ],
    )
    result = materialize_skills([bundle])
    assert result["s1"] == ["SKILL.md", "references/p.md"]


def test_materialize_skills_requires_skill_md() -> None:
    bundle = SkillBundle(id="s1", name="bad", files=[SkillFile(path="other.md", content=b"x")])
    with pytest.raises(SkillMaterializationError, match="SKILL.md"):
        materialize_skills([bundle])


def test_materialize_skills_rejects_traversal() -> None:
    bundle = SkillBundle(
        id="s1", name="evil", files=[SkillFile(path="../etc/SKILL.md", content=b"x")]
    )
    with pytest.raises(SkillMaterializationError, match="traversal"):
        materialize_skills([bundle])


def test_enforce_disabled_tools_hard() -> None:
    results = enforce_disabled_tools("codex-app-server", "advisory", ["bash"])
    assert results[0].enforcement == "advisory"
    assert results[0].requested == "deny"


def test_enforce_disabled_tools_never_lies_about_hard() -> None:
    results = enforce_disabled_tools("fake", "unsupported", ["bash"])
    assert results[0].enforcement == "unsupported"
    assert results[0].safeReason == "advisory_instruction"


# === appended coverage ===





def test_validate_mcp_server_requires_non_empty_name() -> None:
    errors = validate_mcp_server(McpServerConfig(name="", url="https://mcp.example.com"))
    assert "name_required" in errors


def test_validate_mcp_server_requires_non_empty_url() -> None:
    errors = validate_mcp_server(McpServerConfig(name="repo", url=""))
    assert "url_required" in errors


def test_validate_mcp_server_rejects_unknown_transport() -> None:
    errors = validate_mcp_server(
        McpServerConfig(name="repo", url="https://mcp.example.com", transport="ftp")
    )
    assert "transport_invalid" in errors


def test_validate_mcp_server_rejects_unknown_auth_type() -> None:
    errors = validate_mcp_server(
        McpServerConfig(name="repo", url="https://mcp.example.com", authType="oauth2")
    )
    assert "auth_type_invalid" in errors


def test_materialize_skills_skips_disabled_bundles() -> None:
    enabled = SkillBundle(
        id="s_on",
        name="on",
        files=[SkillFile(path="SKILL.md", content=b"x")],
    )
    disabled = SkillBundle(
        id="s_off",
        name="off",
        enabled=False,
        files=[SkillFile(path="SKILL.md", content=b"x")],
    )
    result = materialize_skills([enabled, disabled])
    assert "s_on" in result
    assert "s_off" not in result


def test_materialize_skills_rejects_empty_path() -> None:
    bundle = SkillBundle(id="s1", name="bad", files=[SkillFile(path="", content=b"x")])
    with pytest.raises(SkillMaterializationError, match="skill_path_empty"):
        materialize_skills([bundle])


def test_materialize_skills_rejects_absolute_path() -> None:
    bundle = SkillBundle(id="s1", name="bad", files=[SkillFile(path="/tmp/SKILL.md", content=b"x")])
    with pytest.raises(SkillMaterializationError, match="skill_path_absolute"):
        materialize_skills([bundle])