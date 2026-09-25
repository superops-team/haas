"""Harness adapter interface and canonical event types (specs/harness-adapter/)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, ValidationError, field_validator

__all__ = [
    "AdapterProbe",
    "AdapterTurnResult",
    "AdapterTurnStartError",
    "ArtifactRef",
    "CancelResult",
    "CancelTurnRequest",
    "CleanupResult",
    "CleanupSessionRequest",
    "CredentialHandle",
    "HarnessAdapter",
    "HarnessEvent",
    "HarnessSandboxDecl",
    "InspectSessionRequest",
    "ListArtifactsRequest",
    "McpServerConfig",
    "PreparedSession",
    "PrepareSessionRequest",
    "ResumeSessionRequest",
    "SandboxSpec",
    "SessionInspection",
    "StartTurnRequest",
    "TurnHandle",
    "TypedHarnessEvent",
    "KNOWN_HARNESS_EVENT_TYPES",
    "ValidationError",
]


class AdapterTurnStartError(Exception):
    """Structured error raised when a harness turn cannot be started.

    Sessions/manager layer imports this to map start-turn failures onto
    northbound error codes without depending on harness-native exceptions
    (adapter isolation, AGENTS.md 铁律 #5).
    """

    def __init__(self, code: str, retryable: bool = True, detail: str | None = None) -> None:
        self.code = code
        self.retryable = retryable
        self.detail = detail
        super().__init__(f"{code}: {detail or ''}")


@dataclass
class AdapterProbe:
    adapterId: str
    base: str
    status: str
    runtimeVersion: str
    transport: str
    capabilities: dict[str, Any] = field(default_factory=dict)
    safeDetails: dict[str, Any] = field(default_factory=dict)


@dataclass
class PrepareSessionRequest:
    sessionId: str
    appName: str
    workspace: dict[str, Any] = field(default_factory=dict)
    policy: dict[str, Any] = field(default_factory=dict)
    credentials: dict[str, Any] = field(default_factory=dict)


@dataclass
class PreparedSession:
    sessionId: str
    nativeRef: dict[str, Any] = field(default_factory=dict)


# --- Typed request models (P1-3 type safety) ---------------------------------


class SandboxSpec(BaseModel):
    """Typed view of the sandbox/exec environment handed to a turn.

    ``extra="allow"`` keeps this an additive transition: the manager/sessions
    layer may still pass additional keys (e.g. ``workspaceRoot``) which are
    retained in ``model_extra`` and exposed via attribute access.
    """

    model_config = ConfigDict(extra="allow")

    mode: Literal["read-only", "workspace-write", "danger-full-access"] = "workspace-write"
    network: Any = None
    writableRoot: str | None = None
    workspaceRoot: str | None = None
    writableRoots: Any = None


class CredentialHandle(BaseModel):
    """A secretless reference to a provider credential (never the raw secret)."""

    model_config = ConfigDict(extra="allow")

    ref: str


class McpServerConfig(BaseModel):
    """Typed MCP server declaration. Extra fields are retained for the adapter."""

    model_config = ConfigDict(extra="allow")

    name: str
    url: HttpUrl | None = None
    transport: Literal["stdio", "sse", "http"] = "stdio"


class StartTurnRequest(BaseModel):
    """Typed turn request. Accepts dict inputs from the sessions layer.

    ``extra="allow"`` is a transitional tolerance: callers (sessions.py, owned
    by another agent) keep passing keyword arguments including nested dicts.
    Pydantic coerces ``sandbox``/``mcpServers`` into their typed views while
    unknown top-level keys are preserved.
    """

    model_config = ConfigDict(extra="allow")

    invocationId: str
    sessionId: str
    turnId: str
    appName: str
    input: list[Any]
    model: str | None = None
    instructions: str | None = None
    maxStep: int | None = None
    timeoutSeconds: float = 86_400
    sandbox: SandboxSpec | dict[str, Any] = Field(default_factory=lambda: SandboxSpec())
    policy: dict[str, Any] = Field(default_factory=dict)
    credentials: dict[str, Any] = Field(default_factory=dict)
    mcpServers: list[McpServerConfig | dict[str, Any]] = Field(default_factory=list)
    principalId: str = ""
    userId: str = ""

    @field_validator("sandbox", mode="before")
    @classmethod
    def _coerce_sandbox(cls, v: Any) -> Any:
        if isinstance(v, dict):
            return SandboxSpec(**v)
        return v

    @field_validator("mcpServers", mode="before")
    @classmethod
    def _coerce_mcp_servers(cls, v: Any) -> Any:
        if isinstance(v, list):
            return [McpServerConfig(**item) if isinstance(item, dict) else item for item in v]
        return v


@dataclass
class TurnHandle:
    turnId: str
    sessionId: str
    invocationId: str
    opaque: dict[str, Any] = field(default_factory=dict)


@dataclass
class CancelTurnRequest:
    turnId: str
    sessionId: str
    invocationId: str
    reason: str = "cancelled"


@dataclass
class CancelResult:
    status: str  # accepted | cancelled | unsupported


# --- Canonical harness events ------------------------------------------------

#: Every harness.* event type an adapter may legitimately emit. Unknown types
#: fail boundary validation (TypedHarnessEvent) rather than degrading silently.
KNOWN_HARNESS_EVENT_TYPES = frozenset(
    {
        "harness.text.delta",
        "harness.reasoning.delta",
        "harness.output.item.completed",
        "harness.tool.started",
        "harness.tool.output",
        "harness.tool.completed",
        "harness.tool.failed",
        "harness.plan.updated",
        "harness.usage",
        "harness.turn.started",
        "harness.turn.completed",
        "harness.turn.failed",
        "harness.turn.incomplete",
        "harness.turn.interrupted",
        "harness.turn.cancelled",
        "haas.approval.required",
        "haas.approval.resolved",
        "haas.input.required",
        "haas.input.resolved",
    }
)

HarnessEventType = Literal[
    "harness.text.delta",
    "harness.reasoning.delta",
    "harness.output.item.completed",
    "harness.tool.started",
    "harness.tool.output",
    "harness.tool.completed",
    "harness.tool.failed",
    "harness.plan.updated",
    "harness.usage",
    "harness.turn.started",
    "harness.turn.completed",
    "harness.turn.failed",
    "harness.turn.incomplete",
    "harness.turn.interrupted",
    "harness.turn.cancelled",
    "haas.approval.required",
    "haas.approval.resolved",
    "haas.input.required",
    "haas.input.resolved",
]


class TypedHarnessEvent(BaseModel):
    """Boundary-validated harness event.

    The internal :class:`HarnessEvent` dataclass remains the carrier produced
    by adapters/normalizers; this model is the typed boundary that rejects
    unknown event types (discriminated on ``type``) instead of degrading them
    silently. ``extra="allow"`` preserves payload/action detail.
    """

    model_config = ConfigDict(extra="allow")

    type: HarnessEventType
    invocationId: str
    sessionId: str
    turnId: str
    author: str


@dataclass
class HarnessEvent:
    type: str
    invocationId: str
    sessionId: str
    turnId: str
    author: str
    content: dict[str, Any]
    actions: dict[str, Any] = field(default_factory=dict)
    nativeType: str | None = None
    usage: dict[str, Any] | None = None


@dataclass
class AdapterTurnResult:
    status: str  # completed | failed | incomplete | interrupted | cancelled
    terminalEvent: HarnessEvent | None = None


@dataclass
class HarnessSandboxDecl:
    cwd: str = "/workspace"
    writableRoots: list[str] = field(default_factory=lambda: ["/workspace"])
    approvalMode: str = "on-request"


@dataclass
class ResumeSessionRequest:
    sessionId: str
    opaque: dict[str, Any] = field(default_factory=dict)
    appName: str = ""
    userId: str = ""


@dataclass
class InspectSessionRequest:
    sessionId: str
    appName: str = ""
    userId: str = ""


@dataclass
class SessionInspection:
    status: str
    nativeRef: dict[str, Any] = field(default_factory=dict)


@dataclass
class ListArtifactsRequest:
    sessionId: str
    appName: str = ""
    userId: str = ""


@dataclass
class ArtifactRef:
    name: str
    path: str
    content: bytes | None = None
    mediaType: str | None = None


@dataclass
class CleanupSessionRequest:
    sessionId: str
    reason: str = "deleted"
    appName: str = ""
    userId: str = ""


@dataclass
class CleanupResult:
    status: str


class HarnessAdapter(Protocol):
    """Typed async interface every harness runtime must implement."""

    base: str
    adapter_id: str
    version: str

    async def probe(self) -> AdapterProbe: ...
    async def prepare_session(self, request: PrepareSessionRequest) -> PreparedSession: ...
    async def start_turn(self, request: StartTurnRequest) -> TurnHandle: ...
    def stream_events(self, handle: TurnHandle) -> AsyncIterator[HarnessEvent]: ...
    async def finalize_turn(self, handle: TurnHandle) -> AdapterTurnResult: ...
    async def cancel_turn(self, request: CancelTurnRequest) -> CancelResult: ...
    async def resume_session(self, request: ResumeSessionRequest) -> PreparedSession: ...
    async def inspect_session(self, request: InspectSessionRequest) -> SessionInspection: ...
    async def list_artifacts(self, request: ListArtifactsRequest) -> list[ArtifactRef]: ...
    async def cleanup_session(self, request: CleanupSessionRequest) -> CleanupResult: ...
    def sandbox_declaration(self) -> HarnessSandboxDecl: ...
