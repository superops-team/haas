"""Event Log & SSE: canonical event persistence and ADK projection."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any, cast

from haas.security import redact
from haas.stores import CanonicalEventRecord, MemoryStore

HEARTBEAT_FRAME = ": keep-alive\n\n"


@dataclass
class EventLog:
    store: MemoryStore
    _event_seq: int = 0

    def append(
        self,
        *,
        invocation_id: str,
        session_id: str,
        turn_id: str,
        harness_id: str,
        adapter_id: str,
        author: str,
        content: dict[str, Any],
        actions: dict[str, Any],
    ) -> CanonicalEventRecord:
        existing = self.store.read_invocation(invocation_id, after=-1)
        sequence = len(existing)
        event = CanonicalEventRecord(
            eventId=f"evt_{self._event_seq:013d}",
            invocationId=invocation_id,
            sessionId=session_id,
            turnId=turn_id,
            harnessId=harness_id,
            adapterId=adapter_id,
            author=author,
            sequenceNumber=sequence,
            content=cast(dict[str, Any], redact(content)),
            actions=cast(dict[str, Any], redact(actions)),
            redactionApplied=True,
        )
        self._event_seq += 1
        self.store.append(event)
        return event

    def read_invocation(self, invocation_id: str) -> list[CanonicalEventRecord]:
        return self.store.read_invocation(invocation_id, after=-1)

    def read_session(
        self, session_id: str, after_event_id: str | None = None
    ) -> list[CanonicalEventRecord]:
        return self.store.read_session(session_id, after_cursor=after_event_id)

    def project_adk(self, event: CanonicalEventRecord) -> dict[str, Any]:
        return {
            "id": event.eventId,
            "invocationId": event.invocationId,
            "author": event.author,
            "timestamp": event.observedAtMs / 1000.0,
            "content": event.content,
            "actions": event.actions,
            "longRunningToolIds": [],
        }

    def sse_frame(self, event: CanonicalEventRecord) -> str:
        return f"data: {json.dumps(self.project_adk(event), separators=(',', ':'))}\n\n"

    def haas_frame(self, event: CanonicalEventRecord) -> str:
        payload = asdict(event)
        return f"data: {json.dumps(payload, separators=(',', ':'))}\n\n"
