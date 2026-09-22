"""Event Log & SSE tests (specs/event-log-sse/README.md)."""

import json

import pytest

from haas.events import HEARTBEAT_FRAME, EventLog
from haas.harnesses.base import HarnessEvent
from haas.stores import MemoryStore


def test_append_assigns_gapless_sequence_and_project_adk() -> None:
    log = EventLog(store=MemoryStore())
    first = log.append(
        app_name="chrn_1",
        user_id="u_1",
        invocation_id="inv_1",
        session_id="hsess_1",
        turn_id="turn_1",
        harness_id="chrn_1",
        adapter_id="fake",
        author="codex",
        content={"role": "model", "parts": [{"text": "hi"}]},
        actions={},
    )
    second = log.append(
        app_name="chrn_1",
        user_id="u_1",
        invocation_id="inv_1",
        session_id="hsess_1",
        turn_id="turn_1",
        harness_id="chrn_1",
        adapter_id="fake",
        author="codex",
        content={"role": "model", "parts": [{"text": "there"}]},
        actions={},
    )
    assert first.sequenceNumber == 0
    assert second.sequenceNumber == 1
    assert [e.sequenceNumber for e in log.read_invocation("chrn_1", "u_1", "hsess_1", "inv_1")] == [
        0,
        1,
    ]

    adk = log.project_adk(first)
    assert adk["id"] == first.eventId
    assert adk["invocationId"] == "inv_1"
    assert adk["timestamp"] == first.observedAtMs / 1000.0
    assert "sequenceNumber" not in adk


def test_sse_frame_and_heartbeat() -> None:
    log = EventLog(store=MemoryStore())
    event = log.append(
        app_name="chrn_1",
        user_id="u_1",
        invocation_id="inv_1",
        session_id="hsess_1",
        turn_id="turn_1",
        harness_id="chrn_1",
        adapter_id="fake",
        author="codex",
        content={"role": "model", "parts": [{"text": "hi"}]},
        actions={},
    )
    frame = log.sse_frame(event)
    assert frame.startswith("data: ")
    assert frame.endswith("\n\n")
    assert HEARTBEAT_FRAME == ": keep-alive\n\n"
    assert "sequenceNumber" not in frame


def test_append_redacts_secret_material_before_persist() -> None:
    log = EventLog(store=MemoryStore())
    secret_text = "token sk-ant-abcdefghijklmnopqrstuvwxyz123456"  # haas-secret-ignore
    event = log.append(
        app_name="chrn_1",
        user_id="u_1",
        invocation_id="inv_1",
        session_id="hsess_1",
        turn_id="turn_1",
        harness_id="chrn_1",
        adapter_id="fake",
        author="codex",
        content={"role": "model", "parts": [{"text": secret_text}]},
        actions={"stateDelta": {"authorization": "Bearer abc"}},
    )
    assert event.redactionApplied is True
    assert "sk-ant-" not in event.content["parts"][0]["text"]
    assert event.actions["stateDelta"]["authorization"] == "[REDACTED]"


def test_typed_policy_event_projects_public_fields_only() -> None:
    log = EventLog(store=MemoryStore())
    event = log.append_typed(
        type_="haas.delegation.policy_update_pending",
        app_name="chrn_1",
        user_id="u_1",
        invocation_id=None,
        session_id="hsess_1",
        turn_id=None,
        harness_id="chrn_1",
        adapter_id="internal-adapter",
        author="haas",
        content={"role": "model", "parts": []},
        actions={},
        haas={
            "delegatedSessionId": "dgsess_1",
            "updateId": "upd_1",
            "revision": 2,
            "fields": ["profileRef"],
        },
    )

    assert event.schemaVersion == 2
    payload = log.project_haas(event)
    assert payload["type"] == "haas.delegation.policy_update_pending"
    assert payload["haas"]["revision"] == 2
    assert "adapterId" not in payload
    assert "userId" not in payload
    assert "schemaVersion" not in payload
    assert "redactionApplied" not in payload
    assert json.loads(log.haas_frame(event).removeprefix("data: "))["type"] == payload["type"]


def test_typed_policy_failed_event_requires_safe_failure_metadata() -> None:
    log = EventLog(store=MemoryStore())
    base = {
        "type_": "haas.delegation.policy_update_failed",
        "app_name": "chrn_1",
        "user_id": "u_1",
        "invocation_id": None,
        "session_id": "hsess_1",
        "turn_id": None,
        "harness_id": "chrn_1",
        "adapter_id": "internal",
        "author": "haas",
        "content": {"role": "model", "parts": []},
        "actions": {},
        "haas": {
            "delegatedSessionId": "dgsess_1",
            "updateId": "upd_1",
            "revision": 2,
            "fields": ["profileRef"],
        },
    }

    with pytest.raises(ValueError, match="code"):
        log.append_typed(**base)


