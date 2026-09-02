#!/bin/sh
# HaaS container entrypoint (specs/container-runtime §5.2).
# Prepares runtime dirs, delegates AIO startup, starts HaaS sidecar + Codex
# app-server, and forwards SIGTERM to all child process groups.
set -eu

RUNTIME_ROOT="${HAAS_RUNTIME_ROOT:-/tmp/haas}"
DATA_ROOT="${HAAS_DATA_ROOT:-/data/haas}"
CODEX_SOCKET="${CODEX_SOCKET_PATH:-${RUNTIME_ROOT}/codex.sock}"

mkdir -p \
  "${RUNTIME_ROOT}" \
  "${DATA_ROOT}/state" \
  "${DATA_ROOT}/artifacts" \
  "${DATA_ROOT}/harnesses/codex/home" \
  "$(dirname "${CODEX_SOCKET}")"

# Track child PIDs so SIGTERM reaches the whole process group.
pids=""

configure_nginx_health_route() {
  conf="/opt/gem/nginx/nginx.python_srv.conf"
  if [ ! -f "${conf}" ] || ! grep -Eq '^location = /health[ /{]' "${conf}"; then
    return 0
  fi
  if awk '
    BEGIN { depth = 0; skip = 0 }
    !skip && index($0, "location = /health") == 1 { skip = 1 }
    skip {
      for (i = 1; i <= length($0); i++) {
        c = substr($0, i, 1)
        if (c == "{") depth++
        else if (c == "}") { depth--; if (depth == 0) { skip = 0; break } }
      }
      if (skip) next
      next
    }
    { print }
    END { if (skip) exit 2 }
  ' "${conf}" > "${conf}.haas.tmp"; then
    status=0
  else
    status=$?
  fi
  if [ "$status" -eq 2 ]; then
    echo "==> haas: FATAL incomplete AIO /health nginx block" >&2
    rm -f "${conf}.haas.tmp"
    exit 1
  fi
  if grep -Eq '^location = /health[ /{]' "${conf}.haas.tmp"; then
    echo "==> haas: FATAL failed to replace AIO /health nginx block" >&2
    rm -f "${conf}.haas.tmp"
    exit 1
  fi
  mv -f "${conf}.haas.tmp" "${conf}"
}

configure_nginx_health_route

forward_signal() {
  trap - TERM INT
  echo "==> haas: draining (SIGTERM)"
  for pid in ${pids}; do
    kill -TERM "${pid}" 2>/dev/null || true
  done
  # Delegate AIO shutdown if it was started by this script.
  if [ -n "${AIO_PID:-}" ]; then
    kill -TERM "${AIO_PID}" 2>/dev/null || true
  fi
  wait 2>/dev/null || true
  exit 0
}
trap forward_signal TERM INT

# 1. Delegate OpenSandbox AIO startup (preserves /opt/gem/run.sh capability).
if [ -x /opt/gem/run.sh ]; then
  echo "==> haas: delegating OpenSandbox AIO /opt/gem/run.sh"
  /opt/gem/run.sh &
  AIO_PID=$!
  pids="${pids} ${AIO_PID}"
else
  echo "==> haas: warning: /opt/gem/run.sh not found; AIO services unavailable"
fi

# 2. Start HaaS sidecar on 8092.
#    Use the uvicorn --factory app. Health is NOT gated on AIO/Codex here.
/app/haas/.venv/bin/uvicorn haas.config:create_app --factory \
  --host 127.0.0.1 --port "${HAAS_SIDECAR_PORT:-8092}" &
SIDECAR_PID=$!
pids="${pids} ${SIDECAR_PID}"

# 3. Start Codex app-server listener for the codex adapter (internal only).
codex app-server --listen "unix://${CODEX_SOCKET}" &
CODEX_PID=$!
pids="${pids} ${CODEX_PID}"

echo "==> haas: started (sidecar=${SIDECAR_PID}, codex=${CODEX_PID}, aio=${AIO_PID:-none})"

# The sidecar is the reason this container exists: if it dies (port conflict,
# crash, bad config) the container must fail loudly instead of staying "Up"
# with no API. AIO/Codex exits are reported but do not tear the container down.
while true; do
  if ! kill -0 "${SIDECAR_PID}" 2>/dev/null; then
    wait "${SIDECAR_PID}" 2>/dev/null || true
    echo "==> haas: FATAL sidecar exited; stopping container" >&2
    exit 1
  fi
  if [ -n "${CODEX_PID:-}" ] && ! kill -0 "${CODEX_PID}" 2>/dev/null; then
    echo "==> haas: warning: codex app-server exited; execution readiness will fail" >&2
    CODEX_PID=""
  fi
  sleep 2
done
