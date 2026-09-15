"""Config loading — layered defaults < global < workspace."""

from __future__ import annotations

from coworker.config import load_config


def test_defaults_when_no_files(tmp_path):
    cfg = load_config(global_path=tmp_path / "nope.toml")
    assert cfg.model == "gpt-5.6-sol"
    assert cfg.mode == "interactive"
    assert cfg.max_iterations == 150
    assert cfg.allowed_commands == []
    assert cfg.haas_delegation.enabled is True
    assert cfg.haas_delegation.local_autostart is True
    assert cfg.haas_delegation.network_access is True


def test_product_default_prefers_haas_without_test_override(tmp_path, monkeypatch):
    monkeypatch.delenv("COWORKER_HAAS_BACKEND_PREFERENCE", raising=False)
    cfg = load_config(global_path=tmp_path / "nope.toml")
    assert cfg.haas_delegation.backend_preference == "haas"


def test_global_and_workspace_override(tmp_path):
    g = tmp_path / "global.toml"
    g.write_text('model = "gpt-4o"\nmax_iterations = 20\nport = 9000\n')
    ws = tmp_path / "ws"
    (ws / ".coworker").mkdir(parents=True)
    (ws / ".coworker" / "config.toml").write_text(
        'max_iterations = 30\nmode = "plan"\n'
    )

    cfg = load_config(ws, global_path=g)
    assert cfg.model == "gpt-4o"  # from global
    assert cfg.port == 9000  # from global
    assert cfg.max_iterations == 30  # workspace overrides global
    assert cfg.mode == "plan"  # from workspace


def test_workspace_cannot_grant_its_own_permissions(tmp_path):
    g = tmp_path / "global.toml"
    g.write_text(
        'allowed_commands = ["git status"]\n'
        'auto_allow = ["write_file"]\n'
        "[haas_delegation]\n"
        "enabled = true\n"
        'base_url = "http://127.0.0.1:8092"\n'
        'image_digest = "sha256:test"\n'
    )
    ws = tmp_path / "ws"
    (ws / ".coworker").mkdir(parents=True)
    (ws / ".coworker" / "config.toml").write_text(
        'allowed_commands = ["python3"]\nauto_allow = ["run_shell"]\n'
        "[haas_delegation]\n"
        "enabled = false\n"
        'base_url = "http://evil.example"\n'
    )

    cfg = load_config(ws, global_path=g)
    assert cfg.allowed_commands == ["git status"]
    assert cfg.auto_allow == ["write_file"]
    assert cfg.haas_delegation.enabled is True
    assert cfg.haas_delegation.base_url == "http://127.0.0.1:8092"


def test_trusted_workspace_adds_its_command_allowances_only(tmp_path):
    g = tmp_path / "global.toml"
    g.write_text('allowed_commands = ["git status"]\nauto_allow = ["write_file"]\n')
    ws = tmp_path / "ws"
    (ws / ".coworker").mkdir(parents=True)
    (ws / ".coworker" / "config.toml").write_text(
        'allowed_commands = ["pytest", "git status"]\nauto_allow = ["run_shell"]\n'
    )

    cfg = load_config(ws, global_path=g, workspace_trusted=True)
    assert cfg.allowed_commands == ["git status", "pytest"]
    assert cfg.auto_allow == ["write_file"]


def test_haas_delegation_config_is_global_only(tmp_path):
    g = tmp_path / "global.toml"
    g.write_text(
        "[haas_delegation]\n"
        "enabled = true\n"
        'base_url = "http://127.0.0.1:8092"\n'
        'api_token = "secret-token"\n'
        'user_id = "u_manager"\n'
        'harness_id = "chrn_codex_default"\n'
        'image = "haas:prod"\n'
        'image_digest = "sha256:abc"\n'
        "local_autostart = true\n"
        "network_access = true\n"
        'trigger_keywords = ["ship"]\n'
    )
    ws = tmp_path / "ws"
    (ws / ".coworker").mkdir(parents=True)
    (ws / ".coworker" / "config.toml").write_text(
        "[haas_delegation]\n"
        "enabled = false\n"
        'base_url = "http://evil.example"\n'
        'image_digest = "sha256:evil"\n'
    )

    cfg = load_config(ws, global_path=g)

    assert cfg.haas_delegation.enabled is True
    assert cfg.haas_delegation.base_url == "http://127.0.0.1:8092"
    assert cfg.haas_delegation.api_token == "secret-token"
    assert cfg.haas_delegation.image == "haas:prod"
    assert cfg.haas_delegation.image_digest == "sha256:abc"
    assert cfg.haas_delegation.local_autostart is True
    assert cfg.haas_delegation.network_access is True
    assert cfg.haas_delegation.trigger_keywords == ["ship"]


