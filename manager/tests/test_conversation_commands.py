from __future__ import annotations

import sqlite3

import pytest
from coworker.conversation_commands import (
    ConversationCommandConflict,
    ConversationCommandStore,
)


def test_accept_start_is_durable_and_idempotent(tmp_path):
    store = ConversationCommandStore(tmp_path / "conversation-commands.db")

    first = store.accept(
        session_id="session-1",
        client_command_id="cmd-1",
        idempotency_key="idem-1",
        delivery="start_now",
        payload={"text": "synthetic request", "attachments": []},
        busy=False,
    )
    duplicate = store.accept(
        session_id="session-1",
        client_command_id="cmd-2",
        idempotency_key="idem-1",
        delivery="start_now",
        payload={"text": "different text must not replace the original", "attachments": []},
        busy=False,
    )

    assert first.status == "accepted"
    assert first.disposition == "running"
    assert first.turn_id is not None
    assert duplicate.status == "duplicate"
    assert duplicate.disposition == "running"
    assert duplicate.turn_id == first.turn_id
    assert store.payload(first.client_command_id, session_id="session-1") == {
        "text": "synthetic request",
        "attachments": [],
    }


def test_terminal_receipt_is_returned_to_late_duplicate(tmp_path):
    store = ConversationCommandStore(tmp_path / "conversation-commands.db")
    first = store.accept(
        session_id="session-1",
        client_command_id="cmd-1",
        idempotency_key="idem-1",
        delivery="start_now",
        payload={"text": "synthetic request"},
        busy=False,
    )

    store.mark_terminal("session-1", first.turn_id, outcome_ref="outcome-1")
    duplicate = store.accept(
        session_id="session-1",
        client_command_id="cmd-2",
        idempotency_key="idem-1",
        delivery="start_now",
        payload={"text": "ignored retry"},
        busy=False,
    )

    assert duplicate.status == "duplicate"
    assert duplicate.disposition == "terminal"
    assert duplicate.outcome_ref == "outcome-1"


def test_follow_up_queue_is_ordered_and_claimed_transactionally(tmp_path):
    path = tmp_path / "conversation-commands.db"
    store = ConversationCommandStore(path)
    first = store.accept(
        session_id="session-1",
        client_command_id="cmd-1",
        idempotency_key="idem-1",
        delivery="enqueue",
        payload={"text": "first queued", "attachments": []},
        busy=True,
    )
    second = store.accept(
        session_id="session-1",
        client_command_id="cmd-2",
        idempotency_key="idem-2",
        delivery="enqueue",
        payload={"text": "second queued", "attachments": []},
        busy=True,
    )

    assert [item["queueItemId"] for item in store.queue_snapshot("session-1")] == [
        first.queue_item_id,
        second.queue_item_id,
    ]

    claimed = store.claim_next("session-1")
    assert claimed is not None
    receipt, payload = claimed
    assert receipt.client_command_id == "cmd-1"
    assert receipt.disposition == "running"
    assert receipt.turn_id is not None
    assert payload["text"] == "first queued"
    store.mark_terminal("session-1", receipt.turn_id, outcome_ref="outcome-first")

    reopened = ConversationCommandStore(path)
    assert [item["queueItemId"] for item in reopened.queue_snapshot("session-1")] == [
        second.queue_item_id
    ]


def test_restart_pauses_an_uncertain_claim_until_explicit_resume(tmp_path):
    path = tmp_path / "conversation-commands.db"
    store = ConversationCommandStore(path)
    accepted = store.accept(
        session_id="session-1",
        client_command_id="cmd-1",
        idempotency_key="idem-1",
        delivery="enqueue",
        payload={"text": "do not replay blindly"},
        busy=True,
    )
    claimed = store.claim_next("session-1")
    assert claimed is not None
    assert store.queue_snapshot("session-1")[0]["state"] == "dispatching"
    store.close()

    reopened = ConversationCommandStore(path)
    assert reopened.queue_status("session-1") == {
        "paused": True,
        "reason": "restart_uncertain",
    }
    snapshot = reopened.queue_snapshot("session-1")
    assert snapshot[0]["queueItemId"] == accepted.queue_item_id
    assert snapshot[0]["state"] == "queued"
    assert reopened.claim_next("session-1") is None

    reopened.resume_queue("session-1")
    resumed = reopened.claim_next("session-1")
    assert resumed is not None
    assert resumed[0].client_command_id == "cmd-1"


