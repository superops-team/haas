#!/bin/sh
# Install the HaaS pre-commit hook into .git/hooks/pre-commit.
#
# We intentionally do NOT set core.hooksPath: if the machine already has a
# global hooksPath (such as the ByteDance security hook), git runs that global
# hook, which in turn chains this repo-local .git/hooks/pre-commit.
set -eu

repo_root="$(git rev-parse --show-toplevel)" || exit 1
mkdir -p "$repo_root/.git/hooks"
cp "$repo_root/.githooks/pre-commit" "$repo_root/.git/hooks/pre-commit"
chmod +x "$repo_root/.git/hooks/pre-commit"
echo "Installed $repo_root/.git/hooks/pre-commit"
