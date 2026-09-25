#!/usr/bin/env bash
# Smoke-test the packaged macOS app's bundled sidecar after a build.
#
# This intentionally exercises the installed/bundled executable shape rather than
# the source tree. It catches failures where PyInstaller bundles stale code or the
# local-managed HaaS child cannot start against an upgraded SQLite store.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
PLATFORM="$(cd "$HERE/.." && pwd)"
DEFAULT_APP="$PLATFORM/surfaces/gui/src-tauri/target/release/bundle/macos/OpenHarness.app"
APP_BUNDLE="${1:-$DEFAULT_APP}"
SERVER="$APP_BUNDLE/Contents/Resources/sidecar/openworker-server"
PYTHON_BIN="${PYTHON_BIN:-$PLATFORM/.venv/bin/python}"

if [ ! -x "$SERVER" ]; then
  echo "packaged smoke: missing executable sidecar: $SERVER" >&2
  exit 1
fi
if [ ! -x "$PYTHON_BIN" ]; then
  PYTHON_BIN="python3"
fi

free_port() {
  "$PYTHON_BIN" - <<'PY'
import socket
with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
    sock.bind(("127.0.0.1", 0))
    print(sock.getsockname()[1])
PY
}

MANAGER_PORT="$(free_port)"
HAAS_PORT="$(free_port)"
STATE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/openharness-packaged-smoke.XXXXXX")"
WORKSPACE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/openharness-workspace.XXXXXX")"
MAIN_LOG="$STATE_DIR/logs/openworker-server.log"
HAAS_LOG="$STATE_DIR/logs/haas-sidecar.log"
PID=""
OCCUPIED_MANAGER_PORT=""
OCCUPIED_HAAS_PORT=""
OCCUPIED_STATE_DIR=""
OCCUPIED_WORKSPACE_DIR=""
OCCUPIED_MAIN_LOG=""
OCCUPIED_PID=""
PORT_HOLDER_PID=""

cleanup() {
  for live in "${PID:-}" "${OCCUPIED_PID:-}" "${PORT_HOLDER_PID:-}"; do
    if [ -n "$live" ] && kill -0 "$live" >/dev/null 2>&1; then
      kill "$live" >/dev/null 2>&1 || true
      wait "$live" >/dev/null 2>&1 || true
    fi
  done
  for port in "$MANAGER_PORT" "$HAAS_PORT" "${OCCUPIED_MANAGER_PORT:-}" "${OCCUPIED_HAAS_PORT:-}"; do
    [ -n "$port" ] || continue
    for live_pid in $(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true); do
      kill "$live_pid" >/dev/null 2>&1 || true
    done
  done
  if [ "${OPENHARNESS_KEEP_SMOKE_STATE:-}" != "1" ]; then
    rm -rf "$STATE_DIR" "$WORKSPACE_DIR" "${OCCUPIED_STATE_DIR:-}" "${OCCUPIED_WORKSPACE_DIR:-}"
  else
    echo "packaged smoke: kept state at $STATE_DIR and workspace at $WORKSPACE_DIR"
    [ -n "${OCCUPIED_STATE_DIR:-}" ] && echo "packaged smoke: kept occupied-port state at $OCCUPIED_STATE_DIR and workspace at $OCCUPIED_WORKSPACE_DIR"
  fi
}
trap cleanup EXIT

mkdir -p "$STATE_DIR/logs" "$STATE_DIR/home"
printf '%s\n' 'packaged files origin' > "$WORKSPACE_DIR/packaged-files-origin.txt"

"$PYTHON_BIN" - "$STATE_DIR/haas.db" <<'PY'
import json
import sqlite3
import sys

