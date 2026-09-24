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


class ArtifactQuotaExceeded(Exception):
    """Raised when a session exceeds its file-count or published-file quota.

    Additive error code (P1-2 S2-003); callers map this to a structured
    ``haas_artifact_quota_exceeded`` northbound error.
    """


def _monotonic() -> float:
    return time.monotonic()


class ArtifactStore:
    """Metadata index plus the S6 in-process content store.

    Content storage follows specs/artifact-store §6.1.1: uploaded files and
    accepted harness-produced files are readable as immutable byte snapshots.
    Explicit metadata-only records surface ``haas_file_not_found`` on download
    rather than an empty body.

    Memory is bounded (P1-2 S2-002): superseded produced versions free their
    bytes eagerly, and a global ``maxContentBytes`` ceiling evicts the
    least-recently-accessed non-current-version snapshots when exceeded.
    """

    def __init__(self, policy: ArtifactPolicy | None = None) -> None:
        self._policy = policy or ArtifactPolicy()
        self._files: dict[str, FileRecord] = {}
        self._by_session: dict[tuple[str, str, str], list[str]] = {}
        self._content: dict[str, bytes] = {}
        # Last access monotonic timestamp per held content id, for LRU eviction.
        self._access: dict[str, float] = {}

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
        media_type: str | None = None,
        app_name: str = "",
        user_id: str = "",
        publish: bool = False,
    ) -> FileRecord:
        normalized = safe_relative_path(relative_path, self._policy)
        if len(content) > self._policy.maxFileBytes:
            raise ArtifactPathRejected("file_too_large")

        scope = (app_name, user_id, session_id)
        # Produced (publish) callers enforce maxPublishFiles themselves with
        # supersede awareness; uploads enforce maxFiles here.
        if not publish and len(self._by_session.get(scope, [])) >= self._policy.maxFiles:
            raise ArtifactQuotaExceeded("maxFiles")

        file_id = f"file_{uuid.uuid4().hex[:16]}"
        record = FileRecord(
            id=file_id,
            sessionId=session_id,
            filename=_basename(normalized),
            relativePath=normalized,
            appName=app_name,
            userId=user_id,
            bytes=len(content),
            mediaType=media_type or _infer_media_type(normalized),
            sha256=hashlib.sha256(content).hexdigest(),
            invocationId=invocation_id,
            createdAtMs=int(time.time() * 1000),
            ownerPrincipalId=owner_principal_id,
            previewStatus="available" if store_content else "unavailable",
            downloadStatus="available" if store_content else "unavailable",
        )
        self._files[file_id] = record
        self._by_session.setdefault(scope, []).append(file_id)
        if store_content:
            self._content[file_id] = content
            self._access[file_id] = _monotonic()
            self._evict_if_needed()
        return record

    def register_produced(
        self,
        session_id: str,
        relative_path: str,
        content: bytes,
        *,
        invocation_id: str,
        owner_principal_id: str,
        media_type: str | None = None,
        app_name: str = "",
        user_id: str = "",
    ) -> tuple[FileRecord, bool]:
        """Publish the current version of one produced artifact.

        Terminal scans revisit the complete output root. Keep unchanged files
        stable and replace only the session listing entry when content changes;
        old opaque ids remain metadata-readable but their bytes are freed from
        the in-process content store (P1-2 S2-002).
        """
        normalized = safe_relative_path(relative_path, self._policy)
        digest = hashlib.sha256(content).hexdigest()
        scope = (app_name, user_id, session_id)
        current_ids = self._by_session.get(scope, [])
        matching = [
            file_id
            for file_id in current_ids
            if self._files[file_id].relativePath == normalized
            and self._files[file_id].ownerPrincipalId == owner_principal_id
        ]
        if matching:
            current = self._files[matching[-1]]
            effective_media_type = media_type or _infer_media_type(normalized)
            if current.sha256 == digest and current.mediaType == effective_media_type:
                return current, False

        # Quota: a brand-new published path adds a slot; superseding an existing
        # path reuses its slot and never consumes an extra one.
        post_count = len(current_ids) - len(matching) + 1
        if post_count > self._policy.maxPublishFiles:
            raise ArtifactQuotaExceeded("maxPublishFiles")

        record = self.register(
            session_id,
            normalized,
            content,
            invocation_id=invocation_id,
            owner_principal_id=owner_principal_id,
            media_type=media_type,
            app_name=app_name,
            user_id=user_id,
            publish=True,
        )
        if matching:
            hidden = set(matching)
            self._by_session[scope] = [
                file_id for file_id in self._by_session[scope] if file_id not in hidden
            ]
            # Free the superseded versions' bytes immediately. Metadata records
            # stay resolvable; the body is dropped from the content store.
            for old_id in hidden:
                self._content.pop(old_id, None)
                self._access.pop(old_id, None)
        return record, True

    def list(
        self,
        session_id: str,
        *,
        app_name: str = "",
        user_id: str = "",
        owner_principal_id: str | None = None,
    ) -> list[FileRecord]:
        scope = (app_name, user_id, session_id)
        records = [self._files[fid] for fid in self._by_session.get(scope, [])]
        if owner_principal_id is None:
            return records
        return [r for r in records if r.ownerPrincipalId == owner_principal_id]

    def delete_session(
        self, session_id: str, *, app_name: str = "", user_id: str = ""
    ) -> None:
        """Make every current and superseded artifact for a session unreachable."""
        self._by_session.pop((app_name, user_id, session_id), None)
        file_ids = [
            file_id
            for file_id, record in self._files.items()
            if record.sessionId == session_id
            and record.appName == app_name
            and record.userId == user_id
        ]
        for file_id in file_ids:
            self._files.pop(file_id, None)
            self._content.pop(file_id, None)
            self._access.pop(file_id, None)

    def get(self, file_id: str, *, owner_principal_id: str | None = None) -> FileRecord:
        record = self._files.get(file_id)
        if record is None:
            raise ArtifactNotFoundError(file_id)
        if owner_principal_id is not None and record.ownerPrincipalId != owner_principal_id:
            # Security Boundary §8.3: cross-scope access is indistinguishable
            # from "missing" so existence is not leaked.
            raise ArtifactNotFoundError(file_id)
        self._touch(file_id)
        return record

    def read_content(self, file_id: str, *, owner_principal_id: str | None = None) -> bytes:
        record = self.get(file_id, owner_principal_id=owner_principal_id)
        content = self._content.get(record.id)
        if content is None:
            # Metadata-only records (or evicted/superseded bodies) never
            # masquerade as readable empty files.
            raise ArtifactNotFoundError(file_id)
        return content

    # --- memory bounds --------------------------------------------------

    def _touch(self, file_id: str) -> None:
        if file_id in self._content:
            self._access[file_id] = _monotonic()

    def _evict_if_needed(self) -> None:
        """Evict least-recently-accessed non-current content under budget.

        Current-version snapshots (reachable through ``_by_session``) are never
        evicted: a caller holding a current id must keep downloading. Only
        superseded / non-current snapshots are reclaimable.
        """
        while sum(len(chunk) for chunk in self._content.values()) > self._policy.maxContentBytes:
            current = {
                file_id
                for ids in self._by_session.values()
                for file_id in ids
            }
            victims = [fid for fid in self._content if fid not in current]
            if not victims:
                return
            victims.sort(key=lambda fid: self._access.get(fid, 0.0))
            oldest = victims[0]
            self._content.pop(oldest, None)
            self._access.pop(oldest, None)


def _basename(path: str) -> str:
    import os

    return os.path.basename(path)


def _infer_media_type(path: str) -> str:
    return mimetypes.guess_type(path)[0] or "application/octet-stream"
