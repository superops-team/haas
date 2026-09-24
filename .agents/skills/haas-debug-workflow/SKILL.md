---
name: haas-debug-workflow
description: Multi-runtime debugging for HaaS desktop + sidecar. Use when investigating a bug, reproduction, regression, or unexpected behavior that may span the React WebView, Tauri Rust shell, or Python sidecar. Triggers on "debug", "reproduce", "why does it", "broken", "crash", "hang", "stale data", "sidecar issue", "IPC", "cross-process", or any bug investigation in the HaaS repository. Enforces evidence-closed before/after verification across all three runtimes.
---

# HaaS Debug Workflow

Treat this as a debugging workflow, not a code-only change. Before claiming a fix, you must complete the evidence-closed sequence below.

## Authority

- Root `AGENTS.md` is authoritative, especially "失败必须可恢复或可解释" and secretless rules.
- This skill guides debugging workflow and evidence capture. It does not decide delivery status or replace code review.
- Do not include real credentials, tokens, presigned URLs, raw prompts, or full tool arguments in evidence. Redact before capturing.

## Step 1: Select the correct runtime and say why

HaaS runs three cooperating runtimes. A bug often spans IPC boundaries. Select the primary runtime based on where the symptom first appears, then verify adjacent runtimes.

| Runtime | When to select | Key surfaces |
|---|---|---|
| `webview` | UI renders wrong, stale, frozen, or unresponsive; WebSocket delivery issues; IPC call returns unexpected result; window focus/visibility behavior | React components, DOM, browser console, WebSocket stream, `src/tauri.ts` IPC wrappers, Tauri WebView devtools |
| `rust-shell` | Sidecar won't start / dies / restarts; tray icon missing; autostart broken; single-instance conflict; updater failure; notification not showing; STT engine issue; cross-platform path or binary handling | `src-tauri/src/lib.rs`, `src-tauri/src/main.rs`, `tauri.conf.json`, sidecar spawn/supervision code, Cargo.toml plugins |
| `python-sidecar` | HTTP/SSE endpoint returns error or wrong data; session state wrong; event log missing events; harness adapter fails; model provider error; MCP tool fails; policy denies unexpectedly; artifact read/write fails; persistence corruption | `haas/` package, `manager/coworker/server/`, FastAPI routes, SSE streams, sidecar log, pytest tests |

If the bug crosses two or more runtimes (most non-trivial bugs do), select the runtime where the **root cause** most likely lives, but explicitly list which other runtimes must be verified.

## Step 2: Reproduce before editing

Do not edit code until you have reproduced the bug and captured evidence from the selected runtime.

### Webview reproduction

- Run the GUI: `cd manager/surfaces/gui && npm run dev` (browser) or `npm run tauri dev` (desktop).
- Open browser devtools (browser mode) or Tauri devtools (desktop mode: right-click → Inspect).
- Capture: console errors, network requests (especially `/v1/*` and WebSocket frames), React component state via devtools, screenshot of the wrong UI.
- For IPC issues, add a temporary `console.log` in `src/tauri.ts` wrapper to capture the invoke call and result, then remove it after.

### Rust shell reproduction

- Run with console output: `cd manager/surfaces/gui && npm run tauri dev` — Rust `println!` and sidecar stdout appear in the terminal.
- Check the sidecar log file (see Step 3 for path).
- For sidecar startup failures, run the sidecar directly: `./.venv/bin/openworker-server --port 8092` and observe stdout/stderr.
- For cross-platform issues, note the OS (macOS / Windows / Linux) and architecture (arm64 / amd64).

### Python sidecar reproduction

- Start the server: `./.venv/bin/openworker-server --port 8092` (from repo root).
- Reproduce via `curl` for HTTP endpoints, or via the GUI for SSE streams.
- Capture: HTTP status code, response body (redacted), server log output, stack trace if any.
- For SSE issues, use `curl -N http://127.0.0.1:8092/<sse-endpoint>` and observe the event stream.
- Run the relevant pytest: `uv run --extra dev pytest tests/<test_file> -v` or `cd manager && uv run pytest tests/<test_file> -v`.

