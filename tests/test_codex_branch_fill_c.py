"""Branch-fill wave C tests for Codex adapter / normalizer / rpc.

Offline only. Drives internal adapter state and the JSON-RPC reader loop with
stubbed transports/RPCs (no real Codex, no network). Targets the remaining
uncovered branch arcs in:
  * adapter.py   (inspect/resume/cleanup boundaries, stream_events error paths,
                  _to_codex_input edge, start_turn/resume internals, MCP
                  overrides, artifacts, evidence bookkeeping, model-call id map)
  * normalizer.py (notification method branches, usage/plan edges)
  * rpc.py       (close pending-future, reader loop break/finalize paths)
"""

from __future__ import annotations

import asyncio
import json
import os
import stat
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from haas.execution_evidence import ExecutionEvidenceStore
from haas.harnesses.base import (
    CancelTurnRequest,
    CleanupSessionRequest,
    InspectSessionRequest,
    ResumeSessionRequest,
    StartTurnRequest,
    TurnHandle,
)
from haas.harnesses.codex_app_server.adapter import (
    CodexAdapter,
    _notification_thread_id,
    _notification_turn_id,
    _TurnContext,
)
from haas.harnesses.codex_app_server.normalizer import (
    normalize_notification,
    _tool_name,
)
from haas.harnesses.codex_app_server.rpc import (
    CodexConnectionError,
    CodexJsonRpc,
    CodexSubscriberOverloaded,
)
from haas.harnesses.codex_app_server.transport import CodexEndpoint
from haas.stores import MemoryStore
from haas.stores.memory import ApprovalRecord, InputRequestRecord


# --- helpers ---------------------------------------------------------------


def _make_adapter() -> CodexAdapter:
    adapter = CodexAdapter(CodexEndpoint(transport="stdio", listen_url="stdio://"))
    adapter._generation = 1
    return adapter


class _ScriptedRpc:
    """Minimal offline stand-in for CodexJsonRpc."""

    def __init__(self) -> None:
        self.connected = True
        self.initialized = True
        self.notification_cursor = 0
        self.server_request_cursor = 0
        self.connection_failure_reason: str | None = None
        self.sent: list[tuple[str, Any]] = []
        self.request_impl: Any = AsyncMock(side_effect=lambda m, p: {})
        self._notif_factory: Any = None
        self._sr_factory: Any = None

    async def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self.sent.append((method, params))
        return await self.request_impl(method, params)

    async def respond(self, request_id: Any, result: dict[str, Any]) -> None:
        self.sent.append(("respond", (request_id, result)))

    def notifications(self, *, after: int = 0):  # type: ignore[no-untyped-def]
        return self._notif_factory() if self._notif_factory else _empty_gen()

    def server_requests(self, *, after: int = 0):  # type: ignore[no-untyped-def]
        return self._sr_factory() if self._sr_factory else _empty_gen()

    async def close(self) -> None:
        return None


async def _empty_gen():  # type: ignore[no-untyped-def]
    if False:  # pragma: no cover
        yield {}
    return


def _ctx(turn_id: str = "turn_c", **kw: Any) -> _TurnContext:
    base: dict[str, Any] = dict(
        turn_id=turn_id,
        session_id="hsess_c",
        invocation_id="inv_c",
        thread_id="thr_c",
        codex_turn_id="ct_c",
        timeout_seconds=30.0,
        principal_id="p",
        user_id="u",
        app_name="chrn_1",
    )
    base.update(kw)
    return _TurnContext(**base)


# --- adapter: notification id extraction helpers ---------------------------


def test_notification_thread_id_falls_through_params_dict() -> None:
    # params is a dict but neither threadId/thread_id is a string -> "".
    assert _notification_thread_id({"params": {"threadId": 123, "thread_id": None}}) == ""


def test_notification_thread_id_reads_from_params() -> None:
    assert _notification_thread_id({"params": {"thread_id": "thr_p"}}) == "thr_p"


