"""Config: HaaS configuration assembly contract (specs/config/README.md)."""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from math import isfinite
from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI

from haas.identity import Principal

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
    static_token_file: str | None = None


@dataclass
class ModelProxyConfig:
    listen: str = "127.0.0.1:18080"


@dataclass
class McpProxyConfig:
    listen: str = "127.0.0.1:18081"


@dataclass
class DelegationConfig:
    container_backend: str = "disabled"
    docker_bin: str = "docker"
    docker_network: str = "isolated"
    default_image_variant: str = "lite"
    allow_unpinned_local_image: bool = False
    idle_ttl_seconds: int = 1_800
    max_container_lifetime_seconds: int = 28_800
    rw_workspace_concurrency: str = "single_writer"
    queue_policy: str = "fifo"
    restore_policy: str = "fail_closed"
    policy_change_mode: str = "snapshot_per_session"
    mount_policy: str = "project_rw_extra_ro"


@dataclass
class CodexAdapterConfig:
    transport: str = "unix_websocket"
    socket_path: str = "/tmp/haas/codex.sock"


@dataclass
class AdaptersConfig:
    # Which harness base the production entrypoint assembles. `fake` is for
    # local development and tests only (specs/config 5.1).
    default_base: str = "codex"
    codex: CodexAdapterConfig = field(default_factory=CodexAdapterConfig)


@dataclass
class ObservabilityConfig:
    """Reserved for S2+ (logging/metrics/trace assembly)."""


@dataclass
class SessionRuntimeConfig:
    lease_ttl_ms: int = 30_000
    lease_renew_interval_ms: int = 10_000
    turn_timeout_seconds: float = 86_400

    def __post_init__(self) -> None:
        self.turn_timeout_seconds = _normalize_turn_timeout_seconds(
            self.turn_timeout_seconds
        )


@dataclass
class AppConfig:
    server: ServerConfig = field(default_factory=ServerConfig)
    store: StoreConfig = field(default_factory=StoreConfig)
    identity: IdentityConfig = field(default_factory=IdentityConfig)
    model_proxy: ModelProxyConfig = field(default_factory=ModelProxyConfig)
    mcp_proxy: McpProxyConfig = field(default_factory=McpProxyConfig)
    adapters: AdaptersConfig = field(default_factory=AdaptersConfig)
    session_runtime: SessionRuntimeConfig = field(default_factory=SessionRuntimeConfig)
    delegation: DelegationConfig = field(default_factory=DelegationConfig)
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
    if os.environ.get("HAAS_STATIC_TOKEN_FILE"):
        cfg.identity.static_token_file = os.environ["HAAS_STATIC_TOKEN_FILE"]
    if os.environ.get("HAAS_ADAPTER_BASE"):
        cfg.adapters.default_base = os.environ["HAAS_ADAPTER_BASE"]
    if os.environ.get("HAAS_SESSION_LEASE_TTL_MS"):
        cfg.session_runtime.lease_ttl_ms = int(os.environ["HAAS_SESSION_LEASE_TTL_MS"])
    if os.environ.get("HAAS_SESSION_LEASE_RENEW_INTERVAL_MS"):
        cfg.session_runtime.lease_renew_interval_ms = int(
            os.environ["HAAS_SESSION_LEASE_RENEW_INTERVAL_MS"]
        )
    if os.environ.get("HAAS_SESSION_TURN_TIMEOUT_SECONDS"):
        cfg.session_runtime.turn_timeout_seconds = _normalize_turn_timeout_seconds(
            os.environ["HAAS_SESSION_TURN_TIMEOUT_SECONDS"]
        )
    if os.environ.get("HAAS_DELEGATION_IDLE_TTL_SECONDS"):
        cfg.delegation.idle_ttl_seconds = int(os.environ["HAAS_DELEGATION_IDLE_TTL_SECONDS"])
    if os.environ.get("HAAS_DELEGATION_MAX_CONTAINER_LIFETIME_SECONDS"):
        cfg.delegation.max_container_lifetime_seconds = int(
            os.environ["HAAS_DELEGATION_MAX_CONTAINER_LIFETIME_SECONDS"]
        )
    if os.environ.get("HAAS_DELEGATION_RW_WORKSPACE_CONCURRENCY"):
        cfg.delegation.rw_workspace_concurrency = os.environ[
            "HAAS_DELEGATION_RW_WORKSPACE_CONCURRENCY"
        ]
    if os.environ.get("HAAS_DELEGATION_CONTAINER_BACKEND"):
        cfg.delegation.container_backend = os.environ["HAAS_DELEGATION_CONTAINER_BACKEND"]
    if os.environ.get("HAAS_DELEGATION_DOCKER_BIN"):
        cfg.delegation.docker_bin = os.environ["HAAS_DELEGATION_DOCKER_BIN"]
    if os.environ.get("HAAS_DELEGATION_DOCKER_NETWORK"):
        cfg.delegation.docker_network = os.environ["HAAS_DELEGATION_DOCKER_NETWORK"]
    if os.environ.get("HAAS_DEFAULT_IMAGE_VARIANT"):
        cfg.delegation.default_image_variant = os.environ["HAAS_DEFAULT_IMAGE_VARIANT"]
    if os.environ.get("HAAS_DELEGATION_ALLOW_UNPINNED_LOCAL_IMAGE"):
        cfg.delegation.allow_unpinned_local_image = os.environ[
            "HAAS_DELEGATION_ALLOW_UNPINNED_LOCAL_IMAGE"
        ].strip().lower() in {"1", "true", "yes", "on"}

    _normalize_config(cfg)
    return cfg


