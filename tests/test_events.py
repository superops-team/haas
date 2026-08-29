"""Event Log & SSE tests (specs/event-log-sse/README.md)."""
from haas.events import HEARTBEAT_FRAME, EventLog
from haas.stores import MemoryStore


def test_append_assigns_gapless_sequence_and_project_adk() -> None:
    log = EventLog(store=MemoryStore())
    first = log.append(
        invocation_id="inv_1", session_id="hsess_1", turn_id="turn_1",
        harness_id="chrn_1", adapter_id="fake", author="codex",
        content={"role": "model", "parts": [{"text": "hi"}]},
        actions={},
    )
    second = log.append(
        invocation_id="inv_1", session_id="hsess_1", turn_id="turn_1",
        harness_id="chrn_1", adapter_id="fake", author="codex",
        content={"role": "model", "parts": [{"text": "there"}]},
        actions={},
    )
    assert first.sequenceNumber == 0
    assert second.sequenceNumber == 1
    assert [e.sequenceNumber for e in log.read_invocation("inv_1")] == [0, 1]

    adk = log.project_adk(first)
    assert adk["id"] == first.eventId
    assert adk["invocationId"] == "inv_1"
    assert adk["timestamp"] == first.observedAtMs / 1000.0
    assert "sequenceNumber" not in adk


def test_sse_frame_and_heartbeat() -> None:
    log = EventLog(store=MemoryStore())
    event = log.append(
        invocation_id="inv_1", session_id="hsess_1", turn_id="turn_1",
        harness_id="chrn_1", adapter_id="fake", author="codex",
        content={"role": "model", "parts": [{"text": "hi"}]},
        actions={},
    )
    frame = log.sse_frame(event)
    assert frame.startswith("data: ")
    assert frame.endswith("\n\n")
    assert HEARTBEAT_FRAME == ": keep-alive\n\n"
    assert "sequenceNumber" not in frame


def test_append_redacts_secret_material_before_persist() -> None:
    log = EventLog(store=MemoryStore())
    secret_text = "token sk-ant-abcdefghijklmnopqrstuvwxyz123456"  # haas-secret-ignore
    event = log.append(
        invocation_id="inv_1", session_id="hsess_1", turn_id="turn_1",
        harness_id="chrn_1", adapter_id="fake", author="codex",
        content={"role": "model", "parts": [{"text": secret_text}]},
        actions={"stateDelta": {"authorization": "Bearer abc"}},
    )
    assert event.redactionApplied is True
    assert "sk-ant-" not in event.content["parts"][0]["text"]
    assert event.actions["stateDelta"]["authorization"] == "[REDACTED]"
