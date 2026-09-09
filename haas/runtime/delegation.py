"""Delegated container runtime contract (specs/manager-delegation/).

The production Docker/OpenSandbox implementation is intentionally separate from
the API layer. This module provides the narrow interface HaaS needs today plus a
fake implementation for offline contract tests.
"""
from __future__ import annotations

import asyncio
import base64
import json
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import replace
from typing import Protocol

from haas.stores import DelegatedRuntimeRecord, DelegatedSessionRecord


class DelegatedContainerUnavailable(Exception):
    """Raised when no delegated container backend can restore a session."""


CommandRunner = Callable[[list[str]], Awaitable[str]]


class DelegatedContainerRuntime(Protocol):
    async def restore(
        self, session: DelegatedSessionRecord
    ) -> DelegatedRuntimeRecord: ...

    async def destroy(
        self, session: DelegatedSessionRecord, *, reason: str
    ) -> DelegatedRuntimeRecord: ...

    def run_stream(
        self, session: DelegatedSessionRecord, body: dict[str, object]
    ) -> AsyncIterator[dict[str, object]]: ...


class DisabledDelegatedContainerRuntime:
    """Default runtime: fail closed until a real container backend is configured."""

    async def restore(self, session: DelegatedSessionRecord) -> DelegatedRuntimeRecord:
        raise DelegatedContainerUnavailable("delegation_container_runtime_not_configured")

    async def destroy(
        self, session: DelegatedSessionRecord, *, reason: str
    ) -> DelegatedRuntimeRecord:
        return replace(
            session.runtime,
            status="destroyed",
            containerId=None,
        )

    def run_stream(
        self, session: DelegatedSessionRecord, body: dict[str, object]
    ) -> AsyncIterator[dict[str, object]]:
        async def fail() -> AsyncIterator[dict[str, object]]:
            raise DelegatedContainerUnavailable("delegation_container_runtime_not_configured")
            yield {}  # pragma: no cover

        return fail()


class FakeDelegatedContainerRuntime:
    """Offline delegated runtime used by tests.

    It does not start a process. It proves API/store orchestration by allocating
    deterministic fake container handles and advancing the runtime generation.
    """

    def __init__(self) -> None:
        self.restored: list[str] = []
        self.destroyed: list[str] = []
        self.runs: list[dict[str, object]] = []

    async def restore(self, session: DelegatedSessionRecord) -> DelegatedRuntimeRecord:
        generation = session.runtime.containerGeneration + 1
        now = int(time.time() * 1000)
        self.restored.append(session.id)
        return DelegatedRuntimeRecord(
            status="running",
            containerId=f"fake-{session.id}-{generation}",
            containerGeneration=generation,
            lastStartedAtMs=now,
            lastActiveAtMs=now,
        )

    async def destroy(
        self, session: DelegatedSessionRecord, *, reason: str
    ) -> DelegatedRuntimeRecord:
        self.destroyed.append(f"{session.id}:{reason}")
        return replace(
            session.runtime,
            status="destroyed",
            containerId=None,
        )

    async def run_stream(
        self, session: DelegatedSessionRecord, body: dict[str, object]
    ) -> AsyncIterator[dict[str, object]]:
        self.runs.append({"delegatedSessionId": session.id, "body": body})
        yield {
            "content": {"role": "model", "parts": [{"text": "delegated"}]},
            "actions": {"stateDelta": {"last_text": "delegated"}},
        }
        yield {
            "content": {"role": "model", "parts": []},
            "actions": {"stateDelta": {"status": "completed"}},
        }


async def _subprocess_runner(args: list[str]) -> str:
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        detail = stderr.decode(errors="replace").strip() or stdout.decode(
            errors="replace"
        ).strip()
        raise DelegatedContainerUnavailable(detail or "docker_command_failed")
    return stdout.decode(errors="replace").strip()


