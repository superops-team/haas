"""S1 smoke tests: package import, app factory startup, config defaults."""
from fastapi.testclient import TestClient

from haas.config import AppConfig, create_app, load_config


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
    client = TestClient(create_app(AppConfig()))
    control = client.get("/v1/haas/ready?scope=control")
    assert control.status_code == 200
    assert control.json()["data"]["status"] == "ready"

    execution = client.get("/v1/haas/ready?scope=execution")
    assert execution.status_code == 200
    # FakeAdapter probes ready; a real adapter would report its own readiness.
    assert execution.json()["data"]["status"] == "ready"


def test_adk_health_ready_aliases() -> None:
    client = TestClient(create_app(AppConfig()))
    assert client.get("/health").json()["data"]["status"] == "ok"
    assert client.get("/ready").json()["data"]["status"] == "ready"