db = sys.argv[1]
conn = sqlite3.connect(db)
conn.execute(
    """CREATE TABLE IF NOT EXISTS records (
           namespace TEXT NOT NULL,
           record_key TEXT NOT NULL,
           payload TEXT NOT NULL,
           PRIMARY KEY(namespace, record_key)
       )"""
)
conn.execute(
    """CREATE TABLE IF NOT EXISTS idempotency (
           key_hash TEXT PRIMARY KEY,
           request_hash TEXT NOT NULL,
           result_json TEXT,
           released INTEGER NOT NULL DEFAULT 0,
           accepted INTEGER NOT NULL DEFAULT 0,
           invocation_id TEXT,
           accepted_at_ms INTEGER,
           completed_at_ms INTEGER,
           expires_at_ms INTEGER,
           tombstone INTEGER NOT NULL DEFAULT 0,
           schema_version INTEGER NOT NULL DEFAULT 1,
           created_at_ms INTEGER NOT NULL
       )"""
)
conn.execute("PRAGMA user_version = 1")
session = {
    "id": "hsess_upgrade_fixture",
    "appName": "chrn_codex_default",
    "userId": "manager",
    "state": {},
    "control_state": "idle",
    "supportsResume": False,
    "unknownFutureField": {"ignored": True},
}
invocation = {
    "id": "inv_upgrade_fixture",
    "sessionId": "hsess_upgrade_fixture",
    "appName": "chrn_codex_default",
    "turnId": "turn_upgrade_fixture",
    "userId": "manager",
    "status": "completed",
    "timeoutSeconds": 86400,
    "deadlineAtMs": 1900000000000,
    "unknownFutureField": {"ignored": True},
}
turn = {
    "id": "turn_upgrade_fixture",
    "invocationId": "inv_upgrade_fixture",
    "sessionId": "hsess_upgrade_fixture",
    "status": "completed",
    "unknownFutureField": {"ignored": True},
}
for namespace, key, payload in (
    ("session", "chrn_codex_default|manager|hsess_upgrade_fixture", session),
    ("invocation", "inv_upgrade_fixture", invocation),
    ("turn", "turn_upgrade_fixture", turn),
):
    conn.execute(
        "INSERT INTO records(namespace, record_key, payload) VALUES(?, ?, ?)",
        (namespace, key, json.dumps(payload, separators=(",", ":"))),
    )
conn.commit()
conn.close()
PY

env -i \
  PATH="/usr/bin:/bin:/usr/sbin:/sbin" \
  HOME="$STATE_DIR/home" \
  LANG="${LANG:-en_US.UTF-8}" \
  COWORKER_STATE_DIR="$STATE_DIR" \
  COWORKER_HAAS_DELEGATION_ENABLED=1 \
  COWORKER_HAAS_LOCAL_AUTOSTART=1 \
  COWORKER_HAAS_BACKEND_PREFERENCE=haas \
  COWORKER_HAAS_EXECUTION_MODE=local_api \
  COWORKER_HAAS_MODE=local_managed \
  COWORKER_HAAS_BASE_URL="http://127.0.0.1:$HAAS_PORT" \
  COWORKER_HAAS_REQUIRE_TRUSTED_WORKSPACE=0 \
  OPENAI_API_KEY="placeholder-openai-key" \
  "$SERVER" --cwd "$WORKSPACE_DIR" --host 127.0.0.1 --port "$MANAGER_PORT" \
  >"$MAIN_LOG" 2>&1 &
PID="$!"

wait_http() {
  url="$1"
  watched_pid="$2"
  log_file="$3"
  shift
  shift
  shift
  for _ in $(seq 1 120); do
    if curl -fsS "$@" "$url" >/dev/null 2>&1; then
      return 0
    fi
    if ! kill -0 "$watched_pid" >/dev/null 2>&1; then
      echo "packaged smoke: main sidecar exited before readiness" >&2
      tail -120 "$log_file" >&2 || true
      exit 1
    fi
    sleep 0.25
  done
  echo "packaged smoke: timeout waiting for $url" >&2
  tail -120 "$log_file" >&2 || true
  [ -f "$HAAS_LOG" ] && tail -160 "$HAAS_LOG" >&2 || true
  exit 1
}

wait_http "http://127.0.0.1:$MANAGER_PORT/v1/health" "$PID" "$MAIN_LOG"
TOKEN_FILE="$STATE_DIR/sidecar-$MANAGER_PORT.token"
for _ in $(seq 1 40); do
  [ -s "$TOKEN_FILE" ] && break
  sleep 0.1
done
if [ ! -s "$TOKEN_FILE" ]; then
  echo "packaged smoke: sidecar token was not created" >&2
  tail -120 "$MAIN_LOG" >&2 || true
  exit 1
fi
TOKEN="$(cat "$TOKEN_FILE")"
wait_http "http://127.0.0.1:$HAAS_PORT/v1/haas/health" "$PID" "$MAIN_LOG" \
  -H "Authorization: Bearer $TOKEN"

"$PYTHON_BIN" - "$MANAGER_PORT" "$TOKEN" <<'PY'
import json
import sys
import urllib.request

