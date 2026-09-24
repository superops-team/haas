"""Codex app-server harness adapter (specs/codex-app-server-adapter/).

Implements the :class:`HarnessAdapter` protocol over the Codex app-server
JSON-RPC transport. Native Codex thread/turn/notification semantics stay
inside this module (adapter isolation, AGENTS.md 铁律 #5).
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import os
import stat
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from haas.execution_evidence import MAX_EVIDENCE_BYTES, ExecutionEvidenceStore
from haas.harnesses.base import (
    AdapterProbe,
    AdapterTurnResult,
    AdapterTurnStartError,
    ArtifactRef,
    CancelResult,
    CancelTurnRequest,
    CleanupResult,
    CleanupSessionRequest,
    HarnessEvent,
    HarnessSandboxDecl,
    InspectSessionRequest,
    ListArtifactsRequest,
    McpServerConfig,
    PreparedSession,
    PrepareSessionRequest,
    ResumeSessionRequest,
    SessionInspection,
    StartTurnRequest,
    TurnHandle,
)
from haas.harnesses.codex_app_server.normalizer import (
    command_text,
    normalize_notification,
    notification_method,
    notification_params,
)
from haas.harnesses.codex_app_server.rpc import (
    CodexConnectionError,
    CodexJsonRpc,
    CodexRequestTimeout,
    CodexSubscriberOverloaded,
)
from haas.harnesses.codex_app_server.sandbox import (
    to_thread_sandbox_mode,
    to_turn_sandbox_policy,
)
from haas.harnesses.codex_app_server.schema import (
    codex_cli_version,
    generate_schema_files,
    load_fixture,
    schema_drift,
)
from haas.harnesses.codex_app_server.transport import CodexEndpoint
from haas.security.redact import safe_upstream_body
from haas.stores import ApprovalRecord, InputRequestRecord, MemoryStore

JsonObject = dict[str, Any]

DEFAULT_CWD = "/workspace"
DEFAULT_APPROVAL_POLICY = "on-request"
DEFAULT_TIMEOUT_SECONDS = 86_400.0
BUILTIN_COWORK_RECALL_MCP_NAME = "manager-cowork-recall"
MAX_ARTIFACT_BYTES = 100 * 1024 * 1024
SessionScope = tuple[str, str, str]

# Codex notification methods that terminate a turn, mapped to canonical status.
_TERMINAL_METHODS: dict[str, str] = {
    "turn/completed": "completed",
    "turn/failed": "failed",
    "turn/interrupted": "interrupted",
    "turn/cancelled": "cancelled",
}
_APPROVAL_METHODS = {
    "item/commandExecution/requestApproval": "command",
    "item/fileChange/requestApproval": "file",
}
_TOOL_ITEM_TYPES = {
    "commandExecution", "fileChange", "mcpToolCall", "dynamicToolCall", "webSearch"
}
_TOOL_OUTPUT_NOTIFICATION_METHODS = {
    "item/commandExecution/outputDelta",
    "item/fileChange/outputDelta",
    "item/mcpToolCall/progress",
}


@dataclass
class _TurnContext:
    turn_id: str
    session_id: str
    invocation_id: str
    thread_id: str
    codex_turn_id: str
    timeout_seconds: float
    started_at: float = field(default_factory=time.monotonic)
    terminal_status: str | None = None
    terminal_event: HarnessEvent | None = None
    principal_id: str = ""
    user_id: str = ""
    app_name: str = ""
    model_call_ordinal: int = 0
    current_model_call_id: str | None = None
    notification_cursor: int = 0
    server_request_cursor: int = 0
    current_model_call_metered: bool = False
    completed_tool_in_model_call: bool = False
    active_tool_ids: set[str] = field(default_factory=set)
    item_model_calls: dict[str, str] = field(default_factory=dict)
    tool_model_calls: dict[str, str] = field(default_factory=dict)


def _notification_thread_id(notification: JsonObject) -> str:
    for key in ("threadId", "thread_id"):
        value = notification.get(key)
        if isinstance(value, str):
            return value
    params = notification.get("params")
    if isinstance(params, dict):
        for key in ("threadId", "thread_id"):
            value = params.get(key)
            if isinstance(value, str):
                return value
    return ""


def _notification_turn_id(notification: JsonObject) -> str:
    for key in ("turnId", "turn_id"):
        value = notification.get(key)
        if isinstance(value, str):
            return value
    params = notification.get("params")
    if isinstance(params, dict):
        for key in ("turnId", "turn_id"):
            value = params.get(key)
            if isinstance(value, str):
                return value
        turn = params.get("turn")
        if isinstance(turn, dict):
            value = turn.get("id")
            if isinstance(value, str):
                return value
    return ""


def _notification_type(notification: JsonObject) -> str:
    value = notification.get("type")
    if isinstance(value, str) and value:
        return value
    params = notification.get("params")
    if isinstance(params, dict):
        value = params.get("type")
        if isinstance(value, str) and value:
            return value
    method = notification.get("method")
    if isinstance(method, str):
        return method
    return ""


def _is_thread_not_found(exc: Exception) -> bool:
    return "thread not found" in str(exc).lower()


def _terminal_status(notification: JsonObject) -> str | None:
    """Return the canonical terminal status for a turn-terminating notification."""
    event_type = _notification_type(notification)
    if event_type == "turn/completed":
        params = notification.get("params")
        turn = params.get("turn") if isinstance(params, dict) else None
        raw_status = "completed"
        if isinstance(turn, dict):
            raw_status = str(turn.get("status", "completed"))
        if raw_status in {"completed", "failed", "cancelled", "interrupted"}:
            return raw_status
        return "completed" if raw_status == "stopped" else "failed"
    return _TERMINAL_METHODS.get(event_type)


def _terminal_failure_details(notification: JsonObject) -> tuple[str, str, bool]:
    params = notification.get("params")
    turn = params.get("turn") if isinstance(params, dict) else None
    error = turn.get("error") if isinstance(turn, dict) else None
    error = error if isinstance(error, dict) else {}
    info = error.get("codexErrorInfo")
    info_key = (
        info
        if isinstance(info, str)
        else next(iter(info), "other")
        if isinstance(info, dict)
        else "other"
    )
    code_map = {
        "rateLimitExceeded": ("haas_rate_limited", True),
        "usageLimitExceeded": ("haas_rate_limited", True),
        "serverOverloaded": ("haas_provider_error", True),
        "internalServerError": ("haas_provider_error", True),
        "httpConnectionFailed": ("haas_provider_error", True),
        "responseStreamConnectionFailed": ("haas_provider_error", True),
        "responseStreamDisconnected": ("haas_provider_error", True),
        "responseTooManyFailedAttempts": ("haas_provider_error", True),
        "unauthorized": ("haas_model_proxy_token_invalid", True),
        "contextWindowExceeded": ("haas_model_unavailable", False),
        "sessionBudgetExceeded": ("haas_model_unavailable", False),
    }
    code, retryable = code_map.get(str(info_key), ("haas_provider_error", False))
    message = str(error.get("message") or "Codex turn failed")
    return code, safe_upstream_body(message), retryable


def _disabled_tools(policy: dict[str, Any]) -> list[str]:
    """Return the policy-declared disabled tool names (advisory enforcement).

    Codex cannot hard-block individual tools, so a disabled tool is surfaced
    to the model as an instruction (``toolRestriction="advisory"``).
    """
    tools = policy.get("tools")
    if not isinstance(tools, dict):
        return []
    disabled = tools.get("disabled")
    if not isinstance(disabled, list):
        return []
    return [str(name) for name in disabled if isinstance(name, str) and name]


class HaaSTurnInputInvalid(Exception):
    """Raised when northbound input fails adapter-side validation.

    Carries a stable structured error code (``haas_input_invalid``) so the
    sessions/API layer can map it without inspecting harness-native text.
    """

    def __init__(self, detail: str) -> None:
        self.code = "haas_input_invalid"
        super().__init__(f"haas_input_invalid: {detail}")


def _to_codex_input(
    items: list[Any], *, allow_native_passthrough: bool = False
) -> list[JsonObject]:
    """Convert HaaS/ADK input items to Codex app-server ``turn/start`` input.

    The SessionRuntime passes ADK message objects (``{"role": "user",
    "parts": [{"text": "..."}]}``) as the ``input`` list.  Codex app-server
    expects ``[{"type": "text", "text": "..."}]``.  This function normalises
    ADK messages into the Codex format, keeping the conversion inside the
    adapter (adapter isolation, 铁律 #5).

    Native Codex items (``{"type": ...}``) are only accepted when the caller
    explicitly opts in via ``allow_native_passthrough=True`` (internal
    delegation / test fixtures). On the northbound ADK path they are rejected
    with :class:`HaaSTurnInputInvalid` rather than silently trusted (P0-4
    capability honesty).
    """
    if not items:
        return [{"type": "text", "text": ""}]

    codex_input: list[JsonObject] = []
    for item in items:
        if not isinstance(item, dict):
            continue

        # Already in Codex native format: {"type": "text", "text": "..."}.
        # Rejected on the northbound path unless explicitly permitted.
        if "type" in item:
            if not allow_native_passthrough:
                raise HaaSTurnInputInvalid(
                    "native harness input items are not accepted on the northbound path"
                )
            codex_input.append(item)
            continue

        # ADK message format: {"role": "user", "parts": [{"text": "..."}, ...]}
        parts = item.get("parts")
        if isinstance(parts, list):
            extra = set(item) - {"role", "parts"}
            if extra:
                raise HaaSTurnInputInvalid(
                    f"unexpected input fields: {sorted(extra)!r}"
                )
            for part in parts:
                if not isinstance(part, dict):
                    continue
                text = part.get("text")
                if isinstance(text, str):
                    codex_input.append({"type": "text", "text": text})
            continue

        # Simplified format: {"text": "..."} (used in some tests).
        text = item.get("text")
        if isinstance(text, str):
            codex_input.append({"type": "text", "text": text})
            continue

    return codex_input or [{"type": "text", "text": ""}]


class CodexAdapter:
    """Harness adapter driving a Codex app-server runtime."""

    base = "codex"
    adapter_id = "codex-app-server"
    version = "0.1.0"

    def __init__(
        self,
        endpoint: CodexEndpoint,
        *,
        codex_bin: str = "codex",
        request_timeout: float = 60.0,
    ) -> None:
        self._endpoint = endpoint
        self._codex_bin = codex_bin
        self._rpc = CodexJsonRpc(endpoint, codex_bin=codex_bin, request_timeout=request_timeout)
        self._connect_lock = asyncio.Lock()
        self._session_threads: dict[SessionScope, str] = {}
        self._turn_contexts: dict[str, _TurnContext] = {}
        self._turn_index: dict[str, str] = {}  # codexTurnId -> turnId
        self._session_cwds: dict[tuple[str, str, str], str] = {}
        self._generation = 0
        self._non_resumable: set[SessionScope] = set()
        self._interaction_store: MemoryStore | None = None
        self._pending_interactions: set[str] = set()
        self._execution_evidence: ExecutionEvidenceStore | None = None
        self._command_evidence_refs: dict[tuple[str, str], str] = {}
        self._command_evidence_output: dict[tuple[str, str], str] = {}
        self._command_evidence_truncated: set[tuple[str, str]] = set()

    def bind_interaction_store(self, store: MemoryStore) -> None:
        self._interaction_store = store

    def bind_execution_evidence_store(self, store: ExecutionEvidenceStore) -> None:
        self._execution_evidence = store

    async def close(self) -> None:
        await self._rpc.close()
        self._turn_contexts.clear()
        self._turn_index.clear()
        self._command_evidence_output.clear()
        self._command_evidence_truncated.clear()
        self._command_evidence_refs.clear()

    # --- probe / declaration ------------------------------------------------

    async def probe(self) -> AdapterProbe:
        safe_details: dict[str, Any] = {}
        try:
            version = await asyncio.to_thread(codex_cli_version, self._codex_bin)
        except Exception as exc:  # noqa: BLE001 - probe must fail closed, never raise
            return AdapterProbe(
                adapterId=self.adapter_id,
                base=self.base,
                status="unavailable",
                runtimeVersion="unknown",
                transport=self._endpoint.transport,
                capabilities=self._capabilities(),
                safeDetails={"safeReason": f"codex_binary_unavailable: {exc}"},
            )
        try:
            fixture = load_fixture(version)
            current = await asyncio.to_thread(generate_schema_files, self._codex_bin)
            drift = schema_drift(fixture, current)
            if drift:
                safe_details["safeReason"] = "schema_mismatch"
                safe_details["schemaDrift"] = drift[:20]
        except FileNotFoundError:
            # Fixture not shipped with production installs; schema gate runs in CI.
            safe_details["schemaCheck"] = "skipped"
        except Exception as exc:  # noqa: BLE001
            safe_details["safeReason"] = f"schema_probe_failed: {exc}"

        # The binary being installed does not mean a turn can start: for a
        # socket transport the app-server endpoint must actually be reachable,
        # otherwise /ready would claim we can accept sessions that would fail
        # (specs/container-runtime: /health is liveness, /ready is capacity).
        endpoint_reason = self._endpoint_unavailable_reason()
        if endpoint_reason and "safeReason" not in safe_details:
            safe_details["safeReason"] = endpoint_reason

        if "safeReason" not in safe_details:
            readiness_rpc = CodexJsonRpc(
                self._endpoint,
                codex_bin=self._codex_bin,
                request_timeout=self._rpc.request_timeout,
            )
            try:
                await readiness_rpc.connect()
            except Exception:
                safe_details["safeReason"] = "codex_readiness_probe_failed"
            finally:
                await readiness_rpc.close()

        status = "unavailable" if "safeReason" in safe_details else "ready"
        return AdapterProbe(
            adapterId=self.adapter_id,
            base=self.base,
            status=status,
            runtimeVersion=f"codex-cli {version}",
            transport=self._endpoint.transport,
            capabilities=self._capabilities(),
            safeDetails=safe_details,
        )

    async def probe_readiness(self) -> AdapterProbe:
        """Run only the bounded Codex handshake required for service readiness."""
        safe_details: dict[str, Any] = {}
        endpoint_reason = self._endpoint_unavailable_reason()
        if endpoint_reason is not None:
            safe_details["safeReason"] = endpoint_reason
        if not safe_details:
            readiness_rpc = CodexJsonRpc(
                self._endpoint,
                codex_bin=self._codex_bin,
                request_timeout=self._rpc.request_timeout,
            )
            try:
                await readiness_rpc.connect()
            except Exception:
                safe_details["safeReason"] = "codex_readiness_probe_failed"
            finally:
                await readiness_rpc.close()
        return AdapterProbe(
            adapterId=self.adapter_id,
            base=self.base,
            status="ready" if not safe_details else "unavailable",
            runtimeVersion="unknown",
            transport=self._endpoint.transport,
            capabilities=self._capabilities(),
            safeDetails=safe_details,
        )

    def _endpoint_unavailable_reason(self) -> str | None:
        """Return a safe reason when the configured transport cannot be used.

        Only the socket path is checked: stdio spawns its own process, and a
        loopback WebSocket is dialled lazily per turn.
        """
        transport = self._endpoint.transport
        if transport not in {"unix_websocket", "unix"}:
            return None
        listen_url = self._endpoint.listen_url or ""
        path = listen_url[len("unix://") :] if listen_url.startswith("unix://") else ""
        if not path:
            return "codex_socket_not_configured"
        return None if os.path.exists(path) else "codex_socket_unavailable"

    def _capabilities(self) -> dict[str, Any]:
        return {
            "streaming": True,
            "sessionContinuation": "native",
            "pausing": "native",
            "cancellation": "best_effort",
            "approval": "human_bridge",
            "input": "human_bridge",
            "toolRestriction": "advisory",
            "mcp": "builtin_recall_only",
            "skills": "unsupported",
            "files": "unsupported",
            "usage": "native",
        }

    def sandbox_declaration(self) -> HarnessSandboxDecl:
        return HarnessSandboxDecl(
            cwd=DEFAULT_CWD,
            writableRoots=[DEFAULT_CWD],
            approvalMode=DEFAULT_APPROVAL_POLICY,
        )

    def _request_scope(
        self, session_id: str, app_name: str = "", user_id: str = ""
    ) -> SessionScope:
        requested = (app_name, user_id, session_id)
        if app_name or user_id:
            return requested
        known = {
            key
            for key in (*self._session_threads, *self._session_cwds, *self._non_resumable)
            if key[2] == session_id
        }
        return next(iter(known)) if len(known) == 1 else requested

    # --- session lifecycle --------------------------------------------------

    async def prepare_session(self, request: PrepareSessionRequest) -> PreparedSession:
        await self._ensure_connected()
        return PreparedSession(
            sessionId=request.sessionId,
            nativeRef={"connected": True, "generation": self._generation},
        )

    async def resume_session(self, request: ResumeSessionRequest) -> PreparedSession:
        await self._ensure_connected()
        scope = self._request_scope(request.sessionId, request.appName, request.userId)
        thread_id = self._session_threads.get(scope, "")
        if not thread_id:
            opaque = request.opaque or {}
            thread_id = str(opaque.get("threadId", ""))
        if thread_id:
            try:
                resumed = await self._resume_thread(thread_id, scope)
            except CodexConnectionError:
                resumed = ""
            if resumed:
                return PreparedSession(
                    sessionId=request.sessionId,
                    nativeRef={"threadId": resumed, "generation": self._generation},
                )
        return PreparedSession(
            sessionId=request.sessionId,
            nativeRef={"nonResumable": True, "generation": self._generation},
        )

    async def inspect_session(self, request: InspectSessionRequest) -> SessionInspection:
        scope = self._request_scope(request.sessionId, request.appName, request.userId)
        thread_id = self._session_threads.get(scope, "")
        if scope in self._non_resumable:
            return SessionInspection(status="non_resumable")
        if not thread_id:
            return SessionInspection(status="unknown")
        try:
            await self._ensure_connected()
            resumed = await self._resume_thread(thread_id, scope)
        except CodexConnectionError:
            resumed = ""
        if resumed:
            return SessionInspection(
                status="active",
                nativeRef={"threadId": resumed, "generation": self._generation},
            )
        self._non_resumable.add(scope)
        return SessionInspection(status="non_resumable")

    async def cleanup_session(self, request: CleanupSessionRequest) -> CleanupResult:
        scope = self._request_scope(request.sessionId, request.appName, request.userId)
        self._session_threads.pop(scope, None)
        self._session_cwds.pop(scope, None)
        self._non_resumable.discard(scope)
        return CleanupResult(status="cleaned")

    # --- turn lifecycle -----------------------------------------------------

    async def start_turn(self, request: StartTurnRequest) -> TurnHandle:
        await self._ensure_connected()

        sandbox = request.sandbox
        mode = sandbox.mode
        cwd = str(sandbox.workspaceRoot or DEFAULT_CWD)
        scope = (request.appName, request.userId, request.sessionId)
        legacy_scope = ("", "", request.sessionId)
        if scope not in self._session_threads and legacy_scope in self._session_threads:
            self._session_threads[scope] = self._session_threads.pop(legacy_scope)
        if scope not in self._non_resumable and legacy_scope in self._non_resumable:
            self._non_resumable.remove(legacy_scope)
            self._non_resumable.add(scope)
        self._session_cwds[scope] = cwd
        writable_roots = sandbox.writableRoots or [cwd]
        if not isinstance(writable_roots, list):
            writable_roots = [cwd]
        network_policy = (
            sandbox.network
            if sandbox.network is not None
            else request.policy.get("network")
        )

        thread_id = self._session_threads.get(scope, "")
        if thread_id:
            thread_id = await self._resume_or_drop_thread(
                thread_id, scope, cwd, request
            )
        if not thread_id:
            thread_id = await self._start_thread(scope, cwd, mode, request)

        instructions = request.instructions
        disabled_tools = _disabled_tools(request.policy)
        if disabled_tools:
            notice = (
                "Do not use the following tools: "
                + ", ".join(disabled_tools)
                + ". They are disabled by policy."
            )
            instructions = f"{instructions}\n\n{notice}" if instructions else notice

        turn_params: JsonObject = {
            "threadId": thread_id,
            "input": _to_codex_input(request.input),
            "sandboxPolicy": to_turn_sandbox_policy(mode, writable_roots, network_policy),
            "approvalPolicy": request.policy.get("approvalPolicy", DEFAULT_APPROVAL_POLICY),
            "cwd": cwd,
        }
        if instructions:
            turn_params["instructions"] = instructions
        if request.model:
            turn_params["model"] = request.model
        notification_cursor = self._rpc.notification_cursor
        server_request_cursor = self._rpc.server_request_cursor
        try:
            turn_result = await self._rpc.request("turn/start", turn_params)
        except CodexRequestTimeout as exc:
            raise AdapterTurnStartError(
                "haas_request_timeout",
                retryable=True,
                detail="turn/start did not respond in time",
            ) from exc
        except CodexConnectionError as exc:
            raise AdapterTurnStartError(
                "haas_adapter_unavailable",
                retryable=True,
                detail="turn/start connection failure",
            ) from exc
        turn = turn_result.get("turn", {})
        codex_turn_id = (
            str(turn.get("id", request.turnId)) if isinstance(turn, dict) else request.turnId
        )
        if not codex_turn_id:
            codex_turn_id = request.turnId

        ctx = _TurnContext(
            turn_id=request.turnId,
            session_id=request.sessionId,
            invocation_id=request.invocationId,
            thread_id=thread_id,
            codex_turn_id=codex_turn_id,
            timeout_seconds=request.timeoutSeconds,
            principal_id=request.principalId,
            user_id=request.userId,
            app_name=request.appName,
            notification_cursor=notification_cursor,
            server_request_cursor=server_request_cursor,
        )
        self._turn_contexts[request.turnId] = ctx
        self._turn_index[codex_turn_id] = request.turnId
        return TurnHandle(
            turnId=request.turnId,
            sessionId=request.sessionId,
            invocationId=request.invocationId,
            opaque={
                "adapterId": self.adapter_id,
                "threadId": thread_id,
                "codexTurnId": codex_turn_id,
                "generation": self._generation,
            },
        )

    async def stream_events(self, handle: TurnHandle) -> AsyncIterator[HarnessEvent]:
        ctx = self._turn_contexts.get(handle.turnId)
        if ctx is None:
            raise CodexConnectionError(f"unknown turn handle: {handle.turnId}")

        timeout_at = ctx.started_at + ctx.timeout_seconds
        notifications = self._rpc.notifications(after=ctx.notification_cursor)
        server_requests = self._rpc.server_requests(after=ctx.server_request_cursor)
        notification_task: asyncio.Future[JsonObject] = asyncio.ensure_future(anext(notifications))
        request_task: asyncio.Future[JsonObject] = asyncio.ensure_future(anext(server_requests))
        try:
            while True:
                remaining = timeout_at - time.monotonic()
                if remaining <= 0:
                    # Best-effort interrupt before failing the turn.
                    with contextlib.suppress(CodexConnectionError):
                        await self._rpc.request(
                            "turn/interrupt",
                            {"threadId": ctx.thread_id, "turnId": ctx.codex_turn_id},
                        )
                    terminal = self._terminal_harness_event(
                        ctx,
                        "failed",
                        code="haas_request_timeout",
                        safe_reason="long_task_deadline_exceeded",
                        retryable=True,
                    )
                    ctx.terminal_status = "failed"
                    ctx.terminal_event = terminal
                    yield terminal
                    return

                if not self._rpc.connected:
                    break

                done, _pending = await asyncio.wait(
                    {notification_task, request_task},
                    timeout=min(remaining, 5.0),
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if not done:
                    continue
                if request_task in done:
                    try:
                        request = request_task.result()
                    except CodexSubscriberOverloaded:
                        terminal = self._terminal_harness_event(
                            ctx, "failed", code="haas_adapter_overloaded",
                            safe_reason="Codex event consumer could not keep up",
                            retryable=True,
                        )
                        ctx.terminal_status = "failed"
                        ctx.terminal_event = terminal
                        yield terminal
                        return
                    request_task = asyncio.ensure_future(anext(server_requests))
                    event = self._persist_server_request(request, ctx)
                    if event is not None:
                        yield event
                    continue

                try:
                    notification = notification_task.result()
                except CodexSubscriberOverloaded:
                    terminal = self._terminal_harness_event(
                        ctx, "failed", code="haas_adapter_overloaded",
                        safe_reason="Codex event consumer could not keep up",
                        retryable=True,
                    )
                    ctx.terminal_status = "failed"
                    ctx.terminal_event = terminal
                    yield terminal
                    return
                notification_task = asyncio.ensure_future(anext(notifications))
                if not self._belongs_to_turn(notification, ctx):
                    continue

                status = _terminal_status(notification)
                if status is not None:
                    details: tuple[str | None, str | None, bool | None] = (None, None, None)
                    if status == "failed":
                        details = _terminal_failure_details(notification)
                    terminal = self._terminal_harness_event(
                        ctx,
                        status,
                        code=details[0],
                        safe_reason=details[1],
                        retryable=details[2],
                    )
                    ctx.terminal_status = status
                    ctx.terminal_event = terminal
                    yield terminal
                    return

                event = self._to_harness_event(notification, ctx)
                if event is not None:
                    yield event
        finally:
            for task in (notification_task, request_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(notification_task, request_task, return_exceptions=True)
            await notifications.aclose()
            await server_requests.aclose()

        # Connection dropped or stream ended without a terminal notification.
        terminal = self._terminal_harness_event(
            ctx,
            "incomplete",
            code="haas_adapter_unavailable",
            safe_reason=(
                self._rpc.connection_failure_reason
                or "Codex app-server connection ended before the turn completed"
            ),
            retryable=True,
        )
        ctx.terminal_status = "incomplete"
        ctx.terminal_event = terminal
        yield terminal

    def _persist_server_request(
        self, request: JsonObject, ctx: _TurnContext
    ) -> HarnessEvent | None:
        if self._interaction_store is None or not self._belongs_to_turn(request, ctx):
            return None
        method = str(request.get("method") or "")
        native_id = request.get("id")
        if native_id is None:
            return None
        params = request.get("params")
        params = params if isinstance(params, dict) else {}
        digest = hashlib.sha256(
            f"{self._generation}:{ctx.invocation_id}:{native_id}:{method}".encode()
        ).hexdigest()[:20]
        remaining_seconds = max(0.0, ctx.started_at + ctx.timeout_seconds - time.monotonic())
        expires_at_ms = int(time.time() * 1000 + remaining_seconds * 1000)
        if method in _APPROVAL_METHODS:
            record = self._interaction_store.put_approval(
                ApprovalRecord(
                    id=f"appr_{digest}",
                    sessionId=ctx.session_id,
                    invocationId=ctx.invocation_id,
                    turnId=ctx.turn_id,
                    request={
                        "kind": _APPROVAL_METHODS[method],
                        "safeSummary": (
                            "Run command" if method.startswith("item/command") else "Change files"
                        ),
                        "availableDecisions": ["approved", "denied", "cancelled"],
                        "policyReason": "harness_requested",
                        "expiresAtMs": expires_at_ms,
                    },
                    nativeRequestId=native_id,
                    adapterGeneration=self._generation,
                    expiresAtMs=expires_at_ms,
                )
            )
            self._pending_interactions.add(record.id)
            return HarnessEvent(
                type="haas.approval.required",
                invocationId=ctx.invocation_id,
                sessionId=ctx.session_id,
                turnId=ctx.turn_id,
                author=self.base,
                content={"role": "model", "parts": []},
                actions={"haas": {"approvalId": record.id, **record.request}},
            )
        if method == "item/tool/requestUserInput":
            questions = params.get("questions")
            safe_questions = []
            if isinstance(questions, list):
                for question in questions:
                    if not isinstance(question, dict):
                        continue
                    safe_questions.append(
                        {
                            key: question[key]
                            for key in (
                                "id",
                                "header",
                                "question",
                                "options",
                                "isSecret",
                            )
                            if key in question
                        }
                    )
            input_record = self._interaction_store.put_input_request(
                InputRequestRecord(
                    id=f"inreq_{digest}",
                    sessionId=ctx.session_id,
                    invocationId=ctx.invocation_id,
                    turnId=ctx.turn_id,
                    questions=safe_questions,
                    blocking=bool(params.get("isBlocking", True)),
                    expiresAtMs=expires_at_ms,
                    nativeRequestId=native_id,
                    adapterGeneration=self._generation,
                )
            )
            self._pending_interactions.add(input_record.id)
            return HarnessEvent(
                type="haas.input.required",
                invocationId=ctx.invocation_id,
                sessionId=ctx.session_id,
                turnId=ctx.turn_id,
                author=self.base,
                content={"role": "model", "parts": []},
                actions={
                    "haas": {
                        "inputRequestId": input_record.id,
                        "questions": safe_questions,
                        "blocking": input_record.blocking,
                        "expiresAtMs": expires_at_ms,
                    }
                },
            )
        return None

    async def respond_interaction(
        self, record: ApprovalRecord | InputRequestRecord, payload: dict[str, Any]
    ) -> None:
        if record.adapterGeneration != self._generation:
            raise CodexConnectionError("interaction adapter generation is stale")
        if record.id not in self._pending_interactions:
            raise CodexConnectionError("interaction is not pending on this connection")
        if isinstance(record, ApprovalRecord):
            mapping = {"approved": "accept", "denied": "decline", "cancelled": "cancel"}
            decision = payload.get("decision")
            if decision not in mapping:
                raise CodexConnectionError("invalid_interaction_decision")
            result: JsonObject = {"decision": mapping[decision]}
        else:
            supplied = payload.get("answers") or {}
            result = {
                "answers": {
                    key: {"answers": value.get("values", [])}
                    for key, value in supplied.items()
                    if isinstance(value, dict)
                }
            }
        await self._rpc.respond(record.nativeRequestId, result)
        self._pending_interactions.discard(record.id)

    async def finalize_turn(self, handle: TurnHandle) -> AdapterTurnResult:
        ctx = self._turn_contexts.pop(handle.turnId, None)
        self._clear_command_evidence_state(handle.turnId)
        if ctx is not None:
            self._turn_index.pop(ctx.codex_turn_id, None)
            if ctx.terminal_status is not None:
                return AdapterTurnResult(
                    status=ctx.terminal_status, terminalEvent=ctx.terminal_event
                )
        return AdapterTurnResult(status="incomplete")

    def _clear_command_evidence_state(self, turn_id: str) -> None:
        for key in [key for key in self._command_evidence_output if key[0] == turn_id]:
            self._command_evidence_output.pop(key, None)
            self._command_evidence_truncated.discard(key)
            self._command_evidence_refs.pop(key, None)

    async def _ensure_connected(self) -> None:
        """Connect (or reconnect) and re-initialize; bump generation on reconnect."""
        if self._rpc.connected and self._rpc.initialized:
            return
        async with self._connect_lock:
            if self._rpc.connected and self._rpc.initialized:
                return
            self._generation += 1
            await self._rpc.connect()

    async def _start_thread(
        self,
        scope: SessionScope,
        cwd: str,
        mode: str,
        request: StartTurnRequest,
    ) -> str:
        thread_params: JsonObject = {
            "cwd": cwd,
            "sandbox": to_thread_sandbox_mode(mode),
            "approvalPolicy": request.policy.get("approvalPolicy", DEFAULT_APPROVAL_POLICY),
        }
        if request.model:
            thread_params["model"] = request.model
        thread_params.update(self._model_overrides(request))
        mcp_servers = self._mcp_server_overrides(request)
        if mcp_servers:
            config = thread_params.setdefault("config", {})
            if isinstance(config, dict):
                config["mcp_servers"] = mcp_servers
        thread_result = await self._rpc.request("thread/start", thread_params)
        thread = thread_result.get("thread", {})
        thread_id = str(thread.get("id", "")) if isinstance(thread, dict) else ""
        if not thread_id:
            raise CodexConnectionError("thread/start returned no thread id")
        self._session_threads[scope] = thread_id
        self._non_resumable.discard(scope)
        return thread_id

    async def _resume_or_drop_thread(
        self,
        thread_id: str,
        scope: SessionScope,
        cwd: str,
        request: StartTurnRequest | None = None,
    ) -> str:
        """Validate an existing thread via thread/resume; drop it if it is gone."""
        try:
            return await self._resume_thread(thread_id, scope, cwd, request)
        except CodexConnectionError as exc:
            if not _is_thread_not_found(exc):
                raise
        self._session_threads.pop(scope, None)
        return ""

    @staticmethod
    def _model_overrides(request: StartTurnRequest) -> JsonObject:
        if not request.credentials:
            return {}
        base_url = request.credentials["baseUrl"]
        token = request.credentials["token"]
        return {
            "model": request.model,
            "modelProvider": "haas",
            "config": {
                "features.multi_agent": False,
                "features.default_mode_request_user_input": (
                    request.policy.get("approvalPolicy") == "on-request"
                ),
                "web_search": "disabled",
                "model_reasoning_summary": "auto",
                "model_providers.haas": {
                    "name": "HaaS",
                    "base_url": base_url,
                    "wire_api": "responses",
                    "requires_openai_auth": False,
                    "http_headers": {"Authorization": f"Bearer {token}"},
                },
            },
        }

    @staticmethod
    def _mcp_server_overrides(request: StartTurnRequest) -> JsonObject:
        servers: JsonObject = {}
        for server in request.mcpServers:
            data = server.model_dump(mode="json") if isinstance(server, McpServerConfig) else server
            if not isinstance(data, dict):
                continue
            if (
                data.get("name") != BUILTIN_COWORK_RECALL_MCP_NAME
                or data.get("haas_builtin") is not True
                or data.get("transport") != "http"
            ):
                continue
            url = data.get("url")
            if not isinstance(url, str) or not url.startswith("http://127.0.0.1:"):
                continue
            raw_headers = data.get("headers")
            if not isinstance(raw_headers, dict):
                continue
            headers: dict[str, Any] = raw_headers
            if not (
                isinstance(headers.get("X-HaaS-Session-ID"), str)
                and headers["X-HaaS-Session-ID"]
                and isinstance(headers.get("X-HaaS-Recall-Token"), str)
                and headers["X-HaaS-Recall-Token"]
            ):
                continue
            servers[BUILTIN_COWORK_RECALL_MCP_NAME] = {
                "url": url,
                "http_headers": {
                    key: value
                    for key, value in headers.items()
                    if isinstance(key, str) and isinstance(value, str)
                },
                "enabled": bool(data.get("enabled", True)),
                "required": bool(data.get("required", False)),
                "tool_timeout_sec": float(data.get("timeoutSeconds") or 10),
                "enabled_tools": ["recall"],
            }
        return servers

    async def _resume_thread(
        self,
        thread_id: str,
        scope: SessionScope,
        cwd: str = DEFAULT_CWD,
        request: StartTurnRequest | None = None,
    ) -> str:
        resume_params: JsonObject = {
            "threadId": thread_id,
            "cwd": cwd,
            "excludeTurns": True,
        }
        if request is not None:
            if request.credentials:
                await self._rpc.request("thread/unsubscribe", {"threadId": thread_id})
            resume_params.update(self._model_overrides(request))
            mcp_servers = self._mcp_server_overrides(request)
            if mcp_servers:
                config = resume_params.setdefault("config", {})
                if isinstance(config, dict):
                    config["mcp_servers"] = mcp_servers
        resume_result = await self._rpc.request("thread/resume", resume_params)
        thread = resume_result.get("thread", {})
        resumed = str(thread.get("id", "")) if isinstance(thread, dict) else ""
        if resumed:
            self._session_threads[scope] = resumed
        return resumed or thread_id

    async def cancel_turn(self, request: CancelTurnRequest) -> CancelResult:
        ctx = self._turn_contexts.get(request.turnId)
        if ctx is None or not self._rpc.connected:
            return CancelResult(status="accepted")
        try:
            await self._rpc.request(
                "turn/interrupt",
                {"threadId": ctx.thread_id, "turnId": ctx.codex_turn_id},
            )
            return CancelResult(status="accepted")
        except CodexConnectionError:
            return CancelResult(status="accepted")

    # --- artifacts ----------------------------------------------------------

    async def list_artifacts(self, request: ListArtifactsRequest) -> list[ArtifactRef]:
        return await asyncio.to_thread(
            self._list_artifacts_sync, request.appName, request.userId, request.sessionId
        )

    def _list_artifacts_sync(
        self, app_name: str, user_id: str, session_id: str
    ) -> list[ArtifactRef]:
        cwd = self._session_cwds.get((app_name, user_id, session_id))
        if cwd is None:
            return []
        workspace_root = Path(cwd).resolve()
        output_candidate = workspace_root / "output"
        if output_candidate.is_symlink() or not output_candidate.is_dir():
            return []
        refs: list[ArtifactRef] = []
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            output_fd = os.open(
                output_candidate,
                flags | getattr(os, "O_DIRECTORY", 0),
            )
        except OSError:
            return []
        try:
            for root, dirnames, filenames, dir_fd in os.fwalk(
                ".", topdown=True, follow_symlinks=False, dir_fd=output_fd
            ):
                dirnames[:] = sorted(name for name in dirnames if not name.startswith("."))
                for filename in sorted(filenames):
                    if len(refs) >= 20:
                        return refs
                    relative_path = Path(root, filename)
                    if filename.startswith("."):
                        continue
                    try:
                        file_fd = os.open(filename, flags, dir_fd=dir_fd)
                        with os.fdopen(file_fd, "rb") as file_obj:
                            metadata = os.fstat(file_obj.fileno())
                            if (
                                not stat.S_ISREG(metadata.st_mode)
                                or metadata.st_size > MAX_ARTIFACT_BYTES
                            ):
                                continue
                            content = file_obj.read(MAX_ARTIFACT_BYTES + 1)
                    except OSError:
                        continue
                    if len(content) > MAX_ARTIFACT_BYTES:
                        continue
                    normalized = relative_path.as_posix().removeprefix("./")
                    refs.append(
                        ArtifactRef(
                            name=filename,
                            path=f"output/{normalized}",
                            content=content,
                        )
                    )
        finally:
            os.close(output_fd)
        return refs

    # --- event normalization ------------------------------------------------

    def _belongs_to_turn(self, notification: JsonObject, ctx: _TurnContext) -> bool:
        thread_id = _notification_thread_id(notification)
        turn_id = _notification_turn_id(notification)
        if thread_id and thread_id != ctx.thread_id:
            return False
        return not (turn_id and turn_id != ctx.codex_turn_id)

    def _terminal_harness_event(
        self,
        ctx: _TurnContext,
        status: str,
        *,
        code: str | None = None,
        safe_reason: str | None = None,
        retryable: bool | None = None,
    ) -> HarnessEvent:
        state: JsonObject = {"status": status}
        if code is not None:
            state["code"] = code
        if safe_reason is not None:
            state["reason"] = safe_reason
        if retryable is not None:
            state["retryable"] = retryable
        return HarnessEvent(
            type=f"harness.turn.{status}",
            invocationId=ctx.invocation_id,
            sessionId=ctx.session_id,
            turnId=ctx.turn_id,
            author=self.base,
            content={"role": "model", "parts": []},
            actions={"stateDelta": state},
        )

    def _to_harness_event(self, notification: JsonObject, ctx: _TurnContext) -> HarnessEvent | None:
        model_call_id = self._model_call_id(notification, ctx)
        params = notification.get("params")
        item = params.get("item") if isinstance(params, dict) else None
        if isinstance(item, dict) and item.get("type") == "commandExecution":
            tool_call_id = str(item.get("id") or "")
            command = command_text(item.get("command"))
            if self._execution_evidence is not None and tool_call_id and command:
                key = (ctx.turn_id, tool_call_id)
                method = notification_method(notification)
                if method == "item/started":
                    self._command_evidence_output[key] = ""
                    self._command_evidence_truncated.discard(key)
                accumulated = self._command_evidence_output.get(key, "")
                aggregated = item.get("aggregatedOutput")
                aggregated = aggregated if isinstance(aggregated, str) else ""
                # Codex normally supplies the complete aggregate, but a terminal
                # frame may contain only a tail/summary. Never replace more complete
                # delta evidence with a shorter terminal representation.
                output = max(
                    (accumulated, aggregated), key=lambda value: len(value.encode("utf-8"))
                )
                record = self._execution_evidence.put(
                    principal_id=ctx.principal_id,
                    app_name=ctx.app_name,
                    user_id=ctx.user_id,
                    session_id=ctx.session_id,
                    invocation_id=ctx.invocation_id,
                    tool_call_id=tool_call_id,
                    command=command,
                    working_directory=str(item.get("cwd") or DEFAULT_CWD),
                    output=output,
                    evidence_ref=self._command_evidence_refs.get(key),
                )
                self._command_evidence_refs[key] = record.evidenceRef
                if method == "item/completed":
                    self._command_evidence_output.pop(key, None)
                    self._command_evidence_truncated.discard(key)
                    self._command_evidence_refs.pop(key, None)
                normalized = normalize_notification(
                    notification,
                    invocation_id=ctx.invocation_id,
                    session_id=ctx.session_id,
                    turn_id=ctx.turn_id,
                    author=self.base,
                    model_call_id=model_call_id,
                    evidence_ref=record.evidenceRef,
                    evidence_expires_at_ms=record.expiresAtMs,
                )
                return normalized
        if (
            self._execution_evidence is not None
            and notification_method(notification) == "item/commandExecution/outputDelta"
        ):
            delta_params = notification_params(notification)
            tool_call_id = str(delta_params.get("itemId") or "")
            evidence_ref = self._command_evidence_refs.get((ctx.turn_id, tool_call_id))
            delta = delta_params.get("delta")
            if evidence_ref and isinstance(delta, str) and delta:
                key = (ctx.turn_id, tool_call_id)
                if key in self._command_evidence_truncated:
                    return normalize_notification(
                        notification, invocation_id=ctx.invocation_id,
                        session_id=ctx.session_id, turn_id=ctx.turn_id, author=self.base,
                        model_call_id=model_call_id,
                    )
                raw_output = self._command_evidence_output.get(key, "") + delta
                encoded = raw_output.encode("utf-8")
                if len(encoded) > MAX_EVIDENCE_BYTES:
                    raw_output = (
                        encoded[: MAX_EVIDENCE_BYTES - 18].decode("utf-8", errors="ignore")
                        + "\n...<truncated>\n"
                    )
                    self._command_evidence_truncated.add(key)
                self._command_evidence_output[key] = raw_output
                self._execution_evidence.update_output(evidence_ref, raw_output)
        normalized = normalize_notification(
            notification,
            invocation_id=ctx.invocation_id,
            session_id=ctx.session_id,
            turn_id=ctx.turn_id,
            author=self.base,
            model_call_id=model_call_id,
        )
        if notification_method(notification) in _TERMINAL_METHODS:
            self._clear_command_evidence_state(ctx.turn_id)
        return normalized

    def _model_call_id(self, notification: JsonObject, ctx: _TurnContext) -> str | None:
        """Correlate public process facts to a measured native model round trip."""
        method = notification_method(notification)
        params = notification_params(notification)
        item = params.get("item")
        item = item if isinstance(item, dict) else {}
        item_type = str(item.get("type") or "")
        item_id = str(params.get("itemId") or item.get("id") or "")

        if method in {"item/agentMessage/delta", "item/reasoning/summaryTextDelta"}:
            known = ctx.item_model_calls.get(item_id) if item_id else None
            if known is not None:
                return known
            if (
                ctx.current_model_call_metered
                and ctx.completed_tool_in_model_call
                and not ctx.active_tool_ids
            ):
                self._open_model_call(ctx)
            model_call_id = self._ensure_model_call(ctx)
            if item_id:
                ctx.item_model_calls[item_id] = model_call_id
            return model_call_id

        if method == "thread/tokenUsage/updated":
            model_call_id = self._ensure_model_call(ctx)
            ctx.current_model_call_metered = True
            return model_call_id

        if method == "item/started" and item_type in _TOOL_ITEM_TYPES:
            model_call_id = self._ensure_model_call(ctx)
            if item_id:
                ctx.tool_model_calls[item_id] = model_call_id
                ctx.active_tool_ids.add(item_id)
            return model_call_id

        if method == "item/completed" and item_type in _TOOL_ITEM_TYPES:
            model_call_id = ctx.tool_model_calls.get(item_id) or self._ensure_model_call(ctx)
            if item_id:
                ctx.tool_model_calls[item_id] = model_call_id
                ctx.active_tool_ids.discard(item_id)
            ctx.completed_tool_in_model_call = True
            return model_call_id

        if method in _TOOL_OUTPUT_NOTIFICATION_METHODS:
            model_call_id = ctx.tool_model_calls.get(item_id) or self._ensure_model_call(ctx)
            if item_id:
                ctx.tool_model_calls[item_id] = model_call_id
            return model_call_id

        if method == "item/completed" and item_type == "agentMessage":
            return ctx.item_model_calls.get(item_id) or self._ensure_model_call(ctx)
        return None

    @staticmethod
    def _open_model_call(ctx: _TurnContext) -> str:
        ctx.model_call_ordinal += 1
        ctx.current_model_call_id = f"mcall_{ctx.model_call_ordinal:04d}"
        ctx.current_model_call_metered = False
        ctx.completed_tool_in_model_call = False
        ctx.active_tool_ids.clear()
        return ctx.current_model_call_id

    def _ensure_model_call(self, ctx: _TurnContext) -> str:
        return ctx.current_model_call_id or self._open_model_call(ctx)
