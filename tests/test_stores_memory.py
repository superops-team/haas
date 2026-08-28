"""Contract tests for the in-memory Stores backend (specs/stores/README.md)."""
from haas.stores.memory import (
    CanonicalEventRecord,
    HarnessRecord,
    IdempotencyConflictError,
    InvocationRecord,
    LeaseConflictError,
    MemoryStore,
    SessionRecord,
    TurnRecord,
)


def _event(**kwargs: object) -> CanonicalEventRecord:
    base: dict[str, object] = {
        "eventId": "evt_1",
        "invocationId": "inv_1",
        "sessionId": "hsess_1",
        "turnId": "turn_1",
        "author": "codex",
        "sequenceNumber": 0,
        "content": {"role": "model", "parts": [{"text": "hi"}]},
        "actions": {},
    }
    base.update(kwargs)
    return CanonicalEventRecord(**base)  # type: ignore[arg-type]


def test_registry_upsert_read_list_delete() -> None:
    store = MemoryStore()
    h = store.save_harness(HarnessRecord(id="chrn_1", name="codex", base="codex"))
    assert store.get_harness("chrn_1") == h
    assert [x.id for x in store.list_harnesses()] == ["chrn_1"]
    store.delete_harness("chrn_1")
    assert store.get_harness("chrn_1") is None


def test_session_put_get_delete_and_invocation_turn() -> None:
    store = MemoryStore()
    key = ("chrn_1", "u_1", "hsess_1")
    sess = store.put_session(SessionRecord(id="hsess_1", appName="chrn_1", userId="u_1"))
    assert store.get_session(key) == sess
    inv = store.put_invocation(
        InvocationRecord(id="inv_1", sessionId="hsess_1", appName="chrn_1", turnId="turn_1")
    )
    turn = store.put_turn(TurnRecord(id="turn_1", invocationId="inv_1", sessionId="hsess_1"))
    assert inv.id == "inv_1"
    assert turn.id == "turn_1"
    store.delete_session(key)
    assert store.get_session(key) is None


def test_event_append_and_cursor_reads() -> None:
    store = MemoryStore()
    e0 = _event(eventId="evt_0", sequenceNumber=0)
    e1 = _event(eventId="evt_1", sequenceNumber=1)
    store.append(e0)
    store.append(e1)

    assert [e.eventId for e in store.read_invocation("inv_1", after=0)] == ["evt_1"]
    assert [e.eventId for e in store.read_session("hsess_1")] == ["evt_0", "evt_1"]
    assert [e.eventId for e in store.read_session("hsess_1", after_cursor="evt_0")] == ["evt_1"]


def test_idempotency_reserve_replay_conflict_release() -> None:
    store = MemoryStore()
    first = store.reserve("khash", "rhash")
    assert first.replay is False

    replay = store.reserve("khash", "rhash")
    assert replay.replay is True

    store.complete("khash", {"ok": True})
    assert store.replay("khash") == {"ok": True}

    # Different request hash under the same key must fail closed.
    try:
        store.reserve("khash", "other-hash")
        raise AssertionError("expected IdempotencyConflictError")
    except IdempotencyConflictError:
        pass

    store.release("khash")
    assert store.replay("khash") is None


def test_lease_acquire_and_conflict() -> None:
    store = MemoryStore()
    key = ("chrn_1", "u_1", "hsess_1")
    store.acquire_lease(key, holder="run_a")
    try:
        store.acquire_lease(key, holder="run_b")
        raise AssertionError("expected LeaseConflictError")
    except LeaseConflictError:
        pass
    store.release_lease(key, holder="run_a")
    store.acquire_lease(key, holder="run_b")


def test_admission_window_and_quota() -> None:
    store = MemoryStore()
    # fixed window: only events within the trailing window are counted
    assert store.incr_window("run:tenant_1", now_ms=1_000, window_ms=60_000) == 1
    assert store.incr_window("run:tenant_1", now_ms=2_000, window_ms=60_000) == 2
    # stale timestamps are pruned, so an old event no longer counts
    assert store.incr_window("run:tenant_1", now_ms=200_000, window_ms=60_000) == 1

    # quota acquire/release against a limit
    assert store.acquire_quota("sessions:tenant_1", limit=1) is True
    assert store.acquire_quota("sessions:tenant_1", limit=1) is False
    store.release_quota("sessions:tenant_1")
    assert store.acquire_quota("sessions:tenant_1", limit=1) is True
