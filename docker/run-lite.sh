#!/bin/sh
# Dedicated Lite entrypoint: sidecar + Codex only, no AIO/nginx services.
set -eu

runtime_root="${HAAS_RUNTIME_ROOT:-/tmp/haas}"
data_root="${HAAS_DATA_ROOT:-/data/haas}"
codex_socket="${CODEX_SOCKET_PATH:-${runtime_root}/codex.sock}"
mkdir -p "${runtime_root}" "${data_root}/state" "${data_root}/artifacts" \
  "${data_root}/harnesses/codex/home" "${data_root}/private" \
  "${data_root}/receipts" "$(dirname "${codex_socket}")"
chmod 700 "${data_root}/private" "${data_root}/receipts"
generation="${HAAS_WORKER_GENERATION:-0}"
case "${generation}" in
  ''|*[!0-9]*) echo "invalid worker generation" >&2; exit 1 ;;
esac
token_file="${data_root}/private/generation-${generation}.token"
umask 077
if [ ! -s "${token_file}" ]; then
  dd if=/dev/urandom of="${token_file}.tmp" bs=32 count=1 status=none
  chmod 600 "${token_file}.tmp"
  mv -f "${token_file}.tmp" "${token_file}"
fi
chmod 600 "${token_file}"
rm -f "${codex_socket}"

sidecar_pid=""
codex_pid=""
stop() {
  [ -z "${sidecar_pid}" ] || kill -TERM "${sidecar_pid}" 2>/dev/null || true
  [ -z "${codex_pid}" ] || kill -TERM "${codex_pid}" 2>/dev/null || true
  wait 2>/dev/null || true
}
trap stop EXIT INT TERM

/app/haas/.venv/bin/uvicorn haas.config:create_app --factory \
  --host 0.0.0.0 --port "${HAAS_SIDECAR_PORT:-8092}" &
sidecar_pid=$!
codex app-server --listen "unix://${codex_socket}" &
codex_pid=$!

while kill -0 "${sidecar_pid}" 2>/dev/null; do
  if ! kill -0 "${codex_pid}" 2>/dev/null; then
    codex_pid=""
  fi
  sleep 2
done
wait "${sidecar_pid}"