port, token = sys.argv[1], sys.argv[2]

def request(path: str, method: str = "GET"):
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        method=method,
        headers={"X-OpenWorker-Token": token},
    )
    with urllib.request.urlopen(req, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))

state = request("/v1/browser/state")
if state.get("managed") is not True or state.get("profile") != "app_owned":
    raise SystemExit(f"packaged browser state is not managed/app-owned: {state!r}")
screenshot = request("/v1/browser/screenshot", method="POST")
if screenshot.get("ok") is not True:
    raise SystemExit(f"packaged browser screenshot failed: {screenshot!r}")
if screenshot.get("browser") != "managed_chromium" or screenshot.get("managed") is not True:
    raise SystemExit(f"packaged browser did not use managed Chromium: {screenshot!r}")
if screenshot.get("executable") != "managed_runtime" or screenshot.get("headless") is not True:
    raise SystemExit(f"packaged browser did not use bundled headless runtime: {screenshot!r}")
if not str(screenshot.get("screenshot_data_url") or "").startswith("data:image/png;base64,"):
    raise SystemExit(f"packaged browser did not return a screenshot data URL: {screenshot!r}")
request("/v1/browser/close", method="POST")
PY

"$PYTHON_BIN" - "$MANAGER_PORT" "$TOKEN" "$WORKSPACE_DIR" <<'PY'
import json
import sys
import time
import urllib.parse

from websockets.sync.client import connect

port, token, workspace = sys.argv[1], sys.argv[2], sys.argv[3]
uri = (
    f"ws://127.0.0.1:{port}/ws/session/packaged-smoke"
    f"?workspace={urllib.parse.quote(workspace)}&agent=cowork"
)
seen = []
with connect(uri, subprotocols=["openworker", token], open_timeout=5, close_timeout=1) as ws:
    ready = json.loads(ws.recv(timeout=5))
    if ready.get("type") != "ready":
        raise SystemExit(f"expected ready, got {ready!r}")
    ws.send(json.dumps({
        "type": "user_message",
        "text": "fix packaged startup smoke",
        "model": "openai:gpt-5.6-sol",
    }))
    deadline = time.time() + 20
    while time.time() < deadline:
        event = json.loads(ws.recv(timeout=max(0.1, deadline - time.time())))
        seen.append(event)
        event_type = event.get("type")
        if event_type == "turn_start":
            ws.send(json.dumps({"type": "interrupt"}))
        if event_type == "turn_done":
            break
    else:
        raise SystemExit(f"timed out waiting for visible task outcome; seen={seen!r}")

types = [event.get("type") for event in seen]
if "turn_start" not in types and "error" not in types:
    raise SystemExit(f"task produced neither turn_start nor error; seen={seen!r}")
if "turn_done" not in types:
    raise SystemExit(f"task did not produce turn_done; seen={seen!r}")
PY

"$PYTHON_BIN" - "$MANAGER_PORT" "$TOKEN" "$WORKSPACE_DIR" <<'PY'
import json
import sys
import urllib.parse
import urllib.request

port, token, workspace = sys.argv[1], sys.argv[2], sys.argv[3]

def request(path: str):
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        headers={"X-OpenWorker-Token": token},
    )
    with urllib.request.urlopen(req, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))

encoded_root = urllib.parse.quote(workspace, safe="")
artifact_scope = request(
    f"/v1/sessions/packaged-smoke/artifacts/read?path={encoded_root}"
)
if artifact_scope.get("ok") is not False or artifact_scope.get("source") != "haas":
    raise SystemExit(
        f"packaged artifact scope unexpectedly read the local root: {artifact_scope!r}"
    )

files_scope = request(
    f"/v1/sessions/packaged-smoke/artifacts/read?path={encoded_root}&origin=files"
)
if files_scope.get("ok") is not True or files_scope.get("kind") != "folder":
    raise SystemExit(f"packaged Files root read failed: {files_scope!r}")
if not any(
    entry.get("name") == "packaged-files-origin.txt"
    for entry in files_scope.get("entries", [])
):
    raise SystemExit(f"packaged Files root omitted fixture file: {files_scope!r}")

encoded_file = urllib.parse.quote(
    f"{workspace}/packaged-files-origin.txt", safe=""
)
file_scope = request(
    f"/v1/sessions/packaged-smoke/artifacts/read?path={encoded_file}&origin=files"
)
if file_scope.get("ok") is not True or file_scope.get("content") != "packaged files origin\n":
    raise SystemExit(f"packaged Files file read failed: {file_scope!r}")
