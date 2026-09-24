"""Security Boundary primitives (specs/security-boundary/README.md §5/§8).

The secret regex table is shared with the pre-commit scanner via
``haas/security/patterns.json`` (single source of truth).
"""

from __future__ import annotations

import ipaddress
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

_PATTERNS_FILE = Path(__file__).with_name("patterns.json")

REDACTED = "[REDACTED]"
REDACTED_URL = "[REDACTED_URL]"
REDACTED_PATH = "[REDACTED_PATH]"


def _load_patterns() -> tuple[list[tuple[str, re.Pattern[str]]], re.Pattern[str]]:
    with open(_PATTERNS_FILE, encoding="utf-8") as fh:
        data = json.load(fh)

    token_patterns = [
        (item["name"], re.compile(item["pattern"])) for item in data["token_patterns"]
    ]

    generic_source: str = data["generic_key"]
    suffix = r"\s*[:=]"
    field_source = (
        generic_source[: -len(suffix)] if generic_source.endswith(suffix) else generic_source
    )
    credential_field = re.compile(field_source)

    return token_patterns, credential_field


_TOKEN_PATTERNS, _CREDENTIAL_FIELD = _load_patterns()
_INLINE_CREDENTIAL_VALUE = re.compile(
    r"(?i)(\b(?:password|passwd|pwd|client[_-]?secret|api[_-]?key|apikey|"
    r"access[_-]?(?:token|key)|secret[_-]?key|private[_-]?key|auth[_-]?token|"
    r"session[_-]?token|refresh[_-]?token)\b\s*[:=]\s*)([\"']?)"
    r"([^\s,;&\"'}\]]+)(\2)"
)

# Host filesystem paths are redacted, but URL paths (``scheme://host/...``) are
# application surface, not bind-mounts. The trailing ``/`` in the lookbehind
# excludes the ``//host`` authority separator so ordinary URLs are untouched
# while standalone host paths (``/workspace/tmp/out.json``) still match.
_ABSOLUTE_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9_./])/(?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]*\b"
)

_HEADER_FIELD_NAMES = frozenset({"authorization", "proxy-authorization", "cookie", "set-cookie"})


def _is_secret_field(key: str) -> bool:
    lowered = key.lower()
    return lowered in _HEADER_FIELD_NAMES or bool(_CREDENTIAL_FIELD.search(key))


@dataclass(frozen=True)
class RedactionContext:
    """Flags controlling redaction of content beyond credentials/URLs."""

    trace_content: bool = False
    redact_host_path: bool = True


class SecretSurfaceError(Exception):
    """A value destined for a public surface still contains secret material."""


class ArtifactPathTraversalError(Exception):
    """Artifact path escapes the container root."""


@dataclass(frozen=True)
class UrlPolicy:
    allowed_schemes: frozenset[str] = frozenset({"https"})
    allowed_hosts: frozenset[str] = frozenset()
    allow_loopback_http: bool = True
    allow_private_network: bool = False


@dataclass(frozen=True)
class UrlDecision:
    allowed: bool
    safeReason: str | None = None


def redact(value: object, context: RedactionContext | None = None) -> object:
    """Recursively redact credentials, presigned URLs and host paths."""
    ctx = context or RedactionContext()
    if isinstance(value, str):
        return _redact_text(value, ctx)
    if isinstance(value, dict):
        out: dict[Any, Any] = {}
        for key, item in value.items():
            if isinstance(key, str) and _is_secret_field(key):
                out[key] = REDACTED
            else:
                out[key] = redact(item, ctx)
        return out
    if isinstance(value, (list, tuple)):
        return [redact(item, ctx) for item in value]
    return value


def _redact_text(text: str, ctx: RedactionContext) -> str:
    out = _INLINE_CREDENTIAL_VALUE.sub(
        lambda match: f"{match.group(1)}{match.group(2)}{REDACTED}{match.group(4)}",
        text,
    )
    for name, regex in _TOKEN_PATTERNS:
        replacement = REDACTED_URL if name == "presigned_url" else REDACTED
        out = regex.sub(replacement, out)
    if ctx.redact_host_path:
        out = _ABSOLUTE_PATH_RE.sub(REDACTED_PATH, out)
    return out


