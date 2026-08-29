"""HaaS HTTP/SSE API: ADK-compatible surface + HaaS native health/ready."""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid
from dataclasses import dataclass
from typing import Any, cast

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from haas.admission import AdmissionControl, AdmissionInput
from haas.config import AppConfig
from haas.events import HEARTBEAT_FRAME, EventLog
from haas.harnesses import FakeAdapter, HarnessAdapter
from haas.identity import (
    IdentityProvider,
    InvalidCredentialError,
    MissingCredentialError,
    Principal,
    StaticTokenIdentityProvider,
)
from haas.registry import AppNotFoundError, HarnessRegistry, seed_codex
from haas.sessions import (
    AdapterTurnError,
    InvocationNotFoundError,
    RunRequest,
    SessionBusyError,
    SessionNotFoundError,
    SessionRuntime,
)
from haas.stores import CursorNotFoundError, IdempotencyConflictError, MemoryStore

DEFAULT_TOKEN = "dev-token"


class HaasError(Exception):
    def __init__(
        self,
        status_code: int,
        type_: str,
        code: str,
        safe_reason: str | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(code)
        self.status_code = status_code
        self.type = type_
        self.code = code
        self.safe_reason = safe_reason or code
        self.retryable = retryable


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


@dataclass
class _Runtime:
    store: MemoryStore
    registry: HarnessRegistry
    event_log: EventLog
    admission: AdmissionControl
    identity: IdentityProvider
    sessions: SessionRuntime
    adapter: HarnessAdapter


def build_app(
    config: AppConfig | None = None,
    *,
    adapter: HarnessAdapter | None = None,
    identity_tokens: dict[str, Principal] | None = None,
    run_quota: int = 20,
    rate_limit: int = 100,
) -> FastAPI:
    config = config if config is not None else AppConfig()
    store = MemoryStore()
    registry = HarnessRegistry(store=store)
    seed_codex(registry)
    event_log = EventLog(store=store)
    adapter = adapter or FakeAdapter()
    admission = AdmissionControl(store=store, run_quota=run_quota, rate_limit=rate_limit)
    identity = StaticTokenIdentityProvider(
        identity_tokens or {DEFAULT_TOKEN: Principal(principalId="p_dev")}
    )
    sessions = SessionRuntime(
        store=store, registry=registry, adapter=adapter, event_log=event_log
    )
    runtime = _Runtime(
        store=store,
        registry=registry,
        event_log=event_log,
        admission=admission,
        identity=identity,
        sessions=sessions,
        adapter=adapter,
    )

    app = FastAPI(title="Harness As A Service", version="2026-08-26")
    app.state.haas_config = config
    app.state.runtime = runtime

    @app.exception_handler(HaasError)
    async def haas_error_handler(request: Request, exc: HaasError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "detail": exc.safe_reason,
                "haasError": {
                    "type": exc.type,
                    "code": exc.code,
                    "param": None,
                    "safeReason": exc.safe_reason,
                    "retryable": exc.retryable,
                    "traceId": f"tr_{uuid.uuid4().hex[:16]}",
                },
            },
        )

    @app.get("/v1/haas/health")
    async def health() -> dict[str, Any]:
        return {"data": {"status": "ok"}, "traceId": "tr_local"}

    @app.get("/v1/haas/ready")
    async def ready(scope: str = "control") -> dict[str, Any]:
        if scope == "control":
            return {"data": {"status": "ready", "scope": scope}, "traceId": "tr_local"}
        return {
            "data": {"status": "not_ready", "scope": scope, "reason": "not_implemented"},
            "traceId": "tr_local",
        }

    @app.get("/health")
    async def health_alias() -> dict[str, Any]:
        return await health()

    @app.get("/ready")
    async def ready_alias(scope: str = "control") -> dict[str, Any]:
        return await ready(scope)

    @app.get("/list-apps")
    async def list_apps(request: Request) -> list[str]:
        principal = await _authenticate(runtime.identity, request)
        return runtime.registry.list_apps(principal)

    @app.post("/run")
    async def run(request: Request) -> Any:
        return await _run(runtime, request, streaming=False)

    @app.post("/run_sse")
    async def run_sse(request: Request) -> StreamingResponse:
        return cast(StreamingResponse, await _run(runtime, request, streaming=True))

    @app.get("/apps/{app_name}/users/{user_id}/sessions/{session_id}")
    async def get_session(
        app_name: str, user_id: str, session_id: str, request: Request
    ) -> dict[str, Any]:
        principal = await _authenticate(runtime.identity, request)
        _ensure_owns(runtime.identity, principal, user_id)
        try:
            session = runtime.sessions.get_session(app_name, user_id, session_id)
        except SessionNotFoundError as exc:
            raise HaasError(404, "invalid_request_error", "session_not_found") from exc
        events = runtime.event_log.read_session(session_id)
        return {
            "id": session.id,
            "appName": session.appName,
            "userId": session.userId,
            "state": session.state,
            "events": [runtime.event_log.project_adk(e) for e in events],
            "lastUpdateTime": session.updatedAtMs / 1000.0,
        }

    @app.patch("/apps/{app_name}/users/{user_id}/sessions/{session_id}")
    async def patch_session(
        app_name: str, user_id: str, session_id: str, request: Request
    ) -> dict[str, Any]:
        principal = await _authenticate(runtime.identity, request)
        _ensure_owns(runtime.identity, principal, user_id)
        body = await request.json()
        if not isinstance(body, dict):
            raise HaasError(400, "invalid_request_error", "invalid_input")
        delta = body.get("stateDelta", {})
        if not isinstance(delta, dict):
            raise HaasError(400, "invalid_request_error", "invalid_input")
        try:
            session = runtime.sessions.apply_state_delta(app_name, user_id, session_id, delta)
        except SessionNotFoundError as exc:
            raise HaasError(404, "invalid_request_error", "session_not_found") from exc
        return {"id": session.id, "appName": session.appName, "userId": session.userId,
                "state": session.state, "lastUpdateTime": session.updatedAtMs / 1000.0}

    @app.delete("/apps/{app_name}/users/{user_id}/sessions/{session_id}")
    async def delete_session(
        app_name: str, user_id: str, session_id: str, request: Request
    ) -> JSONResponse:
        principal = await _authenticate(runtime.identity, request)
        _ensure_owns(runtime.identity, principal, user_id)
        try:
            runtime.sessions.delete_session(app_name, user_id, session_id)
        except SessionNotFoundError as exc:
            raise HaasError(404, "invalid_request_error", "session_not_found") from exc
        return JSONResponse(status_code=204, content=None)

    @app.post("/v1/haas/sessions/{session_id}/invocations/{invocation_id}/cancel")
    async def cancel_invocation(
        session_id: str, invocation_id: str, request: Request
    ) -> dict[str, Any]:
        await _authenticate(runtime.identity, request)
        try:
            invocation = await runtime.sessions.cancel_invocation(session_id, invocation_id)
        except InvocationNotFoundError as exc:
            raise HaasError(
                404, "invalid_request_error", "haas_invocation_not_found"
            ) from exc
        return {
            "data": {
                "sessionId": session_id,
                "invocationId": invocation_id,
                "status": invocation.status,
            },
            "traceId": f"tr_{_hash(invocation_id)[:16]}",
        }

    @app.get("/v1/haas/sessions/{session_id}/events")
    async def session_events(
        session_id: str, request: Request, after_event_id: str | None = None
    ) -> StreamingResponse:
        await _authenticate(runtime.identity, request)
        try:
            events = runtime.event_log.read_session(session_id, after_event_id)
        except CursorNotFoundError as exc:
            raise HaasError(
                410, "invalid_request_error", "haas_offset_expired"
            ) from exc

        async def frames() -> Any:
            for event in events:
                yield runtime.event_log.haas_frame(event)
            yield HEARTBEAT_FRAME

        return StreamingResponse(frames(), media_type="text/event-stream")

    @app.get("/v1/haas/sessions/{session_id}/invocations/{invocation_id}/events")
    async def invocation_events(
        session_id: str, invocation_id: str, request: Request
    ) -> StreamingResponse:
        await _authenticate(runtime.identity, request)
        events = runtime.event_log.read_invocation(invocation_id)

        async def frames() -> Any:
            for event in events:
                yield runtime.event_log.haas_frame(event)
            yield HEARTBEAT_FRAME

        return StreamingResponse(frames(), media_type="text/event-stream")

    return app


