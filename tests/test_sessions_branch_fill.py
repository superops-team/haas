"""Branch-fill tests for SessionRuntime cancel/pause/continue/update_policy.

Targets error and idempotent branches that the main happy-path suite does not
exercise: unknown / mismatched ids, already-terminal invocations, no active
turn (lease fallback path), interrupted-resumable revocation, and policy
pending-vs-apply control-state gating. Uses direct store records so the
control-plane branches are hit without driving a full adapter run.
"""

import pytest

from haas.events import EventLog
from haas.harnesses import FakeAdapter
from haas.identity import Principal
from haas.registry import HarnessRegistry, seed_codex
from haas.sessions import (
    InvocationNotResumableError,
    InvocationNotRunningError,
    InvocationNotFoundError,
    PolicyRevisionConflictError,
    PolicyUpdateInvalidError,
    RunRequest,
    SessionRuntime,
)
from haas.stores import (
    InvocationRecord,
    MemoryStore,
    SessionRecord,
    TurnRecord,
)

APP = "chrn_codex_default"


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


def _put_session(
    runtime: SessionRuntime,
    sid: str,
    *,
    control_state: str = "idle",
    supports_resume: bool = False,
    resumable_id: str | None = None,
) -> SessionRecord:
    session = SessionRecord(
        id=sid,
        appName=APP,
        userId="u_1",
        controlState=control_state,
        supportsResume=supports_resume,
        resumableInvocationId=resumable_id,
    )
    runtime.store.put_session(session)
    return session


def _put_invocation(
    runtime: SessionRuntime,
    iid: str,
    sid: str,
    *,
    status: str = "running",
    turn_id: str = "turn_1",
) -> InvocationRecord:
    invocation = InvocationRecord(
        id=iid,
        sessionId=sid,
        appName=APP,
        turnId=turn_id,
        userId="u_1",
        status=status,
    )
    runtime.store.put_invocation(invocation)
    return invocation


# --- cancel_invocation -------------------------------------------------


async def test_cancel_unknown_invocation_raises_not_found(
    runtime: SessionRuntime,
) -> None:
    with pytest.raises(InvocationNotFoundError):
        await runtime.cancel_invocation("hsess_x", "inv_nope")


async def test_cancel_invocation_for_wrong_session_raises_not_found(
    runtime: SessionRuntime,
) -> None:
    _put_invocation(runtime, "inv_real", "hsess_real")
    with pytest.raises(InvocationNotFoundError):
        await runtime.cancel_invocation("hsess_wrong", "inv_real")


async def test_cancel_already_terminal_invocation_is_idempotent(
    runtime: SessionRuntime,
) -> None:
    _put_invocation(runtime, "inv_done", "hsess_done", status="completed")
    out = await runtime.cancel_invocation("hsess_done", "inv_done")
    assert out.status == "completed"
    assert runtime.store.get_invocation("inv_done").status == "completed"


async def test_cancel_running_invocation_without_active_turn_uses_lease_path(
    runtime: SessionRuntime,
) -> None:
    sid, iid, tid = "hsess_lease", "inv_lease", "turn_lease"
    _put_session(runtime, sid, control_state="running")
    _put_invocation(runtime, iid, sid, status="running", turn_id=tid)
    runtime.store.put_turn(
        TurnRecord(id=tid, invocationId=iid, sessionId=sid)
    )

    await runtime.cancel_invocation(sid, iid)

    stored = runtime.store.get_invocation(iid)
    assert stored.status == "cancelled"


async def test_cancel_running_invocation_with_missing_turn_raises_not_found(
    runtime: SessionRuntime,
) -> None:
    sid, iid = "hsess_noturn", "inv_noturn"
    _put_session(runtime, sid, control_state="running")
    _put_invocation(runtime, iid, sid, status="running", turn_id="turn_missing")
    with pytest.raises(InvocationNotFoundError):
        await runtime.cancel_invocation(sid, iid)


async def test_cancel_interrupted_resumable_session_marks_cancelled(
    runtime: SessionRuntime,
) -> None:
    sid, iid = "hsess_paused", "inv_int"
    _put_session(
        runtime, sid, control_state="paused", supports_resume=True, resumable_id=iid
    )
    _put_invocation(runtime, iid, sid, status="interrupted")

    out = await runtime.cancel_invocation(sid, iid)

    assert out.status == "interrupted"
    session = runtime.get_session(APP, "u_1", sid)
    assert session.controlState == "cancelled"
    assert session.supportsResume is False
    assert session.resumableInvocationId is None