PY

kill "$PID" >/dev/null 2>&1 || true
wait "$PID" >/dev/null 2>&1 || true
PID=""

OCCUPIED_MANAGER_PORT="$(free_port)"
OCCUPIED_HAAS_PORT="$(free_port)"
OCCUPIED_STATE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/openharness-packaged-smoke-occupied.XXXXXX")"
OCCUPIED_WORKSPACE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/openharness-workspace-occupied.XXXXXX")"
OCCUPIED_MAIN_LOG="$OCCUPIED_STATE_DIR/logs/openworker-server.log"
mkdir -p "$OCCUPIED_STATE_DIR/logs" "$OCCUPIED_STATE_DIR/home"

"$PYTHON_BIN" - "$OCCUPIED_HAAS_PORT" <<'PY' &
import http.server
import sys

class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(418)
        self.end_headers()
        self.wfile.write(b"occupied")

    def log_message(self, format, *args):
        return

http.server.ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), Handler).serve_forever()
PY
PORT_HOLDER_PID="$!"
"$PYTHON_BIN" - "$OCCUPIED_HAAS_PORT" <<'PY'
import socket
import sys
import time

port = int(sys.argv[1])
deadline = time.time() + 5
while time.time() < deadline:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.25):
            raise SystemExit(0)
    except OSError:
        time.sleep(0.05)
raise SystemExit("occupied-port fixture did not start")
PY

env -i \
  PATH="/usr/bin:/bin:/usr/sbin:/sbin" \
  HOME="$OCCUPIED_STATE_DIR/home" \
  LANG="${LANG:-en_US.UTF-8}" \
  COWORKER_STATE_DIR="$OCCUPIED_STATE_DIR" \
  COWORKER_HAAS_DELEGATION_ENABLED=1 \
  COWORKER_HAAS_LOCAL_AUTOSTART=1 \
  COWORKER_HAAS_BACKEND_PREFERENCE=haas \
  COWORKER_HAAS_EXECUTION_MODE=local_api \
  COWORKER_HAAS_MODE=local_managed \
  COWORKER_HAAS_BASE_URL="http://127.0.0.1:$OCCUPIED_HAAS_PORT" \
  COWORKER_HAAS_REQUIRE_TRUSTED_WORKSPACE=0 \
  OPENAI_API_KEY="placeholder-openai-key" \
  "$SERVER" --cwd "$OCCUPIED_WORKSPACE_DIR" --host 127.0.0.1 --port "$OCCUPIED_MANAGER_PORT" \
  >"$OCCUPIED_MAIN_LOG" 2>&1 &
OCCUPIED_PID="$!"

wait_http "http://127.0.0.1:$OCCUPIED_MANAGER_PORT/v1/health" "$OCCUPIED_PID" "$OCCUPIED_MAIN_LOG"
OCCUPIED_TOKEN_FILE="$OCCUPIED_STATE_DIR/sidecar-$OCCUPIED_MANAGER_PORT.token"
for _ in $(seq 1 40); do
  [ -s "$OCCUPIED_TOKEN_FILE" ] && break
  sleep 0.1
done
if [ ! -s "$OCCUPIED_TOKEN_FILE" ]; then
  echo "packaged smoke: occupied-port sidecar token was not created" >&2
  tail -120 "$OCCUPIED_MAIN_LOG" >&2 || true
  exit 1
fi
OCCUPIED_TOKEN="$(cat "$OCCUPIED_TOKEN_FILE")"

"$PYTHON_BIN" - "$OCCUPIED_MANAGER_PORT" "$OCCUPIED_TOKEN" "$OCCUPIED_WORKSPACE_DIR" <<'PY'
import json
import sys
import time
import urllib.parse

from websockets.sync.client import connect

