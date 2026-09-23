---
name: react-typescript-kit
description: HaaS React/Vite/TypeScript implementation guidance for OpenHarness Manager GUI, TypeScript tooling, route splitting, request ownership, component state, browser/Tauri parity, and packaging-facing frontend code. Use when editing manager/surfaces/gui or related TS/JS tooling; not for read-only review or runtime-only E2E observation.
---

# HaaS React TypeScript Kit

## Authority

- Root `AGENTS.md` remains authoritative.
- This skill guides implementation only. It does not replace `requirement-spec`, `code-automation`, `ui-automation`, `code-review`, or repository gates.
- Do not introduce Arco, VeDesign, Starling, ByteIO, or source-repo-specific dependencies unless the HaaS codebase already uses them and the spec explicitly asks for them.

## Local Stack

- Main GUI: `manager/surfaces/gui`
- Framework: React 18 + Vite + TypeScript + Tauri 2 shell
- Tests: Vitest and Playwright
- Styling: existing CSS/Tailwind-style classes and local components, not a new design system
- Runtime API: `src/api.ts` wrappers to the local Manager sidecar
- Desktop packaging consumes the same built `dist/` assets as browser preview

## Implementation Rules

1. Read nearby components, tests, `package.json`, and relevant specs before editing.
2. Preserve browser/Tauri parity. Do not add desktop-only behavior unless the component spec requires it.
3. Use existing components, hooks, icons, API helpers, state shapes, and CSS classes before adding abstractions.
4. Keep route surfaces lazy when they are not needed for the session shell.
5. Keep heavy dependencies out of the initial synchronous graph when they are only needed after user action.
6. Give each local resource one owner: avoid duplicate polling intervals for the same endpoint and parameters.
7. Model async state explicitly: loading, empty, error, disabled, cancelled, stale response, and retry paths.
8. Clean up timers, subscriptions, observers, WebSocket listeners, and pending async work.
9. Use refs for transient high-frequency values when React state would rerender unrelated surfaces.
10. Keep comments sparse and only for non-obvious race, recovery, compatibility, or security reasons.

## GUI Performance Defaults

- Production preview/performance checks must run against a fresh `npm run build`.
- Use Vite manifest for bundle budget checks.
- For stream-heavy UI, isolate live state from static shell state.
- Background/hidden route surfaces should not keep ordinary polling alive unless correctness requires it.
- Do not treat static analysis candidates as facts until browser/runtime evidence confirms them.

## Text and I18n

- Follow the existing local i18n pattern. If surrounding code uses `t(...)`, add keys consistently.
- Do not introduce new localization tooling.
- For compact tool surfaces, keep copy short and container-safe.

## Verification Handoff

After implementation, hand changed files and behavior risks to `code-automation`.

For GUI-visible behavior, also identify the browser evidence path for `ui-automation` or repository Playwright:

```text
Changed files: <paths>
Behavior risks: <state/race/route/request/bundle risks>
Suggested focused tests: <Vitest files or Playwright specs>
Build impact: <entry bundle, dynamic chunks, Tauri/package considerations>
```

Do not claim runtime/UI evidence from this skill alone.
