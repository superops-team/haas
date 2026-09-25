"""Branch-coverage extras for the Codex notification normalizer.

Targets the remaining notification-method branches and pure helpers that the
golden-event suite does not exercise directly. Offline, no harness required.
"""

from __future__ import annotations

from haas.harnesses.codex_app_server.normalizer import (
    _activity_kind_for_tool_name,
    _item_tool_name,
    _normalize_usage,
    _safe_tool_summary,
    _tool_name,
    _tool_name_for_method,
    command_text,
    normalize_notification,
    notification_method,
    reset_unparsed_notification_counts,
    unparsed_notification_counts,
)

CTX = {
    "invocation_id": "inv_1",
    "session_id": "hsess_1",
    "turn_id": "turn_1",
    "author": "codex",
}


def _notification(method: str, params: dict) -> dict:
    return {"jsonrpc": "2.0", "method": method, "params": params}


# --- command_text shapes ---------------------------------------------------


def test_command_text_unclosed_quote_returns_raw_string() -> None:
    # shlex.split raises ValueError on unbalanced quoting -> raw text passthrough.
    assert command_text("echo 'unclosed quote") == "echo 'unclosed quote"


def test_command_text_non_string_list_parts_is_empty() -> None:
    assert command_text(["ls", 1, 2]) == ""  # type: ignore[list-item]


def test_command_text_non_shell_list_is_joined() -> None:
    assert command_text(["ls", "-la", "/tmp"]) == "ls -la /tmp"


def test_command_text_empty_list_is_empty() -> None:
    assert command_text([]) == ""


# --- notification_method ---------------------------------------------------


def test_notification_method_blank_when_neither_method_nor_type() -> None:
    assert notification_method({"jsonrpc": "2.0"}) == ""
    assert notification_method({"jsonrpc": "2.0", "params": "not-a-dict"}) == ""
    assert notification_method({"jsonrpc": "2.0", "params": {"type": ""}}) == ""


# --- reasoning delta empty -------------------------------------------------


def test_reasoning_delta_empty_text_is_dropped() -> None:
    event = normalize_notification(
        _notification("item/reasoning/summaryTextDelta", {"delta": ""}), **CTX
    )
    assert event is None


# --- item/started & item/completed guards ----------------------------------


def test_item_started_without_dict_item_is_dropped() -> None:
    assert (
        normalize_notification(_notification("item/started", {"item": "nope"}), **CTX) is None
    )


def test_item_completed_agent_message_requires_string_id() -> None:
    assert (
        normalize_notification(
            _notification(
                "item/completed",
                {"item": {"id": 123, "type": "agentMessage"}},
            ),
            **CTX,
        )
        is None
    )


def test_item_completed_unknown_item_type_is_dropped() -> None:
    assert (
        normalize_notification(
            _notification("item/completed", {"item": {"id": "x", "type": "weird"}}),
            **CTX,
        )
        is None
    )


# --- tool started variants -------------------------------------------------


def test_web_search_started_is_search_activity() -> None:
    event = normalize_notification(
        _notification(
            "item/started",
            {"item": {"id": "ws_1", "type": "webSearch", "status": "inProgress"}},
        ),
        **CTX,
    )
    assert event is not None
    assert event.type == "harness.tool.started"
    artifact = event.actions["artifactDelta"]
    assert artifact["toolName"] == "web_search"
    assert artifact["activityKind"] == "search"
    assert artifact["safeSummary"] == "Search the web"


def test_dynamic_tool_started_is_generic_tool() -> None:
    event = normalize_notification(
        _notification(
            "item/started",
            {"item": {"id": "dyn_1", "type": "dynamicToolCall", "tool": "calc"}},
        ),
        **CTX,
    )
    assert event is not None
    artifact = event.actions["artifactDelta"]
    assert artifact["toolName"] == "calc"
    assert artifact["activityKind"] == "tool"
    assert artifact["safeSummary"] == "Call tool"


def test_command_execution_search_action_is_search_activity() -> None:
    event = normalize_notification(
        _notification(
            "item/started",
            {
                "item": {
                    "id": "grep_1",
                    "type": "commandExecution",
                    "command": "grep -r x .",
                    "commandActions": [{"type": "search"}],
                }
            },
        ),
        **CTX,
    )
    assert event is not None
    artifact = event.actions["artifactDelta"]
    assert artifact["activityKind"] == "search"
    assert artifact["safeSummary"] == "Search the workspace"


# --- tool completed variants -----------------------------------------------


def test_web_search_completed() -> None:
    event = normalize_notification(
        _notification(
            "item/completed",
            {"item": {"id": "ws_1", "type": "webSearch", "status": "completed"}},
        ),
        **CTX,
    )
    assert event is not None
    assert event.type == "harness.tool.completed"
    artifact = event.actions["artifactDelta"]
    assert artifact["status"] == "completed"
    assert artifact["activityKind"] == "search"


def test_mcp_tool_completed_failed_maps_to_safe_reason() -> None:
    event = normalize_notification(
        _notification(
            "item/completed",
            {
                "item": {
                    "id": "mcp_1",
                    "type": "mcpToolCall",
                    "server": "cal",
                    "tool": "list",
                    "status": "failed",
                }
            },
        ),
        **CTX,
    )
    assert event is not None
    assert event.type == "harness.tool.failed"
    artifact = event.actions["artifactDelta"]
    assert artifact["safeReason"] == "MCP tool failed"


