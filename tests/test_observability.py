"""Observability tests: metrics, structured log redaction, status snapshot."""

from __future__ import annotations

import json

import pytest

from haas.observability import Metrics, StatusSnapshot, StructuredLogger


def test_metrics_counter_and_gauge() -> None:
    metrics = Metrics()
    metrics.incr("haas_model_proxy_request_total", amount=3)
    metrics.set_gauge("haas_sandbox_active", 2)
    assert metrics.counter("haas_model_proxy_request_total") == 3
    assert metrics.gauge("haas_sandbox_active") == 2.0
    assert metrics.snapshot()["haas_model_proxy_request_total"] == 3


def test_metrics_rejects_dynamic_names() -> None:
    """P2-16: metric names must be static strings, never interpolated runtime values.

    Interpolating session/user/provider ids into a metric name explodes
    cardinality. Only a fixed snake_case name is accepted.
    """
    metrics = Metrics()
    metrics.incr("haas_static_counter_total")  # static name allowed
    assert metrics.counter("haas_static_counter_total") == 1

    for bad in (
        "haas.request.total",  # dotted, not snake_case
        "haas Requests Total",  # spaces / uppercase
        "haas_requests_{session_id}",  # template placeholder
        "haas/requests",  # slash
    ):
        with pytest.raises(ValueError):
            metrics.incr(bad)
        with pytest.raises(ValueError):
            metrics.set_gauge(bad, 1.0)


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


# === appended: structured logger redaction branches ===


def test_logger_skips_redaction_when_disabled() -> None:
    logger = StructuredLogger(redact_output=False)
    fields = {"api_key": "sk-real", "n": 1}  # haas-secret-ignore
    assert logger.event("test", fields)["api_key"] == "sk-real"


def test_logger_log_returns_serialized_json() -> None:
    logger = StructuredLogger(redact_output=False)
    out = logger.log("request", {"count": 2})
    assert '"event":"request"' in out.replace(" ", "")


def test_logger_keeps_payload_when_redact_returns_non_dict(monkeypatch) -> None:
    from haas.observability import logger as logger_module

    monkeypatch.setattr(logger_module, "redact", lambda _payload: "scrubbed")
    logger = StructuredLogger(redact_output=True)
    assert logger.event("x", {"a": 1}) == {"event": "x", "a": 1}