def test_notification_turn_id_turn_not_dict_returns_empty() -> None:
    # params dict, no top-level turnId, turn present but not a dict -> "".
    assert _notification_turn_id({"params": {"turn": "not-a-dict"}}) == ""


def test_notification_turn_id_reads_turn_nested_id() -> None:
    assert (
        _notification_turn_id({"params": {"turn": {"id": "ct_nested"}}})
        == "ct_nested"
    )


# --- adapter: _to_codex_input edge -----------------------------------------


def test_to_codex_input_skips_non_dict_parts() -> None:
    from haas.harnesses.codex_app_server.adapter import _to_codex_input

    out = _to_codex_input(
        [
            {"parts": ["just-a-string", 42, {"text": "ok"}, {"text": "second"}]}
        ]
    )
    assert out == [{"type": "text", "text": "ok"}, {"type": "text", "text": "second"}]


# --- adapter: probe endpoint reason ----------------------------------------


async def test_probe_sets_endpoint_reason_when_socket_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = CodexAdapter(
        CodexEndpoint(transport="unix_websocket", listen_url="unix:///definitely/missing.sock")
    )
    import haas.harnesses.codex_app_server.adapter as mod

    monkeypatch.setattr(mod, "codex_cli_version", lambda _bin: "1.2.3")
    monkeypatch.setattr(mod, "load_fixture", lambda _v: {})
    monkeypatch.setattr(mod, "generate_schema_files", lambda _bin: {})
    monkeypatch.setattr(mod, "schema_drift", lambda _a, _b: {})

    probe = await adapter.probe()
    assert probe.status == "unavailable"
    assert probe.safeDetails["safeReason"] == "codex_socket_unavailable"


# --- adapter: resume / inspect / cleanup state machines --------------------


async def test_resume_session_known_thread_skips_opaque_lookup() -> None:
    adapter = _make_adapter()
    rpc = _ScriptedRpc()
    rpc.request_impl = AsyncMock(
        side_effect=lambda m, p: {"thread": {"id": "thr_resumed"}}
    )
    adapter._rpc = rpc  # type: ignore[assignment]
    scope = ("chrn_1", "u_1", "hsess_r")
    adapter._session_threads[scope] = "thr_known"

    out = await adapter.resume_session(
        ResumeSessionRequest(sessionId="hsess_r", appName="chrn_1", userId="u_1")
    )
    assert out.nativeRef["threadId"] == "thr_resumed"


async def test_resume_session_connection_error_marks_non_resumable() -> None:
    adapter = _make_adapter()
    rpc = _ScriptedRpc()

    async def _impl(method: str, params: dict[str, Any]) -> dict[str, Any]:
        if method == "thread/resume":
            raise CodexConnectionError("thread not found")
        return {}

    rpc.request_impl = AsyncMock(side_effect=_impl)
    adapter._rpc = rpc  # type: ignore[assignment]

    out = await adapter.resume_session(
        ResumeSessionRequest(
            sessionId="hsess_r2",
            appName="chrn_1",
            userId="u_1",
            opaque={"threadId": "thr_x"},
        )
    )
    assert out.nativeRef["nonResumable"] is True


async def test_inspect_session_connection_error_marks_non_resumable() -> None:
    adapter = _make_adapter()
    rpc = _ScriptedRpc()

    async def _impl(method: str, params: dict[str, Any]) -> dict[str, Any]:
        if method == "thread/resume":
            raise CodexConnectionError("thread not found")
        return {}

    rpc.request_impl = AsyncMock(side_effect=_impl)
    adapter._rpc = rpc  # type: ignore[assignment]
    scope = ("chrn_1", "u_1", "hsess_i")
    adapter._session_threads[scope] = "thr_i"

    insp = await adapter.inspect_session(
        InspectSessionRequest(sessionId="hsess_i", appName="chrn_1", userId="u_1")
    )
    assert insp.status == "non_resumable"
    assert scope in adapter._non_resumable


