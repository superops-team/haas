"""HaaS HTTP/SSE API: ADK-compatible surface + HaaS native health/ready."""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import time
import uuid
import zipfile
from collections.abc import AsyncIterator
from dataclasses import dataclass, replace
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
from haas.runtime import DelegatedContainerRuntime, DelegatedContainerUnavailable
from haas.runtime.delegation import DisabledDelegatedContainerRuntime
from haas.sessions import (
    AdapterTurnError,
    InvocationNotFoundError,
    RunRequest,
    SessionBusyError,
    SessionNotFoundError,
    SessionRuntime,
)
from haas.stores import (
    ApprovalNotFoundError,
    ApprovalStateConflictError,
    CanonicalEventRecord,
    CursorNotFoundError,
    DelegatedRuntimeRecord,
    DelegatedSessionRecord,
    HarnessRecord,
    IdempotencyConflictError,
    InvocationRecord,
    LeaseConflictError,
    MemoryStore,
    SessionRecord,
    TurnRecord,
)

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


def _haas_error_content(exc: HaasError) -> dict[str, Any]:
    return {
        "detail": exc.safe_reason,
        "haasError": {
            "type": exc.type,
            "code": exc.code,
            "param": None,
            "safeReason": exc.safe_reason,
            "retryable": exc.retryable,
            "traceId": f"tr_{uuid.uuid4().hex[:16]}",
        },
    }


class MountManifestInvalid(ValueError):
    """Delegated mount manifest violates the manager-approved contract."""


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _trace_id() -> str:
    return f"tr_{uuid.uuid4().hex[:16]}"


