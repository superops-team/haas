"""Structured JSON logger with redaction (specs/observability/README.md §4)."""
from __future__ import annotations

import json
from typing import Any

from haas.security.redact import redact


class StructuredLogger:
    """Emits one-line JSON events; secrets are redacted before serialization."""

    def __init__(self, *, redact_output: bool = True) -> None:
        self._redact_output = redact_output

    def event(self, name: str, fields: dict[str, Any]) -> dict[str, Any]:
        payload: dict[str, Any] = {"event": name, **fields}
        if self._redact_output:
            redacted = redact(payload)
            if isinstance(redacted, dict):
                payload = redacted
        return payload

    def log(self, name: str, fields: dict[str, Any]) -> str:
        return json.dumps(self.event(name, fields), default=str)
