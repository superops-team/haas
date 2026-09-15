"""Delegated-session desired/applied policy reconciliation."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import Protocol

from haas.stores import DelegatedSessionRecord


class DelegatedSessionStore(Protocol):
    def get_delegated_session(self, delegated_session_id: str) -> DelegatedSessionRecord | None: ...

    def put_delegated_session(self, record: DelegatedSessionRecord) -> DelegatedSessionRecord: ...


BUSY_RUNTIME_STATUSES = frozenset({"running", "restoring", "cancelling"})


def reconcile_delegated_policy(
    store: DelegatedSessionStore,
    delegated_session_id: str,
    *,
    apply: Callable[[DelegatedSessionRecord, dict[str, object]], None] | None = None,
) -> DelegatedSessionRecord | None:
    """Apply a pending desired revision once its runtime is quiescent.

    Runtime teardown belongs to the container lifecycle, not policy admission.
    A running invocation therefore keeps its current runtime and applied
    revision until a later reconciliation observes a safe state.
    """
    record = store.get_delegated_session(delegated_session_id)
    if record is None or record.pendingPolicyUpdate is None:
        return record
    if record.runtime.status in BUSY_RUNTIME_STATUSES:
        return record

    pending = record.pendingPolicyUpdate
    target = record.pendingPolicyTarget
    if target is None:
        result = {
            "updateId": pending["updateId"],
            "revision": pending["revision"],
            "status": "failed",
            "code": "haas_internal_error",
            "safeReason": "policy_target_missing",
        }
        return store.put_delegated_session(
            replace(record, pendingPolicyUpdate=None, lastPolicyUpdateResult=result)
        )
    try:
        if apply is not None:
            apply(record, target)
    except Exception:
        result = {
            "updateId": pending["updateId"],
            "revision": pending["revision"],
            "status": "failed",
            "code": "haas_internal_error",
            "safeReason": "policy_apply_failed",
        }
        return store.put_delegated_session(
            replace(
                record,
                pendingPolicyUpdate=None,
                pendingPolicyTarget=None,
                lastPolicyUpdateResult=result,
            )
        )
    result = {
        "updateId": pending["updateId"],
        "revision": pending["revision"],
        "status": "applied",
    }
    return store.put_delegated_session(
        replace(
            record,
            profileRef=dict(target["profileRef"]),
            delegationPolicySnapshot=dict(target["delegationPolicySnapshot"]),
            mountManifest=dict(target["mountManifest"]),
            image=dict(target["image"]),
            appliedRevision=record.desiredRevision,
            pendingPolicyUpdate=None,
            pendingPolicyTarget=None,
            lastPolicyUpdateResult=result,
        )
    )
