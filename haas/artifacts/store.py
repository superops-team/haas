"""Artifact Store core (specs/artifact-store/README.md §5.2)."""
from __future__ import annotations

import hashlib
import mimetypes
import uuid

from haas.artifacts.models import ArtifactPolicy, FileRecord
from haas.artifacts.paths import ArtifactPathRejected, safe_relative_path


class ArtifactNotFoundError(Exception):
    """Raised when an artifact id is unknown."""


class ArtifactStore:
    def __init__(self, policy: ArtifactPolicy | None = None) -> None:
        self._policy = policy or ArtifactPolicy()
        self._files: dict[str, FileRecord] = {}
        self._by_session: dict[str, list[str]] = {}

    def register(
        self,
        session_id: str,
        relative_path: str,
        content: bytes,
        *,
        invocation_id: str = "",
    ) -> FileRecord:
        normalized = safe_relative_path(relative_path, self._policy)
        if len(content) > self._policy.maxFileBytes:
            raise ArtifactPathRejected("file_too_large")

        file_id = f"file_{uuid.uuid4().hex[:16]}"
        record = FileRecord(
            id=file_id,
            sessionId=session_id,
            filename=_basename(normalized),
            relativePath=normalized,
            bytes=len(content),
            mediaType=_infer_media_type(normalized),
            sha256=hashlib.sha256(content).hexdigest(),
            invocationId=invocation_id,
        )
        self._files[file_id] = record
        self._by_session.setdefault(session_id, []).append(file_id)
        return record

    def list(self, session_id: str) -> list[FileRecord]:
        return [self._files[fid] for fid in self._by_session.get(session_id, [])]

    def get(self, file_id: str) -> FileRecord:
        record = self._files.get(file_id)
        if record is None:
            raise ArtifactNotFoundError(file_id)
        return record


def _basename(path: str) -> str:
    import os

    return os.path.basename(path)


def _infer_media_type(path: str) -> str:
    return mimetypes.guess_type(path)[0] or "application/octet-stream"