def _normalize_config(cfg: AppConfig) -> None:
    cfg.session_runtime.turn_timeout_seconds = _normalize_turn_timeout_seconds(
        cfg.session_runtime.turn_timeout_seconds
    )


def _normalize_turn_timeout_seconds(value: float | int | str) -> float:
    seconds = float(value)
    if not isfinite(seconds):
        raise ValueError("turn_timeout_seconds must be finite")
    if seconds <= 0:
        raise ValueError("turn_timeout_seconds must be positive")
    return min(seconds, 86_400)


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
    if isinstance(identity, dict):
        if "provider" in identity:
            cfg.identity.provider = str(identity["provider"])
        if "static_token_file" in identity:
            cfg.identity.static_token_file = str(identity["static_token_file"])

    model_proxy = data.get("model_proxy") or {}
    if isinstance(model_proxy, dict) and "listen" in model_proxy:
        cfg.model_proxy.listen = str(model_proxy["listen"])

    mcp_proxy = data.get("mcp_proxy") or {}
    if isinstance(mcp_proxy, dict) and "listen" in mcp_proxy:
        cfg.mcp_proxy.listen = str(mcp_proxy["listen"])

    adapters = data.get("adapters") or {}
    if isinstance(adapters, dict):
        if "default_base" in adapters:
            cfg.adapters.default_base = str(adapters["default_base"])
        codex = adapters.get("codex") or {}
        if isinstance(codex, dict):
            if "transport" in codex:
                cfg.adapters.codex.transport = str(codex["transport"])
            if "socket_path" in codex:
                cfg.adapters.codex.socket_path = str(codex["socket_path"])
    session_runtime = data.get("session_runtime") or {}
    if isinstance(session_runtime, dict):
        if "lease_ttl_ms" in session_runtime:
            cfg.session_runtime.lease_ttl_ms = int(session_runtime["lease_ttl_ms"])
        if "lease_renew_interval_ms" in session_runtime:
            cfg.session_runtime.lease_renew_interval_ms = int(
                session_runtime["lease_renew_interval_ms"]
            )
        if "turn_timeout_seconds" in session_runtime:
            cfg.session_runtime.turn_timeout_seconds = _normalize_turn_timeout_seconds(
                session_runtime["turn_timeout_seconds"]
            )
    codex = (adapters or {}).get("codex") or {}
    if isinstance(codex, dict):
        if "transport" in codex:
            cfg.adapters.codex.transport = str(codex["transport"])
        if "socket_path" in codex:
            cfg.adapters.codex.socket_path = str(codex["socket_path"])

    delegation = data.get("delegation") or {}
    if isinstance(delegation, dict):
        if "container_backend" in delegation:
            cfg.delegation.container_backend = str(delegation["container_backend"])
        if "docker_bin" in delegation:
            cfg.delegation.docker_bin = str(delegation["docker_bin"])
        if "docker_network" in delegation:
            cfg.delegation.docker_network = str(delegation["docker_network"])
        if "default_image_variant" in delegation:
            cfg.delegation.default_image_variant = str(delegation["default_image_variant"])
        if "allow_unpinned_local_image" in delegation:
            cfg.delegation.allow_unpinned_local_image = bool(
                delegation["allow_unpinned_local_image"]
            )
        if "idle_ttl_seconds" in delegation:
            cfg.delegation.idle_ttl_seconds = int(delegation["idle_ttl_seconds"])
        if "max_container_lifetime_seconds" in delegation:
            cfg.delegation.max_container_lifetime_seconds = int(
                delegation["max_container_lifetime_seconds"]
            )
        if "rw_workspace_concurrency" in delegation:
            cfg.delegation.rw_workspace_concurrency = str(delegation["rw_workspace_concurrency"])
        if "queue_policy" in delegation:
            cfg.delegation.queue_policy = str(delegation["queue_policy"])
        if "restore_policy" in delegation:
            cfg.delegation.restore_policy = str(delegation["restore_policy"])
        if "policy_change_mode" in delegation:
            cfg.delegation.policy_change_mode = str(delegation["policy_change_mode"])
        if "mount_policy" in delegation:
            cfg.delegation.mount_policy = str(delegation["mount_policy"])


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
    app = build_app(
        config,
        store=build_store(config),
        adapter=build_adapter(config),
        identity_tokens=_identity_tokens(config),
        delegated_containers=build_delegated_container_runtime(config),
    )
    credential_fd = os.environ.pop("HAAS_CREDENTIAL_FD", None)
    if credential_fd is not None:
        from haas.model_proxy.runtime import RuntimeModelProxy
        from haas.model_proxy.secret import LocalCredentialResolver

        proxy = RuntimeModelProxy(
            app.state.runtime.registry,
            LocalCredentialResolver(int(credential_fd)),
            config.model_proxy.listen,
            store=app.state.runtime.store,
        )
        app.state.runtime.sessions.model_proxy = proxy
        app.router.lifespan_context = proxy.lifespan
    return app