def test_adk_projection_never_exposes_native_type_or_metadata() -> None:
    log = EventLog(store=MemoryStore())
    event = log.append_typed(
        type_="haas.output.text.delta",
        app_name="chrn_1",
        user_id="u_1",
        invocation_id="inv_1",
        session_id="hsess_1",
        turn_id="turn_1",
        harness_id="chrn_1",
        adapter_id="fake",
        author="codex",
        content={"role": "model", "parts": [{"text": "hi"}]},
        actions={},
        haas={},
    )
    adk = log.project_adk(event)
    assert "type" not in adk
    assert "haas" not in adk


def test_plan_event_rejects_inconsistent_counts() -> None:
    log = EventLog(store=MemoryStore())
    with pytest.raises(ValueError, match="plan metadata"):
        log.append_typed(
            type_="haas.plan.updated",
            app_name="chrn_1",
            user_id="u_1",
            invocation_id="inv_1",
            session_id="hsess_1",
            turn_id="turn_1",
            harness_id="chrn_1",
            adapter_id="codex",
            author="codex",
            content={"role": "model", "parts": []},
            actions={},
            haas={
                "counts": {"pending": 1, "inProgress": 0, "completed": 1},
                "total": 1,
            },
        )


def test_artifact_registered_event_requires_safe_complete_metadata() -> None:
    log = EventLog(store=MemoryStore())
    base = {
        "type_": "haas.artifact.registered",
        "app_name": "chrn_1",
        "user_id": "u_1",
        "invocation_id": "inv_1",
        "session_id": "hsess_1",
        "turn_id": "turn_1",
        "harness_id": "chrn_1",
        "adapter_id": "codex",
        "author": "haas",
        "content": {"role": "model", "parts": []},
        "actions": {"artifactDelta": {}},
        "haas": {
            "fileId": "file_1",
            "relativePath": "output/report.md",
            "mediaType": "text/markdown",
            "bytes": 8,
            "invocationId": "inv_1",
            "previewStatus": "available",
            "downloadStatus": "available",
        },
    }

    event = log.append_typed(**base)
    assert event.haas["relativePath"] == "output/report.md"

    invalid = {**base, "haas": {**base["haas"], "hostPath": "/tmp/report.md"}}
    with pytest.raises(ValueError, match="artifact metadata"):
        log.append_typed(**invalid)

    invalid = {
        **base,
        "haas": {
            **base["haas"],
            "previewStatus": "available",
            "downloadStatus": "unavailable",
        },
    }
    with pytest.raises(ValueError, match="artifact metadata"):
        log.append_typed(**invalid)


@pytest.mark.parametrize(
    ("adapter_type", "canonical_type"),
    [
        ("harness.reasoning.delta", "haas.output.reasoning.delta"),
        ("harness.output.item.completed", "haas.output.item.completed"),
        ("harness.tool.started", "haas.tool.started"),
        ("harness.tool.output", "haas.tool.output"),
        ("harness.tool.completed", "haas.tool.completed"),
        ("harness.tool.failed", "haas.tool.failed"),
        ("haas.approval.required", "haas.approval.required"),
        ("haas.input.required", "haas.input.required"),
    ],
)
def test_append_harness_event_preserves_explicit_normalized_type(
    adapter_type: str, canonical_type: str
) -> None:
    log = EventLog(store=MemoryStore())
    interaction_metadata = (
        {
            "approvalId": "appr_1",
            "kind": "command",
            "safeSummary": "Run command",
            "policyReason": "harness_requested",
            "availableDecisions": ["approved", "denied"],
            "expiresAtMs": 1,
        }
        if adapter_type == "haas.approval.required"
        else {
            "inputRequestId": "inreq_1",
            "questions": [{"id": "q1", "question": "Choose"}],
            "blocking": True,
            "expiresAtMs": 1,
        }
        if adapter_type == "haas.input.required"
        else None
    )
    harness_event = HarnessEvent(
        type=adapter_type,
        invocationId="inv_1",
        sessionId="hsess_1",
        turnId="turn_1",
        author="codex",
        content={"role": "model", "parts": [{"text": "safe", "thought": True}]},
        actions=(
            {"haas": interaction_metadata}
            if interaction_metadata is not None
            else {"artifactDelta": {"toolCallId": "call_1", "toolName": "shell"}}
        ),
    )

    event = log.append_harness_event(
        harness_event,
        app_name="chrn_1",
        user_id="u_1",
        harness_id="chrn_1",
        adapter_id="codex-app-server",
    )

    assert event.type == canonical_type
    if adapter_type.startswith("haas."):
        # Interaction correlation must survive normalization so Manager can
        # answer the exact waiting native request.
        expected_id = "appr_1" if adapter_type == "haas.approval.required" else "inreq_1"
        assert expected_id in event.haas.values()


