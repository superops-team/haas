---
name: code-automation
description: HaaS changed-code verification guidance. Use after modifying Python, React/TypeScript, scripts, packaging, tests, or specs to choose focused unit tests, type checks, build checks, pre-commit, and full-check evidence. This skill selects verification commands; it does not replace UI/browser evidence or code review.
---

# HaaS Code Automation

## Authority

- Root `AGENTS.md` is authoritative.
- This skill chooses code verification for changed files. It does not create specs, run browser evidence, or decide delivery status by itself.
- Do not treat lint/build/pre-commit as selected unit tests; report each check under its own purpose.

## Inputs

Use:

- `git diff --name-only` / `git status --short`;
- changed file content and nearby tests;
- related component spec acceptance cases;
- existing Makefile/npm/uv scripts.

## Verification Selection

### Python / HaaS core

- Focused tests first: `uv run --extra dev pytest <test files or -k expression> -q`.
- Type check when public Python types, APIs, adapters, stores, or runtime code change: `uv run --extra dev mypy haas`.
- Run `make full-check` for cross-component, protocol, release, or final merge readiness.

### Manager GUI React/TypeScript

From `manager/surfaces/gui`:

- Focused Vitest for changed component/helper behavior:
  `npm test -- --run <test files>`.
- Full GUI unit suite when shared GUI behavior changes:
  `npm test -- --run`.
- Build/typecheck/bundle proof:
  `npm run build`.
- Production browser/runtime proof belongs to `ui-automation`, not this skill.

### Packaging / Desktop

- For packaging script or Tauri bundle changes, run the relevant build/smoke:
  `COWORKER_CODEX_BIN=<installed codex> OCW_SKIP_NOTARIZE=1 ./manager/packaging/build_dmg.sh`
  and/or `./manager/packaging/smoke_packaged_app.sh <OpenHarness.app>`.
- Close the installed OpenHarness app before package smoke when needed.

### Specs / Docs

- For docs-only specs, run `git diff --check`.
- If specs change behavior contracts, also run the tests tied to their acceptance cases.

### Final Gates

- Always run `make pre-commit` before commit.
- Run `make full-check` for cross-component changes, release readiness, high-risk runtime work, or when the user asks for a full local gate.
- Docker build/smoke is required only for container changes or explicit release validation; image push requires user confirmation.

## Unit Test Asset Plan

Before adding or modifying tests, state:

```text
Changed behavior:
Stable observable contract:
Regression this test catches:
Test asset action: add | modify | reuse | not_applicable
Reason:
```

Good HaaS tests assert stable behavior: protocol fields, state transitions, request counts, artifact safety, session recovery, visible UI outcomes, or packaging health.

Avoid tests that only assert private implementation shape, CSS class names without behavior, mock call order unrelated to the behavior, or static constants with no decision logic.

## Result Format

```text
Changed files: <paths>
Focused checks:
- <command>: passed|failed|not_run, reason
Broader gates:
- <command>: passed|failed|not_run, reason
Residual risk: <none or explicit>
```