def test_restart_does_not_requeue_a_checkpointed_dispatched_execution(tmp_path):
    path = tmp_path / "conversation-commands.db"
    store = ConversationCommandStore(path)
    accepted = store.accept(
        session_id="session-1",
        client_command_id="cmd-accepted",
        idempotency_key="idem-accepted",
        delivery="enqueue",
        payload={"text": "already accepted by HaaS"},
        busy=True,
    )
    claimed = store.claim_next("session-1")
    assert claimed is not None
    running, _ = claimed
    assert running.turn_id is not None
    assert store.checkpoint_execution(
        "session-1", running.turn_id, execution_ref="inv_accepted"
    )
    store.close()

    reopened = ConversationCommandStore(path)
    pending = reopened.checkpointed_running()
    assert len(pending) == 1
    assert pending[0].execution_ref == "inv_accepted"
    assert reopened.queue_snapshot("session-1")[0]["state"] == "dispatching"
    assert reopened.claim_next("session-1") is None

    assert reopened.mark_execution_terminal(
        "session-1", "inv_accepted", outcome_ref="inv_accepted"
    )
    assert reopened.queue_snapshot("session-1") == []
    receipt = reopened.find_by_idempotency("session-1", "idem-accepted")
    assert receipt is not None and receipt.disposition == "terminal"


def test_uncheckpointed_immediate_command_recovers_as_one_paused_queue_item(tmp_path):
    store = ConversationCommandStore(tmp_path / "conversation-commands.db")
    accepted = store.accept(
        session_id="session-1",
        client_command_id="cmd-immediate",
        idempotency_key="idem-immediate",
        delivery="start_now",
        payload={"text": "preserve this synthetic request", "attachments": []},
        busy=False,
    )
    assert accepted.turn_id is not None
    assert store.uncheckpointed_running() == [
        ("session-1", accepted.turn_id)
    ]

    assert store.recover_uncheckpointed("session-1", accepted.turn_id) is True
    assert store.recover_uncheckpointed("session-1", accepted.turn_id) is False
    recovered = store.find_by_idempotency("session-1", "idem-immediate")
    assert recovered is not None
    assert recovered.disposition == "queued"
    assert recovered.turn_id is None
    assert recovered.queue_item_id is not None
    assert store.queue_status("session-1") == {
        "paused": True,
        "reason": "restart_uncertain",
    }
    snapshot = store.queue_snapshot("session-1")
    assert len(snapshot) == 1
    assert snapshot[0]["queueItemId"] == recovered.queue_item_id
    assert snapshot[0]["safePreview"] == "preserve this synthetic request"
    assert store.claim_next("session-1") is None
    assert store.payload("cmd-immediate", session_id="session-1") == {
        "text": "preserve this synthetic request",
        "attachments": [],
    }


def test_checkpointed_immediate_command_is_not_converted_to_new_work(tmp_path):
    store = ConversationCommandStore(tmp_path / "conversation-commands.db")
    accepted = store.accept(
        session_id="session-1",
        client_command_id="cmd-started",
        idempotency_key="idem-started",
        delivery="start_now",
        payload={"text": "already checkpointed"},
        busy=False,
    )
    assert accepted.turn_id is not None
    assert store.mark_checkpointed("session-1", accepted.turn_id) is True
    assert store.mark_checkpointed("session-1", accepted.turn_id) is False
    assert store.uncheckpointed_running() == []
    assert store.recover_uncheckpointed("session-1", accepted.turn_id) is False
    receipt = store.find_by_idempotency("session-1", "idem-started")
    assert receipt is not None
    assert receipt.disposition == "running"
    assert receipt.turn_id == accepted.turn_id
    assert store.queue_snapshot("session-1") == []