async def test_inspect_session_active_and_cleanup_discards_state() -> None:
    adapter = _make_adapter()
    rpc = _ScriptedRpc()
    rpc.request_impl = AsyncMock(
        side_effect=lambda m, p: {"thread": {"id": "thr_active"}}
    )
    adapter._rpc = rpc  # type: ignore[assignment]
    scope = ("chrn_1", "u_1", "hsess_c2")
    adapter._session_threads[scope] = "thr_i"
    adapter._session_cwds[scope] = "/workspace"

    insp = await adapter.inspect_session(
        InspectSessionRequest(sessionId="hsess_c2", appName="chrn_1", userId="u_1")
    )
    assert insp.status == "active"

    res = await adapter.cleanup_session(
        CleanupSessionRequest(sessionId="hsess_c2", appName="chrn_1", userId="u_1")
    )
    assert res.status == "cleaned"
    assert scope not in adapter._session_threads
    assert scope not in adapter._session_cwds


# --- adapter: stream_events error / filter paths ---------------------------


async def test_stream_events_notification_overloaded_terminal() -> None:
    adapter = _make_adapter()
    adapter._turn_contexts["turn_ovn"] = _ctx("turn_ovn")
    rpc = _ScriptedRpc()

    async def _notif():
        raise CodexSubscriberOverloaded("consumer too slow")
        yield {}  # pragma: no cover

    async def _sr():
        await asyncio.sleep(30)
        yield {}  # pragma: no cover

    rpc._notif_factory = _notif
    rpc._sr_factory = _sr
    adapter._rpc = rpc  # type: ignore[assignment]

    handle = TurnHandle(turnId="turn_ovn", sessionId="hsess_c", invocationId="inv_c")
    events = [e async for e in adapter.stream_events(handle)]
    assert events and events[0].type == "harness.turn.failed"
    assert events[0].actions["stateDelta"]["code"] == "haas_adapter_overloaded"


async def test_stream_events_unknown_notification_yields_nothing_then_terminal() -> None:
    adapter = _make_adapter()
    adapter._turn_contexts["turn_f"] = _ctx("turn_f")
    rpc = _ScriptedRpc()

    async def _notif():
        yield {"method": "some/unknown/event", "params": {}}
        yield {
            "method": "turn/completed",
            "params": {"turn": {"id": "ct_c", "status": "completed"}},
        }
        await asyncio.sleep(30)
        yield {}  # pragma: no cover

    async def _sr():
        await asyncio.sleep(30)
        yield {}  # pragma: no cover

    rpc._notif_factory = _notif
    rpc._sr_factory = _sr
    adapter._rpc = rpc  # type: ignore[assignment]

    handle = TurnHandle(turnId="turn_f", sessionId="hsess_c", invocationId="inv_c")
    events = [e async for e in adapter.stream_events(handle)]
    assert [e.type for e in events] == ["harness.turn.completed"]


# --- adapter: _persist_server_request edges --------------------------------


def _bound_store_adapter() -> CodexAdapter:
    adapter = _make_adapter()
    adapter.bind_interaction_store(MemoryStore())
    return adapter


def test_persist_server_request_missing_id_returns_none() -> None:
    adapter = _bound_store_adapter()
    event = adapter._persist_server_request(
        {"method": "item/commandExecution/requestApproval", "params": {}},
        _ctx(),
    )
    assert event is None


def test_persist_server_request_questions_not_list_still_records() -> None:
    adapter = _bound_store_adapter()
    event = adapter._persist_server_request(
        {
            "id": 55,
            "method": "item/tool/requestUserInput",
            "params": {"questions": "not-a-list", "isBlocking": False},
        },
        _ctx(),
    )
    assert event is not None
    assert event.type == "haas.input.required"
    assert event.actions["haas"]["questions"] == []
    assert event.actions["haas"]["blocking"] is False


def test_persist_server_request_unknown_method_returns_none() -> None:
    adapter = _bound_store_adapter()
    event = adapter._persist_server_request(
        {"id": 77, "method": "item/something/else", "params": {}},
        _ctx(),
    )
    assert event is None


