from __future__ import annotations

import subprocess

import pytest
from coworker.project_workbench import (
    PERSONAL_PROJECT_ID,
    ProjectStore,
    ProjectStoreConflict,
    ProjectStoreError,
)


def _git(*args: str, cwd) -> None:
    subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )


def test_git_worktrees_share_one_project_but_keep_distinct_workspace_bindings(tmp_path):
    repo = tmp_path / "haas"
    worktree = tmp_path / "haas-feature"
    repo.mkdir()
    _git("init", cwd=repo)
    _git("config", "user.email", "test@example.invalid", cwd=repo)
    _git("config", "user.name", "Test", cwd=repo)
    (repo / "README.md").write_text("test\n", encoding="utf-8")
    _git("add", "README.md", cwd=repo)
    _git("commit", "-m", "initial", cwd=repo)
    _git("worktree", "add", "-b", "feature/test", str(worktree), cwd=repo)

    store = ProjectStore(tmp_path / "state.db")
    first = store.ensure_local_project(repo, name="haas")
    second = store.ensure_local_project(worktree, name="ignored duplicate name")

    assert first.project.project_id == second.project.project_id
    assert first.project.name == "haas"
    assert first.workspace.workspace_binding_id != second.workspace.workspace_binding_id
    assert {item.local_path for item in store.list_workspaces(first.project.project_id)} == {
        str(repo.resolve()),
        str(worktree.resolve()),
    }


def test_plain_folders_with_same_display_name_remain_distinct_projects(tmp_path):
    left = tmp_path / "left" / "service"
    right = tmp_path / "right" / "service"
    left.mkdir(parents=True)
    right.mkdir(parents=True)
    store = ProjectStore(tmp_path / "state.db")

    first = store.ensure_local_project(left, name="service")
    second = store.ensure_local_project(right, name="service")

    assert first.project.project_id != second.project.project_id
    assert first.project.name == second.project.name == "service"
    assert len(store.list_projects()) == 3  # two explicit projects + built-in Personal


def test_workspace_less_session_migrates_to_personal_and_explicit_binding_wins(tmp_path):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    store = ProjectStore(tmp_path / "state.db")
    explicit = store.ensure_local_project(workspace, name="Explicit")

    personal = store.ensure_session_binding("session-personal", workspace=None)
    restored = store.ensure_session_binding(
        "session-explicit",
        workspace=str(tmp_path / "different-missing-path"),
        explicit_project_id=explicit.project.project_id,
        explicit_workspace_binding_id=explicit.workspace.workspace_binding_id,
    )

    assert personal.project_id == "prj_personal"
    assert personal.workspace_binding_id is None
    assert restored.project_id == explicit.project.project_id
    assert restored.workspace_binding_id == explicit.workspace.workspace_binding_id
    assert store.ensure_session_binding("session-personal", workspace=None) == personal


def test_remote_identity_uses_endpoint_and_opaque_workspace_ref(tmp_path):
    store = ProjectStore(tmp_path / "state.db")
    created = store.create_remote_project(
        name="haas",
        endpoint_id="hep_team_dev",
        remote_workspace_ref="wsref_opaque",
        display_path="~/workspace/github/haas",
        idempotency_key="idem-remote-1",
    )
    replay = store.create_remote_project(
        name="haas",
        endpoint_id="hep_team_dev",
        remote_workspace_ref="wsref_opaque",
        display_path="~/workspace/github/haas",
        idempotency_key="idem-remote-1",
    )

    assert replay == created
    assert created.workspace.local_path is None
    assert created.workspace.remote_workspace_ref == "wsref_opaque"
    assert "/Users/" not in created.project.canonical_key

    with pytest.raises(ProjectStoreConflict, match="idempotency"):
        store.create_remote_project(
            name="different",
            endpoint_id="hep_team_dev",
            remote_workspace_ref="wsref_other",
            display_path="safe",
            idempotency_key="idem-remote-1",
        )


@pytest.mark.parametrize("workspace_ref", ["bad\nref", "x" * 1025])
def test_remote_workspace_reference_rejects_control_chars_and_oversize(
    tmp_path, workspace_ref
):
    store = ProjectStore(tmp_path / "state.db")

    with pytest.raises(ValueError, match="remote endpoint, workspace reference"):
        store.create_remote_project(
            name="remote",
            endpoint_id="hep_team_dev",
            remote_workspace_ref=workspace_ref,
            display_path="remote",
            idempotency_key="invalid-remote-ref",
        )


