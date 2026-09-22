"""Artifact Store data models (specs/artifact-store/README.md §6)."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class FileRecord:
    id: str
    sessionId: str
    filename: str
    relativePath: str
    appName: str = ""
    userId: str = ""
    bytes: int = 0
    mediaType: str = "application/octet-stream"
    sha256: str = ""
    invocationId: str = ""
    createdAtMs: int = 0
    ownerPrincipalId: str = ""
    previewStatus: str = "available"
    downloadStatus: str = "available"

    def to_dict(self) -> dict[str, object]:
        """Serialize to the OpenAPI `File` shape (object is a required const)."""
        return {
            "id": self.id,
            "object": "file",
            "sessionId": self.sessionId,
            "invocationId": self.invocationId,
            "filename": self.filename,
            "relativePath": self.relativePath,
            "bytes": self.bytes,
            "mediaType": self.mediaType,
            "sha256": self.sha256,
            "createdAtMs": self.createdAtMs,
            "previewStatus": self.previewStatus,
            "downloadStatus": self.downloadStatus,
        }


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
