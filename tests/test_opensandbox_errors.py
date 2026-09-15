"""OpenSandbox client failure and lifecycle coverage (offline, mock transport).

test_opensandbox.py covers the happy-path serialization; this file covers the
response-handling and client-lifecycle branches that only run when the sandbox
service misbehaves.
"""

from __future__ import annotations

import httpx
import pytest

from haas.runtime.models import SandboxSpec
from haas.runtime.opensandbox import OpenSandboxClient, OpenSandboxError

BASE = "http://127.0.0.1:8080"
KEY = "sk-proj-" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4"  # haas-secret-ignore - synthetic


def _client(handler) -> OpenSandboxClient:
    return OpenSandboxClient(
        BASE,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=BASE),
    )


# --- response handling ------------------------------------------------------


async def test_204_is_treated_as_empty_success() -> None:
    client = _client(lambda _r: httpx.Response(204))
    await client.destroy_sandbox("sbx_1")


async def test_empty_body_is_treated_as_empty_success() -> None:
    client = _client(lambda _r: httpx.Response(200, content=b""))
    await client.destroy_sandbox("sbx_1")


async def test_non_json_body_raises() -> None:
    client = _client(lambda _r: httpx.Response(200, text="<html>nope</html>"))
    with pytest.raises(OpenSandboxError, match="non-JSON"):
        await client.create_sandbox(SandboxSpec(sessionId="s_1"))


async def test_non_dict_json_is_coerced_to_empty_dict() -> None:
    """A JSON array is valid JSON but not a resource object."""
    client = _client(lambda _r: httpx.Response(200, json=[1, 2, 3]))
    handle = await client.create_sandbox(SandboxSpec(sessionId="s_1"))
    assert handle.sessionId == "s_1"


@pytest.mark.parametrize("status", [400, 404, 409, 500, 503])
async def test_error_status_raises(status: int) -> None:
    client = _client(lambda _r: httpx.Response(status, json={"error": "boom"}))
    with pytest.raises(OpenSandboxError, match=f"opensandbox HTTP {status}"):
        await client.create_sandbox(SandboxSpec(sessionId="s_1"))


async def test_error_body_is_redacted() -> None:
    """Sandbox bodies may echo credentials; they must not reach the message."""
    client = _client(lambda _r: httpx.Response(500, json={"seen": f"Bearer {KEY}"}))
    with pytest.raises(OpenSandboxError) as excinfo:
        await client.create_sandbox(SandboxSpec(sessionId="s_1"))
    message = str(excinfo.value)
    assert KEY not in message
    assert "[REDACTED]" in message
    assert "opensandbox HTTP 500" in message


async def test_error_body_is_truncated() -> None:
    client = _client(lambda _r: httpx.Response(500, text="x" * 10000))
    with pytest.raises(OpenSandboxError) as excinfo:
        await client.create_sandbox(SandboxSpec(sessionId="s_1"))
    assert "<truncated>" in str(excinfo.value)
    assert len(str(excinfo.value)) < 1000


async def test_empty_error_body_is_reported() -> None:
    client = _client(lambda _r: httpx.Response(502, text=""))
    with pytest.raises(OpenSandboxError, match="<empty>"):
        await client.create_sandbox(SandboxSpec(sessionId="s_1"))


# --- client lifecycle -------------------------------------------------------


async def test_lazily_creates_and_reuses_its_own_client() -> None:
    client = OpenSandboxClient(BASE)
    first = await client._client_ctx()
    second = await client._client_ctx()
    assert first is second
    assert str(first.base_url).rstrip("/") == BASE
    await client.close()


async def test_close_releases_the_owned_client() -> None:
    client = OpenSandboxClient(BASE)
    owned = await client._client_ctx()
    await client.close()
    assert owned.is_closed is True
    # close() must be idempotent - a second call cannot raise.
    await client.close()


async def test_close_does_not_touch_an_injected_client() -> None:
    injected = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _r: httpx.Response(204)), base_url=BASE
    )
    client = OpenSandboxClient(BASE, client=injected)
    await client.close()
    assert injected.is_closed is False
    await injected.aclose()


async def test_base_url_trailing_slash_is_normalized() -> None:
    client = OpenSandboxClient(BASE + "/")
    assert client._base_url == BASE
    await client.close()
