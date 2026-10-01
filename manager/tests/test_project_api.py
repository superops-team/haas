from __future__ import annotations

import subprocess
from pathlib import Path

from coworker.haas.endpoint import EndpointMode, HaasEndpoint
from coworker.providers import AssistantTurn, ModelCapabilities, ProviderClient
from coworker.server import SessionManager, create_app
from coworker.sessions import SessionRecord
from fastapi.testclient import TestClient


class _Provider(ProviderClient):
    def complete(self, **_kwargs):
        return AssistantTurn(text="done", finish_reason="stop")

    def capabilities(self, _model):
        return ModelCapabilities()


def _git(*args: str, cwd) -> str:
    result = subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


def _manager(tmp_path) -> SessionManager:
    return SessionManager(data_dir=tmp_path / "state", provider=_Provider())


def test_project_list_is_the_legacy_migration_barrier(tmp_path):
    repo = tmp_path / "haas"
    repo.mkdir()
    _git("init", cwd=repo)
    manager = _manager(tmp_path)
    manager.session_store.save(
        SessionRecord(
            session_id="session-repo",
            workspace=str(repo),
            model="model",
            mode="interactive",
            agent="cowork",
            title="Repository task",
        )
    )
    manager.session_store.save(
        SessionRecord(
            session_id="session-personal",
            workspace="",
            model="model",
            mode="interactive",
            agent="cowork",
            title="Personal task",
        )
    )
    client = TestClient(create_app(manager))

    projection = client.get("/v1/projects").json()
    projects = projection["projects"]

    assert {project["name"] for project in projects} >= {"haas", "Personal"}
    assert isinstance(projection["orderRevision"], int)
    assert all("pinned" in project and "capabilities" in project for project in projects)
    session_rows = [session for project in projects for session in project["sessions"]]
    assert {session["session_id"] for session in session_rows} == {
        "session-repo",
        "session-personal",
    }
    assert len({session["session_id"] for session in session_rows}) == len(session_rows)
    assert all(session["projectId"] for session in session_rows)


def test_create_project_requires_idempotency_and_reuses_duplicate_workspace(tmp_path):
    workspace = tmp_path / "service"
    workspace.mkdir()
    manager = _manager(tmp_path)
    client = TestClient(create_app(manager))
    body = {
        "name": "Service",
        "workspace": {"location": "local", "path": str(workspace)},
        "defaultEndpointId": "hep_local_managed",
    }

    missing = client.post("/v1/projects", json=body)
    assert missing.status_code == 400
    assert missing.json()["error"]["code"] == "idempotency_key_required"

    first = client.post(
        "/v1/projects", json=body, headers={"Idempotency-Key": "project-create-1"}
    )
    assert first.status_code == 201
    replay = client.post(
        "/v1/projects", json=body, headers={"Idempotency-Key": "project-create-1"}
    )
    assert replay.status_code == 201
    assert replay.json() == first.json()

    duplicate = client.post(
        "/v1/projects",
        json={**body, "name": "Another name"},
        headers={"Idempotency-Key": "project-create-2"},
    )
    assert duplicate.status_code == 201
    assert duplicate.json()["project"]["projectId"] == first.json()["project"]["projectId"]
    assert (
        duplicate.json()["workspace"]["workspaceBindingId"]
        == first.json()["workspace"]["workspaceBindingId"]
    )


