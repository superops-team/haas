"""Codex app-server JSON Schema snapshot and drift detection.

Implements specs/codex-app-server-adapter/README.md §11.1 (schema fixture
contract): pin a Codex CLI version, snapshot the `codex app-server
generate-json-schema` output as a single JSON fixture, and re-run the
generator at probe time to detect wire-protocol drift.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any


def codex_cli_version(codex_bin: str = "codex") -> str:
    """Return the semantic version from ``codex --version`` (e.g. ``0.150.1``)."""
    raw = subprocess.run(
        [codex_bin, "--version"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    # Expected shape: "codex-cli 0.150.1" or "codex 0.150.1".
    parts = raw.split()
    if len(parts) < 2:
        raise RuntimeError(f"unexpected codex --version output: {raw!r}")
    return parts[-1]


def generate_schema_files(codex_bin: str = "codex") -> dict[str, Any]:
    """Generate the app-server schema bundle and return a relative-path -> JSON map.

    The returned map keys are paths relative to the generated output directory
    (e.g. ``codex_app_server_protocol.v2.schemas.json``, ``v2/ThreadStartParams.json``).
    """
    with tempfile.TemporaryDirectory(prefix="haas-codex-schema-") as tmp:
        out_dir = Path(tmp) / "schema"
        subprocess.run(
            [codex_bin, "app-server", "generate-json-schema", "--out", str(out_dir)],
            check=True,
            capture_output=True,
            text=True,
        )
        files: dict[str, Any] = {}
        for path in sorted(out_dir.rglob("*.json")):
            rel = path.relative_to(out_dir).as_posix()
            files[rel] = json.loads(path.read_text(encoding="utf-8"))
    if not files:
        raise RuntimeError("codex app-server generate-json-schema produced no schema files")
    return files


def load_fixture(version: str, fixture_root: Path | None = None) -> dict[str, Any]:
    """Load a pinned schema fixture for ``version`` from tests/fixtures/codex/schema/."""
    default_root = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "codex" / "schema"
    root = fixture_root or default_root
    path = root / f"codex-cli-{version}.json"
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict) or "files" not in data:
        raise ValueError(f"invalid schema fixture (missing 'files' map): {path}")
    return data


def schema_drift(fixture: dict[str, Any], current_files: dict[str, Any]) -> list[str]:
    """Compare a pinned fixture against freshly generated files.

    Returns a list of drift descriptions; an empty list means no drift.
    """
    fixture_files = fixture.get("files")
    if not isinstance(fixture_files, dict):
        raise ValueError("fixture missing 'files' map")

    drift: list[str] = []
    for rel in sorted(set(fixture_files) | set(current_files)):
        in_fixture = rel in fixture_files
        in_current = rel in current_files
        if in_fixture and not in_current:
            drift.append(f"removed: {rel}")
        elif in_current and not in_fixture:
            drift.append(f"added: {rel}")
        elif fixture_files[rel] != current_files[rel]:
            drift.append(f"changed: {rel}")
    return drift