async def test_cancel_interrupted_non_resumable_session_is_untouched(
    runtime: SessionRuntime,
) -> None:
    sid, iid = "hsess_notresume", "inv_int2"
    _put_session(
        runtime, sid, control_state="paused", supports_resume=False, resumable_id=iid
    )
    _put_invocation(runtime, iid, sid, status="interrupted")

    out = await runtime.cancel_invocation(sid, iid)

    assert out.status == "interrupted"
    session = runtime.get_session(APP, "u_1", sid)
    assert session.controlState == "paused"
    assert session.supportsResume is False


# --- pause_invocation --------------------------------------------------


async def test_pause_unknown_invocation_raises_not_found(
    runtime: SessionRuntime,
) -> None:
    with pytest.raises(InvocationNotFoundError):
        await runtime.pause_invocation("hsess_p", "inv_nope")


async def test_pause_already_interrupted_invocation_is_idempotent(
    runtime: SessionRuntime,
) -> None:
    sid, iid = "hsess_idleint", "inv_int3"
    _put_session(runtime, sid, control_state="paused")
    _put_invocation(runtime, iid, sid, status="interrupted")
    out = await runtime.pause_invocation(sid, iid)
    assert out.status == "interrupted"


async def test_pause_completed_invocation_raises_not_running(
    runtime: SessionRuntime,
) -> None:
    sid, iid = "hsess_pdone", "inv_pdone"
    _put_session(runtime, sid)
    _put_invocation(runtime, iid, sid, status="completed")
    with pytest.raises(InvocationNotRunningError):
        await runtime.pause_invocation(sid, iid)


# --- continue / resume -------------------------------------------------


async def test_continue_unknown_invocation_raises_not_found(
    runtime: SessionRuntime,
) -> None:
    with pytest.raises(InvocationNotFoundError):
        async for _ in runtime.continue_stream("hsess_c", "inv_nope"):  # pragma: no cover
            pass


async def test_continue_completed_source_raises_not_resumable(
    runtime: SessionRuntime,
) -> None:
    sid, iid = "hsess_cdone", "inv_cdone"
    _put_session(runtime, sid, control_state="idle")
    _put_invocation(runtime, iid, sid, status="completed")
    with pytest.raises(InvocationNotResumableError):
        async for _ in runtime.continue_stream(sid, iid):  # pragma: no cover
            pass


async def test_continue_interrupted_but_session_not_paused_raises_not_resumable(
    runtime: SessionRuntime,
) -> None:
    sid, iid = "hsess_crun", "inv_crun"
    _put_session(runtime, sid, control_state="running", supports_resume=True)
    _put_invocation(runtime, iid, sid, status="interrupted")
    with pytest.raises(InvocationNotResumableError):
        async for _ in runtime.continue_stream(sid, iid):  # pragma: no cover
            pass


# --- update_policy ----------------------------------------------------


_WS = {"mode": "workspace-write", "root": "/workspace", "writableRoots": ["/workspace"]}
_NET = {"defaultAction": "deny", "allow": ["example.com"]}


def test_update_policy_running_session_marks_pending(runtime: SessionRuntime) -> None:
    session = _put_session(runtime, "hs", control_state="running")
    out = runtime.update_policy(session, expected_revision=1, delta={"network": _NET})
    assert out.policyStatus == "pending"
    assert out.appliedRevision == 1
    assert out.pendingPolicyUpdate is not None


def test_update_policy_pausing_session_marks_pending(runtime: SessionRuntime) -> None:
    session = _put_session(runtime, "hs", control_state="pausing")
    out = runtime.update_policy(session, expected_revision=1, delta={"network": _NET})
    assert out.policyStatus == "pending"
    assert out.appliedRevision == 1


def test_update_policy_idle_session_applies_instantly(runtime: SessionRuntime) -> None:
    session = _put_session(runtime, "hs", control_state="idle")
    out = runtime.update_policy(session, expected_revision=1, delta={"workspace": _WS})
    assert out.policyStatus == "applied"
    assert out.appliedRevision == out.desiredRevision == 2
    assert out.pendingPolicyUpdate is None


def test_update_policy_rejects_revision_conflict(runtime: SessionRuntime) -> None:
    session = _put_session(runtime, "hs", control_state="running")
    with pytest.raises(PolicyRevisionConflictError):
        runtime.update_policy(session, expected_revision=999, delta={"workspace": _WS})


def test_update_policy_rejects_unknown_domain(runtime: SessionRuntime) -> None:
    session = _put_session(runtime, "hs")
    with pytest.raises(PolicyUpdateInvalidError):
        runtime.update_policy(session, expected_revision=1, delta={"hax": {}})


async def test_run_smoke_still_works_alongside_fixture(runtime: SessionRuntime) -> None:
    """Guard that the shared fixture constructs a usable runtime."""
    req = RunRequest(
        app=runtime.registry.resolve_default_app(Principal("p")),
        user_id="u_1",
        message={"role": "user", "parts": [{"text": "hi"}]},
    )
    result = await runtime.run(req)
    assert result.invocation.status == "completed"