port, token, workspace = sys.argv[1], sys.argv[2], sys.argv[3]
uri = (
    f"ws://127.0.0.1:{port}/ws/session/packaged-smoke-occupied"
    f"?workspace={urllib.parse.quote(workspace)}&agent=cowork"
)
seen = []
with connect(uri, subprotocols=["openworker", token], open_timeout=5, close_timeout=1) as ws:
    ready = json.loads(ws.recv(timeout=5))
    if ready.get("type") != "ready":
        raise SystemExit(f"expected ready, got {ready!r}")
    ws.send(json.dumps({
        "type": "user_message",
        "text": "exercise occupied local HaaS port",
        "model": "openai:gpt-5.6-sol",
    }))
    deadline = time.time() + 10
    while time.time() < deadline:
        event = json.loads(ws.recv(timeout=max(0.1, deadline - time.time())))
        seen.append(event)
        if event.get("type") == "turn_done":
            break
    else:
        raise SystemExit(f"timed out waiting for occupied-port outcome; seen={seen!r}")

types = [event.get("type") for event in seen]
errors = [event for event in seen if event.get("type") == "error"]
if "turn_start" in types:
    raise SystemExit(f"occupied port unexpectedly started a turn; seen={seen!r}")
if "turn_done" not in types:
    raise SystemExit(f"occupied port did not produce turn_done; seen={seen!r}")
if not errors or "local_sidecar_port_occupied" not in str(errors[0].get("data", {})):
    raise SystemExit(f"occupied port did not report stable reason; seen={seen!r}")
PY

settings_json="$(curl -fsS -H "X-OpenWorker-Token: $OCCUPIED_TOKEN" \
  "http://127.0.0.1:$OCCUPIED_MANAGER_PORT/v1/settings/haas-delegation")"
"$PYTHON_BIN" - "$settings_json" <<'PY'
import json
import sys

payload = json.loads(sys.argv[1])
status = payload.get("local_status") or {}
if status.get("reason") != "local_sidecar_port_occupied":
    raise SystemExit(f"missing occupied-port diagnostic: {status!r}")
if not str(status.get("logPath") or "").endswith("logs/haas-sidecar.log"):
    raise SystemExit(f"missing sidecar log path: {status!r}")
if not str(status.get("managerLogPath") or "").endswith("logs/openworker-server.log"):
    raise SystemExit(f"missing manager log path: {status!r}")
if "api_token" in payload or "placeholder-openai-key" in json.dumps(payload):
    raise SystemExit("settings leaked api token material")
PY

if ! grep -F 'local_sidecar_port_occupied' "$OCCUPIED_MAIN_LOG" >/dev/null; then
  echo "packaged smoke: occupied-port reason missing from $OCCUPIED_MAIN_LOG" >&2
  tail -120 "$OCCUPIED_MAIN_LOG" >&2 || true
  exit 1
fi

kill "$OCCUPIED_PID" >/dev/null 2>&1 || true
wait "$OCCUPIED_PID" >/dev/null 2>&1 || true
OCCUPIED_PID=""
kill "$PORT_HOLDER_PID" >/dev/null 2>&1 || true
wait "$PORT_HOLDER_PID" >/dev/null 2>&1 || true
PORT_HOLDER_PID=""

for log in "$MAIN_LOG" "$HAAS_LOG"; do
  if [ -f "$log" ] && grep -E \
    'Task exception was never retrieved|unexpected keyword argument|local_haas_exited|\\[PYI-[0-9]+:ERROR\\]|Traceback \\(most recent call last\\)' \
    "$log" >/dev/null; then
    echo "packaged smoke: forbidden failure marker found in $log" >&2
    grep -nE \
      'Task exception was never retrieved|unexpected keyword argument|local_haas_exited|\\[PYI-[0-9]+:ERROR\\]|Traceback \\(most recent call last\\)' \
      "$log" >&2 || true
    exit 1
  fi
done

if [ -f "$OCCUPIED_MAIN_LOG" ] && grep -E \
  'Task exception was never retrieved|unexpected keyword argument|local_haas_exited|\\[PYI-[0-9]+:ERROR\\]|Traceback \\(most recent call last\\)' \
  "$OCCUPIED_MAIN_LOG" >/dev/null; then
  echo "packaged smoke: forbidden failure marker found in $OCCUPIED_MAIN_LOG" >&2
  grep -nE \
    'Task exception was never retrieved|unexpected keyword argument|local_haas_exited|\\[PYI-[0-9]+:ERROR\\]|Traceback \\(most recent call last\\)' \
    "$OCCUPIED_MAIN_LOG" >&2 || true
  exit 1
fi

echo "packaged smoke: PASSED app=$APP_BUNDLE manager_port=$MANAGER_PORT haas_port=$HAAS_PORT occupied_haas_port=$OCCUPIED_HAAS_PORT"
