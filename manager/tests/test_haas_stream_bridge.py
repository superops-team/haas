from __future__ import annotations

import json

from coworker.haas.stream_bridge import BridgeAction, SessionKey, StreamBridgeState


def _bridge(*, session_id: str = "hsess_1") -> StreamBridgeState:
    return StreamBridgeState(
        endpoint_id="endpoint_local",
        session=SessionKey("chrn_codex", "user_1", session_id),
        invocation_id="inv_1",
    )


def test_native_terminal_waits_for_adk_text_and_terminal_projection() -> None:
    bridge = _bridge()

    assert (
        bridge.consume_native(event_id="evt_3", cursor="evt_3", event_type="invocation.completed")
        == []
    )
    assert not bridge.completed

    delta = bridge.consume_adk(event_id="evt_2", cursor="evt_2", text="hello")
    assert [item.kind for item in delta] == ["assistant_delta"]
    assert bridge.consume_adk(event_id="evt_2", cursor="evt_2", text="hello") == []

    completed = bridge.consume_adk(event_id="evt_3", cursor="evt_3", terminal=True)
    assert [item.kind for item in completed] == ["assistant_message", "turn_end"]
    assert completed[0].payload["text"] == "hello"
    assert completed[0].payload["activities"] == []
    assert completed[0].payload["taskOutcome"]["phase"] == "completed"
    assert completed[1].payload == {"status": "completed", "taskPhase": "completed"}
    assert bridge.completed
    assert bridge.consume_adk(event_id="evt_3", cursor="evt_3", terminal=True) == []


def test_projection_dedupe_is_scoped_and_cursors_are_independent() -> None:
    first = _bridge(session_id="hsess_1")
    second = _bridge(session_id="hsess_2")

    assert first.consume_adk(event_id="evt_1", cursor="adk_1", text="a")
    assert (
        first.consume_native(event_id="evt_1", cursor="native_1", event_type="haas.tool.started")
        == []
    )
    assert second.consume_adk(event_id="evt_1", cursor="adk_1", text="b")
    assert first.consume_session(
        event_id="evt_idle", cursor="session_1", event_type="configuration.applied"
    )

    assert first.adk_cursor == "adk_1"
    assert first.native_cursor == "native_1"
    assert first.session_cursor == "session_1"


def test_restart_round_trip_preserves_dedupe_and_completion_barrier() -> None:
    bridge = _bridge()
    bridge.consume_adk(event_id="evt_1", cursor="adk_1", text="partial")
    bridge.consume_native(
        event_id="evt_2",
        cursor="native_2",
        event_type="invocation.failed",
        code="model_failed",
        safe_reason="model unavailable",
        retryable=True,
    )

    restored = StreamBridgeState.from_dict(json.loads(json.dumps(bridge.to_dict())))
    assert restored.consume_adk(event_id="evt_1", cursor="adk_1", text="partial") == []
    actions = restored.consume_adk(event_id="evt_2", cursor="adk_2", terminal=True)

    assert actions[-1].payload == {
        "status": "failed",
        "code": "model_failed",
        "safeReason": "model unavailable",
        "taskPhase": "failed",
        "retryable": True,
    }
    assert StreamBridgeState.from_dict(restored.to_dict()).completed


def test_adk_terminal_before_native_terminal_closes_barrier_once() -> None:
    bridge = _bridge()
    projected = bridge.consume_adk(event_id="evt_2", cursor="adk_2", text="done", terminal=True)
    assert [(item.kind, item.payload) for item in projected] == [
        ("assistant_delta", {"text": "done"})
    ]

    actions = bridge.consume_native(
        event_id="evt_2", cursor="native_2", event_type="invocation.completed"
    )
    assert [item.kind for item in actions] == ["assistant_message", "turn_end"]


def test_authoritative_readback_closes_native_terminal_without_adk_terminal() -> None:
    bridge = _bridge()
    assert (
        bridge.consume_native(
            event_id="evt_failed",
            cursor="evt_failed",
            event_type="invocation.failed",
            code="provider_failed",
            safe_reason="Provider unavailable",
            retryable=False,
        )
        == []
    )

    assert bridge.reconcile_authoritative_terminal(status="running") == []
    assert (
        bridge.reconcile_authoritative_terminal(
            status="failed", terminal_event_id="evt_other"
        )
        == []
    )
    actions = bridge.reconcile_authoritative_terminal(status="failed")

    assert [item.kind for item in actions] == ["assistant_message", "turn_end"]
    assert actions[-1].payload["status"] == "failed"
    assert bridge.completed


