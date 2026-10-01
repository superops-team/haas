from __future__ import annotations

import pytest
from coworker.tool_display import local_tool_display, messages_with_tool_display


def test_local_command_preview_is_safe_bounded_and_not_the_raw_arguments():
    command = "API_KEY=synthetic-placeholder git status --short\necho done " + "文" * 600
    display = local_tool_display("run_shell", {"command": command, "unrelated": "private"})
    assert display["activityKind"] == "command"
    preview = display["commandPreview"]
    assert "synthetic-placeholder" not in preview
    assert "[REDACTED]" in preview
    assert "git status" in preview
    assert "\n" not in preview
    assert len(preview.encode("utf-8")) <= 512
    assert "unrelated" not in display


@pytest.mark.parametrize(
    "name,args",
    [
        ("read_file", {"command": "ignored"}),
        ("run_shell", {}),
        ("run_shell", {"command": 42}),
        ("run_shell", None),
    ],
)
def test_no_command_is_invented_for_unknown_or_malformed_tools(name, args):
    assert local_tool_display(name, args) == {}


def test_plain_command_remains_identifiable():
    assert local_tool_display("run_shell", {"command": "git status --short"}) == {
        "activityKind": "command",
        "commandPreview": "git status --short",
    }


def test_optional_haas_dependency_cannot_break_standalone_tool_execution(monkeypatch):
    import builtins

    original_import = builtins.__import__

    def without_haas(name, *args, **kwargs):
        if name == "haas.security.redact":
            raise ModuleNotFoundError("No module named 'haas'", name="haas")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_haas)
    assert local_tool_display("run_shell", {"command": "git status"}) == {}


def test_readback_handles_old_malformed_calls_without_mutating_history():
    messages = [
        {"role": "user", "content": "synthetic"},
        {
            "role": "assistant",
            "tool_calls": [
                None,
                {"function": None},
                {"function": {"name": "run_shell", "arguments": "invalid"}},
                {"function": {"name": "run_shell", "arguments": '{"command":"git status"}'}},
            ],
        },
    ]
    readback = messages_with_tool_display(messages)
    assert readback[0] == messages[0]
    assert readback[1]["tool_calls"][:2] == messages[1]["tool_calls"][:2]
    assert readback[1]["tool_calls"][2]["_managerDisplay"] == {}
    assert readback[1]["tool_calls"][3]["_managerDisplay"]["commandPreview"] == "git status"
    assert "_managerDisplay" not in messages[1]["tool_calls"][3]
