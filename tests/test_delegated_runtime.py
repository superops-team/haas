"""Delegated container runtime tests (specs/manager-delegation/README.md)."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
from collections.abc import AsyncIterator
from io import StringIO

import pytest

from haas.config import load_config
from haas.runtime import (
    DelegatedContainerUnavailable,
    DisabledDelegatedContainerRuntime,
    DockerDelegatedContainerRuntime,
    FakeDelegatedContainerRuntime,
)
from haas.runtime.broker import FakeWorkerBroker, UnavailableWorkerBroker
from haas.runtime.delegation import _subprocess_runner, _subprocess_worker_stream
from haas.runtime.worker import PrivateWorker, PrivateWorkerError
from haas.runtime.worker import main as worker_main
from haas.stores import DelegatedRuntimeRecord, DelegatedSessionRecord


def _session(**overrides: object) -> DelegatedSessionRecord:
    record = DelegatedSessionRecord(
        id="dgsess_1",
        managerSessionId="mgr_1",
        haasSessionId="hsess_1",
        haasUserId="u_1",
        harnessId="chrn_codex_default",
        image={
            "reference": "haas:local",
            "digest": "sha256:" + "a" * 64,
            "variant": "lite",
            "platform": "linux/arm64",
        },
        provider={
            "providerId": "volcengine-ark",
            "model": "doubao-seed-2.1-turbo",
            "credentialRef": "secret://provider/volcengine",
        },
        mountManifest={
            "version": 1,
            "primaryWorkspace": {
                "hostPathCanonical": "/repo",
                "containerPath": "/workspace",
                "access": "rw",
            },
            "extraMounts": [
                {
                    "hostPathCanonical": "/shared",
                    "containerPath": "/mnt/extra/shared",
                    "access": "ro",
                }
            ],
        },
        delegationPolicySnapshot={
            "version": 1,
            "idleTtlSeconds": 1800,
            "maxContainerLifetimeSeconds": 28800,
            "rwWorkspaceConcurrency": "single_writer",
            "queuePolicy": "fifo",
            "restorePolicy": "fail_closed",
            "mountPolicy": "project_rw_extra_ro",
        },
    )
    for key, value in overrides.items():
        setattr(record, key, value)
    return record


async def test_fake_delegated_runtime_restores_generation() -> None:
    runtime = FakeDelegatedContainerRuntime()
    restored = await runtime.restore(_session())
    assert restored.status == "running"
    assert restored.containerGeneration == 1
    assert restored.containerId == "fake-dgsess_1-1"
    session = _session(runtime=restored)
    events = [event async for event in runtime.run_stream(session, {"value": 1})]
    assert events[-1]["actions"]["stateDelta"]["status"] == "completed"
    destroyed = await runtime.destroy(session, reason="test")
    assert destroyed.status == "destroyed"
    assert runtime.destroyed == ["dgsess_1:test"]


async def test_disabled_runtime_fails_closed_and_can_mark_destroyed() -> None:
    runtime = DisabledDelegatedContainerRuntime()
    session = _session()
    with pytest.raises(DelegatedContainerUnavailable, match="not_configured"):
        await runtime.restore(session)
    with pytest.raises(DelegatedContainerUnavailable, match="not_configured"):
        await anext(runtime.run_stream(session, {}))
    assert (await runtime.destroy(session, reason="shutdown")).status == "destroyed"


async def test_docker_runtime_builds_platform_and_mounts() -> None:
    calls: list[list[str]] = []

    async def runner(args: list[str]) -> str:
        calls.append(args)
        return "container-123"

    runtime = DockerDelegatedContainerRuntime(runner=runner, network="none")
    restored = await runtime.restore(_session())

    assert restored.containerId == "container-123"
    assert restored.containerGeneration == 1
    run = calls[0]
    assert run[:5] == ["docker", "run", "-d", "--platform", "linux/arm64"]
    assert "--network" in run and "none" in run
    assert "type=bind,source=/repo,target=/workspace,readonly=false" in run
    assert "type=bind,source=/shared,target=/mnt/extra/shared,readonly=true" in run
    assert "type=volume,source=haas-session-dgsess_1,target=/data/haas" in run
    assert "HAAS_WORKER_GENERATION=1" in run
    assert all("token" not in arg.lower() for arg in run)
    for flag in (
        "--read-only",
        "--cap-drop",
        "--security-opt",
        "--pids-limit",
        "--memory",
        "--cpus",
    ):
        assert flag in run
    assert run[-1] == "haas:local@sha256:" + "a" * 64


async def test_docker_runtime_rejects_unenforced_network_allow() -> None:
    session = _session()
    session.delegationPolicySnapshot["network"] = {
        "defaultAction": "allow",
        "allow": [],
    }

    with pytest.raises(DelegatedContainerUnavailable, match="haas_policy_unsupported"):
        await DockerDelegatedContainerRuntime(network="none").restore(session)


async def test_docker_runtime_requires_digest_unless_local_override() -> None:
    async def runner(args: list[str]) -> str:
        return "container-123"

    session = _session(
        image={
            "reference": "haas:local",
            "digest": "",
            "variant": "lite",
            "platform": "linux/arm64",
        }
    )
    with pytest.raises(DelegatedContainerUnavailable, match="digest_required"):
        await DockerDelegatedContainerRuntime(runner=runner, network="none").restore(session)

    restored = await DockerDelegatedContainerRuntime(
        runner=runner, network="none", allow_unpinned_local_image=True
    ).restore(session)
    assert restored.containerId == "container-123"


async def test_docker_runtime_reuses_running_container() -> None:
    calls: list[list[str]] = []

    async def runner(args: list[str]) -> str:
        calls.append(args)
        return "true"

    session = _session()
    session.runtime = DelegatedRuntimeRecord(
        status="idle", containerId="existing", containerGeneration=4
    )
    restored = await DockerDelegatedContainerRuntime(runner=runner, network="none").restore(session)
    assert restored.containerId == "existing"
    assert restored.containerGeneration == 4
    assert calls == [["docker", "inspect", "-f", "{{.State.Running}}", "existing"]]


async def test_docker_runtime_recreates_stopped_or_missing_container() -> None:
    calls: list[list[str]] = []

    async def runner(args: list[str]) -> str:
        calls.append(args)
        if args[1] == "inspect":
            raise DelegatedContainerUnavailable("gone")
        return "replacement"

    session = _session()
    session.runtime = DelegatedRuntimeRecord(
        status="idle", containerId="old", containerGeneration=4
    )
    restored = await DockerDelegatedContainerRuntime(runner=runner, network="none").restore(session)
    assert restored.containerId == "replacement"
    assert restored.containerGeneration == 5
    assert calls[1][1] == "run"


async def test_docker_runtime_destroy_removes_existing_container() -> None:
    calls: list[list[str]] = []

    async def runner(args: list[str]) -> str:
        calls.append(args)
        return ""

    session = _session()
    session.runtime = DelegatedRuntimeRecord(status="running", containerId="active")
    runtime = DockerDelegatedContainerRuntime(runner=runner)
    destroyed = await runtime.destroy(session, reason="idle_ttl")
    assert calls == [["docker", "rm", "-f", "active"]]
    assert destroyed.containerId is None
    session.runtime = destroyed
    assert (await runtime.destroy(session, reason="repeat")).status == "destroyed"


async def test_subprocess_runner_propagates_safe_process_failure() -> None:
    assert await _subprocess_runner([sys.executable, "-c", "print('ok')"]) == "ok"
    with pytest.raises(DelegatedContainerUnavailable, match="safe failure"):
        await _subprocess_runner(
            [sys.executable, "-c", "import sys;sys.stderr.write('safe failure');sys.exit(3)"]
        )
    with pytest.raises(DelegatedContainerUnavailable, match="docker_command_failed"):
        await _subprocess_runner([sys.executable, "-c", "import sys;sys.exit(2)"])


async def test_subprocess_worker_stream_forwards_lines_and_failure() -> None:
    command = [
        sys.executable,
        "-c",
        "import sys; data=sys.stdin.buffer.readline(); sys.stdout.buffer.write(data)",
    ]
    assert [line async for line in _subprocess_worker_stream(command, b"event\n")] == [b"event\n"]
    failing = [
        sys.executable,
        "-c",
        "import sys;sys.stdin.buffer.read();sys.stderr.write('worker failed');sys.exit(4)",
    ]
    with pytest.raises(DelegatedContainerUnavailable, match="worker failed"):
        _ = [line async for line in _subprocess_worker_stream(failing, b"request\n")]


async def test_docker_runtime_raises_when_docker_fails() -> None:
    async def runner(args: list[str]) -> str:
        raise DelegatedContainerUnavailable("docker unavailable")

    with pytest.raises(DelegatedContainerUnavailable):
        await DockerDelegatedContainerRuntime(runner=runner, network="none").restore(_session())


@pytest.mark.parametrize("digest", ["sha256:test", "sha256:" + "g" * 64, "md5:" + "a" * 64])
async def test_docker_runtime_rejects_malformed_digest(digest: str) -> None:
    session = _session()
    session.image["digest"] = digest
    with pytest.raises(DelegatedContainerUnavailable, match="digest_invalid"):
        await DockerDelegatedContainerRuntime(network="none").restore(session)


async def test_aio_rejects_arm64_platform() -> None:
    session = _session()
    session.image.update({"variant": "aio", "platform": "linux/arm64"})
    with pytest.raises(DelegatedContainerUnavailable, match="platform_unsupported"):
        await DockerDelegatedContainerRuntime(network="none").restore(session)


async def test_lite_resolves_platform_from_docker_execution_node() -> None:
    calls: list[list[str]] = []

    async def runner(args: list[str]) -> str:
        calls.append(args)
        if args[1] == "version":
            return "arm64"
        return "container-123"

    session = _session()
    session.image.pop("platform")
    restored = await DockerDelegatedContainerRuntime(runner=runner, network="none").restore(session)
    assert restored.containerId == "container-123"
    assert calls[0] == ["docker", "version", "--format", "{{.Server.Arch}}"]
    assert calls[1][4] == "linux/arm64"


async def test_isolated_network_fails_closed_without_broker() -> None:
    with pytest.raises(DelegatedContainerUnavailable, match="broker_unavailable"):
        await DockerDelegatedContainerRuntime(network="isolated").restore(_session())


async def test_unknown_network_and_platform_resolution_fail_closed() -> None:
    with pytest.raises(DelegatedContainerUnavailable, match="network_policy_invalid"):
        await DockerDelegatedContainerRuntime(network="host").restore(_session())

    async def runner(args: list[str]) -> str:
        raise DelegatedContainerUnavailable("docker unavailable")

    session = _session()
    session.image.pop("platform")
    with pytest.raises(DelegatedContainerUnavailable, match="platform_resolution_failed"):
        await DockerDelegatedContainerRuntime(runner=runner, network="none").restore(session)


@pytest.mark.parametrize(
    ("image", "error"),
    [
        (
            {
                "reference": "haas:local",
                "digest": "sha256:" + "a" * 64,
                "variant": "unknown",
                "platform": "linux/arm64",
            },
            "variant_invalid",
        ),
        (
            {
                "reference": "haas:local@sha256:" + "b" * 64,
                "digest": "sha256:" + "a" * 64,
                "variant": "lite",
                "platform": "linux/arm64",
            },
            "digest_mismatch",
        ),
    ],
)
async def test_docker_runtime_rejects_invalid_image_contract(
    image: dict[str, object], error: str
) -> None:
    with pytest.raises(DelegatedContainerUnavailable, match=error):
        await DockerDelegatedContainerRuntime(network="none").restore(_session(image=image))


async def test_delegated_run_requires_controller_execution_id() -> None:
    session = _session()
    session.runtime = DelegatedRuntimeRecord(status="running", containerId="container-1")
    runtime = DockerDelegatedContainerRuntime(network="none")
    with pytest.raises(DelegatedContainerUnavailable, match="execution_id_invalid"):
        await anext(runtime.run_stream(session, {}))

    stopped = _session()
    with pytest.raises(DelegatedContainerUnavailable, match="container_not_running"):
        await anext(runtime.run_stream(stopped, {"executionId": "inv_1"}))


async def test_docker_runtime_uses_private_worker_stdin_without_secret_argv_or_env() -> None:
    calls: list[tuple[list[str], bytes]] = []

    async def worker_stream(args: list[str], request: bytes) -> AsyncIterator[bytes]:
        calls.append((args, request))
        yield (
            json.dumps(
                {
                    "content": {"role": "model", "parts": [{"text": "offline"}]},
                    "actions": {"stateDelta": {"status": "completed"}},
                }
            ).encode()
            + b"\n"
        )

    session = _session(harnessBase="fake")
    session.runtime = DelegatedRuntimeRecord(
        status="running", containerId="container-1", containerGeneration=7
    )
    runtime = DockerDelegatedContainerRuntime(network="none", worker_stream=worker_stream)

    events = [
        event
        async for event in runtime.run_stream(
            session, {"executionId": "inv_abc123", "newMessage": {"parts": []}}
        )
    ]

    assert events[0]["content"]["parts"][0]["text"] == "offline"
    args, stdin = calls[0]
    assert args == [
        "docker",
        "exec",
        "-i",
        "container-1",
        "/opt/haas/bin/private-worker",
    ]
    assert all("token" not in arg.lower() for arg in args)
    envelope = json.loads(stdin)
    assert envelope["executionId"] == "inv_abc123"
    assert envelope["generation"] == 7
    assert envelope["harnessBase"] == "fake"
    assert "token" not in stdin.decode().lower()


async def test_docker_runtime_cancel_stops_worker_stream_with_cancelled_terminal() -> None:
    worker_started = asyncio.Event()

    async def worker_stream(args: list[str], request: bytes) -> AsyncIterator[bytes]:
        del args, request
        worker_started.set()
        await asyncio.Event().wait()
        yield b""  # pragma: no cover

    session = _session(harnessBase="fake")
    session.runtime = DelegatedRuntimeRecord(
        status="running", containerId="container-1", containerGeneration=7
    )
    runtime = DockerDelegatedContainerRuntime(network="none", worker_stream=worker_stream)
    collect = asyncio.create_task(
        anext(runtime.run_stream(session, {"executionId": "inv_cancel"}))
    )
    await worker_started.wait()

    await runtime.cancel(session, "inv_cancel")

    terminal = await asyncio.wait_for(collect, timeout=1)
    assert terminal["actions"]["stateDelta"]["status"] == "cancelled"


@pytest.mark.parametrize("raw", [b"not-json\n", b"[]\n", b"\xff\n"])
async def test_docker_runtime_rejects_malformed_private_worker_output(raw: bytes) -> None:
    async def worker_stream(args: list[str], request: bytes) -> AsyncIterator[bytes]:
        yield raw

    session = _session()
    session.runtime = DelegatedRuntimeRecord(status="running", containerId="worker")
    runtime = DockerDelegatedContainerRuntime(network="none", worker_stream=worker_stream)
    with pytest.raises(DelegatedContainerUnavailable, match="malformed_ndjson"):
        await anext(runtime.run_stream(session, {"executionId": "inv_1"}))


def test_private_worker_replays_signed_receipt_without_duplicate_execution(tmp_path) -> None:
    private = tmp_path / "private"
    private.mkdir()
    token = private / "generation-7.token"
    token.write_bytes(b"a" * 32)
    token.chmod(0o600)
    calls: list[str] = []

    def execute(request: dict[str, object]) -> list[dict[str, object]]:
        calls.append(str(request["newMessage"]))
        return [
            {
                "content": {"role": "model", "parts": [{"text": "offline"}]},
                "actions": {"stateDelta": {"status": "completed"}},
            }
        ]

    worker = PrivateWorker(data_root=tmp_path, generation=7, execute=execute)
    envelope = {
        "version": 1,
        "generation": 7,
        "executionId": "inv_abc123",
        "harnessBase": "fake",
        "request": {"newMessage": {"parts": [{"text": "hello"}]}},
    }

    first = list(worker.run(envelope))
    second = list(worker.run(envelope))

    assert first == second
    assert calls == ["{'parts': [{'text': 'hello'}]}"]
    receipt = tmp_path / "receipts" / "7" / "inv_abc123.ndjson"
    assert receipt.is_file()
    assert receipt.stat().st_mode & 0o777 == 0o600
    assert (receipt.with_suffix(".hmac")).is_file()


def test_private_worker_fails_closed_on_wrong_generation_or_ambiguous_start(tmp_path) -> None:
    private = tmp_path / "private"
    private.mkdir()
    token = private / "generation-7.token"
    token.write_bytes(b"a" * 32)
    token.chmod(0o600)
    worker = PrivateWorker(data_root=tmp_path, generation=7, execute=lambda request: [])
    envelope = {
        "version": 1,
        "generation": 6,
        "executionId": "inv_abc123",
        "harnessBase": "fake",
        "request": {},
    }
    with pytest.raises(PrivateWorkerError, match="generation_mismatch"):
        list(worker.run(envelope))

    envelope["generation"] = 7
    running = tmp_path / "receipts" / "7" / "inv_abc123.running"
    running.parent.mkdir(parents=True)
    running.write_text("started")
    with pytest.raises(PrivateWorkerError, match="execution_ambiguous"):
        list(worker.run(envelope))


def test_private_worker_rejects_receipt_conflict_and_tampering(tmp_path) -> None:
    private = tmp_path / "private"
    private.mkdir()
    token = private / "generation-7.token"
    token.write_bytes(b"a" * 32)
    token.chmod(0o600)
    worker = PrivateWorker(data_root=tmp_path, generation=7, execute=lambda request: [])
    envelope = {
        "version": 1,
        "generation": 7,
        "executionId": "inv_abc123",
        "harnessBase": "fake",
        "request": {"value": 1},
    }
    assert list(worker.run(envelope)) == []

    envelope["request"] = {"value": 2}
    with pytest.raises(PrivateWorkerError, match="execution_conflict"):
        list(worker.run(envelope))

    envelope["request"] = {"value": 1}
    receipt = tmp_path / "receipts" / "7" / "inv_abc123.ndjson"
    receipt.write_text('{"forged":true}\n')
    with pytest.raises(PrivateWorkerError, match="receipt_invalid"):
        list(worker.run(envelope))


def test_private_worker_token_must_be_owner_only(tmp_path) -> None:
    private = tmp_path / "private"
    private.mkdir()
    token = private / "generation-7.token"
    token.write_bytes(b"a" * 32)
    token.chmod(0o644)
    worker = PrivateWorker(data_root=tmp_path, generation=7, execute=lambda request: [])
    with pytest.raises(PrivateWorkerError, match="token_permissions_invalid"):
        list(
            worker.run(
                {
                    "version": 1,
                    "generation": 7,
                    "executionId": "inv_abc123",
                    "harnessBase": "fake",
                    "request": {},
                }
            )
        )


def test_private_worker_validates_envelope_token_and_receipt_shape(tmp_path) -> None:
    worker = PrivateWorker(data_root=tmp_path, generation=7, execute=lambda request: [])
    base = {"version": 1, "generation": 7, "executionId": "inv_1", "request": {}}
    for update, error in [
        ({"version": 2}, "version_unsupported"),
        ({"executionId": "bad/id"}, "execution_id_invalid"),
        ({"request": []}, "request_invalid"),
    ]:
        with pytest.raises(PrivateWorkerError, match=error):
            list(worker.run(base | update))
    with pytest.raises(PrivateWorkerError, match="token_unavailable"):
        list(worker.run(base))

    private = tmp_path / "private"
    private.mkdir()
    token = private / "generation-7.token"
    token.write_bytes(b"short")
    token.chmod(0o600)
    with pytest.raises(PrivateWorkerError, match="token_invalid"):
        list(worker.run(base))


def test_private_worker_rejects_incomplete_and_non_object_receipts(tmp_path) -> None:
    private = tmp_path / "private"
    private.mkdir()
    token = private / "generation-7.token"
    token.write_bytes(b"a" * 32)
    token.chmod(0o600)
    envelope = {"version": 1, "generation": 7, "executionId": "inv_1", "request": {}}
    worker = PrivateWorker(data_root=tmp_path, generation=7, execute=lambda request: [])
    receipt_dir = tmp_path / "receipts" / "7"
    receipt_dir.mkdir(parents=True)
    receipt = receipt_dir / "inv_1.ndjson"
    receipt.write_text("[]\n")
    with pytest.raises(PrivateWorkerError, match="receipt_incomplete"):
        list(worker.run(envelope))


def test_worker_broker_seams_are_explicit() -> None:
    assert FakeWorkerBroker().mcp({}) == {"tools": []}
    with pytest.raises(RuntimeError, match="mcp_broker_unavailable"):
        UnavailableWorkerBroker().mcp({})


def test_private_worker_command_rejects_non_object_input() -> None:
    stderr = StringIO()
    assert worker_main(stdin=StringIO("[]"), stderr=stderr) == 1
    assert stderr.getvalue() == "private_worker_request_invalid\n"


def test_private_worker_command_runs_fake_broker_and_replays(tmp_path, monkeypatch) -> None:
    private = tmp_path / "private"
    private.mkdir()
    token = private / "generation-7.token"
    token.write_bytes(b"a" * 32)
    token.chmod(0o600)
    envelope = {
        "version": 1,
        "generation": 7,
        "executionId": "inv_command",
        "harnessBase": "fake",
        "request": {"newMessage": {"parts": [{"text": "hello"}]}},
    }
    monkeypatch.setenv("HAAS_DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("HAAS_WORKER_GENERATION", "7")

    first = StringIO()
    assert worker_main(stdin=StringIO(json.dumps(envelope)), stdout=first) == 0
    second = StringIO()
    assert worker_main(stdin=StringIO(json.dumps(envelope)), stdout=second) == 0

    assert first.getvalue() == second.getvalue()
    events = [json.loads(line) for line in first.getvalue().splitlines()]
    assert events[-1]["actions"]["stateDelta"]["status"] == "completed"


def test_private_worker_command_fails_closed_without_production_broker(
    tmp_path, monkeypatch
) -> None:
    private = tmp_path / "private"
    private.mkdir()
    token = private / "generation-7.token"
    token.write_bytes(b"a" * 32)
    token.chmod(0o600)
    envelope = {
        "version": 1,
        "generation": 7,
        "executionId": "inv_codex",
        "harnessBase": "codex",
        "request": {"newMessage": {"parts": [{"text": "hello"}]}},
    }
    monkeypatch.setenv("HAAS_DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("HAAS_WORKER_GENERATION", "7")
    stderr = StringIO()

    assert worker_main(stdin=StringIO(json.dumps(envelope)), stderr=stderr) == 1
    assert stderr.getvalue() == "delegation_model_broker_unavailable\n"
    assert not (tmp_path / "receipts" / "7" / "inv_codex.ndjson").exists()


def test_delegation_config_defaults_and_env_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_config()
    assert config.delegation.docker_network == "isolated"
    assert config.delegation.default_image_variant == "lite"

    monkeypatch.setenv("HAAS_DELEGATION_DOCKER_NETWORK", "none")
    monkeypatch.setenv("HAAS_DEFAULT_IMAGE_VARIANT", "aio")
    config = load_config()
    assert config.delegation.docker_network == "none"
    assert config.delegation.default_image_variant == "aio"


@pytest.mark.docker
async def test_docker_runtime_real_smoke(tmp_path) -> None:
    if os.environ.get("HAAS_E2E_DOCKER_DELEGATION") != "1":
        pytest.skip("HAAS_E2E_DOCKER_DELEGATION=1 required")
    if shutil.which("docker") is None:
        pytest.skip("docker CLI is unavailable")

    image = os.environ.get("HAAS_DELEGATION_TEST_IMAGE", "haas:local")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    session = _session(harnessBase="fake")
    session.mountManifest["primaryWorkspace"]["hostPathCanonical"] = str(workspace)
    session.mountManifest["extraMounts"] = []
    if "@sha256:" in image:
        session.image = {
            "reference": image,
            "digest": image.rsplit("@", 1)[1],
            "variant": os.environ.get("HAAS_DELEGATION_TEST_VARIANT", "lite"),
            "platform": os.environ.get("HAAS_DELEGATION_TEST_PLATFORM", "linux/amd64"),
        }
    else:
        session.image = {
            "reference": image,
            "digest": "",
            "variant": os.environ.get("HAAS_DELEGATION_TEST_VARIANT", "lite"),
            "platform": os.environ.get("HAAS_DELEGATION_TEST_PLATFORM", "linux/amd64"),
        }

    runtime = DockerDelegatedContainerRuntime(
        network="none", allow_unpinned_local_image="@sha256:" not in image
    )
    restored = await runtime.restore(session)
    session.runtime = restored
    try:
        assert restored.status == "running"
        assert restored.containerId
        assert restored.containerGeneration == 1
        # Confirm /workspace is the authorized bind mount and is writable.
        proc = await asyncio.create_subprocess_exec(
            "docker",
            "exec",
            restored.containerId,
            "sh",
            "-lc",
            "printf ok >/workspace/marker.txt",
        )
        assert await proc.wait() == 0
        assert (workspace / "marker.txt").read_text() == "ok"
        inspected = await runtime._runner(
            [
                "docker",
                "inspect",
                "--format",
                "{{json .Config.Env}} {{.HostConfig.NetworkMode}}",
                restored.containerId,
            ]
        )
        assert "TOKEN" not in inspected.upper()
        assert inspected.endswith(" none")
        token_mode = await runtime._runner(
            [
                "docker",
                "exec",
                restored.containerId,
                "stat",
                "-c",
                "%a",
                "/data/haas/private/generation-1.token",
            ]
        )
        assert token_mode == "600"
        if session.harnessBase == "fake":
            body = {
                "executionId": "inv_real_smoke",
                "newMessage": {"parts": [{"text": "hello"}]},
            }
            events = [event async for event in runtime.run_stream(session, body)]
            assert events[-1]["actions"]["stateDelta"]["status"] == "completed"
            replay = [event async for event in runtime.run_stream(session, body)]
            assert replay == events
    finally:
        await runtime.destroy(session, reason="test_cleanup")