def test_adk_process_projection_is_live_and_native_copy_is_deduplicated() -> None:
    bridge = _bridge()
    actions = bridge.consume_adk(
        event_id="evt_1",
        cursor="evt_1",
        reasoning="Checking tests",
        artifact={
            "toolCallId": "call_1",
            "toolName": "exec_command",
            "safeSummary": "Run command",
        },
    )
    assert [action.kind for action in actions] == [
        "reasoning_delta",
        "tool_proposed",
        "tool_started",
    ]
    assert (
        bridge.consume_native(
            event_id="evt_1",
            cursor="evt_1",
            event_type="haas.tool.started",
            payload={"toolCallId": "call_1"},
        )
        == []
    )
    assert bridge.reasoning_summary == "Checking tests"
    assert (
        bridge.consume_native(
            event_id="evt_1",
            cursor="evt_1",
            event_type="haas.output.reasoning.delta",
            content={"parts": [{"text": "Checking tests", "thought": True}]},
        )
        == []
    )
    assert bridge.reasoning_summary == "Checking tests"


def test_native_reasoning_projection_wins_once_when_adk_copy_arrives_later() -> None:
    bridge = _bridge()

    native = bridge.consume_native(
        event_id="evt_reasoning",
        cursor="native_reasoning",
        event_type="haas.output.reasoning.delta",
        content={"parts": [{"text": "Checking tests", "thought": True}]},
    )
    adk = bridge.consume_adk(
        event_id="evt_reasoning",
        cursor="adk_reasoning",
        reasoning="Checking tests",
    )

    assert native == [BridgeAction("reasoning_delta", {"text": "Checking tests"})]
    assert adk == []
    assert bridge.reasoning_summary == "Checking tests"


def test_distinct_reasoning_events_preserve_legitimate_repeated_text() -> None:
    bridge = _bridge()

    first = bridge.consume_adk(event_id="evt_reasoning_1", cursor="adk_1", reasoning="The")
    second = bridge.consume_native(
        event_id="evt_reasoning_2",
        cursor="native_2",
        event_type="haas.output.reasoning.delta",
        content={"parts": [{"text": "The", "thought": True}]},
    )

    assert [action.kind for action in first + second] == [
        "reasoning_delta",
        "reasoning_delta",
    ]
    assert bridge.reasoning_summary == "TheThe"


def test_tool_output_is_deduplicated_across_adk_and_native_projection() -> None:
    bridge = _bridge()
    first = bridge.consume_adk(
        event_id="evt_output",
        cursor="evt_output",
        artifact={"toolCallId": "call_1", "outputPreview": "done"},
    )
    duplicate = bridge.consume_native(
        event_id="evt_output",
        cursor="evt_output",
        event_type="haas.tool.output",
        payload={"toolCallId": "call_1", "outputPreview": "done"},
    )
    assert [action.kind for action in first] == ["tool_output_delta"]
    assert duplicate == []


def test_native_process_events_map_to_manager_timeline_actions() -> None:
    bridge = _bridge()

    reasoning = bridge.consume_native(
        event_id="evt_1",
        cursor="evt_1",
        event_type="haas.output.reasoning.delta",
        content={"role": "model", "parts": [{"text": "Checking tests", "thought": True}]},
    )
    started = bridge.consume_native(
        event_id="evt_2",
        cursor="evt_2",
        event_type="haas.tool.started",
        payload={"toolCallId": "call_1", "toolName": "exec_command", "safeSummary": "Run command"},
    )
    output = bridge.consume_native(
        event_id="evt_3",
        cursor="evt_3",
        event_type="haas.tool.output",
        payload={"toolCallId": "call_1", "toolName": "exec_command", "safeSummary": "3 lines"},
    )
    completed = bridge.consume_native(
        event_id="evt_4",
        cursor="evt_4",
        event_type="haas.tool.completed",
        payload={"toolCallId": "call_1", "toolName": "exec_command", "status": "completed"},
    )

    assert reasoning == [BridgeAction("reasoning_delta", {"text": "Checking tests"})]
    assert [action.kind for action in started] == ["tool_proposed", "tool_started"]
    assert output == [
        BridgeAction(
            "tool_output_delta",
            {"toolCallId": "call_1", "toolName": "exec_command", "safeSummary": "3 lines"},
        )
    ]
    assert completed == [
        BridgeAction(
            "tool_finished",
            {
                "toolCallId": "call_1",
                "toolName": "exec_command",
                "status": "completed",
                "invocationId": "inv_1",
            },
        )
    ]


