"""Serializable linked-attempt state for confirmed HaaS replay expiry."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ExpiryEvidence(str, Enum):
    SERVER_CONFIRMED = "server_confirmed"
    NOT_FOUND = "not_found"
    NETWORK = "network"
    CURSOR_EXPIRED = "cursor_expired"


@dataclass(slots=True)
class AttemptRecord:
    manager_turn_id: str
    attempt_id: str
    idempotency_key: str
    invocation_id: str | None = None
    predecessor_invocation_id: str | None = None
    server_expires_at_ms: int | None = None
    terminal: bool = False
    expiry_confirmed: bool = False
    linked_attempt_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "managerTurnId": self.manager_turn_id,
            "attemptId": self.attempt_id,
            "idempotencyKey": self.idempotency_key,
            "invocationId": self.invocation_id,
            "predecessorInvocationId": self.predecessor_invocation_id,
            "serverExpiresAtMs": self.server_expires_at_ms,
            "terminal": self.terminal,
            "expiryConfirmed": self.expiry_confirmed,
            "linkedAttemptId": self.linked_attempt_id,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> AttemptRecord:
        return cls(
            manager_turn_id=str(value["managerTurnId"]),
            attempt_id=str(value["attemptId"]),
            idempotency_key=str(value["idempotencyKey"]),
            invocation_id=value.get("invocationId"),
            predecessor_invocation_id=value.get("predecessorInvocationId"),
            server_expires_at_ms=value.get("serverExpiresAtMs"),
            terminal=bool(value.get("terminal", False)),
            expiry_confirmed=bool(value.get("expiryConfirmed", False)),
            linked_attempt_id=value.get("linkedAttemptId"),
        )


@dataclass(slots=True)
class AttemptLedger:
    attempts: dict[str, AttemptRecord] = field(default_factory=dict)

    def add(
        self,
        *,
        manager_turn_id: str,
        attempt_id: str,
        idempotency_key: str,
        invocation_id: str | None = None,
        predecessor_invocation_id: str | None = None,
        server_expires_at_ms: int | None = None,
        terminal: bool = False,
    ) -> AttemptRecord:
        if attempt_id in self.attempts:
            raise ValueError(f"attempt already exists: {attempt_id}")
        record = AttemptRecord(
            manager_turn_id=manager_turn_id,
            attempt_id=attempt_id,
            idempotency_key=idempotency_key,
            invocation_id=invocation_id,
            predecessor_invocation_id=predecessor_invocation_id,
            server_expires_at_ms=server_expires_at_ms,
            terminal=terminal,
        )
        self.attempts[attempt_id] = record
        return record

    def observe_failure(self, attempt_id: str, evidence: ExpiryEvidence) -> None:
        record = self._get(attempt_id)
        if evidence is ExpiryEvidence.SERVER_CONFIRMED:
            record.expiry_confirmed = True

    def confirm_expiry_from_clock(self, attempt_id: str, *, now_ms: int) -> bool:
        """A local clock is never authoritative for server replay expiry."""
        self._get(attempt_id)
        del now_ms
        return False

    def linked_attempt_for_use(
        self,
        expired_attempt_id: str,
        *,
        new_attempt_id: str,
        new_idempotency_key: str,
    ) -> AttemptRecord | None:
        predecessor = self._get(expired_attempt_id)
        if not predecessor.expiry_confirmed or not predecessor.terminal:
            return None
        if predecessor.linked_attempt_id is not None:
            return self._get(predecessor.linked_attempt_id)
        if predecessor.invocation_id is None:
            raise ValueError("confirmed expired attempt has no invocation id")
        linked = self.add(
            manager_turn_id=predecessor.manager_turn_id,
            attempt_id=new_attempt_id,
            idempotency_key=new_idempotency_key,
            predecessor_invocation_id=predecessor.invocation_id,
        )
        predecessor.linked_attempt_id = linked.attempt_id
        return linked

    def _get(self, attempt_id: str) -> AttemptRecord:
        try:
            return self.attempts[attempt_id]
        except KeyError as exc:
            raise KeyError(f"unknown attempt: {attempt_id}") from exc

    def to_dict(self) -> dict[str, Any]:
        return {"attempts": [self.attempts[key].to_dict() for key in sorted(self.attempts)]}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> AttemptLedger:
        records = [AttemptRecord.from_dict(item) for item in value.get("attempts", [])]
        return cls(attempts={record.attempt_id: record for record in records})
