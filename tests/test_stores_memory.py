"""Contract tests for the in-memory Stores backend (specs/stores/README.md)."""

import contextlib

import pytest

from haas.stores.memory import (
    ApprovalRecord,
    ApprovalStateConflictError,
    CanonicalEventRecord,
    CursorNotFoundError,
    DelegatedRuntimeRecord,
    DelegatedSessionNotFoundError,
    DelegatedSessionRecord,
    HarnessRecord,
    IdempotencyConflictError,
    IdempotencyExpiredError,
    InputRequestRecord,
    InputRequestStateConflictError,
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

    assert [e.eventId for e in store.read_session(("chrn_app", "u_1", "hsess_shared"))] == [
        "evt_user_1"
    ]
    assert [e.eventId for e in store.read_session(("chrn_app", "u_2", "hsess_shared"))] == [
        "evt_user_2"
    ]
    assert [e.eventId for e in store.read_session(("chrn_other", "u_1", "hsess_shared"))] == [
        "evt_app_2"
    ]
    assert store.read_invocation(("chrn_app", "u_2", "hsess_shared"), "inv_user_1") == []


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


def test_idempotency_terminal_result_expires_to_tombstone() -> None:
    now = 1_000
    store = MemoryStore(clock_ms=lambda: now)
    store.reserve("khash", "rhash")
    accepted = store.accept("khash", "inv_1", accepted_at_ms=now)
    store.complete("khash", {"events": []}, completed_at_ms=now + 10)

    assert accepted.expiresAtMs == now + 86_400_000
    assert store.reserve("khash", "rhash").result == {"events": []}

    now = accepted.expiresAtMs
    try:
        store.reserve("khash", "rhash")
        raise AssertionError("expected IdempotencyExpiredError")
    except IdempotencyExpiredError as exc:
        assert exc.key_hash == "khash"
        assert exc.invocation_id == "inv_1"
        assert exc.expires_at_ms == accepted.expiresAtMs

    # Tombstones reject every request hash and survive result eviction.
    assert store.replay("khash") is None
    try:
        store.reserve("khash", "different")
        raise AssertionError("expected IdempotencyExpiredError")
    except IdempotencyExpiredError:
        pass


def test_idempotency_nonterminal_reservation_never_expires() -> None:
    now = 1_000
    store = MemoryStore(clock_ms=lambda: now)
    store.reserve("khash", "rhash")
    accepted = store.accept("khash", "inv_1", accepted_at_ms=now)

    now = accepted.expiresAtMs + 1
    replay = store.reserve("khash", "rhash")
    assert replay.replay is True
    assert replay.result is None
    assert store.is_pending("khash") is True


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


def test_delegated_session_store_round_trip_and_runtime_update() -> None:
    store = MemoryStore()
    record = DelegatedSessionRecord(
        id="dgsess_1",
        managerSessionId="mgr_1",
        haasSessionId="hsess_1",
        haasUserId="u_1",
        harnessId="chrn_1",
        image={"reference": "haas:local", "digest": "sha256:test"},
        provider={
            "providerId": "volcengine-ark",
            "model": "doubao-seed-2.1-turbo",
            "credentialRef": "secret://provider/ark",
        },
        mountManifest={
            "version": 1,
            "primaryWorkspace": {
                "hostPathCanonical": "/repo",
                "containerPath": "/workspace",
                "access": "rw",
            },
        },
        delegationPolicySnapshot={
            "version": 1,
            "idleTtlSeconds": 1800,
            "maxContainerLifetimeSeconds": 28800,
            "rwWorkspaceConcurrency": "single_writer",
            "queuePolicy": "fifo",
            "restorePolicy": "fail_closed",
            "mountPolicy": "project_rw_extra_ro",
        },
    )

    saved = store.put_delegated_session(record)
    assert store.get_delegated_session("dgsess_1") == saved
    assert store.get_delegated_session_by_manager("mgr_1") == saved
    assert store.get_delegated_session_by_haas_session("hsess_1") == saved

    updated = store.update_delegated_runtime(
        "dgsess_1", DelegatedRuntimeRecord(status="ttl_destroyed", containerGeneration=2)
    )
    assert updated.runtime.status == "ttl_destroyed"
    assert updated.runtime.containerGeneration == 2

    try:
        store.update_delegated_runtime("dgsess_missing", DelegatedRuntimeRecord())
        raise AssertionError("expected DelegatedSessionNotFoundError")
    except DelegatedSessionNotFoundError:
        pass


def test_approval_resolve_is_single_use() -> None:
    store = MemoryStore()
    approval = store.put_approval(
        ApprovalRecord(
            id="appr_1",
            sessionId="hsess_1",
            invocationId="inv_1",
            turnId="turn_1",
            request={"kind": "tool", "safeSummary": "Run shell"},
        )
    )
    assert approval.status == "waiting"

    resolved = store.resolve_approval("appr_1", {"decision": "approved"})
    assert resolved.status == "approved"
    assert resolved.decision == {"decision": "approved"}
    assert resolved.resolvedAtMs is not None
    assert store.resolve_approval("appr_1", {"decision": "approved"}) == resolved

    try:
        store.resolve_approval("appr_1", {"decision": "denied"})
        raise AssertionError("expected ApprovalStateConflictError")
    except ApprovalStateConflictError:
        pass


def test_terminal_invocation_closes_waiting_interactions() -> None:
    store = MemoryStore()
    store.put_approval(
        ApprovalRecord(
            id="appr_waiting",
            sessionId="hsess_1",
            invocationId="inv_1",
            turnId="turn_1",
        )
    )
    store.put_input_request(
        InputRequestRecord(
            id="inreq_waiting",
            sessionId="hsess_1",
            invocationId="inv_1",
            turnId="turn_1",
            questions=[],
            nativeRequestId=1,
            adapterGeneration=1,
        )
    )

    store.close_pending_interactions("inv_1", resolved_at_ms=1234)

    approval = store.get_approval("appr_waiting")
    request = store.get_input_request("inreq_waiting")
    assert approval is not None and approval.status == "cancelled"
    assert approval.resolvedAtMs == 1234
    assert request is not None and request.status == "cancelled"
    assert request.resolvedAtMs == 1234
    assert store.list_approvals("hsess_1", status="waiting") == []
    assert store.list_input_requests("hsess_1", status="waiting") == []


def test_successful_invocation_keeps_blocking_interaction_waiting() -> None:
    store = MemoryStore()
    store.put_approval(
        ApprovalRecord(
            id="appr_waiting",
            sessionId="hsess_1",
            invocationId="inv_1",
            turnId="turn_1",
        )
    )

    store.put_invocation(
        InvocationRecord(
            id="inv_1",
            sessionId="hsess_1",
            appName="chrn_1",
            turnId="turn_1",
            status="completed",
        )
    )

    approval = store.get_approval("appr_waiting")
    assert approval is not None and approval.status == "waiting"


def test_interactions_are_listable_and_input_resolution_is_single_use() -> None:
    store = MemoryStore()
    store.put_approval(
        ApprovalRecord(id="appr_1", sessionId="hsess_1", invocationId="inv_1", turnId="turn_1")
    )
    request = store.put_input_request(
        InputRequestRecord(
            id="inreq_1",
            sessionId="hsess_1",
            invocationId="inv_1",
            turnId="turn_1",
            questions=[{"id": "scope", "question": "Which?"}],
            nativeRequestId=17,
            adapterGeneration=3,
        )
    )

    assert [item.id for item in store.list_approvals("hsess_1", status="waiting")] == ["appr_1"]
    assert [item.id for item in store.list_input_requests("hsess_1", status="waiting")] == [
        "inreq_1"
    ]
    assert request.nativeRequestId == 17

    resolved = store.resolve_input_request(
        "inreq_1", {"answers": {"scope": {"values": ["Current diff"]}}}
    )
    assert resolved.status == "answered"
    assert (
        store.resolve_input_request("inreq_1", dict(resolved.answers or {}))
        == resolved
    )
    with pytest.raises(InputRequestStateConflictError):
        store.resolve_input_request("inreq_1", {"answers": {}})


def test_workspace_lock_single_writer_allows_ro_sharing() -> None:
    store = MemoryStore()
    first = store.acquire_workspace_lock("/repo", "dgsess_a", "rw", ttl_ms=60_000)
    assert first.acquired is True

    second = store.acquire_workspace_lock("/repo", "dgsess_b", "rw", ttl_ms=60_000)
    assert second.acquired is False
    assert second.holder == "dgsess_a"

    readonly = store.acquire_workspace_lock("/repo", "dgsess_ro", "ro", ttl_ms=60_000)
    assert readonly.acquired is True

    store.release_workspace_lock("/repo", "dgsess_a")
    third = store.acquire_workspace_lock("/repo", "dgsess_b", "rw", ttl_ms=60_000)
    assert third.acquired is True


# --- P1-2: delete_session cascade + idempotency sweep ----------------------


def test_delete_session_cascades_invocations_turns_events_and_idempotency() -> None:
    store = MemoryStore()
    key = ("chrn_1", "u_1", "hsess_1")
    store.put_session(SessionRecord(id="hsess_1", appName="chrn_1", userId="u_1"))
    store.put_invocation(
        InvocationRecord(id="inv_1", sessionId="hsess_1", appName="chrn_1", turnId="turn_1")
    )
    store.put_turn(TurnRecord(id="turn_1", invocationId="inv_1", sessionId="hsess_1"))
    store.append(_event(eventId="evt_1", invocationId="inv_1", appName="chrn_1", userId="u_1"))
    store.append(
        _event(
            eventId="evt_2", invocationId="inv_1", sequenceNumber=1,
            appName="chrn_1", userId="u_1",
        )
    )
    store.put_approval(
        ApprovalRecord(
            id="appr_1", sessionId="hsess_1", invocationId="inv_1", turnId="turn_1"
        )
    )
    store.reserve("kh_1", "rh_1")
    store.accept("kh_1", "inv_1")

    store.delete_session(key)

    assert store.get_session(key) is None
    assert store.get_invocation("inv_1") is None
    assert store.get_turn("turn_1") is None
    assert store._events_by_session == {}
    assert store._events_by_invocation == {}
    assert store.get_approval("appr_1") is None
    assert "kh_1" not in store._idempotency


def test_idempotency_sweep_removes_expired_tombstones() -> None:
    clock = [1_000]
    store = MemoryStore(clock_ms=lambda: clock[0])
    store.reserve("kh", "rh")
    accepted = store.accept("kh", "inv_1", accepted_at_ms=clock[0])
    store.complete("kh", {"events": []}, completed_at_ms=clock[0] + 10)

    # Advance past TTL: the next reserve turns the result into a tombstone.
    clock[0] = accepted.expiresAtMs + 1
    with contextlib.suppress(IdempotencyExpiredError):
        store.reserve("kh", "rh")
    assert "kh" in store._idempotency

    removed = store.sweep_expired()
    assert removed == 1
    assert "kh" not in store._idempotency


def test_session_record_rejects_invalid_control_state() -> None:
    with pytest.raises(ValueError):
        SessionRecord(id="hsess_x", appName="a", userId="u", controlState="bogus")