def test_command_lifecycle_preserves_action_and_evidence_fields() -> None:
    bridge = _bridge()
    bridge.consume_native(
        event_id="evt_start",
        cursor="evt_start",
        event_type="haas.tool.started",
        payload={
            "toolCallId": "call_1",
            "toolName": "exec_command",
            "activityKind": "command",
            "safeSummary": "Run tests",
            "commandPreview": "pytest -q",
            "workingDirectory": "/workspace",
            "evidenceRef": "evd_1",
            "evidenceExpiresAtMs": 1789264000000,
        },
    )
    bridge.consume_native(
        event_id="evt_done",
        cursor="evt_done",
        event_type="haas.tool.failed",
        payload={
            "toolCallId": "call_1",
            "toolName": "exec_command",
            "status": "failed",
            "safeReason": "Command failed",
            "exitCode": 2,
        },
    )

    activity = bridge.activities["call_1"]
    assert activity["commandPreview"] == "pytest -q"
    assert activity["workingDirectory"] == "/workspace"
    assert activity["evidenceRef"] == "evd_1"
    assert activity["evidenceExpiresAtMs"] == 1789264000000
    assert activity["invocationId"] == "inv_1"


def test_tool_lifecycle_deduplicates_by_tool_call_across_projections() -> None:
    bridge = _bridge()
    proposed = bridge.consume_adk(
        event_id="evt_adk_1",
        cursor="evt_adk_1",
        artifact={
            "toolCallId": "call_1",
            "toolName": "exec_command",
            "safeSummary": "Run command",
        },
    )
    duplicate_start = bridge.consume_native(
        event_id="evt_native_1",
        cursor="evt_native_1",
        event_type="haas.tool.started",
        payload={"toolCallId": "call_1", "toolName": "exec_command"},
    )
    output_one = bridge.consume_native(
        event_id="evt_native_2",
        cursor="evt_native_2",
        event_type="haas.tool.output.delta",
        payload={"toolCallId": "call_1", "outputPreview": "first"},
    )
    output_two = bridge.consume_native(
        event_id="evt_native_3",
        cursor="evt_native_3",
        event_type="haas.tool.output.delta",
        payload={"toolCallId": "call_1", "outputPreview": "second"},
    )

    assert [action.kind for action in proposed] == ["tool_proposed", "tool_started"]
    assert duplicate_start == []
    assert [action.kind for action in output_one + output_two] == [
        "tool_output_delta",
        "tool_output_delta",
    ]


def test_correlated_native_facts_build_ordered_model_call_stages() -> None:
    bridge = _bridge()

    bridge.consume_native(
        event_id="evt_1",
        cursor="evt_1",
        event_type="haas.output.text.delta",
        payload={"itemId": "msg_1", "modelCallId": "mcall_0001"},
        content={"parts": [{"text": "Checking the bridge"}]},
    )
    bridge.consume_native(
        event_id="evt_2",
        cursor="evt_2",
        event_type="haas.output.reasoning.delta",
        payload={"itemId": "reason_1", "summaryIndex": 0, "modelCallId": "mcall_0001"},
        content={"parts": [{"text": "The event has correlation fields", "thought": True}]},
    )
    bridge.consume_native(
        event_id="evt_3",
        cursor="evt_3",
        event_type="haas.output.item.completed",
        payload={"itemId": "msg_1", "modelCallId": "mcall_0001", "messagePhase": "commentary"},
    )
    usage_actions = bridge.consume_native(
        event_id="evt_4",
        cursor="evt_4",
        event_type="haas.usage.updated",
        payload={
            "scope": "model_call",
            "modelCallId": "mcall_0001",
            "usage": {
                "inputTokens": 100,
                "outputTokens": 25,
                "reasoningOutputTokens": 10,
                "cacheReadTokens": 40,
                "totalTokens": 125,
            },
        },
    )

    assert usage_actions[-1].kind == "model_stage_updated"
    assert bridge.model_stages == [
        {
            "modelCallId": "mcall_0001",
            "status": "running",
            "steps": [
                {"stepId": "msg_1", "kind": "commentary", "text": "Checking the bridge"},
                {
                    "stepId": "reason_1:0",
                    "kind": "reasoning_summary",
                    "text": "The event has correlation fields",
                    "previewText": "The event has correlation fields",
                    "previewFrozen": False,
                },
            ],
            "usage": {
                "inputTokens": 100,
                "outputTokens": 25,
                "reasoningOutputTokens": 10,
                "cacheReadTokens": 40,
                "totalTokens": 125,
            },
        }
    ]


