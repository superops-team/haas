"""OpenSandbox HTTP client tests (httpx mock transport, offline)."""
from __future__ import annotations

import httpx
import pytest

from haas.runtime.models import SandboxSpec
from haas.runtime.opensandbox import OpenSandboxClient, OpenSandboxError


def _client(handler: object) -> OpenSandboxClient:
    transport = httpx.MockTransport(handler)
    return OpenSandboxClient(
        "http://127.0.0.1:8080",
        client=httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8080"),
    )


async def test_create_sandbox_serializes_spec() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["url"] = str(request.url)
        seen["json"] = request.read().decode()
        return httpx.Response(200, json={"sandboxId": "sbx_1", "status": "running"})

    client = _client(handler)
    spec = SandboxSpec(
        sessionId="hsess_1",
        workspaceRoot="/workspace",
        writableRoots=["/workspace"],
        readOnlyRoots=["/repo"],
    )
    handle = await client.create_sandbox(spec)
    assert seen["method"] == "POST"
    assert seen["url"] == "http://127.0.0.1:8080/sandboxes"
    assert '"sessionId":"hsess_1"' in seen["json"]
    assert handle.sandboxId == "sbx_1"


async def test_inspect_sandbox() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/sandboxes/sbx_1"
        return httpx.Response(
            200, json={"sandboxId": "sbx_1", "status": "running", "generation": 2}
        )

    client = _client(handler)
    inspection = await client.inspect_sandbox("sbx_1")
    assert inspection.sandboxId == "sbx_1"
    assert inspection.generation == 2


async def test_destroy_sandbox() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "DELETE"
        assert request.url.path == "/sandboxes/sbx_1"
        return httpx.Response(204)

    client = _client(handler)
    await client.destroy_sandbox("sbx_1")


async def test_run_serializes_command() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = request.read().decode()
        return httpx.Response(200, json={"exitCode": 0, "stdout": "ok"})

    client = _client(handler)
    result = await client.run("sbx_1", ["echo", "ok"], cwd="/workspace")
    assert '"command":["echo","ok"]' in seen["body"]
    assert result.exitCode == 0
    assert result.stdout == "ok"


async def test_write_secret_serializes_ref() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = request.read().decode()
        return httpx.Response(200, json={"vaultRef": "vault://sbx_1/provider"})

    client = _client(handler)
    ref = await client.write_secret("hsess_1", "provider", "secret://tenant/provider", 300)
    assert seen["url"] == "http://127.0.0.1:8080/vault/secrets"
    assert '"ref":"secret://tenant/provider"' in seen["body"]
    assert ref == "vault://sbx_1/provider"


async def test_non_2xx_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    client = _client(handler)
    with pytest.raises(OpenSandboxError):
        await client.inspect_sandbox("sbx_1")
