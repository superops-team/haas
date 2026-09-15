"""MCP / Tool / Skill materialization (specs/mcp-tool-skill-runtime §5)."""

from __future__ import annotations

import os

from haas.mcp.models import McpServerConfig, SkillBundle, ToolRestrictionResult
from haas.policy.controller import PolicyController
from haas.policy.models import (
    EffectivePolicy,
    ModelPolicy,
    NetworkPolicy,
    PolicyScope,
    ToolsPolicy,
    WorkspacePolicy,
)

_VALID_TRANSPORTS = {"http", "sse", "stdio"}
_VALID_AUTH_TYPES = {"none", "secret_ref"}
_VALID_TOOL_ENFORCEMENT = {"hard", "advisory", "unsupported"}


class McpValidationError(Exception):
    """Raised when an MCP server config is invalid."""


class SkillMaterializationError(Exception):
    """Raised when a skill bundle cannot be safely materialized."""


def validate_mcp_server(
    server: McpServerConfig, policy: EffectivePolicy | None = None
) -> list[str]:
    """Return a list of validation errors (empty means valid)."""
    errors: list[str] = []
    if not server.name:
        errors.append("name_required")
    if not server.url:
        errors.append("url_required")
    elif not server.url.startswith(("http://", "https://")):
        errors.append("url_scheme_invalid")
    else:
        network_policy = policy if policy is not None else _default_mcp_url_policy()
        decision = PolicyController().authorize_network(network_policy, server.url)
        if not decision.allowed:
            errors.append(f"url_{decision.safeReason}")
    if server.transport not in _VALID_TRANSPORTS:
        errors.append("transport_invalid")
    if server.authType not in _VALID_AUTH_TYPES:
        errors.append("auth_type_invalid")
    if server.authType == "secret_ref" and not server.authRef:
        errors.append("auth_ref_required")
    return errors


def _default_mcp_url_policy() -> EffectivePolicy:
    """Default MCP validation policy: public HTTP(S) allowed, SSRF ranges denied."""
    return EffectivePolicy(
        policyId="pol_mcp_default",
        version=1,
        scope=PolicyScope(),
        workspace=WorkspacePolicy(),
        network=NetworkPolicy(defaultAction="allow", allow=[]),
        tools=ToolsPolicy(),
        model=ModelPolicy(),
    )


def materialize_skills(skills: list[SkillBundle]) -> dict[str, list[str]]:
    """Validate + materialize skill bundles; returns {skill_id: [safe paths]}.

    Rejects paths that are absolute, contain backslashes, or escape the skill
    root via ``..``. Every enabled skill must contain ``SKILL.md``.
    """
    result: dict[str, list[str]] = {}
    for skill in skills:
        if not skill.enabled:
            continue
        paths: list[str] = []
        for file in skill.files:
            paths.append(_safe_skill_path(file.path))
        if "SKILL.md" not in paths:
            raise SkillMaterializationError(f"skill {skill.name} missing SKILL.md")
        result[skill.id] = paths
    return result


def enforce_disabled_tools(
    adapter_id: str,
    capability: str,
    disabled: list[str],
) -> list[ToolRestrictionResult]:
    """Map disabled tools to their truthful enforcement level.

    ``capability`` must be the adapter's declared ``toolRestriction``
    capability (hard / advisory / unsupported). Instruction-only fallback is
    never reported as a hard block.
    """
    if capability not in _VALID_TOOL_ENFORCEMENT:
        capability = "unsupported"
    safe_reason = "native_permission_config" if capability == "hard" else "advisory_instruction"
    return [
        ToolRestrictionResult(
            tool=tool,
            requested="deny",
            enforcement=capability,
            adapterId=adapter_id,
            safeReason=safe_reason,
        )
        for tool in disabled
    ]


def _safe_skill_path(path: str) -> str:
    if not path or "\x00" in path:
        raise SkillMaterializationError("skill_path_empty")
    if path.startswith("/") or path.startswith("\\"):
        raise SkillMaterializationError("skill_path_absolute")
    normalized = os.path.normpath(path)
    if normalized in {".", ".."} or normalized.startswith("../"):
        raise SkillMaterializationError("skill_path_traversal")
    return normalized
