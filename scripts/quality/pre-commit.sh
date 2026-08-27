#!/bin/sh
# HaaS pre-commit quality gates.
# Runs whitespace/conflict checks first, then the secret scanner.
# The secret scan must pass; a failure blocks the commit.
set -eu

repo_root="$(git rev-parse --show-toplevel)" || exit 1

echo "==> HaaS pre-commit: whitespace / conflict-marker check"
git diff --cached --check

echo "==> HaaS pre-commit: secret scan"
python3 "$repo_root/scripts/quality/secret-scan.py"
