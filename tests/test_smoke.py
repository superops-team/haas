"""S1 smoke tests: package import, app factory startup, config defaults."""
from fastapi.testclient import TestClient

from haas.config import AppConfig, create_app, load_config
from haas.harnesses.base import AdapterProbe


def test_package_import_and_default_config() -> None:
    cfg = load_config()
    assert cfg.server.port == 8092
    assert cfg.store.backend == "sqlite"
    assert cfg.identity.provider == "static"


def test_health_returns_envelope() -> None:
    client = TestClient(create_app(AppConfig()))
    resp = client.get("/v1/haas/health")
    assert resp.status_code == 200
    body = resp.json()
    assert "data" in body
    assert "traceId" in body
    assert body["data"]["status"] == "ok"


def test_ready_distinguishes_health_from_ready() -> None:
    # create_app now assembles the real Codex adapter by default, so pin the
    # fake base here to assert the health/ready split itself.
    config = AppConfig()
    config.adapters.default_base = "fake"
    client = TestClient(create_app(config))
    control = client.get("/v1/haas/ready?scope=control")
    assert control.status_code == 200
    assert control.json()["data"]["status"] == "ready"

    execution = client.get("/v1/haas/ready?scope=execution")
    assert execution.status_code == 200
    # FakeAdapter probes ready; a real adapter reports its own readiness.
    assert execution.json()["data"]["status"] == "ready"


def test_ready_is_not_published_when_adapter_is_unavailable() -> None:
    config = AppConfig()
    config.adapters.default_base = "fake"
    app = create_app(config)
    runtime = app.state.runtime

    async def unavailable_probe() -> AdapterProbe:
        return AdapterProbe(
            adapterId="fake",
            base="fake",
            status="unavailable",
            runtimeVersion="test",
            transport="fake",
            safeDetails={"safeReason": "codex_readiness_probe_failed"},
        )

    runtime.adapter.probe = unavailable_probe  # type: ignore[method-assign]
    client = TestClient(app)
    response = client.get("/v1/haas/ready")

    assert response.status_code == 503
    assert response.json()["haasError"]["code"] == "haas_adapter_unavailable"


def test_adk_health_ready_aliases() -> None:
    config = AppConfig()
    config.adapters.default_base = "fake"
    client = TestClient(create_app(config))
    assert client.get("/health").json()["data"]["status"] == "ok"
    assert client.get("/ready").json()["data"]["status"] == "ready"


def test_static_token_file_overrides_default_dev_token(tmp_path) -> None:
    token_file = tmp_path / "haas-token"
    token_file.write_text("local-secret\n")
    config = AppConfig()
    config.identity.static_token_file = str(token_file)
    config.adapters.default_base = "fake"
    client = TestClient(create_app(config))

    assert (
        client.get("/list-apps", headers={"Authorization": "Bearer local-secret"}).status_code
        == 200
    )
    assert (
        client.get("/list-apps", headers={"Authorization": "Bearer dev-token"}).status_code
        == 401
    )


def test_missing_static_token_file_does_not_fall_back_to_dev_token(tmp_path) -> None:
    config = AppConfig()
    config.identity.static_token_file = str(tmp_path / "missing-token")
    config.adapters.default_base = "fake"
    client = TestClient(create_app(config))

    assert (
        client.get("/list-apps", headers={"Authorization": "Bearer dev-token"}).status_code
        == 401
    )
