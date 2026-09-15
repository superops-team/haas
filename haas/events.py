"""Event Log & SSE: canonical event persistence and ADK projection."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any, cast

from haas.harnesses.base import HarnessEvent
from haas.security import redact
from haas.security.redact import bounded_redacted_preview
from haas.stores import CanonicalEventRecord, MemoryStore

HEARTBEAT_FRAME = ": keep-alive\n\n"

_POLICY_UPDATE_TYPES = {
    "haas.delegation.policy_update_pending",
    "haas.delegation.policy_update_applied",
    "haas.delegation.policy_update_failed",
}

_INTERACTION_EVENT_TYPES = {
    "haas.approval.required",
    "haas.approval.resolved",
    "haas.input.required",
    "haas.input.resolved",
}

_HARNESS_EVENT_TYPES = {
    "harness.text.delta": "haas.output.text.delta",
    "harness.reasoning.delta": "haas.output.reasoning.delta",
    "harness.output.item.completed": "haas.output.item.completed",
    "harness.tool.started": "haas.tool.started",
    "harness.tool.output": "haas.tool.output",
    "harness.tool.completed": "haas.tool.completed",
    "harness.tool.failed": "haas.tool.failed",
    "harness.plan.updated": "haas.plan.updated",
    **{type_: type_ for type_ in _INTERACTION_EVENT_TYPES},
    "harness.usage": "haas.usage.updated",
    "harness.turn.started": "haas.turn.started",
    "harness.turn.completed": "haas.turn.completed",
    "harness.turn.failed": "haas.turn.failed",
    "harness.turn.incomplete": "haas.turn.incomplete",
    "harness.turn.interrupted": "haas.turn.interrupted",
    "harness.turn.cancelled": "haas.turn.cancelled",
}


@dataclass
class EventLog:
    store: MemoryStore

    def append(
        self,
        *,
        app_name: str,
        user_id: str,
        invocation_id: str,
        session_id: str,
        turn_id: str,
        harness_id: str,
        adapter_id: str,
        author: str,
        content: dict[str, Any],
        actions: dict[str, Any],
    ) -> CanonicalEventRecord:
        event_type = _infer_event_type(content, actions)
        return self.append_typed(
            type_=event_type,
            app_name=app_name,
            user_id=user_id,
            invocation_id=invocation_id,
            session_id=session_id,
            turn_id=turn_id,
            harness_id=harness_id,
            adapter_id=adapter_id,
            author=author,
            content=content,
            actions=actions,
            haas=_infer_haas_metadata(event_type, actions),
        )

    def append_harness_event(
        self,
        event: HarnessEvent,
        *,
        app_name: str,
        user_id: str,
        harness_id: str,
        adapter_id: str,
    ) -> CanonicalEventRecord:
        type_ = _HARNESS_EVENT_TYPES.get(event.type, "haas.adapter.event_unparsed")
        return self.append_typed(
            type_=type_,
            app_name=app_name,
            user_id=user_id,
            invocation_id=event.invocationId,
            session_id=event.sessionId,
            turn_id=event.turnId,
            harness_id=harness_id,
            adapter_id=adapter_id,
            author=event.author,
            content=event.content,
            actions=event.actions,
            haas=_harness_event_metadata(type_, event),
        )

    def append_typed(
        self,
        *,
        type_: str,
        app_name: str,
        user_id: str,
        invocation_id: str | None,
        session_id: str,
        turn_id: str | None,
        harness_id: str,
        adapter_id: str,
        author: str,
        content: dict[str, Any],
        actions: dict[str, Any],
        haas: dict[str, Any],
    ) -> CanonicalEventRecord:
        _validate_typed_metadata(type_, haas)
        key = (app_name, user_id, session_id)
        existing = (
            self.store.read_invocation(key, invocation_id, after=-1)
            if invocation_id is not None
            else self.store.read_session(key)
        )
        sequence = len(existing)
        event = CanonicalEventRecord(
            eventId=self.store.next_event_id(),
            invocationId=invocation_id,
            sessionId=session_id,
            turnId=turn_id,
            appName=app_name,
            userId=user_id,
            harnessId=harness_id,
            adapterId=adapter_id,
            author=author,
            sequenceNumber=sequence,
            content=cast(dict[str, Any], redact(content)),
            actions=cast(dict[str, Any], redact(actions)),
            type=type_,
            haas=cast(dict[str, Any], redact(haas)),
            redactionApplied=True,
        )
        self.store.append(event)
        return event

    def read_invocation(
        self, app_name: str, user_id: str, session_id: str, invocation_id: str
    ) -> list[CanonicalEventRecord]:
        return self.store.read_invocation((app_name, user_id, session_id), invocation_id, after=-1)

    def read_session(
        self,
        app_name: str,
        user_id: str,
        session_id: str,
        after_event_id: str | None = None,
    ) -> list[CanonicalEventRecord]:
        return self.store.read_session((app_name, user_id, session_id), after_cursor=after_event_id)

    def project_adk(self, event: CanonicalEventRecord) -> dict[str, Any]:
        return {
            "id": event.eventId,
            "invocationId": event.invocationId,
            "author": event.author,
            "timestamp": event.observedAtMs / 1000.0,
            "content": event.content,
            "actions": event.actions,
            "longRunningToolIds": [],
        }

    def sse_frame(self, event: CanonicalEventRecord) -> str:
        return f"data: {json.dumps(self.project_adk(event), separators=(',', ':'))}\n\n"

    def haas_frame(self, event: CanonicalEventRecord) -> str:
        return f"data: {json.dumps(self.project_haas(event), separators=(',', ':'))}\n\n"

    def project_haas(self, event: CanonicalEventRecord) -> dict[str, Any]:
        payload = asdict(event)
        for internal in ("userId", "adapterId", "schemaVersion", "redactionApplied"):
            payload.pop(internal, None)
        return payload


def _infer_event_type(content: dict[str, Any], actions: dict[str, Any]) -> str:
    status = (actions.get("stateDelta") or {}).get("status")
    if status in {"completed", "failed", "incomplete", "interrupted", "cancelled"}:
        return f"haas.turn.{status}"
    parts = content.get("parts") or []
    if any(isinstance(part, dict) and isinstance(part.get("text"), str) for part in parts):
        return "haas.output.text.delta"
    return "haas.adapter.event_unparsed"


def _infer_haas_metadata(type_: str, actions: dict[str, Any]) -> dict[str, Any]:
    status = str((actions.get("stateDelta") or {}).get("status") or "")
    if type_ == "haas.turn.completed":
        return {"status": "completed"}
    if type_ == "haas.turn.cancelled":
        return {"status": "cancelled"}
    if type_ == "haas.turn.interrupted":
        return {"status": "interrupted", "controlIntent": "pause"}
    if type_ in {"haas.turn.failed", "haas.turn.incomplete"}:
        reason = str((actions.get("stateDelta") or {}).get("reason") or status)
        code = str((actions.get("stateDelta") or {}).get("code") or reason)
        retryable = bool((actions.get("stateDelta") or {}).get("retryable", False))
        return {"status": status, "code": code, "safeReason": reason, "retryable": retryable}
    if type_ == "haas.adapter.event_unparsed":
        return {"safeReason": "event_type_unparsed", "retryable": False}
    return {}


def _harness_event_metadata(type_: str, event: HarnessEvent) -> dict[str, Any]:
    state = event.actions.get("stateDelta") or {}
    artifact = event.actions.get("artifactDelta") or {}
    native_metadata = event.actions.get("haas") or {}
    if type_ in {
        "haas.output.text.delta",
        "haas.output.reasoning.delta",
        "haas.output.item.completed",
    }:
        allowed = {"itemId", "modelCallId", "messagePhase", "summaryIndex"}
        return {
            key: value
            for key, value in native_metadata.items()
            if key in allowed and value is not None
        } if isinstance(native_metadata, dict) else {}
    if type_ == "haas.tool.started":
        return _with_tool_activity_fields({
            "toolCallId": str(artifact.get("toolCallId") or ""),
            "toolName": str(artifact.get("toolName") or "unknown"),
            "safeSummary": str(artifact.get("safeSummary") or "Tool started"),
        }, artifact)
    if type_ == "haas.tool.output":
        return _with_tool_activity_fields({
            "toolCallId": str(artifact.get("toolCallId") or ""),
            "toolName": str(artifact.get("toolName") or "unknown"),
            "safeSummary": str(artifact.get("safeSummary") or "Tool produced output"),
        }, artifact)
    if type_ == "haas.tool.completed":
        return _with_tool_activity_fields({
            "toolCallId": str(artifact.get("toolCallId") or ""),
            "toolName": str(artifact.get("toolName") or "unknown"),
            "status": "completed",
        }, artifact)
    if type_ == "haas.tool.failed":
        return _with_tool_activity_fields({
            "toolCallId": str(artifact.get("toolCallId") or ""),
            "toolName": str(artifact.get("toolName") or "unknown"),
            "status": "failed",
            "code": str(artifact.get("code") or "tool_failed"),
            "safeReason": str(artifact.get("safeReason") or "Tool failed"),
            "retryable": bool(artifact.get("retryable", False)),
        }, artifact)
    if type_ in _INTERACTION_EVENT_TYPES:
        metadata = event.actions.get("haas") or {}
        return dict(metadata) if isinstance(metadata, dict) else {}
    if type_ == "haas.usage.updated":
        metadata = {"usage": dict(event.usage or state.get("usage") or {})}
        if isinstance(native_metadata, dict):
            for key in ("scope", "modelCallId", "cumulativeUsage"):
                if native_metadata.get(key) is not None:
                    metadata[key] = native_metadata[key]
        return metadata
    if type_ == "haas.plan.updated":
        metadata = event.actions.get("haas") or {}
        return dict(metadata) if isinstance(metadata, dict) else {}
    if type_ == "haas.turn.started":
        return {"status": "running"}
    if type_.startswith("haas.turn."):
        return _infer_haas_metadata(type_, event.actions)
    if type_ == "haas.adapter.event_unparsed":
        return {"safeReason": "event_type_unparsed", "retryable": False}
    return {}


def _with_tool_activity_fields(
    metadata: dict[str, Any], artifact: dict[str, Any]
) -> dict[str, Any]:
    activity_kind = artifact.get("activityKind")
    if activity_kind in {"command", "read", "search", "edit", "tool"}:
        metadata["activityKind"] = activity_kind
    for key in ("durationMs", "exitCode", "evidenceExpiresAtMs"):
        value = artifact.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            metadata[key] = value
    for key in ("commandPreview", "workingDirectory", "evidenceRef"):
        value = artifact.get(key)
        if isinstance(value, str) and value:
            metadata[key] = value
    model_call_id = artifact.get("modelCallId")
    if isinstance(model_call_id, str) and model_call_id:
        metadata["modelCallId"] = model_call_id
    preview = artifact.get("outputPreview")
    if isinstance(preview, str) and preview:
        bounded, locally_omitted = bounded_redacted_preview(preview)
        metadata["outputPreview"] = bounded
        raw_omitted = artifact.get("omittedLineCount")
        omitted = (
            raw_omitted
            if isinstance(raw_omitted, int) and not isinstance(raw_omitted, bool)
            else 0
        )
        metadata["omittedLineCount"] = max(
            omitted,
            locally_omitted,
        )
    return metadata


def _validate_typed_metadata(type_: str, haas: dict[str, Any]) -> None:
    interaction_required = {
        "haas.approval.required": {
            "approvalId",
            "kind",
            "safeSummary",
            "policyReason",
            "availableDecisions",
            "expiresAtMs",
        },
        "haas.approval.resolved": {"approvalId", "status"},
        "haas.input.required": {
            "inputRequestId",
            "questions",
            "blocking",
            "expiresAtMs",
        },
        "haas.input.resolved": {"inputRequestId", "status"},
    }
    if type_ == "haas.plan.updated":
        counts = haas.get("counts")
        if (
            not isinstance(counts, dict)
            or set(counts) != {"pending", "inProgress", "completed"}
            or any(not isinstance(value, int) or value < 0 for value in counts.values())
            or not isinstance(haas.get("total"), int)
            or haas["total"] < 0
            or sum(counts.values()) != haas["total"]
        ):
            raise ValueError("invalid typed plan metadata")
        return
    if type_ in interaction_required:
        missing = sorted(interaction_required[type_] - haas.keys())
        if missing:
            raise ValueError(f"missing typed event metadata: {', '.join(missing)}")
        return
    if type_ not in _POLICY_UPDATE_TYPES:
        return
    required = {"delegatedSessionId", "updateId", "revision", "fields"}
    if type_ == "haas.delegation.policy_update_failed":
        required |= {"code", "safeReason"}
    missing = sorted(required - haas.keys())
    if missing:
        raise ValueError(f"missing typed event metadata: {', '.join(missing)}")
