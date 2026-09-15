"""Delegated container runtime contract (specs/manager-delegation/).

The production Docker/OpenSandbox implementation is intentionally separate from
the API layer. This module provides the narrow interface HaaS needs today plus a
fake implementation for offline contract tests.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import replace
from typing import Protocol, cast

from haas.stores import DelegatedRuntimeRecord, DelegatedSessionRecord


class DelegatedContainerUnavailable(Exception):
    """Raised when no delegated container backend can restore a session."""


CommandRunner = Callable[[list[str]], Awaitable[str]]
WorkerStream = Callable[[list[str], bytes], AsyncIterator[bytes]]
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_SUPPORTED_PLATFORMS = {"lite": {"linux/amd64", "linux/arm64"}, "aio": {"linux/amd64"}}


class DelegatedContainerRuntime(Protocol):
    async def restore(self, session: DelegatedSessionRecord) -> DelegatedRuntimeRecord: ...

    async def destroy(
        self, session: DelegatedSessionRecord, *, reason: str
    ) -> DelegatedRuntimeRecord: ...

    async def cancel(self, session: DelegatedSessionRecord, execution_id: str) -> None: ...

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

    async def cancel(self, session: DelegatedSessionRecord, execution_id: str) -> None:
        del session, execution_id
        raise DelegatedContainerUnavailable("delegation_container_runtime_not_configured")

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
        self.cancelled: list[tuple[str, str]] = []
        self._cancel_events: dict[str, asyncio.Event] = {}

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

    async def cancel(self, session: DelegatedSessionRecord, execution_id: str) -> None:
        self.cancelled.append((session.id, execution_id))
        self._cancel_events.setdefault(execution_id, asyncio.Event()).set()

    async def run_stream(
        self, session: DelegatedSessionRecord, body: dict[str, object]
    ) -> AsyncIterator[dict[str, object]]:
        self.runs.append({"delegatedSessionId": session.id, "body": body})
        execution_id = str(body.get("executionId") or "")
        cancel = self._cancel_events.setdefault(execution_id, asyncio.Event())
        if cancel.is_set():
            yield {
                "content": {"role": "model", "parts": []},
                "actions": {"stateDelta": {"status": "cancelled"}},
            }
            return
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
        detail = stderr.decode(errors="replace").strip() or stdout.decode(errors="replace").strip()
        raise DelegatedContainerUnavailable(detail or "docker_command_failed")
    return stdout.decode(errors="replace").strip()


async def _subprocess_worker_stream(args: list[str], request: bytes) -> AsyncIterator[bytes]:
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    assert proc.stdin is not None
    assert proc.stdout is not None
    assert proc.stderr is not None
    proc.stdin.write(request)
    await proc.stdin.drain()
    proc.stdin.close()
    stderr_task = asyncio.create_task(proc.stderr.read())
    try:
        async for line in proc.stdout:
            yield line
        code = await proc.wait()
        stderr = await stderr_task
        if code != 0:
            detail = stderr.decode(errors="replace").strip()
            raise DelegatedContainerUnavailable(detail or "delegated_private_worker_failed")
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
        if not stderr_task.done():
            stderr_task.cancel()


class DockerDelegatedContainerRuntime:
    """Docker-backed delegated runtime.

    The API layer still owns auth, policy, and manifest validation. This class
    only converts the already-approved contract into deterministic Docker CLI
    calls. Image selection and isolation are validated before Docker creates a
    worker. The brokered ``isolated`` topology fails closed until its private
    worker transport is configured.
    """

    def __init__(
        self,
        *,
        docker_bin: str = "docker",
        network: str = "none",
        allow_unpinned_local_image: bool = False,
        runner: CommandRunner = _subprocess_runner,
        worker_stream: WorkerStream = _subprocess_worker_stream,
    ) -> None:
        self.docker_bin = docker_bin
        self.network = network
        self.allow_unpinned_local_image = allow_unpinned_local_image
        self._runner = runner
        self._worker_stream = worker_stream
        self._cancel_events: dict[str, asyncio.Event] = {}

    async def restore(self, session: DelegatedSessionRecord) -> DelegatedRuntimeRecord:
        network_policy = session.delegationPolicySnapshot.get("network") or {
            "defaultAction": "deny",
            "allow": [],
        }
        if (
            network_policy.get("defaultAction") == "allow"
            or network_policy.get("allow")
        ) and self.network != "isolated":
            raise DelegatedContainerUnavailable("haas_policy_unsupported")
        if self.network == "isolated":
            raise DelegatedContainerUnavailable("delegation_broker_unavailable")
        if self.network != "none":
            raise DelegatedContainerUnavailable("delegation_network_policy_invalid")
        if session.runtime.containerId:
            running = await self._is_running(session.runtime.containerId)
            if running:
                now = int(time.time() * 1000)
                return replace(session.runtime, status="running", lastActiveAtMs=now)

        generation = session.runtime.containerGeneration + 1
        name = f"haas-{session.id}-{generation}"
        platform = await self._resolve_platform(session)
        args = self._docker_run_args(session, name, platform=platform, generation=generation)
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
            await self._runner([self.docker_bin, "rm", "-f", session.runtime.containerId])
        return replace(session.runtime, status="destroyed", containerId=None)

    async def cancel(self, session: DelegatedSessionRecord, execution_id: str) -> None:
        del session
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", execution_id):
            raise DelegatedContainerUnavailable("delegated_execution_id_invalid")
        self._cancel_events.setdefault(execution_id, asyncio.Event()).set()

    async def run_stream(
        self, session: DelegatedSessionRecord, body: dict[str, object]
    ) -> AsyncIterator[dict[str, object]]:
        if not session.runtime.containerId:
            raise DelegatedContainerUnavailable("delegated_container_not_running")
        execution_id = str(body.get("executionId") or "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", execution_id):
            raise DelegatedContainerUnavailable("delegated_execution_id_invalid")
        envelope = {
            "version": 1,
            "executionId": execution_id,
            "generation": session.runtime.containerGeneration,
            "harnessBase": session.harnessBase,
            "request": body,
        }
        args = [
            self.docker_bin,
            "exec",
            "-i",
            session.runtime.containerId,
            "/opt/haas/bin/private-worker",
        ]
        request = json.dumps(envelope, separators=(",", ":")).encode() + b"\n"
        cancel = self._cancel_events.setdefault(execution_id, asyncio.Event())
        iterator = self._worker_stream(args, request).__aiter__()
        try:
            while True:
                next_event: asyncio.Future[bytes] = asyncio.ensure_future(anext(iterator))
                cancel_wait = asyncio.create_task(cancel.wait())
                wait_set = {
                    cast(asyncio.Future[object], next_event),
                    cast(asyncio.Future[object], cancel_wait),
                }
                done, _ = await asyncio.wait(
                    wait_set, return_when=asyncio.FIRST_COMPLETED
                )
                if cancel_wait in done and cancel.is_set():
                    next_event.cancel()
                    await asyncio.gather(next_event, return_exceptions=True)
                    yield {
                        "content": {"role": "model", "parts": []},
                        "actions": {"stateDelta": {"status": "cancelled"}},
                    }
                    return
                cancel_wait.cancel()
                await asyncio.gather(cancel_wait, return_exceptions=True)
                try:
                    raw = next_event.result()
                except StopAsyncIteration:
                    return
                try:
                    event = json.loads(raw)
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    raise DelegatedContainerUnavailable(
                        "delegated_private_worker_malformed_ndjson"
                    ) from exc
                if not isinstance(event, dict):
                    raise DelegatedContainerUnavailable(
                        "delegated_private_worker_malformed_ndjson"
                    )
                yield event
        finally:
            self._cancel_events.pop(execution_id, None)
            close = getattr(iterator, "aclose", None)
            if callable(close):
                await close()

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

    async def _resolve_platform(self, session: DelegatedSessionRecord) -> str:
        configured = str(session.image.get("platform") or "")
        if configured:
            return configured
        try:
            arch = await self._runner([self.docker_bin, "version", "--format", "{{.Server.Arch}}"])
        except DelegatedContainerUnavailable as exc:
            raise DelegatedContainerUnavailable("delegation_platform_resolution_failed") from exc
        return f"linux/{arch.strip()}"

    def _docker_run_args(
        self,
        session: DelegatedSessionRecord,
        container_name: str,
        *,
        platform: str,
        generation: int,
    ) -> list[str]:
        image_ref = str(session.image.get("reference") or "")
        digest = str(session.image.get("digest") or "")
        variant = str(session.image.get("variant") or "lite")
        if variant not in _SUPPORTED_PLATFORMS:
            raise DelegatedContainerUnavailable("delegation_image_variant_invalid")
        if platform not in _SUPPORTED_PLATFORMS[variant]:
            raise DelegatedContainerUnavailable("delegation_image_platform_unsupported")
        if not digest and "@sha256:" not in image_ref and not self.allow_unpinned_local_image:
            raise DelegatedContainerUnavailable("delegation_image_digest_required")
        embedded_digest = image_ref.rsplit("@", 1)[1] if "@" in image_ref else ""
        for candidate in (digest, embedded_digest):
            if candidate and not _DIGEST_RE.fullmatch(candidate):
                raise DelegatedContainerUnavailable("delegation_image_digest_invalid")
        if digest and embedded_digest and digest != embedded_digest:
            raise DelegatedContainerUnavailable("delegation_image_digest_mismatch")
        image = image_ref if "@sha256:" in image_ref or not digest else f"{image_ref}@{digest}"
        mounts = [session.mountManifest["primaryWorkspace"]]
        mounts.extend(session.mountManifest.get("extraMounts") or [])
        args = [
            self.docker_bin,
            "run",
            "-d",
            "--platform",
            platform,
            "--name",
            container_name,
            "--label",
            f"haas.delegated_session={session.id}",
            "--label",
            f"haas.session={session.haasSessionId}",
            "--env",
            f"HAAS_WORKER_GENERATION={generation}",
            "--network",
            self.network,
            "--user",
            "10001:10001",
            "--read-only",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=256m",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            "256",
            "--memory",
            "4g",
            "--cpus",
            "2",
            "--mount",
            f"type=volume,source=haas-session-{session.id},target=/data/haas",
        ]
        for mount in mounts:
            args.extend(
                [
                    "--mount",
                    ("type=bind,source={source},target={target},readonly={readonly}").format(
                        source=mount["hostPathCanonical"],
                        target=mount["containerPath"],
                        readonly=str(mount["access"] == "ro").lower(),
                    ),
                ]
            )
        args.append(image)
        return args
