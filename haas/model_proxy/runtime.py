from __future__ import annotations

import asyncio
import contextlib
import socket
import time
from collections.abc import AsyncIterator, Iterator
from dataclasses import asdict
from typing import Any

import uvicorn
from fastapi import FastAPI

from haas.model_proxy.app import ProxyApp
from haas.model_proxy.models import RuntimeTokenScope
from haas.model_proxy.proxy import ModelProxy
from haas.model_proxy.route import resolve_model_route
from haas.model_proxy.secret import LocalCredentialResolver, SecretResolutionError
from haas.model_proxy.token import RuntimeTokenError, RuntimeTokenManager
from haas.policy import (
    NetworkPolicy,
    PolicyCompileInput,
    PolicyController,
    PolicyLayer,
    PolicyScope,
)
from haas.registry import HarnessRegistry
from haas.stores import HarnessRecord, InvocationRecord


class _ProxyServer(uvicorn.Server):
    @contextlib.contextmanager
    def capture_signals(self) -> Iterator[None]:
        yield


class RuntimeModelProxy:
    def __init__(
        self,
        registry: HarnessRegistry,
        resolver: LocalCredentialResolver,
        listen: str,
        *,
        cleanup_margin_seconds: int = 30,
    ) -> None:
        self.resolver = resolver
        self.listen = listen
        self.registry = registry
        self.tokens = RuntimeTokenManager(ttl_seconds=900)
        self.cleanup_margin_seconds = cleanup_margin_seconds
        self.proxy = ModelProxy(resolver, self.tokens, PolicyController())
        self.routes: dict[tuple[str, str], dict[str, Any]] = {}
        self._refresh_attempted: set[tuple[str, str]] = set()
        self.base_url = ""

    def route(self, session_id: str, invocation_id: str) -> dict[str, Any] | None:
        return self.routes.get((session_id, invocation_id))

    async def begin(
        self, harness: HarnessRecord, invocation: InvocationRecord, profile: dict[str, Any]
    ) -> dict[str, str]:
        if not self.base_url or not self.resolver.available:
            raise SecretResolutionError("model proxy unavailable")
        route = resolve_model_route(harness, frozen_route=profile["provider"])
        if route.apiType != "responses":
            raise SecretResolutionError("provider API unsupported")
        scope = RuntimeTokenScope(
            sessionId=invocation.sessionId,
            invocationId=invocation.id,
            harnessId=harness.id,
            allowedModels=[route.model],
        )
        await self.resolver.resolve_for(route, scope)
        timeout_seconds = harness.timeoutSeconds or 900
        token = self.tokens.issue(
            scope,
            expires_at_ms=(
                invocation.startedAtMs + (timeout_seconds + self.cleanup_margin_seconds) * 1000
            ),
        )
        self.routes[(invocation.sessionId, invocation.id)] = asdict(route)
        self._refresh_attempted.discard((invocation.sessionId, invocation.id))
        return {"baseUrl": self.base_url, "token": token}

    def end(self, invocation: InvocationRecord, credentials: dict[str, str]) -> None:
        self.tokens.revoke_invocation(invocation.sessionId, invocation.id)
        self.routes.pop((invocation.sessionId, invocation.id), None)
        self._refresh_attempted.discard((invocation.sessionId, invocation.id))

    def refresh(self, invocation: InvocationRecord, token: str) -> dict[str, str]:
        scope = self.tokens.scope(token, allow_expired=True)
        key = (invocation.sessionId, invocation.id)
        if (scope.sessionId, scope.invocationId) != key or key not in self.routes:
            raise RuntimeTokenError("invalid_token")
        if key in self._refresh_attempted:
            raise RuntimeTokenError("refresh_already_attempted")
        self._refresh_attempted.add(key)
        return {
            "baseUrl": self.base_url,
            "token": self.tokens.refresh(
                token,
                expires_at_ms=int(time.time() * 1000) + self.tokens.ttl_ms,
            ),
        }

    def apply_refresh(
        self, invocation: InvocationRecord, previous_token: str, replacement_token: str
    ) -> None:
        if previous_token == replacement_token:
            raise RuntimeTokenError("invalid_token")
        previous = self.tokens.validate(previous_token)
        replacement = self.tokens.validate(replacement_token)
        if (
            replacement.audience,
            replacement.sessionId,
            replacement.invocationId,
            replacement.harnessId,
            replacement.allowedModels,
        ) != (
            previous.audience,
            previous.sessionId,
            previous.invocationId,
            previous.harnessId,
            previous.allowedModels,
        ) or (replacement.sessionId, replacement.invocationId) != (
            invocation.sessionId,
            invocation.id,
        ):
            raise RuntimeTokenError("invalid_token")
        self.tokens.revoke(previous_token)

    @contextlib.asynccontextmanager
    async def lifespan(self, app: FastAPI) -> AsyncIterator[None]:
        host, port_text = self.listen.rsplit(":", 1)
        if host != "127.0.0.1":
            raise ValueError("model proxy must bind IPv4 loopback")
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.bind((host, int(port_text)))
            sock.listen(128)
        except OSError:
            sock.close()
            try:
                yield
            finally:
                self.resolver.close()
            return
        proxy_app = ProxyApp(
            self.proxy,
            self.registry,
            PolicyController().compile(PolicyCompileInput(scope=PolicyScope(), layers=[])),
            frozen_route_resolver=self.route,
            policy_resolver=lambda route: PolicyController().compile(
                PolicyCompileInput(
                    scope=PolicyScope(),
                    layers=[PolicyLayer("manager", network=NetworkPolicy(allow=[route.baseUrl]))],
                )
            ),
        ).build()
        server = _ProxyServer(
            uvicorn.Config(proxy_app, log_level="error", access_log=False, lifespan="off")
        )
        task = asyncio.create_task(server.serve(sockets=[sock]))
        try:
            try:
                async with asyncio.timeout(5):
                    while not server.started:
                        if task.done():
                            await task
                            raise RuntimeError("model proxy startup failed")
                        await asyncio.sleep(0.01)
                self.base_url = f"http://127.0.0.1:{sock.getsockname()[1]}/v1"
            except (OSError, RuntimeError, TimeoutError):
                self.resolver.close()
            yield
        finally:
            self.base_url = ""
            server.should_exit = True
            try:
                await asyncio.wait_for(task, timeout=5)
            except TimeoutError:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            finally:
                sock.close()
                close_adapter = getattr(app.state.runtime.adapter, "close", None)
                try:
                    if close_adapter is not None:
                        await close_adapter()
                finally:
                    await self.proxy.close()
                    self.resolver.close()
                    self.routes.clear()
                    self._refresh_attempted.clear()
                    self.tokens.revoke_all()