def test_tool_activity_metadata_survives_canonical_projection_with_bounds() -> None:
    log = EventLog(store=MemoryStore())
    harness_event = HarnessEvent(
        type="harness.tool.completed",
        invocationId="inv_1",
        sessionId="hsess_1",
        turnId="turn_1",
        author="codex",
        content={"role": "model", "parts": []},
        actions={
            "artifactDelta": {
                "toolCallId": "call_1",
                "toolName": "exec_command",
                "status": "completed",
                "activityKind": "command",
                "modelCallId": "mcall_0001",
                "durationMs": 1200,
                "exitCode": 0,
                "outputPreview": "\n".join(f"line {index}" for index in range(30)),
                "omittedLineCount": 0,
                "nativePayload": "must not cross the boundary",
            }
        },
    )

    event = log.append_harness_event(
        harness_event,
        app_name="chrn_1",
        user_id="u_1",
        harness_id="chrn_1",
        adapter_id="codex-app-server",
    )

    assert event.haas["activityKind"] == "command"
    assert event.haas["modelCallId"] == "mcall_0001"
    assert event.haas["durationMs"] == 1200
    assert event.haas["exitCode"] == 0
    assert len(event.haas["outputPreview"].splitlines()) == 20
    assert event.haas["outputPreview"].splitlines()[:2] == ["line 0", "line 1"]
    assert event.haas["outputPreview"].splitlines()[-2:] == ["line 28", "line 29"]
    assert event.haas["omittedLineCount"] == 10
    assert "nativePayload" not in event.haas


def test_output_correlation_and_nested_usage_survive_native_projection_only() -> None:
    log = EventLog(store=MemoryStore())
    output = HarnessEvent(
        type="harness.reasoning.delta", invocationId="inv_1", sessionId="hsess_1",
        turnId="turn_1", author="codex",
        content={"role": "model", "parts": [{"text": "safe summary", "thought": True}]},
        actions={"haas": {"itemId": "reason_1", "summaryIndex": 0,
                          "modelCallId": "mcall_0001"}},
    )
    usage = HarnessEvent(
        type="harness.usage", invocationId="inv_1", sessionId="hsess_1",
        turnId="turn_1", author="codex", content={"role": "model", "parts": []},
        actions={"stateDelta": {"usage": {"inputTokens": 10, "outputTokens": 5,
                                               "totalTokens": 15}},
                 "haas": {"scope": "model_call", "modelCallId": "mcall_0001",
                          "cumulativeUsage": {"inputTokens": 30, "outputTokens": 8,
                                               "totalTokens": 38}}},
        usage={"inputTokens": 10, "outputTokens": 5, "totalTokens": 15},
    )

    projected = [
        log.append_harness_event(event, app_name="chrn_1", user_id="u_1",
                                 harness_id="chrn_1", adapter_id="codex-app-server")
        for event in (output, usage)
    ]

    assert projected[0].haas == {
        "itemId": "reason_1", "summaryIndex": 0, "modelCallId": "mcall_0001"
    }
    assert projected[1].haas == {
        "usage": {"inputTokens": 10, "outputTokens": 5, "totalTokens": 15},
        "scope": "model_call",
        "modelCallId": "mcall_0001",
        "cumulativeUsage": {"inputTokens": 30, "outputTokens": 8, "totalTokens": 38},
    }
    assert "haas" not in log.project_adk(projected[0])


def test_output_item_completion_requires_item_id_and_preserves_phase() -> None:
    log = EventLog(store=MemoryStore())
    event = log.append_harness_event(
        HarnessEvent(
            type="harness.output.item.completed", invocationId="inv_1",
            sessionId="hsess_1", turnId="turn_1", author="codex",
            content={"role": "model", "parts": []},
            actions={"haas": {"itemId": "msg_1", "modelCallId": "mcall_0001",
                              "messagePhase": "final_answer"}},
        ),
        app_name="chrn_1", user_id="u_1", harness_id="chrn_1",
        adapter_id="codex-app-server",
    )
    assert event.type == "haas.output.item.completed"
    assert event.haas == {"itemId": "msg_1", "modelCallId": "mcall_0001",
                          "messagePhase": "final_answer"}
