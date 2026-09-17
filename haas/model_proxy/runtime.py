from __future__ import annotations

import asyncio
import contextlib
import socket
import time
from collections.abc import AsyncIterator, Iterator
from dataclasses import asdict, dataclass, field
from typing import Any

import uvicorn
from fastapi import FastAPI

from haas.model_proxy.app import ProxyApp
from haas.model_proxy.models import RuntimeTokenScope, provider_scope_key
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
from haas.stores import HarnessRecord, InvocationRecord, MemoryStore


class _ProxyServer(uvicorn.Server):
    @contextlib.contextmanager
    def capture_signals(self) -> Iterator[None]:
        yield


@dataclass
class _SessionCapability:
    session_id: str
    harness_id: str
    provider_scope_key: str
    active_token: str
    active_generation: int
    route: dict[str, Any]
    allowed_models: list[str] = field(default_factory=list)
    grace_token_fingerprints: set[str] = field(default_factory=set)


class RuntimeModelProxy:
    def __init__(
        self,
        registry: HarnessRegistry,
        resolver: LocalCredentialResolver,
        listen: str,
        *,
        cleanup_margin_seconds: int = 30,
        store: MemoryStore | None = None,
    ) -> None:
        self.resolver = resolver
        self.listen = listen
        self.registry = registry
        self.store = store
        self.tokens = RuntimeTokenManager(ttl_seconds=86_400)
        self.cleanup_margin_seconds = cleanup_margin_seconds
        self.proxy = ModelProxy(resolver, self.tokens, PolicyController())
        self.routes: dict[str, dict[str, Any]] = {}
        self._capabilities: dict[str, _SessionCapability] = {}
        self._refresh_attempted: set[tuple[str, int]] = set()
        self.base_url = ""

    @staticmethod
    def provider_scope_key(route: dict[str, Any]) -> str:
        return provider_scope_key(route)

    def route(self, session_id: str, invocation_id: str | None = None) -> dict[str, Any] | None:
        del invocation_id
        return self.routes.get(session_id)

    def scope(self, session_id: str, token: str) -> RuntimeTokenScope | None:
        capability = self._capabilities.get(session_id)
        if capability is None:
            return None
        expected_fingerprint = self.tokens.fingerprint(token)
        if capability.active_token != token:
            stored_fingerprints = (
                set(capability.grace_token_fingerprints)
                | self._stored_token_fingerprints(session_id)
            )
            if expected_fingerprint not in stored_fingerprints:
                return None
        return RuntimeTokenScope(
            sessionId=capability.session_id,
            harnessId=capability.harness_id,
            providerScopeKey=capability.provider_scope_key,
            generation=capability.active_generation,
            issuedAtMs=int(time.time() * 1000),
            allowedModels=list(capability.allowed_models),
        )

    async def begin(
        self, harness: HarnessRecord, invocation: InvocationRecord, profile: dict[str, Any]
    ) -> dict[str, str]:
        if not self.base_url or not self.resolver.available:
            raise SecretResolutionError("model proxy unavailable")
        route = resolve_model_route(harness, frozen_route=profile["provider"])
        if route.apiType != "responses":
            raise SecretResolutionError("provider API unsupported")
        route_dict = asdict(route)
        provider_scope_key = self.provider_scope_key(route_dict)
        timeout_seconds = min(float(invocation.timeoutSeconds or 86_400), 86_400)
        expires_at_ms = int(
            invocation.startedAtMs + (timeout_seconds + self.cleanup_margin_seconds) * 1000
        )
        capability = self._capabilities.get(invocation.sessionId)
        if capability is not None:
            same_scope = (
                capability.harness_id == harness.id
                and capability.provider_scope_key == provider_scope_key
            )
            if same_scope:
                self.routes[invocation.sessionId] = route_dict
                capability.route = route_dict
                self.tokens.renew(capability.active_token, expires_at_ms=expires_at_ms)
                return {"baseUrl": self.base_url, "token": capability.active_token}
            self.revoke_session(invocation.sessionId)

        now_ms = int(time.time() * 1000)
        scope = RuntimeTokenScope(
            sessionId=invocation.sessionId,
            harnessId=harness.id,
            providerScopeKey=provider_scope_key,
            generation=1,
            issuedAtMs=now_ms,
        )
        await self.resolver.resolve_for(route, scope)
        token = self.tokens.issue(
            scope,
            expires_at_ms=expires_at_ms,
        )
        self.routes[invocation.sessionId] = route_dict
        self._capabilities[invocation.sessionId] = _SessionCapability(
            session_id=invocation.sessionId,
            harness_id=harness.id,
            provider_scope_key=provider_scope_key,
            active_token=token,
            active_generation=scope.generation,
            route=route_dict,
            allowed_models=[],
            grace_token_fingerprints=self._stored_token_fingerprints(invocation.sessionId),
        )
        self._persist_capability_summary(invocation, provider_scope_key, token)
        self._refresh_attempted.discard((invocation.sessionId, scope.generation))
        return {"baseUrl": self.base_url, "token": token}

    def end(self, invocation: InvocationRecord, credentials: dict[str, str]) -> None:
        del invocation, credentials

    def refresh(self, invocation: InvocationRecord, token: str) -> dict[str, str]:
        scope = self.tokens.scope(token, allow_expired=True)
        capability = self._capabilities.get(invocation.sessionId)
        key = (invocation.sessionId, scope.generation)
        if key in self._refresh_attempted:
            raise RuntimeTokenError("refresh_already_attempted")
        if (
            scope.sessionId != invocation.sessionId
            or capability is None
            or capability.active_token != token
            or scope.harnessId != capability.harness_id
            or scope.providerScopeKey != capability.provider_scope_key
        ):
            raise RuntimeTokenError("invalid_token")
        self._refresh_attempted.add(key)
        route = self.routes.get(invocation.sessionId)
        if route is None:
            raise RuntimeTokenError("invalid_token")
        generation = capability.active_generation + 1
        replacement_scope = RuntimeTokenScope(
            sessionId=scope.sessionId,
            harnessId=scope.harnessId,
            providerScopeKey=scope.providerScopeKey,
            generation=generation,
            issuedAtMs=int(time.time() * 1000),
            allowedModels=list(scope.allowedModels),
        )
        expires_at_ms = max(
            scope.expiresAtMs,
            int(time.time() * 1000) + self.tokens.ttl_ms,
        )
        replacement = self.tokens.issue(replacement_scope, expires_at_ms=expires_at_ms)
        capability.active_token = replacement
        capability.active_generation = generation
        capability.allowed_models = list(replacement_scope.allowedModels)
        self._persist_capability_summary_for_session(
            invocation.sessionId,
            provider_scope_key=scope.providerScopeKey,
            token=replacement,
            generation=generation,
        )
        return {
            "baseUrl": self.base_url,
            "token": replacement,
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
            replacement.harnessId,
            replacement.providerScopeKey,
            replacement.allowedModels,
        ) != (
            previous.audience,
            previous.sessionId,
            previous.harnessId,
            previous.providerScopeKey,
            previous.allowedModels,
        ) or replacement.sessionId != invocation.sessionId:
            raise RuntimeTokenError("invalid_token")
        capability = self._capabilities.get(invocation.sessionId)
        if capability is None or capability.active_token != replacement_token:
            raise RuntimeTokenError("invalid_token")
        self.tokens.revoke(previous_token)

    def revoke_session(self, session_id: str) -> None:
        self.tokens.revoke_session(session_id)
        self.routes.pop(session_id, None)
        self._capabilities.pop(session_id, None)
        for key in list(self._refresh_attempted):
            if key[0] == session_id:
                self._refresh_attempted.discard(key)
        self._clear_capability_summary(session_id)

    def _persist_capability_summary(
        self, invocation: InvocationRecord, provider_scope_key: str, token: str
    ) -> None:
        self._persist_capability_summary_for_session(
            invocation.sessionId,
            provider_scope_key=provider_scope_key,
            token=token,
            generation=1,
        )

    def _persist_capability_summary_for_session(
        self,
        session_id: str,
        *,
        provider_scope_key: str,
        token: str,
        generation: int,
    ) -> None:
        if self.store is None:
            return
        session = next(
            (candidate for candidate in self.store.list_sessions() if candidate.id == session_id),
            None,
        )
        if session is None:
            return
        state = dict(session.state)
        state["modelProxy"] = {
            "providerScopeKey": provider_scope_key,
            "activeGeneration": generation,
            "activeTokenFingerprint": self.tokens.fingerprint(token),
        }
        session.state = state
        self.store.put_session(session)

    def _stored_token_fingerprints(self, session_id: str) -> set[str]:
        if self.store is None:
            return set()
        for session in self.store.list_sessions():
            if session.id != session_id:
                continue
            model_proxy = session.state.get("modelProxy")
            if isinstance(model_proxy, dict):
                fingerprint = model_proxy.get("activeTokenFingerprint")
                return {fingerprint} if isinstance(fingerprint, str) else set()
        return set()

    def _clear_capability_summary(self, session_id: str) -> None:
        if self.store is None:
            return
        for session in self.store.list_sessions():
            if session.id != session_id:
                continue
            state = dict(session.state)
            state.pop("modelProxy", None)
            session.state = state
            self.store.put_session(session)

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
            session_scope_resolver=self.scope,
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