def _deep_merge(base: dict[str, Any], delta: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in delta.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _delegation_policy_from_config(config: AppConfig) -> dict[str, Any]:
    d = config.delegation
    return {
        "version": 1,
        "idleTtlSeconds": d.idle_ttl_seconds,
        "maxContainerLifetimeSeconds": d.max_container_lifetime_seconds,
        "rwWorkspaceConcurrency": d.rw_workspace_concurrency,
        "queuePolicy": d.queue_policy,
        "restorePolicy": d.restore_policy,
        "policyChangeMode": d.policy_change_mode,
        "mountPolicy": d.mount_policy,
    }


def _validate_delegation_policy(policy: dict[str, Any]) -> None:
    required = {
        "idleTtlSeconds",
        "maxContainerLifetimeSeconds",
        "rwWorkspaceConcurrency",
        "queuePolicy",
        "restorePolicy",
        "mountPolicy",
    }
    if not required.issubset(policy):
        raise ValueError("missing delegation policy fields")
    if int(policy["idleTtlSeconds"]) <= 0 or int(policy["maxContainerLifetimeSeconds"]) <= 0:
        raise ValueError("invalid delegation ttl")
    if policy["rwWorkspaceConcurrency"] != "single_writer":
        raise ValueError("unsupported workspace concurrency")
    if policy["queuePolicy"] != "fifo" or policy["restorePolicy"] != "fail_closed":
        raise ValueError("unsupported delegation policy")


def _validate_mount_entry(entry: Any, *, primary: bool) -> None:
    if not isinstance(entry, dict):
        raise MountManifestInvalid("invalid mount")
    host_path = entry.get("hostPathCanonical")
    container_path = entry.get("containerPath")
    access = entry.get("access")
    if (
        not isinstance(host_path, str)
        or not host_path
        or not isinstance(container_path, str)
        or not container_path
        or not isinstance(access, str)
        or not access
    ):
        raise MountManifestInvalid("invalid mount fields")
    if not host_path.startswith("/") or not container_path.startswith("/"):
        raise MountManifestInvalid("mount paths must be absolute")
    normalized_host = os.path.normpath(host_path)
    if normalized_host != host_path or normalized_host == "/":
        raise MountManifestInvalid("mount host path must be canonical")
    home = os.path.expanduser("~")
    if normalized_host == home:
        raise MountManifestInvalid("must not mount user home")
    if normalized_host in {"/var/run/docker.sock", "/run/docker.sock"}:
        raise MountManifestInvalid("must not mount docker socket")
    if normalized_host.endswith("/.ssh") or "/.ssh/" in normalized_host:
        raise MountManifestInvalid("must not mount ssh directory")
    if access not in {"ro", "rw"}:
        raise MountManifestInvalid("invalid mount access")
    if primary and (container_path != "/workspace" or access != "rw"):
        raise MountManifestInvalid("primary workspace must be /workspace:rw")
    if not primary and access != "ro":
        raise MountManifestInvalid("extra mounts default to ro in the skeleton")


def _validate_mount_manifest(manifest: dict[str, Any]) -> None:
    if not isinstance(manifest, dict):
        raise MountManifestInvalid("invalid mount manifest")
    if int(manifest.get("version", 0)) < 1:
        raise MountManifestInvalid("invalid mount manifest version")
    _validate_mount_entry(manifest.get("primaryWorkspace"), primary=True)
    for entry in manifest.get("extraMounts") or []:
        _validate_mount_entry(entry, primary=False)


def _delegated_session_from_body(
    body: dict[str, Any], config: AppConfig
) -> DelegatedSessionRecord:
    for key in ("managerSessionId", "haasSessionId", "haasUserId", "harnessId"):
        if not isinstance(body.get(key), str) or not body[key]:
            raise ValueError(f"missing {key}")
    image = body.get("image")
    provider = body.get("provider")
    mount_manifest = body.get("mountManifest")
    policy = body.get("delegationPolicySnapshot") or _delegation_policy_from_config(config)
    if not isinstance(image, dict) or not isinstance(image.get("reference"), str):
        raise ValueError("invalid image")
    digest = image.get("digest")
    if not isinstance(digest, str):
        raise ValueError("invalid image digest")
    if (
        not digest
        and "@sha256:" not in image["reference"]
        and not config.delegation.allow_unpinned_local_image
    ):
        raise ValueError("invalid image digest")
    if (
        not isinstance(provider, dict)
        or not isinstance(provider.get("providerId"), str)
        or not isinstance(provider.get("model"), str)
        or not isinstance(provider.get("credentialRef"), str)
    ):
        raise ValueError("invalid provider")
    credential_ref = provider["credentialRef"]
    if not credential_ref.startswith("secret://"):
        raise ValueError("invalid credential ref")
    if not isinstance(policy, dict):
        raise ValueError("invalid delegation policy")
    _validate_delegation_policy(policy)
    if not isinstance(mount_manifest, dict):
        raise ValueError("invalid mount manifest")
    _validate_mount_manifest(mount_manifest)
    return DelegatedSessionRecord(
        id=f"dgsess_{uuid.uuid4().hex[:16]}",
        managerSessionId=body["managerSessionId"],
        haasSessionId=body["haasSessionId"],
        haasUserId=body["haasUserId"],
        harnessId=body["harnessId"],
        harnessBase=str(body.get("harnessBase") or "codex"),
        image=image,
        provider=provider,
        mountManifest=mount_manifest,
        delegationPolicySnapshot=policy,
    )


def _delegated_session_envelope(record: DelegatedSessionRecord) -> dict[str, Any]:
    return {"data": record.to_dict(), "traceId": _trace_id()}


def _delegated_session_ref(record: DelegatedSessionRecord) -> dict[str, Any]:
    return {
        "delegatedSessionId": record.id,
        "managerSessionId": record.managerSessionId,
        "containerGeneration": record.runtime.containerGeneration,
        "runtimeStatus": record.runtime.status,
    }


def _delegated_session_matches_body(
    record: DelegatedSessionRecord, body: dict[str, Any]
) -> bool:
    return (
        record.haasSessionId == body.get("haasSessionId")
        and record.haasUserId == body.get("haasUserId")
        and record.harnessId == body.get("harnessId")
        and record.harnessBase == str(body.get("harnessBase") or "codex")
        and record.image == body.get("image")
        and record.provider == body.get("provider")
        and record.mountManifest == body.get("mountManifest")
        and record.delegationPolicySnapshot
        == (body.get("delegationPolicySnapshot") or record.delegationPolicySnapshot)
    )


def _ensure_delegated_access(
    runtime: _Runtime, principal: Principal, record: DelegatedSessionRecord
) -> None:
    if not runtime.identity.owns(principal, user_id=record.haasUserId):
        raise HaasError(
            404, "invalid_request_error", "haas_delegated_session_not_found"
        )
    try:
        runtime.registry.get_scoped(principal, record.harnessId)
    except HarnessNotFoundError as exc:
        raise HaasError(
            404, "invalid_request_error", "haas_delegated_session_not_found"
        ) from exc


def _has_session_access(runtime: _Runtime, principal: Principal, session_id: str) -> bool:
    visible_apps = {h.id for h in runtime.registry.list_active(principal)}
    return any(
        r.id == session_id and r.appName in visible_apps
        for r in runtime.store.list_sessions(user_ids=principal.userIds)
    )


def _session_key(app_name: str, user_id: str, session_id: str) -> tuple[str, str, str]:
    return (app_name, user_id, session_id)


def _session_to_adk(
    runtime: _Runtime, session: SessionRecord, *, include_events: bool = True
) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": session.id,
        "appName": session.appName,
        "userId": session.userId,
        "state": session.state,
        "lastUpdateTime": session.updatedAtMs / 1000.0,
    }
    if session.delegatedSessionRef is not None:
        data["delegatedSessionRef"] = dict(session.delegatedSessionRef)
    if include_events:
        data["events"] = [
            runtime.event_log.project_adk(e)
            for e in runtime.event_log.read_session(
                session.appName, session.userId, session.id
            )
        ]
    return data


