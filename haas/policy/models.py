"""Policy Controller data models (specs/policy-controller/README.md §6)."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PolicyScope:
    tenantId: str = ""
    workspaceId: str = ""
    harnessId: str = ""
    sessionId: str = ""


@dataclass
class WorkspacePolicy:
    # danger-full-access | workspace-write | read-only
    mode: str = "workspace-write"
    root: str = "/workspace"
    writableRoots: list[str] = field(default_factory=list)


@dataclass
class NetworkPolicy:
    # deny | allow  (deny is the only safe default)
    defaultAction: str = "deny"
    allow: list[str] = field(default_factory=list)


@dataclass
class ToolsPolicy:
    disabled: list[str] = field(default_factory=list)
    approvalMode: str = "never"


@dataclass
class ModelPolicy:
    allowedModels: list[str] = field(default_factory=list)
    fallbackModel: str = ""


@dataclass
class EffectivePolicy:
    policyId: str
    version: int
    scope: PolicyScope
    workspace: WorkspacePolicy
    network: NetworkPolicy
    tools: ToolsPolicy
    model: ModelPolicy


@dataclass
class PolicyLayer:
    """One precedence layer (tenant/workspace/harness/session/turn).

    ``None`` fields mean "no opinion at this layer" and are skipped during
    merge. ``delegation=True`` explicitly grants lower layers the right to
    widen this layer's constraints; without it, widening is rejected.
    """

    name: str
    workspace: WorkspacePolicy | None = None
    network: NetworkPolicy | None = None
    tools: ToolsPolicy | None = None
    model: ModelPolicy | None = None
    delegation: bool = False


@dataclass
class PolicyCompileInput:
    scope: PolicyScope
    # Ordered from broad to narrow (platform/tenant -> turn override).
    layers: list[PolicyLayer] = field(default_factory=list)


@dataclass
class PolicyDecision:
    allowed: bool
    code: str = ""
    safeReason: str = ""
    retryable: bool = False
