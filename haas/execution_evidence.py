"""Short-lived, in-memory command evidence with value-aware redaction."""

from __future__ import annotations

import re
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, replace
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from haas.security.redact import REDACTED, RedactionContext, redact

MAX_EVIDENCE_BYTES = 8 << 20
DEFAULT_EVIDENCE_TTL_MS = 15 * 60 * 1000
_URL_RE = re.compile(r"https?://[^\s<>\"']+")
_AUTH_VALUE_RE = re.compile(
    r"(?i)([\"']?\b(?:authorization|cookie)[\"']?\s*[:=]\s*)"
    r"(?:\"[^\"]*\"|'[^']*'|(?:Bearer\s+|Basic\s+)?[^\s,;&\"']+)"
)
_ENV_SECRET_RE = re.compile(
    r"(?i)(\b(?:password|passwd|pwd|client[_-]?secret|api[_-]?key|apikey|"
    r"access[_-]?(?:token|key)|secret[_-]?key|private[_-]?key|auth[_-]?token|"
    r"session[_-]?token|refresh[_-]?token)\b\s*=\s*)(?:'[^']*'|\"[^\"]*\"|[^\s;&]+)"
)
_SECRET_QUERY_KEYS = {
    "api_key", "apikey", "access_token", "auth_token", "password",
    "client_secret", "secret", "signature", "sig", "token",
    "x-amz-signature", "x-amz-credential", "x-amz-security-token",
    "x-goog-signature", "x-goog-credential", "x-oss-signature",
}
_AUTH_REJECT_QUERY_KEYS = _SECRET_QUERY_KEYS - {"signature", "sig", "token"}
_AUTH_QUERY_KEYS = {"code", "state", "client_id", "redirect_uri", "device_code"}
_EXPIRY_QUERY_KEYS = {"exp", "expires", "expires_at", "expiry"}


def _authorization_expiry_ms(url: str, now_ms: int) -> int | None:
    parts = urlsplit(url)
    if parts.scheme != "https":
        return None
    query = {key.lower(): value for key, value in parse_qsl(parts.query, keep_blank_values=True)}
    path_hint = any(word in parts.path.lower() for word in ("auth", "oauth", "login", "device"))
    if not path_hint and not (_AUTH_QUERY_KEYS & query.keys()):
        return None
    for key in _EXPIRY_QUERY_KEYS:
        try:
            raw = int(query.get(key, ""))
        except ValueError:
            continue
        expiry_ms = raw if raw > 10_000_000_000 else raw * 1000
        if expiry_ms > 0:
            return expiry_ms
    return None


def _is_authorization_url(url: str) -> bool:
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        return False
    query_keys = {key.lower() for key, _ in parse_qsl(parts.query, keep_blank_values=True)}
    if _AUTH_REJECT_QUERY_KEYS & query_keys:
        return False
    path_hint = any(word in parts.path.lower() for word in ("auth", "oauth", "login", "device"))
    oauth_query = "state" in query_keys and bool(
        {"client_id", "redirect_uri", "code"} & query_keys
    )
    return path_hint or oauth_query


def _redact_url_query(url: str) -> str:
    parts = urlsplit(url)
    pairs = [
        (key, REDACTED if key.lower() in _SECRET_QUERY_KEYS else value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
    ]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(pairs), parts.fragment))


def redact_execution_evidence_text(value: str, *, now_ms: int | None = None) -> str:
    """Mask credential values while preserving complete HTTP(S) URLs."""
    secret_marker = "__HAAS_EVIDENCE_SECRET__"

    def redact_fragment(fragment: str) -> str:
        fragment = _AUTH_VALUE_RE.sub(lambda m: f"{m.group(1)}{secret_marker}", fragment)
        fragment = _ENV_SECRET_RE.sub(lambda m: f"{m.group(1)}{secret_marker}", fragment)
        safe = redact(fragment, RedactionContext(redact_host_path=False))
        text = safe if isinstance(safe, str) else str(safe)
        return text.replace(secret_marker, REDACTED)

    parts: list[str] = []
    cursor = 0
    for match in _URL_RE.finditer(value):
        parts.append(redact_fragment(value[cursor:match.start()]))
        url = match.group(0)
        parts.append(url if _is_authorization_url(url) else _redact_url_query(url))
        cursor = match.end()
    parts.append(redact_fragment(value[cursor:]))
    return "".join(parts)


def _bounded(value: str, max_bytes: int = MAX_EVIDENCE_BYTES) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= max_bytes:
        return value
    marker = b"\n...<truncated>\n"
    head = (max_bytes - len(marker)) // 2
    tail = max_bytes - len(marker) - head
    return (
        encoded[:head].decode("utf-8", errors="ignore")
        + marker.decode()
        + encoded[-tail:].decode("utf-8", errors="ignore")
    )


