---
name: requirement-spec
description: Create or update HaaS implementation-ready component specs under specs/. Use when turning a product request, bug report, design note, screenshot, runtime observation, or packaging requirement into durable HaaS EN/ZH specs with requirements, boundaries, testing plan, acceptance cases, and component impact analysis.
---

# HaaS Requirement Spec

## Authority

- Follow root `AGENTS.md` first.
- Write durable contracts only under `specs/`.
- Do not create `prd-spec/`, `docs/verification/`, ledger files, sidecar metadata, or temporary reports.
- HaaS repo expects English and Chinese spec pairs when user-facing or process-important docs are added or materially changed.

## Inputs

Use any concrete source the user provides:

- product request or bug report;
- existing `specs/<component>/README.md`;
- runtime logs, local Web/desktop observations, Playwright output, screenshots, or build output;
- existing implementation in `haas/`, `manager/`, tests, Docker, or packaging scripts.

Prefer current `master/main` implementation and live local behavior over stale docs when aligning documentation.

## Spec Shape

Create or update `specs/<component>/README.md` and, when applicable, `README.zh-CN.md`.

Each spec should include:

1. component role and ownership;
2. background and evidence;
3. goals, non-goals, and user/system scenarios;
4. functional requirements ordered P0/P1/P2;
5. API/event/state/data/security impact;
6. failure, recovery, compatibility, rollback;
7. test plan and executable acceptance cases;
8. task breakdown and implementation sequence;
9. component impact analysis;
10. unresolved risks or explicitly deferred work.

Keep requirements testable. Avoid vague acceptance such as "works normally"; specify commands, browser paths, request counts, state transitions, or expected events.

## HaaS-Specific Boundaries

- Northbound compatibility surfaces are ADK REST/SSE and `/v1/haas/*`.
- Adapter-native details must remain inside the relevant harness adapter.
- Secretless is a hard boundary: specs must mention redaction/logging implications when touching credentials, prompts, URLs, artifacts, events, metrics, or test reports.
- Runtime and Docker changes must preserve the Lite/AIO platform contract.
- GUI specs must distinguish browser production preview, packaged desktop, and hermetic fixture evidence.
- Temporary verification scripts are allowed locally but must be deleted before delivery.

## Writing Workflow

1. Read the nearest existing component specs and `specs/README*.md`.
2. Choose a stable component directory name.
3. Draft or update EN first, then mirror the Chinese file.
4. Add executable acceptance cases with IDs.
5. Add task-to-case mapping when implementation will follow.
6. Add the component to `specs/README.md` and `specs/README.zh-CN.md` if new.
7. Run `git diff --check`; for docs-only changes also check links/paths manually.

## Review Checklist

- Does every P0/P1 requirement have a runnable verification path?
- Are non-goals explicit enough to prevent scope creep?
- Are public API/event/state compatibility decisions explicit?
- Are secretless and artifact safety implications covered?
- Are skipped or future checks clearly marked as `not_run`, `blocked`, or deferred with reason?
- Does the Chinese spec match the English contract, not merely summarize it?

## Output

Report:

```text
Spec files: <paths>
Key requirements: <P0/P1 summary>
Compatibility: <ADK/HaaS native/session/artifact/security impact>
Acceptance cases: <IDs and commands or browser paths>
Open risks: <none or explicit list>
```
