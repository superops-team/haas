"""Tests for ADK message → Codex input format conversion in CodexAdapter."""
from __future__ import annotations

from haas.harnesses.codex_app_server.adapter import _to_codex_input


def test_empty_input_returns_default() -> None:
    assert _to_codex_input([]) == [{"type": "text", "text": ""}]


def test_none_items_returns_default() -> None:
    assert _to_codex_input(None) == [{"type": "text", "text": ""}]  # type: ignore[arg-type]


def test_adk_message_format() -> None:
    """ADK message: {"role": "user", "parts": [{"text": "hello"}]}"""
    result = _to_codex_input([{"role": "user", "parts": [{"text": "hello"}]}])
    assert result == [{"type": "text", "text": "hello"}]


def test_adk_message_multiple_parts() -> None:
    """ADK message with multiple text parts."""
    result = _to_codex_input([
        {"role": "user", "parts": [{"text": "hello"}, {"text": "world"}]}
    ])
    assert result == [
        {"type": "text", "text": "hello"},
        {"type": "text", "text": "world"},
    ]


def test_codex_native_format_pass_through() -> None:
    """Already-native Codex format: {"type": "text", "text": "..."}"""
    result = _to_codex_input([{"type": "text", "text": "hi"}])
    assert result == [{"type": "text", "text": "hi"}]


def test_simplified_format() -> None:
    """Simplified format used in tests: {"text": "..."}"""
    result = _to_codex_input([{"text": "hi"}])
    assert result == [{"type": "text", "text": "hi"}]


def test_mixed_formats() -> None:
    """A mix of ADK and native formats."""
    result = _to_codex_input([
        {"role": "user", "parts": [{"text": "from adk"}]},
        {"type": "text", "text": "from native"},
    ])
    assert result == [
        {"type": "text", "text": "from adk"},
        {"type": "text", "text": "from native"},
    ]


def test_adk_message_with_non_text_part() -> None:
    """Non-text parts in ADK message are skipped."""
    result = _to_codex_input([
        {"role": "user", "parts": [{"text": "hello"}, {"functionCall": {"name": "x"}}]}
    ])
    assert result == [{"type": "text", "text": "hello"}]


def test_all_empty_returns_default() -> None:
    """If all items produce no text, return default empty input."""
    result = _to_codex_input([{"role": "user", "parts": []}])
    assert result == [{"type": "text", "text": ""}]


def test_non_dict_items_skipped() -> None:
    result = _to_codex_input(["not a dict", 42, {"text": "ok"}])  # type: ignore[list-item]
    assert result == [{"type": "text", "text": "ok"}]
