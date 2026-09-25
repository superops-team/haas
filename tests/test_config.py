from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest

from haas.config import AppConfig, SessionRuntimeConfig, build_store, load_config
from haas.events import EventLog
from haas.stores import MemoryStore, SQLiteStore
from haas.stores.memory import CanonicalEventRecord
from haas.stores.memory import MemoryStore as _MemoryStore


def test_build_store_selects_explicit_memory_backend() -> None:
    config = AppConfig()
    config.store.backend = "memory"
    assert isinstance(build_store(config), MemoryStore)


def test_build_store_uses_sqlite_dsn(tmp_path) -> None:
    config = AppConfig()
    config.store.backend = "sqlite"
    config.store.dsn = str(tmp_path / "haas.db")
    store = build_store(config)
    assert isinstance(store, SQLiteStore)
    store.close()


def test_build_store_rejects_unknown_backend() -> None:
    config = AppConfig()
    config.store.backend = "unknown"
    with pytest.raises(ValueError, match="unsupported store backend"):
        build_store(config)


def test_config_file_loads_codex_transport_and_socket(tmp_path) -> None:
    path = tmp_path / "haas.yaml"
    path.write_text(
        "adapters:\n  default_base: codex\n  codex:\n"
        "    transport: unix_websocket\n    socket_path: /tmp/custom-codex.sock\n"
    )
    config = load_config(str(path))
    assert config.adapters.codex.transport == "unix_websocket"
    assert config.adapters.codex.socket_path == "/tmp/custom-codex.sock"


def test_session_runtime_defaults_to_24h_turn_deadline() -> None:
    assert SessionRuntimeConfig().turn_timeout_seconds == 86_400


def test_session_turn_timeout_file_override_is_clamped_to_24h(tmp_path) -> None:
    path = tmp_path / "haas.yaml"
    path.write_text("session_runtime:\n  turn_timeout_seconds: 172800\n", encoding="utf-8")

    config = load_config(str(path))

    assert config.session_runtime.turn_timeout_seconds == 86_400


def test_session_turn_timeout_env_override_is_clamped_to_24h(monkeypatch) -> None:
    monkeypatch.setenv("HAAS_SESSION_TURN_TIMEOUT_SECONDS", "172800")

    config = load_config()

    assert config.session_runtime.turn_timeout_seconds == 86_400


def test_session_turn_timeout_rejects_non_positive_values(monkeypatch) -> None:
    monkeypatch.setenv("HAAS_SESSION_TURN_TIMEOUT_SECONDS", "0")

    with pytest.raises(ValueError, match="turn_timeout_seconds must be positive"):
        load_config()


def test_session_turn_timeout_rejects_non_finite_values(monkeypatch) -> None:
    monkeypatch.setenv("HAAS_SESSION_TURN_TIMEOUT_SECONDS", "nan")

    with pytest.raises(ValueError, match="turn_timeout_seconds must be finite"):
        load_config()


def test_session_runtime_config_rejects_non_positive_direct_value() -> None:
    with pytest.raises(ValueError, match="turn_timeout_seconds must be positive"):
        SessionRuntimeConfig(turn_timeout_seconds=0)


def test_session_runtime_config_clamps_direct_value_to_24h() -> None:
    assert SessionRuntimeConfig(turn_timeout_seconds=172_800).turn_timeout_seconds == 86_400


# --- P2-02: lifespan composition via AsyncExitStack ----------------------


async def test_compose_lifespans_starts_and_stops_in_order() -> None:
    from haas.config import _compose_lifespans

    events: list[str] = []

    @asynccontextmanager
    async def lifespan_a(app: object) -> AsyncIterator[None]:
        events.append("a_start")
        yield
        events.append("a_stop")

    @asynccontextmanager
    async def lifespan_b(app: object) -> AsyncIterator[None]:
        events.append("b_start")
        yield
        events.append("b_stop")

    composed = _compose_lifespans(lifespan_a, lifespan_b)
    async with composed(None):
        events.append("body")

    # Startup order follows argument order; shutdown is reverse.
    assert events == ["a_start", "b_start", "body", "b_stop", "a_stop"]


async def test_compose_lifespans_tears_down_when_body_raises() -> None:
    from haas.config import _compose_lifespans

    events: list[str] = []

    @asynccontextmanager
    async def lifespan_a(app: object) -> AsyncIterator[None]:
        events.append("a_start")
        try:
            yield
        finally:
            events.append("a_stop")

    composed = _compose_lifespans(lifespan_a)
    with pytest.raises(RuntimeError):
        async with composed(None):
            raise RuntimeError("boom")
    # Even on body failure, the stacked lifespan still shuts down.
    assert events == ["a_start", "a_stop"]