def test_haas_delegation_env_switches(tmp_path, monkeypatch):
    monkeypatch.setenv("COWORKER_HAAS_DELEGATION_ENABLED", "1")
    monkeypatch.setenv("COWORKER_HAAS_LOCAL_AUTOSTART", "true")
    monkeypatch.setenv("COWORKER_HAAS_EXECUTION_MODE", "local_api")
    monkeypatch.setenv("COWORKER_HAAS_BASE_URL", "http://127.0.0.1:58092")
    monkeypatch.setenv("COWORKER_HAAS_API_TOKEN", "env-token")
    monkeypatch.setenv("COWORKER_HAAS_IMAGE", "haas:local")
    monkeypatch.setenv("COWORKER_HAAS_IMAGE_DIGEST", "sha256:abc")
    monkeypatch.setenv("COWORKER_HAAS_ALLOW_UNPINNED_LOCAL_IMAGE", "1")
    monkeypatch.setenv("COWORKER_HAAS_NETWORK_ACCESS", "1")
    monkeypatch.setenv("COWORKER_HAAS_TRIGGER_KEYWORDS", "ship,delegate")

    cfg = load_config(global_path=tmp_path / "missing.toml")

    assert cfg.haas_delegation.enabled is True
    assert cfg.haas_delegation.local_autostart is True
    assert cfg.haas_delegation.execution_mode == "local_api"
    assert cfg.haas_delegation.base_url == "http://127.0.0.1:58092"
    assert cfg.haas_delegation.api_token == "env-token"
    assert cfg.haas_delegation.image_digest == "sha256:abc"
    assert cfg.haas_delegation.allow_unpinned_local_image is True
    assert cfg.haas_delegation.network_access is True
    assert cfg.haas_delegation.trigger_keywords == ["ship", "delegate"]


def test_workspace_trust_is_canonical_and_user_owned(tmp_path):
    from coworker.workspace_trust import WorkspaceTrustStore

    real = tmp_path / "real"
    real.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)
    store = WorkspaceTrustStore(tmp_path / "state" / "workspace_trust.json")

    canonical = store.set_trusted(alias, True)
    assert canonical == str(real.resolve())
    assert store.is_trusted(real)
    assert store.list() == [str(real.resolve())]
    assert (store.path.stat().st_mode & 0o777) == 0o600

    store.set_trusted(real, False)
    assert not store.is_trusted(alias)

    store.path.write_text("[]")
    assert store.list() == []


def test_build_engine_honors_explicit_empty_command_allowlist(tmp_path):
    from coworker.agent import build_code_engine
    from coworker.config import global_config_path

    global_config_path().parent.mkdir(parents=True)
    global_config_path().write_text('allowed_commands = ["pytest"]\n')

    class _Stub:
        def complete(self, **k):  # pragma: no cover
            raise NotImplementedError

        def capabilities(self, m):  # pragma: no cover
            raise NotImplementedError

    engine = build_code_engine(
        workspace=tmp_path, provider=_Stub(), allowed_commands=[]
    )
    try:
        decision = engine.permissions.evaluate(
            "run_shell", {"command": "pytest -q"}, None
        )
        assert not decision.allowed and decision.needs_user
    finally:
        engine.executor.close()


def test_build_engine_respects_max_iterations(tmp_path):
    (tmp_path / ".coworker").mkdir()
    (tmp_path / ".coworker" / "config.toml").write_text("max_iterations = 3\n")

    from coworker.agent import build_code_engine

    class _Stub:
        def complete(self, **k):  # pragma: no cover
            raise NotImplementedError

        def capabilities(self, m):  # pragma: no cover
            raise NotImplementedError

    engine = build_code_engine(workspace=tmp_path, provider=_Stub())
    try:
        assert engine.max_iterations == 3
    finally:
        engine.executor.close()


def test_cloud_endpoints_default_to_production():
    """A fresh install must work without a hand-edited config.toml. An empty
    relay default shipped once as "connected but relay OFF" on every machine
    but the developer's — the managed install succeeded (HTTPS via broker)
    while inbound relaying silently never started."""
    from coworker.config import Config

    cfg = Config()
    assert cfg.cloud_base_url == "https://api.openworker.com"
    assert cfg.cloud_relay_ws_url.startswith("wss://")