# --- adapter: internal thread / mcp branches -------------------------------


async def test_start_thread_non_dict_config_skips_mcp_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _make_adapter()
    rpc = _ScriptedRpc()
    rpc.request_impl = AsyncMock(side_effect=lambda m, p: {"thread": {"id": "thr_x"}})
    adapter._rpc = rpc  # type: ignore[assignment]

    req = StartTurnRequest(
        invocationId="i", sessionId="s", turnId="t", appName="a",
        input=[{"text": "x"}],
    )
    monkeypatch.setattr(CodexAdapter, "_model_overrides", staticmethod(lambda r: {"config": "bogus"}))
    monkeypatch.setattr(CodexAdapter, "_mcp_server_overrides", staticmethod(lambda r: {"srv": {}}))

    tid = await adapter._start_thread(("a", "u", "s"), "/workspace", "workspace-write", req)
    assert tid == "thr_x"
    params = next(p for m, p in rpc.sent if m == "thread/start")
    assert params["config"] == "bogus"  # mcp_servers NOT injected


def test_mcp_server_overrides_skips_non_dict_entry() -> None:
    from types import SimpleNamespace

    adapter = _make_adapter()
    fake_req = SimpleNamespace(mcpServers=["not-a-dict", 123])
    out = adapter._mcp_server_overrides(fake_req)  # type: ignore[arg-type]
    assert out == {}


async def test_resume_thread_with_credentials_unsubscribes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _make_adapter()
    rpc = _ScriptedRpc()
    rpc.request_impl = AsyncMock(side_effect=lambda m, p: {"thread": {"id": "thr_r"}})
    adapter._rpc = rpc  # type: ignore[assignment]

    req = StartTurnRequest(
        invocationId="i", sessionId="s", turnId="t", appName="a",
        input=[{"text": "x"}],
        credentials={"baseUrl": "http://127.0.0.1:1", "token": "t"},
    )
    tid = await adapter._resume_thread("thr_old", ("a", "u", "s"), "/workspace", req)
    assert tid == "thr_r"
    assert any(m == "thread/unsubscribe" for m, _ in rpc.sent)


async def test_resume_thread_non_dict_config_skips_mcp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _make_adapter()
    rpc = _ScriptedRpc()
    rpc.request_impl = AsyncMock(side_effect=lambda m, p: {"thread": {"id": "thr_r2"}})
    adapter._rpc = rpc  # type: ignore[assignment]

    req = StartTurnRequest(
        invocationId="i", sessionId="s", turnId="t", appName="a",
        input=[{"text": "x"}],
    )
    monkeypatch.setattr(CodexAdapter, "_model_overrides", staticmethod(lambda r: {"config": "junk"}))
    monkeypatch.setattr(CodexAdapter, "_mcp_server_overrides", staticmethod(lambda r: {"sv": {}}))

    tid = await adapter._resume_thread("thr_o2", ("a", "u", "s"), "/workspace", req)
    assert tid == "thr_r2"
    resume_params = next(p for m, p in rpc.sent if m == "thread/resume")
    assert resume_params["config"] == "junk"


async def test_resume_thread_missing_id_returns_original_thread_id() -> None:
    adapter = _make_adapter()
    rpc = _ScriptedRpc()
    rpc.request_impl = AsyncMock(side_effect=lambda m, p: {"thread": {}})
    adapter._rpc = rpc  # type: ignore[assignment]
    scope = ("a", "u", "s3")
    adapter._session_threads[scope] = "thr_orig"

    tid = await adapter._resume_thread("thr_orig", scope, "/workspace", None)
    assert tid == "thr_orig"


async def test_cancel_turn_success_returns_accepted() -> None:
    adapter = _make_adapter()
    rpc = _ScriptedRpc()
    rpc.request_impl = AsyncMock(side_effect=lambda m, p: {})
    adapter._rpc = rpc  # type: ignore[assignment]
    adapter._turn_contexts["turn_c"] = _ctx("turn_c")

    res = await adapter.cancel_turn(
        CancelTurnRequest(turnId="turn_c", sessionId="hsess_c", invocationId="inv_c")
    )
    assert res.status == "accepted"
    assert any(m == "turn/interrupt" for m, _ in rpc.sent)