class DockerDelegatedContainerRuntime:
    """Docker-backed delegated runtime.

    The API layer still owns auth, policy, and manifest validation. This class
    only converts the already-approved contract into deterministic Docker CLI
    calls, always pinning the HaaS platform to linux/amd64.
    """

    def __init__(
        self,
        *,
        docker_bin: str = "docker",
        network: str = "none",
        allow_unpinned_local_image: bool = False,
        runner: CommandRunner = _subprocess_runner,
    ) -> None:
        self.docker_bin = docker_bin
        self.network = network
        self.allow_unpinned_local_image = allow_unpinned_local_image
        self._runner = runner

    async def restore(self, session: DelegatedSessionRecord) -> DelegatedRuntimeRecord:
        if session.runtime.containerId:
            running = await self._is_running(session.runtime.containerId)
            if running:
                now = int(time.time() * 1000)
                return replace(session.runtime, status="running", lastActiveAtMs=now)

        generation = session.runtime.containerGeneration + 1
        name = f"haas-{session.id}-{generation}"
        args = self._docker_run_args(session, name)
        container_id = await self._runner(args)
        now = int(time.time() * 1000)
        return DelegatedRuntimeRecord(
            status="running",
            containerId=container_id.strip() or name,
            containerGeneration=generation,
            lastStartedAtMs=now,
            lastActiveAtMs=now,
        )

    async def destroy(
        self, session: DelegatedSessionRecord, *, reason: str
    ) -> DelegatedRuntimeRecord:
        if session.runtime.containerId:
            await self._runner(
                [self.docker_bin, "rm", "-f", session.runtime.containerId]
            )
        return replace(session.runtime, status="destroyed", containerId=None)

    async def run_stream(
        self, session: DelegatedSessionRecord, body: dict[str, object]
    ) -> AsyncIterator[dict[str, object]]:
        if not session.runtime.containerId:
            raise DelegatedContainerUnavailable("delegated_container_not_running")
        payload = base64.b64encode(json.dumps(body).encode("utf-8")).decode("ascii")
        script = """
import base64
import json
import sys
import urllib.request

payload = json.loads(base64.b64decode(sys.argv[1]).decode("utf-8"))
data = json.dumps(payload).encode("utf-8")
req = urllib.request.Request(
    "http://127.0.0.1:8092/run_sse",
    data=data,
    headers={
        "Content-Type": "application/json",
        "Authorization": "Bearer dev-token",
    },
    method="POST",
)
with urllib.request.urlopen(req, timeout=30) as resp:
    for line in resp:
        sys.stdout.buffer.write(line)
        sys.stdout.buffer.flush()
""".strip()
        proc = await asyncio.create_subprocess_exec(
            self.docker_bin,
            "exec",
            session.runtime.containerId,
            "python3",
            "-c",
            script,
            payload,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        assert proc.stdout is not None
        assert proc.stderr is not None
        stderr_task = asyncio.create_task(proc.stderr.read())
        try:
            async for raw in proc.stdout:
                line = raw.decode(errors="replace").strip()
                if not line or line.startswith(":") or not line.startswith("data:"):
                    continue
                try:
                    event = json.loads(line.removeprefix("data:").strip())
                except json.JSONDecodeError as exc:
                    raise DelegatedContainerUnavailable("container_returned_malformed_sse") from exc
                if isinstance(event, dict):
                    yield event
            code = await proc.wait()
            stderr = await stderr_task
            if code != 0:
                detail = stderr.decode(errors="replace").strip()
                raise DelegatedContainerUnavailable(detail or "delegated_container_run_failed")
        finally:
            if proc.returncode is None:
                proc.kill()
                await proc.wait()

    async def _is_running(self, container_id: str) -> bool:
        try:
            out = await self._runner(
                [
                    self.docker_bin,
                    "inspect",
                    "-f",
                    "{{.State.Running}}",
                    container_id,
                ]
            )
        except DelegatedContainerUnavailable:
            return False
        return out.strip().lower() == "true"

    def _docker_run_args(
        self, session: DelegatedSessionRecord, container_name: str
    ) -> list[str]:
        image_ref = str(session.image.get("reference") or "")
        digest = str(session.image.get("digest") or "")
        if not digest and "@sha256:" not in image_ref and not self.allow_unpinned_local_image:
            raise DelegatedContainerUnavailable("delegation_image_digest_required")
        image = image_ref if "@sha256:" in image_ref or not digest else f"{image_ref}@{digest}"
        mounts = [session.mountManifest["primaryWorkspace"]]
        mounts.extend(session.mountManifest.get("extraMounts") or [])
        args = [
            self.docker_bin,
            "run",
            "-d",
            "--platform",
            "linux/amd64",
            "--name",
            container_name,
            "--label",
            f"haas.delegated_session={session.id}",
            "--label",
            f"haas.session={session.haasSessionId}",
            "--network",
            self.network,
        ]
        for mount in mounts:
            args.extend(
                [
                    "--mount",
                    (
                        "type=bind,source={source},target={target},readonly={readonly}"
                    ).format(
                        source=mount["hostPathCanonical"],
                        target=mount["containerPath"],
                        readonly=str(mount["access"] == "ro").lower(),
                    ),
                ]
            )
        args.append(image)
        return args
