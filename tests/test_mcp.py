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


def test_validate_mcp_server_valid() -> None:
    assert (
        validate_mcp_server(McpServerConfig(name="repo", url="https://mcp.example.com/mcp"))
        == []
    )


def test_validate_mcp_server_rejects_bad_scheme() -> None:
    errors = validate_mcp_server(McpServerConfig(name="repo", url="file:///etc"))
    assert "url_scheme_invalid" in errors


def test_validate_mcp_server_requires_secret_ref() -> None:
    errors = validate_mcp_server(
        McpServerConfig(name="repo", url="https://mcp.example.com", authType="secret_ref")
    )
    assert "auth_ref_required" in errors


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
    bundle = SkillBundle(
        id="s1", name="bad", files=[SkillFile(path="other.md", content=b"x")]
    )
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