def test_checkpointed_running_command_binds_exact_execution_and_survives_restart(tmp_path):
    path = tmp_path / "conversation-commands.db"
    store = ConversationCommandStore(path)
    accepted = store.accept(
        session_id="session-1",
        client_command_id="cmd-started",
        idempotency_key="idem-started",
        delivery="start_now",
        payload={"text": "continue after a safe restart"},
        busy=False,
    )
    assert accepted.turn_id is not None

    assert store.checkpoint_execution(
        "session-1", accepted.turn_id, execution_ref="inv_exact"
    ) is True
    assert store.checkpoint_execution(
        "session-1", accepted.turn_id, execution_ref="inv_exact"
    ) is False
    with pytest.raises(ConversationCommandConflict):
        store.checkpoint_execution(
            "session-1", accepted.turn_id, execution_ref="inv_other"
        )
    store.close()

    reopened = ConversationCommandStore(path)
    running = reopened.checkpointed_running()
    assert len(running) == 1
    assert running[0].session_id == "session-1"
    assert running[0].turn_id == accepted.turn_id
    assert running[0].execution_ref == "inv_exact"


def test_existing_command_database_adds_nullable_execution_reference(tmp_path):
    path = tmp_path / "conversation-commands.db"
    connection = sqlite3.connect(path)
    connection.execute(
        """
        CREATE TABLE conversation_commands (
            session_id TEXT NOT NULL,
            client_command_id TEXT NOT NULL,
            idempotency_key TEXT NOT NULL,
            status TEXT NOT NULL,
            disposition TEXT NOT NULL,
            turn_id TEXT,
            queue_item_id TEXT,
            outcome_ref TEXT,
            payload_json TEXT NOT NULL,
            checkpointed_at_ms INTEGER,
            created_at_ms INTEGER NOT NULL,
            updated_at_ms INTEGER NOT NULL,
            PRIMARY KEY (session_id, client_command_id),
            UNIQUE (session_id, idempotency_key)
        )
        """
    )
    connection.execute(
        """
        INSERT INTO conversation_commands VALUES (
            'session-1', 'cmd-legacy', 'idem-legacy', 'accepted', 'running',
            'turn-legacy', NULL, NULL, '{}', 100, 90, 100
        )
        """
    )
    connection.commit()
    connection.close()

    store = ConversationCommandStore(path)

    assert store.checkpointed_running()[0].execution_ref is None
    assert store.checkpoint_execution(
        "session-1", "turn-legacy", execution_ref="inv_migrated"
    )
    assert store.checkpointed_running()[0].execution_ref == "inv_migrated"


def test_reconcile_checkpointed_execution_is_idempotent_and_exact(tmp_path):
    store = ConversationCommandStore(tmp_path / "conversation-commands.db")
    first = store.accept(
        session_id="session-1",
        client_command_id="cmd-first",
        idempotency_key="idem-first",
        delivery="start_now",
        payload={"text": "first"},
        busy=False,
    )
    assert first.turn_id is not None
    assert store.checkpoint_execution(
        "session-1", first.turn_id, execution_ref="inv_first"
    )
    assert store.mark_terminal(
        "session-1", first.turn_id, outcome_ref="outcome-first"
    )

    second = store.accept(
        session_id="session-1",
        client_command_id="cmd-second",
        idempotency_key="idem-second",
        delivery="start_now",
        payload={"text": "second"},
        busy=False,
    )
    assert second.turn_id is not None
    assert store.checkpoint_execution(
        "session-1", second.turn_id, execution_ref="inv_second"
    )

    assert store.mark_execution_terminal(
        "session-1", "inv_second", outcome_ref="outcome-inv-second"
    ) is True
    assert store.mark_execution_terminal(
        "session-1", "inv_second", outcome_ref="outcome-inv-second"
    ) is False
    receipt = store.find_by_idempotency("session-1", "idem-second")
    assert receipt is not None
    assert receipt.disposition == "terminal"
    assert receipt.outcome_ref == "outcome-inv-second"
    assert receipt.execution_ref == "inv_second"
    assert store.find_by_idempotency("session-1", "idem-first").outcome_ref == "outcome-first"


def test_queue_mutations_require_the_current_revision(tmp_path):
    store = ConversationCommandStore(tmp_path / "conversation-commands.db")
    receipt = store.accept(
        session_id="session-1",
        client_command_id="cmd-1",
        idempotency_key="idem-1",
        delivery="enqueue",
        payload={"text": "edit me", "attachments": []},
        busy=True,
    )
    assert receipt.queue_item_id is not None

    with pytest.raises(ConversationCommandConflict):
        store.delete_queue_item("session-1", receipt.queue_item_id, expected_revision=2)

    restored = store.restore_queue_item("session-1", receipt.queue_item_id, expected_revision=1)
    assert restored == {"text": "edit me", "attachments": []}
    assert store.queue_snapshot("session-1") == []


