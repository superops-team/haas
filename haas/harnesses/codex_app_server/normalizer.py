"""Codex notification -> canonical HarnessEvent normalization.

Implements specs/harness-adapter/README.md §6.3: native Codex JSON-RPC
notifications are mapped to HaaS canonical events and projected to ADK
``Event`` by the Event Log. Native method names are preserved only in
``nativeType``; upstream payloads are synthesized here (adapter isolation).
"""
from __future__ import annotations

from typing import Any

from haas.harnesses.base import HarnessEvent

JsonObject = dict[str, Any]

# Codex v2 notification methods that carry visible delta content.
_TEXT_DELTA_METHODS = {"item/agentMessage/delta"}
_REASONING_DELTA_METHODS = {
    "item/reasoning/textDelta",
    "item/reasoning/summaryTextDelta",
}
_TOOL_OUTPUT_METHODS = {
    "item/commandExecution/outputDelta",
    "item/fileChange/outputDelta",
    "item/mcpToolCall/progress",
}
_USAGE_METHOD = "thread/tokenUsage/updated"
_TURN_STARTED_METHOD = "turn/started"


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
        return HarnessEvent(
            type="harness.text.delta",
            nativeType=method,
            invocationId=invocation_id,
            sessionId=session_id,
            turnId=turn_id,
            author=author,
            content={"role": "model", "parts": [{"text": text}]},
            actions={"stateDelta": {"status": "running"}},
        )

    if method in _REASONING_DELTA_METHODS:
        text = _delta_text(params)
        if not text:
            return None
        return HarnessEvent(
            type="harness.reasoning.delta",
            nativeType=method,
            invocationId=invocation_id,
            sessionId=session_id,
            turnId=turn_id,
            author=author,
            content={"role": "model", "parts": [{"text": text, "thought": True}]},
            actions={"stateDelta": {"status": "running"}},
        )

    if method in _TOOL_OUTPUT_METHODS:
        text = _delta_text(params)
        return HarnessEvent(
            type="harness.tool.output",
            nativeType=method,
            invocationId=invocation_id,
            sessionId=session_id,
            turnId=turn_id,
            author=author,
            content={"role": "model", "parts": []},
            actions={"artifactDelta": {"toolOutput": text, "toolName": _tool_name(params)}},
        )

    if method == _USAGE_METHOD:
        usage = _normalize_usage(params)
        if usage is None:
            return None
        return HarnessEvent(
            type="harness.usage",
            nativeType=method,
            invocationId=invocation_id,
            sessionId=session_id,
            turnId=turn_id,
            author=author,
            content={"role": "model", "parts": []},
            actions={"stateDelta": {"usage": usage}},
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

    return None


def _tool_name(params: JsonObject) -> str:
    item = params.get("item")
    if isinstance(item, dict):
        value = item.get("name") or item.get("toolName")
        if isinstance(value, str):
            return value
    value = params.get("toolName") or params.get("name")
    return value if isinstance(value, str) else ""


def _normalize_usage(params: JsonObject) -> dict[str, Any] | None:
    usage = params.get("tokenUsage")
    if not isinstance(usage, dict):
        return None
    result: dict[str, Any] = {}
    for key in ("inputTokens", "outputTokens", "totalTokens", "cacheReadTokens"):
        value = usage.get(key)
        if isinstance(value, int):
            result[key] = value
    if not result:
        return None
    return result
