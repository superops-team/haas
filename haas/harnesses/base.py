"""Harness adapter interface and canonical event types (specs/harness-adapter/)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Protocol


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


@dataclass
class StartTurnRequest:
    invocationId: str
    sessionId: str
    turnId: str
    appName: str
    input: list[Any]
    model: str | None = None
    instructions: str | None = None
    maxStep: int | None = None
    timeoutSeconds: float = 86_400
    sandbox: dict[str, Any] = field(default_factory=dict)
    policy: dict[str, Any] = field(default_factory=dict)
    credentials: dict[str, Any] = field(default_factory=dict)
    mcpServers: list[dict[str, Any]] = field(default_factory=list)
    principalId: str = ""
    userId: str = ""


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
