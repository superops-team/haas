#!/bin/sh
# HaaS Dockerfile static checks (specs/container-runtime §11).
# Verifies base-image digest pin, entrypoint syntax, and port conventions.
# Does NOT build the image (build/smoke is a separate, explicit step).
set -eu

repo_root="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$repo_root"

fail=0

echo "==> docker-check: base image digest pin"
if grep -qE '^FROM .*@sha256:[0-9a-f]{64}' Dockerfile; then
  echo "  ok: base image digest is pinned"
else
  echo "  FAIL: Dockerfile FROM must pin a digest (sha256:<64 hex>)"
  fail=1
fi

echo "==> docker-check: entrypoint syntax"
if sh -n docker/run.sh; then
  echo "  ok: docker/run.sh is valid sh"
else
  echo "  FAIL: docker/run.sh failed sh -n"
  fail=1
fi

echo "==> docker-check: port conventions (8092 sidecar)"
if grep -qE 'HAAS_SIDECAR_PORT=8092|--port 8092|"8092"' Dockerfile docker/run.sh; then
  echo "  ok: sidecar port 8092 present"
else
  echo "  FAIL: sidecar port 8092 not found in Dockerfile/docker/run.sh"
  fail=1
fi

if [ "$fail" -ne 0 ]; then
  echo "docker-check: FAILED"
  exit 1
fi
echo "docker-check: PASSED"