async def _authenticate(identity: IdentityProvider, request: Request) -> Principal:
    authorization = request.headers.get("Authorization")
    try:
        return await identity.authenticate(authorization)
    except MissingCredentialError as exc:
        raise HaasError(401, "invalid_request_error", "missing_credential") from exc
    except InvalidCredentialError as exc:
        raise HaasError(401, "invalid_request_error", "invalid_credential") from exc


def _ensure_owns(identity: IdentityProvider, principal: Principal, user_id: str) -> None:
    if not identity.owns(principal, user_id=user_id):
        raise HaasError(404, "invalid_request_error", "session_not_found")


async def _run(runtime: _Runtime, request: Request, *, streaming: bool) -> Any:
    principal = await _authenticate(runtime.identity, request)
    body = await request.json()
    if not isinstance(body, dict):
        raise HaasError(400, "invalid_request_error", "invalid_input")
    app_name = body.get("appName")
    user_id = body.get("userId")
    session_id = body.get("sessionId")
    new_message = body.get("newMessage", {})
    if (
        not isinstance(app_name, str)
        or not isinstance(user_id, str)
        or not app_name
        or not user_id
        or (session_id is not None and not isinstance(session_id, str))
        or not isinstance(new_message, dict)
    ):
        raise HaasError(400, "invalid_request_error", "invalid_input")

    _ensure_owns(runtime.identity, principal, user_id)

    try:
        app = runtime.registry.resolve_app(principal, app_name)
    except AppNotFoundError as exc:
        raise HaasError(404, "invalid_request_error", "app_not_found") from exc

    # Idempotency-Key reservation happens before admission (spec: haas-protocol §7).
    idempotency_key = request.headers.get("Idempotency-Key")
    key_hash: str | None = None
    if idempotency_key:
        key_hash = _hash(idempotency_key)
        request_hash = _hash(json.dumps(body, sort_keys=True, default=str))
        try:
            reservation = runtime.store.reserve(key_hash, request_hash)
        except IdempotencyConflictError as exc:
            raise HaasError(
                409, "invalid_request_error", "haas_idempotency_conflict"
            ) from exc
        attempts = 0
        while reservation.replay:
            result = reservation.result
            if result is None:
                result = await _wait_for_idempotency(runtime.store, key_hash)
            if result is not None:
                return _render_cached(result, streaming=streaming)
            # Previous holder released without completing -> take ownership.
            attempts += 1
            if attempts >= 8:
                raise HaasError(409, "invalid_request_error", "session_busy", retryable=True)
            try:
                reservation = runtime.store.reserve(key_hash, request_hash)
            except IdempotencyConflictError as exc:
                raise HaasError(
                    409, "invalid_request_error", "haas_idempotency_conflict"
                ) from exc

    admission = runtime.admission.admit_run(
        AdmissionInput(principalHash=principal.principalId, appName=app.id,
                       tenantId=principal.tenantId, workspaceId=principal.workspaceId)
    )
    if not admission.allowed:
        if key_hash:
            runtime.store.release(key_hash)
        code = admission.code or "haas_rate_limited"
        retryable = code in {"haas_rate_limited", "haas_quota_exceeded", "haas_queue_full"}
        raise HaasError(
            429 if code != "haas_queue_full" else 503,
            "invalid_request_error", code, admission.safeReason, retryable,
        )

    lease_id = admission.leaseId
    req = RunRequest(
        app=app, user_id=user_id, session_id=session_id, message=new_message
    )
    try:
        try:
            result = await runtime.sessions.run(req)
        except SessionBusyError as exc:
            if key_hash:
                runtime.store.release(key_hash)
            raise HaasError(
                409, "invalid_request_error", "session_busy", retryable=True
            ) from exc
        except AdapterTurnError as exc:
            if key_hash:
                events = [
                    runtime.event_log.project_adk(e)
                    for e in runtime.event_log.read_invocation(exc.invocation_id)
                ]
                runtime.store.complete(key_hash, {"events": events})
            raise HaasError(
                502, "invalid_request_error", "haas_adapter_error", retryable=True
            ) from exc

        adk_events = [runtime.event_log.project_adk(e) for e in result.events]
        if key_hash:
            runtime.store.complete(key_hash, {"events": adk_events})

        if not streaming:
            return adk_events

        async def frames() -> Any:
            for event in adk_events:
                yield f"data: {json.dumps(event, separators=(',', ':'))}\n\n"
            yield ": keep-alive\n\n"

        return StreamingResponse(frames(), media_type="text/event-stream")
    finally:
        if lease_id:
            runtime.admission.release_run(lease_id)


async def _wait_for_idempotency(
    store: MemoryStore, key_hash: str, timeout_s: float = 30.0
) -> Any | None:
    """Wait for an in-flight idempotency reservation to reach a result.

    Returns the first request's result once available; returns None if the
    holder released the reservation without completing (pre-execution failure),
    so the caller can take ownership and execute.
    """
    deadline = time.monotonic() + timeout_s
    while store.is_pending(key_hash):
        if time.monotonic() > deadline:
            raise HaasError(409, "invalid_request_error", "session_busy", retryable=True)
        await asyncio.sleep(0.01)
    return store.replay(key_hash)


def _render_cached(cached: Any, *, streaming: bool) -> Any:
    events = cached.get("events", []) if isinstance(cached, dict) else []
    if not streaming:
        return events

    async def frames() -> Any:
        for event in events:
            yield f"data: {json.dumps(event, separators=(',', ':'))}\n\n"
        yield ": keep-alive\n\n"

    return StreamingResponse(frames(), media_type="text/event-stream")
