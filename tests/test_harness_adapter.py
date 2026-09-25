"""Harness adapter interface and fake adapter tests (specs/harness-adapter/)."""

import pytest
from pydantic import ValidationError

from haas.harnesses.base import (
    CancelTurnRequest,
    CredentialHandle,
    McpServerConfig,
    PrepareSessionRequest,
    StartTurnRequest,
    TypedHarnessEvent,
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
            invocationId="inv_1",
            sessionId="hsess_1",
            turnId="turn_1",
            appName="chrn_1",
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
    assert decl.approvalMode == "on-request"


# --- P1-3: typed request models & boundary event validation -----------------


def _req(**kwargs) -> StartTurnRequest:
    base = dict(invocationId="inv_1", sessionId="hsess_1", turnId="turn_1",
                appName="chrn_1", input=[{"text": "hi"}])
    base.update(kwargs)
    return StartTurnRequest(**base)


def test_start_request_accepts_dict_sandbox_and_mcp() -> None:
    req = _req(
        sandbox={"mode": "read-only", "workspaceRoot": "/ws"},
        mcpServers=[{"name": "s", "url": "http://127.0.0.1:9/x", "transport": "http"}],
    )
    assert req.sandbox.mode == "read-only"
    assert req.sandbox.workspaceRoot == "/ws"
    assert req.mcpServers[0].name == "s"


def test_start_request_rejects_unknown_sandbox_mode() -> None:
    with pytest.raises(ValidationError):
        _req(sandbox={"mode": "bogus-mode"})


def test_credential_handle_requires_ref() -> None:
    with pytest.raises(ValidationError):
        CredentialHandle()


def test_mcp_server_rejects_malformed_url() -> None:
    with pytest.raises(ValidationError):
        McpServerConfig(name="s", url="not-a-url")


def test_mcp_server_accepts_http_url() -> None:
    server = McpServerConfig(name="s", url="http://127.0.0.1:9/x", transport="http")
    assert str(server.url).startswith("http://")


def test_typed_event_rejects_unknown_type() -> None:
    with pytest.raises(ValidationError):
        TypedHarnessEvent(
            type="harness.bogus", invocationId="i", sessionId="s",
            turnId="t", author="codex",
        )


@pytest.mark.parametrize(
    "event_type",
    [
        "harness.text.delta",
        "harness.reasoning.delta",
        "harness.tool.started",
        "harness.tool.completed",
        "harness.tool.failed",
        "harness.turn.completed",
        "harness.turn.failed",
        "haas.approval.required",
        "haas.input.required",
    ],
)
def test_typed_event_accepts_known_types(event_type: str) -> None:
    event = TypedHarnessEvent(
        type=event_type, invocationId="i", sessionId="s", turnId="t", author="codex",
    )
    assert event.type == event_type


# --- FakeAdapter surface coverage: resume/inspect/artifacts/cleanup ---------


async def test_fake_adapter_resume_is_unsupported() -> None:
    import pytest

    from haas.harnesses.base import ResumeSessionRequest

    with pytest.raises(NotImplementedError):
        await FakeAdapter().resume_session(ResumeSessionRequest(sessionId="s"))


async def test_fake_adapter_inspect_list_artifacts_cleanup() -> None:
    from haas.harnesses.base import (
        CleanupSessionRequest,
        InspectSessionRequest,
        ListArtifactsRequest,
    )

    adapter = FakeAdapter()
    inspection = await adapter.inspect_session(InspectSessionRequest(sessionId="s"))
    assert inspection.status == "ready"
    assert await adapter.list_artifacts(ListArtifactsRequest(sessionId="s")) == []
    cleaned = await adapter.cleanup_session(CleanupSessionRequest(sessionId="s"))
    assert cleaned.status == "cleaned"


# --- SlowFakeAdapter: cancel mid-stream -------------------------------------


async def test_slow_fake_adapter_streams_both_events_when_uncancelled() -> None:
    from haas.harnesses.fake import SlowFakeAdapter

    adapter = SlowFakeAdapter(delay=0.0)
    handle = await adapter.start_turn(
        StartTurnRequest(
            invocationId="inv_1", sessionId="s", turnId="t",
            appName="chrn_1", input=[{"text": "hi"}],
        )
    )
    events = [event async for event in adapter.stream_events(handle)]
    assert [e.content["parts"][0]["text"] for e in events] == ["hello", "world"]
    result = await adapter.finalize_turn(handle)
    assert result.status == "completed"


async def test_slow_fake_adapter_stops_after_cancel() -> None:
    from haas.harnesses.fake import SlowFakeAdapter

    adapter = SlowFakeAdapter(delay=0.05)
    handle = await adapter.start_turn(
        StartTurnRequest(
            invocationId="inv_1", sessionId="s", turnId="t",
            appName="chrn_1", input=[{"text": "hi"}],
        )
    )
    await adapter.cancel_turn(
        CancelTurnRequest(turnId="t", sessionId="s", invocationId="inv_1")
    )
    events = [event async for event in adapter.stream_events(handle)]
    # The cancelled turnId short-circuits before/at the first event.
    assert all(e.content["parts"][0]["text"] == "hello" for e in events)
    result = await adapter.finalize_turn(handle)
    assert result.status == "cancelled"
    assert result.terminalEvent is not None
    assert result.terminalEvent.type == "harness.turn.cancelled"


# --- BlockingFakeAdapter: yields one event then blocks until cancel ----------


async def test_blocking_fake_adapter_unblocks_on_cancel() -> None:
    import asyncio

    from haas.harnesses.fake import BlockingFakeAdapter

    adapter = BlockingFakeAdapter()
    handle = await adapter.start_turn(
        StartTurnRequest(
            invocationId="inv_1", sessionId="s", turnId="t",
            appName="chrn_1", input=[{"text": "hi"}],
        )
    )

    collected: list[object] = []

    async def drain() -> None:
        async for event in adapter.stream_events(handle):
            collected.append(event)

    task = asyncio.create_task(drain())
    # Wait for the single event to be yielded, then cancel to release the barrier.
    for _ in range(50):
        if collected:
            break
        await asyncio.sleep(0.01)
    await adapter.cancel_turn(
        CancelTurnRequest(turnId="t", sessionId="s", invocationId="inv_1")
    )
    await asyncio.wait_for(task, timeout=2)
    assert len(collected) == 1
    result = await adapter.finalize_turn(handle)
    assert result.status == "cancelled"
