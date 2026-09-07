"""Contract tests for the in-memory Stores backend (specs/stores/README.md)."""
from haas.stores.memory import (
    CanonicalEventRecord,
    CursorNotFoundError,
    HarnessRecord,
    IdempotencyConflictError,
    InvocationRecord,
    LeaseConflictError,
    LeaseFencingError,
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

    key = ("", "", "hsess_1")
    assert [e.eventId for e in store.read_invocation(key, "inv_1", after=0)] == ["evt_1"]
    assert [e.eventId for e in store.read_session(key)] == ["evt_0", "evt_1"]
    assert [e.eventId for e in store.read_session(key, after_cursor="evt_0")] == ["evt_1"]


def test_event_session_index_is_scoped_by_app_user_session() -> None:
    store = MemoryStore()
    store.append(
        _event(
            eventId="evt_user_1",
            invocationId="inv_user_1",
            appName="chrn_app",
            userId="u_1",
            sessionId="hsess_shared",
        )
    )
    store.append(
        _event(
            eventId="evt_user_2",
            invocationId="inv_user_2",
            appName="chrn_app",
            userId="u_2",
            sessionId="hsess_shared",
        )
    )
    store.append(
        _event(
            eventId="evt_app_2",
            invocationId="inv_app_2",
            appName="chrn_other",
            userId="u_1",
            sessionId="hsess_shared",
        )
    )

    assert [
        e.eventId for e in store.read_session(("chrn_app", "u_1", "hsess_shared"))
    ] == ["evt_user_1"]
    assert [
        e.eventId for e in store.read_session(("chrn_app", "u_2", "hsess_shared"))
    ] == ["evt_user_2"]
    assert [
        e.eventId for e in store.read_session(("chrn_other", "u_1", "hsess_shared"))
    ] == ["evt_app_2"]
    assert (
        store.read_invocation(("chrn_app", "u_2", "hsess_shared"), "inv_user_1")
        == []
    )


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


def test_idempotency_in_flight_pending_tracking() -> None:
    store = MemoryStore()
    store.reserve("khash", "rhash")
    assert store.is_pending("khash") is True
    assert store.replay("khash") is None  # in-flight: no result yet

    store.complete("khash", {"events": []})
    assert store.is_pending("khash") is False
    assert store.replay("khash") == {"events": []}

    store.release("khash")
    assert store.is_pending("khash") is False
    assert store.replay("khash") is None
    # after release, a fresh reservation owns the key again
    assert store.reserve("khash", "rhash").replay is False


def test_session_read_unknown_cursor_raises() -> None:
    store = MemoryStore()
    store.append(_event(eventId="evt_0", sequenceNumber=0))
    try:
        store.read_session(("", "", "hsess_1"), after_cursor="evt_missing")
        raise AssertionError("expected CursorNotFoundError")
    except CursorNotFoundError:
        pass


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


def test_lease_renew_and_fencing_token_rejects_stale_holder() -> None:
    store = MemoryStore()
    key = ("chrn_1", "u_1", "hsess_fence")
    first = store.acquire_lease(key, holder="run_a", ttl_ms=30)
    renewed = store.renew_lease(key, holder="run_a", token=first.token, ttl_ms=30)
    assert renewed.token == first.token
    assert renewed.expiresAtMs >= first.expiresAtMs

    # Force a takeover without sleeping; the new holder must receive a newer token.
    store._leases[key].expiresAtMs = 0  # type: ignore[attr-defined]
    second = store.acquire_lease(key, holder="run_b", ttl_ms=30)
    assert second.token > first.token

    try:
        store.assert_lease(key, holder="run_a", token=first.token)
        raise AssertionError("expected stale holder to be fenced")
    except LeaseFencingError:
        pass

    # Stale release must not remove the current holder's lease.
    store.release_lease(key, holder="run_a", token=first.token)
    store.assert_lease(key, holder="run_b", token=second.token)