def test_reasoning_preview_is_bounded_while_full_summary_keeps_streaming() -> None:
    bridge = _bridge()
    first = "a" * 200
    second = "b" * 80

    for index, text in enumerate((first, second), start=1):
        bridge.consume_native(
            event_id=f"evt_{index}",
            cursor=f"evt_{index}",
            event_type="haas.output.reasoning.delta",
            payload={"itemId": "reason_1", "summaryIndex": 0, "modelCallId": "mcall_0001"},
            content={"parts": [{"text": text, "thought": True}]},
        )

    step = bridge.model_stages[0]["steps"][0]
    assert step["text"] == first + second
    assert step["previewText"] == (first + second)[:240]
    assert step["previewFrozen"] is True


def test_later_step_freezes_reasoning_preview_but_not_full_summary() -> None:
    bridge = _bridge()
    reasoning_payload = {
        "itemId": "reason_1",
        "summaryIndex": 0,
        "modelCallId": "mcall_0001",
    }
    bridge.consume_native(
        event_id="evt_1",
        cursor="evt_1",
        event_type="haas.output.reasoning.delta",
        payload=reasoning_payload,
        content={"parts": [{"text": "Initial summary", "thought": True}]},
    )
    bridge.consume_native(
        event_id="evt_2",
        cursor="evt_2",
        event_type="haas.tool.started",
        payload={"toolCallId": "call_1", "toolName": "exec_command", "modelCallId": "mcall_0001"},
    )
    bridge.consume_native(
        event_id="evt_3",
        cursor="evt_3",
        event_type="haas.output.reasoning.delta",
        payload=reasoning_payload,
        content={"parts": [{"text": " with late detail", "thought": True}]},
    )

    step = bridge.model_stages[0]["steps"][0]
    assert step["previewText"] == "Initial summary"
    assert step["previewFrozen"] is True
    assert step["text"] == "Initial summary with late detail"

    restored = StreamBridgeState.from_dict(json.loads(json.dumps(bridge.to_dict())))
    restored.consume_native(
        event_id="evt_4",
        cursor="evt_4",
        event_type="haas.output.reasoning.delta",
        payload=reasoning_payload,
        content={"parts": [{"text": " after restart", "thought": True}]},
    )
    restored_step = restored.model_stages[0]["steps"][0]
    assert restored_step["previewText"] == "Initial summary"
    assert restored_step["previewFrozen"] is True
    assert restored_step["text"].endswith(" after restart")


def test_reasoning_item_completion_freezes_all_summary_parts() -> None:
    bridge = _bridge()
    for summary_index in (0, 1):
        bridge.consume_native(
            event_id=f"evt_{summary_index}",
            cursor=f"evt_{summary_index}",
            event_type="haas.output.reasoning.delta",
            payload={
                "itemId": "reason_1",
                "summaryIndex": summary_index,
                "modelCallId": "mcall_0001",
            },
            content={"parts": [{"text": f"Part {summary_index}", "thought": True}]},
        )
    actions = bridge.consume_native(
        event_id="evt_done",
        cursor="evt_done",
        event_type="haas.output.item.completed",
        payload={"itemId": "reason_1", "modelCallId": "mcall_0001"},
    )

    assert actions[-1].kind == "model_stage_updated"
    assert all(step["previewFrozen"] is True for step in bridge.model_stages[0]["steps"])


