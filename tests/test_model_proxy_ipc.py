import asyncio
import json
import socket

import pytest

from haas.model_proxy.models import ModelRoute, RuntimeTokenScope
from haas.model_proxy.secret import LocalCredentialResolver, SecretResolutionError


async def test_private_resolver_transmits_scope_and_sequence():
    parent, child = socket.socketpair()
    parent.setblocking(False)
    resolver = LocalCredentialResolver(child.detach())
    scope = RuntimeTokenScope(harnessId="chrn_x", sessionId="hsess_x", invocationId="inv_x")
    route = ModelRoute(
        provider="openai",
        baseUrl="https://provider.example/v1",
        model="test",
        credentialRef="secret://manager/test",
    )
    task = asyncio.create_task(resolver.resolve_for(route, scope))
    loop = asyncio.get_running_loop()
    request = json.loads(await loop.sock_recv(parent, 16384))
    assert request == {
        "sequence": 1,
        "credentialRef": route.credentialRef,
        "harnessId": "chrn_x",
        "sessionId": "hsess_x",
        "model": "test",
        "baseUrl": route.baseUrl,
    }
    await loop.sock_sendall(parent, b'{"sequence":1,"value":"fixture-value"}\n')
    assert await task == "fixture-value"
    with pytest.raises(SecretResolutionError):
        await resolver.resolve(route.credentialRef)
    parent.close()
    with pytest.raises(SecretResolutionError):
        await resolver.resolve_for(route, scope)
    resolver.close()


@pytest.mark.parametrize(
    "reply",
    [b"[]\n", b'{"sequence":2,"value":"no"}\n', b"x" * 16385, b'{"sequence":1,"error":"denied"}\n'],
    ids=["array", "sequence", "oversize", "denied"],
)
async def test_private_resolver_rejects_invalid_response(reply):
    parent, child = socket.socketpair()
    parent.setblocking(False)
    resolver = LocalCredentialResolver(child.detach())
    route = ModelRoute(
        provider="test",
        baseUrl="https://provider.example",
        model="test",
        credentialRef="secret://test",
    )
    scope = RuntimeTokenScope(harnessId="chrn_x", sessionId="hsess_x", invocationId="inv_x")
    task = asyncio.create_task(resolver.resolve_for(route, scope))
    loop = asyncio.get_running_loop()
    await loop.sock_recv(parent, 16384)
    await loop.sock_sendall(parent, reply)
    try:
        with pytest.raises(SecretResolutionError):
            await task
        assert resolver.available == (b"denied" in reply)
    finally:
        parent.close()
        resolver.close()


async def test_private_resolver_cancel_closes_descriptor():
    parent, child = socket.socketpair()
    parent.setblocking(False)
    resolver = LocalCredentialResolver(child.detach())
    route = ModelRoute(
        provider="test",
        baseUrl="https://provider.example",
        model="test",
        credentialRef="secret://test",
    )
    scope = RuntimeTokenScope(harnessId="chrn_x", sessionId="hsess_x", invocationId="inv_x")
    task = asyncio.create_task(resolver.resolve_for(route, scope))
    await asyncio.get_running_loop().sock_recv(parent, 16384)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not resolver.available
    parent.close()


@pytest.mark.parametrize("startup", ["normal", "failed", "occupied"])
async def test_runtime_proxy_lifecycle_and_scoped_tokens(monkeypatch, startup):
    from haas.api import build_app
    from haas.model_proxy.runtime import RuntimeModelProxy, _ProxyServer
    from haas.stores import InvocationRecord

    app = build_app()
    parent, child = socket.socketpair()
    resolver = LocalCredentialResolver(child.detach())
    proxy = RuntimeModelProxy(app.state.runtime.registry, resolver, "127.0.0.1:0")
    blocker = socket.socket()
    blocker.bind(("127.0.0.1", 0))
    blocker.listen()
    if startup == "occupied":
        proxy.listen = f"127.0.0.1:{blocker.getsockname()[1]}"

    async def serve(server, sockets):
        if startup == "failed":
            return
        server.started = True
        while not server.should_exit:
            await asyncio.sleep(0.001)

    monkeypatch.setattr(_ProxyServer, "serve", serve)
    grants = []

    async def resolve(route, scope):
        grants.append((route.model, scope.sessionId))
        return "fixture-value"

    monkeypatch.setattr(resolver, "resolve_for", resolve)
    closed = []

    async def close():
        closed.append(True)

    monkeypatch.setattr(app.state.runtime.adapter, "close", close, raising=False)
    invocation = InvocationRecord(
        id="inv_test", sessionId="hsess_test", appName="chrn_codex_default", turnId="turn_test"
    )
    harness = app.state.runtime.store.get_harness("chrn_codex_default")
    profile = {
        "provider": {
            "providerId": "test",
            "name": "test",
            "wireApi": "responses",
            "apiType": "responses",
            "model": "test",
            "baseUrl": "https://provider.example",
            "credentialRef": "secret://test",
        }
    }
    try:
        with pytest.raises(SecretResolutionError):
            await proxy.begin(harness, invocation, profile)
        async with proxy.lifespan(app):
            if startup == "normal":
                credentials = await proxy.begin(harness, invocation, profile)
                assert proxy.tokens.validate(credentials["token"]).allowedModels == ["test"]
                assert proxy.route("hsess_test", "inv_test")["model"] == "test"
                assert grants == [("test", "hsess_test")]
                proxy.end(invocation, credentials)
                assert not proxy.routes and not proxy.tokens._tokens
                profile["provider"]["apiType"] = "chat_completions"
                profile["provider"]["wireApi"] = "openai-compatible"
                with pytest.raises(SecretResolutionError):
                    await proxy.begin(harness, invocation, profile)
            else:
                assert not proxy.base_url
        assert not resolver.available
        assert not proxy.base_url
        assert bool(closed) == (startup != "occupied")
    finally:
        parent.close()
        resolver.close()
        blocker.close()
