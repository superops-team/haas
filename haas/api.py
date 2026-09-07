"""HaaS HTTP/SSE API: ADK-compatible surface + HaaS native health/ready."""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import time
import uuid
import zipfile
from dataclasses import dataclass
from typing import Annotated, Any, cast

from fastapi import FastAPI, File, Request, UploadFile
from fastapi.responses import JSONResponse, Response, StreamingResponse

from haas.admission import AdmissionControl, AdmissionInput
from haas.artifacts import (
    ArtifactNotFoundError,
    ArtifactPathRejected,
    ArtifactPolicy,
    ArtifactStore,
)
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
from haas.observability import Metrics, StatusSnapshot, StructuredLogger
from haas.registry import (
    AppNotFoundError,
    HarnessNotFoundError,
    HarnessRegistry,
    ImmutableFieldError,
    SkillBundleInvalidError,
    UnsupportedBaseError,
    harness_to_dict,
    seed_codex,
)
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


def _configured_scopes(identity: IdentityProvider) -> list[tuple[str | None, str | None]]:
    """Distinct (tenantId, workspaceId) pairs across configured principals.

    Order is preserved so the first scope keeps the stable
    `chrn_codex_default` id.
    """
    principals = getattr(identity, "principals", None)
    if principals is None:
        return [(None, None)]
    scopes: list[tuple[str | None, str | None]] = []
    for principal in principals():
        key = (principal.tenantId, principal.workspaceId)
        if key not in scopes:
            scopes.append(key)
    return scopes or [(None, None)]


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
    artifacts: ArtifactStore
    metrics: Metrics
    logger: StructuredLogger


