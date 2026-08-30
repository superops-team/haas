"""Remaining-scope contract tests: adapter assembly, session listing, skills.

Covers specs/config §5.1 (adapter assembly), specs/haas-protocol
`/v1/haas/sessions` (SessionListEnvelope + cursor pagination) and
specs/harness-registry §5.1 / specs/mcp-tool-skill-runtime §8 (skill bundle
files and path safety).
"""
from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from haas.api import build_app
from haas.config import AppConfig, create_app
from haas.identity import Principal

pytestmark = pytest.mark.adk

TOKEN_A = "tok-a"
TOKEN_B = "tok-b"
AUTH_A = {"Authorization": f"Bearer {TOKEN_A}"}
AUTH_B = {"Authorization": f"Bearer {TOKEN_B}"}


def _client() -> TestClient:
    return TestClient(
        build_app(
            identity_tokens={
                TOKEN_A: Principal(
                    principalId="p_a", tenantId="t_a", userIds=frozenset({"u_1", "u_2"})
                ),
                TOKEN_B: Principal(principalId="p_b", tenantId="t_b"),
            }
        )
    )


def _run(client: TestClient, session_id: str, user_id: str = "u_1") -> None:
    resp = client.post(
        "/run",
        json={
            "appName": "chrn_codex_default",
            "userId": user_id,
            "sessionId": session_id,
            "newMessage": {"role": "user", "parts": [{"text": "hi"}]},
        },
        headers=AUTH_A,
    )
    assert resp.status_code == 200, resp.text


# --- adapter assembly (specs/config §5.1) -----------------------------------


def test_create_app_defaults_to_codex_adapter() -> None:
    """The production entrypoint must not silently fall back to FakeAdapter."""
    app = create_app(AppConfig())
    adapter = app.state.runtime.adapter
    assert adapter.base == "codex"


def test_create_app_honours_fake_base_for_local_dev() -> None:
    config = AppConfig()
    config.adapters.default_base = "fake"
    app = create_app(config)
    assert app.state.runtime.adapter.base == "fake"


def test_create_app_does_not_require_live_harness() -> None:
    """Assembly must not block startup when Codex is not running."""
    config = AppConfig()
    config.adapters.codex.socket_path = "/tmp/haas-does-not-exist.sock"
    app = create_app(config)
    client = TestClient(app)
    # /health is process liveness only and must stay ok.
    assert client.get("/v1/haas/health").json()["data"]["status"] == "ok"
    # execution readiness must honestly report not_ready.
    body = client.get("/v1/haas/ready?scope=execution").json()
    assert body["data"]["status"] == "not_ready"


def test_adapter_base_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    from haas.config import load_config

    monkeypatch.setenv("HAAS_ADAPTER_BASE", "fake")
    assert load_config().adapters.default_base == "fake"


def test_adapter_config_from_yaml_file(tmp_path: Any) -> None:
    from haas.config import load_config

    path = tmp_path / "haas.yaml"
    path.write_text(
        "adapters:\n"
        "  default_base: fake\n"
        "  codex:\n"
        "    transport: stdio\n"
        "    socket_path: /tmp/custom.sock\n",
        encoding="utf-8",
    )
    cfg = load_config(str(path))
    assert cfg.adapters.default_base == "fake"
    assert cfg.adapters.codex.transport == "stdio"
    assert cfg.adapters.codex.socket_path == "/tmp/custom.sock"


# --- session listing --------------------------------------------------------


def test_list_sessions_returns_envelope_array() -> None:
    client = _client()
    _run(client, "hsess_1")
    resp = client.get("/v1/haas/sessions", headers=AUTH_A)
    assert resp.status_code == 200
    body = resp.json()
    # SessionListEnvelope: data is an ARRAY, nextCursor sits on the envelope.
    assert isinstance(body["data"], list)
    assert "traceId" in body
    assert "nextCursor" in body
    session = body["data"][0]
    for key in ("id", "appName", "userId", "state", "events", "lastUpdateTime"):
        assert key in session


def test_list_sessions_requires_auth() -> None:
    assert _client().get("/v1/haas/sessions").status_code == 401


def test_list_sessions_is_scoped_to_caller() -> None:
    client = _client()
    _run(client, "hsess_mine")
    assert client.get("/v1/haas/sessions", headers=AUTH_B).json()["data"] == []


def test_list_sessions_filters_by_app() -> None:
    client = _client()
    _run(client, "hsess_1")
    hit = client.get(
        "/v1/haas/sessions?app=chrn_codex_default", headers=AUTH_A
    ).json()
    miss = client.get("/v1/haas/sessions?app=chrn_other", headers=AUTH_A).json()
    assert len(hit["data"]) == 1
    assert miss["data"] == []


