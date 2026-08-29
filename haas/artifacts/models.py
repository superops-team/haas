"""Artifact Store data models (specs/artifact-store/README.md §6)."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class FileRecord:
    id: str
    sessionId: str
    filename: str
    relativePath: str
    bytes: int = 0
    mediaType: str = "application/octet-stream"
    sha256: str = ""
    invocationId: str = ""
    createdAtMs: int = 0


@dataclass
class ArtifactPolicy:
    includeRoots: list[str] = field(default_factory=lambda: ["output"])
    excludePrefixes: list[str] = field(
        default_factory=lambda: [".git", ".haas", ".codex", ".cache", "node_modules"]
    )
    maxFiles: int = 1000
    maxPublishFiles: int = 20
    maxFileBytes: int = 104857600
    allowHidden: bool = False
