from __future__ import annotations

import json
import sys
import types
import zipfile
from contextlib import suppress

import pytest
from coworker.automation import Schedule, ScheduledTask, TaskRun, TaskStore
from coworker.cli import main as cli_main
from coworker.conversations import ConversationStore
from coworker.sessions import SessionRecord
from coworker.state_backup import BackupError, create_backup, inspect_backup, restore_backup


def _seed_state(path):
    store = ConversationStore(path)
    store.save(
        SessionRecord(
            session_id="sess123",
            workspace="/tmp/project",
            model="m",
            mode="interactive",
            messages=[{"role": "user", "content": "hello"}],
            grants={"tools": ["run_shell"], "commands": ["git"]},
            bindings={
                "memory": "project-alpha",
                "haas_delegation": {
                    "delegated_session_id": "delsess_1",
                    "runtime": {"pid": 123},
                },
            },
        )
    )
    task_store = TaskStore(path / "automation.db")
    task = ScheduledTask(
        title="Daily",
        instructions="brief me",
        schedule=Schedule(kind="cron", cron="0 9 * * *"),
        workspace="/tmp/project",
        enabled=True,
    )
    task_store.save(task)
    task_store.add_run(TaskRun(task_id=task.id, status="running"))
    task_store.close()
    store.close()

    (path / "prefs.json").write_text('{"theme":"system"}', encoding="utf-8")
    (path / "secrets.json").write_text('{"openai":{"api_key":"sk-secret"}}', encoding="utf-8")
    (path / ".env").write_text("TOKEN=secret\n", encoding="utf-8")
    (path / "haas-token").write_text("sidecar-secret\n", encoding="utf-8")
    (path / "haas-supervised.yaml").write_text("pid: 123\n", encoding="utf-8")
    (path / "logs").mkdir()
    (path / "logs" / "openworker-server.log").write_text("raw prompt secret\n", encoding="utf-8")
    (path / "attachments").mkdir()
    (path / "attachments" / "artifact.txt").write_text("portable attachment", encoding="utf-8")
    with suppress(OSError):
        (path / "attachments" / "link").symlink_to(path / "secrets.json")


def test_backup_contains_only_portable_state(tmp_path):
    source = tmp_path / "state"
    source.mkdir()
    _seed_state(source)

    archive = tmp_path / "backup.zip"
    result = create_backup(source, archive)

    assert result["ok"] is True
    with zipfile.ZipFile(archive) as zf:
        names = set(zf.namelist())
        manifest = json.loads(zf.read("manifest.json"))
        assert "coworker.db" in names
        assert "automation.db" in names
        assert "conversations/sess123.jsonl" in names
        assert "prefs.json" in names
        assert "attachments/artifact.txt" in names
        assert "secrets.json" not in names
        assert ".env" not in names
        assert "haas-token" not in names
        assert not any(name.startswith("logs/") for name in names)
        assert not any(name.endswith("/link") for name in names)
        assert manifest["format"] == "openharness-state-backup"
        assert manifest["version"] == 1
        assert "secret" in manifest["excluded"]
        assert "runtime" in manifest["excluded"]
        assert "logs" in manifest["excluded"]
        assert "symlink" in manifest["excluded"]
        archive_bytes = b"".join(zf.read(name) for name in zf.namelist())
        assert b"sk-secret" not in archive_bytes
        assert b"sidecar-secret" not in archive_bytes

    assert inspect_backup(archive)["entryCount"] == len(names)


def test_backup_rejects_output_inside_source_state(tmp_path):
    source = tmp_path / "state"
    source.mkdir()
    with pytest.raises(BackupError):
        create_backup(source, source / "backup.zip")


