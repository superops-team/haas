"""Model Proxy loopback HTTP application (specs/model-proxy §5.1).

Exposes the harness-facing OpenAI-compatible endpoints on loopback
(127.0.0.1:18080). Real provider credentials never appear here; the proxy
resolves them internally.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.background import BackgroundTask

from haas.model_proxy.models import ModelRoute
from haas.model_proxy.proxy import ModelProxy, ModelProxyError
from haas.model_proxy.route import ModelRouteError, resolve_model_route
from haas.model_proxy.secret import SecretResolutionError
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
        frozen_route_resolver: Callable[[str, str], dict[str, Any] | None] | None = None,
        policy_resolver: Callable[[ModelRoute], EffectivePolicy] | None = None,
    ) -> None:
        self._proxy = proxy
        self._registry = registry
        self._policy = policy
        self._frozen_route_resolver = frozen_route_resolver
        self._policy_resolver = policy_resolver

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
            if not isinstance(body, dict):
                raise ModelProxyError("invalid_input")
            harness = self._lookup_harness(scope.harnessId)
            model = str(body.get("model", ""))
            frozen_route = (
                self._frozen_route_resolver(scope.sessionId, scope.invocationId)
                if self._frozen_route_resolver is not None
                else None
            )
            if self._frozen_route_resolver is not None and frozen_route is None:
                raise ModelProxyError("invocation_route_unavailable")
            route = resolve_model_route(harness, model or None, frozen_route=frozen_route)
            policy = self._policy_resolver(route) if self._policy_resolver else self._policy
            if route.apiType != "responses":
                raise ModelProxyError("provider_api_unsupported")
            if body.get("stream") is True:
                response = await self._proxy.stream_responses(
                    route, body, authorization=authorization, policy=policy
                )
                return StreamingResponse(
                    self._proxy.relay_stream(response),
                    media_type="text/event-stream",
                    background=BackgroundTask(response.aclose),
                )
            data, _usage = await self._proxy.proxy_responses(
                route, body, authorization=authorization, policy=policy
            )
            return data
        except ModelProxyError as exc:
            error = str(exc)
            code = {
                "invalid_token": "haas_model_proxy_token_invalid",
                "token_expired": "haas_model_proxy_token_expired",
            }.get(error, error)
            if error not in {"invalid_token", "token_expired"}:
                return JSONResponse(status_code=400, content={"error": error})
            return JSONResponse(
                status_code=401,
                content={
                    "error": {
                        "type": "authentication_error",
                        "code": code,
                        "safeReason": code.removeprefix("haas_"),
                        "retryable": error in {"invalid_token", "token_expired"},
                    }
                },
            )
        except ModelRouteError:
            # No usable provider route: fail closed with a safe reason rather
            # than leaking a traceback as a bare 500 (spec §10).
            return JSONResponse(status_code=502, content={"error": "haas_provider_error"})
        except SecretResolutionError:
            # Never echo the credential ref back to the harness.
            return JSONResponse(status_code=502, content={"error": "haas_provider_error"})

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
