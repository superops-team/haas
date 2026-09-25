"""Tests for Codex app-server schema snapshot and drift detection.

Default tests are offline (roadmap §8.1): they exercise the pure drift
comparison and fixture loading without invoking the real ``codex`` binary.
Real schema re-generation is gated behind ``HAAS_E2E_CODEX=1``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from haas.harnesses.codex_app_server.schema import (
    codex_cli_version,
    generate_schema_files,
    load_fixture,
    schema_drift,
)


def _fixture(files: dict[str, object]) -> dict[str, object]:
    return {"codexCliVersion": "0.150.1", "files": files}


# --- offline unit tests ----------------------------------------------------


def test_no_drift_when_identical() -> None:
    files = {"v2/A.json": {"a": 1}, "v2/B.json": {"b": 2}}
    assert schema_drift(_fixture(files), dict(files)) == []


def test_detects_removed_file() -> None:
    fixture = _fixture({"v2/A.json": {"a": 1}})
    assert schema_drift(fixture, {}) == ["removed: v2/A.json"]


def test_detects_added_file() -> None:
    fixture = _fixture({})
    assert schema_drift(fixture, {"v2/A.json": {"a": 1}}) == ["added: v2/A.json"]


def test_detects_changed_file() -> None:
    fixture = _fixture({"v2/A.json": {"a": 1}})
    assert schema_drift(fixture, {"v2/A.json": {"a": 2}}) == ["changed: v2/A.json"]


def test_rejects_fixture_without_files_map() -> None:
    with pytest.raises(ValueError, match="files"):
        schema_drift({"codexCliVersion": "0.150.1"}, {})


def test_load_fixture_roundtrip(tmp_path: Path) -> None:
    data = {"codexCliVersion": "0.150.1", "files": {"v2/A.json": {"a": 1}}}
    (tmp_path / "codex-cli-0.150.1.json").write_text(json.dumps(data), encoding="utf-8")
    assert load_fixture("0.150.1", fixture_root=tmp_path) == data


def test_load_fixture_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_fixture("9.9.9", fixture_root=tmp_path)


def test_load_fixture_rejects_malformed(tmp_path: Path) -> None:
    (tmp_path / "codex-cli-0.150.1.json").write_text('{"no":"files"}', encoding="utf-8")
    with pytest.raises(ValueError, match="files"):
        load_fixture("0.150.1", fixture_root=tmp_path)


# --- gated real-CLI schema comparison --------------------------------------


@pytest.mark.e2e
def test_schema_fixture_matches_pinned_codex() -> None:
    if os.environ.get("HAAS_E2E") != "1" and os.environ.get("HAAS_E2E_CODEX") != "1":
        pytest.skip("HAAS_E2E=1 / HAAS_E2E_CODEX=1 required")
    version = codex_cli_version()
    fixture = load_fixture(version)
    current = generate_schema_files()
    assert schema_drift(fixture, current) == []


# --- offline: codex CLI version parsing & generation -----------------------


def test_codex_cli_version_parses_standard_output(monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess

    class _Result:
        stdout = "codex-cli 0.150.1\n"

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Result())
    assert codex_cli_version("codex") == "0.150.1"


def test_codex_cli_version_rejects_unexpected_output(monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess

    class _Result:
        stdout = "weird-single-token"

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Result())
    with pytest.raises(RuntimeError, match="unexpected codex --version output"):
        codex_cli_version("codex")


def test_generate_schema_files_collects_nested_json(monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess

    def _fake_run(cmd: list[str], **_kwargs: object) -> object:
        out_dir = Path(cmd[-1])
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "root.json").write_text('{"a": 1}', encoding="utf-8")
        nested = out_dir / "v2"
        nested.mkdir()
        (nested / "ThreadStartParams.json").write_text('{"b": 2}', encoding="utf-8")
        return object()

    monkeypatch.setattr(subprocess, "run", _fake_run)
    files = generate_schema_files("codex")
    assert files == {
        "root.json": {"a": 1},
        "v2/ThreadStartParams.json": {"b": 2},
    }


def test_generate_schema_files_rejects_empty_output(monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: object())
    with pytest.raises(RuntimeError, match="produced no schema files"):
        generate_schema_files("codex")
