from __future__ import annotations

import json

import pytest
from coworker.haas import LocalBootstrap, SupervisorLockError, SupervisorState


def test_supervisor_lock_is_exclusive_and_state_files_are_private(tmp_path) -> None:
    state = SupervisorState(tmp_path / "haas")
    other = SupervisorState(tmp_path / "haas")

    with state.acquire_lock(), pytest.raises(SupervisorLockError), other.acquire_lock():
        pass

    assert state.state_dir.stat().st_mode & 0o777 == 0o700


def test_supervisor_reuses_private_random_token_and_rejects_symlink(tmp_path) -> None:
    state = SupervisorState(tmp_path / "haas")
    first = state.ensure_token()
    second = state.ensure_token()

    assert first == second
    assert len(first) >= 43
    assert state.token_file.stat().st_mode & 0o777 == 0o600

    bad = SupervisorState(tmp_path / "bad")
    (tmp_path / "target").write_text("secret")
    bad.token_file.symlink_to(tmp_path / "target")
    with pytest.raises(SupervisorLockError):
        bad.ensure_token()


def test_ready_file_requires_expected_launch_and_live_child(tmp_path) -> None:
    state = SupervisorState(tmp_path / "haas")
    launch_id = state.new_launch_id()
    state.ready_file.write_text(json.dumps({"port": 58123, "pid": 4321, "launchId": launch_id}))

    ready = state.read_ready(launch_id=launch_id, child_pid=4321, is_pid_alive=lambda _: True)
    assert ready.port == 58123

    with pytest.raises(SupervisorLockError):
        state.read_ready(launch_id="other", child_pid=4321, is_pid_alive=lambda _: True)
    with pytest.raises(SupervisorLockError):
        state.read_ready(launch_id=launch_id, child_pid=4321, is_pid_alive=lambda _: False)


def test_bootstrap_builds_isolated_secretless_environment_and_profile_steps(tmp_path) -> None:
    state = SupervisorState(tmp_path / "haas")
    bootstrap = LocalBootstrap(
        state, harness_id="chrn_codex_default", harness_base="codex", harness_name="Codex"
    )
    env = bootstrap.process_env(
        base_env={"PATH": "/usr/bin", "OPENAI_API_KEY": "must-not-leak", "HOME": "/real"},
        config_path=tmp_path / "haas.yaml",
        host="127.0.0.1",
        port=0,
        launch_id="launch-1",
    )

    assert env["HOME"] == str(state.state_dir / "home")
    assert env["HAAS_STATIC_TOKEN_FILE"] == str(state.token_file)
    assert env["HAAS_PORT_READY_FILE"] == str(state.ready_file)
    assert env["HAAS_LAUNCH_ID"] == "launch-1"
    assert env["HAAS_BOOTSTRAP_HARNESS_ID"] == "chrn_codex_default"
    assert "OPENAI_API_KEY" not in env
    assert "must-not-leak" not in repr(env)

    assert bootstrap.initial_profile_steps(
        registry_empty=True, profile_ref={"profileId": "hprof_1"}
    ) == ("create", "validate", "activate")
    assert (
        bootstrap.initial_profile_steps(registry_empty=False, profile_ref={"profileId": "hprof_1"})
        == ()
    )
