"""Codex notification -> canonical HarnessEvent normalization.

Implements specs/harness-adapter/README.md §6.3: native Codex JSON-RPC
notifications are mapped to HaaS canonical events and projected to ADK
``Event`` by the Event Log. Native method names are preserved only in
``nativeType``; upstream payloads are synthesized here (adapter isolation).
"""

from __future__ import annotations

import shlex
from typing import Any

from haas.harnesses.base import HarnessEvent
from haas.security.redact import bounded_redacted_preview

JsonObject = dict[str, Any]

# Codex v2 notification methods that carry visible delta content.
_TEXT_DELTA_METHODS = {"item/agentMessage/delta"}
_REASONING_DELTA_METHODS = {"item/reasoning/summaryTextDelta"}
_TOOL_OUTPUT_METHODS = {
    "item/commandExecution/outputDelta",
    "item/fileChange/outputDelta",
    "item/mcpToolCall/progress",
}
_TOOL_TYPES = {
    "commandExecution": "exec_command",
    "fileChange": "apply_patch",
    "mcpToolCall": "mcp_tool",
    "dynamicToolCall": "dynamic_tool",
    "webSearch": "web_search",
}
_USAGE_METHOD = "thread/tokenUsage/updated"
_TURN_STARTED_METHOD = "turn/started"


def command_text(command: Any) -> str:
    """Normalize Codex string/argv command shapes without exposing native structure."""
    if isinstance(command, str):
        try:
            serialized = shlex.split(command)
        except ValueError:
            return command
        if (
            len(serialized) == 3
            and serialized[0].rsplit("/", 1)[-1] in {"sh", "bash", "zsh"}
            and serialized[1] in {"-c", "-lc", "-cl"}
        ):
            return serialized[2].replace("\\'", "'").replace('\\"', '"')
        return command
    if (
        not isinstance(command, list)
        or not command
        or not all(isinstance(part, str) for part in command)
    ):
        return ""
    executable = command[0].rsplit("/", 1)[-1]
    if (
        executable in {"sh", "bash", "zsh"}
        and len(command) >= 3
        and command[1]
        in {
            "-c",
            "-lc",
            "-cl",
        }
    ):
        return str(command[2])
    return shlex.join(command)


def _command_preview(command: Any) -> str:
    preview, _ = bounded_redacted_preview(command_text(command), max_lines=1, max_bytes=512)
    return preview.replace("\n", " ")


def _working_directory_hint(cwd: str) -> str:
    if cwd == "/workspace" or cwd.startswith("/workspace/"):
        return cwd
    preview, _ = bounded_redacted_preview(cwd, max_lines=1, max_bytes=512)
    return preview


def notification_method(notification: JsonObject) -> str:
    """Return the JSON-RPC notification method name (e.g. ``item/agentMessage/delta``)."""
    method = notification.get("method")
    if isinstance(method, str) and method:
        return method
    # Some transports surface the event type in params instead of method.
    params = notification.get("params")
    if isinstance(params, dict):
        value = params.get("type")
        if isinstance(value, str) and value:
            return value
    return ""


def notification_params(notification: JsonObject) -> JsonObject:
    params = notification.get("params")
    return params if isinstance(params, dict) else {}


def _delta_text(params: JsonObject) -> str:
    value = params.get("delta")
    return value if isinstance(value, str) else ""


