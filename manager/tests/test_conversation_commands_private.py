"""Private command persistence must be established before SQLite opens any content."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from coworker.conversation_commands import ConversationCommandStore

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX file mode contract")


def test_wal_files_are_private_under_normal_umask(tmp_path):
    script = """
import json, os, stat, sys
from pathlib import Path
from coworker.conversation_commands import ConversationCommandStore
os.umask(0o022)
path = Path(sys.argv[1]) / 'private-store' / 'commands.db'
store = ConversationCommandStore(path)
store.accept(session_id='session', client_command_id='command', idempotency_key='key',
             delivery='start_now', payload={'text': 'synthetic input'}, busy=False)
files = [path, Path(str(path) + '-wal'), Path(str(path) + '-shm')]
print(json.dumps({'files': [stat.S_IMODE(p.stat().st_mode) for p in files],
                  'directory': stat.S_IMODE(path.parent.stat().st_mode)}))
store.close()
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path)],
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])},
        capture_output=True,
        text=True,
        check=True,
        timeout=15,
    )
    assert json.loads(result.stdout) == {"files": [0o600] * 3, "directory": 0o700}


def test_private_mode_is_established_before_sqlite_connect(tmp_path, monkeypatch):
    import coworker.conversation_commands as commands

    path = tmp_path / "commands.db"
    os.chmod(tmp_path, 0o755)
    connect = commands.sqlite3.connect

    def inspect_connect(database, **kwargs):
        assert stat.S_IMODE(Path(database).stat().st_mode) == 0o600
        assert stat.S_IMODE(tmp_path.stat().st_mode) == 0o755
        return connect(database, **kwargs)

    monkeypatch.setattr(commands.sqlite3, "connect", inspect_connect)
    store = ConversationCommandStore(path)
    store.close()


def test_new_store_directory_is_private_without_changing_existing_parent(tmp_path):
    os.chmod(tmp_path, 0o755)
    directory = tmp_path / "private-store"
    store = ConversationCommandStore(directory / "commands.db")
    try:
        assert stat.S_IMODE(directory.stat().st_mode) == 0o700
        assert stat.S_IMODE(tmp_path.stat().st_mode) == 0o755
    finally:
        store.close()


def test_reopen_repairs_existing_sidecars_without_losing_payload(tmp_path):
    path = tmp_path / "commands.db"
    first = ConversationCommandStore(path)
    first.accept(
        session_id="session", client_command_id="command", idempotency_key="key",
        delivery="start_now", payload={"text": "retained synthetic input"}, busy=False,
    )
    files = [path, Path(str(path) + "-wal"), Path(str(path) + "-shm")]
    for file in files:
        os.chmod(file, 0o644)
    second = ConversationCommandStore(path)
    try:
        assert [stat.S_IMODE(file.stat().st_mode) for file in files] == [0o600] * 3
        assert second.payload("command", session_id="session") == {
            "text": "retained synthetic input"
        }
    finally:
        second.close()
        first.close()


@pytest.mark.parametrize("suffix", ["", "-wal", "-shm", "-journal"])
@pytest.mark.parametrize("link", ["symbolic", "hard"])
def test_linked_database_files_are_rejected_without_touching_target(tmp_path, suffix, link):
    path = tmp_path / "commands.db"
    target = tmp_path / "unrelated"
    target.write_text("untouched synthetic content")
    os.chmod(target, 0o644)
    linked = Path(str(path) + suffix)
    if link == "symbolic":
        linked.symlink_to(target)
    else:
        os.link(target, linked)
    with pytest.raises(OSError):
        ConversationCommandStore(path)
    assert target.read_text() == "untouched synthetic content"
    assert stat.S_IMODE(target.stat().st_mode) == 0o644


def test_non_regular_sidecar_is_rejected(tmp_path):
    path = tmp_path / "commands.db"
    journal = Path(f"{path}-journal")
    os.mkfifo(journal)
    with pytest.raises(OSError, match="not regular"):
        ConversationCommandStore(path)


def test_foreign_owned_file_is_rejected_before_mode_change(tmp_path, monkeypatch):
    import coworker.conversation_commands as commands

    path = tmp_path / "commands.db"
    path.touch(mode=0o644)
    real = os.stat(path)
    monkeypatch.setattr(commands.os, "fstat", lambda _fd: SimpleNamespace(
        st_mode=real.st_mode, st_uid=os.getuid() + 1, st_nlink=1,
    ))
    with pytest.raises(PermissionError):
        ConversationCommandStore(path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o644


def test_permission_failure_aborts_before_sqlite_open(tmp_path, monkeypatch):
    import coworker.conversation_commands as commands

    def deny_mode(_fd, _mode):
        raise PermissionError("synthetic mode failure")

    def unexpected_connect(*_args, **_kwargs):
        pytest.fail("SQLite opened before private permissions were established")

    monkeypatch.setattr(commands.os, "fchmod", deny_mode)
    monkeypatch.setattr(commands.sqlite3, "connect", unexpected_connect)
    with pytest.raises(PermissionError, match="synthetic mode failure"):
        ConversationCommandStore(tmp_path / "commands.db")
