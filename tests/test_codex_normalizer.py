"""Golden-event tests for Codex notification -> HarnessEvent normalization."""
from __future__ import annotations

from haas.harnesses.codex_app_server.normalizer import (
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
        _notification("item/agentMessage/delta", {"delta": "hello"}),
        **CTX,
    )
    assert event is not None
    assert event.type == "harness.text.delta"
    assert event.nativeType == "item/agentMessage/delta"
    assert event.content == {"role": "model", "parts": [{"text": "hello"}]}


def test_reasoning_text_delta() -> None:
    event = normalize_notification(
        _notification("item/reasoning/textDelta", {"delta": "thinking..."}),
        **CTX,
    )
    assert event is not None
    assert event.type == "harness.reasoning.delta"
    assert event.content["parts"][0] == {"text": "thinking...", "thought": True}


def test_tool_output_delta() -> None:
    event = normalize_notification(
        _notification(
            "item/commandExecution/outputDelta",
            {"delta": "stdout", "item": {"name": "bash"}},
        ),
        **CTX,
    )
    assert event is not None
    assert event.type == "harness.tool.output"
    assert event.actions["artifactDelta"]["toolOutput"] == "stdout"
    assert event.actions["artifactDelta"]["toolName"] == "bash"


def test_token_usage() -> None:
    event = normalize_notification(
        _notification(
            "thread/tokenUsage/updated",
            {"tokenUsage": {"inputTokens": 10, "outputTokens": 5}},
        ),
        **CTX,
    )
    assert event is not None
    assert event.type == "harness.usage"
    assert event.usage == {"inputTokens": 10, "outputTokens": 5}


def test_turn_started() -> None:
    event = normalize_notification(_notification("turn/started", {}), **CTX)
    assert event is not None
    assert event.type == "harness.turn.started"


def test_housekeeping_notification_is_dropped() -> None:
    assert normalize_notification(_notification("skills/changed", {}), **CTX) is None
    assert normalize_notification(_notification("project/changed", {}), **CTX) is None


def test_empty_delta_is_dropped() -> None:
    assert (
        normalize_notification(_notification("item/agentMessage/delta", {"delta": ""}), **CTX)
        is None
    )
