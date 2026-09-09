from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from xml.etree import ElementTree

SCRIPT = Path(__file__).parents[1] / "scripts" / "quality" / "repository_metrics.py"


def run(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, check=False, capture_output=True, text=True)


def init_repository(root: Path) -> None:
    run("git", "init", "-q", str(root))
    run("git", "config", "user.name", "HaaS Metrics Test", cwd=root)
    run("git", "config", "user.email", "metrics@example.invalid", cwd=root)
    run("git", "add", ".", cwd=root)
    result = run("git", "commit", "-qm", "fixture", cwd=root)
    assert result.returncode == 0, result.stderr


def test_generates_metrics_from_tracked_source_and_coverage(tmp_path: Path) -> None:
    for directory in ("haas", "tests", "scripts", "docs"):
        (tmp_path / directory).mkdir()
    (tmp_path / "haas" / "app.py").write_text("one = 1\n\ntwo = 2\n", encoding="utf-8")
    (tmp_path / "tests" / "test_app.py").write_text("assert True\n", encoding="utf-8")
    (tmp_path / "scripts" / "run.sh").write_text("#!/bin/sh\n\necho ok\n", encoding="utf-8")
    (tmp_path / "docs" / "generated.py").write_text("excluded = True\n", encoding="utf-8")
    coverage = tmp_path / "coverage.json"
    coverage.write_text(json.dumps({"totals": {"percent_covered": 91.26}}), encoding="utf-8")
    init_repository(tmp_path)
    (tmp_path / "scripts" / "untracked.py").write_text("excluded = True\n", encoding="utf-8")
    (tmp_path / "scripts" / "generated.gif").write_bytes(b"not source")

    output = tmp_path / "badges"
    result = run(
        sys.executable,
        str(SCRIPT),
        "--repo-root",
        str(tmp_path),
        "--coverage-json",
        str(coverage),
        "--output-dir",
        str(output),
    )

    assert result.returncode == 0, result.stderr
    assert "commits: 1" in (output / "commits.svg").read_text(encoding="utf-8")
    assert "lines: 5" in (output / "lines.svg").read_text(encoding="utf-8")
    assert "coverage: 91.3%" in (output / "coverage.svg").read_text(encoding="utf-8")
    first = {item.name: item.read_bytes() for item in output.iterdir()}
    rerun = run(
        sys.executable,
        str(SCRIPT),
        "--repo-root",
        str(tmp_path),
        "--coverage-json",
        str(coverage),
        "--output-dir",
        str(output),
    )
    assert rerun.returncode == 0, rerun.stderr
    assert first == {item.name: item.read_bytes() for item in output.iterdir()}
    for badge in output.glob("*.svg"):
        root = ElementTree.parse(badge).getroot()
        assert root.tag.endswith("svg")
        assert root.attrib["role"] == "img"
        assert not root.findall(".//{http://www.w3.org/2000/svg}script")


def test_invalid_coverage_preserves_existing_output(tmp_path: Path) -> None:
    (tmp_path / "haas").mkdir()
    (tmp_path / "haas" / "app.py").write_text("value = 1\n", encoding="utf-8")
    coverage = tmp_path / "coverage.json"
    coverage.write_text(json.dumps({"totals": {"percent_covered": 101}}), encoding="utf-8")
    init_repository(tmp_path)
    output = tmp_path / "badges"
    output.mkdir()
    sentinel = output / "coverage.svg"
    sentinel.write_text("previous", encoding="utf-8")

    result = run(
        sys.executable,
        str(SCRIPT),
        "--repo-root",
        str(tmp_path),
        "--coverage-json",
        str(coverage),
        "--output-dir",
        str(output),
    )

    assert result.returncode != 0
    assert "between 0 and 100" in result.stderr
    assert sentinel.read_text(encoding="utf-8") == "previous"


def test_shallow_repository_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "haas").mkdir()
    (source / "haas" / "app.py").write_text("value = 1\n", encoding="utf-8")
    (source / "coverage.json").write_text(
        json.dumps({"totals": {"percent_covered": 90}}), encoding="utf-8"
    )
    init_repository(source)
    clone = tmp_path / "clone"
    cloned = run("git", "clone", "-q", "--depth=1", source.as_uri(), str(clone))
    assert cloned.returncode == 0, cloned.stderr

    result = run(
        sys.executable,
        str(SCRIPT),
        "--repo-root",
        str(clone),
        "--coverage-json",
        str(clone / "coverage.json"),
        "--output-dir",
        str(clone / "badges"),
    )

    assert result.returncode != 0
    assert "full Git history" in result.stderr
    assert not (clone / "badges").exists()
