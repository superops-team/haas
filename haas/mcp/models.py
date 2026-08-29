"""MCP / Tool / Skill Runtime data models (specs/mcp-tool-skill-runtime §6)."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class McpServerConfig:
    name: str
    url: str
    transport: str = "http"
    enabled: bool = True
    headers: dict[str, str] = field(default_factory=dict)
    authType: str = "none"  # none | secret_ref
    authRef: str = ""
    timeoutSeconds: int = 60
    required: bool = False


@dataclass
class SkillFile:
    path: str
    content: bytes


@dataclass
class SkillBundle:
    id: str
    name: str
    enabled: bool = True
    files: list[SkillFile] = field(default_factory=list)
    fingerprint: str = ""


@dataclass
class ToolRestrictionResult:
    tool: str
    requested: str
    enforcement: str  # hard | advisory | unsupported
    adapterId: str = ""
    safeReason: str = ""