def test_restore_sanitizes_execution_authority(tmp_path):
    source = tmp_path / "state"
    source.mkdir()
    _seed_state(source)
    archive = tmp_path / "backup.zip"
    create_backup(source, archive)

    target = tmp_path / "restored"
    result = restore_backup(archive, target)

    assert result["ok"] is True
    restored = ConversationStore(target)
    record = restored.load("sess123")
    assert record is not None
    assert record.messages[0]["content"] == "hello"
    assert record.grants == {}
    assert record.bindings == {"memory": "project-alpha"}
    restored.close()

    task_store = TaskStore(target / "automation.db")
    tasks = task_store.list()
    assert len(tasks) == 1
    assert tasks[0].enabled is False
    assert tasks[0].next_run is None
    assert tasks[0].last_status == "error"
    runs = task_store.runs(tasks[0].id)
    assert runs[0].status == "error"
    assert "restore_required" in (runs[0].error or "")
    task_store.close()

    assert not (target / "secrets.json").exists()
    assert not (target / "haas-token").exists()


def test_restore_rejects_unsafe_archive_path(tmp_path):
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(
            "manifest.json",
            json.dumps({"format": "openharness-state-backup", "version": 1}),
        )
        zf.writestr("../escape.txt", "no")

    with pytest.raises(BackupError):
        restore_backup(archive, tmp_path / "target")

    assert not (tmp_path / "escape.txt").exists()


def test_restore_rejects_nested_conversation_archive_entry(tmp_path):
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(
            "manifest.json",
            json.dumps({"format": "openharness-state-backup", "version": 1}),
        )
        zf.writestr("conversations/nested/sess.jsonl", "{}\n")

    with pytest.raises(BackupError):
        restore_backup(archive, tmp_path / "target")


def test_restore_rejects_duplicate_archive_entry(tmp_path):
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(
            "manifest.json",
            json.dumps({"format": "openharness-state-backup", "version": 1}),
        )
        zf.writestr("prefs.json", "{}")
        with pytest.warns(UserWarning, match="Duplicate name"):
            zf.writestr("prefs.json", "{\"theme\":\"evil\"}")

    with pytest.raises(BackupError):
        restore_backup(archive, tmp_path / "target")


def test_restore_rejects_non_empty_target_without_force(tmp_path):
    source = tmp_path / "state"
    source.mkdir()
    _seed_state(source)
    archive = tmp_path / "backup.zip"
    create_backup(source, archive)
    target = tmp_path / "target"
    target.mkdir()
    sentinel = target / "keep.txt"
    sentinel.write_text("keep", encoding="utf-8")

    with pytest.raises(BackupError):
        restore_backup(archive, target)

    assert sentinel.read_text(encoding="utf-8") == "keep"


def test_restore_rejects_unsupported_manifest_version(tmp_path):
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(
            "manifest.json",
            json.dumps({"format": "openharness-state-backup", "version": 999}),
        )

    with pytest.raises(BackupError):
        restore_backup(archive, tmp_path / "target")


def test_state_backup_and_inspect_cli_use_explicit_state_dir(tmp_path, capsys):
    source = tmp_path / "state"
    source.mkdir()
    _seed_state(source)
    archive = tmp_path / "backup.zip"

    cli_main(["state", "backup", str(archive), "--state-dir", str(source)])
    backup_output = json.loads(capsys.readouterr().out)
    assert backup_output["ok"] is True
    assert archive.is_file()

    cli_main(["state", "inspect", str(archive)])
    inspect_output = json.loads(capsys.readouterr().out)
    assert inspect_output["format"] == "openharness-state-backup"
    assert inspect_output["version"] == 1


def test_state_subcommand_does_not_break_existing_tui_cli(tmp_path, monkeypatch):
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    launched = {}

    class FakeApp:
        def __init__(self, **kwargs):
            launched.update(kwargs)

        def run(self):
            launched["ran"] = True

    module = types.ModuleType("coworker.tui.app")
    module.CoworkerApp = FakeApp
    monkeypatch.setitem(sys.modules, "coworker.tui.app", module)

    cli_main(["code", "--cwd", str(tmp_path), "--model", "model-x"])

    assert launched["ran"] is True
    assert launched["model"] == "model-x"
    assert launched["workspace"] == tmp_path.resolve()