def test_queue_delete_renormalizes_remaining_positions_and_revisions(tmp_path):
    store = ConversationCommandStore(tmp_path / "conversation-commands.db")
    first = store.accept(
        session_id="session-1",
        client_command_id="cmd-1",
        idempotency_key="idem-1",
        delivery="enqueue",
        payload={"text": "first"},
        busy=True,
    )
    store.accept(
        session_id="session-1",
        client_command_id="cmd-2",
        idempotency_key="idem-2",
        delivery="enqueue",
        payload={"text": "second"},
        busy=True,
    )
    assert first.queue_item_id is not None

    store.delete_queue_item("session-1", first.queue_item_id, expected_revision=1)

    remaining = store.queue_snapshot("session-1")
    assert remaining[0]["position"] == 1
    assert remaining[0]["revision"] == 2


def test_removing_the_last_item_clears_stale_queue_pause(tmp_path):
    store = ConversationCommandStore(tmp_path / "conversation-commands.db")
    receipt = store.accept(
        session_id="session-1",
        client_command_id="cmd-1",
        idempotency_key="idem-1",
        delivery="enqueue",
        payload={"text": "remove me"},
        busy=True,
    )
    assert receipt.queue_item_id is not None
    store.pause_queue("session-1", "previous_turn_not_completed")

    store.delete_queue_item("session-1", receipt.queue_item_id, expected_revision=1)

    assert store.queue_status("session-1")["paused"] is False


def test_prioritize_queue_item_changes_the_next_claim(tmp_path):
    store = ConversationCommandStore(tmp_path / "conversation-commands.db")
    first = store.accept(
        session_id="session-1",
        client_command_id="cmd-1",
        idempotency_key="idem-1",
        delivery="enqueue",
        payload={"text": "first"},
        busy=True,
    )
    second = store.accept(
        session_id="session-1",
        client_command_id="cmd-2",
        idempotency_key="idem-2",
        delivery="enqueue",
        payload={"text": "second"},
        busy=True,
    )
    assert first.queue_item_id and second.queue_item_id

    store.prioritize_queue_item("session-1", second.queue_item_id, expected_revision=1)
    prioritized = store.queue_snapshot("session-1")
    assert [item["position"] for item in prioritized] == [1, 2]
    assert [item["revision"] for item in prioritized] == [2, 2]
    claimed = store.claim_next("session-1")

    assert claimed is not None
    assert claimed[0].client_command_id == "cmd-2"


def test_send_now_unpauses_a_recovery_queue_and_is_idempotent(tmp_path):
    store = ConversationCommandStore(tmp_path / "conversation-commands.db")
    receipt = store.accept(
        session_id="session-1", client_command_id="cmd-1", idempotency_key="idem-1",
        delivery="enqueue", payload={"text": "run explicitly"}, busy=True,
    )
    assert receipt.queue_item_id is not None
    store.pause_queue("session-1", "restart_uncertain")

    assert store.prioritize_queue_item(
        "session-1", receipt.queue_item_id, expected_revision=1,
        idempotency_key="send-now-1",
    ) is True
    assert store.queue_status("session-1")["paused"] is False
    # Duplicate delivery returns the persisted result but cannot mutate revision again.
    revision = store.queue_snapshot("session-1")[0]["revision"]
    assert store.prioritize_queue_item(
        "session-1", receipt.queue_item_id, expected_revision=1,
        idempotency_key="send-now-1",
    ) is False
    assert store.queue_snapshot("session-1")[0]["revision"] == revision
    claimed = store.claim_next("session-1")
    assert claimed is not None
    assert claimed[0].client_command_id == "cmd-1"


def test_queue_resume_is_idempotent_and_never_requeues_dispatching_work(tmp_path):
    store = ConversationCommandStore(tmp_path / "conversation-commands.db")
    receipt = store.accept(
        session_id="session-1", client_command_id="cmd-1", idempotency_key="idem-1",
        delivery="enqueue", payload={"text": "active"}, busy=True,
    )
    assert receipt.queue_item_id is not None
    claimed = store.claim_next("session-1")
    assert claimed is not None
    before = store.queue_snapshot("session-1")[0]
    store.pause_queue("session-1", "manual")

    assert store.resume_queue("session-1", idempotency_key="resume-1") is True
    store.pause_queue("session-1", "later_failure")
    assert store.resume_queue("session-1", idempotency_key="resume-1") is False
    after = store.queue_snapshot("session-1")[0]
    assert after["state"] == "dispatching"
    assert after["revision"] == before["revision"]
    assert store.queue_status("session-1") == {
        "paused": True,
        "reason": "later_failure",
    }


