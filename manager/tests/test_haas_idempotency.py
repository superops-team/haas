from __future__ import annotations

import json

import pytest
from coworker.haas.attempts import AttemptLedger, ExpiryEvidence


def _ledger() -> AttemptLedger:
    ledger = AttemptLedger()
    ledger.add(
        manager_turn_id="turn_1",
        attempt_id="attempt_1",
        idempotency_key="mgr-turn:session_1:1:attempt_1",
        invocation_id="inv_1",
        server_expires_at_ms=86_400_000,
        terminal=True,
    )
    return ledger


@pytest.mark.parametrize(
    "evidence",
    [ExpiryEvidence.NOT_FOUND, ExpiryEvidence.NETWORK, ExpiryEvidence.CURSOR_EXPIRED],
)
def test_only_explicit_server_expiry_can_create_a_linked_attempt(
    evidence: ExpiryEvidence,
) -> None:
    ledger = _ledger()
    ledger.observe_failure("attempt_1", evidence)

    assert ledger.linked_attempt_for_use(
        "attempt_1",
        new_attempt_id="attempt_2",
        new_idempotency_key="mgr-turn:session_1:1:attempt_2",
    ) is None


def test_confirmed_expiry_creates_one_linked_attempt_and_restart_reuses_it() -> None:
    ledger = _ledger()
    ledger.observe_failure("attempt_1", ExpiryEvidence.SERVER_CONFIRMED)

    linked = ledger.linked_attempt_for_use(
        "attempt_1",
        new_attempt_id="attempt_2",
        new_idempotency_key="mgr-turn:session_1:1:attempt_2",
    )
    assert linked is not None
    assert linked.predecessor_invocation_id == "inv_1"
    assert linked.attempt_id == "attempt_2"

    restored = AttemptLedger.from_dict(json.loads(json.dumps(ledger.to_dict())))
    reused = restored.linked_attempt_for_use(
        "attempt_1",
        new_attempt_id="attempt_unwanted",
        new_idempotency_key="mgr-turn:session_1:1:attempt_unwanted",
    )
    assert reused is not None
    assert reused.attempt_id == "attempt_2"
    assert len(restored.attempts) == 2


def test_local_clock_passing_expiry_does_not_confirm_expiry() -> None:
    ledger = _ledger()
    assert not ledger.confirm_expiry_from_clock("attempt_1", now_ms=86_400_001)
    assert ledger.linked_attempt_for_use(
        "attempt_1",
        new_attempt_id="attempt_2",
        new_idempotency_key="mgr-turn:session_1:1:attempt_2",
    ) is None