def normalize_notification(
    notification: JsonObject,
    *,
    invocation_id: str,
    session_id: str,
    turn_id: str,
    author: str,
    model_call_id: str | None = None,
    evidence_ref: str | None = None,
    evidence_expires_at_ms: int | None = None,
) -> HarnessEvent | None:
    """Normalize one Codex notification into a canonical HarnessEvent.

    Returns ``None`` for notifications that carry no upstream-visible content
    (housekeeping, project/settings changes, etc.).
    """
    method = notification_method(notification)
    params = notification_params(notification)

    if method in _TEXT_DELTA_METHODS:
        text = _delta_text(params)
        if not text:
            return None
        output_metadata = _output_metadata(params, model_call_id=model_call_id)
        return HarnessEvent(
            type="harness.text.delta",
            nativeType=method,
            invocationId=invocation_id,
            sessionId=session_id,
            turnId=turn_id,
            author=author,
            content={"role": "model", "parts": [{"text": text}]},
            actions={"stateDelta": {"status": "running"}, "haas": output_metadata},
        )

    if method in _REASONING_DELTA_METHODS:
        text = _delta_text(params)
        if not text:
            return None
        reasoning_metadata = _output_metadata(
            params, model_call_id=model_call_id, include_summary_index=True
        )
        return HarnessEvent(
            type="harness.reasoning.delta",
            nativeType=method,
            invocationId=invocation_id,
            sessionId=session_id,
            turnId=turn_id,
            author=author,
            content={"role": "model", "parts": [{"text": text, "thought": True}]},
            actions={
                "stateDelta": {"status": "running"},
                "haas": reasoning_metadata,
            },
        )

    if method in {"item/started", "item/completed"}:
        item = params.get("item")
        if not isinstance(item, dict):
            return None
        item_type = str(item.get("type") or "")
        if method == "item/completed" and item_type in {"agentMessage", "reasoning"}:
            item_id = item.get("id")
            if not isinstance(item_id, str) or not item_id:
                return None
            item_metadata: JsonObject = {"itemId": item_id}
            if model_call_id:
                item_metadata["modelCallId"] = model_call_id
            if item_type == "agentMessage":
                phase = item.get("phase")
                if phase in {"commentary", "final_answer"}:
                    item_metadata["messagePhase"] = phase
            return HarnessEvent(
                type="harness.output.item.completed",
                nativeType=method,
                invocationId=invocation_id,
                sessionId=session_id,
                turnId=turn_id,
                author=author,
                content={"role": "model", "parts": []},
                actions={"haas": item_metadata},
            )
        if item_type not in _TOOL_TYPES:
            return None
        tool_call_id = str(item.get("id") or "")
        tool_name = _item_tool_name(item_type, item)
        if method == "item/started":
            started_artifact: JsonObject = {
                "toolCallId": tool_call_id,
                "toolName": tool_name,
                "safeSummary": _safe_tool_summary(item_type, item),
                "activityKind": _activity_kind(item_type, item),
            }
            if model_call_id:
                started_artifact["modelCallId"] = model_call_id
            if item_type == "commandExecution":
                command = item.get("command")
                if command_text(command):
                    started_artifact["commandPreview"] = _command_preview(command)
                cwd = item.get("cwd")
                if isinstance(cwd, str) and cwd:
                    started_artifact["workingDirectory"] = _working_directory_hint(cwd)
                if evidence_ref:
                    started_artifact["evidenceRef"] = evidence_ref
                if evidence_expires_at_ms is not None:
                    started_artifact["evidenceExpiresAtMs"] = evidence_expires_at_ms
            return HarnessEvent(
                type="harness.tool.started",
                nativeType=method,
                invocationId=invocation_id,
                sessionId=session_id,
                turnId=turn_id,
                author=author,
                content={"role": "model", "parts": []},
                actions={"artifactDelta": started_artifact},
            )
        raw_status = str(
            item.get("status") or ("completed" if item_type == "webSearch" else "failed")
        )
        exit_code = item.get("exitCode")
        valid_exit_code = (
            exit_code if isinstance(exit_code, int) and not isinstance(exit_code, bool) else None
        )
        failed = raw_status not in {"completed", "success"} or (
            valid_exit_code is not None and valid_exit_code != 0
        )
        completed_artifact: JsonObject = {
            "toolCallId": tool_call_id,
            "toolName": tool_name,
            "status": "failed" if failed else "completed",
            "activityKind": _activity_kind(item_type, item),
        }
        if model_call_id:
            completed_artifact["modelCallId"] = model_call_id
        if item_type == "commandExecution":
            command = item.get("command")
            if command_text(command):
                completed_artifact["commandPreview"] = _command_preview(command)
            cwd = item.get("cwd")
            if isinstance(cwd, str) and cwd:
                completed_artifact["workingDirectory"] = _working_directory_hint(cwd)
            if evidence_ref:
                completed_artifact["evidenceRef"] = evidence_ref
            if evidence_expires_at_ms is not None:
                completed_artifact["evidenceExpiresAtMs"] = evidence_expires_at_ms
        duration_ms = item.get("durationMs")
        if isinstance(duration_ms, int) and not isinstance(duration_ms, bool):
            completed_artifact["durationMs"] = duration_ms
        if valid_exit_code is not None:
            completed_artifact["exitCode"] = valid_exit_code
        output = item.get("aggregatedOutput")
        if isinstance(output, str) and output:
            preview, omitted = bounded_redacted_preview(output)
            completed_artifact["outputPreview"] = preview
            completed_artifact["omittedLineCount"] = omitted
        if failed:
            completed_artifact.update(
                code="tool_failed",
                safeReason=_safe_tool_failure(item_type),
                retryable=False,
            )
        return HarnessEvent(
            type="harness.tool.failed" if failed else "harness.tool.completed",
            nativeType=method,
            invocationId=invocation_id,
            sessionId=session_id,
            turnId=turn_id,
            author=author,
            content={"role": "model", "parts": []},
            actions={"artifactDelta": completed_artifact},
        )

    if method in _TOOL_OUTPUT_METHODS:
        delta = _delta_text(params)
        if not delta:
            return None
        text, omitted = bounded_redacted_preview(delta)
        tool_call_id = str(params.get("itemId") or "")
        tool_name = _tool_name_for_method(method)
        artifact: JsonObject = {
            "toolCallId": tool_call_id,
            "toolName": tool_name,
            "activityKind": _activity_kind_for_tool_name(tool_name),
            "safeSummary": _safe_output_summary(tool_name),
            "outputPreview": text,
            "omittedLineCount": omitted,
        }
        if model_call_id:
            artifact["modelCallId"] = model_call_id
        return HarnessEvent(
            type="harness.tool.output",
            nativeType=method,
            invocationId=invocation_id,
            sessionId=session_id,
            turnId=turn_id,
            author=author,
            content={"role": "model", "parts": []},
            actions={"artifactDelta": artifact},
        )

    if method == _USAGE_METHOD:
        usage, cumulative_usage = _normalize_usage(params)
        if usage is None:
            return None
        usage_metadata: JsonObject = {"scope": "model_call"}
        if model_call_id:
            usage_metadata["modelCallId"] = model_call_id
        if cumulative_usage is not None:
            usage_metadata["cumulativeUsage"] = cumulative_usage
        return HarnessEvent(
            type="harness.usage",
            nativeType=method,
            invocationId=invocation_id,
            sessionId=session_id,
            turnId=turn_id,
            author=author,
            content={"role": "model", "parts": []},
            actions={"stateDelta": {"usage": usage}, "haas": usage_metadata},
            usage=usage,
        )

    if method == _TURN_STARTED_METHOD:
        return HarnessEvent(
            type="harness.turn.started",
            nativeType=method,
            invocationId=invocation_id,
            sessionId=session_id,
            turnId=turn_id,
            author=author,
            content={"role": "model", "parts": []},
            actions={"stateDelta": {"status": "running"}},
        )

    if method == "turn/plan/updated":
        plan = params.get("plan")
        if not isinstance(plan, list):
            return None
        counts = {"pending": 0, "inProgress": 0, "completed": 0}
        for item in plan:
            if isinstance(item, dict) and item.get("status") in counts:
                counts[str(item["status"])] += 1
        return HarnessEvent(
            type="harness.plan.updated",
            nativeType=method,
            invocationId=invocation_id,
            sessionId=session_id,
            turnId=turn_id,
            author=author,
            content={"role": "model", "parts": []},
            actions={"haas": {"counts": counts, "total": len(plan)}},
        )

    return None