def test_dispatch_failure_explicitly_restores_claimed_item_before_retry(tmp_path):
    store = ConversationCommandStore(tmp_path / "conversation-commands.db")
    receipt = store.accept(
        session_id="session-1", client_command_id="cmd-1", idempotency_key="idem-1",
        delivery="enqueue", payload={"text": "retry after scheduling failure"}, busy=True,
    )
    assert receipt.queue_item_id is not None
    claimed = store.claim_next("session-1")
    assert claimed is not None

    store.pause_queue(
        "session-1", "dispatch_uncertain", restore_dispatching=True,
    )
    snapshot = store.queue_snapshot("session-1")
    assert snapshot[0]["state"] == "queued"
    assert snapshot[0]["revision"] == 3
    restored = store.find_by_idempotency("session-1", "idem-1")
    assert restored is not None
    assert restored.disposition == "queued"
    assert restored.turn_id is None
    assert store.mark_terminal("session-1", None, outcome_ref="unused") is False


def test_move_queue_item_persists_normalized_order_and_revisions(tmp_path):
    store = ConversationCommandStore(tmp_path / "conversation-commands.db")
    receipts = [
        store.accept(
            session_id="session-1",
            client_command_id=f"cmd-{index}",
            idempotency_key=f"idem-{index}",
            delivery="enqueue",
            payload={
                "text": f"queued {index}",
                "contextRefs": [{"kind": "session", "id": "ctx", "label": "Earlier review"}],
            },
            busy=True,
        )
        for index in range(1, 4)
    ]
    target = receipts[2].queue_item_id
    assert target is not None

    store.move_queue_item(
        "session-1",
        target,
        expected_revision=1,
        target_position=1,
        idempotency_key="mutation-1",
    )
    snapshot = store.queue_snapshot("session-1")

    assert [item["clientCommandId"] for item in snapshot] == ["cmd-3", "cmd-1", "cmd-2"]
    assert [item["position"] for item in snapshot] == [1, 2, 3]
    assert snapshot[0]["revision"] == 2
    assert snapshot[0]["contextCount"] == 1
    assert snapshot[0]["requestedDelivery"] == "enqueue"


def test_queue_mutation_idempotency_reuses_the_original_result(tmp_path):
    store = ConversationCommandStore(tmp_path / "conversation-commands.db")
    receipt = store.accept(
        session_id="session-1",
        client_command_id="cmd-1",
        idempotency_key="idem-1",
        delivery="enqueue",
        payload={"text": "delete once"},
        busy=True,
    )
    assert receipt.queue_item_id is not None

    assert store.delete_queue_item(
        "session-1",
        receipt.queue_item_id,
        expected_revision=1,
        idempotency_key="mutation-delete-1",
    )
    assert store.delete_queue_item(
        "session-1",
        receipt.queue_item_id,
        expected_revision=1,
        idempotency_key="mutation-delete-1",
    )

    with pytest.raises(ConversationCommandConflict):
        store.delete_queue_item(
            "session-1",
            "queue-other",
            expected_revision=1,
            idempotency_key="mutation-delete-1",
        )


