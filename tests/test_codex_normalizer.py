"""Golden-event tests for Codex notification -> HarnessEvent normalization."""

from __future__ import annotations

from haas.harnesses.codex_app_server.normalizer import (
    command_text,
    normalize_notification,
    notification_method,
    notification_params,
)

CTX = {
    "invocation_id": "inv_1",
    "session_id": "hsess_1",
    "turn_id": "turn_1",
    "author": "codex",
}


def _notification(method: str, params: dict) -> dict:
    return {"jsonrpc": "2.0", "method": method, "params": params}


def test_notification_method_extraction() -> None:
    assert (
        notification_method(_notification("item/agentMessage/delta", {}))
        == "item/agentMessage/delta"
    )


def test_notification_method_falls_back_to_params_type() -> None:
    assert (
        notification_method({"jsonrpc": "2.0", "params": {"type": "turn/completed"}})
        == "turn/completed"
    )


def test_notification_params() -> None:
    assert notification_params(_notification("x", {"a": 1})) == {"a": 1}


def test_agent_message_delta() -> None:
    event = normalize_notification(
        _notification(
            "item/agentMessage/delta",
            {"delta": "hello", "itemId": "msg_1"},
        ),
        model_call_id="mcall_0001",
        **CTX,
    )
    assert event is not None
    assert event.type == "harness.text.delta"
    assert event.nativeType == "item/agentMessage/delta"
    assert event.content == {"role": "model", "parts": [{"text": "hello"}]}
    assert event.actions["haas"] == {
        "itemId": "msg_1",
        "modelCallId": "mcall_0001",
    }


def test_reasoning_summary_delta_is_visible_but_raw_reasoning_is_private() -> None:
    event = normalize_notification(
        _notification(
            "item/reasoning/summaryTextDelta",
            {"delta": "Checking tests", "itemId": "reason_1", "summaryIndex": 2},
        ),
        model_call_id="mcall_0001",
        **CTX,
    )
    assert event is not None
    assert event.type == "harness.reasoning.delta"
    assert event.content["parts"][0] == {"text": "Checking tests", "thought": True}
    assert event.actions["haas"] == {
        "itemId": "reason_1",
        "summaryIndex": 2,
        "modelCallId": "mcall_0001",
    }
    assert (
        normalize_notification(
            _notification("item/reasoning/textDelta", {"delta": "private reasoning"}),
            **CTX,
        )
        is None
    )


def test_reasoning_item_completion_preserves_item_identity_without_raw_content() -> None:
    event = normalize_notification(
        _notification(
            "item/completed",
            {
                "item": {
                    "id": "reason_1",
                    "type": "reasoning",
                    "summary": [{"type": "summary_text", "text": "private"}],
                }
            },
        ),
        model_call_id="mcall_0001",
        **CTX,
    )

    assert event is not None
    assert event.type == "harness.output.item.completed"
    assert event.content == {"role": "model", "parts": []}
    assert event.actions["haas"] == {
        "itemId": "reason_1",
        "modelCallId": "mcall_0001",
    }
    assert "private" not in str(event)


def test_command_lifecycle_has_stable_safe_metadata() -> None:
    started = normalize_notification(
        _notification(
            "item/started",
            {
                "item": {
                    "id": "call_1",
                    "type": "commandExecution",
                    "command": "cat /Users/example/private.txt",
                    "cwd": "/workspace/project",
                    "commandActions": [
                        {
                            "type": "read",
                            "command": "cat /Users/example/private.txt",
                            "name": "cat",
                            "path": "/Users/example/private.txt",
                        }
                    ],
                    "status": "inProgress",
                }
            },
        ),
        **CTX,
    )
    assert started is not None
    assert started.type == "harness.tool.started"
    assert started.actions["artifactDelta"] == {
        "toolCallId": "call_1",
        "toolName": "exec_command",
        "safeSummary": "Read a file",
        "activityKind": "read",
        "commandPreview": "cat [REDACTED_PATH]",
        "workingDirectory": "/workspace/project",
    }

    completed = normalize_notification(
        _notification(
            "item/completed",
            {
                "item": {
                    "id": "call_1",
                    "type": "commandExecution",
                    "command": "cat /Users/example/private.txt",
                    "cwd": "/workspace/project",
                    "status": "failed",
                    "exitCode": 1,
                    "durationMs": 240,
                    "aggregatedOutput": "private output",
                }
            },
        ),
        **CTX,
    )
    assert completed is not None
    assert completed.type == "harness.tool.failed"
    assert completed.actions["artifactDelta"] == {
        "toolCallId": "call_1",
        "toolName": "exec_command",
        "status": "failed",
        "code": "tool_failed",
        "safeReason": "Command failed",
        "retryable": False,
        "activityKind": "command",
        "durationMs": 240,
        "exitCode": 1,
        "outputPreview": "private output",
        "omittedLineCount": 0,
        "commandPreview": "cat [REDACTED_PATH]",
        "workingDirectory": "/workspace/project",
    }


