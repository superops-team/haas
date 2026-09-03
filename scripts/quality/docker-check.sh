#!/bin/sh
# HaaS Dockerfile checks (specs/container-runtime §11).
#
# Two tiers:
#   default            static checks only (fast, offline, safe for pre-commit)
#   HAAS_DOCKER_BUILD=1 additionally runs a real build + container smoke
#
# The static tier alone once passed while the image could not build at all
# (missing `COPY README.md`), so any container-affecting change must run the
# build tier before it is considered verified.
set -eu

repo_root="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$repo_root"

fail=0

# --- static tier ------------------------------------------------------------

echo "==> docker-check: base image digest pin"
base_arg="$(sed -n 's/^ARG HAAS_BASE_IMAGE=//p' Dockerfile | head -1)"
if printf '%s\n' "$base_arg" | grep -qE '^ghcr.io/agent-infra/sandbox@sha256:[0-9a-f]{64}$'; then
  echo "  ok: default base image digest is pinned"
else
  echo "  FAIL: Dockerfile default HAAS_BASE_IMAGE must pin a digest (sha256:<64 hex>)"
  fail=1
fi

echo "==> docker-check: entrypoint syntax"
echo "==> docker-check: dependency cache mounts"
if grep -qE -- '--mount=type=cache,target=/root/\.npm' Dockerfile && grep -qE -- '--mount=type=cache,target=/root/\.cache/uv' Dockerfile; then
  echo "  ok: npm and uv BuildKit cache mounts are present"
else
  echo "  FAIL: Dockerfile must cache npm and uv dependency downloads"
  fail=1
fi
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

# AIO's node22 REPL defaults to 8092 and would fight the sidecar for the port.
echo "==> docker-check: AIO node REPL port override"
if grep -qE 'NODEJS_REPL_PORT_22=' Dockerfile; then
  echo "  ok: NODEJS_REPL_PORT_22 is overridden (AIO REPL off 8092)"
else
  echo "  FAIL: AIO node22 REPL defaults to 8092 and collides with the sidecar;"
  echo "        set NODEJS_REPL_PORT_22 in Dockerfile"
  fail=1
fi

# HaaS does not use AIO's IDE/notebook/REPL services; they are disabled via
# AIO's own env knobs (specs/runtime-trim/README.md) to cut runtime footprint.
echo "==> docker-check: AIO service trim knobs"
if grep -qE 'DISABLE_CODE_SERVER=true' Dockerfile \
  && grep -qE 'DISABLE_JUPYTER=true' Dockerfile \
  && grep -qE 'DISABLE_NODEJS_REPL=true' Dockerfile; then
  echo "  ok: code-server / jupyter / node REPL disabled via AIO env vars"
else
  echo "  FAIL: Dockerfile must disable unused AIO services via DISABLE_CODE_SERVER,"
  echo "        DISABLE_JUPYTER and DISABLE_NODEJS_REPL (specs/runtime-trim/README.md)"
  fail=1
fi

# pyproject-declared files must exist in the build context, or `uv sync` fails
# at image build time while every static check still passes.
echo "==> docker-check: pyproject readme is copied into the image"
readme="$(sed -n 's/^readme[[:space:]]*=[[:space:]]*"\(.*\)"/\1/p' pyproject.toml | head -1)"
if [ -z "$readme" ]; then
  echo "  ok: pyproject declares no readme"
elif grep -qE "^COPY .*(${readme}|\. )" Dockerfile; then
  echo "  ok: ${readme} is copied before the project install"
else
  echo "  FAIL: pyproject declares readme = \"${readme}\" but Dockerfile never"
  echo "        copies it; the project install will fail during build"
  fail=1
fi

echo "==> docker-check: sidecar liveness is enforced by the entrypoint"
if grep -qE 'FATAL sidecar exited|exit 1' docker/run.sh; then
  echo "  ok: entrypoint exits non-zero when the sidecar dies"
else
  echo "  FAIL: docker/run.sh must fail the container when the sidecar exits,"
  echo "        otherwise the container stays Up with no API"
  fail=1
fi

echo "==> docker-check: nginx HaaS entrypoint fragment"
if grep -q "proxy_pass http://127.0.0.1:8092" docker/nginx.haas.conf \
  && grep -q "location = /ready" docker/nginx.haas.conf \
  && ! grep -q "codex-worker" docker/nginx.haas.conf; then
  echo "  ok: standard HaaS nginx fragment is loopback-only and shim-free"
else
  echo "  FAIL: nginx fragment must proxy standard HaaS routes without legacy shim"
  fail=1
fi

# linux/amd64 is a hard delivery contract (AGENTS.md). The build path must pin
# the platform so amd64 images are produced on any host (e.g. Apple Silicon).
echo "==> docker-check: linux/amd64 platform is pinned in the build path"
if grep -qE '^HAAS_PLATFORM[[:space:]]*:?=[[:space:]]*linux/amd64' Makefile \
  && grep -qE -- '--platform=?[[:space:]"]*\$\(HAAS_PLATFORM\)' Makefile; then
  echo "  ok: make docker-build pins --platform=linux/amd64"
else
  echo "  FAIL: Makefile must set HAAS_PLATFORM := linux/amd64 and build with"
  echo "        --platform=\$(HAAS_PLATFORM)"
  fail=1
fi

