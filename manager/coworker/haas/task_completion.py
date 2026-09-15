"""Task completion state derived only from structured HaaS facts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Mapping

TaskPhase = Literal[
    "running",
    "waiting_for_input",
    "waiting_for_approval",
    "verifying",
    "completed",
    "incomplete",
    "interrupted",
    "failed",
    "cancelled",
]


@dataclass(slots=True)
class TaskCompletionState:
    phase: TaskPhase = "running"
    invocation_status: str | None = None
    continuation_count: int = 0
    continuation_limit: int = 3
    blocking_request_id: str | None = None
    code: str | None = None
    safe_reason: str | None = None
    pending_plan_steps: int = 0
    in_progress_plan_steps: int = 0

    def observe_plan(self, counts: Mapping[str, Any]) -> None:
        self.pending_plan_steps = max(0, int(counts.get("pending") or 0))
        self.in_progress_plan_steps = max(0, int(counts.get("inProgress") or 0))

    def require_approval(self, request_id: str | None) -> None:
        self.phase = "waiting_for_approval"
        self.blocking_request_id = request_id

    def require_input(self, request_id: str | None) -> None:
        self.phase = "waiting_for_input"
        self.blocking_request_id = request_id

    def resolve_interaction(self, request_id: str) -> None:
        if self.blocking_request_id == request_id:
            self.blocking_request_id = None
            self.phase = "completed" if self.invocation_status == "completed" else "running"

    def observe_terminal(
        self,
        status: str,
        *,
        code: str | None = None,
        safe_reason: str | None = None,
        unfinished_work: bool = False,
        verification_required: bool = False,
    ) -> None:
        self.invocation_status = status
        self.code = code
        self.safe_reason = safe_reason
        if self.blocking_request_id and status == "completed":
            return
        if status in {"failed", "cancelled", "incomplete", "interrupted"}:
            self.blocking_request_id = None
            self.phase = status  # type: ignore[assignment]
        elif status == "completed" and (
            unfinished_work
            or verification_required
            or self.pending_plan_steps > 0
            or self.in_progress_plan_steps > 0
        ):
            self.phase = "verifying"
        elif status == "completed":
            self.phase = "completed"
        else:
            self.phase = "incomplete"

    def claim_continuation(self, *, replay_safe: bool) -> bool:
        if self.phase != "verifying" or not replay_safe:
            return False
        if self.continuation_count >= self.continuation_limit:
            self.phase = "incomplete"
            self.code = "haas_task_continuation_exhausted"
            self.safe_reason = "Task continuation limit reached"
            return False
        self.continuation_count += 1
        self.phase = "running"
        self.invocation_status = None
        return True

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "invocationStatus": self.invocation_status,
            "continuationCount": self.continuation_count,
            "continuationLimit": self.continuation_limit,
            "blockingRequestId": self.blocking_request_id,
            "code": self.code,
            "safeReason": self.safe_reason,
            "pendingPlanSteps": self.pending_plan_steps,
            "inProgressPlanSteps": self.in_progress_plan_steps,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any] | None) -> TaskCompletionState:
        value = value or {}
        phase = str(value.get("phase") or "running")
        valid_phases = {
            "running",
            "waiting_for_input",
            "waiting_for_approval",
            "verifying",
            "completed",
            "incomplete",
            "interrupted",
            "failed",
            "cancelled",
        }
        return cls(
            phase=phase if phase in valid_phases else "incomplete",  # type: ignore[arg-type]
            invocation_status=value.get("invocationStatus"),
            continuation_count=max(0, int(value.get("continuationCount") or 0)),
            continuation_limit=max(1, int(value.get("continuationLimit") or 3)),
            blocking_request_id=value.get("blockingRequestId"),
            code=value.get("code"),
            safe_reason=value.get("safeReason"),
            pending_plan_steps=max(0, int(value.get("pendingPlanSteps") or 0)),
            in_progress_plan_steps=max(0, int(value.get("inProgressPlanSteps") or 0)),
        )
