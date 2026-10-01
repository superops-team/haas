"""Safe display facts for tools owned by the Manager-local engine."""

from __future__ import annotations

import json
from typing import Any


def local_tool_display(name: str, arguments: object) -> dict[str, str]:
    if name != "run_shell" or not isinstance(arguments, dict):
        return {}
    command = arguments.get("command")
    if not isinstance(command, str):
        return {}
    try:
        from haas.security.redact import bounded_redacted_preview
    except ModuleNotFoundError as exc:
        if exc.name not in {"haas", "haas.security", "haas.security.redact"}:
            raise
        # Standalone Manager does not require HaaS. Never display unredacted arguments.
        return {}
    preview, _ = bounded_redacted_preview(
        " ".join(command.splitlines()), max_lines=1, max_bytes=512
    )
    return {"activityKind": "command", "commandPreview": preview.replace("\n", " ")}


def messages_with_tool_display(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Decorate the readback copy, keeping display facts out of provider history."""
    result = []
    for message in messages:
        calls = message.get("tool_calls")
        if not isinstance(calls, list):
            result.append(message)
            continue
        decorated = []
        for call in calls:
            if not isinstance(call, dict) or not isinstance(call.get("function"), dict):
                decorated.append(call)
                continue
            function = call["function"]
            try:
                arguments = json.loads(function.get("arguments", "{}"))
            except (ValueError, TypeError):
                arguments = None
            decorated.append(
                {**call, "_managerDisplay": local_tool_display(function.get("name", ""), arguments)}
            )
        result.append({**message, "tool_calls": decorated})
    return result