def test_unknown_reasoning_item_completion_does_not_create_an_empty_stage() -> None:
    bridge = _bridge()

    assert (
        bridge.consume_native(
            event_id="evt_done",
            cursor="evt_done",
            event_type="haas.output.item.completed",
            payload={"itemId": "reason_missing", "modelCallId": "mcall_0001"},
        )
        == []
    )
    assert bridge.model_stages == []


def test_reasoning_completion_matches_an_item_id_that_contains_a_colon() -> None:
    bridge = _bridge()
    bridge.consume_native(
        event_id="evt_1",
        cursor="evt_1",
        event_type="haas.output.reasoning.delta",
        payload={"itemId": "reason:one", "summaryIndex": 0, "modelCallId": "mcall_0001"},
        content={"parts": [{"text": "Summary", "thought": True}]},
    )

    bridge.consume_native(
        event_id="evt_done",
        cursor="evt_done",
        event_type="haas.output.item.completed",
        payload={"itemId": "reason:one", "modelCallId": "mcall_0001"},
    )

    assert bridge.model_stages[0]["steps"][0]["previewFrozen"] is True


def test_usage_does_not_detach_a_later_tool_from_its_model_call_stage() -> None:
    bridge = _bridge()
    bridge.consume_native(
        event_id="evt_1",
        cursor="evt_1",
        event_type="haas.usage.updated",
        payload={
            "scope": "model_call",
            "modelCallId": "mcall_0001",
            "usage": {"inputTokens": 10, "outputTokens": 5, "totalTokens": 15},
        },
    )
    bridge.consume_native(
        event_id="evt_2",
        cursor="evt_2",
        event_type="haas.tool.started",
        payload={
            "toolCallId": "call_1",
            "toolName": "exec_command",
            "safeSummary": "Run tests",
            "modelCallId": "mcall_0001",
        },
    )

    assert len(bridge.model_stages) == 1
    assert bridge.model_stages[0]["status"] == "running"
    assert bridge.model_stages[0]["steps"] == [
        {"stepId": "call_1", "kind": "tool", "activityId": "call_1"}
    ]

    bridge.consume_native(
        event_id="evt_3",
        cursor="evt_3",
        event_type="haas.tool.completed",
        payload={
            "toolCallId": "call_1",
            "toolName": "exec_command",
            "status": "completed",
            "modelCallId": "mcall_0001",
        },
    )
    assert bridge.model_stages[0]["status"] == "completed"


def test_usage_completes_a_stage_when_its_tools_are_already_terminal() -> None:
    bridge = _bridge()
    bridge.consume_native(
        event_id="evt_1",
        cursor="evt_1",
        event_type="haas.tool.started",
        payload={"toolCallId": "call_1", "modelCallId": "mcall_0001"},
    )
    bridge.consume_native(
        event_id="evt_2",
        cursor="evt_2",
        event_type="haas.tool.completed",
        payload={"toolCallId": "call_1", "modelCallId": "mcall_0001", "status": "completed"},
    )

    bridge.consume_native(
        event_id="evt_3",
        cursor="evt_3",
        event_type="haas.usage.updated",
        payload={
            "scope": "model_call",
            "modelCallId": "mcall_0001",
            "usage": {"inputTokens": 10, "outputTokens": 5, "totalTokens": 15},
        },
    )

    assert bridge.model_stages[0]["status"] == "completed"


def test_failed_terminal_only_marks_the_running_stage_failed() -> None:
    bridge = _bridge()
    bridge.consume_native(
        event_id="evt_1",
        cursor="evt_1",
        event_type="haas.output.text.delta",
        payload={"itemId": "msg_1", "modelCallId": "mcall_0001"},
        content={"parts": [{"text": "First stage"}]},
    )
    bridge.consume_native(
        event_id="evt_2",
        cursor="evt_2",
        event_type="haas.output.text.delta",
        payload={"itemId": "msg_2", "modelCallId": "mcall_0002"},
        content={"parts": [{"text": "Second stage"}]},
    )
    bridge.consume_native(
        event_id="evt_3",
        cursor="evt_3",
        event_type="invocation.failed",
        code="model_failed",
        safe_reason="Provider unavailable",
        retryable=True,
    )
    bridge.consume_adk(event_id="evt_3", cursor="evt_3", terminal=True)

    assert [stage["status"] for stage in bridge.model_stages] == ["completed", "failed"]
    assert bridge.model_stages[-1]["steps"][0]["kind"] == "output_pending"