def test_dynamic_tool_completed_failed() -> None:
    event = normalize_notification(
        _notification(
            "item/completed",
            {"item": {"id": "dyn_1", "type": "dynamicToolCall", "status": "failed"}},
        ),
        **CTX,
    )
    assert event is not None
    assert event.type == "harness.tool.failed"
    assert event.actions["artifactDelta"]["safeReason"] == "Tool failed"


def test_command_completed_records_duration_and_output() -> None:
    event = normalize_notification(
        _notification(
            "item/completed",
            {
                "item": {
                    "id": "cc_1",
                    "type": "commandExecution",
                    "command": "true",
                    "status": "completed",
                    "exitCode": 0,
                    "durationMs": 12,
                    "aggregatedOutput": "done\n",
                }
            },
        ),
        **CTX,
    )
    assert event is not None
    assert event.type == "harness.tool.completed"
    artifact = event.actions["artifactDelta"]
    assert artifact["durationMs"] == 12
    assert artifact["exitCode"] == 0
    assert artifact["outputPreview"] == "done"


# --- tool output method name mapping ---------------------------------------


def test_tool_name_for_method_branches() -> None:
    assert _tool_name_for_method("item/fileChange/outputDelta") == "apply_patch"
    assert _tool_name_for_method("item/mcpToolCall/progress") == "mcp_tool"
    assert _tool_name_for_method("item/unknown/progress") == "unknown"


def test_item_tool_name_branches() -> None:
    assert _item_tool_name("dynamicToolCall", {"tool": "t"}) == "t"
    assert _item_tool_name("mcpToolCall", {}) == "mcp.tool"


def test_activity_kind_for_tool_name_branches() -> None:
    assert _activity_kind_for_tool_name("apply_patch") == "edit"
    assert _activity_kind_for_tool_name("web_search") == "search"
    assert _activity_kind_for_tool_name("exec_command") == "command"
    assert _activity_kind_for_tool_name("whatever") == "tool"


def test_safe_tool_summary_branches() -> None:
    assert _safe_tool_summary("webSearch", {}) == "Search the web"
    assert _safe_tool_summary("fileChange", {"changes": [1]}) == "Apply 1 file change"
    assert _safe_tool_summary("dynamicToolCall", {}) == "Call tool"


def test_tool_name_helper_fallback() -> None:
    assert _tool_name({"item": {"name": "the-tool"}}) == "the-tool"
    assert _tool_name({"item": {"toolName": "tn"}}) == "tn"
    assert _tool_name({"toolName": "top"}) == "top"
    assert _tool_name({"name": "top2"}) == "top2"
    assert _tool_name({}) == ""


# --- token usage shapes ------------------------------------------------------


def test_usage_without_model_call_or_cumulative() -> None:
    event = normalize_notification(
        _notification(
            "thread/tokenUsage/updated",
            {
                "tokenUsage": {
                    "inputTokens": 5,
                    "outputTokens": 2,
                    "totalTokens": 7,
                }
            },
        ),
        **CTX,
    )
    assert event is not None
    assert event.type == "harness.usage"
    assert "modelCallId" not in event.actions["haas"]
    assert "cumulativeUsage" not in event.actions["haas"]


def test_usage_token_usage_not_dict_is_none() -> None:
    event = normalize_notification(
        _notification("thread/tokenUsage/updated", {"tokenUsage": "nope"}), **CTX
    )
    assert event is None


def test_normalize_usage_legacy_flat() -> None:
    usage, cumulative = _normalize_usage(
        {"tokenUsage": {"inputTokens": 1, "outputTokens": 2, "totalTokens": 3}}
    )
    assert usage == {"inputTokens": 1, "outputTokens": 2, "totalTokens": 3}
    assert cumulative is None


def test_normalize_usage_non_dict() -> None:
    assert _normalize_usage({"tokenUsage": "x"}) == (None, None)


# --- plan update guards -----------------------------------------------------


def test_plan_update_non_list_is_dropped() -> None:
    assert (
        normalize_notification(
            _notification("turn/plan/updated", {"plan": "nope"}), **CTX
        )
        is None
    )


def test_plan_update_skips_invalid_status_items() -> None:
    event = normalize_notification(
        _notification(
            "turn/plan/updated",
            {
                "plan": [
                    "not-a-dict",
                    {"status": "bogus"},
                    {"status": "inProgress"},
                ]
            },
        ),
        **CTX,
    )
    assert event is not None
    assert event.actions["haas"]["counts"] == {
        "pending": 0,
        "inProgress": 1,
        "completed": 0,
    }
    assert event.actions["haas"]["total"] == 3


# --- unparsed counter reset hygiene ----------------------------------------


def test_unknown_notification_counter_increments() -> None:
    reset_unparsed_notification_counts()
    normalize_notification(_notification("brand/new/method", {}), **CTX)
    assert unparsed_notification_counts().get("brand/new/method") == 1
    reset_unparsed_notification_counts()
