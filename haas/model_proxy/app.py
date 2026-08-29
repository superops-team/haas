"""Model Proxy loopback HTTP application (specs/model-proxy §5.1).

Exposes the harness-facing OpenAI-compatible endpoints on loopback
(127.0.0.1:18080). Real provider credentials never appear here; the proxy
resolves them internally.
"""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from haas.model_proxy.proxy import ModelProxy, ModelProxyError
from haas.model_proxy.route import resolve_model_route
from haas.policy import EffectivePolicy
from haas.registry import HarnessRegistry
from haas.stores import HarnessRecord


class ProxyApp:
    """Wires the ModelProxy core into a FastAPI app for loopback serving."""

    def __init__(
        self,
        proxy: ModelProxy,
        registry: HarnessRegistry,
        policy: EffectivePolicy,
    ) -> None:
        self._proxy = proxy
        self._registry = registry
        self._policy = policy

    def build(self) -> FastAPI:
        app = FastAPI(title="HaaS Model Proxy", version="2026-08-26")
        app.state.proxy_app = self

        @app.get("/health")
        async def health() -> dict[str, Any]:
            return {"status": "ok"}

        @app.get("/ready")
        async def ready() -> dict[str, Any]:
            return {"status": "ready"}

        @app.post("/v1/responses")
        async def responses(request: Request) -> Any:
            body = await request.json()
            return await self._handle_responses(request, body)

        @app.post("/v1/chat/completions")
        async def chat_completions(request: Request) -> Any:
            body = await request.json()
            return await self._handle_chat(request, body)

        return app

    async def _handle_responses(self, request: Request, body: dict[str, Any]) -> Any:
        try:
            authorization = request.headers.get("authorization", "")
            scope = await self._proxy.authenticate(authorization)
            harness = self._lookup_harness(scope.harnessId)
            model = str(body.get("model", ""))
            route = resolve_model_route(harness, model or None)
            data, _usage = await self._proxy.proxy_responses(
                route, body, authorization=authorization, policy=self._policy
            )
            return data
        except ModelProxyError as exc:
            return JSONResponse(status_code=400, content={"error": str(exc)})

    async def _handle_chat(self, request: Request, body: dict[str, Any]) -> Any:
        # Chat Completions shape is normalized through the same relay in a
        # later stage; for now reject explicitly rather than silently corrupt.
        return JSONResponse(
            status_code=501,
            content={"error": "chat_completions_not_implemented"},
        )

    def _lookup_harness(self, harness_id: str) -> HarnessRecord:
        harness = self._registry.get(harness_id)
        if harness is None or harness.status != "active":
            raise ModelProxyError("harness_not_found")
        return harness
