"""OpenSandbox HTTP client (specs/sandbox-runtime/README.md §5).

Thin typed client over the OpenSandbox AIO sandbox API. Endpoint paths are
class constants so they can be corrected after a live OpenSandbox probe
(spec §12: exact endpoints are verified at implementation time against the
pinned commit).
"""
from __future__ import annotations

from typing import Any

import httpx

from haas.security.redact import safe_upstream_body
from haas.runtime.models import (
    ExecResult,
    SandboxHandle,
    SandboxInspection,
    SandboxSpec,
)


class OpenSandboxError(Exception):
    """Raised when the OpenSandbox API returns a non-2xx response."""


class OpenSandboxClient:
    # Endpoint paths (to be confirmed against the pinned OpenSandbox commit).
    SANDBOXES_PATH = "/sandboxes"
    EXEC_PATH = "/sandboxes/{sandbox_id}/exec"
    VAULT_PATH = "/vault/secrets"

    def __init__(
        self,
        base_url: str,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = client
        self._owns_client = client is None
        self._active_client: httpx.AsyncClient | None = None

    async def _client_ctx(self) -> httpx.AsyncClient:
        if self._client is not None:
            return self._client
        if self._active_client is None:
            self._active_client = httpx.AsyncClient(base_url=self._base_url)
        return self._active_client

    async def close(self) -> None:
        if self._active_client is not None:
            await self._active_client.aclose()
            self._active_client = None

    async def create_sandbox(self, spec: SandboxSpec) -> SandboxHandle:
        client = await self._client_ctx()
        payload: dict[str, Any] = {
            "sessionId": spec.sessionId,
            "workspaceRoot": spec.workspaceRoot,
            "writableRoots": spec.writableRoots,
            "readOnlyRoots": spec.readOnlyRoots,
            "network": {"defaultAction": spec.network.defaultAction, "allow": spec.network.allow},
        }
        resp = await client.post(self.SANDBOXES_PATH, json=payload)
        data = self._checked(resp)
        return SandboxHandle(
            sandboxId=str(data.get("sandboxId", data.get("id", ""))),
            sessionId=spec.sessionId,
            status=str(data.get("status", "running")),
        )

    async def inspect_sandbox(self, sandbox_id: str) -> SandboxInspection:
        client = await self._client_ctx()
        resp = await client.get(f"{self.SANDBOXES_PATH}/{sandbox_id}")
        data = self._checked(resp)
        return SandboxInspection(
            sandboxId=str(data.get("sandboxId", data.get("id", sandbox_id))),
            sessionId=str(data.get("sessionId", "")),
            status=str(data.get("status", "running")),
            generation=int(data.get("generation", 1)),
            createdAtMs=int(data.get("createdAtMs", 0)),
        )

    async def destroy_sandbox(self, sandbox_id: str) -> None:
        client = await self._client_ctx()
        resp = await client.delete(f"{self.SANDBOXES_PATH}/{sandbox_id}")
        self._checked(resp)

    async def run(
        self,
        sandbox_id: str,
        command: list[str],
        *,
        cwd: str,
        env: dict[str, str] | None = None,
    ) -> ExecResult:
        client = await self._client_ctx()
        resp = await client.post(
            self.EXEC_PATH.format(sandbox_id=sandbox_id),
            json={"command": command, "cwd": cwd, "env": env or {}},
        )
        data = self._checked(resp)
        return ExecResult(
            sandboxId=sandbox_id,
            exitCode=int(data.get("exitCode", 0)),
            stdout=str(data.get("stdout", "")),
            stderr=str(data.get("stderr", "")),
        )

    async def write_secret(
        self,
        session_id: str,
        audience: str,
        ref: str,
        ttl_seconds: int,
    ) -> str:
        client = await self._client_ctx()
        resp = await client.post(
            self.VAULT_PATH,
            json={
                "sessionId": session_id,
                "audience": audience,
                "ref": ref,
                "ttlSeconds": ttl_seconds,
            },
        )
        data = self._checked(resp)
        return str(data.get("vaultRef", data.get("ref", "")))

    def _checked(self, resp: httpx.Response) -> dict[str, Any]:
        if resp.status_code >= 400:
            # Upstream bodies are an untrusted secret surface: redact and
            # truncate before they reach errors or logs.
            raise OpenSandboxError(
                f"opensandbox HTTP {resp.status_code}: "
                f"{safe_upstream_body(resp.text)}"
            )
        if resp.status_code == 204 or not resp.content:
            return {}
        try:
            data = resp.json()
        except ValueError as exc:
            raise OpenSandboxError("opensandbox returned non-JSON response") from exc
        return data if isinstance(data, dict) else {}
