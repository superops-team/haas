"""Durable, transport-independent state for merging HaaS event projections."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Literal

from .task_completion import TaskCompletionState

Projection = Literal["adk", "native", "session"]
REASONING_PREVIEW_LIMIT = 240


@dataclass(frozen=True, slots=True)
class SessionKey:
    """The complete ADK session identity; session ids alone are not global."""

    app_name: str
    user_id: str
    session_id: str

    def to_dict(self) -> dict[str, str]:
        return {
            "appName": self.app_name,
            "userId": self.user_id,
            "sessionId": self.session_id,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> SessionKey:
        return cls(
            app_name=str(value["appName"]),
            user_id=str(value["userId"]),
            session_id=str(value["sessionId"]),
        )


@dataclass(frozen=True, slots=True)
class BridgeAction:
    """A serializable Manager projection emitted by the bridge."""

    kind: str
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "payload": dict(self.payload)}


@dataclass(slots=True)
class StreamBridgeState:
    """Merge ADK, invocation-native, and session-lifecycle projections.

    Callers persist the object after every consume call and before publishing the
    returned actions.  Transport reconnect and persistence are deliberately kept
    outside this pure state machine.
    """

    endpoint_id: str
    session: SessionKey
    invocation_id: str
    adk_cursor: str | None = None
    native_cursor: str | None = None
    session_cursor: str | None = None
    assistant_text: str = ""
    native_terminal_event_id: str | None = None
    terminal_status: str | None = None
    terminal_code: str | None = None
    terminal_safe_reason: str | None = None
    terminal_retryable: bool | None = None
    reasoning_summary: str = ""
    activities: dict[str, dict[str, Any]] = field(default_factory=dict)
    model_stages: list[dict[str, Any]] = field(default_factory=list)
    completed: bool = False
    task: TaskCompletionState = field(default_factory=TaskCompletionState)
    _seen: set[tuple[str, str, str, str, Projection]] = field(default_factory=set)
    _tool_seen: set[tuple[str, str]] = field(default_factory=set)
    _content_seen: set[tuple[str, str]] = field(default_factory=set)

    def consume_adk(
        self,
        *,
        event_id: str,
        cursor: str,
        text: str | None = None,
        reasoning: str | None = None,
        artifact: Mapping[str, Any] | None = None,
        terminal: bool = False,
    ) -> list[BridgeAction]:
        if not self._claim(event_id, "adk"):
            return []
        self.adk_cursor = cursor
        actions: list[BridgeAction] = []
        if text and self._claim_content(event_id, "assistant"):
            self.assistant_text += text
            actions.append(BridgeAction("assistant_delta", {"text": text}))
        if reasoning and self._claim_content(event_id, "reasoning"):
            self._append_reasoning(reasoning)
            actions.append(BridgeAction("reasoning_delta", {"text": reasoning}))
        tool = dict(artifact or {})
        if tool.get("toolCallId"):
            if tool.get("status") in {"completed", "failed", "cancelled"}:
                actions.extend(self._tool_actions(str(tool["toolCallId"]), "finished", tool))
            elif tool.get("outputPreview") is not None and self._claim_content(
                event_id, "tool_output"
            ):
                actions.append(BridgeAction("tool_output_delta", tool))
            elif tool.get("safeSummary") is not None:
                actions.extend(self._tool_actions(str(tool["toolCallId"]), "started", tool))
        if terminal and event_id == self.native_terminal_event_id:
            actions.extend(self._finish())
        return actions

    def consume_native(
        self,
        *,
        event_id: str,
        cursor: str,
        event_type: str,
        code: str | None = None,
        safe_reason: str | None = None,
        retryable: bool | None = None,
        payload: Mapping[str, Any] | None = None,
        content: Mapping[str, Any] | None = None,
    ) -> list[BridgeAction]:
        if not self._claim(event_id, "native"):
            return []
        self.native_cursor = cursor
        if event_type.startswith("invocation.") and event_type.removeprefix("invocation.") in {
            "completed",
            "failed",
            "cancelled",
            "incomplete",
            "interrupted",
        }:
            self.native_terminal_event_id = event_id
            self.terminal_status = event_type.removeprefix("invocation.")
            self.terminal_code = code
            self.terminal_safe_reason = safe_reason
            self.terminal_retryable = retryable
            self.task.observe_terminal(self.terminal_status, code=code, safe_reason=safe_reason)
            # ADK may have arrived first. Its projection claim proves all text before
            # this terminal was consumed, so the barrier can close immediately.
            if self._projection_seen(event_id, "adk"):
                return self._finish()
            return []
        if event_type == "haas.output.reasoning.delta":
            parts = (content or {}).get("parts") or []
            text = "".join(
                str(part.get("text") or "")
                for part in parts
                if isinstance(part, Mapping) and part.get("thought") is True
            )
            claimed = bool(text) and self._claim_content(event_id, "reasoning")
            if claimed:
                self._append_reasoning(text)
            actions = [BridgeAction("reasoning_delta", {"text": text})] if claimed else []
            if (
                text
                and self._append_stage_text(
                    data=dict(payload or {}), text=text, kind="reasoning_summary"
                )
                and (payload or {}).get("modelCallId")
            ):
                actions.append(self._stage_action())
            return actions
        data = dict(payload or {})
        if event_type == "haas.output.text.delta":
            parts = (content or {}).get("parts") or []
            text = "".join(
                str(part.get("text") or "")
                for part in parts
                if isinstance(part, Mapping) and part.get("thought") is not True
            )
            return (
                [self._stage_action()]
                if text
                and self._append_stage_text(data=data, text=text, kind="output_pending")
                and data.get("modelCallId")
                else []
            )

        if event_type == "haas.output.item.completed":
            changed = self._freeze_reasoning_item(data)
            changed = self._classify_stage_item(data) or changed
            return [self._stage_action()] if changed and data.get("modelCallId") else []
        if event_type == "haas.usage.updated":
            return (
                [self._stage_action()]
                if self._meter_stage(data) and data.get("modelCallId")
                else []
            )
        if event_type == "haas.plan.updated":
            counts = data.get("counts")
            if isinstance(counts, Mapping):
                self.task.observe_plan(counts)
            return [BridgeAction("task_state", self.task.to_dict())]
        if event_type == "haas.approval.required":
            self.task.require_approval(
                str(data.get("approvalId")) if data.get("approvalId") else None
            )
        elif event_type == "haas.input.required":
            self.task.require_input(
                str(data.get("inputRequestId")) if data.get("inputRequestId") else None
            )
        elif event_type == "haas.approval.resolved" and data.get("approvalId"):
            self.task.resolve_interaction(str(data["approvalId"]))
        elif event_type == "haas.input.resolved" and data.get("inputRequestId"):
            self.task.resolve_interaction(str(data["inputRequestId"]))
        if event_type == "haas.tool.started":
            tool_call_id = str(data.get("toolCallId") or "")
            actions = self._tool_actions(tool_call_id, "started", data)
            if self._upsert_stage_tool(data, terminal=False) and data.get("modelCallId"):
                actions.append(self._stage_action())
            return actions
        kind = {
            "haas.tool.output": "tool_output_delta",
            "haas.tool.output.delta": "tool_output_delta",
            "haas.tool.completed": "tool_finished",
            "haas.tool.failed": "tool_finished",
            "haas.approval.required": "permission_required",
            "haas.input.required": "question_requested",
        }.get(event_type)
        if kind == "tool_finished":
            actions = self._tool_actions(str(data.get("toolCallId") or ""), "finished", data)
            if self._upsert_stage_tool(data, terminal=True) and data.get("modelCallId"):
                actions.append(self._stage_action())
            return actions
        if kind == "tool_output_delta" and not self._claim_content(event_id, "tool_output"):
            return []
        if kind == "tool_output_delta":
            self._record_activity(data, "running")
        return [BridgeAction(kind, data)] if kind else []

    def reconcile_authoritative_terminal(
        self, *, status: str, terminal_event_id: str | None = None
    ) -> list[BridgeAction]:
        """Close a stalled dual-stream barrier from matching durable readback."""

        if self.completed or self.native_terminal_event_id is None:
            return []
        if terminal_event_id is not None and terminal_event_id != self.native_terminal_event_id:
            return []
        if status != self.terminal_status:
            return []
        return self._finish()

    def _stage(self, model_call_id: str | None) -> dict[str, Any]:
        stage_id = model_call_id or "legacy"
        for stage in self.model_stages:
            if stage.get("modelCallId") == stage_id:
                return stage
        if model_call_id and self.model_stages:
            previous = self.model_stages[-1]
            if previous.get("status") == "running":
                self._freeze_reasoning_previews(previous)
                previous["status"] = "completed"
        stage: dict[str, Any] = {
            "modelCallId": stage_id,
            "status": "running",
            "steps": [],
        }
        self.model_stages.append(stage)
        return stage

    def _append_stage_text(self, *, data: dict[str, Any], text: str, kind: str) -> bool:
        stage = self._stage(str(data.get("modelCallId") or "") or None)
        item_id = str(data.get("itemId") or "")
        summary_index = data.get("summaryIndex")
        step_id = item_id or f"event_{len(stage['steps']) + 1}"
        if kind == "reasoning_summary" and isinstance(summary_index, int):
            step_id = f"{step_id}:{summary_index}"
        for index, step in enumerate(stage["steps"]):
            if step.get("stepId") == step_id and step.get("kind") == kind:
                previous_text = str(step.get("text") or "")
                if kind == "reasoning_summary":
                    if index != len(stage["steps"]) - 1:
                        self._freeze_reasoning_preview(step)
                    if not step.get("previewFrozen"):
                        preview = str(step.get("previewText") or previous_text) + text
                        step["previewText"] = preview[:REASONING_PREVIEW_LIMIT]
                        if len(preview) >= REASONING_PREVIEW_LIMIT:
                            step["previewFrozen"] = True
                step["text"] = previous_text + text
                return True
        self._freeze_reasoning_previews(stage)
        step = {"stepId": step_id, "kind": kind, "text": text}
        if kind == "reasoning_summary":
            step["previewText"] = text[:REASONING_PREVIEW_LIMIT]
            step["previewFrozen"] = len(text) >= REASONING_PREVIEW_LIMIT
        stage["steps"].append(step)
        return True

    @staticmethod
    def _freeze_reasoning_preview(step: dict[str, Any]) -> bool:
        if step.get("kind") != "reasoning_summary" or step.get("previewFrozen"):
            return False
        step["previewText"] = str(step.get("previewText") or step.get("text") or "")[
            :REASONING_PREVIEW_LIMIT
        ]
        step["previewFrozen"] = True
        return True

    def _freeze_reasoning_previews(self, stage: dict[str, Any]) -> bool:
        changed = False
        for step in stage.get("steps", []):
            changed = self._freeze_reasoning_preview(step) or changed
        return changed

    def _freeze_reasoning_item(self, data: dict[str, Any]) -> bool:
        item_id = str(data.get("itemId") or "")
        if not item_id:
            return False
        stage_id = str(data.get("modelCallId") or "") or "legacy"
        stage = next(
            (
                candidate
                for candidate in self.model_stages
                if candidate.get("modelCallId") == stage_id
            ),
            None,
        )
        if stage is None:
            return False
        changed = False
        for step in stage["steps"]:
            step_owner = str(step.get("stepId") or "").rsplit(":", 1)[0]
            if step.get("kind") == "reasoning_summary" and step_owner == item_id:
                changed = self._freeze_reasoning_preview(step) or changed
        return changed

    def _classify_stage_item(self, data: dict[str, Any]) -> bool:
        item_id = str(data.get("itemId") or "")
        phase = data.get("messagePhase")
        if not item_id or phase not in {"commentary", "final_answer"}:
            return False
        stage = self._stage(str(data.get("modelCallId") or "") or None)
        for step in stage["steps"]:
            if step.get("stepId") == item_id and step.get("kind") == "output_pending":
                step["kind"] = "commentary" if phase == "commentary" else "result"
                return True
        return False

    def _meter_stage(self, data: dict[str, Any]) -> bool:
        if data.get("scope") != "model_call" or not isinstance(data.get("usage"), Mapping):
            return False
        stage = self._stage(str(data.get("modelCallId") or "") or None)
        stage["usage"] = dict(data["usage"])
        has_tool = any(step.get("kind") == "tool" for step in stage["steps"])
        if has_tool and not stage.get("activeToolIds"):
            stage["status"] = "completed"
        return True

    def _upsert_stage_tool(self, data: dict[str, Any], *, terminal: bool) -> bool:
        tool_call_id = str(data.get("toolCallId") or "")
        if not tool_call_id:
            return False
        stage = self._stage(str(data.get("modelCallId") or "") or None)
        if not any(step.get("stepId") == tool_call_id for step in stage["steps"]):
            self._freeze_reasoning_previews(stage)
            stage["steps"].append(
                {"stepId": tool_call_id, "kind": "tool", "activityId": tool_call_id}
            )
        active = set(str(value) for value in stage.get("activeToolIds", []))
        if terminal:
            active.discard(tool_call_id)
        else:
            active.add(tool_call_id)
            stage["status"] = "running"
        if active:
            stage["activeToolIds"] = sorted(active)
        else:
            stage.pop("activeToolIds", None)
            if stage.get("usage") is not None:
                stage["status"] = "completed"
        return True

    def public_model_stages(self) -> list[dict[str, Any]]:
        """Return a detached, persistence-safe view of model-call stages."""
        return deepcopy(
            [
                {key: value for key, value in stage.items() if key != "activeToolIds"}
                for stage in self.model_stages
            ]
        )

    def _stage_action(self) -> BridgeAction:
        return BridgeAction("model_stage_updated", {"modelStages": self.public_model_stages()})

    def _tool_actions(
        self, tool_call_id: str, lifecycle: str, payload: dict[str, Any]
    ) -> list[BridgeAction]:
        if not tool_call_id:
            return []
        key = (tool_call_id, lifecycle)
        if key in self._tool_seen:
            return []
        self._tool_seen.add(key)
        scoped_payload = {**payload, "invocationId": self.invocation_id}
        if lifecycle == "started":
            self._record_activity(scoped_payload, "running")
            return [
                BridgeAction("tool_proposed", scoped_payload),
                BridgeAction("tool_started", scoped_payload),
            ]
        self._record_activity(scoped_payload, str(payload.get("status") or "completed"))
        return [BridgeAction("tool_finished", scoped_payload)]

    def _append_reasoning(self, text: str) -> None:
        combined = self.reasoning_summary + text
        if len(combined) > 4096:
            combined = combined[:2045] + "\n…\n" + combined[-2046:]
        self.reasoning_summary = combined

    def _record_activity(self, payload: Mapping[str, Any], status: str) -> None:
        tool_call_id = str(payload.get("toolCallId") or "")
        if not tool_call_id:
            return
        existing = self.activities.get(tool_call_id, {})
        activity: dict[str, Any] = {
            **existing,
            "id": tool_call_id,
            "kind": str(payload.get("activityKind") or existing.get("kind") or "tool"),
            "status": status,
            "title": "",
            "summary": str(payload.get("safeSummary") or existing.get("summary") or ""),
            "preview": str(payload.get("outputPreview") or existing.get("preview") or ""),
            "omittedLineCount": int(
                payload.get("omittedLineCount") or existing.get("omittedLineCount") or 0
            ),
            "invocationId": self.invocation_id,
        }
        for key in (
            "durationMs",
            "exitCode",
            "safeReason",
            "recoveryGroupId",
            "commandPreview",
            "workingDirectory",
            "evidenceRef",
            "evidenceExpiresAtMs",
        ):
            if payload.get(key) is not None:
                activity[key] = payload[key]
        self.activities[tool_call_id] = activity

    def consume_session(
        self,
        *,
        event_id: str,
        cursor: str,
        event_type: str,
        payload: Mapping[str, Any] | None = None,
    ) -> list[BridgeAction]:
        if not self._claim(event_id, "session"):
            return []
        self.session_cursor = cursor
        return [BridgeAction("status", {"type": event_type, **dict(payload or {})})]

    def _claim(self, event_id: str, projection: Projection) -> bool:
        key = self._dedupe_key(event_id, projection)
        if key in self._seen:
            return False
        self._seen.add(key)
        return True

    def _claim_content(self, event_id: str, kind: str) -> bool:
        key = (event_id, kind)
        if key in self._content_seen:
            return False
        self._content_seen.add(key)
        return True

    def _projection_seen(self, event_id: str, projection: Projection) -> bool:
        return self._dedupe_key(event_id, projection) in self._seen

    def _dedupe_key(
        self, event_id: str, projection: Projection
    ) -> tuple[str, str, str, str, Projection]:
        full_session = "\x1f".join(
            (self.session.app_name, self.session.user_id, self.session.session_id)
        )
        return (
            self.endpoint_id,
            full_session,
            self.invocation_id,
            event_id,
            projection,
        )

    def _finish(self) -> list[BridgeAction]:
        if self.completed or self.terminal_status is None:
            return []
        self.completed = True
        if self.terminal_status == "completed" and self.model_stages:
            for step in self.model_stages[-1].get("steps", []):
                if step.get("kind") == "output_pending":
                    step["kind"] = "result"
        for stage in self.model_stages:
            self._freeze_reasoning_previews(stage)
            if (
                self.terminal_status in {"failed", "incomplete", "interrupted", "cancelled"}
                and stage.get("status") == "running"
            ):
                stage["status"] = self.terminal_status
            elif stage.get("status") == "running":
                stage["status"] = "completed"
        outcome: dict[str, Any] = {"status": self.terminal_status}
        outcome["taskPhase"] = self.task.phase
        if self.terminal_code is not None:
            outcome["code"] = self.terminal_code
        if self.terminal_safe_reason is not None:
            outcome["safeReason"] = self.terminal_safe_reason
        if self.terminal_retryable is not None:
            outcome["retryable"] = self.terminal_retryable
        actions: list[BridgeAction] = []
        for activity in self.activities.values():
            if activity.get("status") not in {"running", "pending", "waiting"}:
                continue
            status = (
                "cancelled"
                if self.terminal_status in {"interrupted", "cancelled"}
                else "failed"
            )
            activity["status"] = status
            activity.setdefault("safeReason", "Tool ended without a terminal event")
            actions.append(BridgeAction("tool_finished", dict(activity)))
        actions.extend(
            [
                BridgeAction(
                    "assistant_message",
                    {
                        "text": self.assistant_text,
                        "reasoning": self.reasoning_summary,
                        "modelStages": self.public_model_stages(),
                        "activities": [dict(activity) for activity in self.activities.values()],
                        "taskOutcome": {**self.task.to_dict(), **outcome},
                    },
                ),
                BridgeAction("turn_end", outcome),
            ]
        )
        return actions

    def to_dict(self) -> dict[str, Any]:
        return {
            "endpointId": self.endpoint_id,
            "session": self.session.to_dict(),
            "invocationId": self.invocation_id,
            "cursors": {
                "adk": self.adk_cursor,
                "native": self.native_cursor,
                "session": self.session_cursor,
            },
            "assistantText": self.assistant_text,
            "nativeTerminalEventId": self.native_terminal_event_id,
            "terminalStatus": self.terminal_status,
            "terminalCode": self.terminal_code,
            "terminalSafeReason": self.terminal_safe_reason,
            "terminalRetryable": self.terminal_retryable,
            "reasoningSummary": self.reasoning_summary,
            "activities": [dict(activity) for activity in self.activities.values()],
            "modelStages": self.public_model_stages(),
            "completed": self.completed,
            "task": self.task.to_dict(),
            "seen": [list(item) for item in sorted(self._seen)],
            "toolSeen": [list(item) for item in sorted(self._tool_seen)],
            "contentSeen": [list(item) for item in sorted(self._content_seen)],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> StreamBridgeState:
        cursors = value.get("cursors") or {}
        state = cls(
            endpoint_id=str(value["endpointId"]),
            session=SessionKey.from_dict(value["session"]),
            invocation_id=str(value["invocationId"]),
            adk_cursor=cursors.get("adk"),
            native_cursor=cursors.get("native"),
            session_cursor=cursors.get("session"),
            assistant_text=str(value.get("assistantText", "")),
            native_terminal_event_id=value.get("nativeTerminalEventId"),
            terminal_status=value.get("terminalStatus"),
            terminal_code=value.get("terminalCode"),
            terminal_safe_reason=value.get("terminalSafeReason"),
            terminal_retryable=value.get("terminalRetryable"),
            reasoning_summary=str(value.get("reasoningSummary") or ""),
            completed=bool(value.get("completed", False)),
            task=TaskCompletionState.from_dict(value.get("task")),
        )
        state.activities = {
            str(activity["id"]): dict(activity)
            for activity in value.get("activities", [])
            if isinstance(activity, Mapping) and activity.get("id")
        }
        state.model_stages = [
            dict(stage) for stage in value.get("modelStages", []) if isinstance(stage, Mapping)
        ]
        state._seen = {tuple(item) for item in value.get("seen", [])}  # type: ignore[misc]
        state._tool_seen = {tuple(item) for item in value.get("toolSeen", [])}  # type: ignore[misc]
        state._content_seen = {tuple(item) for item in value.get("contentSeen", [])}  # type: ignore[misc]
        return state
