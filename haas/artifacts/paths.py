"""Artifact path safety (specs/artifact-store/README.md §8)."""

from __future__ import annotations

import os
from pathlib import Path

from haas.artifacts.models import ArtifactPolicy


class ArtifactPathRejected(Exception):
    """Raised when an artifact path violates traversal/hidden/exclude rules."""


def safe_relative_path(relative_path: str, policy: ArtifactPolicy) -> str:
    """Canonicalize a relative path under the session container root.

    Rejects absolute paths, ``..`` traversal, hidden entries (unless allowed),
    paths outside the configured include roots, and excluded prefixes. Returns
    the normalized relative path.
    """
    normalized = _normalize_candidate_path(relative_path)

    if not _is_under_any_root(normalized, _normalized_policy_roots(policy.includeRoots)):
        raise ArtifactPathRejected("outside_include_roots")

    parts = Path(normalized).parts
    if not policy.allowHidden and any(p.startswith(".") for p in parts):
        raise ArtifactPathRejected("hidden_path_rejected")

    for prefix in _normalized_policy_roots(policy.excludePrefixes):
        if _path_matches_root(normalized, prefix):
            raise ArtifactPathRejected(f"excluded_prefix: {prefix}")

    return normalized


def _normalize_candidate_path(path: str) -> str:
    if not path or "\x00" in path:
        raise ArtifactPathRejected("empty_or_nul_path")
    if path.startswith("/") or path.startswith("\\"):
        raise ArtifactPathRejected("absolute_path_rejected")

    normalized = os.path.normpath(path)
    if normalized in {".", ".."} or normalized.startswith("../"):
        raise ArtifactPathRejected("path_traversal_rejected")
    return normalized


def _normalized_policy_roots(roots: list[str]) -> list[str]:
    normalized_roots: list[str] = []
    for root in roots:
        normalized_roots.append(_normalize_policy_root(root))
    return normalized_roots


def _normalize_policy_root(root: str) -> str:
    if not root or "\x00" in root:
        raise ArtifactPathRejected("invalid_policy_root")
    if root.startswith("/") or root.startswith("\\"):
        raise ArtifactPathRejected("invalid_policy_root")

    normalized = os.path.normpath(root)
    if normalized in {".", ".."} or normalized.startswith("../"):
        raise ArtifactPathRejected("invalid_policy_root")
    return normalized


def _is_under_any_root(path: str, roots: list[str]) -> bool:
    if not roots:
        return False
    return any(_path_matches_root(path, root) for root in roots)


def _path_matches_root(path: str, root: str) -> bool:
    return path == root or path.startswith(root + "/")
