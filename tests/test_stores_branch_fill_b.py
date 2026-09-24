"""Branch-fill unit tests for SQLiteStore and MemoryStore edge paths.

Offline only. Covers:
  * SQLite PRAGMA busy_timeout assertion
  * transaction() nesting depth (inner writers must not re-issue BEGIN)
  * write-failure rollback across put_session / put_invocation / append
  * sweep_expired tombstone eviction boundary
  * Memory put_approval idempotent replay vs conflict
  * Memory delete_session cascade to events_by_session / events_by_invocation
  * reconcile_pending_interactions closing waiting interactions
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from haas.stores.memory import (
    ApprovalRecord,
    CanonicalEventRecord,
    InputRequestRecord,
    InvocationRecord,
    MemoryStore,
    SessionRecord,
    TurnRecord,
)
from haas.stores.sqlite import SQLiteStore


# --- helpers ---------------------------------------------------------------


def _session(sid: str = "hsess_1", app: str = "chrn_1", user: str = "u_1") -> SessionRecord:
    return SessionRecord(id=sid, appName=app, userId=user)


def _invocation(iid: str, sid: str = "hsess_1", status: str = "running") -> InvocationRecord:
    return InvocationRecord(
        id=iid, sessionId=sid, appName="chrn_1", turnId="turn_1", userId="u_1",
        status=status,
    )


def _event(eid: str, sid: str, iid: str | None, seq: int) -> CanonicalEventRecord:
    return CanonicalEventRecord(
        eventId=eid,
        invocationId=iid,
        sessionId=sid,
        turnId="turn_1",
        author="codex",
        sequenceNumber=seq,
        content={"role": "model", "parts": []},
        actions={},
        appName="chrn_1",
        userId="u_1",
    )


# --- SQLite PRAGMA / transaction nesting ----------------------------------


def test_sqlite_busy_timeout_pragma_is_set(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "haas.db")
    row = store._db.execute("PRAGMA busy_timeout").fetchone()
    assert row[0] == 5000


def test_sqlite_transaction_nesting_does_not_double_begin(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "haas.db")
    assert store._txn_depth == 0

    with store.transaction():
        assert store._txn_depth == 1
        with store.transaction():
            assert store._txn_depth == 2
            # Inner nested write must not issue its own BEGIN/COMMIT.
            store.put_session(_session("hsess_nested"))
        assert store._txn_depth == 1
    assert store._txn_depth == 0

    # Committed across the outer txn.
    assert store.get_session(("chrn_1", "u_1", "hsess_nested")) is not None


def test_sqlite_transaction_rollback_restores_memory(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "haas.db")
    store.put_session(_session("hsess_keep"))

    with pytest.raises(RuntimeError):
        with store.transaction():
            store.put_session(_session("hsess_rollback"))
            raise RuntimeError("boom")

    assert store.get_session(("chrn_1", "u_1", "hsess_rollback")) is None
    assert store.get_session(("chrn_1", "u_1", "hsess_keep")) is not None


# --- SQLite write-failure rollback -----------------------------------------


def test_sqlite_put_session_durable_failure_rolls_back_memory(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "haas.db")

    real = store._put_record

    def _boom(namespace: str, key: str, value: Any) -> None:
        if namespace == "session":
            raise RuntimeError("disk on fire")
        return real(namespace, key, value)

    store._put_record = _boom  # type: ignore[assignment]

    with pytest.raises(RuntimeError, match="disk on fire"):
        store.put_session(_session("hsess_lost"))

    # In-memory map must NOT contain the half-written record.
    assert ("chrn_1", "u_1", "hsess_lost") not in store._sessions


def test_sqlite_put_invocation_durable_failure_rolls_back_memory(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "haas.db")
    real = store._put_record

    calls = {"n": 0}

    def _boom(namespace: str, key: str, value: Any) -> None:
        calls["n"] += 1
        if namespace == "invocation":
            raise RuntimeError("write failed")
        return real(namespace, key, value)

    store._put_record = _boom  # type: ignore[assignment]

    with pytest.raises(RuntimeError, match="write failed"):
        store.put_invocation(_invocation("inv_lost"))

    assert "inv_lost" not in store._invocations


def test_sqlite_append_event_durable_failure_rolls_back_event_index(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "haas.db")
    real = store._put_record

    def _boom(namespace: str, key: str, value: Any) -> None:
        if namespace == "event":
            raise RuntimeError("event write failed")
        return real(namespace, key, value)

    store._put_record = _boom  # type: ignore[assignment]

    evt = _event("evt_1", "hsess_1", "inv_1", 1)
    with pytest.raises(RuntimeError, match="event write failed"):
        store.append(evt)

    key = ("chrn_1", "u_1", "hsess_1")
    assert key not in store._events_by_session
    assert ("chrn_1", "u_1", "hsess_1", "inv_1") not in store._events_by_invocation


# --- SQLite sweep_expired --------------------------------------------------


def test_sqlite_sweep_expired_removes_tombstones(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "haas.db")
    # Seed a tombstoned idempotency row directly into both memory and DB.
    from haas.stores.memory import IdempotencyRecord

    past = 1_000
    rec = IdempotencyRecord(
        keyHash="kh_expired",
        requestHash="rh",
        result=None,
        released=True,
        accepted=True,
        invocationId="inv_1",
        acceptedAtMs=0,
        completedAtMs=past,
        expiresAtMs=past,
        tombstone=True,
    )
    store._idempotency["kh_expired"] = rec
    removed = store.sweep_expired(now_ms=2_000)
    assert removed == 1
    assert "kh_expired" not in store._idempotency


# --- Memory put_approval replay / conflict ---------------------------------


def test_memory_put_approval_idempotent_replay_returns_existing() -> None:
    store = MemoryStore()
    rec = ApprovalRecord(
        id="appr_1",
        sessionId="hsess_1",
        invocationId="inv_1",
        turnId="turn_1",
        nativeRequestId=5,
        adapterGeneration=1,
    )
    first = store.put_approval(rec)
    # Replay with identical nativeRequestId / generation / invocationId.
    second = store.put_approval(rec)
    assert first is second


def test_memory_put_approval_conflicting_native_raises() -> None:
    from haas.stores.memory import ApprovalStateConflictError

    store = MemoryStore()
    store.put_approval(
        ApprovalRecord(
            id="appr_2",
            sessionId="hsess_1",
            invocationId="inv_1",
            turnId="turn_1",
            nativeRequestId=5,
            adapterGeneration=1,
        )
    )
    with pytest.raises(ApprovalStateConflictError):
        store.put_approval(
            ApprovalRecord(
                id="appr_2",
                sessionId="hsess_1",
                invocationId="inv_1",
                turnId="turn_1",
                nativeRequestId=999,  # different native request
                adapterGeneration=1,
            )
        )


# --- Memory delete_session cascade -----------------------------------------


def test_memory_delete_session_cascades_event_indexes_to_zero() -> None:
    store = MemoryStore()
    store.put_session(_session())
    store.put_invocation(_invocation("inv_1"))
    store.put_turn(
        TurnRecord(id="turn_1", invocationId="inv_1", sessionId="hsess_1")
    )
    store.append(_event("evt_1", "hsess_1", "inv_1", 1))
    store.append(_event("evt_2", "hsess_1", "inv_1", 2))

    key = ("chrn_1", "u_1", "hsess_1")
    assert len(store._events_by_session.get(key, [])) == 2
    assert len(store._events_by_invocation.get((*key, "inv_1"), [])) == 2

    store.delete_session(key)

    assert key not in store._events_by_session
    assert (*key, "inv_1") not in store._events_by_invocation
    assert "inv_1" not in store._invocations
    assert "turn_1" not in store._turns


def test_memory_delete_session_missing_is_noop() -> None:
    store = MemoryStore()
    # Should not raise.
    store.delete_session(("chrn_1", "u_1", "does-not-exist"))


# --- Memory sweep boundary -------------------------------------------------


def test_memory_sweep_does_not_remove_fresh_or_non_tombstone() -> None:
    from haas.stores.memory import IdempotencyRecord

    store = MemoryStore()
    store._idempotency["kh_fresh"] = IdempotencyRecord(
        keyHash="kh_fresh",
        requestHash="rh",
        result={"ok": True},
        released=True,
        accepted=True,
        invocationId="inv_1",
        expiresAtMs=10_000,
        tombstone=True,  # tombstone but not expired
    )
    store._idempotency["kh_live"] = IdempotencyRecord(
        keyHash="kh_live",
        requestHash="rh",
        result={"ok": True},
        released=False,
        accepted=False,
        invocationId="inv_1",
        expiresAtMs=100,
        tombstone=False,  # not a tombstone at all
    )
    removed = store.sweep_expired(now_ms=5_000)
    assert removed == 0
    assert "kh_fresh" in store._idempotency
    assert "kh_live" in store._idempotency


# --- Memory reconcile_pending_interactions ---------------------------------


def test_memory_reconcile_closes_waiting_interactions_for_failed_invocation() -> None:
    store = MemoryStore()
    store.put_session(_session())
    inv = _invocation("inv_1", status="failed")
    inv.completedAtMs = 1_000
    store.put_invocation(inv)
    store.put_approval(
        ApprovalRecord(
            id="appr_p",
            sessionId="hsess_1",
            invocationId="inv_1",
            turnId="turn_1",
            status="waiting",
        )
    )
    store.put_input_request(
        InputRequestRecord(
            id="inreq_p",
            sessionId="hsess_1",
            invocationId="inv_1",
            turnId="turn_1",
            questions=[{"id": "q1"}],
            nativeRequestId=1,
            adapterGeneration=1,
        )
    )

    store.reconcile_pending_interactions("hsess_1")

    assert store.get_approval("appr_p").status == "cancelled"
    assert store.get_input_request("inreq_p").status == "cancelled"
