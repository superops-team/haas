"""Observability tests: metrics, structured log redaction, status snapshot."""
from __future__ import annotations

import json

from haas.observability import Metrics, StatusSnapshot, StructuredLogger


def test_metrics_counter_and_gauge() -> None:
    metrics = Metrics()
    metrics.incr("haas_model_proxy_request_total", amount=3)
    metrics.set_gauge("haas_sandbox_active", 2)
    assert metrics.counter("haas_model_proxy_request_total") == 3
    assert metrics.gauge("haas_sandbox_active") == 2.0
    assert metrics.snapshot()["haas_model_proxy_request_total"] == 3


def test_structured_logger_redacts_secret_fields() -> None:
    logger = StructuredLogger()
    event = logger.event(
        "haas.model_proxy.request_completed",
        {"authorization": "Bearer sk-secret-value", "status": "ok"},
    )
    assert "[REDACTED]" in json.dumps(event)
    assert "sk-secret-value" not in json.dumps(event)
    assert event["status"] == "ok"


def test_structured_logger_preserves_safe_fields() -> None:
    logger = StructuredLogger()
    event = logger.event("haas.turn.completed", {"sessionId": "s_1", "status": "completed"})
    assert event["sessionId"] == "s_1"


def test_status_snapshot_serializes() -> None:
    snap = StatusSnapshot(adapterStatus={"codex": "ready"}, activeSessions=1)
    data = snap.to_dict()
    assert data["service"] == "haas-sidecar"
    assert data["adapterStatus"]["codex"] == "ready"
