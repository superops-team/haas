#!/usr/bin/env python3
"""HaaS secret scanner for pre-commit.

Scans for credentials and other sensitive material and exits non-zero when any
is found, so the commit is blocked. Stdlib only; no third-party dependencies.

Usage:
  secret-scan.py                 # scan staged added lines (git hook default)
  secret-scan.py <path>...       # scan specific files of the working tree
  secret-scan.py --all           # scan all tracked files (full-repo audit)

A line can be whitelisted for approved test fixtures with the inline marker
`# haas-secret-ignore`; such usage must be confirmed in code review.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys

SELF = os.path.normpath("scripts/quality/secret-scan.py")
IGNORE_MARKER = "haas-secret-ignore"

# Value hints that are clearly placeholders, not real secrets.
PLACEHOLDER_RE = re.compile(
    r"<[^>]+>|YOUR_|your[-_]?\w+|EXAMPLE|example|xxxx+|XXXX+|placeholder|dummy|"
    r"fake|null|none|secret_ref|secret://|vault://|FIXME|TODO|\$\{[^}]+\}",
    re.IGNORECASE,
)

# (name, compiled regex) — each matches a sensitive token on a single line.
PATTERNS = [
    ("private_key", re.compile(
        r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY(?: BLOCK)?-----")),
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("github_token", re.compile(
        r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b")),
    ("openai_style_key", re.compile(
        r"\bsk-(?:proj-|ant-|svcacct-)?[A-Za-z0-9_-]{20,}\b")),
    ("stripe_live_key", re.compile(r"\b(?:sk|pk|rk)_live_[0-9A-Za-z]{10,}\b")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_\-]{20,}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[0-9A-Za-z-]{10,}\b")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}(?:\.[A-Za-z0-9_\-]{8,}){2}\b")),
    ("bearer_token", re.compile(
        r"(?i)\bBearer\s+[A-Za-z0-9][A-Za-z0-9._=+\/\-]{15,}\b")),
    ("basic_auth", re.compile(
        r"(?i)\bBasic\s+[A-Za-z0-9+/]{16,}=*")),
    ("presigned_url", re.compile(
        r"(?:X-Amz-Signature|X-Amz-Credential)=[A-Za-z0-9\/%]{8,}|\bSignature=[A-Fa-f0-9]{32,}")),
]

# Generic `key = "literal"` assignment for well-known secret-bearing field names.
# Keys may be prefixed (e.g. `server_password`, `db-password`, `access_token`),
# but not part of a longer identifier (`mypassword` is intentionally skipped).
GENERIC_KEY = re.compile(
    r"(?i)(?:^|[^A-Za-z0-9])"
    r"(password|passwd|pwd|client[_-]?secret|api[_-]?key|apikey|"
    r"access[_-]?(?:token|key)|secret[_-]?key|private[_-]?key|"
    r"auth[_-]?token|session[_-]?token|refresh[_-]?token)\b"
    r"\s*[:=]")

# Sensitive file names to flag even if the content scanner misses them.
SENSITIVE_FILENAME = [
    re.compile(r"\.env(\..*)?$"),
    re.compile(r"\.pem$"),
    re.compile(r"\.p12$"),
    re.compile(r"\.pfx$"),
    re.compile(r"\.key$"),
    re.compile(r"\.jks$"),
    re.compile(r"\.keystore$"),
    re.compile(r"(^|/)(id_rsa|id_ed25519|id_ecdsa|id_dsa)(\.pub)?$"),
    re.compile(r"(credentials|service-?account)[^/]*\.json$"),
    re.compile(r"\.token$"),
    re.compile(r"(^|/)secrets?/"),
]


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
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
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
