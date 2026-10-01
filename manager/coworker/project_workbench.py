"""Persistent project/workspace/session identity for the Manager workbench."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from .projects import project_key

PERSONAL_PROJECT_ID = "prj_personal"
PERSONAL_CANONICAL_KEY = "manager://personal"
LOCAL_ENDPOINT_ID = "hep_local_managed"


class ProjectStoreError(ValueError):
    """The requested project/workspace operation is invalid."""


class ProjectStoreConflict(ProjectStoreError):
    """The request conflicts with a previously persisted identity or mutation."""

    def __init__(self, message: str, *, project_id: str | None = None) -> None:
        super().__init__(message)
        self.project_id = project_id


@dataclass(frozen=True, slots=True)
class ProjectRecord:
    project_id: str
    canonical_key: str
    name: str
    primary_workspace_binding_id: str | None
    default_endpoint_id: str
    pinned: bool
    position: int
    archived: bool
    created_at_ms: int
    updated_at_ms: int


@dataclass(frozen=True, slots=True)
class WorkspaceBinding:
    workspace_binding_id: str
    project_id: str
    location: str
    endpoint_id: str
    local_path: str | None
    remote_workspace_ref: str | None
    display_path: str
    state: str
    git: dict[str, object] | None
    created_at_ms: int
    updated_at_ms: int


@dataclass(frozen=True, slots=True)
class ProjectWorkspace:
    project: ProjectRecord
    workspace: WorkspaceBinding


@dataclass(frozen=True, slots=True)
class SessionProjectBinding:
    session_id: str
    project_id: str
    workspace_binding_id: str | None
    endpoint_id: str
    endpoint_fingerprint: str | None
    branch_snapshot: str | None
    workspace_revision: str | None
    frozen: bool


def _stable_id(prefix: str, value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:20]
    return f"{prefix}_{digest}"


def _clean_name(value: str, fallback: str) -> str:
    name = " ".join(str(value or "").split())[:80]
    return name or fallback


def _now_ms() -> int:
    return int(time.time() * 1000)


class ProjectStore:
    """SQLite owner for Manager project and executable-workspace identities."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA busy_timeout = 5000")
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS manager_projects (
                project_id TEXT PRIMARY KEY,
                canonical_key TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                primary_workspace_binding_id TEXT,
                default_endpoint_id TEXT NOT NULL,
                pinned INTEGER NOT NULL DEFAULT 0,
                position INTEGER NOT NULL DEFAULT 0,
                archived INTEGER NOT NULL DEFAULT 0,
                created_at_ms INTEGER NOT NULL,
                updated_at_ms INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS manager_workspace_bindings (
                workspace_binding_id TEXT PRIMARY KEY,
                binding_key TEXT NOT NULL UNIQUE,
                project_id TEXT NOT NULL,
                location TEXT NOT NULL,
                endpoint_id TEXT NOT NULL,
                local_path TEXT,
                remote_workspace_ref TEXT,
                display_path TEXT NOT NULL,
                state TEXT NOT NULL,
                git_json TEXT,
                created_at_ms INTEGER NOT NULL,
                updated_at_ms INTEGER NOT NULL,
                FOREIGN KEY(project_id) REFERENCES manager_projects(project_id)
            );
            CREATE TABLE IF NOT EXISTS manager_session_project_bindings (
                session_id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                workspace_binding_id TEXT,
                endpoint_id TEXT NOT NULL,
                endpoint_fingerprint TEXT,
                branch_snapshot TEXT,
                workspace_revision TEXT,
                frozen INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY(project_id) REFERENCES manager_projects(project_id),
                FOREIGN KEY(workspace_binding_id)
                    REFERENCES manager_workspace_bindings(workspace_binding_id)
            );
            CREATE TABLE IF NOT EXISTS manager_project_mutations (
                idempotency_key TEXT PRIMARY KEY,
                request_hash TEXT NOT NULL,
                result_json TEXT NOT NULL,
                created_at_ms INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS manager_project_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )
        project_columns = {
            str(row["name"])
            for row in self._conn.execute("PRAGMA table_info(manager_projects)")
        }
        if "pinned" not in project_columns:
            self._conn.execute(
                "ALTER TABLE manager_projects ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0"
            )
        self._conn.execute(
            "INSERT OR IGNORE INTO manager_project_meta (key, value) VALUES ('order_revision', '0')"
        )
        self._ensure_personal()
        self._conn.commit()

    def _ensure_personal(self) -> None:
        now = _now_ms()
        self._conn.execute(
            """
            INSERT OR IGNORE INTO manager_projects (
                project_id, canonical_key, name, primary_workspace_binding_id,
                default_endpoint_id, position, archived, created_at_ms, updated_at_ms
            ) VALUES (?, ?, ?, NULL, ?, 0, 0, ?, ?)
            """,
            (
                PERSONAL_PROJECT_ID,
                PERSONAL_CANONICAL_KEY,
                "Personal",
                LOCAL_ENDPOINT_ID,
                now,
                now,
            ),
        )

    def ensure_local_project(
        self, workspace: str | Path, *, name: str | None = None
    ) -> ProjectWorkspace:
        raw = Path(workspace).expanduser()
        try:
            resolved = raw.resolve()
        except OSError:
            resolved = raw
        canonical_key = project_key(resolved)
        fallback = Path(canonical_key).name or "Project"
        binding_key = f"local\0{resolved}"
        return self._ensure_project_workspace(
            canonical_key=canonical_key,
            name=_clean_name(name or "", fallback),
            binding_key=binding_key,
            location="local",
            endpoint_id=LOCAL_ENDPOINT_ID,
            local_path=str(resolved),
            remote_workspace_ref=None,
            display_path=str(resolved),
            state="available" if resolved.is_dir() else "missing",
        )

    def create_local_project(
        self,
        *,
        name: str,
        workspace: str | Path,
        endpoint_id: str = LOCAL_ENDPOINT_ID,
        idempotency_key: str,
    ) -> ProjectWorkspace:
        resolved = Path(workspace).expanduser().resolve()
        if not resolved.is_dir():
            raise ProjectStoreError("workspace unavailable")
        mutation_key = idempotency_key.strip()
        if not mutation_key:
            raise ProjectStoreError("idempotency key required")
        request = {
            "name": _clean_name(name, resolved.name or "Project"),
            "path": str(resolved),
            "endpointId": endpoint_id,
        }
        request_hash = hashlib.sha256(
            json.dumps(request, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        with self._lock:
            replay = self._mutation_replay(mutation_key, request_hash)
            if replay is not None:
                return replay
            canonical_key = project_key(resolved)
            created = self._ensure_project_workspace(
                canonical_key=canonical_key,
                name=request["name"],
                binding_key=f"local\0{resolved}",
                location="local",
                endpoint_id=endpoint_id,
                local_path=str(resolved),
                remote_workspace_ref=None,
                display_path=str(resolved),
                state="available",
                commit=False,
            )
            self._record_mutation(mutation_key, request_hash, created)
            self._conn.commit()
            return created

    def create_remote_project(
        self,
        *,
        name: str,
        endpoint_id: str,
        remote_workspace_ref: str,
        display_path: str,
        idempotency_key: str,
    ) -> ProjectWorkspace:
        endpoint = endpoint_id.strip()
        workspace_ref = remote_workspace_ref.strip()
        mutation_key = idempotency_key.strip()
        if (
            not endpoint
            or not workspace_ref
            or len(workspace_ref) > 1024
            or any(ord(character) < 32 for character in workspace_ref)
            or not mutation_key
        ):
            raise ProjectStoreError(
                "remote endpoint, workspace reference and idempotency key required"
            )
        request = {
            "name": _clean_name(name, "Remote project"),
            "endpointId": endpoint,
            "remoteWorkspaceRef": workspace_ref,
            "displayPath": _clean_name(display_path, "Remote workspace"),
        }
        request_hash = hashlib.sha256(
            json.dumps(request, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        with self._lock:
            replay = self._mutation_replay(mutation_key, request_hash)
            if replay is not None:
                return replay

            remote_digest = hashlib.sha256(f"{endpoint}\0{workspace_ref}".encode()).hexdigest()
            created = self._ensure_project_workspace(
                canonical_key=f"remote://{remote_digest}",
                name=request["name"],
                binding_key=f"remote\0{endpoint}\0{workspace_ref}",
                location="remote",
                endpoint_id=endpoint,
                local_path=None,
                remote_workspace_ref=workspace_ref,
                display_path=request["displayPath"],
                state="available",
                commit=False,
            )
            self._record_mutation(mutation_key, request_hash, created)
            self._conn.commit()
            return created

    def add_remote_workspace(
        self,
        project_id: str,
        *,
        endpoint_id: str,
        remote_workspace_ref: str,
        display_path: str,
        idempotency_key: str,
    ) -> WorkspaceBinding:
        project = self.get_project(project_id)
        endpoint = endpoint_id.strip()
        workspace_ref = remote_workspace_ref.strip()
        mutation_key = idempotency_key.strip()
        if (
            not endpoint
            or not workspace_ref
            or len(workspace_ref) > 1024
            or any(ord(character) < 32 for character in workspace_ref)
            or not mutation_key
        ):
            raise ProjectStoreError("valid remote workspace binding is required")
        request = {
            "projectId": project_id,
            "endpointId": endpoint,
            "remoteWorkspaceRef": workspace_ref,
            "displayPath": _clean_name(display_path, "Remote workspace"),
        }
        request_hash, replay = self.replay_json_mutation(
            f"workspace:{mutation_key}", request
        )
        if replay is not None:
            return self.get_workspace(str(replay["workspaceBindingId"]))
        binding_key = f"remote\0{endpoint}\0{workspace_ref}"
        with self._lock:
            existing = self._conn.execute(
                "SELECT project_id FROM manager_workspace_bindings WHERE binding_key = ?",
                (binding_key,),
            ).fetchone()
            if existing is not None and str(existing["project_id"]) != project_id:
                raise ProjectStoreConflict(
                    "workspace already belongs to a project",
                    project_id=str(existing["project_id"]),
                )
            result = self._ensure_project_workspace(
                canonical_key=project.canonical_key,
                name=project.name,
                binding_key=binding_key,
                location="remote",
                endpoint_id=endpoint,
                local_path=None,
                remote_workspace_ref=workspace_ref,
                display_path=request["displayPath"],
                state="available",
                commit=False,
            ).workspace
            self.record_json_mutation(
                f"workspace:{mutation_key}",
                request_hash,
                {"workspaceBindingId": result.workspace_binding_id},
            )
        return result

    def _mutation_replay(
        self, idempotency_key: str, request_hash: str
    ) -> ProjectWorkspace | None:
        replay = self._conn.execute(
            "SELECT request_hash, result_json FROM manager_project_mutations "
            "WHERE idempotency_key = ?",
            (idempotency_key,),
        ).fetchone()
        if replay is None:
            return None
        if replay["request_hash"] != request_hash:
            raise ProjectStoreConflict("idempotency key reused with a different request")
        result = json.loads(replay["result_json"])
        return ProjectWorkspace(
            project=self.get_project(result["projectId"]),
            workspace=self.get_workspace(result["workspaceBindingId"]),
        )

    def _record_mutation(
        self, idempotency_key: str, request_hash: str, result: ProjectWorkspace
    ) -> None:
        self._conn.execute(
            "INSERT INTO manager_project_mutations "
            "(idempotency_key, request_hash, result_json, created_at_ms) VALUES (?, ?, ?, ?)",
            (
                idempotency_key,
                request_hash,
                json.dumps(
                    {
                        "projectId": result.project.project_id,
                        "workspaceBindingId": result.workspace.workspace_binding_id,
                    },
                    separators=(",", ":"),
                ),
                _now_ms(),
            ),
        )

    def replay_json_mutation(
        self, idempotency_key: str, request: dict[str, object]
    ) -> tuple[str, dict[str, object] | None]:
        request_hash = hashlib.sha256(
            json.dumps(request, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        with self._lock:
            row = self._conn.execute(
                "SELECT request_hash, result_json FROM manager_project_mutations "
                "WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
        if row is None:
            return request_hash, None
        if row["request_hash"] != request_hash:
            raise ProjectStoreConflict("idempotency key reused with a different request")
        value = json.loads(row["result_json"])
        if not isinstance(value, dict):
            raise ProjectStoreError("stored mutation result is invalid")
        return request_hash, value

    def record_json_mutation(
        self, idempotency_key: str, request_hash: str, result: dict[str, object]
    ) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO manager_project_mutations "
                "(idempotency_key, request_hash, result_json, created_at_ms) VALUES (?, ?, ?, ?)",
                (
                    idempotency_key,
                    request_hash,
                    json.dumps(result, sort_keys=True, separators=(",", ":")),
                    _now_ms(),
                ),
            )
            self._conn.commit()

    def order_revision(self) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM manager_project_meta WHERE key = 'order_revision'"
            ).fetchone()
        return int(row["value"]) if row is not None else 0

    def _bump_order_revision(self) -> int:
        self._conn.execute(
            "UPDATE manager_project_meta "
            "SET value = CAST(value AS INTEGER) + 1 WHERE key = 'order_revision'"
        )
        row = self._conn.execute(
            "SELECT value FROM manager_project_meta WHERE key = 'order_revision'"
        ).fetchone()
        return int(row["value"])

    @staticmethod
    def _project_payload(project: ProjectRecord) -> dict[str, object]:
        return {
            "project_id": project.project_id,
            "canonical_key": project.canonical_key,
            "name": project.name,
            "primary_workspace_binding_id": project.primary_workspace_binding_id,
            "default_endpoint_id": project.default_endpoint_id,
            "pinned": project.pinned,
            "position": project.position,
            "archived": project.archived,
            "created_at_ms": project.created_at_ms,
            "updated_at_ms": project.updated_at_ms,
        }

    @staticmethod
    def _project_from_payload(payload: dict[str, object]) -> ProjectRecord:
        return ProjectRecord(
            project_id=str(payload["project_id"]),
            canonical_key=str(payload["canonical_key"]),
            name=str(payload["name"]),
            primary_workspace_binding_id=(
                str(payload["primary_workspace_binding_id"])
                if payload.get("primary_workspace_binding_id") is not None
                else None
            ),
            default_endpoint_id=str(payload["default_endpoint_id"]),
            pinned=bool(payload["pinned"]),
            position=int(payload["position"]),
            archived=bool(payload["archived"]),
            created_at_ms=int(payload["created_at_ms"]),
            updated_at_ms=int(payload["updated_at_ms"]),
        )

    def update_project(
        self,
        project_id: str,
        *,
        name: str | None = None,
        default_endpoint_id: str | None = None,
        pinned: bool | None = None,
        archived: bool | None = None,
        idempotency_key: str,
    ) -> tuple[ProjectRecord, int]:
        patch: dict[str, object] = {}
        if name is not None:
            if not isinstance(name, str):
                raise ProjectStoreError("project name must be a string")
            cleaned = " ".join(name.split())[:80]
            if not cleaned:
                raise ProjectStoreError("project name is required")
            patch["name"] = cleaned
        if default_endpoint_id is not None:
            if not isinstance(default_endpoint_id, str):
                raise ProjectStoreError("default endpoint must be a string")
            endpoint = default_endpoint_id.strip()
            if not endpoint:
                raise ProjectStoreError("default endpoint is required")
            patch["default_endpoint_id"] = endpoint
        if pinned is not None:
            if not isinstance(pinned, bool):
                raise ProjectStoreError("pinned must be a boolean")
            patch["pinned"] = pinned
        if archived is not None:
            if not isinstance(archived, bool):
                raise ProjectStoreError("archived must be a boolean")
            patch["archived"] = archived
        if not patch:
            raise ProjectStoreError("project update is empty")
        if project_id == PERSONAL_PROJECT_ID and any(
            key in patch for key in ("name", "pinned", "archived")
        ):
            raise ProjectStoreError("project is protected")
        if patch.get("archived") is True and patch.get("pinned") is True:
            raise ProjectStoreError("archived project cannot be pinned")
        if patch.get("archived") is True:
            patch["pinned"] = False

        request = {"projectId": project_id, **patch}
        mutation_key = f"project:update:{project_id}:{idempotency_key.strip()}"
        if not idempotency_key.strip():
            raise ProjectStoreError("idempotency key required")
        request_hash, replay = self.replay_json_mutation(mutation_key, request)
        if replay is not None:
            return (
                self._project_from_payload(dict(replay["project"])),
                int(replay["order_revision"]),
            )

        with self._lock:
            existing = self.get_project(project_id)
            if patch.get("pinned") is True and existing.archived and patch.get("archived") is not False:
                raise ProjectStoreError("archived project cannot be pinned")
            columns = list(patch)
            values = [int(value) if isinstance(value, bool) else value for value in patch.values()]
            now = _now_ms()
            self._conn.execute(
                f"UPDATE manager_projects SET {', '.join(f'{column} = ?' for column in columns)}, "
                "updated_at_ms = ? WHERE project_id = ?",
                (*values, now, project_id),
            )
            revision = (
                self._bump_order_revision()
                if any(key in patch for key in ("name", "pinned", "archived"))
                else self.order_revision()
            )
            project = self.get_project(project_id)
            self.record_json_mutation(
                mutation_key,
                request_hash,
                {
                    "project": self._project_payload(project),
                    "order_revision": revision,
                },
            )
            return project, revision

    def reorder_projects(
        self,
        project_ids: list[str],
        *,
        observed_revision: int,
        idempotency_key: str,
    ) -> tuple[list[ProjectRecord], int]:
        mutation_key = f"project:reorder:{idempotency_key.strip()}"
        if not idempotency_key.strip():
            raise ProjectStoreError("idempotency key required")
        request: dict[str, object] = {
            "projectIds": list(project_ids),
            "observedRevision": int(observed_revision),
        }
        request_hash, replay = self.replay_json_mutation(mutation_key, request)
        if replay is not None:
            return (
                [
                    self._project_from_payload(dict(item))
                    for item in list(replay["projects"])
                ],
                int(replay["order_revision"]),
            )

        with self._lock:
            if self.order_revision() != observed_revision:
                raise ProjectStoreConflict("project order is stale")
            current = [
                project.project_id
                for project in self.list_projects()
                if project.project_id != PERSONAL_PROJECT_ID and not project.archived
            ]
            if len(project_ids) != len(set(project_ids)) or set(project_ids) != set(current):
                raise ProjectStoreError("project order must contain every visible project once")
            now = _now_ms()
            for position, ordered_id in enumerate(project_ids, start=1):
                self._conn.execute(
                    "UPDATE manager_projects SET position = ?, updated_at_ms = ? "
                    "WHERE project_id = ?",
                    (position, now, ordered_id),
                )
            revision = self._bump_order_revision()
            projects = [self.get_project(project_id) for project_id in project_ids]
            self.record_json_mutation(
                mutation_key,
                request_hash,
                {
                    "projects": [self._project_payload(project) for project in projects],
                    "order_revision": revision,
                },
            )
            return projects, revision

    def _ensure_project_workspace(
        self,
        *,
        canonical_key: str,
        name: str,
        binding_key: str,
        location: str,
        endpoint_id: str,
        local_path: str | None,
        remote_workspace_ref: str | None,
        display_path: str,
        state: str,
        commit: bool = True,
    ) -> ProjectWorkspace:
        if location not in {"local", "remote"}:
            raise ProjectStoreError("unsupported workspace location")
        project_id = _stable_id("prj", canonical_key)
        workspace_id = _stable_id("wsb", binding_key)
        now = _now_ms()
        with self._lock:
            existing = self._conn.execute(
                "SELECT project_id FROM manager_projects WHERE canonical_key = ?",
                (canonical_key,),
            ).fetchone()
            if existing is not None:
                project_id = str(existing["project_id"])
            else:
                position = int(
                    self._conn.execute(
                        "SELECT COALESCE(MAX(position), 0) + 1 FROM manager_projects"
                    ).fetchone()[0]
                )
                self._conn.execute(
                    """
                    INSERT INTO manager_projects (
                        project_id, canonical_key, name, primary_workspace_binding_id,
                        default_endpoint_id, position, archived, created_at_ms, updated_at_ms
                    ) VALUES (?, ?, ?, NULL, ?, ?, 0, ?, ?)
                    """,
                    (project_id, canonical_key, name, endpoint_id, position, now, now),
                )
                self._bump_order_revision()
            self._conn.execute(
                """
                INSERT OR IGNORE INTO manager_workspace_bindings (
                    workspace_binding_id, binding_key, project_id, location, endpoint_id,
                    local_path, remote_workspace_ref, display_path, state, git_json,
                    created_at_ms, updated_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)
                """,
                (
                    workspace_id,
                    binding_key,
                    project_id,
                    location,
                    endpoint_id,
                    local_path,
                    remote_workspace_ref,
                    display_path,
                    state,
                    now,
                    now,
                ),
            )
            self._conn.execute(
                """
                UPDATE manager_projects
                SET primary_workspace_binding_id = COALESCE(primary_workspace_binding_id, ?),
                    updated_at_ms = ?
                WHERE project_id = ?
                """,
                (workspace_id, now, project_id),
            )
            if commit:
                self._conn.commit()
            return ProjectWorkspace(
                project=self.get_project(project_id),
                workspace=self.get_workspace(workspace_id),
            )

    def ensure_session_binding(
        self,
        session_id: str,
        *,
        workspace: str | None,
        explicit_project_id: str | None = None,
        explicit_workspace_binding_id: str | None = None,
        endpoint_id: str = LOCAL_ENDPOINT_ID,
        endpoint_fingerprint: str | None = None,
        branch_snapshot: str | None = None,
        workspace_revision: str | None = None,
        frozen: bool = False,
    ) -> SessionProjectBinding:
        with self._lock:
            existing = self.get_session_binding(session_id)
            if existing is not None:
                return existing
            project_id = explicit_project_id
            workspace_id = explicit_workspace_binding_id
            if project_id and self._project_row(project_id) is None:
                project_id = None
                workspace_id = None
            if workspace_id and self._workspace_row(workspace_id) is None:
                project_id = None
                workspace_id = None
            if workspace_id:
                workspace_row = self._workspace_row(workspace_id)
                if workspace_row is None:
                    raise ProjectStoreError("workspace binding not found")
                workspace_project_id = str(workspace_row["project_id"])
                if project_id and workspace_project_id != project_id:
                    raise ProjectStoreError("workspace binding does not belong to project")
                project_id = workspace_project_id
                endpoint_id = str(workspace_row["endpoint_id"])
            if not project_id:
                if workspace:
                    ensured = self.ensure_local_project(workspace)
                    project_id = ensured.project.project_id
                    workspace_id = ensured.workspace.workspace_binding_id
                else:
                    project_id = PERSONAL_PROJECT_ID
                    workspace_id = None
            self._conn.execute(
                """
                INSERT INTO manager_session_project_bindings (
                    session_id, project_id, workspace_binding_id, endpoint_id,
                    endpoint_fingerprint, branch_snapshot, workspace_revision, frozen
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    project_id,
                    workspace_id,
                    endpoint_id,
                    endpoint_fingerprint,
                    branch_snapshot,
                    workspace_revision,
                    int(frozen),
                ),
            )
            self._conn.commit()
            return self.get_session_binding(session_id)  # type: ignore[return-value]

    def list_projects(self) -> list[ProjectRecord]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM manager_projects ORDER BY position, created_at_ms, project_id"
            ).fetchall()
        return [self._project_from_row(row) for row in rows]

    def list_workspaces(self, project_id: str) -> list[WorkspaceBinding]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM manager_workspace_bindings WHERE project_id = ? "
                "ORDER BY created_at_ms, workspace_binding_id",
                (project_id,),
            ).fetchall()
        return [self._workspace_from_row(row) for row in rows]

    def get_project(self, project_id: str) -> ProjectRecord:
        row = self._project_row(project_id)
        if row is None:
            raise ProjectStoreError("project not found")
        return self._project_from_row(row)

    def get_workspace(self, workspace_binding_id: str) -> WorkspaceBinding:
        row = self._workspace_row(workspace_binding_id)
        if row is None:
            raise ProjectStoreError("workspace binding not found")
        return self._workspace_from_row(row)

    def get_session_binding(self, session_id: str) -> SessionProjectBinding | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM manager_session_project_bindings WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        if row is None:
            return None
        return SessionProjectBinding(
            session_id=str(row["session_id"]),
            project_id=str(row["project_id"]),
            workspace_binding_id=row["workspace_binding_id"],
            endpoint_id=str(row["endpoint_id"]),
            endpoint_fingerprint=row["endpoint_fingerprint"],
            branch_snapshot=row["branch_snapshot"],
            workspace_revision=row["workspace_revision"],
            frozen=bool(row["frozen"]),
        )

    def freeze_session_binding(self, session_id: str) -> SessionProjectBinding | None:
        with self._lock:
            self._conn.execute(
                "UPDATE manager_session_project_bindings SET frozen = 1 WHERE session_id = ?",
                (session_id,),
            )
            self._conn.commit()
        return self.get_session_binding(session_id)

    def _project_row(self, project_id: str) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM manager_projects WHERE project_id = ?", (project_id,)
            ).fetchone()

    def _workspace_row(self, workspace_binding_id: str) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM manager_workspace_bindings WHERE workspace_binding_id = ?",
                (workspace_binding_id,),
            ).fetchone()

    @staticmethod
    def _project_from_row(row: sqlite3.Row) -> ProjectRecord:
        return ProjectRecord(
            project_id=str(row["project_id"]),
            canonical_key=str(row["canonical_key"]),
            name=str(row["name"]),
            primary_workspace_binding_id=row["primary_workspace_binding_id"],
            default_endpoint_id=str(row["default_endpoint_id"]),
            pinned=bool(row["pinned"]),
            position=int(row["position"]),
            archived=bool(row["archived"]),
            created_at_ms=int(row["created_at_ms"]),
            updated_at_ms=int(row["updated_at_ms"]),
        )

    @staticmethod
    def _workspace_from_row(row: sqlite3.Row) -> WorkspaceBinding:
        git_raw = row["git_json"]
        return WorkspaceBinding(
            workspace_binding_id=str(row["workspace_binding_id"]),
            project_id=str(row["project_id"]),
            location=str(row["location"]),
            endpoint_id=str(row["endpoint_id"]),
            local_path=row["local_path"],
            remote_workspace_ref=row["remote_workspace_ref"],
            display_path=str(row["display_path"]),
            state=str(row["state"]),
            git=json.loads(git_raw) if git_raw else None,
            created_at_ms=int(row["created_at_ms"]),
            updated_at_ms=int(row["updated_at_ms"]),
        )
