"""Unified HarnessAdapter contract suite (specs/harness-adapter §11).

Every concrete adapter must pass this suite; it is parametrized over the
in-process fakes for S3 and reused for real adapters in later stages.
"""

import pytest

from haas.harnesses.base import (
    CancelTurnRequest,
    PrepareSessionRequest,
    StartTurnRequest,
)
from haas.harnesses.fake import FakeAdapter, SlowFakeAdapter


@pytest.fixture(params=[FakeAdapter, SlowFakeAdapter])
def adapter(request: pytest.FixtureRequest) -> FakeAdapter:
    return request.param()


async def test_contract_probe(adapter: FakeAdapter) -> None:
    probe = await adapter.probe()
    assert probe.status == "ready"
    assert probe.capabilities["streaming"] is True
    assert probe.capabilities["cancellation"] in {"hard", "best_effort", "unsupported"}


async def test_contract_prepare_and_start(adapter: FakeAdapter) -> None:
    prepared = await adapter.prepare_session(
        PrepareSessionRequest(sessionId="hsess_1", appName="chrn_1")
    )
    assert prepared.sessionId == "hsess_1"

    handle = await adapter.start_turn(
        StartTurnRequest(
            invocationId="inv_1",
            sessionId="hsess_1",
            turnId="turn_1",
            appName="chrn_1",
            input=[{"text": "hi"}],
        )
    )
    assert handle.turnId == "turn_1"


async def test_contract_stream_and_terminal(adapter: FakeAdapter) -> None:
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
    assert events
    assert all(event.author == adapter.base for event in events)
    assert all("parts" in event.content for event in events)

    result = await adapter.finalize_turn(handle)
    assert result.status in {"completed", "cancelled", "failed", "incomplete"}


async def test_contract_cancel(adapter: FakeAdapter) -> None:
    result = await adapter.cancel_turn(
        CancelTurnRequest(turnId="turn_1", sessionId="hsess_1", invocationId="inv_1")
    )
    assert result.status in {"accepted", "cancelled", "unsupported"}
