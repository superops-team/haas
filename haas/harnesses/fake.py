"""Fake harness adapter for protocol decoupling tests (roadmap S3)."""
from __future__ import annotations

from collections.abc import AsyncIterator

from haas.harnesses.base import (
    AdapterProbe,
    AdapterTurnResult,
    ArtifactRef,
    CancelResult,
    CancelTurnRequest,
    CleanupResult,
    CleanupSessionRequest,
    HarnessEvent,
    HarnessSandboxDecl,
    InspectSessionRequest,
    ListArtifactsRequest,
    PreparedSession,
    PrepareSessionRequest,
    ResumeSessionRequest,
    SessionInspection,
    StartTurnRequest,
    TurnHandle,
)


class FakeAdapter:
    """Deterministic in-process adapter: emits two text deltas then completes."""

    base = "fake"
    adapter_id = "fake-adapter"
    version = "0.1.0"

    async def probe(self) -> AdapterProbe:
        return AdapterProbe(
            adapterId=self.adapter_id,
            base=self.base,
            status="ready",
            runtimeVersion="fake-0.1.0",
            transport="in_process",
            capabilities={
                "streaming": True,
                "sessionContinuation": "emulated",
                "cancellation": "hard",
                "toolRestriction": "advisory",
                "mcp": "unsupported",
                "skills": "unsupported",
                "files": "unsupported",
                "usage": "unavailable",
            },
        )

    async def prepare_session(self, request: PrepareSessionRequest) -> PreparedSession:
        return PreparedSession(sessionId=request.sessionId, nativeRef={"adapter": self.base})

    async def start_turn(self, request: StartTurnRequest) -> TurnHandle:
        return TurnHandle(
            turnId=request.turnId,
            sessionId=request.sessionId,
            invocationId=request.invocationId,
            opaque={"input_len": len(request.input)},
        )

    async def stream_events(self, handle: TurnHandle) -> AsyncIterator[HarnessEvent]:
        for index, text in enumerate(("hello", "world")):
            yield HarnessEvent(
                type="harness.text.delta",
                nativeType="fake/text",
                invocationId=handle.invocationId,
                sessionId=handle.sessionId,
                turnId=handle.turnId,
                author=self.base,
                content={"role": "model", "parts": [{"text": text}]},
                actions={"stateDelta": {"last_text": text, "seq": index}},
            )

    async def finalize_turn(self, handle: TurnHandle) -> AdapterTurnResult:
        terminal = HarnessEvent(
            type="harness.turn.completed",
            invocationId=handle.invocationId,
            sessionId=handle.sessionId,
            turnId=handle.turnId,
            author=self.base,
            content={"role": "model", "parts": []},
            actions={"stateDelta": {"status": "completed"}},
        )
        return AdapterTurnResult(status="completed", terminalEvent=terminal)

    async def cancel_turn(self, request: CancelTurnRequest) -> CancelResult:
        return CancelResult(status="cancelled")

    async def resume_session(self, request: ResumeSessionRequest) -> PreparedSession:
        raise NotImplementedError("fake resume not supported")

    async def inspect_session(self, request: InspectSessionRequest) -> SessionInspection:
        return SessionInspection(status="ready")

    async def list_artifacts(self, request: ListArtifactsRequest) -> list[ArtifactRef]:
        return []

    async def cleanup_session(self, request: CleanupSessionRequest) -> CleanupResult:
        return CleanupResult(status="cleaned")

    def sandbox_declaration(self) -> HarnessSandboxDecl:
        return HarnessSandboxDecl()
