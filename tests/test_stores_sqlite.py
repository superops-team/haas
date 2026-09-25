"""Persistent SQLite store contract tests."""

from __future__ import annotations

import json
import sqlite3

import pytest

from haas.events import EventLog
from haas.stores import (
    ApprovalRecord,
    CanonicalEventRecord,
    DelegatedRuntimeRecord,
    DelegatedSessionRecord,
    HarnessRecord,
    IdempotencyConflictError,
    IdempotencyExpiredError,
    InputRequestRecord,
    InvocationRecord,
    ProfileRecord,
    ProviderConfig,
    SessionRecord,
    SQLiteStore,
    TurnRecord,
)


def _profile(*, status: str = "draft", content: dict[str, object] | None = None) -> ProfileRecord:
    return ProfileRecord(
        id="hprof_1",
        harnessId="chrn_1",
        base="codex",
        version=1,
        content=content or {"model": "gpt-test"},
        profileFingerprint="sha256:test",
        status=status,
        tenantId="tenant_1",
        workspaceId="workspace_1",
        createdAtMs=100,
    )


def test_sqlite_store_survives_restart(tmp_path) -> None:
    path = tmp_path / "haas.db"
    now = 1_000
    first = SQLiteStore(path, clock_ms=lambda: now)
    key = ("chrn_1", "u_1", "hsess_1")
    first.put_session(SessionRecord(id=key[2], appName=key[0], userId=key[1]))
    first.put_invocation(
        InvocationRecord(
            id="inv_1", sessionId=key[2], appName=key[0], userId=key[1], turnId="turn_1"
        )
    )
    first.put_turn(TurnRecord(id="turn_1", invocationId="inv_1", sessionId=key[2]))
    first.append(
        CanonicalEventRecord(
            eventId="evt_1",
            invocationId="inv_1",
            sessionId=key[2],
            turnId="turn_1",
            author="codex",
            sequenceNumber=0,
            content={"role": "model", "parts": []},
            actions={},
            appName=key[0],
            userId=key[1],
        )
    )
    first.reserve("key-hash", "request-hash")
    accepted = first.accept("key-hash", "inv_1", accepted_at_ms=now)
    first.complete("key-hash", {"events": [{"id": "evt_1"}]}, completed_at_ms=now)
    first.close()

    reopened = SQLiteStore(path, clock_ms=lambda: now)
    assert reopened.get_session(key) is not None
    assert reopened.get_invocation("inv_1") is not None
    assert [event.eventId for event in reopened.read_session(key)] == ["evt_1"]
    assert reopened.replay("key-hash") == {"events": [{"id": "evt_1"}]}
    reopened.close()

    now = accepted.expiresAtMs
    expired = SQLiteStore(path, clock_ms=lambda: now)
    with pytest.raises(IdempotencyExpiredError):
        expired.reserve("key-hash", "request-hash")
    expired.close()

    tombstone = SQLiteStore(path, clock_ms=lambda: now + 1)
    with pytest.raises(IdempotencyExpiredError):
        tombstone.reserve("key-hash", "request-hash")
    tombstone.close()


def test_event_ids_remain_monotonic_across_sqlite_restart(tmp_path) -> None:
    path = tmp_path / "haas.db"
    first_store = SQLiteStore(path)
    first = EventLog(first_store).append_typed(
        type_="haas.output.text.delta",
        app_name="chrn_1",
        user_id="u_1",
        invocation_id="inv_1",
        session_id="hsess_1",
        turn_id="turn_1",
        harness_id="chrn_1",
        adapter_id="fake",
        author="fake",
        content={"role": "model", "parts": [{"text": "first"}]},
        actions={},
        haas={},
    )
    first_store.close()

    second_store = SQLiteStore(path)
    second = EventLog(second_store).append_typed(
        type_="haas.output.text.delta",
        app_name="chrn_1",
        user_id="u_1",
        invocation_id="inv_2",
        session_id="hsess_1",
        turn_id="turn_2",
        harness_id="chrn_1",
        adapter_id="fake",
        author="fake",
        content={"role": "model", "parts": [{"text": "second"}]},
        actions={},
        haas={},
    )
    assert first.eventId == "evt_0000000000000"
    assert second.eventId == "evt_0000000000001"
    assert [event.eventId for event in second_store.read_session(("chrn_1", "u_1", "hsess_1"))] == [
        first.eventId,
        second.eventId,
    ]
    second_store.close()