# --- adapter: artifact listing ---------------------------------------------


def test_list_artifacts_caps_at_twenty(tmp_path: Path) -> None:
    out = tmp_path / "output"
    out.mkdir()
    for i in range(25):
        (out / f"file_{i:02d}.txt").write_text(f"data{i}", encoding="utf-8")

    adapter = _make_adapter()
    adapter._session_cwds[("a", "u", "s")] = str(tmp_path)
    refs = adapter._list_artifacts_sync("a", "u", "s")
    assert len(refs) == 20


def test_list_artifacts_skips_non_regular_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = tmp_path / "output"
    out.mkdir()
    (out / "a.txt").write_text("hello", encoding="utf-8")

    real_fstat = os.fstat

    def fake_fstat(fd: int) -> os.stat_result:
        st = real_fstat(fd)
        return os.stat_result(
            (0o0000, st.st_ino, st.st_dev, st.st_nlink, st.st_uid, st.st_gid,
             10, st.st_atime, st.st_mtime, st.st_ctime)
        )

    monkeypatch.setattr(os, "fstat", fake_fstat)

    adapter = _make_adapter()
    adapter._session_cwds[("a", "u", "s")] = str(tmp_path)
    refs = adapter._list_artifacts_sync("a", "u", "s")
    assert refs == []


# --- adapter: command-evidence bookkeeping ---------------------------------


