"""Tests for the Codex app-server adapter.

Offline unit tests cover sandbox projection. Loopback integration tests drive
the full turn lifecycle against an in-process fake app-server (no real Codex).
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from haas.execution_evidence import ExecutionEvidenceStore
from haas.harnesses.base import (
    CancelTurnRequest,
    ListArtifactsRequest,
    PrepareSessionRequest,
    StartTurnRequest,
    TurnHandle,
)
from haas.harnesses.codex_app_server.adapter import (
    CodexAdapter,
    _terminal_failure_details,
    _TurnContext,
)
from haas.harnesses.codex_app_server.sandbox import (
    SandboxPolicyError,
    to_thread_sandbox_mode,
    to_turn_sandbox_policy,
)
from haas.harnesses.codex_app_server.transport import CodexEndpoint
from haas.policy import NetworkPolicy

# --- offline: sandbox projection -------------------------------------------


def test_thread_sandbox_mode_mapping() -> None:
    assert to_thread_sandbox_mode("workspace-write") == "workspace-write"
    assert to_thread_sandbox_mode("workspace_write") == "workspace-write"
    assert to_thread_sandbox_mode("read-only") == "read-only"
    assert to_thread_sandbox_mode("danger-full-access") == "danger-full-access"


def test_terminal_failure_details_are_stable_redacted_and_recoverable() -> None:
    secret_error = "provider rejected api_key=abcdefghijklmnop"  # haas-secret-ignore
    notification = {
        "method": "turn/completed",
        "params": {
            "turn": {
                "status": "failed",
                "error": {
                    "message": secret_error,
                    "codexErrorInfo": "rateLimitExceeded",
                },
            }
        },
    }

    code, reason, retryable = _terminal_failure_details(notification)

    assert code == "haas_rate_limited"
    assert reason == "provider rejected api_key=[REDACTED]"
    assert retryable is True


def test_command_output_delta_accumulates_in_ephemeral_evidence() -> None:
    adapter = CodexAdapter(CodexEndpoint(transport="stdio", listen_url="stdio://"))
    store = ExecutionEvidenceStore(clock_ms=lambda: 1_789_263_000_000)
    adapter.bind_execution_evidence_store(store)
    ctx = _TurnContext(
        turn_id="turn_1", session_id="hsess_1", invocation_id="inv_1",
        thread_id="thread_1", codex_turn_id="codex_turn_1", timeout_seconds=30,
        principal_id="p_1", user_id="u_1", app_name="chrn_1",
    )
    adapter._to_harness_event(
        {
            "method": "item/started",
            "params": {"item": {
                "id": "call_1", "type": "commandExecution",
                "command": "printf hello", "cwd": "/workspace/project",
            }},
        },
        ctx,
    )
    adapter._to_harness_event(
        {
            "method": "item/commandExecution/outputDelta",
            "params": {"itemId": "call_1", "delta": "API_KEY="},
        },
        ctx,
    )
    adapter._to_harness_event(
        {
            "method": "item/commandExecution/outputDelta",
            "params": {"itemId": "call_1", "delta": "top-secret\nhello"},
        },
        ctx,
    )

    evidence_ref = adapter._command_evidence_refs[("turn_1", "call_1")]
    assert store.get(evidence_ref).output == "API_KEY=[REDACTED]\nhello"


@pytest.mark.asyncio
async def test_list_artifacts_scans_output_root(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    (workspace / "output" / "reports").mkdir(parents=True)
    (workspace / "output" / "reports" / "report.md").write_text(
        "# Report", encoding="utf-8"
    )
    (workspace / "output_secret").mkdir()
    (workspace / "output_secret" / "leak.md").write_text("secret", encoding="utf-8")
    (workspace / "output" / ".hidden.md").write_text("hidden", encoding="utf-8")

    adapter = CodexAdapter(CodexEndpoint(transport="stdio", listen_url="stdio://"))
    adapter._session_cwds[("chrn_1", "u_1", "hsess_1")] = str(workspace)

    artifacts = await adapter.list_artifacts(
        ListArtifactsRequest(sessionId="hsess_1", appName="chrn_1", userId="u_1")
    )

    assert [(item.name, item.path, item.content) for item in artifacts] == [
        ("report.md", "output/reports/report.md", b"# Report")
    ]


@pytest.mark.asyncio
async def test_list_artifacts_rejects_symlink_files_and_output_root(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    (workspace / "output").mkdir(parents=True)
    (workspace / "output" / "leak.txt").symlink_to(outside)

    adapter = CodexAdapter(CodexEndpoint(transport="stdio", listen_url="stdio://"))
    adapter._session_cwds[("chrn_1", "u_1", "hsess_file_link")] = str(workspace)
    assert await adapter.list_artifacts(
        ListArtifactsRequest(
            sessionId="hsess_file_link", appName="chrn_1", userId="u_1"
        )
    ) == []

    # Replace the real output directory with a link to prove the root itself is rejected.
    (workspace / "output" / "leak.txt").unlink()
    (workspace / "output").rmdir()
    (workspace / "output").symlink_to(tmp_path)
    adapter._session_cwds[("chrn_1", "u_1", "hsess_root_link")] = str(workspace)
    assert await adapter.list_artifacts(
        ListArtifactsRequest(
            sessionId="hsess_root_link", appName="chrn_1", userId="u_1"
        )
    ) == []


@pytest.mark.asyncio
async def test_list_artifacts_scopes_same_session_id_by_app_and_user(tmp_path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    for workspace, content in ((first, "first"), (second, "second")):
        (workspace / "output").mkdir(parents=True)
        (workspace / "output" / "report.txt").write_text(content, encoding="utf-8")

    adapter = CodexAdapter(CodexEndpoint(transport="stdio", listen_url="stdio://"))
    adapter._session_cwds[("chrn_1", "u_1", "hsess_shared")] = str(first)
    adapter._session_cwds[("chrn_2", "u_2", "hsess_shared")] = str(second)

    artifacts = await adapter.list_artifacts(
        ListArtifactsRequest(
            sessionId="hsess_shared", appName="chrn_2", userId="u_2"
        )
    )

    assert [artifact.content for artifact in artifacts] == [b"second"]


def test_command_output_delta_keeps_100001_byte_evidence_complete() -> None:
    adapter = CodexAdapter(CodexEndpoint(transport="stdio", listen_url="stdio://"))
    store = ExecutionEvidenceStore(clock_ms=lambda: 1_789_263_000_000)
    adapter.bind_execution_evidence_store(store)
    ctx = _TurnContext(
        turn_id="turn_1", session_id="hsess_1", invocation_id="inv_1",
        thread_id="thread_1", codex_turn_id="codex_turn_1", timeout_seconds=30,
        principal_id="p_1", user_id="u_1", app_name="chrn_1",
    )
    adapter._to_harness_event(
        {
            "method": "item/started",
            "params": {"item": {
                "id": "call_1", "type": "commandExecution",
                "command": "python3 -c 'print(\"x\" * 100000)'",
                "cwd": "/workspace/project",
            }},
        },
        ctx,
    )
    output = "x" * 100_000 + "\n"
    adapter._to_harness_event(
        {
            "method": "item/commandExecution/outputDelta",
            "params": {"itemId": "call_1", "delta": output},
        },
        ctx,
    )

    evidence_ref = adapter._command_evidence_refs[("turn_1", "call_1")]
    assert store.get(evidence_ref).output == output
    assert len(store.get(evidence_ref).output.encode("utf-8")) == 100_001


def test_command_completion_does_not_replace_more_complete_delta_evidence() -> None:
    adapter = CodexAdapter(CodexEndpoint(transport="stdio", listen_url="stdio://"))
    store = ExecutionEvidenceStore(clock_ms=lambda: 1_789_263_000_000)
    adapter.bind_execution_evidence_store(store)
    ctx = _TurnContext(
        turn_id="turn_1", session_id="hsess_1", invocation_id="inv_1",
        thread_id="thread_1", codex_turn_id="codex_turn_1", timeout_seconds=30,
        principal_id="p_1", user_id="u_1", app_name="chrn_1",
    )
    adapter._to_harness_event(
        {"method": "item/started", "params": {"item": {
            "id": "call_1", "type": "commandExecution",
            "command": "produce-output", "cwd": "/workspace/project",
        }}},
        ctx,
    )
    complete_output = "first\nsecond\nthird\n"
    adapter._to_harness_event(
        {"method": "item/commandExecution/outputDelta",
         "params": {"itemId": "call_1", "delta": complete_output}},
        ctx,
    )
    evidence_ref = adapter._command_evidence_refs[("turn_1", "call_1")]

    adapter._to_harness_event(
        {"method": "item/completed", "params": {"item": {
            "id": "call_1", "type": "commandExecution",
            "command": "produce-output", "cwd": "/workspace/project",
            "status": "completed", "exitCode": 0,
            "aggregatedOutput": "third\n",
        }}},
        ctx,
    )

    assert store.get(evidence_ref).output == complete_output


@pytest.mark.asyncio
async def test_finalize_turn_clears_private_command_accumulators() -> None:
    adapter = CodexAdapter(CodexEndpoint(transport="stdio", listen_url="stdio://"))
    store = ExecutionEvidenceStore(clock_ms=lambda: 1_789_263_000_000)
    adapter.bind_execution_evidence_store(store)
    ctx = _TurnContext(
        turn_id="turn_1", session_id="hsess_1", invocation_id="inv_1",
        thread_id="thread_1", codex_turn_id="codex_turn_1", timeout_seconds=30,
        principal_id="p_1", user_id="u_1", app_name="chrn_1",
    )
    adapter._turn_contexts[ctx.turn_id] = ctx
    adapter._to_harness_event(
        {"method": "item/started", "params": {"item": {
            "id": "call_1", "type": "commandExecution",
            "command": "printf secret", "cwd": "/workspace/project",
        }}},
        ctx,
    )
    adapter._to_harness_event(
        {"method": "item/commandExecution/outputDelta",
         "params": {"itemId": "call_1", "delta": "temporary raw output"}},
        ctx,
    )

    await adapter.finalize_turn(
        TurnHandle(turnId="turn_1", sessionId="hsess_1", invocationId="inv_1")
    )

    assert not adapter._command_evidence_output
    assert not adapter._command_evidence_refs
    assert not adapter._command_evidence_truncated


def test_model_call_correlation_is_stable_across_items_tools_and_usage() -> None:
    adapter = CodexAdapter(CodexEndpoint(transport="stdio", listen_url="stdio://"))
    ctx = _TurnContext(
        turn_id="turn_1", session_id="hsess_1", invocation_id="inv_1",
        thread_id="thread_1", codex_turn_id="codex_turn_1", timeout_seconds=30,
    )

    reasoning = adapter._to_harness_event(
        {"method": "item/reasoning/summaryTextDelta",
         "params": {"itemId": "reason_1", "summaryIndex": 0, "delta": "Inspect"}},
        ctx,
    )
    message = adapter._to_harness_event(
        {"method": "item/agentMessage/delta",
         "params": {"itemId": "msg_1", "delta": "I will inspect."}},
        ctx,
    )
    tool = adapter._to_harness_event(
        {"method": "item/started",
         "params": {"item": {"id": "call_1", "type": "commandExecution",
                                "command": "pwd", "status": "inProgress"}}},
        ctx,
    )
    usage = adapter._to_harness_event(
        {"method": "thread/tokenUsage/updated",
         "params": {"tokenUsage": {
             "last": {"inputTokens": 10, "cachedInputTokens": 0,
                      "cacheWriteInputTokens": 0, "outputTokens": 5,
                      "reasoningOutputTokens": 2, "totalTokens": 15},
             "total": {"inputTokens": 10, "cachedInputTokens": 0,
                       "cacheWriteInputTokens": 0, "outputTokens": 5,
                       "reasoningOutputTokens": 2, "totalTokens": 15},
         }}},
        ctx,
    )
    tool_done = adapter._to_harness_event(
        {"method": "item/completed",
         "params": {"item": {"id": "call_1", "type": "commandExecution",
                                "command": "pwd", "status": "completed", "exitCode": 0}}},
        ctx,
    )
    result = adapter._to_harness_event(
        {"method": "item/agentMessage/delta",
         "params": {"itemId": "msg_2", "delta": "Done."}},
        ctx,
    )
    result_done = adapter._to_harness_event(
        {"method": "item/completed",
         "params": {"item": {"id": "msg_2", "type": "agentMessage",
                                "text": "Done.", "phase": "final_answer"}}},
        ctx,
    )

    assert reasoning and message and tool and usage and tool_done and result and result_done
    first_call_ids = {
        reasoning.actions["haas"]["modelCallId"],
        message.actions["haas"]["modelCallId"],
        tool.actions["artifactDelta"]["modelCallId"],
        usage.actions["haas"]["modelCallId"],
        tool_done.actions["artifactDelta"]["modelCallId"],
    }
    assert first_call_ids == {"mcall_0001"}
    assert result.actions["haas"]["modelCallId"] == "mcall_0002"
    assert result_done.actions["haas"] == {
        "itemId": "msg_2", "modelCallId": "mcall_0002",
        "messagePhase": "final_answer",
    }


def test_thread_sandbox_mode_rejects_unknown() -> None:
    with pytest.raises(SandboxPolicyError):
        to_thread_sandbox_mode("no-isolation")


def test_turn_sandbox_policy_workspace_write() -> None:
    policy = to_turn_sandbox_policy("workspace-write", ["/workspace"])
    assert policy["type"] == "workspaceWrite"
    assert policy["writableRoots"] == ["/workspace"]
    assert policy["networkAccess"] is False


def test_turn_sandbox_policy_network_default_deny_disables_access() -> None:
    policy = to_turn_sandbox_policy(
        "workspace-write",
        ["/workspace"],
        NetworkPolicy(defaultAction="deny", allow=["https://api.openai.com"]),
    )
    assert policy["networkAccess"] is False


def test_turn_sandbox_policy_network_allow_enables_access() -> None:
    policy = to_turn_sandbox_policy(
        "workspace-write",
        ["/workspace"],
        NetworkPolicy(defaultAction="allow"),
    )
    assert policy["networkAccess"] is True


def test_turn_sandbox_policy_read_only() -> None:
    policy = to_turn_sandbox_policy("read-only", [], NetworkPolicy(defaultAction="deny"))
    assert policy["type"] == "readOnly"
    assert policy["networkAccess"] is False


def test_turn_sandbox_policy_read_only_network_allow_enables_access() -> None:
    policy = to_turn_sandbox_policy("read-only", [], NetworkPolicy(defaultAction="allow"))
    assert policy["type"] == "readOnly"
    assert policy["networkAccess"] is True


def test_turn_sandbox_policy_danger_full_access() -> None:
    policy = to_turn_sandbox_policy("danger-full-access", [])
    assert policy["type"] == "dangerFullAccess"


def test_turn_sandbox_policy_rejects_broad_root() -> None:
    with pytest.raises(SandboxPolicyError, match="too_broad"):
        to_turn_sandbox_policy("workspace-write", ["/"])


def test_turn_sandbox_policy_rejects_relative_root() -> None:
    with pytest.raises(SandboxPolicyError, match="not_absolute"):
        to_turn_sandbox_policy("workspace-write", ["workspace"])


# --- loopback integration: full turn lifecycle -----------------------------


@pytest.mark.integration
async def test_codex_adapter_full_turn() -> None:
    async def handler(ws: Any) -> None:
        async for raw in ws:
            msg = json.loads(raw)
            method = msg.get("method")
            if method == "initialize":
                await ws.send(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": {}}))
            elif method == "initialized":
                continue
            elif method == "thread/start":
                thread_resp = {
                    "jsonrpc": "2.0",
                    "id": msg["id"],
                    "result": {"thread": {"id": "thr_1"}},
                }
                await ws.send(json.dumps(thread_resp))
            elif method == "turn/start":
                turn_resp = {
                    "jsonrpc": "2.0",
                    "id": msg["id"],
                    "result": {"turn": {"id": "codex_turn_1"}},
                }
                await ws.send(json.dumps(turn_resp))
                delta = {
                    "jsonrpc": "2.0",
                    "method": "item/agentMessage/delta",
                    "params": {
                        "type": "item/agentMessage/delta",
                        "threadId": "thr_1",
                        "turnId": "codex_turn_1",
                        "delta": "hello",
                    },
                }
                await ws.send(json.dumps(delta))
                completed = {
                    "jsonrpc": "2.0",
                    "method": "turn/completed",
                    "params": {
                        "type": "turn/completed",
                        "threadId": "thr_1",
                        "turnId": "codex_turn_1",
                        "turn": {"id": "codex_turn_1", "status": "completed"},
                    },
                }
                await ws.send(json.dumps(completed))
            elif method == "turn/interrupt":
                await ws.send(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": {}}))

    from websockets.asyncio.server import serve

    async with serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        adapter = CodexAdapter(
            CodexEndpoint(transport="loopback_websocket", listen_url=f"ws://127.0.0.1:{port}")
        )
        await adapter.prepare_session(PrepareSessionRequest(sessionId="hsess_1", appName="chrn_1"))
        handle = await adapter.start_turn(
            StartTurnRequest(
                invocationId="inv_1",
                sessionId="hsess_1",
                turnId="turn_1",
                appName="chrn_1",
                input=[{"text": "hi"}],
            )
        )
        events = [event async for event in adapter.stream_events(handle)]
        assert events, "expected at least one event"
        assert any(event.type == "harness.text.delta" for event in events)
        result = await adapter.finalize_turn(handle)
        assert result.status == "completed"


@pytest.mark.integration
async def test_codex_adapter_cancel() -> None:
    interrupt_sent: list[dict[str, Any]] = []

    async def handler(ws: Any) -> None:
        async for raw in ws:
            msg = json.loads(raw)
            method = msg.get("method")
            if method == "initialize":
                await ws.send(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": {}}))
            elif method == "initialized":
                continue
            elif method == "thread/start":
                thread_resp = {
                    "jsonrpc": "2.0",
                    "id": msg["id"],
                    "result": {"thread": {"id": "thr_1"}},
                }
                await ws.send(json.dumps(thread_resp))
            elif method == "turn/start":
                turn_resp = {
                    "jsonrpc": "2.0",
                    "id": msg["id"],
                    "result": {"turn": {"id": "codex_turn_1"}},
                }
                await ws.send(json.dumps(turn_resp))
            elif method == "turn/interrupt":
                interrupt_sent.append(msg["params"])
                await ws.send(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": {}}))

    from websockets.asyncio.server import serve

    async with serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        adapter = CodexAdapter(
            CodexEndpoint(transport="loopback_websocket", listen_url=f"ws://127.0.0.1:{port}")
        )
        await adapter.prepare_session(PrepareSessionRequest(sessionId="hsess_1", appName="chrn_1"))
        await adapter.start_turn(
            StartTurnRequest(
                invocationId="inv_1",
                sessionId="hsess_1",
                turnId="turn_1",
                appName="chrn_1",
                input=[{"text": "hi"}],
            )
        )
        result = await adapter.cancel_turn(
            CancelTurnRequest(turnId="turn_1", sessionId="hsess_1", invocationId="inv_1")
        )
        assert result.status == "accepted"
        assert interrupt_sent == [{"threadId": "thr_1", "turnId": "codex_turn_1"}]


@pytest.mark.integration
async def test_codex_adapter_timeout_fails_turn() -> None:
    """A stuck turn (no terminal notification) must time out, not hang forever."""

    async def handler(ws: Any) -> None:
        async for raw in ws:
            msg = json.loads(raw)
            method = msg.get("method")
            if method == "initialize":
                await ws.send(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": {}}))
            elif method == "initialized":
                continue
            elif method == "thread/start":
                thread_resp = {
                    "jsonrpc": "2.0",
                    "id": msg["id"],
                    "result": {"thread": {"id": "thr_1"}},
                }
                await ws.send(json.dumps(thread_resp))
            elif method == "turn/start":
                turn_resp = {
                    "jsonrpc": "2.0",
                    "id": msg["id"],
                    "result": {"turn": {"id": "codex_turn_1"}},
                }
                await ws.send(json.dumps(turn_resp))
                # Deliberately emit no notification: the turn is stuck.
            elif method == "turn/interrupt":
                await ws.send(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": {}}))

    from websockets.asyncio.server import serve

    async with serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        adapter = CodexAdapter(
            CodexEndpoint(transport="loopback_websocket", listen_url=f"ws://127.0.0.1:{port}")
        )
        await adapter.prepare_session(PrepareSessionRequest(sessionId="hsess_1", appName="chrn_1"))
        handle = await adapter.start_turn(
            StartTurnRequest(
                invocationId="inv_1",
                sessionId="hsess_1",
                turnId="turn_1",
                appName="chrn_1",
                input=[{"text": "hi"}],
                timeoutSeconds=1.0,
            )
        )
        events = [event async for event in adapter.stream_events(handle)]
        assert events, "expected a terminal event"
        assert events[-1].actions["stateDelta"] == {
            "status": "failed",
            "code": "haas_request_timeout",
            "reason": "long_task_deadline_exceeded",
            "retryable": True,
        }
        result = await adapter.finalize_turn(handle)
        assert result.status == "failed"