async def _json_object(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except (ValueError, TypeError) as exc:
        raise HaasError(400, "invalid_request_error", "invalid_input") from exc
    if not isinstance(body, dict):
        raise HaasError(400, "invalid_request_error", "invalid_input")
    return body


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
    delegated_containers: DelegatedContainerRuntime
    metrics: Metrics
    logger: StructuredLogger


def build_app(
    config: AppConfig | None = None,
    *,
    adapter: HarnessAdapter | None = None,
    identity_tokens: dict[str, Principal] | None = None,
    delegated_containers: DelegatedContainerRuntime | None = None,
    run_quota: int = 20,
    rate_limit: int = 100,
    max_file_bytes: int | None = None,
    session_lease_ttl_ms: int | None = None,
    session_lease_renew_interval_ms: int | None = None,
    session_turn_timeout_s: float | None = None,
) -> FastAPI:
    config = config if config is not None else AppConfig()
    if session_lease_ttl_ms is None:
        session_lease_ttl_ms = config.session_runtime.lease_ttl_ms
    if session_lease_renew_interval_ms is None:
        session_lease_renew_interval_ms = config.session_runtime.lease_renew_interval_ms
    if session_turn_timeout_s is None:
        session_turn_timeout_s = config.session_runtime.turn_timeout_seconds
    store = MemoryStore()
    adapter = adapter or FakeAdapter()
    identity = StaticTokenIdentityProvider(
        {DEFAULT_TOKEN: Principal(principalId="p_dev")}
        if identity_tokens is None
        else identity_tokens
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
        store=store,
        registry=registry,
        adapter=adapter,
        event_log=event_log,
        lease_ttl_ms=session_lease_ttl_ms,
        lease_renew_interval_ms=session_lease_renew_interval_ms,
        turn_timeout_s=session_turn_timeout_s,
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
        delegated_containers=delegated_containers or DisabledDelegatedContainerRuntime(),
        metrics=Metrics(),
        logger=StructuredLogger(),
    )

    app = FastAPI(title="Harness As A Service", version="2026-08-26")
    app.state.haas_config = config
    app.state.runtime = runtime

    @app.exception_handler(HaasError)
    async def haas_error_handler(request: Request, exc: HaasError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=_haas_error_content(exc))

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
        return _session_to_adk(runtime, session)

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
            delegated = runtime.store.get_delegated_session_by_haas_session(session_id)
            if delegated is not None:
                _ensure_delegated_access(runtime, principal, delegated)
                destroyed = await runtime.delegated_containers.destroy(
                    delegated, reason="session_deleted"
                )
                runtime.store.update_delegated_runtime(delegated.id, destroyed)
                primary_workspace = delegated.mountManifest.get("primaryWorkspace") or {}
                workspace_path = primary_workspace.get("hostPathCanonical")
                if isinstance(workspace_path, str):
                    runtime.store.release_workspace_lock(workspace_path, delegated.id)
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

    @app.post("/v1/haas/sessions/{session_id}/approvals/{approval_id}")
    async def resolve_approval(
        session_id: str, approval_id: str, request: Request
    ) -> dict[str, Any]:
        principal = await _authenticate(runtime.identity, request)
        body = await _json_object(request)
        decision = body.get("decision")
        if decision not in {"approved", "denied"}:
            raise HaasError(400, "invalid_request_error", "invalid_input")
        approval = runtime.store.get_approval(approval_id)
        if approval is None or approval.sessionId != session_id:
            raise HaasError(404, "invalid_request_error", "haas_approval_not_found")
        if not _has_session_access(runtime, principal, session_id):
            raise HaasError(404, "invalid_request_error", "haas_approval_not_found")
        try:
            resolved = runtime.store.resolve_approval(approval_id, body)
        except ApprovalNotFoundError as exc:
            raise HaasError(
                404, "invalid_request_error", "haas_approval_not_found"
            ) from exc
        except ApprovalStateConflictError as exc:
            raise HaasError(
                409, "invalid_request_error", "haas_approval_state_conflict"
            ) from exc
        runtime.logger.event(
            "haas.approval.resolved",
            {"approvalId": approval_id, "sessionId": session_id, "status": resolved.status},
        )
        return {"data": resolved.to_dict(), "traceId": _trace_id()}

    @app.get("/v1/haas/sessions/{session_id}/events")
    async def session_events(
        session_id: str, request: Request, after_event_id: str | None = None
    ) -> StreamingResponse:
        principal = await _authenticate(runtime.identity, request)
        session = _resolve_visible_session(runtime, principal, session_id)
        try:
            events = runtime.event_log.read_session(
                session.appName, session.userId, session.id, after_event_id
            )
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
        principal = await _authenticate(runtime.identity, request)
        invocation = _resolve_visible_invocation(
            runtime, principal, session_id, invocation_id
        )
        events = runtime.event_log.read_invocation(
            invocation.appName, invocation.userId, invocation.sessionId, invocation_id
        )

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
        return await _json_object(request)

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
            if reservation.replay:
                result = reservation.result
                if result is None:
                    result = await _wait_for_idempotency(runtime.store, key_hash)
                if result is not None:
                    return dict(result)

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

    @app.post("/v1/haas/delegated-sessions")
    async def create_delegated_session(request: Request) -> dict[str, Any]:
        principal = await _authenticate(runtime.identity, request)
        body = await _json_object(request)
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
            if reservation.replay:
                result = reservation.result
                if result is None:
                    result = await _wait_for_idempotency(runtime.store, key_hash)
                if result is not None:
                    return dict(result)
                raise HaasError(
                    409, "invalid_request_error", "session_busy", retryable=True
                )

        try:
            record = _delegated_session_from_body(body, config)
            _ensure_owns(runtime.identity, principal, record.haasUserId)
            try:
                harness = runtime.registry.get_scoped(principal, record.harnessId)
            except HarnessNotFoundError as exc:
                raise HaasError(
                    404, "invalid_request_error", "haas_harness_not_found"
                ) from exc
            if harness.base != record.harnessBase:
                raise HaasError(400, "invalid_request_error", "invalid_input")

            existing = runtime.store.get_delegated_session_by_manager(
                record.managerSessionId
            )
            if existing is not None:
                _ensure_delegated_access(runtime, principal, existing)
                if not _delegated_session_matches_body(existing, body):
                    raise HaasError(
                        409,
                        "invalid_request_error",
                        "haas_delegated_session_conflict",
                        safe_reason="delegated_session_binding_conflict",
                    )
                saved = existing
            else:
                saved = runtime.store.put_delegated_session(record)
                session = runtime.store.get_session(
                    _session_key(record.harnessId, record.haasUserId, record.haasSessionId)
                )
                if session is None:
                    session = SessionRecord(
                        id=record.haasSessionId,
                        appName=record.harnessId,
                        userId=record.haasUserId,
                    )
                session = replace(
                    session,
                    delegatedSessionRef=_delegated_session_ref(saved),
                )
                runtime.store.put_session(session)
        except HaasError:
            if key_hash:
                runtime.store.release(key_hash)
            raise
        except MountManifestInvalid as exc:
            if key_hash:
                runtime.store.release(key_hash)
            raise HaasError(
                403,
                "invalid_request_error",
                "haas_delegation_mount_invalid",
                safe_reason="delegation_mount_invalid",
            ) from exc
        except (TypeError, ValueError) as exc:
            if key_hash:
                runtime.store.release(key_hash)
            raise HaasError(400, "invalid_request_error", "invalid_input") from exc

        envelope = _delegated_session_envelope(saved)
        if key_hash:
            runtime.store.complete(key_hash, envelope)
        runtime.logger.event(
            "haas.delegation.session_bound",
            {"delegatedSessionId": saved.id, "harnessId": saved.harnessId},
        )
        return envelope

    @app.get("/v1/haas/delegated-sessions/{delegated_session_id}")
    async def get_delegated_session(
        delegated_session_id: str, request: Request
    ) -> dict[str, Any]:
        principal = await _authenticate(runtime.identity, request)
        record = runtime.store.get_delegated_session(delegated_session_id)
        if record is None:
            raise HaasError(
                404, "invalid_request_error", "haas_delegated_session_not_found"
            )
        _ensure_delegated_access(runtime, principal, record)
        return _delegated_session_envelope(record)

    @app.post("/v1/haas/delegated-sessions/{delegated_session_id}/restore")
    async def restore_delegated_session(
        delegated_session_id: str, request: Request
    ) -> dict[str, Any]:
        principal = await _authenticate(runtime.identity, request)
        if request.headers.get("content-length") not in {None, "0"}:
            await _json_object(request)
        record = runtime.store.get_delegated_session(delegated_session_id)
        if record is None:
            raise HaasError(
                404, "invalid_request_error", "haas_delegated_session_not_found"
            )
        _ensure_delegated_access(runtime, principal, record)
        try:
            _validate_mount_manifest(record.mountManifest)
        except MountManifestInvalid as exc:
            raise HaasError(
                403,
                "invalid_request_error",
                "haas_delegation_mount_invalid",
                safe_reason="delegation_mount_invalid",
            ) from exc
        primary_workspace = record.mountManifest["primaryWorkspace"]
        lock = runtime.store.acquire_workspace_lock(
            primary_workspace["hostPathCanonical"],
            record.id,
            primary_workspace["access"],
            ttl_ms=record.delegationPolicySnapshot["maxContainerLifetimeSeconds"]
            * 1000,
        )
        if not lock.acquired:
            raise HaasError(
                409,
                "invalid_request_error",
                "haas_workspace_lock_busy",
                safe_reason="workspace_lock_busy",
                retryable=True,
            )
        try:
            restored_runtime = await runtime.delegated_containers.restore(record)
        except DelegatedContainerUnavailable as exc:
            runtime.store.release_workspace_lock(
                primary_workspace["hostPathCanonical"], record.id
            )
            runtime.store.update_delegated_runtime(
                delegated_session_id,
                DelegatedRuntimeRecord(
                    status="failed",
                    containerId=None,
                    containerGeneration=record.runtime.containerGeneration,
                    lastStartedAtMs=record.runtime.lastStartedAtMs,
                    lastActiveAtMs=record.runtime.lastActiveAtMs,
                ),
            )
            raise HaasError(
                503,
                "invalid_request_error",
                "haas_delegation_backend_unavailable",
                safe_reason="delegation_backend_unavailable",
                retryable=True,
            ) from exc
        restored = runtime.store.update_delegated_runtime(
            delegated_session_id, restored_runtime
        )
        session = runtime.store.get_session(
            _session_key(restored.harnessId, restored.haasUserId, restored.haasSessionId)
        )
        if session is not None:
            runtime.store.put_session(
                replace(session, delegatedSessionRef=_delegated_session_ref(restored))
            )
        runtime.logger.event(
            "haas.delegation.restore_started",
            {
                "delegatedSessionId": restored.id,
                "containerGeneration": restored.runtime.containerGeneration,
            },
        )
        return _delegated_session_envelope(restored)

    @app.post("/v1/haas/delegated-sessions/{delegated_session_id}/policy")
    async def update_delegated_session_policy(
        delegated_session_id: str, request: Request
    ) -> dict[str, Any]:
        principal = await _authenticate(runtime.identity, request)
        body = await _json_object(request)
        record = runtime.store.get_delegated_session(delegated_session_id)
        if record is None:
            raise HaasError(
                404, "invalid_request_error", "haas_delegated_session_not_found"
            )
        _ensure_delegated_access(runtime, principal, record)
        policy = body.get("delegationPolicySnapshot")
        if not isinstance(policy, dict):
            raise HaasError(400, "invalid_request_error", "invalid_input")
        mount_manifest = body.get("mountManifest", record.mountManifest)
        if not isinstance(mount_manifest, dict):
            raise HaasError(400, "invalid_request_error", "invalid_input")
        try:
            _validate_delegation_policy(policy)
            _validate_mount_manifest(mount_manifest)
        except MountManifestInvalid as exc:
            raise HaasError(
                403,
                "invalid_request_error",
                "haas_delegation_mount_invalid",
                safe_reason="delegation_mount_invalid",
            ) from exc
        except ValueError as exc:
            raise HaasError(400, "invalid_request_error", "invalid_input") from exc
        old_primary_workspace = record.mountManifest["primaryWorkspace"]
        runtime_after_policy = record.runtime
        if record.runtime.containerId is not None and record.runtime.status in {
            "running",
            "idle",
            "restoring",
        }:
            try:
                runtime_after_policy = await runtime.delegated_containers.destroy(
                    record, reason="policy_updated"
                )
            except DelegatedContainerUnavailable as exc:
                raise HaasError(
                    503,
                    "invalid_request_error",
                    "haas_delegation_backend_unavailable",
                    safe_reason="delegation_backend_unavailable",
                    retryable=True,
                ) from exc
            runtime.store.release_workspace_lock(
                old_primary_workspace["hostPathCanonical"], record.id
            )
        updated = runtime.store.put_delegated_session(
            DelegatedSessionRecord(
                id=record.id,
                managerSessionId=record.managerSessionId,
                haasSessionId=record.haasSessionId,
                haasUserId=record.haasUserId,
                harnessId=record.harnessId,
                harnessBase=record.harnessBase,
                image=record.image,
                provider=record.provider,
                mountManifest=mount_manifest,
                delegationPolicySnapshot=policy,
                runtime=runtime_after_policy,
                binding=record.binding,
                createdAtMs=record.createdAtMs,
            )
        )
        runtime.logger.event(
            "haas.delegation.policy_updated",
            {"delegatedSessionId": updated.id},
        )
        session = runtime.store.get_session(
            _session_key(updated.harnessId, updated.haasUserId, updated.haasSessionId)
        )
        if session is not None:
            runtime.store.put_session(
                replace(session, delegatedSessionRef=_delegated_session_ref(updated))
            )
        return _delegated_session_envelope(updated)

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
            "data": [_session_to_adk(runtime, r) for r in page],
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


def _resolve_visible_session(
    runtime: _Runtime, principal: Principal, session_id: str
) -> SessionRecord:
    """Resolve a native HaaS session id to exactly one caller-visible session.

    The native `/v1/haas/sessions/{session_id}/events` route is intentionally a
    control-plane convenience route and does not carry ADK's app/user path
    scope. Because `sessionId` is caller-controlled, the implementation must
    not guess when several visible `(appName, userId, sessionId)` tuples share
    the same bare id.
    """
    visible_apps = {
        h.id
        for h in runtime.store.list_harnesses(
            (principal.tenantId, principal.workspaceId)
        )
    }
    records = [
        r
        for r in runtime.store.list_sessions(user_ids=principal.userIds)
        if r.id == session_id and r.appName in visible_apps
    ]
    if len(records) != 1:
        raise HaasError(404, "invalid_request_error", "session_not_found")
    return records[0]


def _resolve_visible_invocation(
    runtime: _Runtime, principal: Principal, session_id: str, invocation_id: str
) -> InvocationRecord:
    invocation = runtime.store.get_invocation(invocation_id)
    if invocation is None or invocation.sessionId != session_id:
        raise HaasError(404, "invalid_request_error", "haas_invocation_not_found")
    _ensure_owns(runtime.identity, principal, invocation.userId)
    visible_apps = {
        h.id
        for h in runtime.store.list_harnesses(
            (principal.tenantId, principal.workspaceId)
        )
    }
    if invocation.appName not in visible_apps:
        raise HaasError(404, "invalid_request_error", "haas_invocation_not_found")
    if runtime.store.get_session((invocation.appName, invocation.userId, session_id)) is None:
        raise HaasError(404, "invalid_request_error", "haas_invocation_not_found")
    return invocation


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
    delegated_record = (
        runtime.store.get_delegated_session_by_haas_session(session_id)
        if isinstance(session_id, str)
        else None
    )
    if delegated_record is not None:
        try:
            if delegated_record.harnessId != app.id or delegated_record.haasUserId != user_id:
                raise HaasError(404, "invalid_request_error", "session_not_found")
            _ensure_delegated_access(runtime, principal, delegated_record)
        except HaasError:
            if key_hash:
                runtime.store.release(key_hash)
            if lease_id:
                runtime.admission.release_run(lease_id)
            raise
        return await _run_delegated(
            runtime,
            app=app,
            user_id=user_id,
            session_id=delegated_record.haasSessionId,
            message=new_message,
            delegated=delegated_record,
            streaming=streaming,
            key_hash=key_hash,
            lease_id=lease_id,
        )
    req = RunRequest(
        app=app, user_id=user_id, session_id=session_id, message=new_message
    )
    if streaming:
        stream = runtime.sessions.run_stream(req)
        adk_events: list[dict[str, Any]] = []
        try:
            first = await stream.__anext__()
        except SessionBusyError as exc:
            if key_hash:
                runtime.store.release(key_hash)
            if lease_id:
                runtime.admission.release_run(lease_id)
            raise HaasError(
                409, "invalid_request_error", "session_busy", retryable=True
            ) from exc
        except StopAsyncIteration:
            if key_hash:
                runtime.store.complete(key_hash, {"events": []})
            if lease_id:
                runtime.admission.release_run(lease_id)
            return StreamingResponse(iter(()), media_type="text/event-stream")
        except AdapterTurnError as exc:
            if key_hash:
                invocation = runtime.store.get_invocation(exc.invocation_id)
                events = [
                    runtime.event_log.project_adk(e)
                    for e in (
                        runtime.event_log.read_invocation(
                            invocation.appName,
                            invocation.userId,
                            invocation.sessionId,
                            exc.invocation_id,
                        )
                        if invocation is not None
                        else []
                    )
                ]
                runtime.store.complete(key_hash, {"events": events})
            if lease_id:
                runtime.admission.release_run(lease_id)
            raise HaasError(
                502, "invalid_request_error", "haas_adapter_error", retryable=True
            ) from exc

        first_adk = runtime.event_log.project_adk(first)
        adk_events.append(first_adk)
        queue: asyncio.Queue[str | None] = asyncio.Queue()
        disconnected = False

        async def produce() -> None:
            nonlocal disconnected
            try:
                try:
                    async for event in stream:
                        adk_event = runtime.event_log.project_adk(event)
                        adk_events.append(adk_event)
                        if not disconnected:
                            queue.put_nowait(
                                f"data: {json.dumps(adk_event, separators=(',', ':'))}\n\n"
                            )
                except AdapterTurnError:
                    # SessionRuntime has already persisted and yielded the
                    # terminal failed event before raising; the SSE response
                    # cannot change HTTP status after headers are sent.
                    pass
                if key_hash:
                    runtime.store.complete(key_hash, {"events": adk_events})
            finally:
                if lease_id:
                    runtime.admission.release_run(lease_id)
                if not disconnected:
                    queue.put_nowait(HEARTBEAT_FRAME)
                queue.put_nowait(None)

        producer = asyncio.create_task(produce())

        async def frames() -> Any:
            nonlocal disconnected
            try:
                yield f"data: {json.dumps(first_adk, separators=(',', ':'))}\n\n"
                while True:
                    frame = await queue.get()
                    if frame is None:
                        break
                    yield frame
            finally:
                disconnected = True
                if producer.done():
                    producer.result()

        return StreamingResponse(frames(), media_type="text/event-stream")

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
                invocation = runtime.store.get_invocation(exc.invocation_id)
                events = [
                    runtime.event_log.project_adk(e)
                    for e in (
                        runtime.event_log.read_invocation(
                            invocation.appName,
                            invocation.userId,
                            invocation.sessionId,
                            exc.invocation_id,
                        )
                        if invocation is not None
                        else []
                    )
                ]
                error = HaasError(
                    502, "invalid_request_error", "haas_adapter_error", retryable=True
                )
                body = _haas_error_content(error)
                runtime.store.complete(
                    key_hash,
                    {"status_code": error.status_code, "body": body, "events": events},
                )
                return JSONResponse(status_code=error.status_code, content=body)
            raise HaasError(
                502, "invalid_request_error", "haas_adapter_error", retryable=True
            ) from exc

        adk_events = [runtime.event_log.project_adk(e) for e in result.events]
        if key_hash:
            runtime.store.complete(key_hash, {"status_code": 200, "events": adk_events})

        return adk_events
    finally:
        if lease_id:
            runtime.admission.release_run(lease_id)


async def _run_delegated(
    runtime: _Runtime,
    *,
    app: HarnessRecord,
    user_id: str,
    session_id: str,
    message: dict[str, Any],
    delegated: DelegatedSessionRecord,
    streaming: bool,
    key_hash: str | None,
    lease_id: str | None,
) -> Any:
    key = _session_key(app.id, user_id, session_id)
    holder = f"run_{uuid.uuid4().hex[:8]}"
    try:
        lease = runtime.store.acquire_lease(key, holder)
    except LeaseConflictError as exc:
        if key_hash:
            runtime.store.release(key_hash)
        if lease_id:
            runtime.admission.release_run(lease_id)
        raise HaasError(409, "invalid_request_error", "session_busy", retryable=True) from exc
    token = lease.token

    existing_session = runtime.store.get_session(key)
    if existing_session is None:
        existing_session = SessionRecord(
            id=session_id,
            appName=app.id,
            userId=user_id,
            delegatedSessionRef=_delegated_session_ref(delegated),
        )
    session = runtime.store.put_session(
        replace(existing_session, delegatedSessionRef=_delegated_session_ref(delegated))
    )
    invocation = InvocationRecord(
        id=f"inv_{uuid.uuid4().hex[:16]}",
        sessionId=session_id,
        appName=app.id,
        turnId=f"turn_{uuid.uuid4().hex[:16]}",
        userId=user_id,
        status="running",
    )
    turn = TurnRecord(
        id=invocation.turnId,
        invocationId=invocation.id,
        sessionId=session_id,
        status="running",
    )
    runtime.store.put_invocation(invocation)
    runtime.store.put_turn(turn)

    async def events() -> AsyncIterator[CanonicalEventRecord]:
        nonlocal delegated, session, invocation, turn
        primary_workspace = delegated.mountManifest["primaryWorkspace"]
        lock_acquired = False
        try:
            _validate_mount_manifest(delegated.mountManifest)
            lock = runtime.store.acquire_workspace_lock(
                primary_workspace["hostPathCanonical"],
                delegated.id,
                primary_workspace["access"],
                ttl_ms=delegated.delegationPolicySnapshot["maxContainerLifetimeSeconds"] * 1000,
            )
            if not lock.acquired:
                raise HaasError(
                    409,
                    "invalid_request_error",
                    "haas_workspace_lock_busy",
                    safe_reason="workspace_lock_busy",
                    retryable=True,
                )
            lock_acquired = True
            restored_runtime = await runtime.delegated_containers.restore(delegated)
            delegated = runtime.store.update_delegated_runtime(delegated.id, restored_runtime)
            session = runtime.store.put_session(
                replace(session, delegatedSessionRef=_delegated_session_ref(delegated))
            )
            body = {
                "appName": app.id,
                "userId": user_id,
                "sessionId": session_id,
                "newMessage": message,
                "streaming": True,
            }
            async for event in runtime.delegated_containers.run_stream(delegated, body):
                content = event.get("content") if isinstance(event, dict) else {}
                actions = event.get("actions") if isinstance(event, dict) else {}
                canonical = runtime.event_log.append(
                    app_name=app.id,
                    user_id=user_id,
                    invocation_id=invocation.id,
                    session_id=session_id,
                    turn_id=turn.id,
                    harness_id=app.id,
                    adapter_id="delegated-container",
                    author=str(event.get("author") or delegated.harnessBase)
                    if isinstance(event, dict)
                    else delegated.harnessBase,
                    content=content if isinstance(content, dict) else {},
                    actions=actions if isinstance(actions, dict) else {},
                )
                delta = canonical.actions.get("stateDelta")
                if isinstance(delta, dict):
                    session.state = _deep_merge(session.state, delta)
                    session = runtime.store.put_session(session)
                yield canonical
            final_status = str(session.state.get("status") or "completed")
            if final_status not in {"completed", "failed", "cancelled", "incomplete"}:
                final_status = "completed"
            invocation.status = final_status
            turn.status = final_status
        except (DelegatedContainerUnavailable, MountManifestInvalid, HaasError) as exc:
            if lock_acquired and (
                isinstance(exc, MountManifestInvalid)
                or (
                    isinstance(exc, DelegatedContainerUnavailable)
                    and delegated.runtime.containerId is None
                )
            ):
                runtime.store.release_workspace_lock(
                    primary_workspace["hostPathCanonical"], delegated.id
                )
            invocation.status = "failed"
            turn.status = "failed"
            if isinstance(exc, HaasError):
                code = exc.code
                safe_reason = exc.safe_reason
                status_code = exc.status_code
                retryable = exc.retryable
            elif isinstance(exc, MountManifestInvalid):
                code = "haas_delegation_mount_invalid"
                safe_reason = "delegation_mount_invalid"
                status_code = 403
                retryable = False
            else:
                code = "haas_delegation_backend_unavailable"
                safe_reason = "delegation_backend_unavailable"
                status_code = 503
                retryable = True
            terminal = runtime.event_log.append(
                app_name=app.id,
                user_id=user_id,
                invocation_id=invocation.id,
                session_id=session_id,
                turn_id=turn.id,
                harness_id=app.id,
                adapter_id="delegated-container",
                author=delegated.harnessBase,
                content={"role": "model", "parts": []},
                actions={
                    "stateDelta": {
                        "status": "failed",
                        "safeReason": safe_reason,
                    }
                },
            )
            session.state = _deep_merge(session.state, terminal.actions["stateDelta"])
            runtime.store.put_session(session)
            yield terminal
            raise HaasError(
                status_code,
                "invalid_request_error",
                code,
                safe_reason=safe_reason,
                retryable=retryable,
            ) from exc
        finally:
            invocation.completedAtMs = int(time.time() * 1000)
            turn.completedAtMs = invocation.completedAtMs
            runtime.store.put_invocation(invocation)
            runtime.store.put_turn(turn)
            runtime.store.put_session(session)
            runtime.store.release_lease(key, holder, token)

    if streaming:
        adk_events: list[dict[str, Any]] = []
        queue: asyncio.Queue[str | None] = asyncio.Queue()
        disconnected = False

        async def produce() -> None:
            nonlocal disconnected
            try:
                try:
                    async for event in events():
                        adk_event = runtime.event_log.project_adk(event)
                        adk_events.append(adk_event)
                        if not disconnected:
                            queue.put_nowait(
                                f"data: {json.dumps(adk_event, separators=(',', ':'))}\n\n"
                            )
                except HaasError:
                    # The delegated runtime already emitted a terminal failure
                    # event.  SSE headers may already be committed, so complete
                    # the stream with persisted evidence instead of raising
                    # through the response task.
                    pass
                finally:
                    if key_hash:
                        runtime.store.complete(key_hash, {"events": adk_events})
            finally:
                if lease_id:
                    runtime.admission.release_run(lease_id)
                if not disconnected:
                    queue.put_nowait(HEARTBEAT_FRAME)
                queue.put_nowait(None)

        producer = asyncio.create_task(produce())

        async def frames() -> Any:
            nonlocal disconnected
            try:
                while True:
                    frame = await queue.get()
                    if frame is None:
                        break
                    yield frame
            finally:
                disconnected = True
                if producer.done():
                    producer.result()

        return StreamingResponse(frames(), media_type="text/event-stream")

    delegated_adk_events: list[dict[str, Any]] = []
    try:
        async for event in events():
            delegated_adk_events.append(runtime.event_log.project_adk(event))
    finally:
        if key_hash:
            runtime.store.complete(key_hash, {"events": delegated_adk_events})
        if lease_id:
            runtime.admission.release_run(lease_id)
    return delegated_adk_events


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
    if not isinstance(cached, dict):
        events: list[Any] = []
        status_code = 200
        body: Any = None
    else:
        events = cached.get("events", [])
        status_code = int(cached.get("status_code", 200))
        body = cached.get("body")
    if status_code >= 400:
        return JSONResponse(
            status_code=status_code,
            content=body if isinstance(body, dict) else {"detail": "request_failed"},
        )
    if not streaming:
        return events

    async def frames() -> Any:
        for event in events:
            yield f"data: {json.dumps(event, separators=(',', ':'))}\n\n"
        yield ": keep-alive\n\n"

    return StreamingResponse(frames(), media_type="text/event-stream")
