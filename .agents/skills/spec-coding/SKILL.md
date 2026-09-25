---
name: spec-coding
description: HaaS-local spec-driven development workflow. Use when a HaaS task needs a durable specs/ contract, implementation planning, verification evidence, code review, packaging, or release/readiness closeout. This skill adapts Spec Coding ideas to this repository and never uses external ledger, .agentnative, or prd-spec workflows.
---

# HaaS Spec Coding

## Authority

- `AGENTS.md` is the highest-priority project rule. This skill narrows workflow only for this repository.
- Long-lived contracts go under `specs/`. Do not create or commit `prd-spec/`, `docs/verification/`, ledger files, temporary reports, or external Spec Coding metadata.
- Do not use `.agentnative`, `spec-coding` CLI, run ledgers, phase ledgers, or hidden current pointers in this repository.
- Record temporary verification evidence in the final answer, commit message, PR text, or an existing long-lived spec section.

## Scope

Use this skill for non-trivial HaaS feature work, refactors, bug fixes, GUI/runtime changes, packaging changes, and completion audits that need traceability from requirement to verification.

Skip it for small read-only questions, simple command output, pure status checks, or clearly local one-line fixes where `AGENTS.md` allows direct repair.

## Workflow

1. **Locate the contract**
   - Read the relevant `specs/<component>/README.md` before implementation.
   - If no component contract exists, create or update one using `requirement-spec`.
   - For GUI work, check `specs/manager-gui-performance/`, `specs/manager-haas-sidecar-backend/`, and product identity specs when relevant.

2. **Review the spec**
   - Run an explicit spec review before runtime edits.
   - Block implementation on P0/P1 gaps: unclear trigger, state machine ambiguity, missing error/recovery behavior, secretless risk, missing acceptance test, or public compatibility uncertainty.
   - Keep the review result in final/PR evidence, not a temporary checked-in report.

3. **Plan implementation**
   - Map each change to a spec requirement or task.
   - State impacted components and why unaffected protocol surfaces remain unchanged.
   - Prefer small slices with independent verification: spec, failing test, implementation, focused test, then broader gates.

4. **Develop**
   - Follow TDD for behavior or regression fixes.
   - Use `react-typescript-kit` for GUI/TypeScript implementation.
   - Use `code-automation` for selected unit tests and typecheck selection.
   - Use `ui-automation` or repository Playwright fixtures for real browser evidence.
   - Preserve user changes in the worktree; never revert unrelated files.

5. **Verify**
   - Start with the smallest relevant tests, then escalate by risk.
   - Typical GUI path: focused Vitest -> `make gui-check` -> `make gui-preview-smoke` when production-bundle browser evidence is needed -> `make pre-commit`.
   - Cross-component/final path: `make full-check`; it includes GUI unit/build evidence through `make gui-check`.
   - Packaging path: close running installed app, run `manager/packaging/build_dmg.sh`, then `manager/packaging/smoke_packaged_app.sh` against the built or installed app.

6. **Review and close**
   - Run `code-review`, `brooks-review`, and `brooks-test` for substantive changes.
   - Findings must be `fixed`, `false_positive`, or explicitly recorded residual risk.
   - Final closeout must include: changed specs, compatibility impact, verification commands/results, skipped checks and reason, security/secretless conclusion, and commit/push status when requested.

## HaaS Defaults

- Public protocol changes must be additive unless a spec explicitly defines versioning, migration, retirement, and rollback.
- Codex app-server is an adapter detail; do not leak native events/protocols to northbound APIs.
- Events are facts, not rendering instructions.
- Secretless boundaries apply to logs, metrics, events, artifacts, test reports, and final summaries.
- Container/image changes require Docker build/smoke evidence; image push requires explicit user confirmation.

## Output

Keep progress concise and evidence-based:

```text
Spec: <component specs changed or reused>
Implementation: <files / behavior>
Verification: <commands and results>
Review: <findings fixed / false positive / residual risk>
Delivery: <commit, push, packaging or install status>
```
