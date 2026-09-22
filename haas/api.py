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
from math import isfinite
from typing import Annotated, Any, cast
from urllib.parse import quote

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
from haas.execution_evidence import ExecutionEvidenceStore
from haas.harnesses import FakeAdapter, HarnessAdapter
from haas.harnesses.codex_app_server.rpc import CodexConnectionError
from haas.identity import (
    IdentityProvider,
    InvalidCredentialError,
    MissingCredentialError,
    Principal,
    StaticTokenIdentityProvider,
)
from haas.observability import Metrics, StatusSnapshot, StructuredLogger
from haas.profiles import (
    HarnessProfileService,
    ProfileConflictError,
    ProfileNotFoundError,
    execution_intent_fingerprint,
    profile_to_dict,
)
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
from haas.runtime.reconciler import reconcile_delegated_policy
from haas.sessions import (
    AdapterTurnError,
    AdapterTurnTimeoutError,
    InvocationNotFoundError,
    InvocationNotResumableError,
    InvocationNotRunningError,
    PolicyRevisionConflictError,
    PolicyUpdateInvalidError,
    ResumeRequiredError,
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
    IdempotencyExpiredError,
    InputRequestNotFoundError,
    InputRequestStateConflictError,
    InvocationRecord,
    LeaseConflictError,
    MemoryStore,
    SessionRecord,
    TurnRecord,
)

SSE_HEARTBEAT_SECONDS = 15.0

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


def _timeout_seconds_from_haas(options: dict[str, Any]) -> float | None:
    if "timeoutSeconds" not in options:
        return None
    value = options["timeoutSeconds"]
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError("timeoutSeconds must be numeric")
    seconds = float(value)
    if not isfinite(seconds):
        raise ValueError("timeoutSeconds must be finite")
    if seconds <= 0:
        raise ValueError("timeoutSeconds must be positive")
    return min(seconds, 86_400)


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
        "network": {"defaultAction": "allow", "allow": []},
        "tools": {"disabled": [], "approvalMode": "on-request"},
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
    network = policy.get("network", {"defaultAction": "deny", "allow": []})
    if (
        not isinstance(network, dict)
        or network.get("defaultAction") not in {"deny", "allow"}
        or not isinstance(network.get("allow", []), list)
        or not all(isinstance(item, str) for item in network.get("allow", []))
    ):
        raise ValueError("invalid delegation network policy")
    tools = policy.get("tools", {"disabled": [], "approvalMode": "never"})
    if (
        not isinstance(tools, dict)
        or tools.get("approvalMode") not in {"never", "on-request", "always"}
        or not isinstance(tools.get("disabled", []), list)
        or not all(isinstance(item, str) for item in tools.get("disabled", []))
    ):
        raise ValueError("invalid delegation tools policy")


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


def _delegated_session_from_body(body: dict[str, Any], config: AppConfig) -> DelegatedSessionRecord:
    for key in ("managerSessionId", "haasSessionId", "haasUserId", "harnessId"):
        if not isinstance(body.get(key), str) or not body[key]:
            raise ValueError(f"missing {key}")
    image = body.get("image")
    provider = body.get("provider")
    profile_ref = body.get("profileRef")
    workspace_mode = body.get("workspaceMode", "bind_mount")
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
    if profile_ref is not None:
        _validate_profile_ref(profile_ref)
    else:
        profile_ref = {}
    if not isinstance(workspace_mode, str) or not workspace_mode:
        raise ValueError("invalid workspace mode")
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
        profileRef=profile_ref,
        provider=provider,
        workspaceMode=workspace_mode,
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


def _delegated_session_matches_body(record: DelegatedSessionRecord, body: dict[str, Any]) -> bool:
    return (
        record.haasSessionId == body.get("haasSessionId")
        and record.haasUserId == body.get("haasUserId")
        and record.harnessId == body.get("harnessId")
        and record.harnessBase == str(body.get("harnessBase") or "codex")
        and record.image == body.get("image")
        and record.profileRef == body.get("profileRef", record.profileRef)
        and record.provider == body.get("provider")
        and record.workspaceMode == body.get("workspaceMode", "bind_mount")
        and record.mountManifest == body.get("mountManifest")
        and record.delegationPolicySnapshot
        == (body.get("delegationPolicySnapshot") or record.delegationPolicySnapshot)
    )


def _validate_profile_ref(profile_ref: Any) -> None:
    if not isinstance(profile_ref, dict):
        raise ValueError("invalid profile ref")
    if (
        not isinstance(profile_ref.get("profileId"), str)
        or not profile_ref["profileId"]
        or not isinstance(profile_ref.get("profileVersion"), int)
        or profile_ref["profileVersion"] < 1
        or not isinstance(profile_ref.get("profileFingerprint"), str)
        or not profile_ref["profileFingerprint"]
    ):
        raise ValueError("invalid profile ref")


def _ensure_delegated_access(
    runtime: _Runtime, principal: Principal, record: DelegatedSessionRecord
) -> None:
    if not runtime.identity.owns(principal, user_id=record.haasUserId):
        raise HaasError(404, "invalid_request_error", "haas_delegated_session_not_found")
    try:
        runtime.registry.get_scoped(principal, record.harnessId)
    except HarnessNotFoundError as exc:
        raise HaasError(404, "invalid_request_error", "haas_delegated_session_not_found") from exc


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
            for e in runtime.event_log.read_session(session.appName, session.userId, session.id)
        ]
    return data


def _session_control(session: SessionRecord) -> dict[str, Any]:
    return {
        "controlState": session.controlState,
        "supportsResume": session.supportsResume,
        "resumableInvocationId": session.resumableInvocationId,
    }


def _session_policy_revision(session: SessionRecord) -> dict[str, Any]:
    data: dict[str, Any] = {
        "sessionId": session.id,
        "desiredRevision": session.desiredRevision,
        "appliedRevision": session.appliedRevision,
        "status": session.policyStatus,
        "desiredPolicy": session.desiredPolicy,
        "appliedPolicy": session.appliedPolicy,
    }
    result = session.lastPolicyUpdateResult or {}
    if session.policyStatus == "failed":
        data["code"] = result.get("code", "haas_internal_error")
        data["safeReason"] = result.get("safeReason", "policy_apply_failed")
    return data


def _invocation_envelope(runtime: _Runtime, invocation: InvocationRecord) -> dict[str, Any]:
    session = runtime.store.get_session(
        (invocation.appName, invocation.userId, invocation.sessionId)
    )
    if session is None:
        raise HaasError(404, "invalid_request_error", "haas_invocation_not_found")
    return {
        "data": {
            "id": invocation.id,
            "sessionId": invocation.sessionId,
            "turnId": invocation.turnId,
            "appName": invocation.appName,
            "status": invocation.status,
            "continuedFromInvocationId": invocation.continuedFromInvocationId,
            "continuedFromTurnId": invocation.continuedFromTurnId,
            "sessionControl": _session_control(session),
            "acceptedAtMs": invocation.acceptedAtMs,
            "startedAtMs": invocation.startedAtMs,
            "completedAtMs": invocation.completedAtMs,
            "timeoutSeconds": invocation.timeoutSeconds,
            "deadlineAtMs": invocation.deadlineAtMs,
            "idempotencyExpiresAtMs": invocation.idempotencyExpiresAtMs,
        },
        "traceId": _trace_id(),
    }


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
    profiles: HarnessProfileService
    execution_evidence: ExecutionEvidenceStore