# --- P2-03: ADK Event.timestamp is a float epoch seconds -----------------


def test_adk_project_event_timestamp_is_float_epoch_seconds() -> None:
    # ADK 2.0 contract (specs/README.md) defines Event.timestamp as float epoch
    # seconds. Lock that contract so an accidental int-ms / ISO-8601 change is
    # caught here rather than at the wire.
    event = CanonicalEventRecord(
        eventId="evt_1",
        invocationId="inv_1",
        sessionId="s_1",
        turnId="t_1",
        author="user",
        sequenceNumber=0,
        content={"role": "user", "parts": [{"text": "hi"}]},
        actions={},
        observedAtMs=1_700_000_000_000,
    )
    projected = EventLog(_MemoryStore()).project_adk(event)
    ts = projected["timestamp"]
    assert isinstance(ts, float)
    assert ts == pytest.approx(1_700_000_000.0)


# --- Env var overrides (HAAS_*) ------------------------------------------


def test_env_vars_override_all_fields(monkeypatch) -> None:
    monkeypatch.setenv("HAAS_SIDECAR_PORT", "9999")
    monkeypatch.setenv("HAAS_STORE_BACKEND", "memory")
    monkeypatch.setenv("HAAS_IDENTITY_PROVIDER", "static")
    monkeypatch.setenv("HAAS_STATIC_TOKEN_FILE", "/tmp/tok")
    monkeypatch.setenv("HAAS_ADAPTER_BASE", "fake")
    monkeypatch.setenv("HAAS_SESSION_LEASE_TTL_MS", "1234")
    monkeypatch.setenv("HAAS_SESSION_LEASE_RENEW_INTERVAL_MS", "100")
    monkeypatch.setenv("HAAS_DELEGATION_IDLE_TTL_SECONDS", "42")
    monkeypatch.setenv("HAAS_DELEGATION_MAX_CONTAINER_LIFETIME_SECONDS", "99")
    monkeypatch.setenv("HAAS_DELEGATION_RW_WORKSPACE_CONCURRENCY", "multi_writer")
    monkeypatch.setenv("HAAS_DELEGATION_CONTAINER_BACKEND", "docker")
    monkeypatch.setenv("HAAS_DELEGATION_DOCKER_BIN", "podman")
    monkeypatch.setenv("HAAS_DELEGATION_DOCKER_NETWORK", "bridge")
    monkeypatch.setenv("HAAS_DEFAULT_IMAGE_VARIANT", "aio")
    monkeypatch.setenv("HAAS_DELEGATION_ALLOW_UNPINNED_LOCAL_IMAGE", "yes")

    cfg = load_config()

    assert cfg.server.port == 9999
    assert cfg.store.backend == "memory"
    assert cfg.identity.provider == "static"
    assert cfg.identity.static_token_file == "/tmp/tok"
    assert cfg.adapters.default_base == "fake"
    assert cfg.session_runtime.lease_ttl_ms == 1234
    assert cfg.session_runtime.lease_renew_interval_ms == 100
    assert cfg.delegation.idle_ttl_seconds == 42
    assert cfg.delegation.max_container_lifetime_seconds == 99
    assert cfg.delegation.rw_workspace_concurrency == "multi_writer"
    assert cfg.delegation.container_backend == "docker"
    assert cfg.delegation.docker_bin == "podman"
    assert cfg.delegation.docker_network == "bridge"
    assert cfg.delegation.default_image_variant == "aio"
    assert cfg.delegation.allow_unpinned_local_image is True


def test_env_allow_unpinned_local_image_falsy(monkeypatch) -> None:
    monkeypatch.setenv("HAAS_DELEGATION_ALLOW_UNPINNED_LOCAL_IMAGE", "no")
    cfg = load_config()
    assert cfg.delegation.allow_unpinned_local_image is False


# --- File overlay: all sections ------------------------------------------


