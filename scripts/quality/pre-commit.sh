#!/bin/sh
# HaaS pre-commit quality gates.
# Runs skill, whitespace/conflict, and secret checks.
# Every check must pass; a failure blocks the commit.
set -eu

repo_root="$(git rev-parse --show-toplevel)" || exit 1

echo "==> HaaS pre-commit: repository-local agent skills"
uv run --project "$repo_root" python "$repo_root/scripts/quality/check_agent_skills.py" \
  "$repo_root/.agents/skills"

echo "==> HaaS pre-commit: whitespace / conflict-marker check"
git diff --cached --check

echo "==> HaaS pre-commit: secret scan"
python3 "$repo_root/scripts/quality/secret-scan.py"
