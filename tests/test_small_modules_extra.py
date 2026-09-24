"""Coverage for artifact path validation, structured logger redaction branches,
and delegated-session policy reconciliation."""

from __future__ import annotations

import pytest

from haas.artifacts.models import ArtifactPolicy
from haas.artifacts.paths import ArtifactPathRejected, safe_relative_path
from haas.observability.logger import StructuredLogger
from haas.runtime.reconciler import reconcile_delegated_policy
from haas.stores import DelegatedSessionRecord, MemoryStore


# --- artifact paths ----------------------------------------------------------


def test_safe_relative_path_rejects_empty_and_nul() -> None:
    policy = ArtifactPolicy()
    with pytest.raises(ArtifactPathRejected, match="empty_or_nul_path"):
        safe_relative_path("", policy)
    with pytest.raises(ArtifactPathRejected, match="empty_or_nul_path"):
        safe_relative_path("a\x00b", policy)


@pytest.mark.parametrize("bad_root", ["", "/absolute", "../escape"])
def test_safe_relative_path_rejects_invalid_policy_roots(bad_root: str) -> None:
    policy = ArtifactPolicy(includeRoots=[bad_root])
    with pytest.raises(ArtifactPathRejected, match="invalid_policy_root"):
        safe_relative_path("output/file.txt", policy)


# --- structured logger -------------------------------------------------------


def test_logger_skips_redaction_when_disabled() -> None:
    logger = StructuredLogger(redact_output=False)
    fields = {"api_key": "sk-real", "n": 1}  # haas-secret-ignore
    assert logger.event("test", fields)["api_key"] == "sk-real"


def test_logger_log_returns_serialized_json() -> None:
    logger = StructuredLogger(redact_output=False)
    out = logger.log("request", {"count": 2})
    assert '"event":"request"' in out.replace(" ", "")


def test_logger_keeps_payload_when_redact_returns_non_dict(monkeypatch) -> None:
    from haas.observability import logger as logger_module

    monkeypatch.setattr(logger_module, "redact", lambda _payload: "scrubbed")
    logger = StructuredLogger(redact_output=True)
    assert logger.event("x", {"a": 1}) == {"event": "x", "a": 1}


# --- reconciler ----------------------------------------------------------------


def _delegated(**over) -> DelegatedSessionRecord:
    base = dict(
        id="ds_1",
        managerSessionId="mgr_1",
        haasSessionId="hsess_1",
        haasUserId="u_1",
        harnessId="chrn_1",
        image={"variant": "lite"},
        provider={"name": "openai"},
        mountManifest={},
        delegationPolicySnapshot={},
    )
    base.update(over)
    return DelegatedSessionRecord(**base)


def test_reconcile_returns_record_when_no_pending_update() -> None:
    store = MemoryStore()
    record = _delegated()
    store.put_delegated_session(record)
    out = reconcile_delegated_policy(store, "ds_1")
    assert out.id == "ds_1"


def test_reconcile_returns_record_when_runtime_busy() -> None:
    store = MemoryStore()
    record = _delegated(
        pendingPolicyUpdate={"updateId": "u1", "revision": 2},
        pendingPolicyTarget={"profileRef": {}, "delegationPolicySnapshot": {}, "mountManifest": {}, "image": {}},
        desiredRevision=2,
    )
    from dataclasses import replace

    record = replace(record, runtime=replace(record.runtime, status="running"))
    store.put_delegated_session(record)
    out = reconcile_delegated_policy(store, "ds_1")
    assert out.pendingPolicyUpdate is not None


def test_reconcile_fails_when_target_missing() -> None:
    store = MemoryStore()
    record = _delegated(pendingPolicyUpdate={"updateId": "u1", "revision": 2}, pendingPolicyTarget=None)
    store.put_delegated_session(record)
    out = reconcile_delegated_policy(store, "ds_1")
    assert out.lastPolicyUpdateResult["status"] == "failed"
    assert out.pendingPolicyUpdate is None


def test_reconcile_records_apply_failure() -> None:
    store = MemoryStore()
    target = {
        "profileRef": {"profileId": "p1"},
        "delegationPolicySnapshot": {},
        "mountManifest": {},
        "image": {},
    }
    record = _delegated(pendingPolicyUpdate={"updateId": "u1", "revision": 2}, pendingPolicyTarget=target)
    store.put_delegated_session(record)

    def boom(_record, _target):
        raise RuntimeError("apply exploded")

    out = reconcile_delegated_policy(store, "ds_1", apply=boom)
    assert out.lastPolicyUpdateResult["status"] == "failed"
    assert out.pendingPolicyTarget is None


def test_reconcile_applies_target_when_quiescent() -> None:
    store = MemoryStore()
    target = {
        "profileRef": {"profileId": "p1"},
        "delegationPolicySnapshot": {"a": 1},
        "mountManifest": {"m": 1},
        "image": {"variant": "aio"},
    }
    record = _delegated(
        pendingPolicyUpdate={"updateId": "u1", "revision": 2},
        pendingPolicyTarget=target,
        desiredRevision=2,
    )
    store.put_delegated_session(record)
    applied = []

    def apply(_record, t):
        applied.append(t)

    out = reconcile_delegated_policy(store, "ds_1", apply=apply)
    assert applied == [target]
    assert out.lastPolicyUpdateResult["status"] == "applied"
    assert out.appliedRevision == 2
    assert out.image == {"variant": "aio"}