def test_local_project_rejects_a_remote_default_endpoint(tmp_path):
    workspace = tmp_path / "service"
    workspace.mkdir()
    manager = _manager(tmp_path)
    manager.endpoint_store.put(
        HaasEndpoint.create(
            endpoint_id="hep_remote_team",
            mode=EndpointMode.REMOTE,
            base_url="https://haas.example.invalid",
            token_ref="secret://manager/haas/remote-team",
            server_identity="team-dev",
        )
    )
    client = TestClient(create_app(manager))

    response = client.post(
        "/v1/projects",
        json={
            "name": "Service",
            "workspace": {"location": "local", "path": str(workspace)},
            "defaultEndpointId": "hep_remote_team",
        },
        headers={"Idempotency-Key": "project-local-remote-endpoint"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "endpoint_unavailable"


def test_project_can_add_a_remote_workspace_binding_idempotently(tmp_path):
    workspace = tmp_path / "service"
    workspace.mkdir()
    manager = _manager(tmp_path)
    local = manager.project_store.ensure_local_project(workspace, name="Service")
    manager.endpoint_store.put(
        HaasEndpoint.create(
            endpoint_id="hep_remote_team",
            mode=EndpointMode.REMOTE,
            base_url="https://haas.example.invalid",
            token_ref="secret://manager/haas/remote-team",
            server_identity="team-dev",
        )
    )
    client = TestClient(create_app(manager))
    body = {
        "location": "remote",
        "endpointId": "hep_remote_team",
        "remoteWorkspaceRef": "/srv/workspaces/service",
        "displayPath": "team-dev · service",
    }

    first = client.post(
        f"/v1/projects/{local.project.project_id}/workspaces",
        json=body,
        headers={"Idempotency-Key": "workspace-add-remote"},
    )
    replay = client.post(
        f"/v1/projects/{local.project.project_id}/workspaces",
        json=body,
        headers={"Idempotency-Key": "workspace-add-remote"},
    )

    assert first.status_code == 201
    assert replay.json() == first.json()
    assert first.json()["workspace"]["projectId"] == local.project.project_id
    assert first.json()["workspace"]["remoteWorkspaceRef"] == "/srv/workspaces/service"
    assert len(manager.project_store.list_workspaces(local.project.project_id)) == 2


def test_project_workspace_git_and_endpoint_views_are_safe(tmp_path):
    workspace = tmp_path / "haas"
    workspace.mkdir()
    _git("init", cwd=workspace)
    _git("checkout", "-b", "feature/safe-view", cwd=workspace)
    (workspace / "dirty.txt").write_text("dirty", encoding="utf-8")
    manager = _manager(tmp_path)
    created = manager.project_store.ensure_local_project(workspace, name="haas")
    manager.endpoint_store.put(
        HaasEndpoint.create(
            endpoint_id="hep_remote_team",
            mode=EndpointMode.REMOTE,
            base_url="https://haas.example.invalid",
            token_ref="secret://manager/haas/remote-team",
            server_identity="team-dev",
        )
    )
    client = TestClient(create_app(manager))

    project_id = created.project.project_id
    workspaces = client.get(f"/v1/projects/{project_id}/workspaces").json()["workspaces"]
    assert workspaces[0]["workspaceBindingId"] == created.workspace.workspace_binding_id

    git = client.get(
        f"/v1/workspaces/{created.workspace.workspace_binding_id}/git"
    ).json()["git"]
    assert git["isRepository"] is True
    assert git["branchName"] == "feature/safe-view"
    assert git["dirtyFileCount"] == 1
    assert "diff" not in git

    endpoints = client.get("/v1/haas/endpoints").json()["endpoints"]
    remote = next(item for item in endpoints if item["endpointId"] == "hep_remote_team")
    assert remote["mode"] == "remote"
    assert remote["serverIdentity"] == "team-dev"
    assert "tokenRef" not in remote
    assert "baseUrl" not in remote


def test_git_branch_create_and_switch_are_guarded_and_idempotent(tmp_path):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    _git("init", cwd=workspace)
    _git("config", "user.email", "test@example.invalid", cwd=workspace)
    _git("config", "user.name", "Test", cwd=workspace)
    (workspace / "README.md").write_text("test\n", encoding="utf-8")
    _git("add", "README.md", cwd=workspace)
    _git("commit", "-m", "initial", cwd=workspace)
    manager = _manager(tmp_path)
    created = manager.project_store.ensure_local_project(workspace, name="repo")
    client = TestClient(create_app(manager))
    workspace_id = created.workspace.workspace_binding_id
    initial = client.get(f"/v1/workspaces/{workspace_id}/git").json()["git"]

    body = {
        "branchName": "feature/project-workbench",
        "observedRevision": initial["observedRevision"],
    }
    first = client.post(
        f"/v1/workspaces/{workspace_id}/git/branches",
        json=body,
        headers={"Idempotency-Key": "git-create-1"},
    )
    assert first.status_code == 200
    assert first.json()["git"]["branchName"] == "feature/project-workbench"
    assert client.post(
        f"/v1/workspaces/{workspace_id}/git/branches",
        json=body,
        headers={"Idempotency-Key": "git-create-1"},
    ).json() == first.json()

    stale = client.post(
        f"/v1/workspaces/{workspace_id}/git/switch",
        json={"branchName": "master", "observedRevision": "stale"},
        headers={"Idempotency-Key": "git-switch-stale"},
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "git_state_stale"


def test_project_patch_reorder_and_sidebar_order_are_authoritative(tmp_path):
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"
    first_dir.mkdir()
    second_dir.mkdir()
    manager = _manager(tmp_path)
    first = manager.project_store.ensure_local_project(first_dir, name="First").project
    second = manager.project_store.ensure_local_project(second_dir, name="Second").project
    client = TestClient(create_app(manager))
    revision = client.get("/v1/projects").json()["orderRevision"]

    missing_key = client.patch(
        f"/v1/projects/{first.project_id}", json={"pinned": True}
    )
    assert missing_key.status_code == 400
    assert missing_key.json()["error"]["code"] == "idempotency_key_required"

    patched = client.patch(
        f"/v1/projects/{first.project_id}",
        json={"name": "Renamed", "pinned": True},
        headers={"Idempotency-Key": "project-patch-1"},
    )
    assert patched.status_code == 200
    assert patched.json()["project"]["name"] == "Renamed"
    assert patched.json()["project"]["pinned"] is True
    assert patched.json()["orderRevision"] == revision + 1

    reordered = client.post(
        "/v1/projects/reorder",
        json={
            "projectIds": [second.project_id, first.project_id],
            "observedRevision": patched.json()["orderRevision"],
        },
        headers={"Idempotency-Key": "project-reorder-1"},
    )
    assert reordered.status_code == 200
    assert [row["projectId"] for row in reordered.json()["projects"] if row["projectId"] != "prj_personal"] == [
        second.project_id,
        first.project_id,
    ]

    stale = client.post(
        "/v1/projects/reorder",
        json={
            "projectIds": [first.project_id, second.project_id],
            "observedRevision": patched.json()["orderRevision"],
        },
        headers={"Idempotency-Key": "project-reorder-stale"},
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "project_order_stale"

    order = client.post(
        "/v1/settings/sidebar-order",
        json={"projectOrder": "name", "conversationOrder": "oldest"},
        headers={"Idempotency-Key": "sidebar-order-1"},
    )
    assert order.status_code == 200
    assert order.json() == {"projectOrder": "name", "conversationOrder": "oldest"}
    assert client.get("/v1/settings").json()["project_order"] == "name"
    assert client.get("/v1/settings").json()["conversation_order"] == "oldest"

    invalid_boolean = client.patch(
        f"/v1/projects/{first.project_id}",
        json={"archived": "false"},
        headers={"Idempotency-Key": "project-invalid-boolean"},
    )
    assert invalid_boolean.status_code == 422
    assert invalid_boolean.json()["error"]["code"] == "project_invalid"
    assert manager.project_store.get_project(first.project_id).archived is False

    unknown_field = client.patch(
        f"/v1/projects/{first.project_id}",
        json={"deleteFiles": True},
        headers={"Idempotency-Key": "project-unknown-field"},
    )
    assert unknown_field.status_code == 422
    assert unknown_field.json()["error"]["code"] == "project_invalid"

    manager.endpoint_store.put(
        HaasEndpoint.create(
            endpoint_id="hep_unbound_remote",
            mode=EndpointMode.REMOTE,
            base_url="https://unbound.example.invalid",
            token_ref="secret://manager/haas/unbound",
            server_identity="unbound",
        )
    )
    unbound_endpoint = client.patch(
        f"/v1/projects/{first.project_id}",
        json={"defaultEndpointId": "hep_unbound_remote"},
        headers={"Idempotency-Key": "project-unbound-endpoint"},
    )
    assert unbound_endpoint.status_code == 422
    assert unbound_endpoint.json()["error"]["code"] == "endpoint_unavailable"


def test_project_remove_and_archive_reject_busy_then_preserve_history(tmp_path):
    workspace = tmp_path / "service"
    workspace.mkdir()
    manager = _manager(tmp_path)
    created = manager.project_store.ensure_local_project(workspace, name="Service")
    session = SessionRecord(
        session_id="session-service",
        workspace=str(workspace),
        model="model",
        mode="interactive",
        agent="cowork",
        title="Service task",
    )
    manager.session_store.save(session)
    manager.project_store.ensure_session_binding(
        session.session_id,
        workspace=str(workspace),
        explicit_project_id=created.project.project_id,
        explicit_workspace_binding_id=created.workspace.workspace_binding_id,
    )
    client = TestClient(create_app(manager))
    manager.mark_running(session.session_id)

    busy = client.post(
        f"/v1/projects/{created.project.project_id}/sessions/archive",
        json={},
        headers={"Idempotency-Key": "archive-busy"},
    )
    assert busy.status_code == 409
    assert busy.json()["error"]["code"] == "project_sessions_busy"
    busy_remove = client.patch(
        f"/v1/projects/{created.project.project_id}",
        json={"archived": True},
        headers={"Idempotency-Key": "remove-busy-project"},
    )
    assert busy_remove.status_code == 409
    assert busy_remove.json()["error"]["code"] == "project_sessions_busy"

    manager.mark_idle(session.session_id)
    queued = manager.conversation_commands.accept(
        session_id=session.session_id,
        client_command_id="queued-before-archive",
        idempotency_key="queued-before-archive",
        delivery="enqueue",
        payload={"text": "queued work"},
        busy=True,
    )
    assert queued.queue_item_id
    queued_busy = client.post(
        f"/v1/projects/{created.project.project_id}/sessions/archive",
        json={},
        headers={"Idempotency-Key": "archive-queued"},
    )
    assert queued_busy.status_code == 409
    manager.conversation_commands.delete_queue_item(
        session.session_id,
        queued.queue_item_id,
        expected_revision=1,
    )
    archived = client.post(
        f"/v1/projects/{created.project.project_id}/sessions/archive",
        json={},
        headers={"Idempotency-Key": "archive-idle"},
    )
    assert archived.status_code == 200
    assert archived.json()["archivedSessionIds"] == [session.session_id]
    assert manager.session_store.load(session.session_id).archived is True
    assert manager.project_store.get_project(created.project.project_id).archived is False

    removed = client.patch(
        f"/v1/projects/{created.project.project_id}",
        json={"archived": True},
        headers={"Idempotency-Key": "remove-project"},
    )
    assert removed.status_code == 200
    assert removed.json()["project"]["archived"] is True
    assert manager.project_store.list_workspaces(created.project.project_id)
    assert manager.session_store.load(session.session_id) is not None
    assert manager.try_mark_running(session.session_id) is False


def test_sidebar_order_preferences_survive_manager_restart(tmp_path):
    manager = _manager(tmp_path)
    client = TestClient(create_app(manager))
    response = client.post(
        "/v1/settings/sidebar-order",
        json={"projectOrder": "recent", "conversationOrder": "name"},
        headers={"Idempotency-Key": "sidebar-order-persist"},
    )
    assert response.status_code == 200

    restored = _manager(tmp_path)
    settings = restored.get_settings()
    assert settings["project_order"] == "recent"
    assert settings["conversation_order"] == "name"


def test_project_reveal_and_persistent_worktree_use_server_resolved_paths(
    tmp_path, monkeypatch
):
    repo = tmp_path / "service"
    repo.mkdir()
    _git("init", cwd=repo)
    _git("config", "user.email", "test@example.invalid", cwd=repo)
    _git("config", "user.name", "Test", cwd=repo)
    (repo / "README.md").write_text("test\n", encoding="utf-8")
    _git("add", "README.md", cwd=repo)
    _git("commit", "-m", "initial", cwd=repo)
    manager = _manager(tmp_path)
    created = manager.project_store.ensure_local_project(repo, name="Service")
    client = TestClient(create_app(manager))
    opened: list[list[str]] = []

    class _Process:
        pass

    def _popen(args, **_kwargs):
        opened.append(list(args))
        return _Process()

    monkeypatch.setattr("coworker.server.manager.subprocess.Popen", _popen)
    reveal = client.post(
        f"/v1/projects/{created.project.project_id}/reveal",
        json={},
        headers={"Idempotency-Key": "reveal-project"},
    )
    assert reveal.status_code == 200
    assert reveal.json() == {"ok": True}
    assert opened and str(repo.resolve()) in opened[0]
    monkeypatch.undo()

    preview = client.get(
        f"/v1/projects/{created.project.project_id}/worktrees/preview",
        params={"branchName": "feature/persistent-worktree"},
    )
    assert preview.status_code == 200
    assert Path(preview.json()["displayPath"]) == (
        tmp_path / "service-feature-persistent-worktree"
    )

    worktree = client.post(
        f"/v1/projects/{created.project.project_id}/worktrees",
        json={"branchName": "feature/persistent-worktree"},
        headers={"Idempotency-Key": "project-worktree"},
    )
    assert worktree.status_code == 201
    destination = Path(worktree.json()["workspace"]["localPath"])
    assert destination == tmp_path / "service-feature-persistent-worktree"
    assert destination.is_dir()
    assert worktree.json()["workspace"]["projectId"] == created.project.project_id
    assert client.post(
        f"/v1/projects/{created.project.project_id}/worktrees",
        json={"branchName": "feature/persistent-worktree"},
        headers={"Idempotency-Key": "project-worktree"},
    ).json() == worktree.json()


def test_remote_project_capabilities_disable_local_actions(tmp_path):
    manager = _manager(tmp_path)
    manager.endpoint_store.put(
        HaasEndpoint.create(
            endpoint_id="hep_remote_team",
            mode=EndpointMode.REMOTE,
            base_url="https://haas.example.invalid",
            token_ref="secret://manager/haas/remote-team",
            server_identity="team-dev",
        )
    )
    created = manager.project_store.create_remote_project(
        name="Remote",
        endpoint_id="hep_remote_team",
        remote_workspace_ref="workspace-1",
        display_path="Remote workspace",
        idempotency_key="remote-project",
    )
    client = TestClient(create_app(manager))

    project = next(
        row
        for row in client.get("/v1/projects").json()["projects"]
        if row["projectId"] == created.project.project_id
    )
    assert project["capabilities"]["reveal"]["enabled"] is False
    assert project["capabilities"]["createWorktree"]["enabled"] is False
    reveal = client.post(
        f"/v1/projects/{created.project.project_id}/reveal",
        json={},
        headers={"Idempotency-Key": "remote-reveal"},
    )
    assert reveal.status_code == 422
    assert reveal.json()["error"]["code"] == "project_capability_unsupported"