def test_command_array_uses_shell_payload_as_safe_preview() -> None:
    event = normalize_notification(
        _notification(
            "item/started",
            {
                "item": {
                    "id": "call_array",
                    "type": "commandExecution",
                    "command": [
                        "/bin/zsh",
                        "-lc",
                        "cat specs/event-log-sse/README.md",
                    ],
                    "status": "inProgress",
                }
            },
        ),
        **CTX,
    )

    assert event is not None
    assert event.actions["artifactDelta"]["commandPreview"] == ("cat specs/event-log-sse/README.md")


def test_serialized_shell_command_uses_payload_as_safe_preview() -> None:
    wrapped = '/bin/zsh -lc "python3 -c \\\'print(\\"x\\" * 100000)\\\'"'

    assert command_text(wrapped) == "python3 -c 'print(\"x\" * 100000)'"


def test_command_event_carries_only_an_opaque_evidence_locator() -> None:
    signed = "https://auth.example.com/oauth/authorize?code=keep&exp=1789264000"
    event = normalize_notification(
        _notification(
            "item/started",
            {
                "item": {
                    "id": "call_auth",
                    "type": "commandExecution",
                    "command": f"open '{signed}'",
                    "cwd": "/workspace",
                }
            },
        ),
        **CTX,
        evidence_ref="evd_123",
        evidence_expires_at_ms=1789264000000,
    )
    assert event is not None
    artifact = event.actions["artifactDelta"]
    assert artifact["evidenceRef"] == "evd_123"
    assert artifact["evidenceExpiresAtMs"] == 1789264000000
    assert signed not in str(artifact)


def test_tool_output_delta() -> None:
    event = normalize_notification(
        _notification(
            "item/commandExecution/outputDelta",
            {"delta": "stdout", "itemId": "call_1"},
        ),
        **CTX,
    )
    assert event is not None
    assert event.type == "harness.tool.output"
    assert event.actions["artifactDelta"] == {
        "toolCallId": "call_1",
        "toolName": "exec_command",
        "activityKind": "command",
        "safeSummary": "Command output",
        "outputPreview": "stdout",
        "omittedLineCount": 0,
    }
    assert (
        normalize_notification(
            _notification(
                "item/commandExecution/outputDelta",
                {"delta": "", "itemId": "call_1"},
            ),
            **CTX,
        )
        is None
    )


def test_nonzero_command_exit_is_a_failed_tool_even_when_status_says_completed() -> None:
    event = normalize_notification(
        _notification(
            "item/completed",
            {
                "item": {
                    "id": "call_nonzero",
                    "type": "commandExecution",
                    "command": "false",
                    "status": "completed",
                    "exitCode": 7,
                }
            },
        ),
        **CTX,
    )

    assert event is not None
    assert event.type == "harness.tool.failed"
    assert event.actions["artifactDelta"]["exitCode"] == 7


def test_file_change_and_mcp_have_presentation_neutral_activity_kinds() -> None:
    changed = normalize_notification(
        _notification(
            "item/started",
            {
                "item": {
                    "id": "call_edit",
                    "type": "fileChange",
                    "changes": [{"path": "/private/repo/a.py", "kind": "update"}],
                    "status": "inProgress",
                }
            },
        ),
        **CTX,
    )
    mcp = normalize_notification(
        _notification(
            "item/started",
            {
                "item": {
                    "id": "call_mcp",
                    "type": "mcpToolCall",
                    "server": "calendar",
                    "tool": "list_events",
                    "status": "inProgress",
                }
            },
        ),
        **CTX,
    )

    assert changed is not None and mcp is not None
    assert changed.actions["artifactDelta"] == {
        "toolCallId": "call_edit",
        "toolName": "apply_patch",
        "safeSummary": "Apply 1 file change",
        "activityKind": "edit",
    }
    assert mcp.actions["artifactDelta"]["activityKind"] == "tool"
    assert "calendar" not in mcp.actions["artifactDelta"]["safeSummary"]