def test_sqlite_store_forward_migrates_empty_version_zero_database(tmp_path) -> None:
    path = tmp_path / "legacy.db"
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA user_version = 0")
    connection.commit()
    connection.close()

    store = SQLiteStore(path)
    assert store.schema_version == SQLiteStore.SCHEMA_VERSION
    store.close()

    connection = sqlite3.connect(path)
    assert connection.execute("PRAGMA user_version").fetchone() == (SQLiteStore.SCHEMA_VERSION,)
    connection.close()


def test_sqlite_store_ignores_unknown_session_fields_and_keeps_control_state(tmp_path) -> None:
    path = tmp_path / "forward-compatible.db"
    with SQLiteStore(path) as first:
        first.put_session(
            SessionRecord(
                id="hsess_forward",
                appName="chrn_1",
                userId="u_1",
                controlState="running",
                supportsResume=True,
                resumableInvocationId="inv_resume",
            )
        )
    connection = sqlite3.connect(path)
    payload = json.loads(
        connection.execute(
            "SELECT payload FROM records WHERE namespace='session' AND record_key=?",
            ("chrn_1|u_1|hsess_forward",),
        ).fetchone()[0]
    )
    payload["unknownFutureField"] = {"safe": True}
    connection.execute(
        "UPDATE records SET payload=? WHERE namespace='session' AND record_key=?",
        (json.dumps(payload), "chrn_1|u_1|hsess_forward"),
    )
    connection.commit()
    connection.close()

    with SQLiteStore(path) as reopened:
        restored = reopened.get_session(("chrn_1", "u_1", "hsess_forward"))

    assert restored is not None
    assert restored.controlState == "running"
    assert restored.supportsResume is True
    assert restored.resumableInvocationId == "inv_resume"


def test_sqlite_store_ignores_unknown_invocation_and_turn_fields(tmp_path) -> None:
    path = tmp_path / "forward-compatible-invocation.db"
    with SQLiteStore(path):
        pass
    connection = sqlite3.connect(path)
    connection.execute(
        "INSERT INTO records(namespace, record_key, payload) VALUES(?, ?, ?)",
        (
            "invocation",
            "inv_forward",
            json.dumps(
                {
                    "id": "inv_forward",
                    "sessionId": "hsess_forward",
                    "appName": "chrn_1",
                    "turnId": "turn_forward",
                    "userId": "u_1",
                    "status": "completed",
                    "deadlineAtMs": 1_900_000_000_000,
                    "unknownFutureField": "ignored",
                }
            ),
        ),
    )
    connection.execute(
        "INSERT INTO records(namespace, record_key, payload) VALUES(?, ?, ?)",
        (
            "turn",
            "turn_forward",
            json.dumps(
                {
                    "id": "turn_forward",
                    "invocationId": "inv_forward",
                    "sessionId": "hsess_forward",
                    "status": "completed",
                    "unknownFutureField": "ignored",
                }
            ),
        ),
    )
    connection.commit()
    connection.close()

    with SQLiteStore(path) as reopened:
        invocation = reopened.get_invocation("inv_forward")
        turn = reopened.get_turn("turn_forward")

    assert invocation is not None
    assert invocation.deadlineAtMs == 1_900_000_000_000
    assert invocation.status == "completed"
    assert turn is not None
    assert turn.status == "completed"


def test_sqlite_store_normalizes_legacy_control_state_alias(tmp_path) -> None:
    path = tmp_path / "control-state-alias.db"
    with SQLiteStore(path):
        pass
    payload = {
        "id": "hsess_alias",
        "appName": "chrn_1",
        "userId": "u_1",
        "state": {},
        "control_state": "paused",
        "supportsResume": True,
        "resumableInvocationId": "inv_resume",
        "unknownFutureField": "ignored",
    }
    connection = sqlite3.connect(path)
    connection.execute(
        "INSERT INTO records(namespace, record_key, payload) VALUES(?, ?, ?)",
        ("session", "chrn_1|u_1|hsess_alias", json.dumps(payload)),
    )
    connection.commit()
    connection.close()

    with SQLiteStore(path) as reopened:
        restored = reopened.get_session(("chrn_1", "u_1", "hsess_alias"))

    assert restored is not None
    assert restored.controlState == "paused"
    assert restored.supportsResume is True
    assert restored.resumableInvocationId == "inv_resume"


