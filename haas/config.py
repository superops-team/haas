"""Config: HaaS configuration assembly contract (specs/config/README.md)."""
from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI

# --- Configuration models -------------------------------------------------


@dataclass
class ServerConfig:
    host: str = "0.0.0.0"
    port: int = 8092


@dataclass
class StoreConfig:
    backend: str = "sqlite"
    dsn: str | None = None
    event_retention_seconds: int = 2_592_000


@dataclass
class IdentityConfig:
    provider: str = "static"


@dataclass
class ModelProxyConfig:
    listen: str = "127.0.0.1:18080"


@dataclass
class McpProxyConfig:
    listen: str = "127.0.0.1:18081"


@dataclass
class CodexAdapterConfig:
    transport: str = "unix_websocket"
    socket_path: str = "/tmp/haas/codex.sock"


@dataclass
class AdaptersConfig:
    codex: CodexAdapterConfig = field(default_factory=CodexAdapterConfig)


@dataclass
class ObservabilityConfig:
    """Reserved for S2+ (logging/metrics/trace assembly)."""


@dataclass
class AppConfig:
    server: ServerConfig = field(default_factory=ServerConfig)
    store: StoreConfig = field(default_factory=StoreConfig)
    identity: IdentityConfig = field(default_factory=IdentityConfig)
    model_proxy: ModelProxyConfig = field(default_factory=ModelProxyConfig)
    mcp_proxy: McpProxyConfig = field(default_factory=McpProxyConfig)
    adapters: AdaptersConfig = field(default_factory=AdaptersConfig)
    observability: ObservabilityConfig = field(default_factory=ObservabilityConfig)


# --- Loading --------------------------------------------------------------


def load_config(path: str | None = None) -> AppConfig:
    """Load AppConfig with precedence: defaults < config file < HAAS_* env.

    S1 implements the documented top-level precedence and the env vars from
    specs/config/README.md §5; remaining knobs are additive in later stages.
    """
    cfg = AppConfig()

    file_path = path or os.environ.get("HAAS_CONFIG")
    if file_path:
        _overlay_file(cfg, file_path)

    if os.environ.get("HAAS_SIDECAR_PORT"):
        cfg.server.port = int(os.environ["HAAS_SIDECAR_PORT"])
    if os.environ.get("HAAS_STORE_BACKEND"):
        cfg.store.backend = os.environ["HAAS_STORE_BACKEND"]
    if os.environ.get("HAAS_IDENTITY_PROVIDER"):
        cfg.identity.provider = os.environ["HAAS_IDENTITY_PROVIDER"]

    return cfg


def _overlay_file(cfg: AppConfig, path: str) -> None:
    with open(Path(path), encoding="utf-8") as fh:
        data: dict[str, Any] = yaml.safe_load(fh) or {}

    server = data.get("server") or {}
    if isinstance(server, dict):
        if "host" in server:
            cfg.server.host = str(server["host"])
        if "port" in server:
            cfg.server.port = int(server["port"])

    store = data.get("store") or {}
    if isinstance(store, dict):
        if "backend" in store:
            cfg.store.backend = str(store["backend"])
        if "dsn" in store:
            cfg.store.dsn = store["dsn"]
        if "event_retention_seconds" in store:
            cfg.store.event_retention_seconds = int(store["event_retention_seconds"])

    identity = data.get("identity") or {}
    if isinstance(identity, dict) and "provider" in identity:
        cfg.identity.provider = str(identity["provider"])

    model_proxy = data.get("model_proxy") or {}
    if isinstance(model_proxy, dict) and "listen" in model_proxy:
        cfg.model_proxy.listen = str(model_proxy["listen"])

    mcp_proxy = data.get("mcp_proxy") or {}
    if isinstance(mcp_proxy, dict) and "listen" in mcp_proxy:
        cfg.mcp_proxy.listen = str(mcp_proxy["listen"])

    adapters = data.get("adapters") or {}
    codex = (adapters or {}).get("codex") or {}
    if isinstance(codex, dict):
        if "transport" in codex:
            cfg.adapters.codex.transport = str(codex["transport"])
        if "socket_path" in codex:
            cfg.adapters.codex.socket_path = str(codex["socket_path"])


# --- App factory ----------------------------------------------------------


def _envelope(data: Any) -> dict[str, Any]:
    return {"data": data, "traceId": f"tr_{uuid.uuid4().hex}"}


def create_app(config: AppConfig | None = None) -> FastAPI:
    """Build the FastAPI application (uvicorn --factory compatible).

    Called with no args by uvicorn, it loads config via ``load_config()``;
    tests pass an explicit ``AppConfig`` to skip file/env loading.
    """
    from haas.api import build_app

    config = config if config is not None else load_config()
    return build_app(config)