def test_file_overlay_covers_every_section(tmp_path) -> None:
    path = tmp_path / "haas.yaml"
    path.write_text(
        "server:\n"
        "  host: 127.0.0.1\n"
        "  port: 9000\n"
        "store:\n"
        "  backend: memory\n"
        "  dsn: ':memory:'\n"
        "  event_retention_seconds: 100\n"
        "identity:\n"
        "  provider: static\n"
        "  static_token_file: /tmp/tok\n"
        "model_proxy:\n"
        "  listen: 127.0.0.1:19000\n"
        "mcp_proxy:\n"
        "  listen: 127.0.0.1:19001\n"
        "adapters:\n"
        "  default_base: fake\n"
        "  codex:\n"
        "    transport: stdio\n"
        "    socket_path: /tmp/x.sock\n"
        "session_runtime:\n"
        "  lease_ttl_ms: 5000\n"
        "  lease_renew_interval_ms: 500\n"
        "  turn_timeout_seconds: 100\n"
        "delegation:\n"
        "  container_backend: docker\n"
        "  docker_bin: podman\n"
        "  docker_network: bridge\n"
        "  default_image_variant: aio\n"
        "  allow_unpinned_local_image: true\n"
        "  idle_ttl_seconds: 10\n"
        "  max_container_lifetime_seconds: 20\n"
        "  rw_workspace_concurrency: multi_writer\n"
        "  queue_policy: lifo\n"
        "  restore_policy: fail_open\n"
        "  policy_change_mode: per_turn\n"
        "  mount_policy: none\n",
        encoding="utf-8",
    )
    cfg = load_config(str(path))
    assert cfg.server.host == "127.0.0.1"
    assert cfg.server.port == 9000
    assert cfg.store.backend == "memory"
    assert cfg.store.dsn == ":memory:"
    assert cfg.store.event_retention_seconds == 100
    assert cfg.identity.provider == "static"
    assert cfg.identity.static_token_file == "/tmp/tok"
    assert cfg.model_proxy.listen == "127.0.0.1:19000"
    assert cfg.mcp_proxy.listen == "127.0.0.1:19001"
    assert cfg.adapters.default_base == "fake"
    assert cfg.adapters.codex.transport == "stdio"
    assert cfg.adapters.codex.socket_path == "/tmp/x.sock"
    assert cfg.session_runtime.lease_ttl_ms == 5000
    assert cfg.session_runtime.lease_renew_interval_ms == 500
    assert cfg.session_runtime.turn_timeout_seconds == 100
    assert cfg.delegation.container_backend == "docker"
    assert cfg.delegation.docker_bin == "podman"
    assert cfg.delegation.docker_network == "bridge"
    assert cfg.delegation.default_image_variant == "aio"
    assert cfg.delegation.allow_unpinned_local_image is True
    assert cfg.delegation.idle_ttl_seconds == 10
    assert cfg.delegation.max_container_lifetime_seconds == 20
    assert cfg.delegation.rw_workspace_concurrency == "multi_writer"
    assert cfg.delegation.queue_policy == "lifo"
    assert cfg.delegation.restore_policy == "fail_open"
    assert cfg.delegation.policy_change_mode == "per_turn"
    assert cfg.delegation.mount_policy == "none"


def test_file_overlay_skips_non_dict_sections(tmp_path) -> None:
    path = tmp_path / "haas.yaml"
    path.write_text(
        "server: notadict\n"
        "store: [1, 2]\n"
        "identity: 5\n"
        "model_proxy: null\n"
        "mcp_proxy: ''\n"
        "adapters: null\n"
        "session_runtime: []\n"
        "delegation: ~\n",
        encoding="utf-8",
    )
    cfg = load_config(str(path))
    # Defaults retained when a section is present but not a mapping.
    assert cfg.server.host == "0.0.0.0"
    assert cfg.store.backend == "sqlite"
    assert cfg.identity.provider == "static"


# --- Identity tokens ------------------------------------------------------


def test_identity_tokens_reads_token_file(tmp_path) -> None:
    from haas.config import _identity_tokens
    from haas.identity import Principal

    cfg = AppConfig()
    cfg.identity.provider = "static"
    tok = tmp_path / "token"
    tok.write_text("secret-token\n", encoding="utf-8")
    cfg.identity.static_token_file = str(tok)
    tokens = _identity_tokens(cfg)
    assert tokens == {"secret-token": Principal(principalId="p_local_manager")}


def test_identity_tokens_missing_file_returns_empty(tmp_path) -> None:
    from haas.config import _identity_tokens

    cfg = AppConfig()
    cfg.identity.provider = "static"
    cfg.identity.static_token_file = str(tmp_path / "does-not-exist")
    assert _identity_tokens(cfg) == {}


