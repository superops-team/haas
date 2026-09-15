from __future__ import annotations

from coworker.haas.stream_bridge import SessionKey, StreamBridgeState
from coworker.haas.task_completion import TaskCompletionState
from coworker.server.manager import SessionManager
from coworker.providers.base import ModelCapabilities, ProviderClient


def test_non_success_invocation_never_completes_task() -> None:
    for status in ("failed", "incomplete", "interrupted", "cancelled"):
        state = TaskCompletionState()
        state.observe_terminal(status, code="provider_failed", safe_reason="provider failed")
        assert state.phase == status
        assert state.phase != "completed"


def test_blocking_interaction_outranks_terminal_until_resolved() -> None:
    state = TaskCompletionState()
    state.require_input("inreq_1")
    state.observe_terminal("completed")
    assert state.phase == "waiting_for_input"
    state.resolve_interaction("inreq_1")
    assert state.phase == "completed"


def test_non_success_terminal_closes_pending_interaction() -> None:
    state = TaskCompletionState()
    state.require_input("inreq_1")
    state.observe_terminal(
        "failed", code="provider_failed", safe_reason="Provider unavailable"
    )

    assert state.phase == "failed"
    assert state.blocking_request_id is None
    state.resolve_interaction("inreq_1")
    assert state.phase == "failed"


def test_verification_continuation_is_bounded_and_persisted() -> None:
    state = TaskCompletionState(continuation_limit=3)
    for expected in (1, 2, 3):
        state.observe_terminal("completed", unfinished_work=True)
        assert state.phase == "verifying"
        assert state.claim_continuation(replay_safe=True)
        assert state.continuation_count == expected
    state.observe_terminal("completed", unfinished_work=True)
    assert not state.claim_continuation(replay_safe=True)
    assert state.phase == "incomplete"
    assert state.code == "haas_task_continuation_exhausted"
    assert TaskCompletionState.from_dict(state.to_dict()).to_dict() == state.to_dict()


def test_structured_plan_status_prevents_false_completion() -> None:
    state = TaskCompletionState()
    state.observe_plan({"pending": 1, "inProgress": 1, "completed": 2})
    state.observe_terminal("completed")
    assert state.phase == "verifying"
    state.observe_plan({"pending": 0, "inProgress": 0, "completed": 4})
    state.observe_terminal("completed")
    assert state.phase == "completed"


def test_unsafe_continuation_never_starts() -> None:
    state = TaskCompletionState()
    state.observe_terminal("completed", verification_required=True)
    assert not state.claim_continuation(replay_safe=False)
    assert state.phase == "verifying"
    assert state.continuation_count == 0


def test_stream_bridge_persists_task_phase_from_structured_events() -> None:
    bridge = StreamBridgeState(
        endpoint_id="local",
        session=SessionKey("chrn_codex", "manager", "hsess_1"),
        invocation_id="inv_1",
    )
    bridge.consume_native(
        event_id="evt_1",
        cursor="evt_1",
        event_type="haas.input.required",
        payload={"inputRequestId": "inreq_1"},
    )
    assert bridge.task.phase == "waiting_for_input"
    restored = StreamBridgeState.from_dict(bridge.to_dict())
    assert restored.task.blocking_request_id == "inreq_1"


def test_manager_restores_safe_task_outcome_from_session_binding(tmp_path) -> None:
    class StubProvider(ProviderClient):
        def complete(self, **kwargs):  # pragma: no cover - not called
            raise AssertionError("not called")

        def capabilities(self, model: str) -> ModelCapabilities:
            del model
            return ModelCapabilities()

    manager = SessionManager(workspace=tmp_path, provider=StubProvider())
    engine = manager.get_engine("s1", workspace=str(tmp_path), agent="cowork")
    assert engine is not None
    bridge = StreamBridgeState(
        endpoint_id="local",
        session=SessionKey("chrn_codex", "manager", "hsess_1"),
        invocation_id="inv_1",
    )
    bridge.task.observe_terminal(
        "failed", code="provider_failed", safe_reason="Provider unavailable"
    )
    bridge.terminal_retryable = True
    manager._persist_haas_binding("s1", engine, {
        "stream_bridge": bridge.to_dict(),
        "api_token": "must-not-return",
    })

    assert manager.haas_task_outcome("s1") == {
        "phase": "failed",
        "code": "provider_failed",
        "safeReason": "Provider unavailable",
        "retryable": True,
    }