@dataclass(frozen=True, slots=True)
class ExecutionEvidenceRecord:
    evidenceRef: str
    principalId: str
    appName: str
    userId: str
    sessionId: str
    invocationId: str
    toolCallId: str
    command: str
    workingDirectory: str
    output: str
    outputStream: str
    links: tuple[dict[str, object], ...]
    expiresAtMs: int

    def public(self) -> dict[str, object]:
        return {
            "evidenceRef": self.evidenceRef,
            "sessionId": self.sessionId,
            "invocationId": self.invocationId,
            "toolCallId": self.toolCallId,
            "command": self.command,
            "workingDirectory": self.workingDirectory,
            "output": self.output,
            "outputStream": self.outputStream,
            "links": [dict(link) for link in self.links],
            "expiresAtMs": self.expiresAtMs,
        }


class ExecutionEvidenceStore:
    """Process-local store; records are never serialized by this class."""

    def __init__(self, *, clock_ms: Callable[[], int] | None = None) -> None:
        self._clock_ms = clock_ms or (lambda: int(time.time() * 1000))
        self._records: dict[str, ExecutionEvidenceRecord] = {}

    def _purge_expired_content(self) -> None:
        now = self._clock_ms()
        for evidence_ref, record in tuple(self._records.items()):
            if record.expiresAtMs > now or not (
                record.command or record.workingDirectory or record.output or record.links
            ):
                continue
            # Keep only scope metadata so an authorized caller gets the stable 410
            # contract without retaining the expired command, output or signed URLs.
            self._records[evidence_ref] = replace(
                record, command="", workingDirectory="", output="", links=(),
            )

    def put(
        self,
        *,
        principal_id: str,
        app_name: str,
        user_id: str,
        session_id: str,
        invocation_id: str,
        tool_call_id: str,
        command: str,
        working_directory: str,
        output: str = "",
        evidence_ref: str | None = None,
        expires_at_ms: int | None = None,
    ) -> ExecutionEvidenceRecord:
        self._purge_expired_content()
        now = self._clock_ms()
        raw_urls = _URL_RE.findall(f"{command}\n{output}")[:32]
        auth_expiries = [
            expiry
            for url in raw_urls
            if (expiry := _authorization_expiry_ms(url, now))
        ]
        expires = min(
            [
                expires_at_ms or now + DEFAULT_EVIDENCE_TTL_MS,
                now + DEFAULT_EVIDENCE_TTL_MS,
                *auth_expiries,
            ]
        )
        safe_command = redact_execution_evidence_text(command, now_ms=now)
        safe_working_directory = redact_execution_evidence_text(working_directory, now_ms=now)
        safe_output = _bounded(redact_execution_evidence_text(output, now_ms=now))
        links = tuple(
            {
                "url": url if _is_authorization_url(url) else _redact_url_query(url),
                "kind": "authorization" if _is_authorization_url(url) else "ordinary",
                "expiresAtMs": (
                    _authorization_expiry_ms(url, now) or expires
                )
                if _is_authorization_url(url)
                else None,
            }
            for url in raw_urls
        )
        record = ExecutionEvidenceRecord(
            evidenceRef=evidence_ref or f"evd_{uuid.uuid4().hex[:24]}",
            principalId=principal_id,
            appName=app_name,
            userId=user_id,
            sessionId=session_id,
            invocationId=invocation_id,
            toolCallId=tool_call_id,
            command=safe_command,
            workingDirectory=safe_working_directory,
            output=safe_output,
            outputStream="combined",
            links=links,
            expiresAtMs=expires,
        )
        self._records[record.evidenceRef] = record
        return record

    def get(self, evidence_ref: str) -> ExecutionEvidenceRecord | None:
        self._purge_expired_content()
        return self._records.get(evidence_ref)

    def expired(self, record: ExecutionEvidenceRecord) -> bool:
        return record.expiresAtMs <= self._clock_ms()

    def update_output(self, evidence_ref: str, output: str) -> ExecutionEvidenceRecord | None:
        self._purge_expired_content()
        record = self._records.get(evidence_ref)
        if record is None or self.expired(record):
            return None
        now = self._clock_ms()
        raw_urls = _URL_RE.findall(f"{record.command}\n{output}")[:32]
        auth_expiries = [
            expiry for url in raw_urls
            if (expiry := _authorization_expiry_ms(url, now))
        ]
        expires = min([record.expiresAtMs, *auth_expiries])
        links = tuple(
            {
                "url": url if _is_authorization_url(url) else _redact_url_query(url),
                "kind": "authorization" if _is_authorization_url(url) else "ordinary",
                "expiresAtMs": (_authorization_expiry_ms(url, now) or expires)
                if _is_authorization_url(url) else None,
            }
            for url in raw_urls
        )
        updated = replace(
            record,
            output=_bounded(redact_execution_evidence_text(output, now_ms=now)),
            links=links,
            expiresAtMs=expires,
        )
        self._records[evidence_ref] = updated
        return updated

    def delete_session(self, session_id: str) -> None:
        self._records = {k: v for k, v in self._records.items() if v.sessionId != session_id}
