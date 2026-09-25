---
name: code-automation
description: HaaS changed-code verification guidance. Use when verifying modifications to Python, React/TypeScript, scripts, packaging, tests, or specs to choose focused unit tests, type checks, build checks, pre-commit, and full-check evidence. This skill selects verification commands; it does not replace UI/browser evidence or code review.
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

## Cross-Layer Data Flow Check

Trigger this check when the changed files span **3 or more HaaS layers**. The HaaS layers are: `api`, `protocol`, `harnesses`, `sessions`, `events`, `policy`, `model_proxy`, `mcp`, `artifacts`, `runtime`, `observability` (plus the GUI surface under `manager/surfaces/gui`).

Identify the touched layers first:

```bash
git diff --name-only HEAD
```

Then trace data across layers rather than validating each file in isolation:

- **Read flow** — storage / persistence → service → API → UI. Confirm the value that lands in the UI actually originates where you think, and that no layer silently drops, renames, or reinterprets a field.
- **Write flow** — UI → API → service → storage. Confirm a user action persists through every layer, including idempotency and error paths.
- **Type/schema continuity** — confirm the same type or schema passes between layers. A dict re-shapen in `api/` that no longer matches the `protocol/` model is a defect even if every file type-checks individually.
- **Error propagation** — confirm errors reach the caller as structured objects (status, error code, message), not swallowed exceptions or generic 500s. A layer that catches and returns `None` without propagating breaks recovery semantics.

If the change spans fewer than 3 layers, note that and skip the deep trace.

## Spec Sync Check

When a behavior contract changes, confirm the component contract does not drift from the implementation:

- If the change alters an interface, event, error code, state transition, header, session/response ID, or artifact URL, confirm the matching `specs/<component>/README.md` is updated. Root `AGENTS.md` rule "文档不能漂移" makes this mandatory: README, OpenAPI/schema, implementation, and tests must not tell four different stories.
- Compare the changed files against the relevant spec and confirm the spec's接口/事件/错误码/状态迁移 still match the code.
- When you discover a non-obvious pattern, edge case, or hard-won lesson during the change, ask whether the relevant spec should capture it — surface the question rather than silently absorbing it into code comments.
- This skill does not author specs (that is `requirement-spec` / `spec-coding`); it only verifies that existing specs stay in sync.

## Code Reuse Check

Before adding a new utility function, hook, or constant:

- Search for existing similar code first:

  ```bash
  grep -rn "pattern" haas/ manager/
  ```

- If the same value or helper is defined in 2 or more places, extract it to a shared module/constant instead of adding a third copy.
- After a batch change (renaming, moving, replacing a value), confirm every occurrence was updated: re-run the grep for the old pattern and confirm zero hits.
- Prefer using an existing component, hook, helper, or state shape before introducing a new abstraction (see also `react-typescript-kit` rule 3).