def test_command_and_queue_failure_boundaries_are_explicit(tmp_path):
    store = ConversationCommandStore(tmp_path / "conversation-commands.db")
    store.accept(
        session_id="session-1",
        client_command_id="cmd-1",
        idempotency_key="idem-1",
        delivery="start_now",
        payload={"text": "running"},
        busy=False,
    )

    with pytest.raises(ConversationCommandConflict, match="client command id"):
        store.accept(
            session_id="session-1",
            client_command_id="cmd-1",
            idempotency_key="idem-other",
            delivery="start_now",
            payload={"text": "conflict"},
            busy=False,
        )
    with pytest.raises(ConversationCommandConflict, match="session is busy"):
        store.accept(
            session_id="session-1",
            client_command_id="cmd-2",
            idempotency_key="idem-2",
            delivery="start_now",
            payload={"text": "must queue"},
            busy=True,
        )
    with pytest.raises(KeyError):
        store.payload("missing", session_id="session-1")
    assert not store.mark_terminal("session-1", None, outcome_ref="unused")
    assert store.claim_next("empty-session") is None
    with pytest.raises(KeyError):
        store.delete_queue_item("session-1", "missing", expected_revision=1)

    restored = store.accept(
        session_id="session-1",
        client_command_id="cmd-restored",
        idempotency_key="idem-restored",
        delivery="enqueue",
        payload={"text": "restore twice"},
        busy=True,
    )
    assert restored.queue_item_id
    first_restore = store.restore_queue_item(
        "session-1",
        restored.queue_item_id,
        expected_revision=1,
        idempotency_key="restore-mutation",
    )
    duplicate_restore = store.restore_queue_item(
        "session-1",
        restored.queue_item_id,
        expected_revision=1,
        idempotency_key="restore-mutation",
    )
    assert duplicate_restore == first_restore

    prioritized = store.accept(
        session_id="session-1",
        client_command_id="cmd-prioritized",
        idempotency_key="idem-prioritized",
        delivery="enqueue",
        payload={"text": "prioritize twice"},
        busy=True,
    )
    assert prioritized.queue_item_id
    assert store.prioritize_queue_item(
        "session-1",
        prioritized.queue_item_id,
        expected_revision=1,
        idempotency_key="prioritize-mutation",
    )
    assert not store.prioritize_queue_item(
        "session-1",
        prioritized.queue_item_id,
        expected_revision=1,
        idempotency_key="prioritize-mutation",
    )

    moved = store.accept(
        session_id="session-1",
        client_command_id="cmd-moved",
        idempotency_key="idem-moved",
        delivery="enqueue",
        payload={"text": "move twice"},
        busy=True,
    )
    assert moved.queue_item_id
    store.move_queue_item(
        "session-1",
        moved.queue_item_id,
        expected_revision=1,
        target_position=1,
        idempotency_key="move-mutation",
    )
    store.move_queue_item(
        "session-1",
        moved.queue_item_id,
        expected_revision=1,
        target_position=1,
        idempotency_key="move-mutation",
    )


def test_corrupt_stored_payloads_and_mutation_results_fail_closed(tmp_path):
    path = tmp_path / "conversation-commands.db"
    store = ConversationCommandStore(path)
    immediate = store.accept(
        session_id="session-1",
        client_command_id="cmd-immediate",
        idempotency_key="idem-immediate",
        delivery="start_now",
        payload={"text": "synthetic"},
        busy=False,
    )
    store.close()
    with sqlite3.connect(path) as database:
        database.execute(
            "UPDATE conversation_commands SET payload_json = '[]' WHERE client_command_id = ?",
            (immediate.client_command_id,),
        )
    store = ConversationCommandStore(path)
    with pytest.raises(ValueError, match="payload is not an object"):
        store.payload(immediate.client_command_id, session_id="session-1")

    queued = store.accept(
        session_id="session-1",
        client_command_id="cmd-queued",
        idempotency_key="idem-queued",
        delivery="enqueue",
        payload={"text": "synthetic queued"},
        busy=True,
    )
    assert queued.queue_item_id is not None
    store.delete_queue_item(
        "session-1",
        queued.queue_item_id,
        expected_revision=1,
        idempotency_key="delete-corrupt-result",
    )
    store.close()
    with sqlite3.connect(path) as database:
        database.execute(
            "UPDATE conversation_queue_mutations SET result_json = '[]' WHERE idempotency_key = ?",
            ("delete-corrupt-result",),
        )
    store = ConversationCommandStore(path)
    with pytest.raises(ValueError, match="mutation result is not an object"):
        store.delete_queue_item(
            "session-1",
            queued.queue_item_id,
            expected_revision=1,
            idempotency_key="delete-corrupt-result",
        )


def test_delete_session_removes_command_receipts_and_queue(tmp_path):
    store = ConversationCommandStore(tmp_path / "conversation-commands.db")
    store.accept(
        session_id="session-1",
        client_command_id="cmd-1",
        idempotency_key="idem-1",
        delivery="enqueue",
        payload={"text": "queued"},
        busy=True,
    )

    store.delete_session("session-1")

    assert store.queue_snapshot("session-1") == []
    assert store.find_by_idempotency("session-1", "idem-1") is None
