"""Artifact Store core (specs/artifact-store/README.md §5.2)."""
from __future__ import annotations

import hashlib
import mimetypes
import time
import uuid

from haas.artifacts.models import ArtifactPolicy, FileRecord
from haas.artifacts.paths import ArtifactPathRejected, safe_relative_path


class ArtifactNotFoundError(Exception):
    """Raised when an artifact id is unknown or out of caller scope."""


class ArtifactStore:
    """Metadata index plus the S6 in-process content store.

    Content storage boundary follows specs/artifact-store §6.1.1: uploaded
    bytes are readable back from here; harness-produced records that only
    exist in the container workspace carry metadata without content and must
    surface ``haas_file_not_found`` on download rather than an empty body.
    """

    def __init__(self, policy: ArtifactPolicy | None = None) -> None:
        self._policy = policy or ArtifactPolicy()
        self._files: dict[str, FileRecord] = {}
        self._by_session: dict[str, list[str]] = {}
        self._content: dict[str, bytes] = {}

    @property
    def policy(self) -> ArtifactPolicy:
        return self._policy

    def register(
        self,
        session_id: str,
        relative_path: str,
        content: bytes,
        *,
        invocation_id: str = "",
        owner_principal_id: str = "",
        store_content: bool = True,
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
            createdAtMs=int(time.time() * 1000),
            ownerPrincipalId=owner_principal_id,
        )
        self._files[file_id] = record
        self._by_session.setdefault(session_id, []).append(file_id)
        if store_content:
            self._content[file_id] = content
        return record

    def list(self, session_id: str, *, owner_principal_id: str | None = None) -> list[FileRecord]:
        records = [self._files[fid] for fid in self._by_session.get(session_id, [])]
        if owner_principal_id is None:
            return records
        return [r for r in records if r.ownerPrincipalId == owner_principal_id]

    def get(self, file_id: str, *, owner_principal_id: str | None = None) -> FileRecord:
        record = self._files.get(file_id)
        if record is None:
            raise ArtifactNotFoundError(file_id)
        if owner_principal_id is not None and record.ownerPrincipalId != owner_principal_id:
            # Security Boundary §8.3: cross-scope access is indistinguishable
            # from "missing" so existence is not leaked.
            raise ArtifactNotFoundError(file_id)
        return record

    def read_content(self, file_id: str, *, owner_principal_id: str | None = None) -> bytes:
        record = self.get(file_id, owner_principal_id=owner_principal_id)
        content = self._content.get(record.id)
        if content is None:
            # Container-only record: not readable back in S6.
            raise ArtifactNotFoundError(file_id)
        return content


def _basename(path: str) -> str:
    import os

    return os.path.basename(path)


def _infer_media_type(path: str) -> str:
    return mimetypes.guess_type(path)[0] or "application/octet-stream"
