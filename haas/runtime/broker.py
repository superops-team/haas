"""Private broker seam for network-isolated delegated workers.

P0 deliberately ships only the deterministic offline implementation.  A worker
must never fall back to direct provider or MCP network access when no broker is
configured.
"""

from __future__ import annotations

from typing import Protocol


class BrokerUnavailable(RuntimeError):
    """Raised when a requested broker capability is not configured."""


class WorkerBroker(Protocol):
    def model(self, request: dict[str, object]) -> dict[str, object]: ...

    def mcp(self, request: dict[str, object]) -> dict[str, object]: ...


class UnavailableWorkerBroker:
    """Fail-closed production placeholder until the private relay ships."""

    def model(self, request: dict[str, object]) -> dict[str, object]:
        raise BrokerUnavailable("delegation_model_broker_unavailable")

    def mcp(self, request: dict[str, object]) -> dict[str, object]:
        raise BrokerUnavailable("delegation_mcp_broker_unavailable")


class FakeWorkerBroker:
    """Fixed offline relay used only by the private-worker smoke path."""

    def model(self, request: dict[str, object]) -> dict[str, object]:
        return {"text": str(request.get("text") or "offline")}

    def mcp(self, request: dict[str, object]) -> dict[str, object]:
        return {"tools": []}