## Step 3: Check logs early

Logs are evidence. Check them before editing code.

| Runtime | Log locations |
|---|---|
| Python sidecar | `<state_dir>/logs/openworker-server.log` (current), `<state_dir>/logs/openworker-server.log.old` (previous rotation). `state_dir()` resolves to the app's per-user state directory. In dev, also check the terminal stdout/stderr of the `openworker-server` process. |
| Rust shell | Terminal output from `npm run tauri dev` (stdout/stderr of both Rust and the supervised sidecar). macOS: `~/Library/Logs/OpenHarness/` if present. No persistent Rust log file by default. |
| Webview | Browser console / Tauri devtools Console tab. Network tab for HTTP/SSE. React DevTools for component state and renders. |

To find `state_dir()` on macOS: `~/Library/Application Support/com.openharness.desktop/` or the path printed by the sidecar at startup. On Linux: `~/.local/share/com.openharness.desktop/`. On Windows: `%APPDATA%\com.openharness.desktop\`.

When reading logs, search for: ERROR, Exception, Traceback, panic, failed, crash, timeout, disconnected, 500, 4xx. Do not paste log lines containing credentials or raw prompts into evidence — redact them.

## Step 4: Apply the smallest justified fix

- Identify the root cause with file and line reference.
- Apply the minimal change that addresses the root cause. Do not refactor unrelated code in the same change.
- If the fix touches a public API, event, error code, or config field, check `specs/haas-protocol/` and the relevant component spec for compatibility constraints. Additive changes only unless a version bump is explicitly planned.
- If the fix touches harness adapter code, verify no harness-native protocol leaks to the northbound API (AGENTS.md adapter isolation rule).

## Step 5: Re-run the same reproduction and capture post-fix evidence

- Run the exact same reproduction steps from Step 2.
- Capture new evidence showing the bug is fixed.
- If the bug involved settings, startup, persistence, window creation, sidecar restart, or cross-process behavior, include a restart/reload/reopen verification:
  - Restart the sidecar and verify state persists / recovers correctly.
  - Reload the WebView and verify the UI state is correct.
  - Reopen the desktop app and verify startup behavior.
- Run the relevant tests to confirm no regression: `uv run --extra dev pytest <affected_tests> -q` for Python; `cd manager/surfaces/gui && npm test -- --run <affected_tests>` for GUI.

## Step 6: Report with required structure

Your final response must include these sections:

1. **Runtime chosen** — which runtime(s) and why
2. **Pre-fix reproduction** — exact steps and evidence (log lines, console output, test failure, screenshot description)
3. **Root cause** — concrete cause with file/line reference
4. **Fix applied** — short description of the change
5. **Post-fix verification** — same steps with new evidence
6. **Additional verification** — tests run, restart/reload/reopen checks, and what was NOT verified
7. **Gaps or blockers** — any missing evidence and why

## Do not conclude early

Do **not** say the bug is fixed if any of these is missing:

- Pre-fix reproduction evidence
- Relevant log evidence (or an explicit statement that checked logs had nothing useful)
- Post-fix evidence from the same runtime/path
- Required restart/reload/reopen verification (when applicable)

Unit tests alone are **not** enough when the bug spans runtimes or IPC boundaries, unless the bug is truly unit-level and you explicitly justify that.

If the environment blocks full verification, report: the blocker, what you tried, which evidence is still missing, and the strongest partial evidence you have.

## Quick checklist

Before ending, make sure the answer is yes to all:

- Did I reproduce the bug before fixing it?
- Did I show evidence, not just claim reproduction?
- Did I inspect relevant logs?
- Did I verify in the correct runtime(s)?
- Did I rerun the same scenario after the fix?
- Did I include both before and after evidence?
- Did I avoid claiming completion if required evidence is missing?
- Did I redact credentials, raw prompts, and full tool arguments from all evidence?