def validate_url(url: str, policy: UrlPolicy | None = None) -> UrlDecision:
    """Reject caller-controlled URLs unless they match the allowlist (SSRF).

    Default policy allows ``https`` to public hosts and ``http`` to loopback
    only. Hostnames require an explicit allowlist entry (no DNS resolution in
    this offline primitive).
    """
    policy = policy or UrlPolicy()
    parts = urlsplit(url)

    host = parts.hostname
    if not host:
        return UrlDecision(False, "host_not_allowed")

    if host in policy.allowed_hosts:
        allowed = parts.scheme in policy.allowed_schemes
        return UrlDecision(allowed, None if allowed else "scheme_not_allowed")

    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        # Non-allowlisted hostname: no DNS resolution offline.
        if parts.scheme not in policy.allowed_schemes:
            return UrlDecision(False, "scheme_not_allowed")
        return UrlDecision(False, "host_not_allowed")

    if ip.is_loopback:
        if parts.scheme == "http":
            allowed = policy.allow_loopback_http
            return UrlDecision(allowed, None if allowed else "loopback_not_allowed")
        if parts.scheme in policy.allowed_schemes:
            return UrlDecision(True)
        return UrlDecision(False, "scheme_not_allowed")

    if ip.is_private or ip.is_link_local or ip.is_reserved or ip.is_multicast:
        return UrlDecision(
            policy.allow_private_network,
            None if policy.allow_private_network else "private_network_not_allowed",
        )

    if parts.scheme not in policy.allowed_schemes:
        return UrlDecision(False, "scheme_not_allowed")
    return UrlDecision(True)


def validate_artifact_path(container_root: str, requested: str) -> Path:
    """Return a canonical path confined to ``container_root`` (no traversal)."""
    root = Path(container_root).resolve()
    target = (root / requested).resolve()
    if not target.is_relative_to(root):
        raise ArtifactPathTraversalError(f"path escapes container root: {requested!r}")
    return target


def assert_no_secret_surface(surface: object) -> None:
    """Raise if a value destined for a public surface still contains secrets."""

    def walk(node: object) -> None:
        if isinstance(node, str):
            for name, regex in _TOKEN_PATTERNS:
                if regex.search(node):
                    raise SecretSurfaceError(f"secret pattern {name!r} found")
        elif isinstance(node, dict):
            for key, item in node.items():
                if isinstance(key, str) and _is_secret_field(key):
                    raise SecretSurfaceError(f"secret field {key!r} found")
                walk(item)
        elif isinstance(node, (list, tuple)):
            for item in node:
                walk(item)

    walk(surface)


MAX_UPSTREAM_BODY = 512


def safe_upstream_body(text: str, limit: int = MAX_UPSTREAM_BODY) -> str:
    """Redact and truncate an upstream response body for errors/logs.

    Provider / harness / sandbox responses may echo an injected
    Authorization header or other credentials, so any body that reaches an
    error message, log or event must pass through here first.
    """
    if not text:
        return "<empty>"
    safe = redact(text)
    if not isinstance(safe, str):  # pragma: no cover - redact keeps str
        safe = str(safe)
    if len(safe) > limit:
        return safe[:limit] + "...<truncated>"
    return safe


def bounded_redacted_preview(
    value: str, *, max_lines: int = 20, max_bytes: int = 4096
) -> tuple[str, int]:
    """Return a redacted head/tail preview and the number of omitted lines."""
    safe = redact(value)
    text = safe if isinstance(safe, str) else str(safe)
    lines = text.splitlines()
    omitted = max(0, len(lines) - max_lines)
    if len(lines) > max_lines:
        head_count = max_lines // 2
        visible = [*lines[:head_count], *lines[-(max_lines - head_count) :]]
    else:
        visible = lines
    bounded = "\n".join(visible)
    encoded = bounded.encode("utf-8")
    if len(encoded) <= max_bytes:
        return bounded, omitted
    marker = b"\n...\n"
    head_bytes = (max_bytes - len(marker)) // 2
    tail_bytes = max_bytes - len(marker) - head_bytes
    head = encoded[:head_bytes].decode("utf-8", errors="ignore")
    tail = encoded[-tail_bytes:].decode("utf-8", errors="ignore")
    return head + marker.decode() + tail, omitted