def test_sqlite_store_quarantines_invalid_session_control_state(tmp_path) -> None:
    path = tmp_path / "invalid-control-state.db"
    with SQLiteStore(path):
        pass
    connection = sqlite3.connect(path)
    connection.execute(
        "INSERT INTO records(namespace, record_key, payload) VALUES(?, ?, ?)",
        (
            "session",
            "chrn_1|u_1|hsess_bad",
            json.dumps(
                {
                    "id": "hsess_bad",
                    "appName": "chrn_1",
                    "userId": "u_1",
                    "controlState": "impossible",
                    "supportsResume": False,
                }
            ),
        ),
    )
    connection.execute(
        "INSERT INTO records(namespace, record_key, payload) VALUES(?, ?, ?)",
        (
            "session",
            "chrn_1|u_1|hsess_ok",
            json.dumps(
                {
                    "id": "hsess_ok",
                    "appName": "chrn_1",
                    "userId": "u_1",
                    "controlState": "idle",
                }
            ),
        ),
    )
    connection.commit()
    connection.close()

    with SQLiteStore(path) as reopened:
        assert reopened.get_session(("chrn_1", "u_1", "hsess_bad")) is None
        assert reopened.get_session(("chrn_1", "u_1", "hsess_ok")) is not None


def test_sqlite_store_persists_control_plane_records_and_fencing(tmp_path) -> None:
    path = tmp_path / "control.db"
    first = SQLiteStore(path)
    harness = first.save_harness(
        HarnessRecord(
            id="chrn_1",
            name="codex",
            base="codex",
            provider=ProviderConfig(name="ark", credentialRef="secret://ark"),
        )
    )
    delegated = first.put_delegated_session(
        DelegatedSessionRecord(
            id="dgsess_1",
            managerSessionId="mgr_1",
            haasSessionId="hsess_1",
            haasUserId="u_1",
            harnessId=harness.id,
            image={"reference": "haas:test"},
            provider={"providerId": "ark"},
            mountManifest={"version": 1},
            delegationPolicySnapshot={"version": 1},
            runtime=DelegatedRuntimeRecord(status="idle", containerGeneration=3),
        )
    )
    first.put_approval(
        ApprovalRecord(id="appr_1", sessionId="hsess_1", invocationId="inv_1", turnId="turn_1")
    )
    first.put_input_request(
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
    lease = first.acquire_lease(("chrn_1", "u_1", "hsess_1"), "holder-a")
    first.acquire_workspace_lock("/repo", delegated.id, "rw")
    first.close()

    second = SQLiteStore(path)
    assert second.get_harness(harness.id) == harness
    assert second.get_delegated_session_by_manager("mgr_1") == delegated
    assert second.get_approval("appr_1") is not None
    restored_input = second.get_input_request("inreq_1")
    assert restored_input is not None
    assert restored_input.nativeRequestId == 17
    second.assert_lease(("chrn_1", "u_1", "hsess_1"), "holder-a", lease.token)
    assert second.acquire_workspace_lock("/repo", "dgsess_2", "rw").acquired is False
    second.close()


def test_sqlite_idempotency_reservation_is_atomic_across_connections(tmp_path) -> None:
    path = tmp_path / "idempotency.db"
    first = SQLiteStore(path)
    second = SQLiteStore(path)
    assert first.reserve("shared", "request").replay is False
    assert second.reserve("shared", "request").replay is True
    with pytest.raises(IdempotencyConflictError):
        second.reserve("shared", "different")
    first.close()
    second.close()


def test_sqlite_store_context_manager_closes_connection(tmp_path) -> None:
    with SQLiteStore(tmp_path / "context.db") as store:
        assert store.schema_version == SQLiteStore.SCHEMA_VERSION

    with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
        _ = store.schema_version


def test_sqlite_store_rejects_unknown_newer_schema(tmp_path) -> None:
    path = tmp_path / "newer.db"
    connection = sqlite3.connect(path)
    connection.execute(f"PRAGMA user_version = {SQLiteStore.SCHEMA_VERSION + 1}")
    connection.close()

    with pytest.raises(RuntimeError, match=r"store schema 2 is newer than supported 1"):
        SQLiteStore(path)


def test_sqlite_store_persists_updates_and_deletes(tmp_path) -> None:
    path = tmp_path / "updates.db"
    key = ("chrn_1", "u_1", "hsess_1")
    with SQLiteStore(path) as store:
        store.save_harness(HarnessRecord(id="chrn_1", name="before", base="codex"))
        store.save_harness(HarnessRecord(id="chrn_1", name="after", base="codex"))
        store.put_session(SessionRecord(id=key[2], appName=key[0], userId=key[1]))
        store.put_session(
            SessionRecord(id=key[2], appName=key[0], userId=key[1], state={"phase": "updated"})
        )
        store.put_approval(
            ApprovalRecord(id="appr_1", sessionId=key[2], invocationId="inv_1", turnId="turn_1")
        )
        store.resolve_approval("appr_1", {"decision": "approved"})

    with SQLiteStore(path) as reopened:
        assert reopened.get_harness("chrn_1").name == "after"  # type: ignore[union-attr]
        assert reopened.get_session(key).state == {"phase": "updated"}  # type: ignore[union-attr]
        assert reopened.get_approval("appr_1").status == "approved"  # type: ignore[union-attr]
        reopened.delete_harness("chrn_1")
        reopened.delete_session(key)

    with SQLiteStore(path) as final:
        assert final.get_harness("chrn_1") is None
        assert final.get_session(key) is None


def test_sqlite_terminal_invocation_persists_interaction_cleanup(tmp_path) -> None:
    path = tmp_path / "terminal-interactions.db"
    with SQLiteStore(path) as store:
        store.put_approval(
            ApprovalRecord(
                id="appr_1", sessionId="hsess_1", invocationId="inv_1", turnId="turn_1"
            )
        )
        store.put_input_request(
            InputRequestRecord(
                id="inreq_1",
                sessionId="hsess_1",
                invocationId="inv_1",
                turnId="turn_1",
                questions=[],
                nativeRequestId=1,
                adapterGeneration=1,
            )
        )
        store.put_invocation(
            InvocationRecord(
                id="inv_1",
                sessionId="hsess_1",
                appName="chrn_1",
                turnId="turn_1",
                status="failed",
                completedAtMs=1234,
            )
        )

    with SQLiteStore(path) as reopened:
        approval = reopened.get_approval("appr_1")
        request = reopened.get_input_request("inreq_1")
        assert approval is not None and approval.status == "cancelled"
        assert request is not None and request.status == "cancelled"


def test_sqlite_store_persists_profile_lifecycle(tmp_path) -> None:
    path = tmp_path / "profiles.db"
    with SQLiteStore(path, clock_ms=lambda: 200) as store:
        created = store.save_profile(_profile())
        updated = store.save_profile(_profile(status="active", content={"model": "gpt-updated"}))
        assert updated.createdAtMs == created.createdAtMs == 100

    with SQLiteStore(path) as reopened:
        assert reopened.get_profile("hprof_1") == updated
        assert reopened.list_profiles(
            ("tenant_1", "workspace_1"), harness_id="chrn_1", status="active"
        ) == [updated]


def test_sqlite_store_persists_lease_renewal_and_release(tmp_path) -> None:
    path = tmp_path / "lease.db"
    key = ("chrn_1", "u_1", "hsess_1")
    with SQLiteStore(path) as store:
        lease = store.acquire_lease(key, "holder-a")
        renewed = store.renew_lease(key, "holder-a", lease.token, ttl_ms=60_000)
        store.release_lease(key, "wrong-holder", renewed.token)

    with SQLiteStore(path) as reopened:
        reopened.assert_lease(key, "holder-a", renewed.token)
        reopened.release_lease(key, "holder-a", renewed.token)

    with SQLiteStore(path) as final:
        replacement = final.acquire_lease(key, "holder-b")
        assert replacement.token > renewed.token


def test_sqlite_store_persists_workspace_lock_release(tmp_path) -> None:
    path = tmp_path / "workspace.db"
    with SQLiteStore(path) as store:
        assert store.acquire_workspace_lock("/read-only", "dgsess_ro", "ro").acquired
        assert store.acquire_workspace_lock("/repo", "dgsess_1", "rw").acquired
        store.release_workspace_lock("/repo", "wrong-holder")

    with SQLiteStore(path) as reopened:
        assert not reopened.acquire_workspace_lock("/repo", "dgsess_2", "rw").acquired
        reopened.release_workspace_lock("/repo", "dgsess_1")

    with SQLiteStore(path) as final:
        assert final.acquire_workspace_lock("/repo", "dgsess_2", "rw").acquired


def test_sqlite_idempotency_accept_complete_replay_and_release_persist(tmp_path) -> None:
    path = tmp_path / "idempotency-lifecycle.db"
    with SQLiteStore(path, clock_ms=lambda: 1_000) as store:
        assert store.replay("missing") is None
        store.complete("missing", {"ignored": True})
        store.release("missing")
        with pytest.raises(KeyError):
            store.accept("missing", "inv_missing")

        assert store.reserve("key", "request").replay is False
        accepted = store.accept("key", "inv_1")
        assert accepted.acceptedAtMs == 1_000
        store.complete("key", {"events": []})

    with SQLiteStore(path, clock_ms=lambda: 1_001) as reopened:
        assert reopened.replay("key") == {"events": []}
        reopened.release("key")

    with SQLiteStore(path) as final:
        assert final.replay("key") is None
        assert final.reserve("key", "new-request").replay is False


# --- P0-3: atomicity, busy_timeout, write-failure rollback -----------------


def test_transaction_rolls_back_all_records_on_fault_injection(tmp_path) -> None:
    """All-or-nothing: a mid-transaction write failure must leave no half-committed state."""
    import time as _time

    path = tmp_path / "atomic.db"
    key = ("chrn_1", "u_1", "hsess_1")
    with SQLiteStore(path) as store:
        store.put_session(
            SessionRecord(
                id=key[2], appName=key[0], userId=key[1], controlState="running"
            )
        )
        store.put_invocation(
            InvocationRecord(
                id="inv_1",
                sessionId=key[2],
                appName=key[0],
                turnId="turn_1",
                status="running",
            )
        )
        store.put_turn(
            TurnRecord(id="turn_1", invocationId="inv_1", sessionId=key[2], status="running")
        )
        baseline = CanonicalEventRecord(
            eventId="evt_0",
            invocationId="inv_1",
            sessionId=key[2],
            turnId="turn_1",
            author="fake",
            sequenceNumber=0,
            content={"role": "model", "parts": []},
            actions={},
            appName=key[0],
            userId=key[1],
            observedAtMs=100,
        )
        store.append(baseline)

        calls = {"n": 0}
        real_put = store._put_record

        def flaky(namespace: str, record_key: str, value: object) -> None:
            calls["n"] += 1
            if calls["n"] == 3:  # third write = put_session
                raise OSError("injected disk full")
            real_put(namespace, record_key, value)  # type: ignore[arg-type]

        store._put_record = flaky  # type: ignore[method-assign]
        with pytest.raises(OSError), store.transaction():
            store.put_invocation(
                InvocationRecord(
                    id="inv_1",
                    sessionId=key[2],
                    appName=key[0],
                    turnId="turn_1",
                    status="failed",
                )
            )
            store.put_turn(
                TurnRecord(
                    id="turn_1",
                    invocationId="inv_1",
                    sessionId=key[2],
                    status="failed",
                )
            )
            store.put_session(
                SessionRecord(
                    id=key[2], appName=key[0], userId=key[1], controlState="idle"
                )
            )
            store.append(
                CanonicalEventRecord(
                    eventId="evt_1",
                    invocationId="inv_1",
                    sessionId=key[2],
                    turnId="turn_1",
                    author="fake",
                    sequenceNumber=1,
                    content={"role": "model", "parts": []},
                    actions={},
                    appName=key[0],
                    userId=key[1],
                    observedAtMs=200,
                )
            )

    # Restart: readback must converge to the pre-terminal (running) state.
    with SQLiteStore(path) as reopened:
        invocation = reopened.get_invocation("inv_1")
        turn = reopened.get_turn("turn_1")
        session = reopened.get_session(key)
        assert invocation is not None and invocation.status == "running"
        assert turn is not None and turn.status == "running"
        assert session is not None and session.controlState == "running"
        assert [e.eventId for e in reopened.read_session(key)] == ["evt_0"]
    _time.sleep(0)


def test_write_failure_rolls_back_in_memory_state(tmp_path) -> None:
    """If _put_record raises, in-memory state reverts to the pre-write value."""
    path = tmp_path / "rollback.db"
    key = ("chrn_1", "u_1", "hsess_1")
    with SQLiteStore(path) as store:
        store.put_session(
            SessionRecord(
                id=key[2], appName=key[0], userId=key[1], controlState="running"
            )
        )
        real_put = store._put_record

        def boom(*_args: object, **_kwargs: object) -> None:
            raise OSError("injected write failure")

        store._put_record = boom  # type: ignore[method-assign]
        with pytest.raises(OSError):
            store.put_session(
                SessionRecord(
                    id=key[2], appName=key[0], userId=key[1], controlState="idle"
                )
            )
        store._put_record = real_put  # type: ignore[method-assign]

        session = store.get_session(key)
        assert session is not None
        assert session.controlState == "running"  # reverted, not "idle"


def test_busy_timeout_makes_writer_wait_instead_of_failing_immediately(tmp_path) -> None:
    """A second connection holding the write lock blocks the store writer, not SQLITE_BUSY."""
    import threading
    import time as _time

    path = tmp_path / "busy.db"
    # Seed the schema before the blocker starts.
    SQLiteStore(path).close()

    lock_held = threading.Event()

    def hold_lock() -> None:
        blocker = sqlite3.connect(path, check_same_thread=False)
        blocker.isolation_level = None
        blocker.execute("BEGIN IMMEDIATE")
        blocker.execute(
            "INSERT INTO records(namespace, record_key, payload) VALUES('session','hold','{}')"
        )
        lock_held.set()
        _time.sleep(1.2)
        blocker.execute("COMMIT")
        blocker.close()

    holder = threading.Thread(target=hold_lock, daemon=True)
    holder.start()
    lock_held.wait(2.0)
    _time.sleep(0.1)  # let the RESERVED lock settle

    store = SQLiteStore(path)
    started = _time.monotonic()
    store.put_session(
        SessionRecord(id="hsess_new", appName="chrn_1", userId="u_1", controlState="idle")
    )
    elapsed = _time.monotonic() - started
    holder.join()
    store.close()
    # The writer waited on the busy_timeout path rather than failing at once.
    assert elapsed >= 1.0




# === appended: sqlite atomicity / rollback / load-path coverage ===






def _event(event_id: str, invocation_id: str = "inv_1") -> CanonicalEventRecord:
    return CanonicalEventRecord(
        eventId=event_id,
        invocationId=invocation_id,
        sessionId="hsess_1",
        turnId="turn_1",
        author="fake",
        sequenceNumber=0,
        content={"role": "model", "parts": []},
        actions={},
        appName="chrn_1",
        userId="u_1",
    )


# --- transaction success / nesting -----------------------------------------


def test_transaction_commits_multiple_records_atomically(tmp_path) -> None:
    path = tmp_path / "commit.db"
    key = ("chrn_1", "u_1", "hsess_1")
    with SQLiteStore(path) as store, store.transaction():
        store.put_session(SessionRecord(id=key[2], appName=key[0], userId=key[1]))
        store.put_invocation(
            InvocationRecord(
                id="inv_1", sessionId=key[2], appName=key[0], turnId="turn_1"
            )
        )

    with SQLiteStore(path) as reopened:
        assert reopened.get_session(key) is not None
        assert reopened.get_invocation("inv_1") is not None


def test_nested_transaction_joins_outer_atomic_unit(tmp_path) -> None:
    """A nested transaction shares the outer boundary; writes inside it commit
    when the outermost block commits (no inner BEGIN/COMMIT of its own)."""
    path = tmp_path / "nested.db"
    key = ("chrn_1", "u_1", "hsess_1")
    with SQLiteStore(path) as store:
        store.put_session(SessionRecord(id=key[2], appName=key[0], userId=key[1]))
        with store.transaction(), store.transaction():
            store.put_invocation(
                InvocationRecord(
                    id="inv_nested", sessionId=key[2], appName=key[0], turnId="turn_1"
                )
            )
    with SQLiteStore(path) as reopened:
        assert reopened.get_invocation("inv_nested") is not None


def test_restore_memory_checkpoint_is_noop_when_snapshot_missing(tmp_path) -> None:
    path = tmp_path / "snapshot-none.db"
    with SQLiteStore(path) as store:
        # Defensive path: a nested frame has no snapshot, so restoring is a no-op.
        store._restore_memory_checkpoint(None)


# --- write-failure rollback helpers ------------------------------------------


def test_put_session_failure_pops_unknown_previous_record(tmp_path) -> None:
    path = tmp_path / "rollback-new.db"
    with SQLiteStore(path) as store:
        real_put = store._put_record

        def boom(*_a: object, **_k: object) -> None:
            raise OSError("disk full")

        store._put_record = boom  # type: ignore[method-assign]
        with pytest.raises(OSError):
            store.put_session(
                SessionRecord(id="hsess_new", appName="chrn_1", userId="u_1")
            )
        store._put_record = real_put  # type: ignore[method-assign]
        assert store.get_session(("chrn_1", "u_1", "hsess_new")) is None


def test_append_failure_rolls_back_event_memory(tmp_path) -> None:
    path = tmp_path / "rollback-event.db"
    key = ("chrn_1", "u_1", "hsess_1")
    with SQLiteStore(path) as store:
        store.put_session(SessionRecord(id=key[2], appName=key[0], userId=key[1]))
        real_put = store._put_record

        def boom(*_a: object, **_k: object) -> None:
            raise OSError("disk full")

        store._put_record = boom  # type: ignore[method-assign]
        with pytest.raises(OSError):
            store.append(_event("evt_x"))
        store._put_record = real_put  # type: ignore[method-assign]
        assert store.read_session(key) == []


def test_put_invocation_failure_rolls_back_with_own_txn(tmp_path) -> None:
    path = tmp_path / "rollback-inv.db"
    with SQLiteStore(path) as store:
        store.put_invocation(
            InvocationRecord(
                id="inv_1", sessionId="hsess_1", appName="chrn_1", turnId="turn_1",
                status="running",
            )
        )
        real_put = store._put_record

        def boom(*_a: object, **_k: object) -> None:
            raise OSError("disk full")

        store._put_record = boom  # type: ignore[method-assign]
        with pytest.raises(OSError):
            store.put_invocation(
                InvocationRecord(
                    id="inv_1", sessionId="hsess_1", appName="chrn_1", turnId="turn_1",
                    status="failed",
                )
            )
        store._put_record = real_put  # type: ignore[method-assign]
        assert store.get_invocation("inv_1").status == "running"


def test_put_turn_failure_rolls_back(tmp_path) -> None:
    path = tmp_path / "rollback-turn.db"
    with SQLiteStore(path) as store:
        store.put_turn(
            TurnRecord(id="turn_1", invocationId="inv_1", sessionId="hsess_1", status="running")
        )
        real_put = store._put_record

        def boom(*_a: object, **_k: object) -> None:
            raise OSError("disk full")

        store._put_record = boom  # type: ignore[method-assign]
        with pytest.raises(OSError):
            store.put_turn(
                TurnRecord(id="turn_1", invocationId="inv_1", sessionId="hsess_1", status="failed")
            )
        store._put_record = real_put  # type: ignore[method-assign]
        assert store.get_turn("turn_1").status == "running"


def test_save_harness_failure_rolls_back(tmp_path) -> None:
    path = tmp_path / "rollback-harness.db"
    with SQLiteStore(path) as store:
        store.save_harness(HarnessRecord(id="chrn_1", name="before", base="codex"))
        real_put = store._put_record

        def boom(*_a: object, **_k: object) -> None:
            raise OSError("disk full")

        store._put_record = boom  # type: ignore[method-assign]
        with pytest.raises(OSError):
            store.save_harness(HarnessRecord(id="chrn_1", name="after", base="codex"))
        store._put_record = real_put  # type: ignore[method-assign]
        assert store.get_harness("chrn_1").name == "before"


def test_save_profile_failure_rolls_back(tmp_path) -> None:
    path = tmp_path / "rollback-profile.db"
    with SQLiteStore(path, clock_ms=lambda: 100) as store:
        store.save_profile(
            ProfileRecord(
                id="hprof_1", harnessId="chrn_1", base="codex", version=1,
                content={"model": "gpt-a"}, profileFingerprint="sha256:a", status="draft",
                tenantId="t", workspaceId="w", createdAtMs=100,
            )
        )
        real_put = store._put_record

        def boom(*_a: object, **_k: object) -> None:
            raise OSError("disk full")

        store._put_record = boom  # type: ignore[method-assign]
        with pytest.raises(OSError):
            store.save_profile(
                ProfileRecord(
                    id="hprof_1", harnessId="chrn_1", base="codex", version=2,
                    content={"model": "gpt-b"}, profileFingerprint="sha256:b", status="draft",
                    tenantId="t", workspaceId="w", createdAtMs=100,
                )
            )
        store._put_record = real_put  # type: ignore[method-assign]
        assert store.get_profile("hprof_1").content == {"model": "gpt-a"}


# --- durable interaction closure / resolution --------------------------------


def test_close_pending_interactions_persists_cancellation(tmp_path) -> None:
    path = tmp_path / "close-interactions.db"
    with SQLiteStore(path) as store:
        store.put_approval(
            ApprovalRecord(id="appr_1", sessionId="hsess_1", invocationId="inv_1", turnId="turn_1")
        )
        store.put_input_request(
            InputRequestRecord(
                id="inreq_1", sessionId="hsess_1", invocationId="inv_1", turnId="turn_1",
                questions=[{"id": "q", "question": "?"}], nativeRequestId=1, adapterGeneration=1,
            )
        )
        approvals, inputs = store.close_pending_interactions("inv_1")
        assert [a.id for a in approvals] == ["appr_1"]
        assert [i.id for i in inputs] == ["inreq_1"]

    with SQLiteStore(path) as reopened:
        assert reopened.get_approval("appr_1").status == "cancelled"
        assert reopened.get_input_request("inreq_1").status == "cancelled"


def test_resolve_input_request_persists_answers(tmp_path) -> None:
    path = tmp_path / "resolve-input.db"
    with SQLiteStore(path) as store:
        store.put_input_request(
            InputRequestRecord(
                id="inreq_1", sessionId="hsess_1", invocationId="inv_1", turnId="turn_1",
                questions=[{"id": "q", "question": "?"}], nativeRequestId=1, adapterGeneration=1,
            )
        )
        resolved = store.resolve_input_request("inreq_1", {"q": "yes"})
        assert resolved.status == "answered"

    with SQLiteStore(path) as reopened:
        assert reopened.get_input_request("inreq_1").answers == {"q": "yes"}


# --- _load quarantine paths --------------------------------------------------


def test_load_skips_unknown_namespace_records(tmp_path) -> None:
    path = tmp_path / "unknown-namespace.db"
    with SQLiteStore(path):
        pass
    connection = sqlite3.connect(path)
    connection.execute(
        "INSERT INTO records(namespace, record_key, payload) VALUES('bogus','k','{}')"
    )
    connection.commit()
    connection.close()

    with SQLiteStore(path) as reopened:
        # Unknown namespace is ignored without raising.
        assert reopened.get_session(("chrn_1", "u_1", "hsess_never")) is None


def test_load_skips_corrupt_payload_without_crashing(tmp_path) -> None:
    path = tmp_path / "corrupt.db"
    with SQLiteStore(path):
        pass
    connection = sqlite3.connect(path)
    # Payload that is valid JSON but not a dict -> _construct_record raises.
    connection.execute(
        "INSERT INTO records(namespace, record_key, payload) "
        "VALUES('session','chrn_1|u_1|hsess_bad','[1,2,3]')"
    )
    # Missing required field -> _construct_record raises ValueError.
    connection.execute(
        "INSERT INTO records(namespace, record_key, payload) "
        "VALUES('session','chrn_1|u_1|hsess_bad2','{}')"
    )
    connection.commit()
    connection.close()

    with SQLiteStore(path) as reopened:
        assert reopened.get_session(("chrn_1", "u_1", "hsess_bad")) is None
        assert reopened.get_session(("chrn_1", "u_1", "hsess_bad2")) is None


def test_load_restores_lease_token_record(tmp_path) -> None:
    path = tmp_path / "lease-token.db"
    key = ("chrn_1", "u_1", "hsess_1")
    with SQLiteStore(path) as store:
        lease = store.acquire_lease(key, "holder-a")
        token = lease.token
    with SQLiteStore(path) as reopened:
        reopened.assert_lease(key, "holder-a", token)
