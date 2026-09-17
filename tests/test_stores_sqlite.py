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
