"""Admission Control: quota/rate/queue admission (specs/admission-control/)."""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from haas.stores import MemoryStore


def _now_ms() -> int:
    return int(time.time() * 1000)


@dataclass
class AdmissionInput:
    principalHash: str
    appName: str
    tenantId: str | None = None
    workspaceId: str | None = None
    resource: str = "run"
    estimatedCost: int = 1


@dataclass
class AdmissionDecision:
    allowed: bool
    queued: bool = False
    leaseId: str | None = None
    limitName: str | None = None
    remaining: int | None = None
    code: str | None = None
    safeReason: str | None = None
    retryAfterMs: int | None = None


@dataclass
class AdmissionControl:
    store: MemoryStore
    run_quota: int = 20
    rate_limit: int = 100
    rate_window_ms: int = 60_000
    _leases: dict[str, str] = field(default_factory=dict)

    def _bucket(self, req: AdmissionInput) -> str:
        return f"{req.resource}:{req.tenantId or req.principalHash}"

    def admit_run(self, req: AdmissionInput) -> AdmissionDecision:
        bucket = self._bucket(req)
        now = _now_ms()

        count = self.store.incr_window(bucket, now, self.rate_window_ms)
        if count > self.rate_limit:
            return AdmissionDecision(
                allowed=False,
                code="haas_rate_limited",
                safeReason="rate_limit_exceeded",
                retryAfterMs=self.rate_window_ms,
            )

        if not self.store.acquire_quota(bucket, self.run_quota):
            return AdmissionDecision(
                allowed=False,
                code="haas_quota_exceeded",
                safeReason="quota_exceeded",
                retryAfterMs=1000,
            )

        lease_id = f"adm_{uuid.uuid4().hex[:12]}"
        self._leases[lease_id] = bucket
        remaining = max(0, self.run_quota - self.store.quota_count(bucket))
        return AdmissionDecision(
            allowed=True,
            leaseId=lease_id,
            limitName="runs_per_tenant",
            remaining=remaining,
        )

    def release_run(self, run_id: str) -> None:
        bucket = self._leases.pop(run_id, None)
        if bucket is not None:
            self.store.release_quota(bucket)

    def snapshot_queues(self) -> dict[str, Any]:
        return {"runQueueDepth": 0, "runQueueLimit": 0, "activeRuns": 0}