def test_successful_terminal_reclassifies_unphased_final_output_as_result() -> None:
    bridge = _bridge()
    bridge.consume_native(
        event_id="evt_1",
        cursor="evt_1",
        event_type="haas.output.text.delta",
        payload={"itemId": "msg_1", "modelCallId": "mcall_0001"},
        content={"parts": [{"text": "Completed work"}]},
    )
    bridge.consume_native(
        event_id="evt_2",
        cursor="evt_2",
        event_type="invocation.completed",
    )

    actions = bridge.consume_adk(event_id="evt_2", cursor="evt_2", terminal=True)

    message = next(action for action in actions if action.kind == "assistant_message")
    assert message.payload["modelStages"][0]["status"] == "completed"
    assert message.payload["modelStages"][0]["steps"] == [
        {"stepId": "msg_1", "kind": "result", "text": "Completed work"}
    ]
    assert bridge.model_stages[0]["steps"][0]["kind"] == "result"


def test_public_stages_are_detached_snapshots() -> None:
    bridge = _bridge()
    bridge.consume_native(
        event_id="evt_1",
        cursor="evt_1",
        event_type="haas.output.text.delta",
        payload={"itemId": "msg_1", "modelCallId": "mcall_0001"},
        content={"parts": [{"text": "First"}]},
    )
    snapshot = bridge.public_model_stages()
    bridge.consume_native(
        event_id="evt_2",
        cursor="evt_2",
        event_type="haas.output.reasoning.delta",
        payload={"itemId": "reason_1", "summaryIndex": 0, "modelCallId": "mcall_0001"},
        content={"parts": [{"text": "Later", "thought": True}]},
    )

    assert len(snapshot[0]["steps"]) == 1


def test_dedup_sets_are_truncated_after_invocation_completion() -> None:
    bridge = _bridge()
    bridge.consume_adk(event_id="evt_1", cursor="adk_1", text="partial")
    bridge.consume_native(
        event_id="evt_tool",
        cursor="native_tool",
        event_type="haas.tool.started",
        payload={"toolCallId": "call_1", "safeSummary": "run"},
    )
    assert bridge._seen
    assert bridge._tool_seen
    assert bridge._content_seen

    bridge.consume_native(event_id="evt_2", cursor="native_2", event_type="invocation.completed")
    actions = bridge.consume_adk(event_id="evt_2", cursor="adk_2", terminal=True)

    assert actions[-1].kind == "turn_end"
    assert bridge.completed
    # After the invocation closes, per-invocation dedup state is truncated so it
    # cannot grow unbounded across a long-lived bridge (P1-2 S1-012).
    assert bridge._seen == set()
    assert bridge._tool_seen == set()
    assert bridge._content_seen == set()


def test_model_call_stages_survive_restart_and_terminal_message() -> None:
    bridge = _bridge()
    bridge.consume_native(
        event_id="evt_1",
        cursor="evt_1",
        event_type="haas.output.reasoning.delta",
        payload={"itemId": "reason_1", "summaryIndex": 0, "modelCallId": "mcall_0001"},
        content={"parts": [{"text": "Check the failure", "thought": True}]},
    )
    restored = StreamBridgeState.from_dict(json.loads(json.dumps(bridge.to_dict())))
    restored.consume_native(
        event_id="evt_2",
        cursor="evt_2",
        event_type="invocation.failed",
        code="tool_failed",
        safe_reason="Command failed",
        retryable=False,
    )
    actions = restored.consume_adk(event_id="evt_2", cursor="evt_2", terminal=True)

    message = next(action for action in actions if action.kind == "assistant_message")
    assert message.payload["modelStages"][0]["status"] == "failed"
    assert message.payload["modelStages"][0]["steps"][0]["kind"] == "reasoning_summary"