def test_to_harness_event_output_delta_no_evidence_ref(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = _make_adapter()
    adapter._execution_evidence = MagicMock(spec=ExecutionEvidenceStore)
    ctx = _ctx("turn_e")
    notification = {
        "method": "item/commandExecution/outputDelta",
        "params": {"itemId": "tool_x", "delta": "some-output"},
    }
    event = adapter._to_harness_event(notification, ctx)
    assert event is not None
    assert event.type == "harness.tool.output"


def test_to_harness_event_truncated_evidence_skips_update() -> None:
    adapter = _make_adapter()
    fake_store = MagicMock(spec=ExecutionEvidenceStore)
    adapter._execution_evidence = fake_store
    ctx = _ctx("turn_e2")
    key = ("turn_e2", "tool_y")
    adapter._command_evidence_refs[key] = "evd_y"
    adapter._command_evidence_truncated.add(key)

    notification = {
        "method": "item/commandExecution/outputDelta",
        "params": {"itemId": "tool_y", "delta": "tail"},
    }
    event = adapter._to_harness_event(notification, ctx)
    assert event is not None
    fake_store.update_output.assert_not_called()


def test_to_harness_event_oversized_delta_marks_truncated() -> None:
    adapter = _make_adapter()
    fake_store = MagicMock(spec=ExecutionEvidenceStore)
    adapter._execution_evidence = fake_store
    ctx = _ctx("turn_e3")
    key = ("turn_e3", "tool_z")
    adapter._command_evidence_refs[key] = "evd_z"
    adapter._command_evidence_output[key] = "a" * (8 << 20)  # already at cap

    notification = {
        "method": "item/commandExecution/outputDelta",
        "params": {"itemId": "tool_z", "delta": "b" * 1000},
    }
    event = adapter._to_harness_event(notification, ctx)
    assert event is not None
    assert key in adapter._command_evidence_truncated
    fake_store.update_output.assert_called_once()


def test_to_harness_event_terminal_method_clears_evidence_state() -> None:
    adapter = _make_adapter()
    adapter._execution_evidence = MagicMock(spec=ExecutionEvidenceStore)
    ctx = _ctx("turn_tm")
    adapter._command_evidence_output[("turn_tm", "k")] = "out"

    notification = {
        "method": "turn/completed",
        "params": {"turn": {"id": "ct_c", "status": "completed"}},
    }
    adapter._to_harness_event(notification, ctx)
    assert ("turn_tm", "k") not in adapter._command_evidence_output


# --- adapter: _model_call_id correlation branches --------------------------


def test_model_call_id_agent_message_known_item() -> None:
    adapter = _make_adapter()
    ctx = _ctx("turn_m")
    ctx.item_model_calls["item_a"] = "mcall_0007"
    out = adapter._model_call_id(
        {"method": "item/agentMessage/delta", "params": {"itemId": "item_a"}}, ctx
    )
    assert out == "mcall_0007"


def test_model_call_id_agent_message_delta_without_item_id() -> None:
    adapter = _make_adapter()
    ctx = _ctx("turn_m2")
    out = adapter._model_call_id(
        {"method": "item/agentMessage/delta", "params": {}}, ctx
    )
    assert isinstance(out, str) and out


def test_model_call_id_tool_started_without_item_id() -> None:
    adapter = _make_adapter()
    ctx = _ctx("turn_m3")
    out = adapter._model_call_id(
        {
            "method": "item/started",
            "params": {"item": {"type": "commandExecution"}, "itemId": ""},
        },
        ctx,
    )
    assert isinstance(out, str) and out
    assert ctx.active_tool_ids == set()


def test_model_call_id_tool_completed_without_item_id() -> None:
    adapter = _make_adapter()
    ctx = _ctx("turn_m4")
    out = adapter._model_call_id(
        {
            "method": "item/completed",
            "params": {"item": {"type": "commandExecution"}},
        },
        ctx,
    )
    assert isinstance(out, str) and out
    assert ctx.completed_tool_in_model_call is True


def test_model_call_id_tool_output_without_item_id() -> None:
    adapter = _make_adapter()
    ctx = _ctx("turn_m5")
    out = adapter._model_call_id(
        {
            "method": "item/commandExecution/outputDelta",
            "params": {"delta": "x"},
        },
        ctx,
    )
    assert isinstance(out, str) and out


def test_model_call_id_unknown_method_returns_none() -> None:
    adapter = _make_adapter()
    ctx = _ctx("turn_m6")
    out = adapter._model_call_id({"method": "turn/started", "params": {}}, ctx)
    assert out is None


# --- normalizer: remaining notification branches ---------------------------


def test_normalizer_working_dir_outside_workspace_is_previewed() -> None:
    event = normalize_notification(
        {
            "method": "item/started",
            "params": {"item": {"type": "commandExecution", "id": "c1", "command": "ls", "cwd": "/tmp/data"}},
        },
        invocation_id="i", session_id="s", turn_id="t", author="codex",
    )
    assert event is not None
    # cwd outside /workspace goes through the redacted-preview path (branch 98-99).
    assert "workingDirectory" in event.actions["artifactDelta"]


def test_normalizer_item_completed_agent_message_without_model_call_id() -> None:
    event = normalize_notification(
        {
            "method": "item/completed",
            "params": {"item": {"type": "agentMessage", "id": "am1", "phase": "final_answer"}},
        },
        invocation_id="i", session_id="s", turn_id="t", author="codex",
        model_call_id=None,
    )
    assert event is not None
    assert "modelCallId" not in event.actions["haas"]
    assert event.actions["haas"]["messagePhase"] == "final_answer"


def test_normalizer_agent_message_completed_other_phase_has_no_message_phase() -> None:
    event = normalize_notification(
        {
            "method": "item/completed",
            "params": {"item": {"type": "agentMessage", "id": "am2", "phase": "scratchpad"}},
        },
        invocation_id="i", session_id="s", turn_id="t", author="codex",
        model_call_id="mc1",
    )
    assert event is not None
    assert event.actions["haas"]["modelCallId"] == "mc1"
    assert "messagePhase" not in event.actions["haas"]


def test_normalizer_tool_started_empty_command_has_no_preview() -> None:
    event = normalize_notification(
        {
            "method": "item/started",
            "params": {"item": {"type": "commandExecution", "id": "c2", "command": "", "cwd": "/workspace"}},
        },
        invocation_id="i", session_id="s", turn_id="t", author="codex",
    )
    assert event is not None
    assert "commandPreview" not in event.actions["artifactDelta"]
    assert event.actions["artifactDelta"]["workingDirectory"] == "/workspace"


def test_normalizer_tool_completed_empty_command_has_no_preview() -> None:
    event = normalize_notification(
        {
            "method": "item/completed",
            "params": {
                "item": {"type": "commandExecution", "id": "c3", "command": [], "status": "completed", "exitCode": 0},
            },
        },
        invocation_id="i", session_id="s", turn_id="t", author="codex",
    )
    assert event is not None
    assert event.type == "harness.tool.completed"
    assert "commandPreview" not in event.actions["artifactDelta"]


def test_tool_name_helper_item_name_non_string_falls_back() -> None:
    assert _tool_name({"item": {"name": 123}, "toolName": "top"}) == "top"


def test_normalizer_reasoning_delta_non_int_summary_index_dropped() -> None:
    event = normalize_notification(
        {
            "method": "item/reasoning/summaryTextDelta",
            "params": {"delta": "thinking", "summaryIndex": "not-an-int"},
        },
        invocation_id="i", session_id="s", turn_id="t", author="codex",
    )
    assert event is not None
    assert "summaryIndex" not in event.actions["haas"]


# --- rpc: reader loop / close paths ----------------------------------------


class _FakeTransport:
    def __init__(self, frames: list[Any]) -> None:
        self._frames = list(frames)

    async def recv(self) -> Any:
        nxt = self._frames.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt

    async def close(self) -> None:
        return None


def _rpc() -> CodexJsonRpc:
    return CodexJsonRpc(CodexEndpoint(transport="loopback_websocket", listen_url="ws://127.0.0.1:1"))


async def test_rpc_close_sets_exception_on_unfinished_pending_request() -> None:
    rpc = _rpc()
    loop = asyncio.get_running_loop()
    fut: asyncio.Future[dict[str, Any]] = loop.create_future()
    rpc._pending_requests[42] = fut
    rpc._connected = True

    await rpc.close()
    assert fut.done()
    with pytest.raises(CodexConnectionError, match="connection closed"):
        fut.result()


async def test_rpc_reader_loop_breaks_on_recv_connection_error() -> None:
    from websockets.exceptions import ConnectionClosed

    rpc = _rpc()
    rpc._transport = _FakeTransport([ConnectionClosed(None, None)])  # type: ignore[arg-type]
    rpc._connected = True
    await rpc._notification_loop()
    assert rpc._connected is False


async def test_rpc_reader_response_for_already_done_future_is_skipped() -> None:
    rpc = _rpc()
    loop = asyncio.get_running_loop()
    done_fut: asyncio.Future[dict[str, Any]] = loop.create_future()
    done_fut.set_result({"result": {}})
    rpc._pending_requests[1] = done_fut

    body = json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"ok": True}})
    from websockets.exceptions import ConnectionClosed

    rpc._transport = _FakeTransport([body, ConnectionClosed(None, None)])  # type: ignore[arg-type]
    rpc._connected = True
    await rpc._notification_loop()  # must not raise, must not double-set
    assert rpc._connected is False


async def test_rpc_reader_finalize_skips_already_done_pending_future() -> None:
    rpc = _rpc()
    loop = asyncio.get_running_loop()
    done_fut: asyncio.Future[dict[str, Any]] = loop.create_future()
    done_fut.set_result({"result": {}})
    rpc._pending_requests[7] = done_fut

    rpc._transport = _FakeTransport([ConnectionResetError("reset")])
    rpc._connected = True
    await rpc._notification_loop()
    # done future was not re-raised; pending map cleared.
    assert rpc._pending_requests == {}


async def test_rpc_reader_loop_immediate_exit_when_not_connected() -> None:
    rpc = _rpc()
    rpc._transport = _FakeTransport([])
    rpc._connected = False
    await rpc._notification_loop()  # while condition false -> finally only
    assert rpc._pending_requests == {}
