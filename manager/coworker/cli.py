"""CLI entry point. `coworker` launches the TUI; `coworker code` boots the code skill."""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from pathlib import Path

from .config import load_config
from .conversations import ConversationStore
from .memory import MemorySettingsStore, SQLiteMemoryStore
from .permissions import Mode
from .secrets import state_dir
from .state_backup import BackupError, create_backup, inspect_backup, restore_backup


def main(argv: list[str] | None = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["state"]:
        _state_main(argv[1:])
        return

    cfg = load_config()
    parser = argparse.ArgumentParser(prog="openworker", description="Agent coworker (TUI).")
    parser.add_argument("skill", nargs="?", default="code", help="skill to launch (default: code)")
    parser.add_argument("--cwd", default=".", help="workspace directory")
    parser.add_argument("--model", default=cfg.model, help="model id, e.g. openai gpt-5.5")
    parser.add_argument(
        "--mode",
        default=cfg.mode,
        choices=["plan", "interactive", "auto", "bypass-approvals", "auto-approve"],
        help="permission mode",
    )
    parser.add_argument("--resume", default=None, help="resume a session id")
    args = parser.parse_args(argv)

    workspace = Path(args.cwd).expanduser().resolve()
    # Unified global store shared with the GUI/server (one place for all conversations).
    data_dir = state_dir()
    # Same on/off switch and user rules the GUI manages (MEMORY-SPEC §4.3/§6). The
    # store is always wired: off means "stop learning", so saved facts stay usable.
    memory_settings = MemorySettingsStore(data_dir / "memory-settings.json")
    memory_store = SQLiteMemoryStore(data_dir / "coworker.db")
    session_store = ConversationStore(data_dir)
    session_store.touch_workspace(os.path.realpath(str(workspace)))

    resume_messages = None
    session_id = args.resume or uuid.uuid4().hex[:12]
    model, mode = args.model, args.mode
    if args.resume:
        record = session_store.load(args.resume)
        if record is not None:
            resume_messages = record.messages
            model, mode = record.model, record.mode

    from .tui.app import CoworkerApp

    app = CoworkerApp(
        workspace=workspace,
        model=model,
        mode=Mode(mode),
        memory_store=memory_store,
        memory_off=not memory_settings.enabled,
        user_rules=memory_settings.user_rules,
        session_store=session_store,
        session_id=session_id,
        resume_messages=resume_messages,
    )
    app.run()


def _state_main(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(
        prog="openworker state", description="Backup, inspect, and restore local state."
    )
    subparsers = parser.add_subparsers(dest="state_command", required=True)
    backup = subparsers.add_parser("backup", help="write a portable state backup archive")
    backup.add_argument("archive", help="destination .zip archive")
    backup.add_argument(
        "--state-dir",
        default=None,
        help="source state directory (default: platform state dir)",
    )
    inspect = subparsers.add_parser("inspect", help="inspect a backup archive manifest")
    inspect.add_argument("archive", help="backup .zip archive")
    restore = subparsers.add_parser("restore", help="restore a backup into a state directory")
    restore.add_argument("archive", help="backup .zip archive")
    restore.add_argument(
        "--state-dir",
        default=None,
        help="target state directory (default: platform state dir)",
    )
    restore.add_argument(
        "--force",
        action="store_true",
        help="replace a non-empty target using a staged restore",
    )

    args = parser.parse_args(argv)
    try:
        if args.state_command == "backup":
            source_or_target = Path(args.state_dir).expanduser() if args.state_dir else state_dir()
            result = create_backup(source_or_target, args.archive)
        elif args.state_command == "inspect":
            result = inspect_backup(args.archive)
        else:
            source_or_target = Path(args.state_dir).expanduser() if args.state_dir else state_dir()
            result = restore_backup(args.archive, source_or_target, force=args.force)
    except BackupError as exc:
        raise SystemExit(f"openworker state {args.state_command}: {exc}") from exc
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
