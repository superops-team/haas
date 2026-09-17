from __future__ import annotations

import pytest

from haas.config import AppConfig, SessionRuntimeConfig, build_store, load_config
from haas.stores import MemoryStore, SQLiteStore


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
