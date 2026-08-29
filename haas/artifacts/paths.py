"""Artifact path safety (specs/artifact-store/README.md §8)."""
from __future__ import annotations

import os
from pathlib import Path

from haas.artifacts.models import ArtifactPolicy


class ArtifactPathRejected(Exception):
    """Raised when an artifact path violates traversal/hidden/exclude rules."""


def safe_relative_path(relative_path: str, policy: ArtifactPolicy) -> str:
    """Canonicalize a relative path under the session container root.

    Rejects absolute paths, ``..`` traversal, hidden entries (unless allowed)
    and excluded prefixes. Returns the normalized relative path.
    """
    if not relative_path or "\x00" in relative_path:
        raise ArtifactPathRejected("empty_or_nul_path")
    if relative_path.startswith("/") or relative_path.startswith("\\"):
        raise ArtifactPathRejected("absolute_path_rejected")

    normalized = os.path.normpath(relative_path)
    if normalized in {".", ".."} or normalized.startswith("../"):
        raise ArtifactPathRejected("path_traversal_rejected")

    parts = Path(normalized).parts
    if not policy.allowHidden and any(p.startswith(".") for p in parts):
        raise ArtifactPathRejected("hidden_path_rejected")

    for prefix in policy.excludePrefixes:
        if normalized == prefix or normalized.startswith(prefix.rstrip("/") + "/"):
            raise ArtifactPathRejected(f"excluded_prefix: {prefix}")

    return normalized
