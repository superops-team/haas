"""Fake harness adapter for protocol decoupling tests (roadmap S3)."""

from __future__ import annotations

import asyncio
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
                "pausing": "emulated",
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


class SlowFakeAdapter(FakeAdapter):
    """Cancel-able fake adapter: yields events with a delay and honors cancel."""

    base = "fake-slow"
    adapter_id = "fake-slow"

    def __init__(self, delay: float = 0.02) -> None:
        self.delay = delay
        self._cancelled: set[str] = set()

    async def stream_events(self, handle: TurnHandle) -> AsyncIterator[HarnessEvent]:
        for index, text in enumerate(("hello", "world")):
            if handle.turnId in self._cancelled:
                return
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
            await asyncio.sleep(self.delay)

    async def cancel_turn(self, request: CancelTurnRequest) -> CancelResult:
        self._cancelled.add(request.turnId)
        return CancelResult(status="cancelled")

    async def finalize_turn(self, handle: TurnHandle) -> AdapterTurnResult:
        if handle.turnId in self._cancelled:
            terminal = HarnessEvent(
                type="harness.turn.cancelled",
                invocationId=handle.invocationId,
                sessionId=handle.sessionId,
                turnId=handle.turnId,
                author=self.base,
                content={"role": "model", "parts": []},
                actions={"stateDelta": {"status": "cancelled"}},
            )
            return AdapterTurnResult(status="cancelled", terminalEvent=terminal)
        return await super().finalize_turn(handle)


class BlockingFakeAdapter(SlowFakeAdapter):
    """Yields the first event, then blocks until cancel_turn is called."""

    base = "fake-blocking"
    adapter_id = "fake-blocking"

    def __init__(self) -> None:
        super().__init__(delay=0.0)
        self._barriers: dict[str, asyncio.Event] = {}

    async def stream_events(self, handle: TurnHandle) -> AsyncIterator[HarnessEvent]:
        yield HarnessEvent(
            type="harness.text.delta",
            nativeType="fake/text",
            invocationId=handle.invocationId,
            sessionId=handle.sessionId,
            turnId=handle.turnId,
            author=self.base,
            content={"role": "model", "parts": [{"text": "hello"}]},
            actions={"stateDelta": {"last_text": "hello", "seq": 0}},
        )
        barrier = asyncio.Event()
        self._barriers[handle.turnId] = barrier
        await barrier.wait()

    async def cancel_turn(self, request: CancelTurnRequest) -> CancelResult:
        self._cancelled.add(request.turnId)
        self._barriers.setdefault(request.turnId, asyncio.Event()).set()
        return CancelResult(status="cancelled")

    async def finalize_turn(self, handle: TurnHandle) -> AdapterTurnResult:
        if handle.turnId in self._cancelled:
            terminal = HarnessEvent(
                type="harness.turn.cancelled",
                invocationId=handle.invocationId,
                sessionId=handle.sessionId,
                turnId=handle.turnId,
                author=self.base,
                content={"role": "model", "parts": []},
                actions={"stateDelta": {"status": "cancelled"}},
            )
            return AdapterTurnResult(status="cancelled", terminalEvent=terminal)
        return await super().finalize_turn(handle)