def test_identity_tokens_empty_file_returns_empty(tmp_path) -> None:
    from haas.config import _identity_tokens

    cfg = AppConfig()
    cfg.identity.provider = "static"
    tok = tmp_path / "empty"
    tok.write_text("   ", encoding="utf-8")
    cfg.identity.static_token_file = str(tok)
    assert _identity_tokens(cfg) == {}


def test_identity_tokens_non_static_provider_returns_none() -> None:
    from haas.config import _identity_tokens

    cfg = AppConfig()
    cfg.identity.provider = "oauth"
    assert _identity_tokens(cfg) is None


# --- build_adapter --------------------------------------------------------


def test_build_adapter_fake() -> None:
    from haas.config import build_adapter
    from haas.harnesses import FakeAdapter

    cfg = AppConfig()
    cfg.adapters.default_base = "fake"
    assert isinstance(build_adapter(cfg), FakeAdapter)


def test_build_adapter_codex_unix_websocket() -> None:
    from haas.config import build_adapter

    cfg = AppConfig()
    cfg.adapters.default_base = "codex"
    cfg.adapters.codex.transport = "unix_websocket"
    cfg.adapters.codex.socket_path = "/tmp/c.sock"
    adapter = build_adapter(cfg)
    assert adapter._endpoint.listen_url == "unix:///tmp/c.sock"


def test_build_adapter_codex_stdio() -> None:
    from haas.config import build_adapter

    cfg = AppConfig()
    cfg.adapters.default_base = "codex"
    cfg.adapters.codex.transport = "stdio"
    adapter = build_adapter(cfg)
    assert adapter._endpoint.listen_url == "stdio://"


def test_build_adapter_codex_unknown_transport_uses_raw_socket_path() -> None:
    from haas.config import build_adapter

    cfg = AppConfig()
    cfg.adapters.default_base = "codex"
    cfg.adapters.codex.transport = "websocket"
    cfg.adapters.codex.socket_path = "ws://127.0.0.1:9999"
    adapter = build_adapter(cfg)
    assert adapter._endpoint.listen_url == "ws://127.0.0.1:9999"


def test_build_adapter_rejects_unknown_base() -> None:
    from haas.config import build_adapter

    cfg = AppConfig()
    cfg.adapters.default_base = "nope"
    with pytest.raises(ValueError, match="unsupported adapters.default_base"):
        build_adapter(cfg)


# --- build_delegated_container_runtime ------------------------------------


def test_build_delegated_disabled() -> None:
    from haas.config import build_delegated_container_runtime
    from haas.runtime import DisabledDelegatedContainerRuntime

    cfg = AppConfig()
    assert isinstance(build_delegated_container_runtime(cfg), DisabledDelegatedContainerRuntime)


def test_build_delegated_docker() -> None:
    from haas.config import build_delegated_container_runtime
    from haas.runtime import DockerDelegatedContainerRuntime

    cfg = AppConfig()
    cfg.delegation.container_backend = "docker"
    cfg.delegation.docker_bin = "podman"
    rt = build_delegated_container_runtime(cfg)
    assert isinstance(rt, DockerDelegatedContainerRuntime)


def test_build_delegated_rejects_unknown() -> None:
    from haas.config import build_delegated_container_runtime

    cfg = AppConfig()
    cfg.delegation.container_backend = "jail"
    with pytest.raises(ValueError, match="unsupported delegation.container_backend"):
        build_delegated_container_runtime(cfg)


# --- create_app -----------------------------------------------------------


def test_create_app_builds_default_app() -> None:
    from haas.config import create_app

    cfg = AppConfig()
    cfg.store.backend = "memory"
    cfg.adapters.default_base = "fake"
    app = create_app(cfg)
    assert app is not None
    assert app.state.haas_config is cfg


def test_create_app_attaches_model_proxy_when_credential_fd_set(monkeypatch) -> None:
    import socket

    from haas.config import create_app

    cfg = AppConfig()
    cfg.store.backend = "memory"
    cfg.adapters.default_base = "fake"
    s1, s2 = socket.socketpair()
    try:
        monkeypatch.setenv("HAAS_CREDENTIAL_FD", str(s1.fileno()))
        app = create_app(cfg)
        assert app.state.runtime.sessions.model_proxy is not None
        assert app.router.lifespan_context is not None
    finally:
        s1.close()
        s2.close()
