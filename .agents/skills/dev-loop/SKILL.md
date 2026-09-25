---
name: dev-loop
description: Orchestrate the complete HaaS development lifecycle from an approved component spec through TDD, implementation, required reviews, verification, and delivery evidence. Use when the user explicitly asks to complete a substantial HaaS change end to end; use spec-coding alone for ordinary contract traceability or partial phases.
---

# HaaS Development Loop

## Authority and Scope

- Follow root `AGENTS.md` before this workflow.
- Use this skill only to coordinate the complete lifecycle. Delegate spec details to
  `requirement-spec` and `spec-coding`, spec quality to `review-spec`, verification
  selection to `code-automation`, and the mandatory review gates to their named skills.
- Preserve unrelated worktree changes. Do not create commits, push branches, publish
  images, or mutate external systems unless the user requested that delivery action.
- Do not create `prd-spec/`, `docs/verification/`, external ledgers, or temporary reports
  in the repository.

## Phase 0: Establish Evidence and Scope

1. Define a stable change ID.
2. Inspect `git status --short` and separate existing user changes from the task scope.
3. Read the relevant component specs and nearby repository instructions.
4. Record affected components and explain why apparently adjacent components are
   unchanged.
5. For a bugfix, reproduce the failure or trace the causal path with concrete logs,
   state, or a focused failing probe before writing the contract. If reproduction is
   impossible, label the diagnosis as a hypothesis and the task as blocked or explicitly
   risk-accepted; do not turn an unverified guess into a normative spec.

Do not edit production code before these inputs are understood.

## Phase 1: Specify and Review

1. Create or update the English and Chinese component specs with `requirement-spec`.
2. State goals, non-goals, interfaces, state/data/security impact, failure and recovery,
   compatibility, acceptance cases, and task order.
3. Run `review-spec` across context, boundaries, security, redaction, compatibility,
   observability, testability, and rollback.
4. Resolve every blocking finding before implementation. Record remaining non-blocking
   risks in the component spec or final delivery evidence.

## Phase 2: Plan the Smallest Complete Slices

Map each implementation task to a spec requirement and acceptance case. Prefer vertical
slices that produce a stable observable result and can be verified independently.

For each task, record:

- requirement and acceptance-case IDs;
- files or components likely to change;
- caller and downstream impact;
- failing test or contract test to add first;
- focused and broader verification commands.

P0/P1/P2 expresses implementation order, not permission to silently drop scope.

## Phase 3: Implement with TDD

For each behavioral change:

1. Add a focused failing test that demonstrates the missing behavior or regression.
2. Run it and preserve the red-state evidence.
3. Implement the smallest change that satisfies the contract.
4. Re-run the focused test and refactor under green.
5. Check the changed file's callers, downstream consumers, error paths, cleanup paths,
   and corresponding spec text.

For prose-only or metadata-only changes where executable TDD is not meaningful, use
structural validation and a before/after failing fixture instead of inventing a runtime
test.

## Phase 4: Verify by Risk

Use `code-automation` to select commands. Start focused, then escalate:

- Always run the acceptance cases tied to the changed behavior.
- For API, SSE, session, adapter, model proxy, MCP, skill, container, secretless, or
  recovery changes, collect unit, integration, and E2E/smoke evidence as required by
  root `AGENTS.md`; do not downgrade these to optional risk-selected checks.
- Always run `make pre-commit` before delivery.
- Run `make full-check` for cross-component, high-risk, final merge, or release work.
- Use UI/browser evidence for user-visible GUI behavior.
- Run Docker build/smoke only for container changes or explicit release validation.
- Never push an image without explicit user confirmation.

Report skipped checks as `not_run` with the reason and residual risk. A skipped check is
never a pass.

## Phase 5: Run Required Reviews

Execute the repository gates in this order after implementation and tests:

1. `code-review`: two rounds, fixing confirmed findings.
2. `brooks-review`: architecture and maintainability findings.
3. `brooks-test`: test-suite quality findings.

Pass the Phase 0 task-owned file/diff manifest explicitly to every review. Review relevant
off-diff callers when needed, but never modify unrelated user-owned files. If a required
review must sample because the changeset is too large, split it into exhaustive coherent
chunks; otherwise report the gate as partial/`not_run`, not passed.

Record every finding with location, severity, and one of `fixed`, `false_positive`, or
`accepted_risk`. Do not retain `accepted_risk` by default; when unavoidable, document it
in the component spec or final delivery evidence.

After any review fix, re-run the focused checks and every broader gate the fix may affect.

## Phase 6: Close Out Honestly

Deliver a concise evidence report containing:

- specs changed and acceptance cases satisfied;
- implementation files and behavior;
- ADK and HaaS native compatibility impact;
- security, redaction, event, artifact, and container conclusions;
- exact verification commands and results;
- `not_run` checks with reasons and residual risk;
- review findings and dispositions;
- commit, push, packaging, or release status only when requested.

Do not claim completion while a required gate is failing or a spec blocker remains.
