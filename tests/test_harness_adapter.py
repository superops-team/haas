"""Harness adapter interface and fake adapter tests (specs/harness-adapter/)."""
from haas.harnesses.base import (
    CancelTurnRequest,
    PrepareSessionRequest,
    StartTurnRequest,
)
from haas.harnesses.fake import FakeAdapter


async def test_fake_adapter_probe_and_prepare() -> None:
    adapter = FakeAdapter()
    probe = await adapter.probe()
    assert probe.status == "ready"
    assert probe.capabilities["streaming"] is True

    prepared = await adapter.prepare_session(
        PrepareSessionRequest(sessionId="hsess_1", appName="chrn_1")
    )
    assert prepared.sessionId == "hsess_1"


async def test_fake_adapter_stream_and_finalize() -> None:
    adapter = FakeAdapter()
    handle = await adapter.start_turn(
        StartTurnRequest(
            invocationId="inv_1", sessionId="hsess_1", turnId="turn_1", appName="chrn_1",
            input=[{"text": "hi"}],
        )
    )

    events = [event async for event in adapter.stream_events(handle)]
    assert [e.type for e in events] == ["harness.text.delta", "harness.text.delta"]
    assert [e.content["parts"][0]["text"] for e in events] == ["hello", "world"]

    result = await adapter.finalize_turn(handle)
    assert result.status == "completed"
    assert result.terminalEvent is not None
    assert result.terminalEvent.type == "harness.turn.completed"


async def test_fake_adapter_cancel() -> None:
    adapter = FakeAdapter()
    result = await adapter.cancel_turn(
        CancelTurnRequest(turnId="turn_1", sessionId="hsess_1", invocationId="inv_1")
    )
    assert result.status == "cancelled"


async def test_fake_adapter_sandbox_declaration() -> None:
    adapter = FakeAdapter()
    decl = adapter.sandbox_declaration()
    assert decl.cwd == "/workspace"
    assert decl.approvalMode == "never"
