"""Delegated container runtime tests (specs/manager-delegation/README.md)."""
from __future__ import annotations

import asyncio
import os
import shutil

import pytest

from haas.runtime import (
    DelegatedContainerUnavailable,
    DockerDelegatedContainerRuntime,
    FakeDelegatedContainerRuntime,
)
from haas.stores import DelegatedRuntimeRecord, DelegatedSessionRecord


def _session(**overrides: object) -> DelegatedSessionRecord:
    record = DelegatedSessionRecord(
        id="dgsess_1",
        managerSessionId="mgr_1",
        haasSessionId="hsess_1",
        haasUserId="u_1",
        harnessId="chrn_codex_default",
        image={"reference": "haas:local", "digest": "sha256:test"},
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


async def test_docker_runtime_builds_platform_and_mounts() -> None:
    calls: list[list[str]] = []

    async def runner(args: list[str]) -> str:
        calls.append(args)
        return "container-123"

    runtime = DockerDelegatedContainerRuntime(
        runner=runner, allow_unpinned_local_image=True
    )
    restored = await runtime.restore(_session())

    assert restored.containerId == "container-123"
    assert restored.containerGeneration == 1
    run = calls[0]
    assert run[:5] == ["docker", "run", "-d", "--platform", "linux/amd64"]
    assert "--network" in run and "none" in run
    assert (
        "type=bind,source=/repo,target=/workspace,readonly=false"
        in run
    )
    assert (
        "type=bind,source=/shared,target=/mnt/extra/shared,readonly=true"
        in run
    )
    assert run[-1] == "haas:local@sha256:test"


async def test_docker_runtime_requires_digest_unless_local_override() -> None:
    async def runner(args: list[str]) -> str:
        return "container-123"

    session = _session(image={"reference": "haas:local", "digest": ""})
    with pytest.raises(DelegatedContainerUnavailable, match="digest_required"):
        await DockerDelegatedContainerRuntime(runner=runner).restore(session)

    restored = await DockerDelegatedContainerRuntime(
        runner=runner, allow_unpinned_local_image=True
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
    restored = await DockerDelegatedContainerRuntime(runner=runner).restore(session)
    assert restored.containerId == "existing"
    assert restored.containerGeneration == 4
    assert calls == [["docker", "inspect", "-f", "{{.State.Running}}", "existing"]]


async def test_docker_runtime_raises_when_docker_fails() -> None:
    async def runner(args: list[str]) -> str:
        raise DelegatedContainerUnavailable("docker unavailable")

    with pytest.raises(DelegatedContainerUnavailable):
        await DockerDelegatedContainerRuntime(runner=runner).restore(_session())


@pytest.mark.docker
async def test_docker_runtime_real_smoke(tmp_path) -> None:
    if os.environ.get("HAAS_E2E_DOCKER_DELEGATION") != "1":
        pytest.skip("HAAS_E2E_DOCKER_DELEGATION=1 required")
    if shutil.which("docker") is None:
        pytest.skip("docker CLI is unavailable")

    image = os.environ.get("HAAS_DELEGATION_TEST_IMAGE", "haas:local")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    session = _session()
    session.mountManifest["primaryWorkspace"]["hostPathCanonical"] = str(workspace)
    session.mountManifest["extraMounts"] = []
    if "@sha256:" in image:
        session.image = {"reference": image, "digest": image.rsplit("@", 1)[1]}
    else:
        session.image = {"reference": image, "digest": ""}

    runtime = DockerDelegatedContainerRuntime()
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
    finally:
        await runtime.destroy(session, reason="test_cleanup")