def _tool_name(params: JsonObject) -> str:
    item = params.get("item")
    if isinstance(item, dict):
        value = item.get("name") or item.get("toolName")
        if isinstance(value, str):
            return value
    value = params.get("toolName") or params.get("name")
    return value if isinstance(value, str) else ""


def _tool_name_for_method(method: str) -> str:
    if "commandExecution" in method:
        return "exec_command"
    if "fileChange" in method:
        return "apply_patch"
    if "mcpToolCall" in method:
        return "mcp_tool"
    return "unknown"


def _item_tool_name(item_type: str, item: JsonObject) -> str:
    if item_type == "mcpToolCall":
        server = str(item.get("server") or "mcp")
        tool = str(item.get("tool") or "tool")
        return f"{server}.{tool}"
    if item_type == "dynamicToolCall":
        return str(item.get("tool") or "dynamic_tool")
    return _TOOL_TYPES[item_type]


def _activity_kind(item_type: str, item: JsonObject) -> str:
    if item_type == "fileChange":
        return "edit"
    if item_type == "webSearch":
        return "search"
    if item_type != "commandExecution":
        return "tool"
    actions = item.get("commandActions")
    action_types = {
        str(action.get("type"))
        for action in actions or []
        if isinstance(action, dict) and action.get("type")
    }
    if "search" in action_types:
        return "search"
    if action_types and action_types <= {"read", "listFiles"}:
        return "read"
    return "command"


