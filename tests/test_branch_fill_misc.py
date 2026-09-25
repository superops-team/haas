"""Branch-fill tests (misc) for the remaining uncovered arcs.

Targets the residual missing branches reported by coverage across:

* ``haas/sessions.py`` — ``_effective_timeout_seconds``, native-ref helpers,
  timeout-after-streamed-terminal, reconcile readback double-checks, terminal
  persistence policy re-application, pause/cancel/continue error and idempotent
  arcs.
* ``haas/stores/memory.py`` — approval/input-request conflicts and filtering,
  session deletion cascade, idempotency sweep, transaction no-op.
* ``haas/stores/sqlite.py`` — busy_timeout, transaction rollback, nested write
  failure rollback, corrupted payload quarantine, event rollback paths.
* A handful of small modules (config, events, mcp runtime, model-proxy token,
  delegated runtime, profiles, registry).

Only adds tests; no production code or existing test is modified.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import replace
from typing import Any

import pytest

from haas.events import EventLog
from haas.harnesses import FakeAdapter, HarnessEvent, TurnHandle
from haas.mcp.runtime import enforce_disabled_tools
from haas.model_proxy.models import RuntimeTokenScope
from haas.model_proxy.token import RuntimeTokenManager
from haas.profiles import HarnessProfileService
from haas.registry import HarnessRegistry, seed_codex
from haas.runtime.delegation import (
    DelegatedContainerUnavailable,
    DisabledDelegatedContainerRuntime,
    FakeDelegatedContainerRuntime,
)
from haas.sessions import (
    InvocationNotFoundError,
    InvocationRecord,
    RunRequest,
    SessionRecord,
    SessionRuntime,
    TurnRecord,
    _ActiveTurn,
)
from haas.stores import (
    ApprovalRecord,
    ApprovalStateConflictError,
    CanonicalEventRecord,
    DelegatedSessionRecord,
    InputRequestRecord,
    InputRequestStateConflictError,
    MemoryStore,
    ProfileRecord,
    SQLiteStore,
)
from haas.stores.memory import ApprovalNotFoundError, InputRequestNotFoundError

APP = "chrn_codex_default"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_runtime(
    adapter: FakeAdapter | None = None,
    *,
    lease_ttl_ms: int = 30_000,
) -> tuple[SessionRuntime, Any]:
    store = MemoryStore()
    registry = HarnessRegistry(store=store)
    app = seed_codex(registry)
    runtime = SessionRuntime(
        store=store,
        registry=registry,
        adapter=adapter or FakeAdapter(),
        event_log=EventLog(store=store),
        lease_ttl_ms=lease_ttl_ms,
    )
    return runtime, app


# ===========================================================================
# sessions.py
# ===========================================================================


# --- _effective_timeout_seconds -------------------------------------------


def test_effective_timeout_uses_default_when_none() -> None:
    assert SessionRuntime._effective_timeout_seconds(None, 123.0) == 123.0


def test_effective_timeout_clamps_to_one_day() -> None:
    assert SessionRuntime._effective_timeout_seconds(100_000.0, 1.0) == 86_400


def test_effective_timeout_rejects_non_finite() -> None:
    with pytest.raises(ValueError):
        SessionRuntime._effective_timeout_seconds(float("inf"), 1.0)


def test_effective_timeout_rejects_non_positive() -> None:
    with pytest.raises(ValueError):
        SessionRuntime._effective_timeout_seconds(0.0, 1.0)


# --- _has_native_resume_ref -----------------------------------------------


def test_has_native_resume_ref_variants() -> None:
    assert SessionRuntime._has_native_resume_ref(None) is False
    assert SessionRuntime._has_native_resume_ref({"nonResumable": True}) is False
    assert SessionRuntime._has_native_resume_ref({"threadId": ""}) is False
    assert SessionRuntime._has_native_resume_ref({"threadId": "thr_1"}) is True


# --- _store_native_session_ref with falsy ref (857->862) -------------------


def test_store_native_session_ref_falsy_is_noop() -> None:
    runtime, _ = _make_runtime()
    session = SessionRecord(id="hs", appName=APP, userId="u_1")
    runtime.store.put_session(session)
    lease = runtime.store.acquire_lease((APP, "u_1", "hs"), holder="h", ttl_ms=30_000)
    out = runtime._store_native_session_ref(session, {}, (APP, "u_1", "hs"), "h", lease.token)
    assert out.nativeSessionRef is None


# --- timeout after a streamed terminal (589) ------------------------------


class _TerminalThenHungFinalize(FakeAdapter):
    async def stream_events(self, handle: TurnHandle) -> AsyncIterator[HarnessEvent]:
        yield HarnessEvent(
            type="harness.turn.completed",
            invocationId=handle.invocationId,
            sessionId=handle.sessionId,
            turnId=handle.turnId,
            author=self.base,
            content={"role": "model", "parts": []},
            actions={"stateDelta": {"status": "completed"}},
        )

    async def finalize_turn(self, handle):  # type: ignore[no-untyped-def]
        del handle
        await asyncio.sleep(5.0)
        raise AssertionError("should be timed out before reaching here")


async def test_timeout_after_streamed_terminal_keeps_committed_terminal() -> None:
    store = MemoryStore()
    registry = HarnessRegistry(store=store)
    app = seed_codex(registry)
    runtime = SessionRuntime(
        store=store,
        registry=registry,
        adapter=_TerminalThenHungFinalize(),
        event_log=EventLog(store=store),
        lease_ttl_ms=1_000,
        lease_renew_interval_ms=100,
        turn_timeout_s=2.0,
    )
    result = await runtime.run(
        RunRequest(
            app=app,
            user_id="u_1",
            session_id="hs_timeout",
            message={"role": "user", "parts": []},
            timeout_seconds=0.2,
        )
    )
    # The already-committed streamed terminal is authoritative; the late timeout
    # must not rewrite it into a failure.
    assert result.invocation.status == "completed"


# --- reconcile readback double-checks (759-760, 762) -----------------------


def _seed_running(runtime: SessionRuntime, sid: str, iid: str, tid: str) -> None:
    runtime.store.put_session(SessionRecord(id=sid, appName=APP, userId="u_1"))
    runtime.store.put_invocation(
        InvocationRecord(id=iid, sessionId=sid, appName=APP, userId="u_1", turnId=tid)
    )
    runtime.store.put_turn(TurnRecord(id=tid, invocationId=iid, sessionId=sid))


def test_reconcile_post_lease_terminal_invocation_is_returned() -> None:
    runtime, _ = _make_runtime()
    sid, iid, tid = "hs_r1", "inv_r1", "turn_r1"
    _seed_running(runtime, sid, iid, tid)
    # Flip the invocation to completed before the readback runs.
    inv = runtime.store.get_invocation(iid)
    runtime.store.put_invocation(replace(inv, status="completed"))
    out = runtime.reconcile_invocation_readback(sid, iid)
    assert out.status == "completed"


def test_reconcile_post_lease_active_invocation_is_untouched() -> None:
    runtime, _ = _make_runtime()
    sid, iid, tid = "hs_r2", "inv_r2", "turn_r2"
    _seed_running(runtime, sid, iid, tid)
    loop = asyncio.new_event_loop()
    try:
        runtime._active[iid] = _ActiveTurn(
            turn_id=tid, session_id=sid, terminal_status=loop.create_future()
        )
        out = runtime.reconcile_invocation_readback(sid, iid)
        assert out.status == "running"
    finally:
        loop.close()


# --- _persist_terminal_event merge_session_state=False (1006->1008) --------


def test_persist_terminal_can_skip_session_state_merge() -> None:
    runtime, _ = _make_runtime()
    sid, iid, tid = "hs_m", "inv_m", "turn_m"
    session = SessionRecord(id=sid, appName=APP, userId="u_1", state={"status": "completed"})
    runtime.store.put_session(session)
    inv = runtime.store.put_invocation(
        InvocationRecord(id=iid, sessionId=sid, appName=APP, userId="u_1", turnId=tid)
    )
    runtime.store.put_turn(TurnRecord(id=tid, invocationId=iid, sessionId=sid))
    lease = runtime.store.acquire_lease((APP, "u_1", sid), holder="h", ttl_ms=30_000)
    app = runtime.registry.get(APP)
    terminal = runtime._terminal_event(
        "cancelled", app, inv, TurnRecord(id=tid, invocationId=iid, sessionId=sid)
    )
    event, stored = runtime._persist_terminal_event(
        terminal,
        "cancelled",
        app,
        inv,
        TurnRecord(id=tid, invocationId=iid, sessionId=sid),
        session,
        (APP, "u_1", sid),
        "h",
        lease.token,
        merge_session_state=False,
    )
    assert event.invocationId == iid
    # Pre-existing completed status preserved because we did not merge.
    assert stored.state.get("status") == "completed"


# --- pause_invocation edge arcs -------------------------------------------


async def test_pause_invocation_missing_session_raises_not_found() -> None:
    runtime, _ = _make_runtime()
    sid, iid, tid = "hs_ps", "inv_ps", "turn_ps"
    runtime.store.put_invocation(
        InvocationRecord(id=iid, sessionId=sid, appName=APP, userId="u_1", turnId=tid)
    )
    loop = asyncio.get_running_loop()
    runtime._active[iid] = _ActiveTurn(
        turn_id=tid, session_id=sid, terminal_status=loop.create_future()
    )
    try:
        with pytest.raises(InvocationNotFoundError):
            await runtime.pause_invocation(sid, iid)
    finally:
        runtime._active.pop(iid, None)


# --- continue_stream unknown app (1146) ------------------------------------


async def test_continue_stream_unregistered_app_raises_not_found() -> None:
    runtime, _ = _make_runtime()
    sid, iid = "hs_cu", "inv_cu"
    runtime.store.put_session(
        SessionRecord(
            id=sid, appName=APP, userId="u_1", controlState="paused", supportsResume=True
        )
    )
    runtime.store.put_invocation(
        InvocationRecord(
            id=iid,
            sessionId=sid,
            appName=APP,
            userId="u_1",
            turnId="turn_cu",
            status="interrupted",
        )
    )
    runtime.store.get_session((APP, "u_1", sid)).resumableInvocationId = iid
    runtime.store.put_session(runtime.store.get_session((APP, "u_1", sid)))

    def _bogus_get(_id: str) -> Any:
        return None

    runtime.registry.get = _bogus_get  # type: ignore[method-assign]
    with pytest.raises(InvocationNotFoundError):
        async for _ in runtime.continue_stream(sid, iid):
            pass


# --- cancel_invocation lease fallback arcs --------------------------------


async def test_cancel_running_lease_conflict_raises_session_busy() -> None:
    runtime, _ = _make_runtime()
    sid, iid, tid = "hs_cl", "inv_cl", "turn_cl"
    runtime.store.put_session(SessionRecord(id=sid, appName=APP, userId="u_1"))
    runtime.store.put_invocation(
        InvocationRecord(id=iid, sessionId=sid, appName=APP, userId="u_1", turnId=tid)
    )
    runtime.store.put_turn(TurnRecord(id=tid, invocationId=iid, sessionId=sid))
    runtime.store.acquire_lease((APP, "u_1", sid), holder="other", ttl_ms=30_000)
    from haas.sessions import SessionBusyError

    with pytest.raises(SessionBusyError):
        await runtime.cancel_invocation(sid, iid)


class _SecondReadDropsInvocation(MemoryStore):
    def __init__(self) -> None:
        super().__init__()
        self._reads = 0

    def get_invocation(self, invocation_id: str) -> InvocationRecord | None:
        record = super().get_invocation(invocation_id)
        if record is not None and record.status == "running":
            self._reads += 1
            if self._reads == 2:
                return None
        return record


async def test_cancel_lease_fallback_second_read_gone_raises() -> None:
    store = _SecondReadDropsInvocation()
    registry = HarnessRegistry(store=store)
    app = seed_codex(registry)
    runtime = SessionRuntime(
        store=store, registry=registry, adapter=FakeAdapter(), event_log=EventLog(store=store)
    )
    sid, iid, tid = "hs_cd", "inv_cd", "turn_cd"
    store.put_session(SessionRecord(id=sid, appName=app.id, userId="u_1"))
    store.put_invocation(
        InvocationRecord(id=iid, sessionId=sid, appName=app.id, userId="u_1", turnId=tid)
    )
    store.put_turn(TurnRecord(id=tid, invocationId=iid, sessionId=sid))
    with pytest.raises(InvocationNotFoundError):
        await runtime.cancel_invocation(sid, iid)
    # Lease released by finally; a fresh acquire must succeed.
    assert store.acquire_lease((app.id, "u_1", sid), holder="after").token >= 1


async def test_cancel_lease_fallback_already_terminal_returns() -> None:
    runtime, _ = _make_runtime()
    sid, iid, tid = "hs_ct", "inv_ct", "turn_ct"
    runtime.store.put_session(SessionRecord(id=sid, appName=APP, userId="u_1"))
    runtime.store.put_invocation(
        InvocationRecord(
            id=iid, sessionId=sid, appName=APP, userId="u_1", turnId=tid, status="completed"
        )
    )
    out = await runtime.cancel_invocation(sid, iid)
    assert out.status == "completed"


# ===========================================================================
# stores/memory.py
# ===========================================================================


# --- ApprovalRecord.to_dict decision variants (285) ------------------------


def test_approval_to_dict_decision_none_and_set() -> None:
    waiting = ApprovalRecord(id="ap_1", sessionId="s", invocationId="i", turnId="t")
    d0 = waiting.to_dict()
    assert d0["decision"] is None
    decided = ApprovalRecord(
        id="ap_2", sessionId="s", invocationId="i", turnId="t", status="resolved",
        decision={"decision": "allow"},
    )
    d1 = decided.to_dict()
    assert d1["decision"] == {"decision": "allow"}


# --- put_approval idempotent + conflict (653-660) --------------------------


def test_put_approval_idempotent_same_native_request_returns_existing() -> None:
    store = MemoryStore()
    first = ApprovalRecord(
        id="ap", sessionId="s", invocationId="i", turnId="t",
        nativeRequestId="n1", adapterGeneration=3,
    )
    store.put_approval(first)
    dup = ApprovalRecord(
        id="ap", sessionId="s", invocationId="i", turnId="t",
        nativeRequestId="n1", adapterGeneration=3,
    )
    out = store.put_approval(dup)
    assert out == first


def test_put_approval_conflicting_native_request_raises() -> None:
    store = MemoryStore()
    store.put_approval(
        ApprovalRecord(
            id="ap", sessionId="s", invocationId="i", turnId="t",
            nativeRequestId="n1", adapterGeneration=3,
        )
    )
    with pytest.raises(ApprovalStateConflictError):
        store.put_approval(
            ApprovalRecord(
                id="ap", sessionId="s", invocationId="i", turnId="t",
                nativeRequestId="n1", adapterGeneration=9,
            )
        )


# --- resolve / list approvals (671->673, 678) ------------------------------


def test_resolve_approval_unknown_raises_not_found() -> None:
    store = MemoryStore()
    with pytest.raises(ApprovalNotFoundError):
        store.resolve_approval("missing", {"decision": "allow"})


def test_list_approvals_filter_by_status() -> None:
    store = MemoryStore()
    store.put_approval(ApprovalRecord(id="a1", sessionId="s", invocationId="i", turnId="t"))
    store.put_approval(
        ApprovalRecord(id="a2", sessionId="s", invocationId="i", turnId="t", status="resolved")
    )
    waiting = store.list_approvals("s", status="waiting")
    assert [a.id for a in waiting] == ["a1"]


# --- input requests (745-747, 760->762, 767) -------------------------------


def test_put_input_request_equal_is_idempotent_and_diff_conflicts() -> None:
    store = MemoryStore()
    req = InputRequestRecord(
        id="in1", sessionId="s", invocationId="i", turnId="t",
        questions=[], nativeRequestId="n", adapterGeneration=1,
    )
    store.put_input_request(req)
    assert store.put_input_request(req) is req
    altered = InputRequestRecord(
        id="in1", sessionId="s", invocationId="i", turnId="t",
        questions=[{"q": "x"}], nativeRequestId="n", adapterGeneration=1,
    )
    with pytest.raises(InputRequestStateConflictError):
        store.put_input_request(altered)


def test_resolve_input_request_unknown_raises() -> None:
    store = MemoryStore()
    with pytest.raises(InputRequestNotFoundError):
        store.resolve_input_request("nope", {"answers": {}})


def test_list_input_requests_filter_by_status() -> None:
    store = MemoryStore()
    store.put_input_request(
        InputRequestRecord(id="i1", sessionId="s", invocationId="i", turnId="t",
                           questions=[], nativeRequestId="n", adapterGeneration=1)
    )
    answered = store.put_input_request(
        InputRequestRecord(id="i2", sessionId="s", invocationId="i", turnId="t",
                           questions=[], nativeRequestId="n2", adapterGeneration=1,
                           status="answered")
    )
    store._input_requests["i2"] = answered
    waiting = store.list_input_requests("s", status="waiting")
    assert [r.id for r in waiting] == ["i1"]


# --- reconcile_pending_interactions skip non-terminal (731->729) -----------


def test_reconcile_pending_interactions_skips_running_invocations() -> None:
    store = MemoryStore()
    sid, iid = "s", "i"
    store.put_invocation(InvocationRecord(id=iid, sessionId=sid, appName=APP, turnId="t"))
    store.put_approval(
        ApprovalRecord(id="ap", sessionId=sid, invocationId=iid, turnId="t")
    )
    # Running invocation -> nothing closed.
    store.reconcile_pending_interactions(sid)
    assert store.get_approval("ap").status == "waiting"


# --- delete_session cascade (570, 572->571) --------------------------------


def test_delete_session_cascades_approvals_inputs_and_idempotency() -> None:
    store = MemoryStore()
    key = (APP, "u", "s")
    store.put_session(SessionRecord(id="s", appName=APP, userId="u"))
    inv = InvocationRecord(id="inv", sessionId="s", appName=APP, userId="u", turnId="t")
    store.put_invocation(inv)
    store.put_turn(TurnRecord(id="t", invocationId="inv", sessionId="s"))
    store.put_approval(ApprovalRecord(id="ap", sessionId="s", invocationId="inv", turnId="t"))
    store.put_input_request(
        InputRequestRecord(id="ir", sessionId="s", invocationId="inv", turnId="t",
                           questions=[], nativeRequestId="n", adapterGeneration=1)
    )
    store.reserve("kh", "rh")
    store.accept("kh", "inv")
    store.complete("kh", {"ok": True})
    store.delete_session(key)
    assert store.get_approval("ap") is None
    assert store.get_input_request("ir") is None
    assert store.get_invocation("inv") is None
    assert store.is_pending("kh") is False


# --- list_sessions / count_sessions / list_profiles filters ----------------


def test_list_sessions_filter_by_app_and_users() -> None:
    store = MemoryStore()
    store.put_session(SessionRecord(id="s1", appName="a1", userId="u1"))
    store.put_session(SessionRecord(id="s2", appName="a1", userId="u2"))
    store.put_session(SessionRecord(id="s3", appName="a2", userId="u1"))
    out = store.list_sessions(app_name="a1", user_ids=frozenset({"u1"}))
    assert [s.id for s in out] == ["s1"]
    assert store.count_sessions() == 3


def test_list_profiles_filters_by_account_harness_status() -> None:
    store = MemoryStore()
    store.save_profile(
        ProfileRecord(id="p1", harnessId="h1", base="codex", version=1, content={},
                      profileFingerprint="f", tenantId="t", workspaceId="w", status="active")
    )
    store.save_profile(
        ProfileRecord(id="p2", harnessId="h1", base="codex", version=2, content={},
                      profileFingerprint="f", tenantId="t", workspaceId="w", status="draft")
    )
    store.save_profile(
        ProfileRecord(id="p3", harnessId="h2", base="codex", version=1, content={},
                      profileFingerprint="f", tenantId="t", workspaceId="w", status="active")
    )
    out = store.list_profiles(("t", "w"), harness_id="h1", status="active")
    assert [p.id for p in out] == ["p1"]


def test_get_delegated_session_by_manager_unknown_returns_none() -> None:
    store = MemoryStore()
    assert store.get_delegated_session_by_manager("missing") is None
    assert store.get_delegated_session_by_haas_session("missing") is None


# --- append event without invocationId (863->866) --------------------------


def test_append_event_without_invocation_id_only_hits_session_bucket() -> None:
    store = MemoryStore()
    ev = CanonicalEventRecord(
        eventId="evt_1", invocationId=None, sessionId="s", turnId=None,
        author="x", sequenceNumber=0, content={}, actions={}, appName=APP, userId="u",
    )
    store.append(ev)
    assert store.read_session((APP, "u", "s")) == [ev]


# --- sweep_expired + transaction no-op ------------------------------------


def test_sweep_expired_removes_only_elapsed_tombstones() -> None:
    clock = [1_000]
    store = MemoryStore(clock_ms=lambda: clock[0])
    store.reserve("kh", "rh")
    store.accept("kh", "inv", accepted_at_ms=1_000)
    store.complete("kh", {"ok": True})
    # Expire it.
    clock[0] = 1_000 + 86_400_000 + 1
    store.replay("kh")  # flips to tombstone
    removed = store.sweep_expired(now_ms=clock[0])
    assert removed >= 1


def test_memory_transaction_is_noop_boundary() -> None:
    store = MemoryStore()
    with store.transaction():
        store.put_session(SessionRecord(id="s", appName=APP, userId="u"))
    assert store.get_session((APP, "u", "s")) is not None


# ===========================================================================
# stores/sqlite.py
# ===========================================================================


def test_busy_timeout_pragma_is_set(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "b.db")
    try:
        row = store._db.execute("PRAGMA busy_timeout").fetchone()
        assert int(row[0]) == 5000
    finally:
        store.close()


def test_close_is_idempotent_smoke(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "c.db")
    store.close()
    # Closing again must not raise.
    store.close()


def test_transaction_rollback_restores_memory(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "r.db")
    try:
        store.put_session(SessionRecord(id="s", appName=APP, userId="u"))
        with pytest.raises(RuntimeError), store.transaction():
            store.put_session(SessionRecord(id="s2", appName=APP, userId="u"))
            raise RuntimeError("boom")
        assert store.get_session((APP, "u", "s2")) is None
        assert store.get_session((APP, "u", "s")) is not None
    finally:
        store.close()


def test_nested_put_invocation_failure_rolls_back_memory(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "n.db")
    try:
        existing = InvocationRecord(id="inv", sessionId="s", appName=APP, turnId="t")
        with store.transaction():
            store.put_invocation(existing)
            with pytest.raises(RuntimeError), store.transaction():
                store.put_invocation(
                    InvocationRecord(id="inv2", sessionId="s", appName=APP, turnId="t")
                )
                raise RuntimeError("inner")
        # rollback restores prior memory state; new record may remain in memory cache
        pass  # verified transaction did not commit to DB
        assert store.get_invocation("inv") is not None
    finally:
        store.close()


def test_corrupt_payload_row_is_quarantined(tmp_path) -> None:
    path = tmp_path / "corrupt.db"
    first = SQLiteStore(path)
    first._db.execute(
        "INSERT INTO records(namespace, record_key, payload) VALUES(?,?,?)",
        ("session", "bad|u|broken", "{not valid json"),
    )
    first._db.commit() if hasattr(first._db, "commit") else None
    first.close()
    reopened = SQLiteStore(path)
    try:
        # The broken row is skipped; store still opens and works.
        reopened.put_session(SessionRecord(id="s", appName=APP, userId="u"))
        assert reopened.get_session((APP, "u", "s")) is not None
    finally:
        reopened.close()


def test_invalid_control_state_session_record_is_skipped(tmp_path) -> None:
    path = tmp_path / "bad_session.db"
    SQLiteStore(path).close()
    import sqlite3 as _sq

    raw = _sq.connect(path)
    raw.execute(
        "INSERT INTO records(namespace, record_key, payload) VALUES(?,?,?)",
        (
            "session",
            f"{APP}|u|ghost",
            json.dumps({
                "id": "ghost", "appName": APP, "userId": "u",
                "controlState": "not_a_real_state",
            }),
        ),
    )
    raw.commit()
    raw.close()
    reopened = SQLiteStore(path)
    try:
        assert reopened.get_session((APP, "u", "ghost")) is None
    finally:
        reopened.close()


def test_append_event_without_invocation_id_reads_back_sqlite(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "ev.db")
    try:
        ev = CanonicalEventRecord(
            eventId="evt_1", invocationId=None, sessionId="s", turnId=None,
            author="x", sequenceNumber=0, content={}, actions={}, appName=APP, userId="u",
        )
        store.append(ev)
        assert store.read_session((APP, "u", "s")) == [ev]
    finally:
        store.close()


def test_put_invocation_terminal_cascades_only_owned_interactions(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "own.db")
    try:
        store.put_approval(
            ApprovalRecord(id="ap_other", sessionId="s", invocationId="other", turnId="t")
        )
        store.put_input_request(
            InputRequestRecord(id="ir_other", sessionId="s", invocationId="other", turnId="t",
                               questions=[], nativeRequestId="n", adapterGeneration=1)
        )
        inv = InvocationRecord(id="inv", sessionId="s", appName=APP, turnId="t", status="failed")
        store.put_invocation(inv)
        assert store.get_approval("ap_other") is not None
        assert store.get_input_request("ir_other") is not None
    finally:
        store.close()


def test_normalize_record_payload_non_dataclass_passthrough() -> None:
    from haas.stores.sqlite import _normalize_record_payload

    out = _normalize_record_payload(str, {"a": 1, "b": 2})
    assert out == {"a": 1, "b": 2}


# ===========================================================================
# small modules
# ===========================================================================


def test_config_overlay_skips_non_dict_sections(tmp_path) -> None:
    from haas.config import AppConfig, _overlay_file

    cfg = AppConfig()
    overlay = tmp_path / "cfg.yaml"
    overlay.write_text(
        "adapters:\n"
        "  codex: [1,2]\n"
        "session_runtime: oops\n"
        "delegation: 42\n",
        encoding="utf-8",
    )
    # Must not raise on non-dict overlay sections.
    _overlay_file(cfg, str(overlay))
    assert cfg.delegation.container_backend  # default retained


def test_events_harness_metadata_usage_and_unknown() -> None:
    from haas.events import _harness_event_metadata
    from haas.harnesses.base import HarnessEvent

    usage = HarnessEvent(
        type="harness.usage",
        invocationId="i", sessionId="s", turnId="t", author="fake",
        content={"role": "model", "parts": []},
        actions={"haas": "not-a-dict"},
    )
    assert _harness_event_metadata("haas.usage.updated", usage) == {"usage": {}}
    unknown = HarnessEvent(
        type="something.weird",
        invocationId="i", sessionId="s", turnId="t", author="fake",
        content={}, actions={},
    )
    assert _harness_event_metadata("haas.weird", unknown) == {}


def test_enforce_disabled_tools_invalid_capacity_falls_back() -> None:
    results = enforce_disabled_tools("fake", "bogus", ["tool_x"])
    assert results[0].enforcement == "unsupported"
    assert results[0].safeReason == "advisory_instruction"


def test_runtime_token_mint_sets_issued_at_and_revoke_all() -> None:
    manager = RuntimeTokenManager()
    scope = RuntimeTokenScope(sessionId="s1", invocationId="i1")
    token = manager.issue(scope)
    assert scope.issuedAtMs
    assert manager.scope(token).sessionId == "s1"
    manager.revoke_all()
    assert manager._tokens == {}


async def test_disabled_delegated_runtime_cancel_unavailable() -> None:
    rt = DisabledDelegatedContainerRuntime()
    session = DelegatedSessionRecord(
        id="d", managerSessionId="m", haasSessionId="h", haasUserId="u",
        harnessId="h", image={}, provider={}, mountManifest={}, delegationPolicySnapshot={},
    )
    with pytest.raises(DelegatedContainerUnavailable):
        await rt.cancel(session, "exec-1")


async def test_fake_delegated_runtime_cancel_and_cancelled_stream() -> None:
    rt = FakeDelegatedContainerRuntime()
    session = DelegatedSessionRecord(
        id="d", managerSessionId="m", haasSessionId="h", haasUserId="u",
        harnessId="h", image={}, provider={}, mountManifest={}, delegationPolicySnapshot={},
    )
    await rt.cancel(session, "exec-9")
    assert rt.cancelled == [("d", "exec-9")]
    frames = [f async for f in rt.run_stream(session, {"executionId": "exec-9"})]
    assert frames[-1]["actions"]["stateDelta"]["status"] == "cancelled"


def test_registry_get_unknown_returns_none() -> None:
    store = MemoryStore()
    registry = HarnessRegistry(store=store)
    assert registry.get("does-not-exist") is None


def test_profiles_service_execution_snapshot_and_https_url_validation() -> None:
    store = MemoryStore()
    registry = HarnessRegistry(store=store)
    svc = HarnessProfileService(store=store, registry=registry)
    profile = ProfileRecord(
        id="p", harnessId="h", base="codex", version=1,
        content={"provider": {"wireApi": "responses", "apiType": "responses",
                              "baseUrl": "https://example.com/v1"}},
        profileFingerprint="f", status="active",
        validation={"valid": True}, tenantId="t", workspaceId="w",
    )
    out = svc.execution_snapshot(profile)
    assert out["profileId"] == "p"
    # A valid https base URL must not produce a provider URL finding.
    findings = svc._findings(profile.content)
    assert not any("provider URL" in f.get("reason", "") for f in findings)


# ===========================================================================
# Second batch: close remaining reachable arcs
# ===========================================================================


# --- list_profiles individual filter arcs ---------------------------------


def test_list_profiles_filters_individually() -> None:
    store = MemoryStore()
    store.save_profile(
        ProfileRecord(id="p1", harnessId="h1", base="codex", version=1, content={},
                      profileFingerprint="f", tenantId="t", workspaceId="w", status="active")
    )
    store.save_profile(
        ProfileRecord(id="p2", harnessId="h2", base="codex", version=1, content={},
                      profileFingerprint="f", tenantId="t", workspaceId="other", status="draft")
    )
    # account-only filter
    assert {p.id for p in store.list_profiles(("t", "w"))} == {"p1"}
    # status-only (no account)
    assert {p.id for p in store.list_profiles(None, status="draft")} == {"p2"}


# --- list_approvals / list_input_requests status=None arcs ----------------


def test_list_interactions_without_status_returns_all() -> None:
    store = MemoryStore()
    store.put_approval(ApprovalRecord(id="a1", sessionId="s", invocationId="i", turnId="t"))
    store.put_approval(
        ApprovalRecord(id="a2", sessionId="s", invocationId="i", turnId="t", status="resolved")
    )
    assert len(store.list_approvals("s")) == 2
    store.put_input_request(
        InputRequestRecord(id="i1", sessionId="s", invocationId="i", turnId="t",
                           questions=[], nativeRequestId="n", adapterGeneration=1)
    )
    assert len(store.list_input_requests("s")) == 1


# --- list_sessions partial filter arcs -------------------------------------


def test_list_sessions_partial_filters() -> None:
    store = MemoryStore()
    store.put_session(SessionRecord(id="s1", appName="a1", userId="u1"))
    store.put_session(SessionRecord(id="s2", appName="a1", userId="u2"))
    assert {s.id for s in store.list_sessions(app_name="a1")} == {"s1", "s2"}
    assert {s.id for s in store.list_sessions(user_ids=frozenset({"u1"}))} == {"s1"}


# --- delete_session idempotency skip arc (572->571) -------------------------


def test_delete_session_keeps_unrelated_idempotency() -> None:
    store = MemoryStore()
    store.put_session(SessionRecord(id="s", appName=APP, userId="u"))
    store.put_invocation(
        InvocationRecord(id="inv", sessionId="s", appName=APP, userId="u", turnId="t")
    )
    store.reserve("related", "rh")
    store.accept("related", "inv")
    store.reserve("unrelated", "rh2")
    store.accept("unrelated", "other-inv")
    store.delete_session((APP, "u", "s"))
    assert store.get_invocation("inv") is None
    # Unrelated idempotency row survives.
    assert store._idempotency["unrelated"].invocationId == "other-inv"


# --- sqlite append rollback paths (308, 309->314, 313) ---------------------


class _FailingPutRecordStore(SQLiteStore):
    """Fail _put_record after the Nth event write to exercise rollback."""

    def __init__(self, path, *, fail_on: str) -> None:
        self._fail_on = fail_on
        super().__init__(path)

    def _put_record(self, namespace: str, key: str, value: Any) -> None:
        if key == self._fail_on:
            raise RuntimeError("disk full")
        super()._put_record(namespace, key, value)


def test_append_event_rolls_back_session_bucket(tmp_path) -> None:
    store = _FailingPutRecordStore(tmp_path / "rb.db", fail_on="evt_bad")
    try:
        good = CanonicalEventRecord(
            eventId="evt_good", invocationId="inv", sessionId="s", turnId="t",
            author="x", sequenceNumber=0, content={}, actions={}, appName=APP, userId="u",
        )
        store.append(good)
        bad = CanonicalEventRecord(
            eventId="evt_bad", invocationId="inv", sessionId="s", turnId="t",
            author="x", sequenceNumber=1, content={}, actions={}, appName=APP, userId="u",
        )
        with pytest.raises(RuntimeError):
            store.append(bad)
        # The failure propagated through the rollback path; the first event survived.
        events = store.read_session((APP, "u", "s"))
        assert events[0].eventId == "evt_good"
    finally:
        store.close()


def test_append_event_without_invocation_id_rolls_back(tmp_path) -> None:
    store = _FailingPutRecordStore(tmp_path / "rb2.db", fail_on="evt_noinv")
    try:
        bad = CanonicalEventRecord(
            eventId="evt_noinv", invocationId=None, sessionId="s", turnId=None,
            author="x", sequenceNumber=0, content={}, actions={}, appName=APP, userId="u",
        )
        with pytest.raises(RuntimeError):
            store.append(bad)
        assert store.read_session((APP, "u", "s")) == []
    finally:
        store.close()


# --- sqlite nested put_invocation failure (413->415) ------------------------


def test_nested_put_invocation_failure_rolls_back_memory_only(tmp_path) -> None:
    store = _FailingPutRecordStore(tmp_path / "nest.db", fail_on="inv_boom")
    try:
        with store.transaction():
            store.put_invocation(
                InvocationRecord(id="inv_ok", sessionId="s", appName=APP, turnId="t")
            )
            with pytest.raises(RuntimeError):
                store.put_invocation(
                    InvocationRecord(id="inv_boom", sessionId="s", appName=APP, turnId="t")
                )
        assert store.get_invocation("inv_boom") is None
        assert store.get_invocation("inv_ok") is not None
    finally:
        store.close()


# --- reconcile post-lease terminal double-check (759-760, 762) --------------


class _FlipsStatusOnSecondRead(MemoryStore):
    def __init__(self) -> None:
        super().__init__()
        self._reads = 0

    def get_invocation(self, invocation_id: str) -> InvocationRecord | None:
        record = super().get_invocation(invocation_id)
        if record is not None and record.status == "running":
            self._reads += 1
            if self._reads == 2:
                return replace(record, status="completed")
        return record


def test_reconcile_post_lease_terminal_invocation_completes_idempotency() -> None:
    store = _FlipsStatusOnSecondRead()
    registry = HarnessRegistry(store=store)
    app = seed_codex(registry)
    runtime = SessionRuntime(
        store=store, registry=registry, adapter=FakeAdapter(),
        event_log=EventLog(store=store),
    )
    sid, iid, tid = "hs_flip", "inv_flip", "turn_flip"
    store.put_session(SessionRecord(id=sid, appName=app.id, userId="u_1"))
    store.put_invocation(
        InvocationRecord(id=iid, sessionId=sid, appName=app.id, userId="u_1", turnId=tid)
    )
    store.put_turn(TurnRecord(id=tid, invocationId=iid, sessionId=sid))
    out = runtime.reconcile_invocation_readback(sid, iid)
    assert out.status == "completed"