def test_list_sessions_paginates_with_cursor() -> None:
    client = _client()
    for index in range(5):
        _run(client, f"hsess_{index}")

    first = client.get("/v1/haas/sessions?limit=2", headers=AUTH_A).json()
    assert len(first["data"]) == 2
    assert first["nextCursor"]

    second = client.get(
        f"/v1/haas/sessions?limit=2&cursor={first['nextCursor']}", headers=AUTH_A
    ).json()
    assert len(second["data"]) == 2

    seen = {s["id"] for s in first["data"]} | {s["id"] for s in second["data"]}
    assert len(seen) == 4, "pages must not overlap"

    last = client.get(
        f"/v1/haas/sessions?limit=2&cursor={second['nextCursor']}", headers=AUTH_A
    ).json()
    assert len(last["data"]) == 1
    assert last["nextCursor"] is None, "exhausted page must report null cursor"


def test_list_sessions_rejects_invalid_limit() -> None:
    client = _client()
    assert client.get("/v1/haas/sessions?limit=0", headers=AUTH_A).status_code == 400
    assert client.get("/v1/haas/sessions?limit=101", headers=AUTH_A).status_code == 400


def test_list_sessions_rejects_unknown_cursor() -> None:
    resp = _client().get("/v1/haas/sessions?cursor=bogus", headers=AUTH_A)
    assert resp.status_code == 400
    assert resp.json()["haasError"]["code"] == "invalid_input"


# --- skill bundle files -----------------------------------------------------


SKILL = {
    "id": "skill_repo_rules",
    "name": "repo-rules",
    "enabled": True,
    "files": [
        {"path": "SKILL.md", "content": "Skill instructions..."},
        {"path": "scripts/run.sh", "content": "echo hi"},
    ],
}


def _harness_with_skill(client: TestClient, skill: dict[str, Any]) -> str:
    resp = client.post(
        "/v1/haas/harnesses",
        json={"base": "codex", "name": "skilled", "skills": [skill]},
        headers=AUTH_A,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["id"]


def test_skill_files_round_trip() -> None:
    """spec §11: renaming/reading must not lose skill files."""
    client = _client()
    harness_id = _harness_with_skill(client, SKILL)
    resp = client.get(
        f"/v1/haas/harnesses/{harness_id}/skills/skill_repo_rules/files",
        headers=AUTH_A,
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["id"] == "skill_repo_rules"
    assert data["name"] == "repo-rules"
    paths = [f["path"] for f in data["files"]]
    assert paths == ["SKILL.md", "scripts/run.sh"]
    assert data["files"][0]["content"] == "Skill instructions..."


def test_skill_files_unknown_skill_is_404() -> None:
    client = _client()
    harness_id = _harness_with_skill(client, SKILL)
    resp = client.get(
        f"/v1/haas/harnesses/{harness_id}/skills/skill_nope/files", headers=AUTH_A
    )
    assert resp.status_code == 404


def test_skill_files_unknown_harness_is_404() -> None:
    resp = _client().get(
        "/v1/haas/harnesses/chrn_missing/skills/s/files", headers=AUTH_A
    )
    assert resp.status_code == 404


def test_skill_files_cross_tenant_is_404() -> None:
    client = _client()
    harness_id = _harness_with_skill(client, SKILL)
    resp = client.get(
        f"/v1/haas/harnesses/{harness_id}/skills/skill_repo_rules/files",
        headers=AUTH_B,
    )
    assert resp.status_code == 404


@pytest.mark.parametrize(
    "bad_path",
    ["../escape.md", "/etc/passwd", "nested/../../escape.md", "a\x00b.md"],
)
def test_skill_rejects_unsafe_paths(bad_path: str) -> None:
    """spec §8: skill paths cannot escape their skill root."""
    client = _client()
    skill = {
        "id": "s1",
        "name": "bad",
        "files": [{"path": "SKILL.md", "content": "x"}, {"path": bad_path, "content": "y"}],
    }
    resp = client.post(
        "/v1/haas/harnesses",
        json={"base": "codex", "name": "bad-skill", "skills": [skill]},
        headers=AUTH_A,
    )
    assert resp.status_code == 422
    assert resp.json()["haasError"]["code"] == "haas_skill_source_invalid"


def test_skill_requires_skill_md() -> None:
    """spec §10: an enabled skill bundle without SKILL.md fails validation."""
    client = _client()
    skill = {"id": "s1", "name": "no-md", "enabled": True,
             "files": [{"path": "notes.md", "content": "x"}]}
    resp = client.post(
        "/v1/haas/harnesses",
        json={"base": "codex", "name": "bad", "skills": [skill]},
        headers=AUTH_A,
    )
    assert resp.status_code == 422
    assert resp.json()["haasError"]["code"] == "haas_skill_source_invalid"


def test_skill_binary_content_is_preserved_byte_for_byte() -> None:
    """spec §4: binary content must survive round-trip via contentB64."""
    import base64

    client = _client()
    raw = bytes(range(256))
    skill = {
        "id": "s_bin",
        "name": "bin",
        "files": [
            {"path": "SKILL.md", "content": "doc"},
            {"path": "blob.bin", "contentB64": base64.b64encode(raw).decode()},
        ],
    }
    harness_id = _harness_with_skill(client, skill)
    data = client.get(
        f"/v1/haas/harnesses/{harness_id}/skills/s_bin/files", headers=AUTH_A
    ).json()["data"]
    blob = next(f for f in data["files"] if f["path"] == "blob.bin")
    assert base64.b64decode(blob["contentB64"]) == raw
