---
name: parallel-code-review
description: Parallel multi-lens code review for HaaS changes using six HaaS-specific review lenses. Use when reviewing a PR or changeset that touches API/SSE contracts, session/adapter/runtime code, Tauri desktop packaging, secretless paths, recovery semantics, or tests. Triggers on "code review", "review this change", "PR review", "review lenses", or before the brooks-review/brooks-test gates. Accelerates the first code-review round by running independent lenses in parallel; does not replace brooks-review (architecture) or brooks-test (test quality).
---

# Parallel Code Review

Run independent, read-only review passes over a changeset, each through a single HaaS-specific lens, then dedupe and verify findings. This skill accelerates the **first** code-review round. It does **not** replace the existing review gate: two rounds of code-review → brooks-review (architecture/maintainability) → brooks-test (test quality) still stand.

## Authority

- Root `AGENTS.md` is authoritative, especially the AI development iron rules, adapter isolation, secretless hard boundary, and compatibility commitments.
- This skill produces findings; the author dispositions each one. It does not merge, block, or change code on its own.

## The six HaaS review lenses

Run only the lenses relevant to the changed files. A Python-sidecar change does not need the cross-platform lens; a Tauri bundle change does not need recovery-semantics deep dives (unless it touches sidecar supervision).

### 1. `secretless`

The hard boundary. Inspect changed code, logs, events, tests, and artifacts for:

- Real API keys, provider credentials, Authorization header values, cookies, or session tokens.
- Presigned URLs, raw prompts, or full tool arguments in logs, events, metrics, or artifacts.
- Credentials passed as plaintext harness env/config instead of a secret store / vault / in-memory secret handle.
- Test fixtures that contain real-looking secrets (fixtures must use obvious placeholders; only `# haas-secret-ignore` inline markers may exempt single lines, and only after review).

Check both the new code and any log/error/event payloads it constructs. A secret that never lands in git but leaks into an SSE event is still a blocker.

### 2. `protocol-compatibility`

Northbound contract safety. For ADK (`/run_sse`, `/run`, session CRUD) and `/v1/haas/*`:

- Are new fields additive only? No changed/renamed/removed field, event name, error code, header, session/response ID, or artifact URL.
- If a breaking change is truly needed, is there a new version, migration plan, old-entry retirement condition, and rollback path? Flag it as a blocker until that exists.
- Does `POST /run` (collected JSON) stay parity-consistent with `POST /run_sse` (streamed aggregation)?
- Do mutating APIs honor `Idempotency-Key` dedupe?

### 3. `adapter-isolation`

Harness-native protocol leakage.

- Codex app-server JSON-RPC, Pi JSONL, OpenCode JSON, or AMP protocol details must not appear in northbound API schemas, SSE event payloads, or error messages.
- Adapter output must be canonical HaaS events/responses, not raw harness events.
- Does the adapter bypass the policy controller on tool, network, filesystem, or approval grants? If a harness only supports instruction-level tool disabling, is the catalog honestly marked `enforcement=advisory` rather than claiming hard block?

### 4. `recovery-semantics`

Failure must be recoverable or explainable. For changed paths involving timeout, cancel, adapter crash, SSE disconnect, event-log write failure, model/MCP provider failure, sandbox restart, or process exit:

- Is there an explicit state, error code, and documented recovery action?
- Is there test evidence for the failure path?
- Can the caller distinguish a recoverable transient from a permanent failure from the structured error (not from parsing human text)?

### 5. `cross-platform`

Desktop shell concerns for Tauri changes:

- macOS entitlements / notarization, Windows WebView2 behavior, Linux quirks.
- Sidecar binary path resolution across platforms (do not hardcode POSIX paths; handle `.exe` on Windows, app-bundle paths on macOS).
- Single-instance, tray, autostart, updater, and notification behavior parity.
- Docker/lite vs AIO variant port and runtime assumptions if the change touches container packaging.

### 6. `tests`

Evidence quality.

- Does new behavior have unit + integration evidence (or an explicit `not_run` with reason)?
- Does a bug fix include a regression test that would have caught it?
- Are acceptance cases in the relevant `specs/<component>/README.md` actually covered by tests?
- Do tests avoid asserting private implementation shape, brittle CSS, or mock call order unrelated to behavior?

## Workflow

1. **Scope**: identify the changed files (`git diff --name-only`) and pick the relevant lenses. State which lenses run and which are skipped (and why).
2. **Parallel read-only passes**: run each selected lens independently. Each pass only reads code and reports findings; it does not edit.
3. **Collect findings**: gather raw findings from all passes.
4. **Dedupe**: the same issue spotted by two lenses (e.g. a leaked token flagged by both `secretless` and `recovery-semantics`) becomes one finding with both lens tags.
5. **Verify**: re-check each finding against the actual code before reporting. Drop anything you cannot cite to a file:line.
6. **Report**: produce the findings list in the format below. The author then fixes, marks false-positive, or accepts risk.

## Findings format

Each finding:

```text
- [lens] file/path.ts:123 — <one-sentence problem>
  Severity: blocker | major | minor | nit
  Disposition: open
```

After author action, disposition becomes one of:

- `fixed` — change applied.
- `false_positive` — explain why it is not actually a problem.
- `accepted_risk` — must be recorded in the relevant `specs/<component>/README.md` or the final delivery note. Default is to fix; `accepted_risk` is the exception, not the default.

## Relationship to the existing gate

- This skill feeds the **first** of the two required code-review rounds. Running the six lenses in parallel replaces what would otherwise be a single monolithic read.
- It does **not** run brooks-review (architecture and maintainability) or brooks-test (test quality). Those still run as separate gates after code-review.
- It does not decide delivery. Final准出 still requires `make pre-commit` / `make secret-scan` and, where applicable, `make full-check`.

## Quick lens selection

| Changed area | Lenses |
|---|---|
| `haas/api/`, SSE, protocol schemas | protocol-compatibility, recovery-semantics, tests |
| `haas/harnesses/` | adapter-isolation, secretless, recovery-semantics, tests |
| `haas/model_proxy/`, `policy/` | secretless, adapter-isolation, recovery-semantics, tests |
| `src-tauri/`, packaging, bundle | cross-platform, secretless, tests |
| `manager/surfaces/gui/` | cross-platform (IPC parity), tests |
| Any change with secrets/logging/events | secretless |

## Source

Orchestration pattern adapted from [Logseq `logseq-review-workflow`](https://github.com/logseq/logseq/tree/main/.agents/skills/logseq-review-workflow); six-lens parallel structure adapted from [Bruno `code-review`](https://github.com/usebruno/bruno/tree/main/.claude/skills/code-review). Lenses are replaced with HaaS-specific concerns (secretless / protocol-compatibility / adapter-isolation / recovery-semantics / cross-platform / tests). No external code retained.
