"""Branch-fill unit tests for SQLiteStore rollback/cascade and MemoryStore
sweep/cascade/validation paths.

SQLite tests use a per-test tmp_path database. Memory tests run against an
in-process MemoryStore (no real disk, no network).
"""

from __future__ import annotations

import pytest

from haas.stores.memory import (
    IdempotencyRecord,
    InvocationRecord,
    MemoryStore,
    SessionRecord,
)
from haas.stores.sqlite import SQLiteStore


# --- SQLite: put_session rollback -----------------------------------------


def test_sqlite_put_session_disk_failure_rolls_back_memory(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = SQLiteStore(tmp_path / "rollback.db")
    key = ("chrn", "u1", "s1")
    original = SessionRecord(id="s1", appName="chrn", userId="u1", status="active")
    store.put_session(original)
    prior = store._sessions[key]

    replacement = SessionRecord(id="s1", appName="chrn", userId="u1", status="closed")

    def _boom(*_a: object, **_k: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(store, "_put_record", _boom)

    with pytest.raises(OSError, match="disk full"):
        store.put_session(replacement)

    # In-memory state must be restored to the pre-write value, not the new one.
    assert store._sessions[key] is prior
    assert store._sessions[key].status == "active"


# --- SQLite: put_invocation nested transaction -----------------------------


def test_sqlite_put_invocation_inside_outer_transaction_issues_no_own_txn(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = SQLiteStore(tmp_path / "nested.db")
    executed: list[str] = []

    class _ExecProxy:
        def __init__(self, conn: object, executed: list[str]) -> None:
            self._conn = conn
            self._executed = executed

        def execute(self, sql: str, params: object = ()) -> object:
            self._executed.append(str(sql))
            return self._conn.execute(sql, params)  # type: ignore[attr-defined]

        def __getattr__(self, name: str) -> object:
            return getattr(self._conn, name)

    store._db = _ExecProxy(store._db, executed)

    invocation = InvocationRecord(
        id="inv_1", sessionId="s1", appName="chrn", turnId="turn_1"
    )
    with store.transaction():
        store.put_session(SessionRecord(id="s1", appName="chrn", userId="u1"))
        store.put_invocation(invocation)

    begins = [s for s in executed if "BEGIN IMMEDIATE" in s]
    commits = [s for s in executed if s.strip() == "COMMIT"]
    # Exactly one BEGIN/COMMIT pair comes from the outer transaction; the
    # nested put_invocation must not open its own.
    assert len(begins) == 1
    assert len(commits) == 1
    # And the row actually landed.
    assert "inv_1" in store._invocations


# --- SQLite: delete_session cascade ----------------------------------------


def test_sqlite_delete_session_cascades_invocation_rows(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "cascade.db")
    key = ("chrn", "u1", "s1")
    store.put_session(SessionRecord(id="s1", appName="chrn", userId="u1"))
    store.put_invocation(
        InvocationRecord(id="inv_1", sessionId="s1", appName="chrn", turnId="turn_1")
    )

    before = store._db.execute(
        "SELECT COUNT(*) FROM records WHERE namespace IN ('session','invocation')"
    ).fetchone()[0]
    assert before >= 2

    store.delete_session(key)

    assert key not in store._sessions
    assert "inv_1" not in store._invocations
    leftover = store._db.execute(
        "SELECT COUNT(*) FROM records WHERE namespace IN ('session','invocation')"
    ).fetchone()[0]
    assert leftover == 0


# --- Memory: delete_session cascade events --------------------------------


def test_memory_delete_session_cascades_events_and_invocations() -> None:
    store = MemoryStore()
    key = ("chrn", "u1", "s1")
    inv_key = ("chrn", "u1", "s1", "inv_1")
    store.put_session(SessionRecord(id="s1", appName="chrn", userId="u1"))
    store.put_invocation(
        InvocationRecord(id="inv_1", sessionId="s1", appName="chrn", turnId="turn_1")
    )
    store._events_by_session[key] = [{"eventId": "e1"}]
    store._events_by_invocation[inv_key] = [{"eventId": "e1"}]

    store.delete_session(key)

    assert key not in store._sessions
    assert key not in store._events_by_session
    assert inv_key not in store._events_by_invocation
    assert "inv_1" not in store._invocations


# --- Memory: sweep_expired tombstones --------------------------------------


def test_memory_sweep_expired_removes_only_elapsed_tombstones() -> None:
    store = MemoryStore(clock_ms=lambda: 1_000_000)
    store._idempotency["stale"] = IdempotencyRecord(
        keyHash="stale", requestHash="r", tombstone=True, expiresAtMs=500
    )
    store._idempotency["fresh_tomb"] = IdempotencyRecord(
        keyHash="fresh_tomb", requestHash="r", tombstone=True, expiresAtMs=2_000_000
    )
    store._idempotency["live"] = IdempotencyRecord(
        keyHash="live", requestHash="r", tombstone=False, expiresAtMs=100
    )

    removed = store.sweep_expired(now_ms=1_000_000)

    assert removed == 1
    assert "stale" not in store._idempotency
    assert "fresh_tomb" in store._idempotency
    assert "live" in store._idempotency


# --- Memory: SessionRecord controlState validation --------------------------


def test_memory_session_record_rejects_unknown_control_state() -> None:
    with pytest.raises(ValueError, match="invalid session controlState"):
        SessionRecord(
            id="s_bad", appName="chrn", userId="u1", controlState="exploding"
        )


def test_memory_session_record_accepts_known_control_state() -> None:
    record = SessionRecord(
        id="s_ok", appName="chrn", userId="u1", controlState="pausing"
    )
    assert record.controlState == "pausing"


# --- Memory: reserve lazily triggers sweep ---------------------------------


def test_memory_reserve_lazily_sweeps_expired_tombstones() -> None:
    store = MemoryStore(clock_ms=lambda: 1_000_000)
    # An already-elapsed tombstone under an unrelated key.
    store._idempotency["stale"] = IdempotencyRecord(
        keyHash="stale", requestHash="r", tombstone=True, expiresAtMs=500
    )

    reservation = store.reserve("fresh", "req-hash")

    assert reservation.replay is False
    assert "fresh" in store._idempotency
    # The fresh reserve path must have swept the unrelated expired tombstone.
    assert "stale" not in store._idempotency
