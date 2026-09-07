"""Sandbox policy projection for Codex app-server (specs/codex-app-server-adapter §8).

Codex uses two different enum shapes for sandbox:

- ``thread/start`` ``SandboxMode`` is kebab-case:
  ``read-only`` | ``workspace-write`` | ``danger-full-access``
- ``turn/start`` ``SandboxPolicy`` is a camelCase tagged enum:
  ``{"type": "readOnly" | "workspaceWrite" | "dangerFullAccess", ...}``

The adapter only projects the Sandbox Runtime result (mode + writable roots +
network policy); it never widens isolation. Network access is fail-closed.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from haas.policy.models import NetworkPolicy

JsonObject = dict[str, Any]

# HaaS kebab-case mode -> Codex thread/start SandboxMode (kebab-case).
_THREAD_SANDBOX_MODE_MAP: dict[str, str] = {
    "read-only": "read-only",
    "workspace-write": "workspace-write",
    "danger-full-access": "danger-full-access",
}


class SandboxPolicyError(ValueError):
    """Raised when a sandbox mode or writable root would weaken isolation."""


def normalize_mode(mode: str) -> str:
    """Normalize a HaaS sandbox mode to kebab-case and validate it."""
    normalized = mode.replace("_", "-")
    if normalized not in _THREAD_SANDBOX_MODE_MAP:
        raise SandboxPolicyError(f"unsupported sandbox mode: {mode}")
    return normalized


def to_thread_sandbox_mode(mode: str) -> str:
    """Convert a HaaS sandbox mode to Codex ``thread/start`` SandboxMode."""
    return _THREAD_SANDBOX_MODE_MAP[normalize_mode(mode)]


def to_turn_sandbox_policy(
    mode: str,
    writable_roots: list[str],
    network_policy: NetworkPolicy | dict[str, Any] | None = None,
) -> JsonObject:
    """Convert a HaaS sandbox mode to Codex ``turn/start`` SandboxPolicy.

    Network access is fail-closed: missing or malformed policy data denies
    egress, and only an explicit ``defaultAction=allow`` enables it.
    """
    normalized = normalize_mode(mode)
    network_access = _network_access_allowed(network_policy)
    if normalized == "workspace-write":
        return {
            "type": "workspaceWrite",
            "writableRoots": _dedupe_writable_roots(writable_roots),
            "networkAccess": network_access,
        }
    if normalized == "read-only":
        return {"type": "readOnly", "networkAccess": network_access}
    if normalized == "danger-full-access":
        return {"type": "dangerFullAccess"}
    raise SandboxPolicyError(f"unsupported sandbox mode: {mode}")


def _network_access_allowed(network_policy: NetworkPolicy | dict[str, Any] | None) -> bool:
    if network_policy is None:
        return False
    if isinstance(network_policy, NetworkPolicy):
        return network_policy.defaultAction == "allow"
    if isinstance(network_policy, dict):
        return network_policy.get("defaultAction") == "allow"
    return False


def _dedupe_writable_roots(roots: list[str]) -> list[str]:
    result: list[str] = []
    for root in roots:
        if not root:
            continue
        if not root.startswith("/"):
            raise SandboxPolicyError("sandbox_writable_root_not_absolute")
        normalized = str(Path(root))
        _validate_writable_root(normalized)
        if normalized not in result:
            result.append(normalized)
    return result


def _validate_writable_root(root: str) -> None:
    if root in {"/", "/data", "/data/worker", "/root"}:
        raise SandboxPolicyError("sandbox_writable_root_too_broad")
