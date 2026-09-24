"""Unit-level coverage for event type inference, metadata projection and
typed-metadata validation branches that the integration suites do not hit."""

from __future__ import annotations

import pytest

from haas.events import (
    EventLog,
    _harness_event_metadata,
    _infer_event_type,
    _infer_haas_metadata,
    _validate_typed_metadata,
)
from haas.harnesses.base import HarnessEvent
from haas.stores import MemoryStore


# --- event type inference ----------------------------------------------------


@pytest.mark.parametrize("status", ["completed", "failed", "incomplete", "interrupted", "cancelled"])
def test_infer_event_type_turn_status(status: str) -> None:
    assert _infer_event_type({}, {"stateDelta": {"status": status}}) == f"haas.turn.{status}"


def test_infer_event_type_unparsed_when_no_signal() -> None:
    assert _infer_event_type({"input": "hi"}, {}) == "haas.adapter.event_unparsed"


# --- haas metadata projection ----------------------------------------------


def test_infer_haas_metadata_terminal_statuses() -> None:
    assert _infer_haas_metadata("haas.turn.completed", {}) == {"status": "completed"}
    assert _infer_haas_metadata("haas.turn.cancelled", {}) == {"status": "cancelled"}
    assert _infer_haas_metadata("haas.turn.interrupted", {}) == {
        "status": "interrupted",
        "controlIntent": "pause",
    }
    failed = _infer_haas_metadata(
        "haas.turn.failed",
        {"stateDelta": {"status": "failed", "reason": "boom", "code": "x", "retryable": True}},
    )
    assert failed["status"] == "failed"
    assert failed["code"] == "x"
    assert failed["retryable"] is True
    assert _infer_haas_metadata("haas.adapter.event_unparsed", {}) == {
        "safeReason": "event_type_unparsed",
        "retryable": False,
    }


# --- harness event metadata --------------------------------------------------


def _harness_event(type_: str, *, usage=None, actions=None) -> HarnessEvent:
    return HarnessEvent(
        type=type_,
        invocationId="inv_1",
        sessionId="hsess_1",
        turnId="turn_1",
        author="codex",
        content={},
        actions=actions or {},
        usage=usage,
    )


def test_harness_metadata_usage_carries_native_fields() -> None:
    event = _harness_event(
        "haas.usage.updated",
        usage={"input_tokens": 3},
        actions={"haas": {"scope": "prov", "modelCallId": "mc_1", "cumulativeUsage": True}},
    )
    meta = _harness_event_metadata("haas.usage.updated", event)
    assert meta["usage"] == {"input_tokens": 3}
    assert meta["scope"] == "prov"
    assert meta["modelCallId"] == "mc_1"


def test_harness_metadata_plan_and_turn_lifecycle() -> None:
    plan = _harness_event("haas.plan.updated", actions={"haas": {"counts": {}}})
    assert _harness_event_metadata("haas.plan.updated", plan) == {"counts": {}}
    started = _harness_event("haas.turn.started")
    assert _harness_event_metadata("haas.turn.started", started) == {"status": "running"}
    failed = _harness_event(
        "haas.turn.failed", actions={"stateDelta": {"status": "failed", "reason": "r"}}
    )
    assert _harness_event_metadata("haas.turn.failed", failed)["safeReason"] == "r"
    unparsed = _harness_event("haas.adapter.event_unparsed")
    assert _harness_event_metadata("haas.adapter.event_unparsed", unparsed)["retryable"] is False


def test_tool_activity_metadata_carries_command_fields() -> None:
    event = _harness_event(
        "haas.tool.started",
        actions={
            "artifactDelta": {
                "toolCallId": "tc_1",
                "toolName": "bash",
                "activityKind": "command",
                "commandPreview": "ls -la",
                "workingDirectory": "/workspace",
                "evidenceRef": "ev_1",
            }
        },
    )
    meta = _harness_event_metadata("haas.tool.started", event)
    assert meta["commandPreview"] == "ls -la"
    assert meta["workingDirectory"] == "/workspace"
    assert meta["evidenceRef"] == "ev_1"
    assert meta["activityKind"] == "command"


# --- typed metadata validation -----------------------------------------------


def test_validate_plan_metadata_accepts_consistent_counts() -> None:
    # Must not raise.
    _validate_typed_metadata(
        "haas.plan.updated",
        {"counts": {"pending": 1, "inProgress": 1, "completed": 0}, "total": 2},
    )


def test_validate_interaction_metadata_requires_fields() -> None:
    with pytest.raises(ValueError, match="missing typed event metadata"):
        _validate_typed_metadata("haas.approval.required", {"approvalId": "ap_1"})


# --- read session with cursor ------------------------------------------------


def test_read_session_after_cursor(tmp_path=None) -> None:
    store = MemoryStore()
    log = EventLog(store=store)
    for i in range(3):
        log.append(
            app_name="chrn_1",
            user_id="u_1",
            invocation_id="inv_1",
            session_id="hsess_1",
            turn_id="turn_1",
            harness_id="codex",
            adapter_id="codex",
            author="codex",
            content={"role": "model", "parts": [{"type": "text", "text": f"m{i}"}]},
            actions={},
        )
    first = log.read_session("chrn_1", "u_1", "hsess_1")
    assert len(first) == 3
    after = log.read_session("chrn_1", "u_1", "hsess_1", after_event_id=first[1].eventId)
    assert [event.eventId for event in after] == [first[2].eventId]
