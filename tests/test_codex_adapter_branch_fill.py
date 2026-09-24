"""Branch-fill unit tests for CodexAdapter error / edge paths.

Offline only: drives the adapter with a stubbed RPC layer (no real Codex,
no real socket). Covers respond_interaction validation, inspect/cleanup
state transitions, disabled-tools parsing, and MCP override rejection.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from haas.harnesses.base import (
    CleanupSessionRequest,
    InspectSessionRequest,
    McpServerConfig,
    StartTurnRequest,
)
from haas.harnesses.codex_app_server.adapter import (
    BUILTIN_COWORK_RECALL_MCP_NAME,
    CodexAdapter,
    _disabled_tools,
)
from haas.harnesses.codex_app_server.transport import CodexEndpoint
from haas.stores import ApprovalRecord, InputRequestRecord
from haas.stores.memory import MemoryStore


def _make_adapter() -> CodexAdapter:
    adapter = CodexAdapter(
        CodexEndpoint(transport="stdio", listen_url="stdio://")
    )
    adapter._generation = 1
    return adapter


def _approval_record(record_id: str, generation: int = 1) -> ApprovalRecord:
    return ApprovalRecord(
        id=record_id,
        sessionId="hsess_1",
        invocationId="inv_1",
        turnId="turn_1",
        nativeRequestId="native_1",
        adapterGeneration=generation,
    )


# --- respond_interaction ---------------------------------------------------


async def test_respond_interaction_unknown_request_id_raises() -> None:
    adapter = _make_adapter()
    adapter._rpc.respond = AsyncMock()  # type: ignore[assignment]
    # id deliberately never added to _pending_interactions
    record = _approval_record("appr_unknown")

    with pytest.raises(Exception, match="not pending"):
        await adapter.respond_interaction(record, {"decision": "approved"})

    adapter._rpc.respond.assert_not_called()


async def test_respond_interaction_stale_generation_raises() -> None:
    adapter = _make_adapter()
    adapter._rpc.respond = AsyncMock()  # type: ignore[assignment]
    adapter._pending_interactions.add("appr_stale")
    record = _approval_record("appr_stale", generation=99)

    with pytest.raises(Exception, match="generation is stale"):
        await adapter.respond_interaction(record, {"decision": "approved"})

    adapter._rpc.respond.assert_not_called()


async def test_respond_interaction_invalid_decision_raises() -> None:
    adapter = _make_adapter()
    adapter._rpc.respond = AsyncMock()  # type: ignore[assignment]
    adapter._pending_interactions.add("appr_bad")
    record = _approval_record("appr_bad")

    with pytest.raises(Exception, match="invalid_interaction_decision"):
        await adapter.respond_interaction(record, {"decision": "maybe"})

    adapter._rpc.respond.assert_not_called()


async def test_respond_interaction_approved_no_store_happy_path() -> None:
    # No interaction store bound: the in-memory _pending_interactions set is the
    # only source of truth, so respond must still round-trip through the RPC.
    adapter = _make_adapter()
    adapter._rpc.respond = AsyncMock()  # type: ignore[assignment]
    assert adapter._interaction_store is None
    adapter._pending_interactions.add("appr_ok")
    record = _approval_record("appr_ok")

    await adapter.respond_interaction(record, {"decision": "approved"})

    adapter._rpc.respond.assert_awaited_once_with(
        "native_1", {"decision": "accept"}
    )
    assert "appr_ok" not in adapter._pending_interactions


async def test_respond_interaction_input_request_answers_shape() -> None:
    adapter = _make_adapter()
    adapter._rpc.respond = AsyncMock()  # type: ignore[assignment]
    adapter._pending_interactions.add("inreq_1")
    record = InputRequestRecord(
        id="inreq_1",
        sessionId="hsess_1",
        invocationId="inv_1",
        turnId="turn_1",
        questions=[{"id": "q1"}],
        nativeRequestId="native_q",
        adapterGeneration=1,
    )

    await adapter.respond_interaction(
        record,
        {"answers": {"q1": {"values": ["yes"]}, "junk": "not-a-dict"}},
    )

    adapter._rpc.respond.assert_awaited_once_with(
        "native_q", {"answers": {"q1": {"answers": ["yes"]}}}
    )
    assert "inreq_1" not in adapter._pending_interactions


# --- inspect / cleanup -----------------------------------------------------


async def test_inspect_non_resumable_returns_empty_inspection() -> None:
    adapter = _make_adapter()
    scope = ("chrn_1", "u_1", "hsess_1")
    adapter._non_resumable.add(scope)

    result = await adapter.inspect_session(
        InspectSessionRequest(
            sessionId="hsess_1", appName="chrn_1", userId="u_1"
        )
    )

    assert result.status == "non_resumable"
    assert result.nativeRef == {}


async def test_inspect_unknown_session_returns_unknown() -> None:
    adapter = _make_adapter()

    result = await adapter.inspect_session(
        InspectSessionRequest(sessionId="hsess_gone")
    )

    assert result.status == "unknown"


async def test_cleanup_pops_state_and_close_closes_transport() -> None:
    adapter = _make_adapter()
    scope = ("chrn_1", "u_1", "hsess_1")
    adapter._session_threads[scope] = "thr_1"
    adapter._session_cwds[scope] = "/work"
    adapter._non_resumable.add(scope)
    adapter._rpc.close = AsyncMock()  # type: ignore[assignment]

    result = await adapter.cleanup_session(
        CleanupSessionRequest(
            sessionId="hsess_1", appName="chrn_1", userId="u_1"
        )
    )

    assert result.status == "cleaned"
    assert scope not in adapter._session_threads
    assert scope not in adapter._session_cwds
    assert scope not in adapter._non_resumable

    await adapter.close()
    adapter._rpc.close.assert_awaited_once()


# --- disabled tools --------------------------------------------------------


def test_disabled_tools_non_list_is_skipped() -> None:
    # policy.tools.disabled must be a list; anything else is ignored.
    assert _disabled_tools({"tools": {"disabled": "bash"}}) == []
    # policy.tools itself must be a dict.
    assert _disabled_tools({"tools": "nope"}) == []
    # non-string entries inside the list are dropped.
    assert _disabled_tools({"tools": {"disabled": ["bash", 42, "", "shell"]}}) == [
        "bash",
        "shell",
    ]


# --- mcp override rejection ------------------------------------------------


def test_mcp_override_rejects_non_builtin_server() -> None:
    request = StartTurnRequest(
        invocationId="inv_1",
        sessionId="hsess_1",
        turnId="turn_1",
        appName="chrn_1",
        input=[],
        mcpServers=[
            McpServerConfig(
                name="manager-cowork-recall",
                transport="http",
                url="http://127.0.0.1:18080",
                haas_builtin=False,  # not flagged as built-in -> rejected
            )
        ],
    )

    servers = CodexAdapter._mcp_server_overrides(request)

    assert servers == {}


def test_mcp_override_rejects_missing_recall_headers() -> None:
    request = StartTurnRequest(
        invocationId="inv_1",
        sessionId="hsess_1",
        turnId="turn_1",
        appName="chrn_1",
        input=[],
        mcpServers=[
            McpServerConfig(
                name=BUILTIN_COWORK_RECALL_MCP_NAME,
                transport="http",
                url="http://127.0.0.1:18080",
                haas_builtin=True,
                headers={},  # missing X-HaaS-Session-ID / Recall-Token
            )
        ],
    )

    servers = CodexAdapter._mcp_server_overrides(request)

    assert servers == {}


def test_mcp_override_accepts_valid_builtin_recall() -> None:
    request = StartTurnRequest(
        invocationId="inv_1",
        sessionId="hsess_1",
        turnId="turn_1",
        appName="chrn_1",
        input=[],
        mcpServers=[
            McpServerConfig(
                name=BUILTIN_COWORK_RECALL_MCP_NAME,
                transport="http",
                url="http://127.0.0.1:18080/recall",
                haas_builtin=True,
                headers={
                    "X-HaaS-Session-ID": "hsess_1",
                    "X-HaaS-Recall-Token": "tok",
                },
            )
        ],
    )

    servers = CodexAdapter._mcp_server_overrides(request)

    assert BUILTIN_COWORK_RECALL_MCP_NAME in servers
    entry = servers[BUILTIN_COWORK_RECALL_MCP_NAME]
    assert entry["url"] == "http://127.0.0.1:18080/recall"
    assert entry["enabled_tools"] == ["recall"]
