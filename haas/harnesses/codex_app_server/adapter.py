"""Codex app-server harness adapter (specs/codex-app-server-adapter/).

Implements the :class:`HarnessAdapter` protocol over the Codex app-server
JSON-RPC transport. Native Codex thread/turn/notification semantics stay
inside this module (adapter isolation, AGENTS.md 铁律 #5).
"""
from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from haas.harnesses.base import (
    AdapterProbe,
    AdapterTurnResult,
    ArtifactRef,
    CancelResult,
    CancelTurnRequest,
    CleanupResult,
    CleanupSessionRequest,
    HarnessEvent,
    HarnessSandboxDecl,
    InspectSessionRequest,
    ListArtifactsRequest,
    PreparedSession,
    PrepareSessionRequest,
    ResumeSessionRequest,
    SessionInspection,
    StartTurnRequest,
    TurnHandle,
)
from haas.harnesses.codex_app_server.normalizer import normalize_notification
from haas.harnesses.codex_app_server.rpc import (
    CodexConnectionError,
    CodexJsonRpc,
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

JsonObject = dict[str, Any]

DEFAULT_CWD = "/workspace"
DEFAULT_APPROVAL_POLICY = "never"
DEFAULT_TIMEOUT_SECONDS = 900.0

# Codex notification methods that terminate a turn, mapped to canonical status.
_TERMINAL_METHODS: dict[str, str] = {
    "turn/completed": "completed",
    "turn/failed": "failed",
    "turn/interrupted": "interrupted",
    "turn/cancelled": "cancelled",
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
        self._session_threads: dict[str, str] = {}
        self._turn_contexts: dict[str, _TurnContext] = {}
        self._turn_index: dict[str, str] = {}  # codexTurnId -> turnId
        self._generation = 0
        self._non_resumable: set[str] = set()

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

    def _endpoint_unavailable_reason(self) -> str | None:
        """Return a safe reason when the configured transport cannot be used.

        Only the socket path is checked: stdio spawns its own process, and a
        loopback WebSocket is dialled lazily per turn.
        """
        transport = self._endpoint.transport
        if transport not in {"unix_websocket", "unix"}:
            return None
        listen_url = self._endpoint.listen_url or ""
        path = listen_url[len("unix://"):] if listen_url.startswith("unix://") else ""
        if not path:
            return "codex_socket_not_configured"
        import os

        return None if os.path.exists(path) else "codex_socket_unavailable"

    def _capabilities(self) -> dict[str, Any]:
        return {
            "streaming": True,
            "sessionContinuation": "native",
            "cancellation": "best_effort",
            "toolRestriction": "advisory",
            "mcp": "unsupported",
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

    # --- session lifecycle --------------------------------------------------

    async def prepare_session(self, request: PrepareSessionRequest) -> PreparedSession:
        await self._ensure_connected()
        return PreparedSession(
            sessionId=request.sessionId,
            nativeRef={"connected": True, "generation": self._generation},
        )

    async def resume_session(self, request: ResumeSessionRequest) -> PreparedSession:
        await self._ensure_connected()
        thread_id = self._session_threads.get(request.sessionId, "")
        if not thread_id:
            opaque = request.opaque or {}
            thread_id = str(opaque.get("threadId", ""))
        if thread_id:
            try:
                resumed = await self._resume_thread(thread_id, request.sessionId)
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
        thread_id = self._session_threads.get(request.sessionId, "")
        if request.sessionId in self._non_resumable:
            return SessionInspection(status="non_resumable")
        if not thread_id:
            return SessionInspection(status="unknown")
        try:
            await self._ensure_connected()
            resumed = await self._resume_thread(thread_id, request.sessionId)
        except CodexConnectionError:
            resumed = ""
        if resumed:
            return SessionInspection(
                status="active",
                nativeRef={"threadId": resumed, "generation": self._generation},
            )
        self._non_resumable.add(request.sessionId)
        return SessionInspection(status="non_resumable")

    async def cleanup_session(self, request: CleanupSessionRequest) -> CleanupResult:
        self._session_threads.pop(request.sessionId, None)
        return CleanupResult(status="cleaned")

    # --- turn lifecycle -----------------------------------------------------

    async def start_turn(self, request: StartTurnRequest) -> TurnHandle:
        await self._ensure_connected()

        sandbox = request.sandbox
        mode = str(sandbox.get("mode", "workspace-write"))
        cwd = str(sandbox.get("workspaceRoot", DEFAULT_CWD))
        writable_roots = sandbox.get("writableRoots") or [cwd]
        if not isinstance(writable_roots, list):
            writable_roots = [cwd]

        thread_id = self._session_threads.get(request.sessionId, "")
        if thread_id:
            thread_id = await self._resume_or_drop_thread(
                thread_id, request.sessionId, cwd
            )
        if not thread_id:
            thread_id = await self._start_thread(request.sessionId, cwd, mode, request)

        turn_params: JsonObject = {
            "threadId": thread_id,
            "input": request.input or [{"type": "text", "text": ""}],
            "sandboxPolicy": to_turn_sandbox_policy(mode, writable_roots),
            "approvalPolicy": request.policy.get("approvalPolicy", DEFAULT_APPROVAL_POLICY),
            "cwd": cwd,
        }
        if request.model:
            turn_params["model"] = request.model
        turn_result = await self._rpc.request("turn/start", turn_params)
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
        )
        self._turn_contexts[request.turnId] = ctx
        self._turn_index[codex_turn_id] = request.turnId
        return TurnHandle(
            turnId=request.turnId,
            sessionId=request.sessionId,
            invocationId=request.invocationId,
            opaque={"threadId": thread_id, "codexTurnId": codex_turn_id},
        )

    async def stream_events(self, handle: TurnHandle) -> AsyncIterator[HarnessEvent]:
        ctx = self._turn_contexts.get(handle.turnId)
        if ctx is None:
            raise CodexConnectionError(f"unknown turn handle: {handle.turnId}")

        timeout_at = ctx.started_at + ctx.timeout_seconds
        notifications = self._rpc.notifications()
        while True:
            remaining = timeout_at - time.monotonic()
            if remaining <= 0:
                # Best-effort interrupt before failing the turn.
                with contextlib.suppress(CodexConnectionError):
                    await self._rpc.request(
                        "turn/interrupt",
                        {"threadId": ctx.thread_id, "turnId": ctx.codex_turn_id},
                    )
                terminal = self._terminal_harness_event(ctx, "failed")
                ctx.terminal_status = "failed"
                ctx.terminal_event = terminal
                yield terminal
                return

            if not self._rpc.connected:
                break

            try:
                async with asyncio.timeout(min(remaining, 5.0)):
                    notification = await anext(notifications)
            except TimeoutError:
                continue
            except StopAsyncIteration:
                break

            if not self._belongs_to_turn(notification, ctx):
                continue

            status = _terminal_status(notification)
            if status is not None:
                if status == "interrupted":
                    status = "cancelled"
                terminal = self._terminal_harness_event(ctx, status)
                ctx.terminal_status = status
                ctx.terminal_event = terminal
                yield terminal
                return

            event = self._to_harness_event(notification, ctx)
            if event is not None:
                yield event

        # Connection dropped or stream ended without a terminal notification.
        terminal = self._terminal_harness_event(ctx, "incomplete")
        ctx.terminal_status = "incomplete"
        ctx.terminal_event = terminal
        yield terminal

    async def finalize_turn(self, handle: TurnHandle) -> AdapterTurnResult:
        ctx = self._turn_contexts.pop(handle.turnId, None)
        if ctx is not None:
            self._turn_index.pop(ctx.codex_turn_id, None)
            if ctx.terminal_status is not None:
                return AdapterTurnResult(
                    status=ctx.terminal_status, terminalEvent=ctx.terminal_event
                )
        return AdapterTurnResult(status="incomplete")

    async def _ensure_connected(self) -> None:
        """Connect (or reconnect) and re-initialize; bump generation on reconnect."""
        if self._rpc.connected and self._rpc.initialized:
            return
        self._generation += 1
        await self._rpc.connect()

    async def _start_thread(
        self,
        session_id: str,
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
        thread_result = await self._rpc.request("thread/start", thread_params)
        thread = thread_result.get("thread", {})
        thread_id = str(thread.get("id", "")) if isinstance(thread, dict) else ""
        if not thread_id:
            raise CodexConnectionError("thread/start returned no thread id")
        self._session_threads[session_id] = thread_id
        self._non_resumable.discard(session_id)
        return thread_id

    async def _resume_or_drop_thread(
        self,
        thread_id: str,
        session_id: str,
        cwd: str,
    ) -> str:
        """Validate an existing thread via thread/resume; drop it if it is gone."""
        try:
            return await self._resume_thread(thread_id, session_id, cwd)
        except CodexConnectionError as exc:
            if not _is_thread_not_found(exc):
                raise
        self._session_threads.pop(session_id, None)
        return ""

    async def _resume_thread(self, thread_id: str, session_id: str, cwd: str = DEFAULT_CWD) -> str:
        resume_params: JsonObject = {"threadId": thread_id, "cwd": cwd}
        resume_result = await self._rpc.request("thread/resume", resume_params)
        thread = resume_result.get("thread", {})
        resumed = str(thread.get("id", "")) if isinstance(thread, dict) else ""
        if resumed:
            self._session_threads[session_id] = resumed
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
            return CancelResult(status="cancelled")
        except CodexConnectionError:
            return CancelResult(status="accepted")

    # --- artifacts ----------------------------------------------------------

    async def list_artifacts(self, request: ListArtifactsRequest) -> list[ArtifactRef]:
        return []

    # --- event normalization ------------------------------------------------

    def _belongs_to_turn(self, notification: JsonObject, ctx: _TurnContext) -> bool:
        thread_id = _notification_thread_id(notification)
        turn_id = _notification_turn_id(notification)
        if thread_id and thread_id != ctx.thread_id:
            return False
        return not (turn_id and turn_id != ctx.codex_turn_id)

    def _terminal_harness_event(self, ctx: _TurnContext, status: str) -> HarnessEvent:
        return HarnessEvent(
            type=f"harness.turn.{status}",
            invocationId=ctx.invocation_id,
            sessionId=ctx.session_id,
            turnId=ctx.turn_id,
            author=self.base,
            content={"role": "model", "parts": []},
            actions={"stateDelta": {"status": status}},
        )

    def _to_harness_event(self, notification: JsonObject, ctx: _TurnContext) -> HarnessEvent | None:
        return normalize_notification(
            notification,
            invocation_id=ctx.invocation_id,
            session_id=ctx.session_id,
            turn_id=ctx.turn_id,
            author=self.base,
        )
