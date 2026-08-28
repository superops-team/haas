"""HaaS HTTP/SSE API: ADK-compatible surface + HaaS native health/ready."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, cast

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from haas.admission import AdmissionControl, AdmissionInput
from haas.config import AppConfig
from haas.events import EventLog
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
    RunRequest,
    SessionBusyError,
    SessionNotFoundError,
    SessionRuntime,
)
from haas.stores import IdempotencyConflictError, MemoryStore

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
                    "traceId": f"tr_{_hash(exc.code)[:16]}",
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
        delta = body.get("stateDelta", {})
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
    app_name = body.get("appName")
    user_id = body.get("userId")
    session_id = body.get("sessionId")
    new_message = body.get("newMessage", {})
    if not app_name or not user_id:
        raise HaasError(400, "invalid_request_error", "invalid_input")

    _ensure_owns(runtime.identity, principal, user_id)

    try:
        app = runtime.registry.resolve_app(principal, app_name)
    except AppNotFoundError as exc:
        raise HaasError(404, "invalid_request_error", "app_not_found") from exc

    admission = runtime.admission.admit_run(
        AdmissionInput(principalHash=principal.principalId, appName=app.id,
                       tenantId=principal.tenantId, workspaceId=principal.workspaceId)
    )
    if not admission.allowed:
        code = admission.code or "haas_rate_limited"
        retryable = code in {"haas_rate_limited", "haas_quota_exceeded", "haas_queue_full"}
        raise HaasError(
            429 if code != "haas_queue_full" else 503,
            "invalid_request_error", code, admission.safeReason, retryable,
        )

    idempotency_key = request.headers.get("Idempotency-Key")
    cached = None
    if idempotency_key:
        key_hash = _hash(idempotency_key)
        request_hash = _hash(json.dumps(body, sort_keys=True, default=str))
        try:
            reservation = runtime.store.reserve(key_hash, request_hash)
            if reservation.replay:
                cached = runtime.store.replay(key_hash)
        except IdempotencyConflictError as exc:
            raise HaasError(
                409, "invalid_request_error", "haas_idempotency_conflict"
            ) from exc

    if cached is not None:
        return _render_cached(cached, streaming=streaming)

    req = RunRequest(app=app, user_id=user_id, session_id=session_id, message=new_message)
    try:
        result = await runtime.sessions.run(req)
    except SessionBusyError as exc:
        raise HaasError(409, "invalid_request_error", "session_busy", retryable=True) from exc

    adk_events = [runtime.event_log.project_adk(e) for e in result.events]

    if idempotency_key:
        runtime.store.complete(_hash(idempotency_key), {"events": adk_events})

    if not streaming:
        return adk_events

    async def frames() -> Any:
        for event in adk_events:
            yield f"data: {json.dumps(event, separators=(',', ':'))}\n\n"
        yield ": keep-alive\n\n"

    return StreamingResponse(frames(), media_type="text/event-stream")


def _render_cached(cached: Any, *, streaming: bool) -> Any:
    events = cached.get("events", []) if isinstance(cached, dict) else []
    if not streaming:
        return events

    async def frames() -> Any:
        for event in events:
            yield f"data: {json.dumps(event, separators=(',', ':'))}\n\n"
        yield ": keep-alive\n\n"

    return StreamingResponse(frames(), media_type="text/event-stream")
