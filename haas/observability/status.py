"""Runtime status snapshot (specs/observability/README.md §5.1)."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class StatusSnapshot:
    service: str = "haas-sidecar"
    status: str = "ok"
    version: str = "2026-08-26"
    adapterStatus: dict[str, str] = field(default_factory=dict)
    activeSessions: int = 0
    checks: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "service": self.service,
            "status": self.status,
            "version": self.version,
            "adapterStatus": self.adapterStatus,
            "activeSessions": self.activeSessions,
            "checks": self.checks,
        }
