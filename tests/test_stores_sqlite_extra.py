"""Atomicity, rollback and load-path coverage for the SQLite store.

Focuses on the write-failure rollback helpers, nested transactions, durable
write paths that the round-trip tests do not exercise (close_pending_interactions,
resolve_input_request), and the ``_load`` quarantine paths. Uses a temp DB and
monkeypatched ``_put_record`` for fault injection; no real network or provider.
"""

from __future__ import annotations

import sqlite3

import pytest

from haas.stores import (
    ApprovalRecord,
    CanonicalEventRecord,
    HarnessRecord,
    InputRequestRecord,
    InvocationRecord,
    ProfileRecord,
    SessionRecord,
    SQLiteStore,
    TurnRecord,
)


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
