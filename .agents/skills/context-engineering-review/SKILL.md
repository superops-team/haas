---
name: context-engineering-review
description: Review and improve HaaS repository context engineering: AGENTS.md, specs index, README, local .agents/skills, verification evidence flow, memory/summary usage, and agent onboarding. Use when asked to assess or optimize how coding agents find HaaS context, avoid drift, recover long tasks, or choose the right workflow.
---

# HaaS Context Engineering Review

## Purpose

Assess whether an agent can quickly and safely answer:

- What is HaaS?
- Which contracts govern this task?
- Which files and commands are authoritative?
- What evidence is needed before closeout?
- What must not be committed?

## Scope

For a full review, sample:

- root `AGENTS.md`;
- `README.md` / `README.zh-CN.md`;
- `specs/README.md` and relevant `specs/<component>/README*.md`;
- `.agents/skills/*/SKILL.md`;
- `Makefile`, `scripts/quality/*`, `manager/surfaces/gui/package.json`, and packaging scripts when relevant;
- recent commit messages or final reports when reviewing process evidence.

Do not read every spec by default. Select the ones relevant to the target task.

## Scoring

Score each dimension from 0-5 and convert to 100:

| Dimension | What To Check |
|---|---|
| Context Map | Root entry points and authority order are clear. |
| Domain Boundary | HaaS protocol, Manager, adapter, runtime, container, GUI responsibilities are separated. |
| Retrieval | Specs, skill names, file names, and indexes make relevant context easy to find. |
| Dynamic Context | Long tasks, status, verification state, package/install state, and blockers are easy to recover. |
| Compression | Final reports and specs preserve key evidence without requiring full chat replay. |
| Isolation | Skills and docs do not import unrelated source-repo, ByteDance-only, or stale workflows into HaaS tasks. |
| Evidence Fitness | Test/build/E2E/package evidence is reproducible, content-safe, and tied to acceptance cases. |
| Operability | Commands are concrete, local, failure-aware, and cleanup-safe. |

## Review Method

1. Identify the target agent workflow: specs, GUI implementation, sidecar/runtime, Docker, packaging, docs, or release.
2. Read only the context needed for that workflow.
3. List evidence for each score; do not score from memory alone.
4. Prefer deletion or narrowing when context is stale or unrelated.
5. Recommend exact file edits, section moves, indexes, examples, or command snippets.

## HaaS Smells

- Skills mention `.agentnative`, external ledgers, `prd-spec/`, `docs/verification/`, Starling, TEA/ByteIO, or private platform flows without saying they are not HaaS defaults.
- Specs and implementation disagree, and the source of truth is unclear.
- Verification claims exist only in chat and are not reproducible by command.
- Temporary Playwright/performance scripts are committed.
- GUI performance checks run against stale `dist/`.
- Packaging smoke ignores existing installed OpenHarness single-instance behavior.
- Secret-bearing data appears in examples, logs, reports, or final summaries.

## Output

Use this format:

```text
Score: <0-100>
Conclusion: <one sentence>

Dimension scores:
- Context Map: <0-5>, evidence: <paths/sections>
- ...

Top recommendations:
1. <file/section>: <exact action>. Benefit: <why>.
2. ...

Risks/tradeoffs:
- <anything that might remove useful context or require user decision>
```
