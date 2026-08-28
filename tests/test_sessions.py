"""Session Runtime unit tests (specs/session-runtime/README.md)."""
import pytest

from haas.events import EventLog
from haas.harnesses import FakeAdapter
from haas.identity import Principal
from haas.registry import HarnessRegistry, seed_codex
from haas.sessions import RunRequest, SessionBusyError, SessionNotFoundError, SessionRuntime
from haas.stores import MemoryStore, SessionRecord


@pytest.fixture
def runtime() -> SessionRuntime:
    store = MemoryStore()
    registry = HarnessRegistry(store=store)
    seed_codex(registry)
    return SessionRuntime(
        store=store,
        registry=registry,
        adapter=FakeAdapter(),
        event_log=EventLog(store=store),
    )


async def test_run_creates_session_invocation_events(runtime: SessionRuntime) -> None:
    req = RunRequest(
        app=runtime.registry.resolve_default_app(Principal("p")),
        user_id="u_1", message={"role": "user", "parts": [{"text": "hi"}]},
    )
    result = await runtime.run(req)
    assert result.invocation.status == "completed"
    assert result.events
    assert result.events[-1].actions["stateDelta"]["status"] == "completed"
    assert result.session.id.startswith("hsess_")


async def test_session_busy(runtime: SessionRuntime) -> None:
    key = ("chrn_codex_default", "u_1", "hsess_1")
    runtime.store.acquire_lease(key, holder="other")
    req = RunRequest(
        app=runtime.registry.resolve_app(Principal("p"), "chrn_codex_default"),
        user_id="u_1", message={"role": "user", "parts": [{"text": "hi"}]}, session_id="hsess_1",
    )
    with pytest.raises(SessionBusyError):
        await runtime.run(req)


def test_get_and_delete_session(runtime: SessionRuntime) -> None:
    with pytest.raises(SessionNotFoundError):
        runtime.get_session("chrn_codex_default", "u_1", "nope")

    runtime.store.put_session(
        SessionRecord(id="hsess_1", appName="chrn_codex_default", userId="u_1")
    )
    assert runtime.get_session("chrn_codex_default", "u_1", "hsess_1").id == "hsess_1"
    runtime.delete_session("chrn_codex_default", "u_1", "hsess_1")
    with pytest.raises(SessionNotFoundError):
        runtime.get_session("chrn_codex_default", "u_1", "hsess_1")


def test_apply_state_delta_deep_merge(runtime: SessionRuntime) -> None:
    runtime.store.put_session(
        SessionRecord(id="hsess_1", appName="chrn_codex_default", userId="u_1")
    )
    runtime.apply_state_delta("chrn_codex_default", "u_1", "hsess_1", {"a": {"b": 1}})
    session = runtime.apply_state_delta("chrn_codex_default", "u_1", "hsess_1", {"a": {"c": 2}})
    assert session.state == {"a": {"b": 1, "c": 2}}