def _activity_kind_for_tool_name(tool_name: str) -> str:
    if tool_name == "apply_patch":
        return "edit"
    if tool_name == "web_search":
        return "search"
    if tool_name == "exec_command":
        return "command"
    return "tool"


def _safe_tool_summary(item_type: str, item: JsonObject) -> str:
    if item_type == "commandExecution":
        kind = _activity_kind(item_type, item)
        if kind == "read":
            return "Read a file"
        if kind == "search":
            return "Search the workspace"
        return "Run command"
    if item_type == "fileChange":
        count = len(item.get("changes") or [])
        return f"Apply {count} file change{'s' if count != 1 else ''}"
    if item_type == "mcpToolCall":
        return "Call MCP tool"
    if item_type == "webSearch":
        return "Search the web"
    return "Call tool"


def _safe_output_summary(tool_name: str) -> str:
    return {
        "exec_command": "Command output",
        "apply_patch": "File change output",
        "mcp_tool": "Tool progress",
        "web_search": "Search progress",
    }.get(tool_name, "Tool progress")


def _safe_tool_failure(item_type: str) -> str:
    return {
        "commandExecution": "Command failed",
        "fileChange": "File change failed",
        "mcpToolCall": "MCP tool failed",
        "dynamicToolCall": "Tool failed",
        "webSearch": "Web search failed",
    }[item_type]


def _output_metadata(
    params: JsonObject, *, model_call_id: str | None, include_summary_index: bool = False
) -> JsonObject:
    metadata: JsonObject = {}
    item_id = params.get("itemId")
    if isinstance(item_id, str) and item_id:
        metadata["itemId"] = item_id
    if include_summary_index:
        summary_index = params.get("summaryIndex")
        if isinstance(summary_index, int) and not isinstance(summary_index, bool):
            metadata["summaryIndex"] = summary_index
    if model_call_id:
        metadata["modelCallId"] = model_call_id
    return metadata


def _normalize_token_breakdown(usage: object) -> dict[str, Any] | None:
    if not isinstance(usage, dict):
        return None
    result: dict[str, Any] = {}
    keys = {
        "inputTokens": "inputTokens",
        "outputTokens": "outputTokens",
        "totalTokens": "totalTokens",
        "cachedInputTokens": "cacheReadTokens",
        "cacheReadTokens": "cacheReadTokens",
        "cacheWriteInputTokens": "cacheWriteTokens",
        "cacheWriteTokens": "cacheWriteTokens",
        "reasoningOutputTokens": "reasoningOutputTokens",
    }
    for native_key, canonical_key in keys.items():
        value = usage.get(native_key)
        if isinstance(value, int) and not isinstance(value, bool):
            result[canonical_key] = value
    required = {"inputTokens", "outputTokens", "totalTokens"}
    return result if required <= result.keys() else None


def _normalize_usage(
    params: JsonObject,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    token_usage = params.get("tokenUsage")
    if not isinstance(token_usage, dict):
        return None, None
    if "last" in token_usage or "total" in token_usage:
        return (
            _normalize_token_breakdown(token_usage.get("last")),
            _normalize_token_breakdown(token_usage.get("total")),
        )
    # Legacy fixtures and older adapters emitted a flat breakdown. Preserve it
    # as measured call usage, but never manufacture a cumulative snapshot.
    return _normalize_token_breakdown(token_usage), None
