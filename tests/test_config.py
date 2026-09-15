from __future__ import annotations

import pytest

from haas.config import AppConfig, build_store, load_config
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