def test_project_metadata_mutations_are_revisioned_idempotent_and_non_destructive(
    tmp_path,
):
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"
    first_dir.mkdir()
    second_dir.mkdir()
    store = ProjectStore(tmp_path / "state.db")
    first = store.ensure_local_project(first_dir, name="First").project
    second = store.ensure_local_project(second_dir, name="Second").project
    initial_revision = store.order_revision()

    updated, revision = store.update_project(
        first.project_id,
        name="Renamed First",
        pinned=True,
        idempotency_key="update-first",
    )
    replay, replay_revision = store.update_project(
        first.project_id,
        name="Renamed First",
        pinned=True,
        idempotency_key="update-first",
    )

    assert updated == replay
    assert revision == replay_revision == initial_revision + 1
    assert updated.name == "Renamed First"
    assert updated.pinned is True
    assert store.list_workspaces(first.project_id)

    reordered, reordered_revision = store.reorder_projects(
        [second.project_id, first.project_id],
        observed_revision=revision,
        idempotency_key="reorder-projects",
    )
    assert [project.project_id for project in reordered] == [
        second.project_id,
        first.project_id,
    ]
    assert reordered_revision == revision + 1

    with pytest.raises(ProjectStoreConflict, match="stale"):
        store.reorder_projects(
            [first.project_id, second.project_id],
            observed_revision=revision,
            idempotency_key="stale-reorder",
        )

    with pytest.raises(ProjectStoreConflict, match="idempotency"):
        store.update_project(
            first.project_id,
            name="Different",
            idempotency_key="update-first",
        )

    with pytest.raises(ProjectStoreError, match="protected"):
        store.update_project(
            PERSONAL_PROJECT_ID,
            archived=True,
            idempotency_key="archive-personal",
        )


def test_existing_project_table_migrates_pinned_without_losing_order(tmp_path):
    import sqlite3

    db = tmp_path / "legacy.db"
    connection = sqlite3.connect(db)
    connection.execute(
        """
        CREATE TABLE manager_projects (
            project_id TEXT PRIMARY KEY,
            canonical_key TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            primary_workspace_binding_id TEXT,
            default_endpoint_id TEXT NOT NULL,
            position INTEGER NOT NULL DEFAULT 0,
            archived INTEGER NOT NULL DEFAULT 0,
            created_at_ms INTEGER NOT NULL,
            updated_at_ms INTEGER NOT NULL
        )
        """
    )
    connection.execute(
        "INSERT INTO manager_projects VALUES (?, ?, ?, NULL, ?, ?, 0, ?, ?)",
        ("prj_existing", "/existing", "Existing", "hep_local_managed", 7, 1, 1),
    )
    connection.commit()
    connection.close()

    store = ProjectStore(db)
    existing = store.get_project("prj_existing")

    assert existing.position == 7
    assert existing.pinned is False
    assert store.order_revision() >= 0


def test_project_mutation_validation_and_replay_edges(tmp_path):
    workspace = tmp_path / "workspace"
    other_workspace = tmp_path / "other"
    workspace.mkdir()
    other_workspace.mkdir()
    store = ProjectStore(tmp_path / "state.db")
    project = store.ensure_local_project(workspace, name="Project").project
    other = store.ensure_local_project(other_workspace, name="Other").project

    with pytest.raises(ProjectStoreError, match="workspace unavailable"):
        store.create_local_project(
            name="Missing",
            workspace=tmp_path / "missing",
            idempotency_key="missing-workspace",
        )
    with pytest.raises(ProjectStoreError, match="idempotency"):
        store.create_local_project(name="Project", workspace=workspace, idempotency_key="")
    with pytest.raises(ProjectStoreError, match="remote workspace"):
        store.add_remote_workspace(
            project.project_id,
            endpoint_id="",
            remote_workspace_ref="",
            display_path="",
            idempotency_key="",
        )

    invalid_updates = [
        {"name": 1},
        {"name": "   "},
        {"default_endpoint_id": 1},
        {"default_endpoint_id": ""},
        {"pinned": "false"},
        {"archived": "false"},
    ]
    for index, patch in enumerate(invalid_updates):
        with pytest.raises(ProjectStoreError):
            store.update_project(
                project.project_id,
                idempotency_key=f"invalid-{index}",
                **patch,  # type: ignore[arg-type]
            )
    with pytest.raises(ProjectStoreError, match="empty"):
        store.update_project(project.project_id, idempotency_key="empty-update")
    with pytest.raises(ProjectStoreError, match="cannot be pinned"):
        store.update_project(
            project.project_id,
            pinned=True,
            archived=True,
            idempotency_key="pin-and-archive",
        )
    store.update_project(
        project.project_id, archived=True, idempotency_key="archive-project"
    )
    with pytest.raises(ProjectStoreError, match="cannot be pinned"):
        store.update_project(
            project.project_id, pinned=True, idempotency_key="pin-archived"
        )

    current_revision = store.order_revision()
    ordered, revision = store.reorder_projects(
        [other.project_id],
        observed_revision=current_revision,
        idempotency_key="reorder-edge",
    )
    replayed, replay_revision = store.reorder_projects(
        [other.project_id],
        observed_revision=current_revision,
        idempotency_key="reorder-edge",
    )
    assert replayed == ordered
    assert replay_revision == revision
    with pytest.raises(ProjectStoreError, match="idempotency"):
        store.reorder_projects(
            [other.project_id], observed_revision=revision, idempotency_key=""
        )
    with pytest.raises(ProjectStoreError, match="every visible"):
        store.reorder_projects(
            [other.project_id, other.project_id],
            observed_revision=revision,
            idempotency_key="duplicate-order",
        )
