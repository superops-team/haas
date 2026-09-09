#!/usr/bin/env python3
"""Generate deterministic repository-owned SVG metric badges."""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from xml.sax.saxutils import escape

SOURCE_SUFFIXES = frozenset({".py", ".sh", ".js", ".mjs", ".cjs"})
SOURCE_ROOTS = ("haas", "tests", "scripts")


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(("git", *args), cwd=repo, check=False, capture_output=True, text=True)
    if result.returncode != 0:
        raise ValueError(result.stderr.strip() or "git command failed")
    return result.stdout.strip()


def collect_commit_count(repo: Path) -> int:
    if git(repo, "rev-parse", "--is-shallow-repository") == "true":
        raise ValueError("commit count requires a full Git history")
    return int(git(repo, "rev-list", "--count", "HEAD"))


def collect_source_lines(repo: Path) -> int:
    raw = subprocess.run(
        ("git", "ls-files", "-z", "--", *SOURCE_ROOTS),
        cwd=repo,
        check=True,
        capture_output=True,
    ).stdout
    total = 0
    for encoded in raw.split(b"\0"):
        if not encoded:
            continue
        relative = Path(os.fsdecode(encoded))
        if relative.suffix not in SOURCE_SUFFIXES:
            continue
        with (repo / relative).open("r", encoding="utf-8") as source:
            total += sum(1 for line in source if line.strip())
    return total


def read_coverage(coverage_json: Path) -> float:
    try:
        value = float(
            json.loads(coverage_json.read_text(encoding="utf-8"))["totals"]["percent_covered"]
        )
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise ValueError("coverage JSON does not contain totals.percent_covered") from error
    if not math.isfinite(value) or not 0 <= value <= 100:
        raise ValueError("coverage percentage must be finite and between 0 and 100")
    return value


def badge(label: str, value: str, color: str) -> str:
    title = escape(f"{label}: {value}")
    safe_label, safe_value = escape(label), escape(value)
    label_width = 86
    value_width = max(58, 16 + len(value) * 8)
    width = label_width + value_width
    return f"""<svg xmlns="http://www.w3.org/2000/svg"
  width="{width}" height="28" viewBox="0 0 {width} 28"
  role="img" aria-label="{title}">
  <title>{title}</title>
  <linearGradient id="surface" x2="0" y2="1">
    <stop stop-color="#172033"/><stop offset="1" stop-color="#0b1220"/>
  </linearGradient>
  <clipPath id="round"><rect width="{width}" height="28" rx="7"/></clipPath>
  <g clip-path="url(#round)">
    <rect width="{label_width}" height="28" fill="url(#surface)"/>
    <rect x="{label_width}" width="{value_width}" height="28" fill="{color}"/>
    <path d="M{label_width} 0v28" stroke="#ffffff" stroke-opacity=".12"/>
  </g>
  <g fill="#ffffff" text-anchor="middle"
    font-family="ui-monospace,SFMono-Regular,Menlo,Consolas,monospace"
    font-size="11" font-weight="600">
    <text x="{label_width / 2:g}" y="18">{safe_label}</text>
    <text x="{label_width + value_width / 2:g}" y="18">{safe_value}</text>
  </g>
</svg>
"""


def publish(output: Path, values: dict[str, tuple[str, str]]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output.name}-", dir=output.parent))
    backup = output.with_name(f".{output.name}.previous")
    try:
        for name, (value, color) in values.items():
            (temporary / f"{name}.svg").write_text(badge(name, value, color), encoding="utf-8")
        if backup.exists():
            shutil.rmtree(backup)
        if output.exists():
            output.rename(backup)
        temporary.rename(output)
        if backup.exists():
            shutil.rmtree(backup)
    except Exception:
        if output.exists() and backup.exists():
            shutil.rmtree(output)
        if backup.exists():
            backup.rename(output)
        raise
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--coverage-json", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    repo = args.repo_root.resolve()
    commits = collect_commit_count(repo)
    lines = collect_source_lines(repo)
    coverage = read_coverage(args.coverage_json.resolve())
    publish(
        args.output_dir.resolve(),
        {
            "commits": (f"{commits:,}", "#0891b2"),
            "lines": (f"{lines:,}", "#059669"),
            "coverage": (f"{coverage:.1f}%", "#7c3aed"),
        },
    )
    print(f"commits={commits} lines={lines} coverage={coverage:.1f}%")


if __name__ == "__main__":
    main()
