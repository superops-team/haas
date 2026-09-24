"""Validation / materialization edge coverage for haas.mcp.runtime."""

from __future__ import annotations

import pytest

from haas.mcp import (
    McpServerConfig,
    SkillBundle,
    SkillFile,
    SkillMaterializationError,
    materialize_skills,
    validate_mcp_server,
)


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