def build_app(
    config: AppConfig | None = None,
    *,
    adapter: HarnessAdapter | None = None,
    identity_tokens: dict[str, Principal] | None = None,
    delegated_containers: DelegatedContainerRuntime | None = None,
    store: MemoryStore | None = None,
    run_quota: int = 20,
    rate_limit: int = 100,
    max_file_bytes: int | None = None,
    session_lease_ttl_ms: int | None = None,
    session_lease_renew_interval_ms: int | None = None,
    session_turn_timeout_s: float | None = None,
) -> FastAPI:
    config_provided = config is not None
    config = config if config is not None else AppConfig()
    if session_lease_ttl_ms is None:
        session_lease_ttl_ms = config.session_runtime.lease_ttl_ms
    if session_lease_renew_interval_ms is None:
        session_lease_renew_interval_ms = config.session_runtime.lease_renew_interval_ms
    if session_turn_timeout_s is None:
        session_turn_timeout_s = config.session_runtime.turn_timeout_seconds
    if store is None and config_provided:
        from haas.config import build_store

        store = build_store(config)
    store = store or MemoryStore()
    adapter = adapter or FakeAdapter()
    bind_interaction_store = getattr(adapter, "bind_interaction_store", None)
    if callable(bind_interaction_store):
        bind_interaction_store(store)
    execution_evidence = ExecutionEvidenceStore()
    bind_evidence_store = getattr(adapter, "bind_execution_evidence_store", None)
    if callable(bind_evidence_store):
        bind_evidence_store(execution_evidence)
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
    artifact_policy = ArtifactPolicy()
    if max_file_bytes is not None:
        artifact_policy.maxFileBytes = max_file_bytes
    artifacts = ArtifactStore(artifact_policy)
    sessions = SessionRuntime(
        store=store,
        registry=registry,
        adapter=adapter,
        event_log=event_log,
        lease_ttl_ms=session_lease_ttl_ms,
        lease_renew_interval_ms=session_lease_renew_interval_ms,
        turn_timeout_s=session_turn_timeout_s,
        artifacts=artifacts,
    )
    runtime = _Runtime(
        store=store,
        registry=registry,
        event_log=event_log,
        admission=admission,
        identity=identity,
        sessions=sessions,
        adapter=adapter,
        artifacts=artifacts,
        delegated_containers=delegated_containers or DisabledDelegatedContainerRuntime(),
        metrics=Metrics(),
        logger=StructuredLogger(),
        profiles=HarnessProfileService(store=store, registry=registry),
        execution_evidence=execution_evidence,
    )

    app = FastAPI(title="Harness As A Service", version="2026-08-26")
    app.state.haas_config = config
    app.state.runtime = runtime

    @app.exception_handler(HaasError)
    async def haas_error_handler(request: Request, exc: HaasError) -> JSONResponse:
        evidence_error = exc.code in {
            "haas_execution_evidence_not_found",
            "haas_execution_evidence_expired",
        }
        return JSONResponse(
            status_code=exc.status_code,
            content=_haas_error_content(exc),
            headers=(
                {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}
                if evidence_error else None
            ),
        )

    @app.get("/v1/haas/health")
    async def health() -> dict[str, Any]:
        return {"data": {"status": "ok"}, "traceId": "tr_local"}

    probe_cache: dict[str, Any] = {"result": None, "at": 0.0}

    async def _execution_ready() -> tuple[str, str | None]:
        if adapter.base == "codex":
            if not store.list_profiles(status="active"):
                return "not_ready", "profile_not_configured"
            proxy = sessions.model_proxy
            if proxy is None or not proxy.base_url or not proxy.resolver.available:
                return "not_ready", "model_proxy_unavailable"
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
        if scope == "control":
            return {
                "data": {"status": "ready", "scope": scope},
                "traceId": "tr_local",
            }
        if scope == "execution":
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

    @app.get("/v1/haas/capabilities")
    async def capabilities(request: Request) -> dict[str, Any]:
        principal = await _authenticate(runtime.identity, request)
        records = runtime.registry.list_active(principal)
        await _execution_ready()
        probe = probe_cache["result"]
        raw = getattr(probe, "capabilities", {}) if probe is not None else {}

        def state(
            value: Any, *, enforcement: str = "none", unavailable: str = "unsupported"
        ) -> dict[str, Any]:
            allowed_modes = {
                "native",
                "proxy",
                "emulated",
                "best_effort",
                "advisory",
                "instructions",
                "workspace_scan",
                "estimated",
                "unattended_only",
                "human_bridge",
                "none",
            }
            if value is True:
                mode = "native"
            elif value in {None, False, "unsupported", "unavailable"}:
                mode = "none"
            else:
                mode = str(value)
            if mode == "hard":
                mode, enforcement = "native", "hard"
            elif mode not in allowed_modes:
                mode = "none"
            return {
                "status": (
                    "available"
                    if mode != "none"
                    else "unavailable"
                    if value == "unavailable"
                    else unavailable
                ),
                "mode": mode,
                "enforcement": enforcement,
            }

        harness_caps = {
            "streaming": state(raw.get("streaming")),
            "sessionContinuation": state(raw.get("sessionContinuation")),
            "pausing": state(raw.get("pausing")),
            "cancellation": state(raw.get("cancellation")),
            "approval": state(raw.get("approval")),
            "input": state(raw.get("input")),
            "toolRestriction": state(raw.get("toolRestriction"), enforcement="advisory"),
            "mcp": state(raw.get("mcp")),
            "skills": state(raw.get("skills")),
            "files": state(raw.get("files")),
            "usage": state(raw.get("usage")),
        }
        interaction = (
            "human_bridge" if raw.get("approval") == raw.get("input") == "human_bridge" else "none"
        )
        return {
            "data": {
                "object": "haas_capabilities",
                "protocolVersion": app.version,
                "adkProtocolVersion": "2.0",
                "features": {
                    "runSse": state(True),
                    "delegatedSessions": state(True),
                    "modelProxy": state(sessions.model_proxy is not None, enforcement="hard"),
                    "mcpProxy": state(False),
                    "skillMaterialization": state(False),
                    "approvalHandling": state(interaction),
                    "inputHandling": state(interaction),
                    "artifacts": state(True),
                },
                "workspaceModes": [
                    {
                        "id": "bind_mount",
                        **state("native", enforcement="hard"),
                    }
                ],
                "harnesses": [
                    {
                        "id": record.id,
                        "base": record.base,
                        "status": (
                            "available"
                            if probe is not None and probe.status == "ready"
                            else "unavailable"
                        ),
                        "capabilities": harness_caps,
                    }
                    for record in records
                ],
                "generatedAtMs": int(time.time() * 1000),
            },
            "traceId": _trace_id(),
        }

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

    @app.get("/v1/haas/sessions/{session_id}/invocations/{invocation_id}")
    async def get_invocation(
        session_id: str, invocation_id: str, request: Request
    ) -> dict[str, Any]:
        principal = await _authenticate(runtime.identity, request)
        invocation = _resolve_visible_invocation(runtime, principal, session_id, invocation_id)
        invocation = runtime.sessions.reconcile_invocation_readback(session_id, invocation.id)
        return _invocation_envelope(runtime, invocation)

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
        return {
            "id": session.id,
            "appName": session.appName,
            "userId": session.userId,
            "state": session.state,
            "lastUpdateTime": session.updatedAtMs / 1000.0,
        }

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
            runtime.execution_evidence.delete_session(session_id)
        except SessionNotFoundError as exc:
            raise HaasError(404, "invalid_request_error", "session_not_found") from exc
        return JSONResponse(status_code=204, content=None)

    @app.post("/v1/haas/sessions/{session_id}/invocations/{invocation_id}/cancel")
    async def cancel_invocation(
        session_id: str, invocation_id: str, request: Request
    ) -> dict[str, Any]:
        principal = await _authenticate(runtime.identity, request)
        invocation = _resolve_visible_invocation(
            runtime, principal, session_id, invocation_id
        )
        key_hash, replay = await _reserve_mutation(runtime.store, request, {}, principal)
        if replay is not None:
            return replay
        try:
            delegated = runtime.store.get_delegated_session_by_haas_session(session_id)
            if delegated is not None and invocation.status == "running":
                _ensure_delegated_access(runtime, principal, delegated)
                await runtime.delegated_containers.cancel(delegated, invocation_id)
            else:
                invocation = await runtime.sessions.cancel_invocation(session_id, invocation_id)
        except DelegatedContainerUnavailable as exc:
            if key_hash:
                runtime.store.release(key_hash)
            raise HaasError(
                503,
                "invalid_request_error",
                "haas_delegation_backend_unavailable",
                safe_reason="delegation_cancel_unavailable",
                retryable=True,
            ) from exc
        except InvocationNotFoundError as exc:
            if key_hash:
                runtime.store.release(key_hash)
            raise HaasError(404, "invalid_request_error", "haas_invocation_not_found") from exc
        except SessionBusyError as exc:
            if key_hash:
                runtime.store.release(key_hash)
            raise HaasError(409, "invalid_request_error", "session_busy", retryable=True) from exc
        except AdapterTurnError as exc:
            if key_hash:
                runtime.store.release(key_hash)
            raise HaasError(
                502,
                "invalid_request_error",
                "haas_adapter_error",
                safe_reason="adapter_error",
                retryable=True,
            ) from exc
        except HaasError:
            if key_hash:
                runtime.store.release(key_hash)
            raise
        envelope = _invocation_envelope(runtime, invocation)
        if key_hash:
            runtime.store.complete(key_hash, envelope)
        return envelope

    @app.post("/v1/haas/sessions/{session_id}/invocations/{invocation_id}/pause")
    async def pause_invocation(
        session_id: str, invocation_id: str, request: Request
    ) -> dict[str, Any]:
        principal = await _authenticate(runtime.identity, request)
        _resolve_visible_invocation(runtime, principal, session_id, invocation_id)
        key_hash, replay = await _reserve_mutation(runtime.store, request, {}, principal)
        if replay is not None:
            return replay
        try:
            invocation = await runtime.sessions.pause_invocation(session_id, invocation_id)
        except InvocationNotFoundError as exc:
            if key_hash:
                runtime.store.release(key_hash)
            raise HaasError(404, "invalid_request_error", "haas_invocation_not_found") from exc
        except InvocationNotRunningError as exc:
            if key_hash:
                runtime.store.release(key_hash)
            raise HaasError(
                409, "invalid_request_error", "haas_invocation_not_running"
            ) from exc
        except AdapterTurnError as exc:
            if key_hash:
                runtime.store.release(key_hash)
            raise HaasError(
                502,
                "invalid_request_error",
                "haas_adapter_error",
                safe_reason="adapter_error",
                retryable=True,
            ) from exc
        envelope = _invocation_envelope(runtime, invocation)
        if key_hash:
            runtime.store.complete(key_hash, envelope)
        return envelope

    @app.post("/v1/haas/sessions/{session_id}/invocations/{invocation_id}/continue")
    async def continue_invocation(
        session_id: str, invocation_id: str, request: Request
    ) -> StreamingResponse:
        principal = await _authenticate(runtime.identity, request)
        _resolve_visible_invocation(runtime, principal, session_id, invocation_id)
        body = await _json_object(request)
        instruction = body.get("additionalInstruction")
        if instruction is not None and (
            not isinstance(instruction, str) or len(instruction) > 4000
        ):
            raise HaasError(400, "invalid_request_error", "invalid_input")
        key_hash, replay = await _reserve_mutation(runtime.store, request, body, principal)
        if replay is not None:
            return cast(StreamingResponse, _render_cached(replay, streaming=True))
        try:
            stream = runtime.sessions.continue_stream(
                session_id, invocation_id, instruction=instruction
            )
            first = await stream.__anext__()
        except InvocationNotFoundError as exc:
            if key_hash:
                runtime.store.release(key_hash)
            raise HaasError(404, "invalid_request_error", "haas_invocation_not_found") from exc
        except InvocationNotResumableError as exc:
            if key_hash:
                runtime.store.release(key_hash)
            raise HaasError(
                409, "invalid_request_error", "haas_invocation_not_resumable"
            ) from exc
        except SessionBusyError as exc:
            if key_hash:
                runtime.store.release(key_hash)
            raise HaasError(409, "invalid_request_error", "session_busy", retryable=True) from exc
        except StopAsyncIteration as exc:
            if key_hash:
                runtime.store.release(key_hash)
            raise HaasError(
                502, "invalid_request_error", "haas_adapter_error"
            ) from exc

        if first.invocationId is None:
            if key_hash:
                runtime.store.release(key_hash)
            raise HaasError(503, "service_unavailable", "haas_store_unavailable")
        expires_at_ms = _accept_idempotency(runtime, key_hash, first.invocationId)
        headers = _accepted_headers(
            invocation_id=first.invocationId,
            session_id=first.sessionId,
            expires_at_ms=expires_at_ms,
        )
        adk_events = [runtime.event_log.project_adk(first)]

        async def frames() -> Any:
            yield runtime.event_log.sse_frame(first)
            try:
                try:
                    async for event in stream:
                        adk_events.append(runtime.event_log.project_adk(event))
                        yield runtime.event_log.sse_frame(event)
                except AdapterTurnError:
                    # The runtime persists and yields the accepted invocation's
                    # failed terminal before raising. Once SSE headers are sent,
                    # that terminal is the protocol result; close cleanly.
                    pass
            finally:
                if key_hash:
                    runtime.store.complete(
                        key_hash,
                        {
                            "events": adk_events,
                            "idempotency_expires_at_ms": expires_at_ms,
                            "invocation_id": first.invocationId,
                            "session_id": first.sessionId,
                        },
                    )

        return StreamingResponse(frames(), media_type="text/event-stream", headers=headers)

    @app.post("/v1/haas/sessions/{session_id}/policy")
    async def update_session_policy(session_id: str, request: Request) -> Response:
        principal = await _authenticate(runtime.identity, request)
        body = await _json_object(request)
        if not request.headers.get("Idempotency-Key"):
            raise HaasError(400, "invalid_request_error", "invalid_input")
        key_hash, replay = await _reserve_mutation(runtime.store, request, body, principal)
        if replay is not None:
            data = replay.get("data", {})
            status_code = 200 if data.get("status") == "applied" else 202
            return JSONResponse(status_code=status_code, content=replay)
        try:
            session = _resolve_visible_session(runtime, principal, session_id)
            expected_revision = body.get("expectedRevision")
            policy = body.get("policy")
            if (
                not isinstance(expected_revision, int)
                or isinstance(expected_revision, bool)
                or expected_revision < 1
                or not isinstance(policy, dict)
            ):
                raise PolicyUpdateInvalidError(session_id)
            updated = runtime.sessions.update_policy(
                session, expected_revision=expected_revision, delta=policy
            )
        except PolicyRevisionConflictError as exc:
            if key_hash:
                runtime.store.release(key_hash)
            raise HaasError(
                409, "invalid_request_error", "haas_policy_revision_conflict"
            ) from exc
        except PolicyUpdateInvalidError as exc:
            if key_hash:
                runtime.store.release(key_hash)
            raise HaasError(400, "invalid_request_error", "invalid_input") from exc
        except HaasError:
            if key_hash:
                runtime.store.release(key_hash)
            raise
        envelope = {"data": _session_policy_revision(updated), "traceId": _trace_id()}
        if key_hash:
            runtime.store.complete(key_hash, envelope)
        status_code = 200 if updated.policyStatus == "applied" else 202
        return JSONResponse(status_code=status_code, content=envelope)

    @app.post("/v1/haas/sessions/{session_id}/approvals/{approval_id}")
    async def resolve_approval(
        session_id: str, approval_id: str, request: Request
    ) -> dict[str, Any]:
        principal = await _authenticate(runtime.identity, request)
        body = await _json_object(request)
        key_hash, replay = await _reserve_mutation(runtime.store, request, body, principal)
        if replay is not None:
            return replay
        try:
            decision = body.get("decision")
            runtime.store.reconcile_pending_interactions(session_id)
            approval = runtime.store.get_approval(approval_id)
            if approval is None or approval.sessionId != session_id:
                raise HaasError(404, "invalid_request_error", "haas_approval_not_found")
            if not _has_session_access(runtime, principal, session_id):
                raise HaasError(404, "invalid_request_error", "haas_approval_not_found")
            if decision not in {"approved", "denied"} or body.get("scope") != "action":
                raise HaasError(400, "invalid_request_error", "invalid_input")
            if approval.status != "waiting":
                if approval.status == decision and approval.decision == body:
                    envelope = {"data": approval.to_dict(), "traceId": _trace_id()}
                    if key_hash:
                        runtime.store.complete(key_hash, envelope)
                    return envelope
                raise HaasError(409, "invalid_request_error", "haas_approval_state_conflict")
            responder = getattr(runtime.adapter, "respond_interaction", None)
            if callable(responder) and approval.nativeRequestId is not None:
                await responder(approval, body)
            resolved = runtime.store.resolve_approval(approval_id, body)
        except HaasError:
            if key_hash:
                runtime.store.release(key_hash)
            raise
        except CodexConnectionError as exc:
            if key_hash:
                runtime.store.release(key_hash)
            raise HaasError(409, "invalid_request_error", "haas_approval_state_conflict") from exc
        except ApprovalNotFoundError as exc:
            if key_hash:
                runtime.store.release(key_hash)
            raise HaasError(404, "invalid_request_error", "haas_approval_not_found") from exc
        except ApprovalStateConflictError as exc:
            if key_hash:
                runtime.store.release(key_hash)
            raise HaasError(409, "invalid_request_error", "haas_approval_state_conflict") from exc
        runtime.logger.event(
            "haas.approval.resolved",
            {"approvalId": approval_id, "sessionId": session_id, "status": resolved.status},
        )
        session = _resolve_visible_session(runtime, principal, session_id)
        runtime.event_log.append_typed(
            type_="haas.approval.resolved",
            app_name=session.appName,
            user_id=session.userId,
            invocation_id=resolved.invocationId,
            session_id=session_id,
            turn_id=resolved.turnId,
            harness_id=session.appName,
            adapter_id=runtime.adapter.adapter_id,
            author="haas",
            content={"role": "model", "parts": []},
            actions={},
            haas={"approvalId": approval_id, "status": resolved.status},
        )
        envelope = {"data": resolved.to_dict(), "traceId": _trace_id()}
        if key_hash:
            runtime.store.complete(key_hash, envelope)
        return envelope

    @app.get("/v1/haas/sessions/{session_id}/approvals")
    async def list_approvals(
        session_id: str, request: Request, status: str = "waiting"
    ) -> dict[str, Any]:
        principal = await _authenticate(runtime.identity, request)
        _resolve_visible_session(runtime, principal, session_id)
        runtime.store.reconcile_pending_interactions(session_id)
        return {
            "data": [
                record.to_dict()
                for record in runtime.store.list_approvals(session_id, status=status)
            ],
            "traceId": _trace_id(),
        }

    @app.get("/v1/haas/sessions/{session_id}/input-requests")
    async def list_input_requests(
        session_id: str, request: Request, status: str = "waiting"
    ) -> dict[str, Any]:
        principal = await _authenticate(runtime.identity, request)
        _resolve_visible_session(runtime, principal, session_id)
        runtime.store.reconcile_pending_interactions(session_id)
        return {
            "data": [
                record.to_dict()
                for record in runtime.store.list_input_requests(session_id, status=status)
            ],
            "traceId": _trace_id(),
        }

    @app.post("/v1/haas/sessions/{session_id}/input-requests/{input_request_id}")
    async def answer_input_request(
        session_id: str, input_request_id: str, request: Request
    ) -> dict[str, Any]:
        principal = await _authenticate(runtime.identity, request)
        _resolve_visible_session(runtime, principal, session_id)
        body = await _json_object(request)
        key_hash, replay = await _reserve_mutation(runtime.store, request, body, principal)
        if replay is not None:
            return replay
        try:
            answers = body.get("answers")
            if not isinstance(answers, dict):
                raise HaasError(400, "invalid_request_error", "invalid_input")
            runtime.store.reconcile_pending_interactions(session_id)
            record = runtime.store.get_input_request(input_request_id)
            if record is None or record.sessionId != session_id:
                raise HaasError(404, "invalid_request_error", "haas_input_request_not_found")
            expected_ids = {str(question.get("id")) for question in record.questions}
            if set(answers) != expected_ids or any(
                not isinstance(answer, dict)
                or (
                    not isinstance(answer.get("values"), list)
                    and not isinstance(answer.get("secretRef"), str)
                )
                for answer in answers.values()
            ):
                raise HaasError(400, "invalid_request_error", "invalid_input")
            if record.status != "waiting":
                if record.status == "answered" and record.answers == body:
                    envelope = {"data": record.to_dict(), "traceId": _trace_id()}
                    if key_hash:
                        runtime.store.complete(key_hash, envelope)
                    return envelope
                raise HaasError(
                    409, "invalid_request_error", "haas_input_request_state_conflict"
                )
            responder = getattr(runtime.adapter, "respond_interaction", None)
            if callable(responder):
                await responder(record, body)
            resolved = runtime.store.resolve_input_request(input_request_id, body)
        except HaasError:
            if key_hash:
                runtime.store.release(key_hash)
            raise
        except CodexConnectionError as exc:
            if key_hash:
                runtime.store.release(key_hash)
            raise HaasError(
                409, "invalid_request_error", "haas_input_request_state_conflict"
            ) from exc
        except InputRequestNotFoundError as exc:
            if key_hash:
                runtime.store.release(key_hash)
            raise HaasError(404, "invalid_request_error", "haas_input_request_not_found") from exc
        except InputRequestStateConflictError as exc:
            if key_hash:
                runtime.store.release(key_hash)
            raise HaasError(
                409, "invalid_request_error", "haas_input_request_state_conflict"
            ) from exc
        session = _resolve_visible_session(runtime, principal, session_id)
        runtime.event_log.append_typed(
            type_="haas.input.resolved",
            app_name=session.appName,
            user_id=session.userId,
            invocation_id=resolved.invocationId,
            session_id=session_id,
            turn_id=resolved.turnId,
            harness_id=session.appName,
            adapter_id=runtime.adapter.adapter_id,
            author="haas",
            content={"role": "model", "parts": []},
            actions={},
            haas={"inputRequestId": input_request_id, "status": resolved.status},
        )
        envelope = {"data": resolved.to_dict(), "traceId": _trace_id()}
        if key_hash:
            runtime.store.complete(key_hash, envelope)
        return envelope

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
            raise HaasError(410, "invalid_request_error", "haas_offset_expired") from exc

        async def frames() -> Any:
            for event in events:
                yield runtime.event_log.haas_frame(event)
            yield HEARTBEAT_FRAME

        return StreamingResponse(frames(), media_type="text/event-stream")

    @app.get("/v1/haas/sessions/{session_id}/events-page")
    async def session_events_page(
        session_id: str,
        request: Request,
        after_event_id: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        principal = await _authenticate(runtime.identity, request)
        if limit < 1 or limit > 1000:
            raise HaasError(400, "invalid_request_error", "invalid_input")
        session = _resolve_visible_session(runtime, principal, session_id)
        try:
            events = runtime.event_log.read_session(
                session.appName, session.userId, session.id, after_event_id
            )
        except CursorNotFoundError as exc:
            raise HaasError(410, "invalid_request_error", "haas_offset_expired") from exc
        page = events[:limit]
        return {
            "data": [runtime.event_log.project_haas(event) for event in page],
            "nextCursor": page[-1].eventId if len(events) > limit else None,
            "traceId": _trace_id(),
        }

    @app.get("/v1/haas/sessions/{session_id}/invocations/{invocation_id}/events")
    async def invocation_events(
        session_id: str, invocation_id: str, request: Request
    ) -> StreamingResponse:
        principal = await _authenticate(runtime.identity, request)
        invocation = _resolve_visible_invocation(runtime, principal, session_id, invocation_id)
        events = runtime.event_log.read_invocation(
            invocation.appName, invocation.userId, invocation.sessionId, invocation_id
        )

        async def frames() -> Any:
            for event in events:
                yield runtime.event_log.haas_frame(event)
            yield HEARTBEAT_FRAME

        return StreamingResponse(frames(), media_type="text/event-stream")

    @app.get(
        "/v1/haas/sessions/{session_id}/invocations/{invocation_id}/tools/{tool_call_id}/evidence"
    )
    async def get_execution_evidence(
        session_id: str,
        invocation_id: str,
        tool_call_id: str,
        evidence_ref: str,
        request: Request,
    ) -> JSONResponse:
        principal = await _authenticate(runtime.identity, request)
        invocation = _resolve_visible_invocation(runtime, principal, session_id, invocation_id)
        record = runtime.execution_evidence.get(evidence_ref)
        if (
            record is None
            or record.principalId != principal.principalId
            or record.appName != invocation.appName
            or record.userId != invocation.userId
            or record.sessionId != session_id
            or record.invocationId != invocation_id
            or record.toolCallId != tool_call_id
        ):
            raise HaasError(
                404, "invalid_request_error", "haas_execution_evidence_not_found"
            )
        if runtime.execution_evidence.expired(record):
            raise HaasError(
                410, "invalid_request_error", "haas_execution_evidence_expired"
            )
        return JSONResponse(
            {"data": record.public(), "traceId": _trace_id()},
            headers={
                "Cache-Control": "no-store",
                "Referrer-Policy": "no-referrer",
            },
        )

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
            {
                "adapterId": runtime.adapter.adapter_id,
                "base": runtime.adapter.base,
                "status": exec_status,
            }
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
            if not filename or filename in {".", ".."} or "/" in filename or "\\" in filename:
                raise ArtifactPathRejected("absolute_path_rejected")
            record = runtime.artifacts.register(
                session_id="",
                relative_path=f"output/{filename}",
                content=content,
                owner_principal_id=principal.principalId,
            )
        except ArtifactPathRejected as exc:
            if "too_large" in str(exc):
                raise HaasError(413, "invalid_request_error", "haas_file_too_large") from exc
            raise HaasError(400, "invalid_request_error", "invalid_input") from exc
        runtime.metrics.incr("haas_file_upload_total")
        record.mediaType = file.content_type or record.mediaType
        return {"data": record.to_dict(), "traceId": f"tr_{uuid.uuid4().hex[:16]}"}

    @app.get("/v1/haas/files/{file_id}/content")
    async def download_file(file_id: str, request: Request) -> Response:
        principal = await _authenticate(runtime.identity, request)
        try:
            record = runtime.artifacts.get(file_id, owner_principal_id=principal.principalId)
            content = runtime.artifacts.read_content(
                file_id, owner_principal_id=principal.principalId
            )
        except ArtifactNotFoundError as exc:
            raise HaasError(404, "invalid_request_error", "haas_file_not_found") from exc
        runtime.metrics.incr("haas_artifact_download_total")
        encoded_name = quote(record.filename, safe="")
        return Response(
            content=content,
            media_type=record.mediaType,
            headers={
                "X-Content-Type-Options": "nosniff",
                # Active content (HTML/JS/SVG) must never render inline
                # from this origin (specs/security-boundary §8.4).
                "Content-Disposition": f"attachment; filename*=UTF-8''{encoded_name}",
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
    async def list_session_artifacts(session_id: str, request: Request) -> dict[str, Any]:
        principal = await _authenticate(runtime.identity, request)
        session = _resolve_visible_session(runtime, principal, session_id)
        records = runtime.artifacts.list(
            session_id,
            app_name=session.appName,
            user_id=session.userId,
            owner_principal_id=principal.principalId,
        )
        return {
            "data": {"artifacts": [r.to_dict() for r in records]},
            "traceId": f"tr_{uuid.uuid4().hex[:16]}",
        }

    @app.get("/v1/haas/sessions/{session_id}/artifacts/archive")
    async def download_session_archive(session_id: str, request: Request) -> Response:
        principal = await _authenticate(runtime.identity, request)
        session = _resolve_visible_session(runtime, principal, session_id)
        records = runtime.artifacts.list(
            session_id,
            app_name=session.appName,
            user_id=session.userId,
            owner_principal_id=principal.principalId,
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
            key_hash = _hash(f"{principal.principalId}:{idempotency_key}")
            request_hash = _hash(
                f"{request.method}:{request.url.path}:"
                + json.dumps(body, sort_keys=True, default=str)
            )
            try:
                reservation = runtime.store.reserve(key_hash, request_hash)
            except IdempotencyConflictError as exc:
                raise HaasError(409, "invalid_request_error", "haas_idempotency_conflict") from exc
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
            raise HaasError(422, "invalid_request_error", "haas_unsupported_base") from exc
        except SkillBundleInvalidError as exc:
            if key_hash:
                runtime.store.release(key_hash)
            raise HaasError(422, "invalid_request_error", "haas_skill_source_invalid") from exc
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
            raise HaasError(404, "invalid_request_error", "haas_harness_not_found") from exc
        return _harness_envelope(record)

    @app.put("/v1/haas/harnesses/{harness_id}")
    async def update_harness(harness_id: str, request: Request) -> dict[str, Any]:
        principal = await _authenticate(runtime.identity, request)
        body = await _harness_body(request)
        try:
            record = runtime.registry.update(principal, harness_id, body)
        except HarnessNotFoundError as exc:
            raise HaasError(404, "invalid_request_error", "haas_harness_not_found") from exc
        except ImmutableFieldError as exc:
            # Immutable-field conflict is a body validation failure; no new
            # error code is introduced (specs/harness-registry §5.1.1).
            raise HaasError(400, "invalid_request_error", "invalid_input") from exc
        except UnsupportedBaseError as exc:
            raise HaasError(422, "invalid_request_error", "haas_unsupported_base") from exc
        except SkillBundleInvalidError as exc:
            raise HaasError(422, "invalid_request_error", "haas_skill_source_invalid") from exc
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
            raise HaasError(404, "invalid_request_error", "haas_harness_not_found") from exc
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

    # --- Harness Profile CRUD (specs/harness-profile §5.1) --------------

    def _profile_envelope(record: Any) -> dict[str, Any]:
        return {"data": profile_to_dict(record), "traceId": _trace_id()}

    @app.get("/v1/haas/profiles")
    async def list_profiles(
        request: Request,
        harnessId: str | None = None,
        status: str | None = None,
        limit: int = 20,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        principal = await _authenticate(runtime.identity, request)
        if limit < 1 or limit > 100 or cursor is not None:
            raise HaasError(400, "invalid_request_error", "invalid_input")
        try:
            records = runtime.profiles.list(principal, harness_id=harnessId, status=status)
        except ValueError as exc:
            raise HaasError(400, "invalid_request_error", "invalid_input") from exc
        return {
            "data": [profile_to_dict(record) for record in records[:limit]],
            "nextCursor": None,
            "traceId": _trace_id(),
        }

    @app.post("/v1/haas/profiles")
    async def create_profile(request: Request) -> dict[str, Any]:
        principal = await _authenticate(runtime.identity, request)
        body = await _json_object(request)
        idempotency_key = request.headers.get("Idempotency-Key")
        key_hash: str | None = None
        if idempotency_key:
            key_hash = _hash(f"{principal.principalId}:{idempotency_key}")
            request_hash = _hash(
                f"{request.method}:{request.url.path}:"
                + json.dumps(body, sort_keys=True, default=str)
            )
            try:
                reservation = runtime.store.reserve(key_hash, request_hash)
            except IdempotencyConflictError as exc:
                raise HaasError(409, "invalid_request_error", "haas_idempotency_conflict") from exc
            if reservation.replay:
                result = reservation.result
                if result is None:
                    result = await _wait_for_idempotency(runtime.store, key_hash)
                if result is not None:
                    return dict(result)
        try:
            envelope = _profile_envelope(runtime.profiles.create(principal, body))
        except ProfileNotFoundError as exc:
            if key_hash:
                runtime.store.release(key_hash)
            raise HaasError(404, "invalid_request_error", "haas_harness_not_found") from exc
        except (TypeError, ValueError) as exc:
            if key_hash:
                runtime.store.release(key_hash)
            raise HaasError(422, "invalid_request_error", "invalid_input") from exc
        if key_hash:
            runtime.store.complete(key_hash, envelope)
        return envelope

    @app.get("/v1/haas/profiles/{profile_id}")
    async def get_profile(profile_id: str, request: Request) -> dict[str, Any]:
        principal = await _authenticate(runtime.identity, request)
        try:
            return _profile_envelope(runtime.profiles.get(principal, profile_id))
        except ProfileNotFoundError as exc:
            raise HaasError(404, "invalid_request_error", "haas_profile_not_found") from exc

    @app.put("/v1/haas/profiles/{profile_id}")
    async def update_profile(profile_id: str, request: Request) -> dict[str, Any]:
        principal = await _authenticate(runtime.identity, request)
        body = await _json_object(request)
        try:
            return _profile_envelope(runtime.profiles.update(principal, profile_id, body))
        except ProfileNotFoundError as exc:
            raise HaasError(404, "invalid_request_error", "haas_profile_not_found") from exc
        except ProfileConflictError as exc:
            raise HaasError(409, "invalid_request_error", "haas_profile_conflict") from exc
        except (TypeError, ValueError) as exc:
            raise HaasError(422, "invalid_request_error", "invalid_input") from exc

    @app.post("/v1/haas/profiles/{profile_id}/validate")
    async def validate_profile(profile_id: str, request: Request) -> dict[str, Any]:
        principal = await _authenticate(runtime.identity, request)
        try:
            validation = runtime.profiles.validate(principal, profile_id)
        except ProfileNotFoundError as exc:
            raise HaasError(404, "invalid_request_error", "haas_profile_not_found") from exc
        return {"data": validation, "traceId": _trace_id()}

    @app.post("/v1/haas/profiles/{profile_id}/activate")
    async def activate_profile(profile_id: str, request: Request) -> dict[str, Any]:
        principal = await _authenticate(runtime.identity, request)
        try:
            return _profile_envelope(runtime.profiles.activate(principal, profile_id))
        except ProfileNotFoundError as exc:
            raise HaasError(404, "invalid_request_error", "haas_profile_not_found") from exc
        except ProfileConflictError as exc:
            raise HaasError(409, "invalid_request_error", "haas_profile_conflict") from exc

    @app.post("/v1/haas/delegated-sessions")
    async def create_delegated_session(request: Request) -> dict[str, Any]:
        principal = await _authenticate(runtime.identity, request)
        body = await _json_object(request)
        idempotency_key = request.headers.get("Idempotency-Key")
        key_hash: str | None = None
        if idempotency_key:
            key_hash = _hash(f"{principal.principalId}:{idempotency_key}")
            request_hash = _hash(
                f"{request.method}:{request.url.path}:"
                + json.dumps(body, sort_keys=True, default=str)
            )
            try:
                reservation = runtime.store.reserve(key_hash, request_hash)
            except IdempotencyConflictError as exc:
                raise HaasError(409, "invalid_request_error", "haas_idempotency_conflict") from exc
            if reservation.replay:
                result = reservation.result
                if result is None:
                    result = await _wait_for_idempotency(runtime.store, key_hash)
                if result is not None:
                    return dict(result)
                raise HaasError(409, "invalid_request_error", "session_busy", retryable=True)

        try:
            record = _delegated_session_from_body(body, config)
            _ensure_owns(runtime.identity, principal, record.haasUserId)
            try:
                harness = runtime.registry.get_scoped(principal, record.harnessId)
            except HarnessNotFoundError as exc:
                raise HaasError(404, "invalid_request_error", "haas_harness_not_found") from exc
            if harness.base != record.harnessBase:
                raise HaasError(400, "invalid_request_error", "invalid_input")

            existing = runtime.store.get_delegated_session_by_manager(record.managerSessionId)
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
    async def get_delegated_session(delegated_session_id: str, request: Request) -> dict[str, Any]:
        principal = await _authenticate(runtime.identity, request)
        record = runtime.store.get_delegated_session(delegated_session_id)
        if record is None:
            raise HaasError(404, "invalid_request_error", "haas_delegated_session_not_found")
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
            raise HaasError(404, "invalid_request_error", "haas_delegated_session_not_found")
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
            ttl_ms=record.delegationPolicySnapshot["maxContainerLifetimeSeconds"] * 1000,
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
            runtime.store.release_workspace_lock(primary_workspace["hostPathCanonical"], record.id)
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
            unsupported = str(exc) == "haas_policy_unsupported"
            raise HaasError(
                422 if unsupported else 503,
                "invalid_request_error",
                "haas_policy_unsupported"
                if unsupported
                else "haas_delegation_backend_unavailable",
                safe_reason=(
                    "network_policy_unsupported"
                    if unsupported
                    else "delegation_backend_unavailable"
                ),
                retryable=not unsupported,
            ) from exc
        restored = runtime.store.update_delegated_runtime(delegated_session_id, restored_runtime)
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
    ) -> Response:
        principal = await _authenticate(runtime.identity, request)
        body = await _json_object(request)
        idempotency_key = request.headers.get("Idempotency-Key")
        key_hash: str | None = None
        if idempotency_key:
            key_hash = _hash(f"{principal.principalId}:{idempotency_key}")
            request_hash = _hash(
                f"{request.method}:{request.url.path}:"
                + json.dumps(body, sort_keys=True, default=str)
            )
            try:
                reservation = runtime.store.reserve(key_hash, request_hash)
            except IdempotencyConflictError as exc:
                raise HaasError(409, "invalid_request_error", "haas_idempotency_conflict") from exc
            if reservation.replay:
                result = reservation.result
                if result is None:
                    result = await _wait_for_idempotency(runtime.store, key_hash)
                if result is not None:
                    data = cast(dict[str, Any], result).get("data", {})
                    status = (
                        200 if data.get("desiredRevision") == data.get("appliedRevision") else 202
                    )
                    return JSONResponse(status_code=status, content=dict(result))
                raise HaasError(409, "invalid_request_error", "session_busy", retryable=True)
        record = runtime.store.get_delegated_session(delegated_session_id)
        if record is None:
            if key_hash:
                runtime.store.release(key_hash)
            raise HaasError(404, "invalid_request_error", "haas_delegated_session_not_found")
        try:
            _ensure_delegated_access(runtime, principal, record)
        except HaasError:
            if key_hash:
                runtime.store.release(key_hash)
            raise
        expected_revision = body.get("expectedRevision")
        if expected_revision is not None and (
            not isinstance(expected_revision, int)
            or isinstance(expected_revision, bool)
            or expected_revision != record.desiredRevision
        ):
            if key_hash:
                runtime.store.release(key_hash)
            raise HaasError(
                409, "invalid_request_error", "haas_policy_revision_conflict"
            )
        domains = {
            "profileRef",
            "delegationPolicySnapshot",
            "mountManifest",
            "image",
        }
        supplied_domains = domains.intersection(body)
        if not supplied_domains:
            if key_hash:
                runtime.store.release(key_hash)
            raise HaasError(400, "invalid_request_error", "invalid_input")

        profile_ref = body.get("profileRef", record.profileRef)
        policy = body.get("delegationPolicySnapshot", record.delegationPolicySnapshot)
        mount_manifest = body.get("mountManifest", record.mountManifest)
        image = body.get("image", record.image)
        try:
            if "profileRef" in supplied_domains:
                _validate_profile_ref(profile_ref)
            if not isinstance(policy, dict) or not isinstance(mount_manifest, dict):
                raise ValueError("invalid policy domain")
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
            _validate_delegation_policy(policy)
            _validate_mount_manifest(mount_manifest)
        except MountManifestInvalid as exc:
            if key_hash:
                runtime.store.release(key_hash)
            raise HaasError(
                403,
                "invalid_request_error",
                "haas_delegation_mount_invalid",
                safe_reason="delegation_mount_invalid",
            ) from exc
        except ValueError as exc:
            if key_hash:
                runtime.store.release(key_hash)
            raise HaasError(400, "invalid_request_error", "invalid_input") from exc

        revision = record.desiredRevision + 1
        update_id = f"dgpupd_{uuid.uuid4().hex[:16]}"
        pending = {
            "updateId": update_id,
            "revision": revision,
            "requestedAtMs": int(time.time() * 1000),
            "fields": sorted(supplied_domains),
        }
        updated = runtime.store.put_delegated_session(
            replace(
                record,
                desiredRevision=revision,
                pendingPolicyUpdate=pending,
                pendingPolicyTarget={
                    "image": image,
                    "profileRef": profile_ref,
                    "mountManifest": mount_manifest,
                    "delegationPolicySnapshot": policy,
                },
            )
        )
        # An idle or absent runtime can apply synchronously. Busy runtimes keep
        # executing their current applied snapshot and are reconciled later.
        updated = reconcile_delegated_policy(runtime.store, updated.id) or updated
        event = (
            "haas.delegation.policy_update_applied"
            if updated.appliedRevision == revision
            else "haas.delegation.policy_update_pending"
        )
        runtime.logger.event(event, {"delegatedSessionId": updated.id})
        runtime.event_log.append_typed(
            type_=event,
            app_name=updated.harnessId,
            user_id=updated.haasUserId,
            invocation_id=None,
            session_id=updated.haasSessionId,
            turn_id=None,
            harness_id=updated.harnessId,
            adapter_id="delegated-policy-reconciler",
            author="haas",
            content={"role": "model", "parts": []},
            actions={},
            haas={
                "delegatedSessionId": updated.id,
                "updateId": update_id,
                "revision": revision,
                "fields": sorted(supplied_domains),
            },
        )
        session = runtime.store.get_session(
            _session_key(updated.harnessId, updated.haasUserId, updated.haasSessionId)
        )
        if session is not None:
            runtime.store.put_session(
                replace(session, delegatedSessionRef=_delegated_session_ref(updated))
            )
        envelope = _delegated_session_envelope(updated)
        if key_hash:
            runtime.store.complete(key_hash, envelope)
        status = 200 if updated.appliedRevision == revision else 202
        return JSONResponse(status_code=status, content=envelope)

    @app.get("/v1/haas/sessions/{session_id}/profile")
    async def get_session_profile(session_id: str, request: Request) -> dict[str, Any]:
        principal = await _authenticate(runtime.identity, request)
        delegated = runtime.store.get_delegated_session_by_haas_session(session_id)
        if delegated is not None:
            try:
                _ensure_delegated_access(runtime, principal, delegated)
            except HaasError as exc:
                if exc.status_code == 404:
                    raise HaasError(404, "invalid_request_error", "session_not_found") from exc
                raise
            raise HaasError(409, "invalid_request_error", "haas_profile_rebind_unsupported")
        session = _resolve_visible_session(runtime, principal, session_id)
        snapshot = session.effectiveProfile or {}
        required = (
            "profileId",
            "profileVersion",
            "profileFingerprint",
            "harnessId",
            "base",
        )
        if not all(snapshot.get(field) is not None for field in required):
            raise HaasError(404, "invalid_request_error", "session_not_found")
        intent = {
            key: value
            for key, value in snapshot.items()
            if key not in {"profileId", "profileVersion", "profileFingerprint", "resolvedAtMs"}
        }
        return {
            "data": {
                **{field: snapshot[field] for field in required},
                "executionIntentFingerprint": execution_intent_fingerprint(intent),
            },
            "traceId": _trace_id(),
        }

    @app.post("/v1/haas/sessions/{session_id}/profile-rebind")
    async def rebind_session_profile(session_id: str, request: Request) -> Response:
        principal = await _authenticate(runtime.identity, request)
        delegated = runtime.store.get_delegated_session_by_haas_session(session_id)
        if delegated is not None:
            _ensure_delegated_access(runtime, principal, delegated)
            raise HaasError(409, "invalid_request_error", "haas_profile_rebind_unsupported")
        session = _resolve_visible_session(runtime, principal, session_id)
        body = await _json_object(request)
        expected = body.get("expectedProfileVersion")
        if (
            set(body) - {"profileId", "expectedProfileVersion", "reason"}
            or not isinstance(body.get("profileId"), str)
            or not body["profileId"]
            or ("expectedProfileVersion" in body and (type(expected) is not int or expected < 1))
            or ("reason" in body and not isinstance(body["reason"], str))
        ):
            raise HaasError(400, "invalid_request_error", "invalid_input")
        key = _session_key(session.appName, session.userId, session_id)
        idempotency_key = request.headers.get("Idempotency-Key")
        key_hash = (
            _hash(json.dumps([principal.principalId, key, "profile-rebind", idempotency_key]))
            if idempotency_key
            else None
        )
        reserved = False
        holder = f"rebind_{uuid.uuid4().hex}"
        while True:
            try:
                lease = runtime.store.acquire_lease(key, holder, ttl_ms=30_000)
                break
            except LeaseConflictError:
                await asyncio.sleep(0.05)
        try:
            if key_hash:
                try:
                    reservation = runtime.store.reserve(
                        key_hash, _hash(json.dumps(body, sort_keys=True))
                    )
                except IdempotencyConflictError as exc:
                    raise HaasError(
                        409, "invalid_request_error", "haas_idempotency_conflict"
                    ) from exc
                if reservation.replay:
                    if reservation.result is None:
                        raise HaasError(
                            409, "invalid_request_error", "session_busy", retryable=True
                        )
                    return JSONResponse(content=reservation.result)
                reserved = True
            session = runtime.sessions.get_session(*key)
            try:
                profile = runtime.profiles.get(principal, body["profileId"])
                if profile.harnessId != session.appName:
                    raise ProfileNotFoundError(profile.id)
                snapshot = runtime.profiles.execution_snapshot(profile)
            except ProfileNotFoundError as exc:
                raise HaasError(404, "invalid_request_error", "haas_profile_not_found") from exc
            except ProfileConflictError as exc:
                raise HaasError(409, "invalid_request_error", "haas_profile_conflict") from exc
            _validate_local_materialization(runtime, snapshot)
            previous = session.effectiveProfile or {}
            if previous.get("profileId") != profile.id:
                if (
                    expected is not None and expected != previous.get("profileVersion")
                ) or previous.get("profileVersion", 0) >= profile.version:
                    raise HaasError(409, "invalid_request_error", "haas_profile_conflict")
                runtime.store.put_session(replace(session, effectiveProfile=snapshot))
            else:
                snapshot = previous
            envelope = {
                "data": {
                    field: snapshot[field]
                    for field in (
                        "profileId",
                        "profileVersion",
                        "profileFingerprint",
                        "harnessId",
                        "base",
                        "resolvedAtMs",
                    )
                },
                "traceId": _trace_id(),
            }
            if key_hash:
                runtime.store.complete(key_hash, envelope)
                reserved = False
            return JSONResponse(content=envelope)
        finally:
            if key_hash and reserved:
                runtime.store.release(key_hash)
            runtime.store.release_lease(key, holder, lease.token)

    @app.get("/v1/haas/harnesses/{harness_id}/skills/{skill_id}/files")
    async def list_skill_files(harness_id: str, skill_id: str, request: Request) -> dict[str, Any]:
        principal = await _authenticate(runtime.identity, request)
        try:
            record = runtime.registry.get_scoped(principal, harness_id)
        except HarnessNotFoundError as exc:
            raise HaasError(404, "invalid_request_error", "haas_harness_not_found") from exc
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
            for r in runtime.store.list_sessions(app_name=app, user_ids=principal.userIds)
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
        h.id for h in runtime.store.list_harnesses((principal.tenantId, principal.workspaceId))
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
        h.id for h in runtime.store.list_harnesses((principal.tenantId, principal.workspaceId))
    }
    if invocation.appName not in visible_apps:
        raise HaasError(404, "invalid_request_error", "haas_invocation_not_found")
    if runtime.store.get_session((invocation.appName, invocation.userId, session_id)) is None:
        raise HaasError(404, "invalid_request_error", "haas_invocation_not_found")
    return invocation


def _local_effective_profile(
    runtime: _Runtime,
    principal: Principal,
    app: HarnessRecord,
    user_id: str,
    session_id: str | None,
    body: dict[str, Any],
) -> dict[str, Any] | None:
    selection = body.get("haas") or {}
    if not isinstance(selection, dict):
        raise HaasError(400, "invalid_request_error", "invalid_input")
    session = runtime.store.get_session((app.id, user_id, session_id)) if session_id else None
    effective = session.effectiveProfile if session else None
    if (
        effective
        and selection.get("profileId")
        and (
            selection["profileId"] != effective["profileId"]
            or selection.get("profileVersion") != effective["profileVersion"]
        )
    ):
        raise HaasError(409, "invalid_request_error", "haas_profile_rebind_required")
    if effective is None:
        try:
            if selection.get("profileId"):
                profile = runtime.profiles.get(principal, str(selection["profileId"]))
                if profile.harnessId != app.id or profile.version != selection.get(
                    "profileVersion"
                ):
                    raise ProfileNotFoundError(profile.id)
                effective = runtime.profiles.execution_snapshot(profile)
            else:
                active = runtime.profiles.list(principal, harness_id=app.id, status="active")
                if active:
                    effective = runtime.profiles.execution_snapshot(active[0])
        except ProfileNotFoundError as exc:
            raise HaasError(404, "invalid_request_error", "haas_profile_not_found") from exc
        except ProfileConflictError as exc:
            raise HaasError(409, "invalid_request_error", "haas_profile_conflict") from exc
    if runtime.adapter.base == "codex":
        proxy = runtime.sessions.model_proxy
        if not effective or proxy is None or not proxy.base_url or not proxy.resolver.available:
            raise HaasError(
                503, "service_unavailable", "haas_provider_error", "model_proxy_unavailable"
            )
        _validate_local_materialization(runtime, effective)
    return effective


def _codex_builtin_mcp_only(servers: Any) -> bool:
    if not isinstance(servers, list):
        return False
    for server in servers:
        if not isinstance(server, dict):
            return False
        if (
            server.get("name") != "manager-cowork-recall"
            or server.get("haas_builtin") is not True
            or server.get("transport") != "http"
        ):
            return False
        url = server.get("url")
        if not isinstance(url, str) or not url.startswith("http://127.0.0.1:"):
            return False
        headers = server.get("headers")
        if not isinstance(headers, dict):
            return False
        if not (
            isinstance(headers.get("X-HaaS-Session-ID"), str)
            and headers["X-HaaS-Session-ID"]
            and isinstance(headers.get("X-HaaS-Recall-Token"), str)
            and headers["X-HaaS-Recall-Token"]
        ):
            return False
    return True


def _validate_local_materialization(runtime: _Runtime, effective: dict[str, Any]) -> None:
    unsupported = any(
        effective.get(field) for field in ("skills", "agentsMd", "policy", "workspace", "budget")
    )
    mcp_servers = effective.get("mcpServers")
    if mcp_servers and not _codex_builtin_mcp_only(mcp_servers):
        unsupported = True
    if runtime.adapter.base == "codex" and unsupported:
        raise HaasError(
            409,
            "invalid_request_error",
            "haas_profile_conflict",
            "profile_materialization_unsupported",
        )


async def _run(runtime: _Runtime, request: Request, *, streaming: bool) -> Any:
    principal = await _authenticate(runtime.identity, request)
    body = await request.json()
    if not isinstance(body, dict):
        raise HaasError(400, "invalid_request_error", "invalid_input")
    app_name = body.get("appName")
    user_id = body.get("userId")
    session_id = body.get("sessionId")
    new_message = body.get("newMessage", {})
    sandbox = body.get("sandbox", {})
    policy = body.get("policy", {})
    haas_options = body.get("haas", {})
    if (
        not isinstance(app_name, str)
        or not isinstance(user_id, str)
        or not app_name
        or not user_id
        or (session_id is not None and not isinstance(session_id, str))
        or not isinstance(new_message, dict)
        or not isinstance(sandbox, dict)
        or not isinstance(policy, dict)
        or not isinstance(haas_options, dict)
    ):
        raise HaasError(400, "invalid_request_error", "invalid_input")
    try:
        timeout_seconds = _timeout_seconds_from_haas(haas_options)
    except (TypeError, ValueError) as exc:
        raise HaasError(
            400,
            "invalid_request_error",
            "invalid_input",
            safe_reason="invalid_timeout_seconds",
        ) from exc

    _ensure_owns(runtime.identity, principal, user_id)

    try:
        app = runtime.registry.resolve_app(principal, app_name)
    except AppNotFoundError as exc:
        raise HaasError(404, "invalid_request_error", "app_not_found") from exc

    last_event_id = request.headers.get("Last-Event-ID")
    if last_event_id is not None:
        if not streaming or not isinstance(session_id, str):
            raise HaasError(400, "invalid_request_error", "invalid_input")
        try:
            remaining = runtime.event_log.read_session(app.id, user_id, session_id, last_event_id)
        except CursorNotFoundError as exc:
            raise HaasError(410, "invalid_request_error", "haas_offset_expired") from exc
        all_events = runtime.event_log.read_session(app.id, user_id, session_id)
        cursor = next(event for event in all_events if event.eventId == last_event_id)
        invocation_id = cursor.invocationId
        replay = [event for event in remaining if event.invocationId == invocation_id]

        async def replay_frames() -> Any:
            for event in replay:
                yield runtime.event_log.sse_frame(event)
            yield HEARTBEAT_FRAME

        return StreamingResponse(replay_frames(), media_type="text/event-stream")

    # Idempotency-Key reservation happens before admission (spec: haas-protocol §7).
    idempotency_key = request.headers.get("Idempotency-Key")
    key_hash: str | None = None
    if idempotency_key:
        key_hash = _hash(f"{principal.principalId}:{idempotency_key}")
        request_hash = _hash(
            f"{request.method}:{request.url.path}:"
            + json.dumps(body, sort_keys=True, default=str)
        )
        try:
            reservation = runtime.store.reserve(key_hash, request_hash)
        except IdempotencyExpiredError as exc:
            raise HaasError(410, "invalid_request_error", "haas_idempotency_expired") from exc
        except IdempotencyConflictError as exc:
            raise HaasError(409, "invalid_request_error", "haas_idempotency_conflict") from exc
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
            except IdempotencyExpiredError as exc:
                raise HaasError(410, "invalid_request_error", "haas_idempotency_expired") from exc
            except IdempotencyConflictError as exc:
                raise HaasError(409, "invalid_request_error", "haas_idempotency_conflict") from exc

    admission = runtime.admission.admit_run(
        AdmissionInput(
            principalHash=principal.principalId,
            appName=app.id,
            tenantId=principal.tenantId,
            workspaceId=principal.workspaceId,
        )
    )
    if not admission.allowed:
        if key_hash:
            runtime.store.release(key_hash)
        code = admission.code or "haas_rate_limited"
        retryable = code in {"haas_rate_limited", "haas_quota_exceeded", "haas_queue_full"}
        raise HaasError(
            429 if code != "haas_queue_full" else 503,
            "invalid_request_error",
            code,
            admission.safeReason,
            retryable,
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
            if delegated_record.appliedRevision < delegated_record.desiredRevision:
                raise HaasError(
                    409,
                    "invalid_request_error",
                    "session_busy",
                    safe_reason="configuration_update_pending",
                    retryable=True,
                )
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
            policy={
                **policy,
                "network": dict(
                    delegated_record.delegationPolicySnapshot.get("network")
                    or {"defaultAction": "deny", "allow": []}
                ),
            },
            delegated=delegated_record,
            streaming=streaming,
            key_hash=key_hash,
            lease_id=lease_id,
            timeout_seconds=timeout_seconds,
        )
    try:
        effective_profile = _local_effective_profile(
            runtime, principal, app, user_id, session_id, body
        )
    except HaasError:
        if key_hash:
            runtime.store.release(key_hash)
        if lease_id:
            runtime.admission.release_run(lease_id)
        raise
    req = RunRequest(
        app=app,
        user_id=user_id,
        session_id=session_id,
        message=new_message,
        sandbox=sandbox,
        policy=policy,
        effective_profile=effective_profile,
        principal_id=principal.principalId,
        timeout_seconds=timeout_seconds,
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
            safe_reason = (
                "configuration_update_pending"
                if str(exc) == "configuration_update_pending"
                else "session_busy"
            )
            raise HaasError(
                409,
                "invalid_request_error",
                "session_busy",
                safe_reason=safe_reason,
                retryable=True,
            ) from exc
        except ResumeRequiredError as exc:
            if key_hash:
                runtime.store.release(key_hash)
            if lease_id:
                runtime.admission.release_run(lease_id)
            raise HaasError(
                409, "invalid_request_error", "haas_resume_required"
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
        if first.invocationId is None:
            raise HaasError(503, "service_unavailable", "haas_store_unavailable", retryable=True)
        expires_at_ms = _accept_idempotency(runtime, key_hash, first.invocationId)
        accepted_headers = _accepted_headers(
            invocation_id=first.invocationId,
            session_id=first.sessionId,
            expires_at_ms=expires_at_ms,
        )
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
                    runtime.store.complete(
                        key_hash,
                        {
                            "events": adk_events,
                            "idempotency_expires_at_ms": expires_at_ms,
                            "invocation_id": first.invocationId,
                            "session_id": first.sessionId,
                        },
                    )
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
                    try:
                        async with asyncio.timeout(SSE_HEARTBEAT_SECONDS):
                            frame = await queue.get()
                    except TimeoutError:
                        yield HEARTBEAT_FRAME
                        continue
                    if frame is None:
                        break
                    yield frame
            finally:
                disconnected = True
                if producer.done():
                    producer.result()

        return StreamingResponse(
            frames(),
            media_type="text/event-stream",
            headers=accepted_headers,
        )

    try:
        try:
            result = await runtime.sessions.run(req)
        except SessionBusyError as exc:
            if key_hash:
                runtime.store.release(key_hash)
            safe_reason = (
                "configuration_update_pending"
                if str(exc) == "configuration_update_pending"
                else "session_busy"
            )
            raise HaasError(
                409,
                "invalid_request_error",
                "session_busy",
                safe_reason=safe_reason,
                retryable=True,
            ) from exc
        except ResumeRequiredError as exc:
            if key_hash:
                runtime.store.release(key_hash)
            raise HaasError(
                409, "invalid_request_error", "haas_resume_required"
            ) from exc
        except AdapterTurnTimeoutError as exc:
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
            expires_at_ms = (
                _accept_idempotency(runtime, key_hash, exc.invocation_id)
                if key_hash
                else None
            )
            headers = _accepted_headers(
                invocation_id=exc.invocation_id,
                session_id=invocation.sessionId if invocation is not None else session_id,
                expires_at_ms=expires_at_ms,
            )
            if key_hash:
                runtime.store.complete(
                    key_hash,
                    {
                        "events": events,
                        "idempotency_expires_at_ms": expires_at_ms,
                        "invocation_id": exc.invocation_id,
                        "session_id": (
                            invocation.sessionId if invocation is not None else session_id
                        ),
                    },
                )
            return JSONResponse(status_code=200, content=events, headers=headers)
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
                    {
                        "status_code": error.status_code,
                        "body": body,
                        "events": events,
                        "invocation_id": exc.invocation_id,
                        "session_id": (
                            invocation.sessionId if invocation is not None else session_id
                        ),
                    },
                )
                return JSONResponse(status_code=error.status_code, content=body)
            raise HaasError(
                502, "invalid_request_error", "haas_adapter_error", retryable=True
            ) from exc

        adk_events = [runtime.event_log.project_adk(e) for e in result.events]
        expires_at_ms = _accept_idempotency(runtime, key_hash, result.invocation.id)
        accepted_headers = _accepted_headers(
            invocation_id=result.invocation.id,
            session_id=result.invocation.sessionId,
            expires_at_ms=expires_at_ms,
        )
        if key_hash:
            runtime.store.complete(
                key_hash,
                {
                    "status_code": 200,
                    "events": adk_events,
                    "idempotency_expires_at_ms": expires_at_ms,
                    "invocation_id": result.invocation.id,
                    "session_id": result.invocation.sessionId,
                },
            )

        return JSONResponse(content=adk_events, headers=accepted_headers)
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
    policy: dict[str, Any],
    delegated: DelegatedSessionRecord,
    streaming: bool,
    key_hash: str | None,
    lease_id: str | None,
    timeout_seconds: float | None,
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
    expires_at_ms = _accept_idempotency(runtime, key_hash, invocation.id)
    accepted_headers = _accepted_headers(
        invocation_id=invocation.id,
        session_id=session_id,
        expires_at_ms=expires_at_ms,
    )

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
                "executionId": invocation.id,
                "containerGeneration": delegated.runtime.containerGeneration,
                "appName": app.id,
                "userId": user_id,
                "sessionId": session_id,
                "newMessage": message,
                "streaming": True,
                "timeoutSeconds": timeout_seconds or 86_400,
                "policy": dict(policy),
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
                unsupported = str(exc) == "haas_policy_unsupported"
                code = (
                    "haas_policy_unsupported"
                    if unsupported
                    else "haas_delegation_backend_unavailable"
                )
                safe_reason = (
                    "network_policy_unsupported"
                    if unsupported
                    else "delegation_backend_unavailable"
                )
                status_code = 422 if unsupported else 503
                retryable = not unsupported
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
            current_delegated = runtime.store.get_delegated_session(delegated.id)
            if current_delegated is not None and current_delegated.runtime.status == "running":
                current_delegated = runtime.store.update_delegated_runtime(
                    current_delegated.id,
                    replace(
                        current_delegated.runtime,
                        status="idle",
                        lastActiveAtMs=int(time.time() * 1000),
                    ),
                )
                pending = current_delegated.pendingPolicyUpdate
                reconciled = reconcile_delegated_policy(runtime.store, current_delegated.id)
                if pending is not None and reconciled is not None:
                    result = reconciled.lastPolicyUpdateResult or {}
                    event_type = (
                        "haas.delegation.policy_update_applied"
                        if result.get("status") == "applied"
                        else "haas.delegation.policy_update_failed"
                    )
                    metadata = {
                        "delegatedSessionId": reconciled.id,
                        "updateId": pending["updateId"],
                        "revision": pending["revision"],
                        "fields": pending["fields"],
                    }
                    if event_type.endswith("failed"):
                        metadata.update(
                            {
                                "code": result.get("code", "haas_internal_error"),
                                "safeReason": result.get("safeReason", "policy_apply_failed"),
                            }
                        )
                    runtime.event_log.append_typed(
                        type_=event_type,
                        app_name=app.id,
                        user_id=user_id,
                        invocation_id=None,
                        session_id=session_id,
                        turn_id=None,
                        harness_id=app.id,
                        adapter_id="delegated-policy-reconciler",
                        author="haas",
                        content={"role": "model", "parts": []},
                        actions={},
                        haas=metadata,
                    )
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
                        runtime.store.complete(
                            key_hash,
                            {
                                "events": adk_events,
                                "idempotency_expires_at_ms": expires_at_ms,
                                "invocation_id": invocation.id,
                                "session_id": session_id,
                            },
                        )
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
                    try:
                        async with asyncio.timeout(SSE_HEARTBEAT_SECONDS):
                            frame = await queue.get()
                    except TimeoutError:
                        yield HEARTBEAT_FRAME
                        continue
                    if frame is None:
                        break
                    yield frame
            finally:
                disconnected = True
                if producer.done():
                    producer.result()

        return StreamingResponse(
            frames(), media_type="text/event-stream", headers=accepted_headers
        )

    delegated_adk_events: list[dict[str, Any]] = []
    try:
        async for event in events():
            delegated_adk_events.append(runtime.event_log.project_adk(event))
    finally:
        if key_hash:
            runtime.store.complete(
                key_hash,
                {
                    "events": delegated_adk_events,
                    "idempotency_expires_at_ms": expires_at_ms,
                    "invocation_id": invocation.id,
                    "session_id": session_id,
                },
            )
        if lease_id:
            runtime.admission.release_run(lease_id)
    return JSONResponse(content=delegated_adk_events, headers=accepted_headers)


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


async def _reserve_mutation(
    store: MemoryStore,
    request: Request,
    body: dict[str, Any],
    principal: Principal,
) -> tuple[str | None, dict[str, Any] | None]:
    idempotency_key = request.headers.get("Idempotency-Key")
    if not idempotency_key:
        return None, None
    # Idempotency keys are caller-controlled and commonly reused by different
    # tenants. Namespace reservations by authenticated principal so a guessed key
    # can never replay another caller's mutation response before resource checks.
    key_hash = _hash(f"{principal.principalId}:{idempotency_key}")
    request_hash = _hash(
        f"{request.method}:{request.url.path}:" + json.dumps(body, sort_keys=True, default=str)
    )
    try:
        reservation = store.reserve(key_hash, request_hash)
    except IdempotencyExpiredError as exc:
        raise HaasError(410, "invalid_request_error", "haas_idempotency_expired") from exc
    except IdempotencyConflictError as exc:
        raise HaasError(409, "invalid_request_error", "haas_idempotency_conflict") from exc
    if not reservation.replay:
        return key_hash, None
    result = reservation.result
    if result is None:
        result = await _wait_for_idempotency(store, key_hash)
    return key_hash, dict(result) if isinstance(result, dict) else None


def _accept_idempotency(runtime: _Runtime, key_hash: str | None, invocation_id: str) -> int | None:
    if key_hash is None:
        return None
    record = runtime.store.accept(key_hash, invocation_id)
    invocation = runtime.store.get_invocation(invocation_id)
    if invocation is not None:
        invocation.acceptedAtMs = record.acceptedAtMs
        invocation.idempotencyKeyHash = key_hash
        invocation.idempotencyExpiresAtMs = record.expiresAtMs
        runtime.store.put_invocation(invocation)
    return record.expiresAtMs


def _idempotency_headers(expires_at_ms: int | None) -> dict[str, str] | None:
    if expires_at_ms is None:
        return None
    return {"Idempotency-Expires-At": str(expires_at_ms)}


def _accepted_headers(
    *,
    invocation_id: str | None,
    session_id: str | None,
    expires_at_ms: int | None,
) -> dict[str, str]:
    headers = _idempotency_headers(expires_at_ms) or {}
    if invocation_id:
        headers["X-HaaS-Invocation-ID"] = invocation_id
    if session_id:
        headers["X-HaaS-Session-ID"] = session_id
    return headers


def _render_cached(cached: Any, *, streaming: bool) -> Any:
    expires_at_ms: int | None = None
    if not isinstance(cached, dict):
        events: list[Any] = []
        status_code = 200
        body: Any = None
        invocation_id: Any = None
        session_id: Any = None
    else:
        events = cached.get("events", [])
        status_code = int(cached.get("status_code", 200))
        body = cached.get("body")
        expires_at_ms = cached.get("idempotency_expires_at_ms")
        invocation_id = cached.get("invocation_id")
        session_id = cached.get("session_id")
    headers = _accepted_headers(
        invocation_id=invocation_id if isinstance(invocation_id, str) else None,
        session_id=session_id if isinstance(session_id, str) else None,
        expires_at_ms=expires_at_ms,
    )
    if status_code >= 400:
        return JSONResponse(
            status_code=status_code,
            content=body if isinstance(body, dict) else {"detail": "request_failed"},
            headers=headers,
        )
    if not streaming:
        return JSONResponse(content=events, headers=headers)

    async def frames() -> Any:
        for event in events:
            yield f"data: {json.dumps(event, separators=(',', ':'))}\n\n"
        yield ": keep-alive\n\n"

    return StreamingResponse(
        frames(),
        media_type="text/event-stream",
        headers=headers,
    )