def build_app(
    config: AppConfig | None = None,
    *,
    adapter: HarnessAdapter | None = None,
    identity_tokens: dict[str, Principal] | None = None,
    run_quota: int = 20,
    rate_limit: int = 100,
    max_file_bytes: int | None = None,
) -> FastAPI:
    config = config if config is not None else AppConfig()
    store = MemoryStore()
    adapter = adapter or FakeAdapter()
    identity = StaticTokenIdentityProvider(
        identity_tokens or {DEFAULT_TOKEN: Principal(principalId="p_dev")}
    )
    registry = HarnessRegistry(
        store=store,
        # A base is usable when an adapter backs it (specs/harness-registry
        # §5.1.2); the assembled adapter is always registered.
        known_bases=frozenset({"codex", "fake", adapter.base}),
    )
    # Seed once per configured principal scope so tenant-bound callers see the
    # P0 catalog instead of an empty one (specs/harness-registry §5.1.3).
    seed_codex(registry, scopes=_configured_scopes(identity))
    event_log = EventLog(store=store)
    admission = AdmissionControl(store=store, run_quota=run_quota, rate_limit=rate_limit)
    sessions = SessionRuntime(
        store=store, registry=registry, adapter=adapter, event_log=event_log
    )
    artifact_policy = ArtifactPolicy()
    if max_file_bytes is not None:
        artifact_policy.maxFileBytes = max_file_bytes
    runtime = _Runtime(
        store=store,
        registry=registry,
        event_log=event_log,
        admission=admission,
        identity=identity,
        sessions=sessions,
        adapter=adapter,
        artifacts=ArtifactStore(artifact_policy),
        metrics=Metrics(),
        logger=StructuredLogger(),
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

    probe_cache: dict[str, Any] = {"result": None, "at": 0.0}

    async def _execution_ready() -> tuple[str, str | None]:
        now = time.monotonic()
        if probe_cache["result"] is None or now - probe_cache["at"] > 30.0:
            try:
                readiness_probe = getattr(adapter, "probe_readiness", None)
                probe_cache["result"] = await (
                    readiness_probe() if readiness_probe is not None else adapter.probe()
                )
            except Exception as exc:  # noqa: BLE001 - readiness must not raise
                probe_cache["result"] = None
                return "not_ready", str(exc)
            probe_cache["at"] = now
        probe = probe_cache["result"]
        if probe is not None and getattr(probe, "status", "") == "ready":
            return "ready", None
        reason = None
        if probe is not None:
            details = getattr(probe, "safeDetails", {}) or {}
            reason = details.get("safeReason") or "adapter_not_ready"
        return "not_ready", reason

    @app.get("/v1/haas/ready")
    async def ready(scope: str = "control") -> dict[str, Any]:
        if scope in {"control", "execution"}:
            status, reason = await _execution_ready()
            if status != "ready":
                raise HaasError(
                    503,
                    "service_unavailable",
                    "haas_adapter_unavailable",
                    safe_reason=reason or "adapter_not_ready",
                    retryable=True,
                )
            payload: dict[str, Any] = {"status": status, "scope": scope}
            return {"data": payload, "traceId": "tr_local"}
        return {
            "data": {"status": "not_ready", "scope": scope, "reason": "unknown_scope"},
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

    # --- Observability: status / diagnostics (specs/observability §5.1) ----

    started_at = time.monotonic()

    @app.get("/v1/haas/status")
    async def status(request: Request) -> dict[str, Any]:
        await _authenticate(runtime.identity, request)
        exec_status, exec_reason = await _execution_ready()
        snapshot = StatusSnapshot(
            status="ok" if exec_status == "ready" else "degraded",
            adapterStatus={runtime.adapter.adapter_id: exec_status},
            activeSessions=runtime.store.count_sessions(),
            checks={"control": "passed", "execution": exec_status},
        )
        data = snapshot.to_dict()
        data["uptimeSeconds"] = int(time.monotonic() - started_at)
        data["protocol"] = {
            "haasVersion": "2026-08-26",
            "adkProtocol": "2.0",
            "capability": "run/run_sse/sessions",
        }
        data["adapters"] = [
            {"adapterId": runtime.adapter.adapter_id,
             "base": runtime.adapter.base,
             "status": exec_status}
        ]
        data["lastErrorSafeReason"] = exec_reason
        return {"data": data, "traceId": f"tr_{uuid.uuid4().hex[:16]}"}

    @app.get("/v1/haas/diagnostics")
    async def diagnostics(request: Request) -> dict[str, Any]:
        await _authenticate(runtime.identity, request)
        # Diagnostics are routed through the structured logger so the shared
        # redaction table applies before anything leaves the process.
        payload = runtime.logger.event(
            "haas.diagnostics",
            {
                "adapterBase": runtime.adapter.base,
                "adapterId": runtime.adapter.adapter_id,
                "storeBackend": config.store.backend,
                "identityProvider": config.identity.provider,
                "metrics": runtime.metrics.snapshot(),
                "sessionCount": runtime.store.count_sessions(),
                "harnessCount": len(runtime.store.list_harnesses()),
            },
        )
        payload["partial"] = False
        return {"data": payload, "traceId": f"tr_{uuid.uuid4().hex[:16]}"}

    # --- Artifact Store (specs/artifact-store §5.1) -----------------------

    @app.post("/v1/haas/files")
    async def upload_file(
        request: Request,
        file: Annotated[UploadFile, File()],
        purpose: str = "user_data",
    ) -> dict[str, Any]:
        principal = await _authenticate(runtime.identity, request)
        content = await file.read()
        policy = runtime.artifacts.policy
        if len(content) > policy.maxFileBytes:
            raise HaasError(413, "invalid_request_error", "haas_file_too_large")
        filename = file.filename or ""
        try:
            if (
                not filename
                or filename in {".", ".."}
                or "/" in filename
                or "\\" in filename
            ):
                raise ArtifactPathRejected("absolute_path_rejected")
            record = runtime.artifacts.register(
                session_id="",
                relative_path=f"output/{filename}",
                content=content,
                owner_principal_id=principal.principalId,
            )
        except ArtifactPathRejected as exc:
            if "too_large" in str(exc):
                raise HaasError(
                    413, "invalid_request_error", "haas_file_too_large"
                ) from exc
            raise HaasError(400, "invalid_request_error", "invalid_input") from exc
        runtime.metrics.incr("haas_file_upload_total")
        record.mediaType = file.content_type or record.mediaType
        return {"data": record.to_dict(), "traceId": f"tr_{uuid.uuid4().hex[:16]}"}

    @app.get("/v1/haas/files/{file_id}/content")
    async def download_file(file_id: str, request: Request) -> Response:
        principal = await _authenticate(runtime.identity, request)
        try:
            record = runtime.artifacts.get(
                file_id, owner_principal_id=principal.principalId
            )
            content = runtime.artifacts.read_content(
                file_id, owner_principal_id=principal.principalId
            )
        except ArtifactNotFoundError as exc:
            raise HaasError(404, "invalid_request_error", "haas_file_not_found") from exc
        runtime.metrics.incr("haas_artifact_download_total")
        return Response(
            content=content,
            media_type=record.mediaType,
            headers={
                "X-Content-Type-Options": "nosniff",
                # Active content (HTML/JS/SVG) must never render inline
                # from this origin (specs/security-boundary §8.4).
                "Content-Disposition": f'attachment; filename="{record.filename}"',
            },
        )

    @app.get("/v1/haas/files/{file_id}/pdf")
    async def preview_file_pdf(file_id: str, request: Request) -> Response:
        principal = await _authenticate(runtime.identity, request)
        try:
            runtime.artifacts.get(file_id, owner_principal_id=principal.principalId)
        except ArtifactNotFoundError as exc:
            raise HaasError(404, "invalid_request_error", "haas_file_not_found") from exc
        raise HaasError(501, "invalid_request_error", "haas_preview_unavailable")

    @app.get("/v1/haas/sessions/{session_id}/artifacts")
    async def list_session_artifacts(
        session_id: str, request: Request
    ) -> dict[str, Any]:
        principal = await _authenticate(runtime.identity, request)
        records = runtime.artifacts.list(
            session_id, owner_principal_id=principal.principalId
        )
        return {
            "data": {"artifacts": [r.to_dict() for r in records]},
            "traceId": f"tr_{uuid.uuid4().hex[:16]}",
        }

    @app.get("/v1/haas/sessions/{session_id}/artifacts/archive")
    async def download_session_archive(session_id: str, request: Request) -> Response:
        principal = await _authenticate(runtime.identity, request)
        records = runtime.artifacts.list(
            session_id, owner_principal_id=principal.principalId
        )
        if not records:
            raise HaasError(404, "invalid_request_error", "haas_file_not_found")
        buffer = io.BytesIO()
        try:
            with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
                for record in records:
                    try:
                        content = runtime.artifacts.read_content(
                            record.id, owner_principal_id=principal.principalId
                        )
                    except ArtifactNotFoundError:
                        # Container-only record: skip rather than fail the
                        # whole archive (specs/artifact-store §6.1.1).
                        continue
                    archive.writestr(record.relativePath, content)
        except (OSError, zipfile.BadZipFile) as exc:
            raise HaasError(
                500, "invalid_request_error", "haas_archive_failed", retryable=True
            ) from exc
        runtime.metrics.incr("haas_artifact_archive_built_total")
        return Response(
            content=buffer.getvalue(),
            media_type="application/zip",
            headers={
                "X-Content-Type-Options": "nosniff",
                "Content-Disposition": f'attachment; filename="{session_id}-artifacts.zip"',
            },
        )

    # --- Harness Registry CRUD (specs/harness-registry §5.1) --------------

    def _harness_envelope(record: Any) -> dict[str, Any]:
        return {
            "data": harness_to_dict(record),
            "traceId": f"tr_{uuid.uuid4().hex[:16]}",
        }

    async def _harness_body(request: Request) -> dict[str, Any]:
        try:
            body = await request.json()
        except (ValueError, TypeError) as exc:
            raise HaasError(400, "invalid_request_error", "invalid_input") from exc
        if not isinstance(body, dict):
            raise HaasError(400, "invalid_request_error", "invalid_input")
        return body

    @app.get("/v1/haas/harnesses")
    async def list_harnesses(request: Request) -> dict[str, Any]:
        principal = await _authenticate(runtime.identity, request)
        records = runtime.registry.list_active(principal)
        return {
            "data": {"harnesses": [harness_to_dict(r) for r in records]},
            "traceId": f"tr_{uuid.uuid4().hex[:16]}",
        }

    @app.post("/v1/haas/harnesses")
    async def create_harness(request: Request) -> dict[str, Any]:
        principal = await _authenticate(runtime.identity, request)
        body = await _harness_body(request)

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
            if reservation.replay and reservation.result is not None:
                return dict(reservation.result)

        try:
            record = runtime.registry.create(principal, body)
        except UnsupportedBaseError as exc:
            if key_hash:
                runtime.store.release(key_hash)
            raise HaasError(
                422, "invalid_request_error", "haas_unsupported_base"
            ) from exc
        except SkillBundleInvalidError as exc:
            if key_hash:
                runtime.store.release(key_hash)
            raise HaasError(
                422, "invalid_request_error", "haas_skill_source_invalid"
            ) from exc
        except (ValueError, TypeError) as exc:
            if key_hash:
                runtime.store.release(key_hash)
            raise HaasError(400, "invalid_request_error", "invalid_input") from exc

        runtime.logger.event("haas.harness.created", {"id": record.id, "base": record.base})
        runtime.metrics.incr("haas_harness_created_total")
        envelope = _harness_envelope(record)
        if key_hash:
            runtime.store.complete(key_hash, envelope)
        return envelope

    @app.get("/v1/haas/harnesses/{harness_id}")
    async def get_harness(harness_id: str, request: Request) -> dict[str, Any]:
        principal = await _authenticate(runtime.identity, request)
        try:
            record = runtime.registry.get_scoped(principal, harness_id)
        except HarnessNotFoundError as exc:
            raise HaasError(
                404, "invalid_request_error", "haas_harness_not_found"
            ) from exc
        return _harness_envelope(record)

    @app.put("/v1/haas/harnesses/{harness_id}")
    async def update_harness(harness_id: str, request: Request) -> dict[str, Any]:
        principal = await _authenticate(runtime.identity, request)
        body = await _harness_body(request)
        try:
            record = runtime.registry.update(principal, harness_id, body)
        except HarnessNotFoundError as exc:
            raise HaasError(
                404, "invalid_request_error", "haas_harness_not_found"
            ) from exc
        except ImmutableFieldError as exc:
            # Immutable-field conflict is a body validation failure; no new
            # error code is introduced (specs/harness-registry §5.1.1).
            raise HaasError(400, "invalid_request_error", "invalid_input") from exc
        except UnsupportedBaseError as exc:
            raise HaasError(
                422, "invalid_request_error", "haas_unsupported_base"
            ) from exc
        except SkillBundleInvalidError as exc:
            raise HaasError(
                422, "invalid_request_error", "haas_skill_source_invalid"
            ) from exc
        except (ValueError, TypeError) as exc:
            raise HaasError(400, "invalid_request_error", "invalid_input") from exc
        runtime.logger.event("haas.harness.updated", {"id": record.id})
        return _harness_envelope(record)

    @app.delete("/v1/haas/harnesses/{harness_id}")
    async def delete_harness(harness_id: str, request: Request) -> dict[str, Any]:
        principal = await _authenticate(runtime.identity, request)
        try:
            runtime.registry.delete(principal, harness_id)
        except HarnessNotFoundError as exc:
            raise HaasError(
                404, "invalid_request_error", "haas_harness_not_found"
            ) from exc
        runtime.logger.event("haas.harness.deleted", {"id": harness_id})
        return {"data": {"deleted": True}, "traceId": f"tr_{uuid.uuid4().hex[:16]}"}

    @app.get("/v1/haas/models")
    async def list_models(request: Request) -> dict[str, Any]:
        principal = await _authenticate(runtime.identity, request)
        backends: dict[str, Any] = {}
        for record in runtime.registry.list_active(principal):
            entry = backends.setdefault(
                record.base, {"default": record.defaultModel or "", "models": []}
            )
            model = record.defaultModel or ""
            if model and all(m.get("id") != model for m in entry["models"]):
                entry["models"].append({"id": model, "object": "model"})
            if not entry["default"] and model:
                entry["default"] = model
        return {
            "data": {"backends": backends},
            "traceId": f"tr_{uuid.uuid4().hex[:16]}",
        }

    @app.get("/v1/haas/harnesses/{harness_id}/skills/{skill_id}/files")
    async def list_skill_files(
        harness_id: str, skill_id: str, request: Request
    ) -> dict[str, Any]:
        principal = await _authenticate(runtime.identity, request)
        try:
            record = runtime.registry.get_scoped(principal, harness_id)
        except HarnessNotFoundError as exc:
            raise HaasError(
                404, "invalid_request_error", "haas_harness_not_found"
            ) from exc
        for bundle in record.skills:
            if str(bundle.get("id", "")) == skill_id:
                return {
                    "data": {
                        "id": skill_id,
                        "name": str(bundle.get("name") or skill_id),
                        "files": list(bundle.get("files") or []),
                    },
                    "traceId": f"tr_{uuid.uuid4().hex[:16]}",
                }
        raise HaasError(404, "invalid_request_error", "haas_harness_not_found")

    @app.get("/v1/haas/sessions")
    async def list_sessions(
        request: Request,
        limit: int = 20,
        cursor: str | None = None,
        app: str | None = None,
    ) -> dict[str, Any]:
        principal = await _authenticate(runtime.identity, request)
        if limit < 1 or limit > 100:
            raise HaasError(400, "invalid_request_error", "invalid_input")

        # Scope: only sessions whose harness and user belong to the caller.
        visible_apps = {h.id for h in runtime.registry.list_active(principal)}
        if app is not None and app not in visible_apps:
            return {
                "data": [],
                "nextCursor": None,
                "traceId": f"tr_{uuid.uuid4().hex[:16]}",
            }
        records = [
            r
            for r in runtime.store.list_sessions(
                app_name=app, user_ids=principal.userIds
            )
            if r.appName in visible_apps
        ]

        start = 0
        if cursor:
            for index, record in enumerate(records):
                if record.id == cursor:
                    start = index + 1
                    break
            else:
                # An unknown cursor must fail loudly rather than silently
                # restarting from page one and duplicating results.
                raise HaasError(400, "invalid_request_error", "invalid_input")

        page = records[start : start + limit]
        exhausted = start + limit >= len(records)
        next_cursor = None if exhausted or not page else page[-1].id
        return {
            "data": [
                {
                    "id": r.id,
                    "appName": r.appName,
                    "userId": r.userId,
                    "state": r.state,
                    "events": [
                        runtime.event_log.project_adk(e)
                        for e in runtime.event_log.read_session(r.id)
                    ],
                    "lastUpdateTime": r.updatedAtMs / 1000.0,
                }
                for r in page
            ],
            "nextCursor": next_cursor,
            "traceId": f"tr_{uuid.uuid4().hex[:16]}",
        }

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