def test_agent_message_completion_supplies_authoritative_phase_without_text() -> None:
    event = normalize_notification(
        _notification(
            "item/completed",
            {
                "item": {
                    "id": "msg_1",
                    "type": "agentMessage",
                    "text": "must not be repeated",
                    "phase": "commentary",
                }
            },
        ),
        model_call_id="mcall_0001",
        **CTX,
    )

    assert event is not None
    assert event.type == "harness.output.item.completed"
    assert event.content == {"role": "model", "parts": []}
    assert event.actions["haas"] == {
        "itemId": "msg_1",
        "modelCallId": "mcall_0001",
        "messagePhase": "commentary",
    }
    assert "must not be repeated" not in str(event)


def test_token_usage_uses_native_last_and_preserves_total_snapshot() -> None:
    event = normalize_notification(
        _notification(
            "thread/tokenUsage/updated",
            {
                "tokenUsage": {
                    "last": {
                        "inputTokens": 100,
                        "cachedInputTokens": 40,
                        "cacheWriteInputTokens": 3,
                        "outputTokens": 25,
                        "reasoningOutputTokens": 10,
                        "totalTokens": 125,
                    },
                    "total": {
                        "inputTokens": 300,
                        "cachedInputTokens": 80,
                        "cacheWriteInputTokens": 3,
                        "outputTokens": 75,
                        "reasoningOutputTokens": 20,
                        "totalTokens": 375,
                    },
                    "modelContextWindow": 200000,
                }
            },
        ),
        model_call_id="mcall_0002",
        **CTX,
    )
    assert event is not None
    assert event.type == "harness.usage"
    assert event.usage == {
        "inputTokens": 100,
        "cacheReadTokens": 40,
        "cacheWriteTokens": 3,
        "outputTokens": 25,
        "reasoningOutputTokens": 10,
        "totalTokens": 125,
    }
    assert event.actions["haas"] == {
        "scope": "model_call",
        "modelCallId": "mcall_0002",
        "cumulativeUsage": {
            "inputTokens": 300,
            "cacheReadTokens": 80,
            "cacheWriteTokens": 3,
            "outputTokens": 75,
            "reasoningOutputTokens": 20,
            "totalTokens": 375,
        },
    }


def test_incomplete_token_usage_is_unreported_instead_of_zero_filled() -> None:
    event = normalize_notification(
        _notification(
            "thread/tokenUsage/updated",
            {"tokenUsage": {"last": {"inputTokens": 100}}},
        ),
        model_call_id="mcall_0001",
        **CTX,
    )

    assert event is None


def test_turn_started() -> None:
    event = normalize_notification(_notification("turn/started", {}), **CTX)
    assert event is not None
    assert event.type == "harness.turn.started"


def test_housekeeping_notification_is_dropped() -> None:
    assert normalize_notification(_notification("skills/changed", {}), **CTX) is None
    assert normalize_notification(_notification("project/changed", {}), **CTX) is None


def test_plan_update_keeps_only_structured_status_counts() -> None:
    event = normalize_notification(
        _notification(
            "turn/plan/updated",
            {
                "plan": [
                    {"step": "secret implementation detail", "status": "completed"},
                    {"step": "unfinished private task", "status": "pending"},
                ]
            },
        ),
        **CTX,
    )
    assert event is not None
    assert event.type == "harness.plan.updated"
    assert event.actions["haas"] == {
        "counts": {"pending": 1, "inProgress": 0, "completed": 1},
        "total": 2,
    }
    assert "secret implementation detail" not in str(event)


def test_empty_delta_is_dropped() -> None:
    assert (
        normalize_notification(_notification("item/agentMessage/delta", {"delta": ""}), **CTX)
        is None
    )


# --- P1-4: unknown notification observability ------------------------------

from haas.harnesses.codex_app_server import normalizer as _norm
from haas.harnesses.codex_app_server.normalizer import (
    normalize_notification,
    reset_unparsed_notification_counts,
    unparsed_notification_counts,
)


def test_unknown_notification_is_counted(caplog) -> None:
    reset_unparsed_notification_counts()
    with caplog.at_level("DEBUG", logger="haas.harness.codex.normalizer"):
        event = normalize_notification(
            _notification("some/future/newMethod", {"a": 1}),
            **CTX,
        )
    assert event is None
    counts = unparsed_notification_counts()
    assert counts.get("some/future/newMethod") == 1
    assert any(
        record.message == "adapter_event_unparsed" for record in caplog.records
    )
    reset_unparsed_notification_counts()


def test_known_notification_not_counted() -> None:
    reset_unparsed_notification_counts()
    normalize_notification(
        _notification("item/agentMessage/delta", {"delta": "hi"}),
        **CTX,
    )
    assert unparsed_notification_counts() == {}
