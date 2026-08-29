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
  --host 0.0.0.0 --port "${HAAS_SIDECAR_PORT:-8092}" &
SIDECAR_PID=$!
pids="${pids} ${SIDECAR_PID}"

# 3. Start Codex app-server listener for the codex adapter (internal only).
codex app-server --listen "unix://${CODEX_SOCKET}" &
CODEX_PID=$!
pids="${pids} ${CODEX_PID}"

echo "==> haas: started (sidecar=${SIDECAR_PID}, codex=${CODEX_PID}, aio=${AIO_PID:-none})"

# Wait for any child to exit; on SIGTERM the trap drains everything.
wait -n 2>/dev/null || wait
