"""S6 artifact API wiring contract tests.

Covers specs/artifact-store/README.md §5.1/§6.1/§6.1.1/§6.1.2 and the
OpenAPI `File`/`FileEnvelope`/`HaasEnvelope` shapes in
specs/haas-protocol/haas-2026-08-26.openapi.yaml.
"""
from __future__ import annotations

import io
import zipfile

import pytest
from fastapi.testclient import TestClient

from haas.api import DEFAULT_TOKEN, build_app
from haas.identity import Principal

# /v1/haas/* control-plane surface is part of the published protocol contract.
pytestmark = pytest.mark.adk

AUTH = {"Authorization": f"Bearer {DEFAULT_TOKEN}"}
OTHER_TOKEN = "other-token"


def _client() -> TestClient:
    app = build_app(
        identity_tokens={
            DEFAULT_TOKEN: Principal(principalId="p_dev"),
            OTHER_TOKEN: Principal(principalId="p_other"),
        }
    )
    return TestClient(app)


def _upload(client: TestClient, name: str = "report.md", body: bytes = b"hello") -> dict:
    resp = client.post(
        "/v1/haas/files",
        files={"file": (name, body, "text/markdown")},
        headers=AUTH,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


# --- upload -----------------------------------------------------------------


def test_upload_returns_file_envelope() -> None:
    payload = _upload(_client())
    assert "traceId" in payload
    data = payload["data"]
    assert data["object"] == "file"
    assert data["id"].startswith("file_")
    assert data["filename"] == "report.md"
    assert data["bytes"] == 5
    assert data["sha256"]
    assert data["mediaType"] == "text/markdown"


def test_upload_requires_auth() -> None:
    resp = _client().post(
        "/v1/haas/files", files={"file": ("a.txt", b"x", "text/plain")}
    )
    assert resp.status_code == 401
    assert resp.json()["haasError"]["code"] == "missing_credential"


def test_upload_rejects_oversized_file() -> None:
    app = build_app(max_file_bytes=4)
    client = TestClient(app)
    resp = client.post(
        "/v1/haas/files",
        files={"file": ("big.bin", b"123456", "application/octet-stream")},
        headers=AUTH,
    )
    assert resp.status_code == 413
    assert resp.json()["haasError"]["code"] == "haas_file_too_large"


def test_upload_rejects_traversal_filename() -> None:
    resp = _client().post(
        "/v1/haas/files",
        files={"file": ("../../etc/passwd", b"x", "text/plain")},
        headers=AUTH,
    )
    assert resp.status_code == 400
    assert resp.json()["haasError"]["code"] == "invalid_input"


@pytest.mark.parametrize("filename", ["nested/report.md", r"nested\\report.md", "..", "."])
def test_upload_rejects_non_basename_filename(filename: str) -> None:
    resp = _client().post(
        "/v1/haas/files",
        files={"file": (filename, b"x", "text/plain")},
        headers=AUTH,
    )
    assert resp.status_code == 400
    assert resp.json()["haasError"]["code"] == "invalid_input"


# --- download ---------------------------------------------------------------


def test_download_returns_bytes_with_nosniff() -> None:
    client = _client()
    file_id = _upload(client, body=b"payload-bytes")["data"]["id"]
    resp = client.get(f"/v1/haas/files/{file_id}/content", headers=AUTH)
    assert resp.status_code == 200
    assert resp.content == b"payload-bytes"
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["content-disposition"].startswith("attachment")


def test_download_unknown_file_returns_404() -> None:
    resp = _client().get("/v1/haas/files/file_missing/content", headers=AUTH)
    assert resp.status_code == 404
    assert resp.json()["haasError"]["code"] == "haas_file_not_found"


def test_download_cross_principal_returns_404() -> None:
    """Security Boundary §8.3: cross-scope read is 404, never 403."""
    client = _client()
    file_id = _upload(client)["data"]["id"]
    resp = client.get(
        f"/v1/haas/files/{file_id}/content",
        headers={"Authorization": f"Bearer {OTHER_TOKEN}"},
    )
    assert resp.status_code == 404
    assert resp.json()["haasError"]["code"] == "haas_file_not_found"


def test_pdf_preview_not_implemented() -> None:
    client = _client()
    file_id = _upload(client)["data"]["id"]
    resp = client.get(f"/v1/haas/files/{file_id}/pdf", headers=AUTH)
    assert resp.status_code == 501
    assert resp.json()["haasError"]["code"] == "haas_preview_unavailable"


# --- session artifact listing ----------------------------------------------


def test_list_session_artifacts_empty() -> None:
    resp = _client().get("/v1/haas/sessions/hsess_none/artifacts", headers=AUTH)
    assert resp.status_code == 200
    body = resp.json()
    assert body["data"]["artifacts"] == []
    assert "traceId" in body


def test_list_session_artifacts_after_attach() -> None:
    app = build_app()
    client = TestClient(app)
    store = app.state.runtime.artifacts
    store.register(
        "hsess_1", "output/a.md", b"aa", owner_principal_id="p_dev"
    )
    resp = client.get("/v1/haas/sessions/hsess_1/artifacts", headers=AUTH)
    assert resp.status_code == 200
    artifacts = resp.json()["data"]["artifacts"]
    assert len(artifacts) == 1
    assert artifacts[0]["relativePath"] == "output/a.md"
    assert artifacts[0]["object"] == "file"


def test_list_session_artifacts_scoped_by_principal() -> None:
    app = build_app(
        identity_tokens={
            DEFAULT_TOKEN: Principal(principalId="p_dev"),
            OTHER_TOKEN: Principal(principalId="p_other"),
        }
    )
    client = TestClient(app)
    app.state.runtime.artifacts.register(
        "hsess_1", "output/a.md", b"aa", owner_principal_id="p_dev"
    )
    resp = client.get(
        "/v1/haas/sessions/hsess_1/artifacts",
        headers={"Authorization": f"Bearer {OTHER_TOKEN}"},
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["artifacts"] == []


# --- archive ----------------------------------------------------------------


def test_archive_returns_zip() -> None:
    app = build_app()
    client = TestClient(app)
    app.state.runtime.artifacts.register(
        "hsess_1", "output/a.md", b"alpha", owner_principal_id="p_dev"
    )
    app.state.runtime.artifacts.register(
        "hsess_1", "output/b.txt", b"beta", owner_principal_id="p_dev"
    )
    resp = client.get("/v1/haas/sessions/hsess_1/artifacts/archive", headers=AUTH)
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"
    assert resp.headers["x-content-type-options"] == "nosniff"
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        assert sorted(zf.namelist()) == ["output/a.md", "output/b.txt"]
        assert zf.read("output/a.md") == b"alpha"


def test_archive_empty_session_returns_404() -> None:
    resp = _client().get(
        "/v1/haas/sessions/hsess_none/artifacts/archive", headers=AUTH
    )
    assert resp.status_code == 404
    assert resp.json()["haasError"]["code"] == "haas_file_not_found"


# --- status / diagnostics ---------------------------------------------------


def test_status_returns_snapshot() -> None:
    resp = _client().get("/v1/haas/status", headers=AUTH)
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["status"] in {"ok", "degraded"}
    assert data["protocol"]["adkProtocol"] == "2.0"
    assert isinstance(data["adapters"], list)


def test_diagnostics_is_redacted() -> None:
    resp = _client().get("/v1/haas/diagnostics", headers=AUTH)
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["partial"] is False
    # Diagnostics must never carry raw credential material.
    assert DEFAULT_TOKEN not in resp.text