if [ "$fail" -ne 0 ]; then
  echo "docker-check: FAILED (static)"
  exit 1
fi
echo "docker-check: static checks PASSED"

# --- build + smoke tier -----------------------------------------------------

if [ "${HAAS_DOCKER_BUILD:-0}" != "1" ]; then
  echo "==> docker-check: build/smoke skipped (set HAAS_DOCKER_BUILD=1 to run)"
  exit 0
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "docker-check: FAILED - HAAS_DOCKER_BUILD=1 but docker is unavailable"
  exit 1
fi

image="haas:docker-check"
name="haas-docker-check-$$"
port="${HAAS_DOCKER_CHECK_PORT:-18099}"
# linux/amd64 is the project's hard platform contract (AGENTS.md). Build and run
# the smoke image on amd64 even on Apple Silicon hosts.
platform="${HAAS_PLATFORM:-linux/amd64}"

cleanup() {
  docker rm -f "$name" >/dev/null 2>&1 || true
  docker rmi "$image" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

echo "==> docker-check: building image from current checkout (platform=$platform)"
base_image="${HAAS_BASE_IMAGE:-$base_arg}"
if ! printf '%s\n' "$base_image" | grep -qE '@sha256:[0-9a-f]{64}$'; then
  echo "docker-check: FAILED - HAAS_BASE_IMAGE must be digest-pinned"
  exit 1
fi
if ! docker build -q --platform "$platform" --build-arg "HAAS_BASE_IMAGE=$base_image" -t "$image" . >/dev/null; then
  echo "docker-check: FAILED - image build failed"
  exit 1
fi
echo "  ok: image built"

echo "==> docker-check: container run smoke"
docker run -d --platform "$platform" --name "$name" -p "${port}:8080" "$image" >/dev/null

ready=0
i=0
while [ "$i" -lt 60 ]; do
  if curl -fsS --max-time 3 "http://127.0.0.1:${port}/health" >/dev/null 2>&1; then
    ready=1
    break
  fi
  # Fail fast when the container is already gone.
  if [ "$(docker inspect -f '{{.State.Running}}' "$name" 2>/dev/null)" != "true" ]; then
    echo "  FAIL: container exited during startup"
    docker logs "$name" 2>&1 | tail -20
    exit 1
  fi
  i=$((i + 1))
  sleep 2
done

if [ "$ready" -ne 1 ]; then
  echo "  FAIL: nginx did not proxy HaaS /health on public port 8080"
  docker logs "$name" 2>&1 | tail -20
  exit 1
fi
echo "  ok: nginx proxies HaaS health on 8080"

# AIO must survive alongside the sidecar (no port cannibalisation). AIO boots
# slower than the sidecar, so poll instead of sampling once.
aio=0
i=0
while [ "$i" -lt 30 ]; do
  if docker exec "$name" sh -c \
     "curl -fsS --max-time 5 -o /dev/null http://127.0.0.1:8080/v1/docs" >/dev/null 2>&1; then
    aio=1
    break
  fi
  i=$((i + 1))
  sleep 2
done
if [ "$aio" -eq 1 ]; then
  echo "  ok: AIO still serving on 8080"
else
  echo "  FAIL: AIO port 8080 not serving; HaaS must not displace AIO services"
  docker logs "$name" 2>&1 | tail -20
  exit 1
fi

# Trimmed AIO services must NOT be running (specs/runtime-trim/README.md),
# while the CUA/BUA + sandbox base is preserved.
echo "==> docker-check: trimmed AIO services are disabled"
trimmed_up="$(docker exec "$name" sh -c \
  "ps -eo args 2>/dev/null | grep -E 'code-server|jupyter-lab|jupyter lab' | grep -v grep" 2>/dev/null || true)"
if [ -z "$trimmed_up" ]; then
  echo "  ok: code-server / jupyter are not running"
else
  echo "  FAIL: a trimmed service is still running:"
  printf '%s\n' "$trimmed_up" | head -3
  exit 1
fi

# The production entrypoint must reach the real harness, not a test double.
bases="$(curl -fsS --max-time 10 -H 'Authorization: Bearer dev-token' \
  "http://127.0.0.1:${port}/v1/haas/status" 2>/dev/null \
  | tr ',' '\n' | grep -c '"base": *"codex"' || true)"
if [ "${bases:-0}" -ge 1 ]; then
  echo "  ok: codex adapter is assembled in the image"
else
  echo "  FAIL: no codex adapter in /v1/haas/status; the entrypoint fell back"
  echo "        to a test adapter"
  exit 1
fi

# A dead sidecar must take the container down instead of staying "Up".
docker exec "$name" sh -c "pkill -f 'uvicorn haas.config'" >/dev/null 2>&1 || true
i=0
while [ "$i" -lt 15 ]; do
  state="$(docker inspect -f '{{.State.Running}}' "$name" 2>/dev/null || echo false)"
  [ "$state" = "false" ] && break
  i=$((i + 1))
  sleep 1
done
code="$(docker inspect -f '{{.State.ExitCode}}' "$name" 2>/dev/null || echo -1)"
if [ "$state" = "false" ] && [ "$code" != "0" ]; then
  echo "  ok: container exits non-zero (${code}) when the sidecar dies"
else
  echo "  FAIL: sidecar died but container did not fail (running=${state} code=${code})"
  exit 1
fi

echo "docker-check: PASSED (static + build/smoke)"