def build_store(config: AppConfig) -> Any:
    """Build the configured durable store; memory requires explicit opt-in."""
    if config.store.backend == "memory":
        from haas.stores import MemoryStore

        return MemoryStore()
    if config.store.backend == "sqlite":
        from haas.stores import SQLiteStore

        return SQLiteStore(config.store.dsn or "haas.db")
    raise ValueError(f"unsupported store backend: {config.store.backend}")


def _identity_tokens(config: AppConfig) -> dict[str, Principal] | None:
    if config.identity.provider != "static" or not config.identity.static_token_file:
        return None
    try:
        token = Path(config.identity.static_token_file).read_text(encoding="utf-8").strip()
    except OSError:
        return {}
    if not token:
        return {}
    return {token: Principal(principalId="p_local_manager")}


def build_adapter(config: AppConfig) -> Any:
    """Assemble the harness adapter named by `adapters.default_base`.

    Assembly only builds connection configuration; it never dials the harness,
    so the process still starts when Codex is down. Execution readiness is
    reported honestly by /v1/haas/ready?scope=execution (specs/config 5.1).
    """
    base = config.adapters.default_base
    if base == "fake":
        from haas.harnesses import FakeAdapter

        return FakeAdapter()
    if base == "codex":
        from haas.harnesses.codex_app_server import CodexAdapter
        from haas.harnesses.codex_app_server.transport import CodexEndpoint

        codex = config.adapters.codex
        transport = codex.transport
        if transport in {"unix_websocket", "unix"}:
            listen_url = f"unix://{codex.socket_path}"
        elif transport == "stdio":
            listen_url = "stdio://"
        else:
            listen_url = codex.socket_path
        return CodexAdapter(CodexEndpoint(transport=transport, listen_url=listen_url))
    raise ValueError(f"unsupported adapters.default_base: {base}")


def build_delegated_container_runtime(config: AppConfig) -> Any:
    """Assemble the optional delegated container runtime.

    The default is disabled so a local development server never starts Docker by
    accident. Set `delegation.container_backend=docker` to enable the Docker CLI
    adapter.
    """
    backend = config.delegation.container_backend
    if backend == "disabled":
        from haas.runtime import DisabledDelegatedContainerRuntime

        return DisabledDelegatedContainerRuntime()
    if backend == "docker":
        from haas.runtime import DockerDelegatedContainerRuntime

        return DockerDelegatedContainerRuntime(
            docker_bin=config.delegation.docker_bin,
            network=config.delegation.docker_network,
            allow_unpinned_local_image=config.delegation.allow_unpinned_local_image,
        )
    raise ValueError(f"unsupported delegation.container_backend: {backend}")
