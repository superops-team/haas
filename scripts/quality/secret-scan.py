#!/usr/bin/env python3
"""HaaS secret scanner for pre-commit.

Scans for credentials and other sensitive material and exits non-zero when any
is found, so the commit is blocked. Stdlib only; no third-party dependencies.

The regex table is the single source of truth shared with the runtime redaction
pipeline (``haas/security/patterns.json``). Keep both consumers in sync by
editing that file, not this one.

Usage:
  secret-scan.py                 # scan staged added lines (git hook default)
  secret-scan.py <path>...       # scan specific files of the working tree
  secret-scan.py --all           # scan all tracked files (full-repo audit)

A line can be whitelisted for approved test fixtures with the inline marker
`# haas-secret-ignore`; such usage must be confirmed in code review.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

SELF = os.path.normpath("scripts/quality/secret-scan.py")
IGNORE_MARKER = "haas-secret-ignore"
PATTERNS_FILE = (
    Path(__file__).resolve().parents[2] / "haas" / "security" / "patterns.json"
)

# Value hints that are clearly placeholders, not real secrets.
PLACEHOLDER_RE = re.compile(
    r"<[^>]+>|YOUR_|your[-_]?\w+|EXAMPLE|example|xxxx+|XXXX+|placeholder|dummy|"
    r"fake|null|none|test|stale|live|openai-key|sk-ant-x|hunter2|abc123|\bsecret\b|"
    r"secret_ref|secret://|vault://|"
    r"FIXME|TODO|\$\{[^}]+\}",
    re.IGNORECASE,
)

DYNAMIC_VALUE_RE = re.compile(
    r"^(?:"
    r"[A-Za-z_][A-Za-z0-9_\.]*(?:\(|\)|\]|\s|\}|\)|,|$)"
    r"|Optional\["
    r"|str\)"
    r"|os\.environ"
    r"|\("
    r"|\{"
    r")"
)


def load_patterns() -> tuple[
    list[tuple[str, re.Pattern[str]]], re.Pattern[str], list[re.Pattern[str]]
]:
    with open(PATTERNS_FILE, encoding="utf-8") as fh:
        data = json.load(fh)

    token_patterns = [
        (item["name"], re.compile(item["pattern"]))
        for item in data["token_patterns"]
    ]
    generic_key = re.compile(data["generic_key"])
    sensitive_filenames = [
        re.compile(pattern) for pattern in data["sensitive_filenames"]
    ]
    return token_patterns, generic_key, sensitive_filenames


PATTERNS, GENERIC_KEY, SENSITIVE_FILENAME = load_patterns()


def run_git(*args: str) -> str:
    proc = subprocess.run(["git", *args], capture_output=True, text=True)
    if proc.returncode != 0:
        print("error: git " + " ".join(args) + " failed", file=sys.stderr)
        sys.exit(2)
    return proc.stdout


def find_in_line(line: str) -> list[tuple[str, str]]:
    """Return triggers for one line, or []."""
    if IGNORE_MARKER in line:
        return []

    hits: list[tuple[str, str]] = []
    for name, regex in PATTERNS:
        for m in regex.finditer(line):
            if PLACEHOLDER_RE.search(m.group(0)) or PLACEHOLDER_RE.search(line):
                continue
            hits.append((name, m.group(0)))

    m = GENERIC_KEY.search(line)
    if m:
        remainder = line[m.end():].strip()
        value = remainder
        if value[:1] in ("'", '"'):
            quote = value[0]
            end = value.find(quote, 1)
            value = value[1:end] if end != -1 else value[1:]
        else:
            value = value.split(",")[0].split("#")[0].strip()
        value = value.strip()
        if (len(value) >= 8
                and not PLACEHOLDER_RE.search(value)
                and not PLACEHOLDER_RE.search(line)
                and not DYNAMIC_VALUE_RE.search(value)
                and "==" not in line
                and value not in ("true", "false")):
            hits.append(("generic_secret_assignment", f"{m.group(1)}={value[:24]}..."))

    return hits


def checked_path_records(files: list[str]):
    """Yield (path, line_no, line) for every line of the given files."""
    for path in files:
        path = os.path.normpath(path)
        if path == SELF:
            continue
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                data = fh.read()
        except OSError:
            continue  # binary or missing; not a secret risk here
        for line_no, line in enumerate(data.splitlines(), start=1):
            yield path, line_no, line


def staged_line_records():
    """Yield (path, line_no, added_line) for staged added/renamed/copied lines."""
    text = run_git("diff", "--cached", "--unified=0", "--no-color",
                   "--diff-filter=ACMR")
    path: str | None = None
    new_ln = 0
    for raw in text.splitlines():
        if raw.startswith("+++ b/"):
            path = raw[6:]
            new_ln = 0
            continue
        if raw.startswith("+++ "):
            path = raw[4:]
            new_ln = 0
            continue
        if raw.startswith("@@"):
            m = re.search(r"\+(\d+)", raw)
            new_ln = (int(m.group(1)) - 1) if m else 0
            continue
        if path is None:
            continue
        if raw.startswith("+") and not raw.startswith("+++"):
            new_ln += 1
            yield path, new_ln, raw[1:]
        elif not raw.startswith("-"):
            new_ln += 1


def main(argv: list[str]) -> int:
    if argv == ["--all"]:
        files = run_git("ls-files").splitlines()
        records = checked_path_records(files)
    elif argv and not argv[0].startswith("-"):
        files = argv
        records = checked_path_records(files)
    else:
        files = run_git("diff", "--cached", "--name-only",
                        "--diff-filter=ACMR").splitlines()
        records = staged_line_records()

    found = 0
    for name in files:
        for regex in SENSITIVE_FILENAME:
            if regex.search(name):
                print(f"{name}: sensitive filename (blocked)")
                found += 1
                break

    for path, line_no, line in records:
        for rule, snippet in find_in_line(line):
            print(f"{path}:{line_no}: {rule}: {snippet.strip()}")
            found += 1

    if found:
        print(f"\nSecret scan blocked: {found} finding(s). "
              "Remove the material or review it before committing.",
              file=sys.stderr)
        return 1
    print("Secret scan passed: no findings")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
