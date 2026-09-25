# Agent Skills Component Specification

**English** | [简体中文](README.zh-CN.md)

Status: Implemented; review and pre-commit gates passed
Last reviewed: 2026-09-24
Change ID: agent-skills-tauri-python-adoption
Related specs: [Manager GUI Performance](../manager-gui-performance/README.md), [Manager HaaS Sidecar Backend](../manager-haas-sidecar-backend/README.md), [Context Engineering Review](../../.agents/skills/context-engineering-review/SKILL.md)

## 1. Component Role

Agent Skills owns the repository-local `.agents/skills/` directory: the modular, self-contained knowledge packages that transform a general coding agent into a HaaS-specialized one. It covers skill inventory, quality standards, provenance tracking, multi-tool compatibility, and the adoption of high-value skills adapted from top open-source projects.

This spec covers two adoption waves:

1. **Tauri/Rust desktop wave**: skills for multi-runtime debugging, React rendering performance in a Tauri WebView, parallel code review with HaaS-specific lenses, and cross-layer quality check enhancement.
2. **Python/FastAPI backend wave**: skills for FastAPI API/SSE contracts, async/concurrency, sidecar lifecycle, structured logging, pytest contract testing, type checking, and secretless patterns. Section 5.4 records the completed research and adopted skills.

This component does not change runtime code, protocol schemas, or build configuration. It is a developer-tooling contract.

## 2. Sources and Rationale

### 2.1 Existing skill inventory (pre-adoption)

The repository ships 11 skills under `.agents/skills/`:

| Skill | Domain | Provenance |
|---|---|---|
| `agent-browser` | Browser automation | Vendored from ZCode (Apache-2.0, derived from vercel-labs/agent-browser) |
| `beads` | Project task tracking | Local (gitignored) |
| `code-automation` | Changed-code verification | HaaS-adapted |
| `context-engineering-review` | Context quality audit | HaaS-adapted |
| `dogfood` | Exploratory web testing | Vendored from ZCode (Apache-2.0) |
| `fallow` | TS/JS codebase intelligence | Vendored from fallow-rs (MIT) |
| `react-best-practices` | React performance rules | Vendored from ZCode (MIT, Vercel Engineering) |
| `react-typescript-kit` | HaaS GUI implementation guide | HaaS-adapted |
| `requirement-spec` | Spec authoring | HaaS-adapted |
| `spec-coding` | Spec-driven workflow | HaaS-adapted |
| `ui-automation` | GUI/browser verification | HaaS-adapted |

### 2.2 Identified gaps

The existing inventory is strong in React/TypeScript, spec workflow, and code verification, but has three material gaps:

1. **No multi-runtime debugging skill**. HaaS runs three cooperating runtimes (React WebView / Tauri Rust shell / Python sidecar). A bug often spans IPC boundaries, and no skill guides runtime selection, evidence capture, or cross-runtime reproduction.
2. **No Tauri WebView rendering performance skill at the component level**. The `manager-gui-performance` spec covers architecture-level optimization (polling deduplication, route splitting, state boundaries), but lacks component-level diagnosis methods (identity stability, Context slicing, Profiler verification). `react-best-practices` is generic and does not address Tauri-specific constraints.
3. **No Python backend skill**. The HaaS sidecar is a Python HTTP/SSE service, but no skill covers FastAPI SSE contracts, async/concurrency patterns, sidecar lifecycle, structured logging, pytest contract testing, or secretless redaction on the Python side.

### 2.3 External research baseline (Tauri/Rust wave)

Research verified 17 projects (9 confirmed Tauri+Rust by actual `tauri.conf.json` / `Cargo.toml`). Only two Tauri projects contain `.agents/skills`:

| Project | Stars | Skills | Key transferable value |
|---|---:|---|---|
| [GitButler](https://github.com/gitbutlerapp/gitbutler) | 21.7k | 5 | `lite-render-perf`: React Compiler memoization analysis, Context selector discipline, CDP verification. `but-performance-tests`: Hyperfine shell performance scenario pattern. |
| [EcoPaste](https://github.com/EcoPasteHub/EcoPaste) | 7.4k | 13 | `trellis-check`: cross-layer data flow verification, code reuse check, spec sync. Trellis session lifecycle workflow. |

[Logseq](https://github.com/logseq/logseq) (45k stars, Electron — not Tauri) contains 11 skills. Its `logseq-debug-workflow` (multi-runtime evidence-closed debugging) and `logseq-review-workflow` (parallel multi-lens review) are technology-agnostic and directly transferable. [Bruno](https://github.com/usebruno/bruno) (47k stars, Electron) provides a 6-lens parallel code review pattern in `.claude/skills`.

High-star Tauri projects without `.agents/skills`: Spacedrive (39k), Cap (22.7k), Pake (61.7k), cc-switch (133.8k), Clash Verge Rev (~147k). The `.agents/skills` convention has low adoption in the Tauri ecosystem.

### 2.4 External research baseline (Python/FastAPI wave)

Research verified 16 top Python/FastAPI projects via `gh api`. Only 5 contain `.agents/skills`, all concentrated in the tiangolo/pydantic ecosystem:

| Project | Stars | Skills | Key transferable value |
|---|---:|---|---|
| [FastAPI](https://github.com/fastapi/fastapi) | 102.6k | 1 skill + 6 references | `references/streaming.md`: `EventSourceResponse` + `ServerSentEvent` SSE pattern (directly for `POST /run_sse`); `responses.md`: response_model sensitive-field filtering (secretless); Annotated dependency injection; async/sync rules |
| [Pydantic](https://github.com/pydantic/pydantic) | 28.9k | 1 skill | Field() metadata classification, Annotated patterns, union metadata position traps — for HaaS protocol/ADK Event schema |
| [Pydantic AI](https://github.com/pydantic/pydantic-ai) | 20.1k | 6 dev-workflow skills | `adding-a-provider-api-feature`: "find existing abstraction first" adapter extension methodology; `complete-partial-pr`: streaming/non-streaming, sync/async completeness checklist |
| [SQLModel](https://github.com/fastapi/sqlmodel) | 18.3k | 1 skill | table=True vs non-table schema separation — low-medium value (HaaS DB not yet specified) |
| [Full Stack FastAPI Template](https://github.com/fastapi/full-stack-fastapi-template) | 45.7k | symlinks + library-skills | Wheel-packaged skills pattern (symlink from `.venv`) — design reference only |

Projects without `.agents/skills`: Django (91k), Flask (74.8k), uv (90.1k, has `.codex/skills`), Celery (28.9k), Starlette (12.6k), Uvicorn (11k), HTTPX (15.5k), Typer (20k), FastAPI Best Practices (18.1k), FastAPI Users (6.2k), SQLAlchemy (12.2k). The `.agents/skills` convention has very low adoption in the Python backend ecosystem outside the tiangolo ecosystem.

HaaS gaps not covered by any open-source skill: sidecar lifecycle management, async session/state machines, structured redacted logging, pytest contract/integration/E2E, secretless credential handling, error recovery matrices, Docker multi-arch builds, SSE reconnect/replay. These remain HaaS-original work.

## 3. Upstream and Downstream Relationships

```text
Root AGENTS.md (authoritative)
  -> specs/agent-skills (this component: inventory + quality contract)
  -> .agents/skills/* (skill implementations)
  -> .agents/skills/SOURCES.md (provenance tracking)
  -> .claude/skills -> ../.agents/skills (multi-tool compatibility symlink)
  -> coding agent runtime (loads skills by frontmatter trigger)
```

- Upstream: Root `AGENTS.md` AI development rules, `specs/README.md` component index, existing skill inventory.
- Downstream: All coding agents operating in this repository; `code-automation`, `spec-coding`, `ui-automation`, `react-typescript-kit` skills that may reference the new skills.
- Build boundary: Skills are Markdown + optional scripts/references. They do not affect the Python, Rust, or TypeScript build graphs. A skill may contain executable scripts, but those are invoked by the agent, not by the application build.

## 4. Goals, Non-goals, and User Scenarios

### 4.1 Goals

1. Add a multi-runtime debugging skill covering WebView / Rust shell / Python sidecar with evidence-closed before/after verification.
2. Add a Tauri WebView React rendering performance skill adapted to HaaS's local state model (no React Compiler dependency), covering identity stability, Context boundaries, and Profiler verification.
3. Enhance `code-automation` with cross-layer data flow verification and spec sync checks, without duplicating existing verification guidance.
4. Add a parallel code review skill with HaaS-specific lenses (secretless, protocol compatibility, adapter isolation, recovery semantics, cross-platform), aligned with the existing two-round code-review + brooks-review + brooks-test gate.
5. Add `.claude/skills` symlink to `../.agents/skills` for multi-tool compatibility, confirmed safe against `.gitignore` and working tree state.
6. Update `SOURCES.md` with provenance for all newly adopted or adapted skills.
7. Python/FastAPI wave: adopt high-value skills identified by research, deduplicated against existing and Tauri/Rust-wave skills.

### 4.2 Non-goals

- No runtime code changes (Python, Rust, TypeScript). This spec covers developer tooling only.
- No introduction of React Compiler. The rendering performance skill must work with HaaS's current React 18 + local state model.
- No Rust CLI performance test skill or Rust CLI pattern skill in this wave. These are P2 future items for when the Rust side (`openharness-desktop`) grows beyond `lib.rs` + `main.rs`.
- No creation of `prd-spec/`, `docs/verification/`, or temporary verification reports.
- No modification to user's uncommitted changes in existing skills (`code-automation`, `spec-coding`, `ui-automation`). Enhancements are additive only.
- No skill for TUI testing, Logseq-specific REPL, or other technology-specific patterns not present in HaaS.

### 4.3 User Scenarios

- Given a bug where the GUI shows stale data after a sidecar restart, the agent loads `haas-debug-workflow`, selects the Python sidecar runtime, reproduces with log evidence, applies a fix, and verifies across all three runtimes.
- Given a live transcript that causes the sidebar to re-render on every token delta, the agent loads `tauri-react-render-perf`, verifies identity stability with React Profiler, and applies a narrow state boundary.
- Given a change that touches `haas/api/`, `haas/protocol/`, and `haas/harnesses/`, the agent's `code-automation` enhanced check verifies cross-layer data flow and confirms spec sync.
- Given a PR for a Tauri bundle change, the agent loads `parallel-code-review`, runs secretless, protocol-compatibility, and cross-platform lenses in parallel, and produces a deduplicated findings report.

## 5. Responsibility Boundaries and Functional Requirements

### 5.1 P0: haas-debug-workflow

**Scope**: Multi-runtime debugging for HaaS desktop + sidecar.

- The skill must define three runtimes and their selection criteria:
  - `webview`: React rendering, DOM, UI state, WebSocket delivery, Tauri WebView-specific behavior (window focus, document visibility, IPC call results).
  - `rust-shell`: Tauri Rust shell, sidecar process supervision, tray icon, autostart, single-instance, updater, notification, STT engine, cross-platform IPC.
  - `python-sidecar`: HTTP/SSE API, session runtime, event log, harness adapter, model proxy, MCP, policy, artifact store, persistence.
- The skill must enforce an evidence-closed workflow: select runtime with justification → reproduce before editing → capture concrete evidence (logs, REPL output, failing test, CLI output) → apply smallest justified fix → re-run same reproduction → capture post-fix evidence → include restart/reload/reopen verification when the bug involves settings, startup, persistence, window creation, or cross-process behavior.
- The skill must list HaaS-specific log locations and inspection commands for each runtime.
- The skill must define a required output structure: runtime chosen, pre-fix reproduction, root cause, fix applied, post-fix verification, additional verification, gaps or blockers.
- The skill must explicitly state that unit tests alone are not sufficient when the bug spans runtimes or IPC boundaries.
- **Adaptation source**: [Logseq `logseq-debug-workflow`](https://github.com/logseq/logseq/tree/main/.agents/skills/logseq-debug-workflow), technology-agnostic pattern adapted to HaaS runtimes.

### 5.2 P0: tauri-react-render-perf

**Scope**: Component-level React rendering performance in the Tauri WebView, adapted to HaaS's local state model.

- The skill must cover three mechanisms without requiring React Compiler:
  1. **Identity stability**: derived values, context values, props, and effect dependencies must return stable identities when data is unchanged. Reducers must early-return on no-op updates. Hook result destructuring must avoid new identities.
  2. **Context boundaries**: `use(Context)` re-renders every consumer on identity change; there is no selector. Per-row or per-branch state must use narrow subscriptions (HaaS uses local state, not Redux — the skill must adapt to `useSyncExternalStore` or component-level state patterns, not Redux `useAppSelector`).
  3. **Profiler verification**: named React Profiler boundaries must verify that live token deltas do not re-render inactive branches (sidebar, composer, right rail). The skill must reference the `manager-gui-performance` spec P0-2 acceptance criteria.
- The skill must provide HaaS-specific verification commands: Vitest with Profiler counters, production Vite build + preview Playwright for render observation.
- The skill must explicitly state that React Compiler is not introduced in this wave; the identity-discipline and Context-boundary patterns are compiler-agnostic.
- The skill must not duplicate `react-best-practices` (generic Vercel rules) or `react-typescript-kit` (HaaS implementation guide). It focuses narrowly on rendering performance diagnosis in the Tauri WebView.
- **Adaptation source**: [GitButler `lite-render-perf`](https://github.com/gitbutlerapp/gitbutler/tree/main/.agents/skills/lite-render-perf), Redux/react-query examples replaced with HaaS local state patterns.

### 5.3 P1: code-automation enhancement (cross-layer + spec sync)

**Scope**: Additive enhancement to the existing `.agents/skills/code-automation/SKILL.md`. The existing content (verification selection by language, unit test asset plan, result format) is preserved unchanged.

- Add a **Cross-Layer Data Flow Check** section: when changed files span 3+ HaaS layers (api, protocol, harnesses, sessions, events, policy, model_proxy, mcp, artifacts, runtime, observability), verify that read flows (storage → service → API → UI) and write flows (UI → API → service → storage) trace correctly, types/schemas pass between layers, and errors propagate to the caller.
- Add a **Spec Sync Check** section: when behavior contracts change, confirm `specs/<component>/README.md` is updated. When a non-obvious pattern or lesson is discovered, ask whether the relevant spec should capture it. Reference AGENTS.md rule "文档不能漂移".
- Add a **Code Reuse Check**: before creating new utilities or constants, search for existing similar code. If 2+ places define the same value, extract to a shared constant.
- The enhancement must not duplicate `spec-coding` (which covers spec authoring workflow) or `context-engineering-review` (which audits context quality). It adds verification steps to the changed-code check.
- **Adaptation source**: [EcoPaste `trellis-check`](https://github.com/EcoPasteHub/EcoPaste/tree/main/.agents/skills/trellis-check), cross-layer and spec-sync sections adapted to HaaS layer names.

### 5.4 P0/P1: Python/FastAPI wave

Research identified three high-value skills with no overlap against existing or Tauri/Rust-wave skills.

#### 5.4.1 P0: fastapi-backend

**Scope**: FastAPI implementation guidance for the HaaS sidecar, with progressive disclosure (SKILL.md overview + `references/streaming.md` deep dive).

- SKILL.md must cover: `Annotated[..., Depends(...)]` dependency injection with type aliases, `yield` dependencies for resource cleanup, async/sync selection rules (default `def` runs in thread pool; blocking code must never run in `async def`; use Asyncer `asyncify`/`syncify` for mixed calls), `response_model` for automatic sensitive-field filtering, and router organization (prefix/tags/dependencies).
- `references/streaming.md` must cover: `EventSourceResponse` + `ServerSentEvent` pattern for `POST /run_sse`, `event`/`id`/`retry`/`comment` fields, `StreamingResponse` for bytes/JSON Lines, and alignment with the `haas-protocol` spec SSE conventions (`data:` frames, heartbeat comments, terminal event parity).
- The skill must reference HaaS-specific commands: `uv run --extra dev pytest tests/ -q`, `uv run --extra dev mypy haas`, `make test-integration`.
- Must not duplicate `code-automation` (verification command selection) or `haas-debug-workflow` (debugging workflow). It focuses on FastAPI implementation patterns.
- **Adaptation source**: [FastAPI official skill](https://github.com/fastapi/fastapi/tree/master/fastapi/.agents/skills/fastapi), HaaS-specific adaptation with SSE references aligned to `haas-protocol`.

#### 5.4.2 P0: pydantic-modeling

**Scope**: Pydantic data modeling best practices for HaaS protocol schemas, ADK Event payloads, and internal records.

- Must cover: `Field()` metadata classification (field-specific vs type-specific), `Annotated` + `Field()` preferred over bare `Field()` assignment, union metadata position traps (field-specific metadata like `deprecated` must wrap the entire union, not sit inside `Annotated[int | None, Field(...)]`), model hierarchy and subclass patterns, and UTC datetime handling.
- Must reference HaaS schema locations: `haas/protocol/`, `specs/haas-protocol/*.openapi.yaml`, ADK `Event` schema.
- Must not duplicate `fastapi-backend` (which covers API-level patterns). This skill focuses on model-level design.
- **Adaptation source**: [Pydantic official skill](https://github.com/pydantic/pydantic/blob/main/.agents/skills/pydantic/SKILL.md), HaaS-specific adaptation.

#### 5.4.3 P1: adapter-extension

**Scope**: Methodology for extending Harness adapters (Codex → Pi/OpenCode/AMP) and verifying adapter change completeness.

- Must cover the "find existing abstraction first" decision framework: enumerate how sibling adapters expose the same capability, reuse existing abstraction when it covers the need, promote to a shared field when ≥3 adapters share a concept, use `Literal` types (no `extra_body` or untyped `**kwargs`), and default-on vs opt-in decision tree (default-on only when no observable behavior change and no cost increase; preview/costly/rate-limiting features must be opt-in).
- Must cover the completeness checklist for adapter changes: streaming/non-streaming parity, sync/async paths, roundtrip verification, parse/dump consistency, and error path coverage.
- Must reference `harness-adapter` spec and `codex-app-server-adapter` spec.
- Must not duplicate `parallel-code-review` (which is about review lenses). This skill is about design methodology for adapter extension, triggered when adding or modifying an adapter.
- **Adaptation sources**: [Pydantic AI `adding-a-provider-api-feature`](https://github.com/pydantic/pydantic-ai/tree/main/.agents/skills) + [`complete-partial-pr`](https://github.com/pydantic/pydantic-ai/tree/main/.agents/skills), HaaS-specific adaptation for harness adapters.

#### 5.4.4 Not adopted (research conclusions only)

- SQLModel skill: low-medium value, HaaS DB layer not yet specified.
- Full Stack FastAPI Template library-skills wheel pattern: design reference only, not adopted as a skill.
- uv `.codex/skills` and hooks: uv-specific Codex automation, not directly reusable.
- Sidecar lifecycle, session state machine, redacted logging, pytest contract testing, secretless credential handling, error recovery, Docker multi-arch, SSE reconnect/replay: no open-source skill found; remain HaaS-original work to be developed as needed.

### 5.5 P1: parallel-code-review

**Scope**: Parallel multi-lens code review for HaaS changes.

- The skill must define HaaS-specific review lenses:
  - `secretless`: no real credentials, Authorization headers, cookies, presigned URLs, raw prompts, or full tool arguments in code, logs, events, tests, or artifacts.
  - `protocol-compatibility`: ADK and `/v1/haas/*` fields are additive only; no breaking changes to event names, error codes, headers, session/response IDs, or artifact URLs without versioning and migration plan.
  - `adapter-isolation`: harness-native protocols (Codex JSON-RPC, etc.) do not leak to northbound API; adapter does not bypass policy controller.
  - `recovery-semantics`: timeout, cancel, crash, SSE disconnect, event log write failure, provider failure have explicit state, error code, recovery action, and test evidence.
  - `cross-platform`: Tauri bundle, macOS entitlements, Windows WebView2, Linux behavior, sidecar binary path handling.
  - `tests`: new behavior has unit + integration evidence; bug fixes have regression tests; acceptance cases in specs are covered.
- The skill must align with the existing review gate: two rounds of code-review, then brooks-review (architecture/maintainability), then brooks-test (test quality). The parallel lenses accelerate the first code-review round; they do not replace brooks-review or brooks-test.
- The skill must define a findings deduplication and verification step: each finding has location, severity, and disposition (fixed / false_positive / accepted_risk). `accepted_risk` must be recorded in the relevant spec or final delivery note.
- **Adaptation sources**: [Logseq `logseq-review-workflow`](https://github.com/logseq/logseq/tree/main/.agents/skills/logseq-review-workflow) (orchestration pattern) + [Bruno `code-review`](https://github.com/usebruno/bruno/tree/main/.claude/skills/code-review) (6-lens pattern), lenses replaced with HaaS-specific concerns.

### 5.6 P1: .claude/skills symlink

- Create `.claude/skills` as a symbolic link to `../.agents/skills`.
- Confirm `.gitignore` does not exclude `.claude/skills` (only `.claude/settings.json` and `.claude/settings.local.json` are ignored).
- Confirm the existing `.claude/settings.json` is preserved.
- The symlink enables Claude Code and other tools that read `.claude/skills` to use the same skill inventory as `.agents/skills`.
- **Pattern source**: Logseq and GitButler both use this symlink pattern.

### 5.7 P1: SOURCES.md update

- Add entries for all newly adopted or adapted skills with: skill name, source repository URL, revision/commit, license, adaptation notes.
- Skills adapted from external sources must retain upstream copyright headers where applicable.
- HaaS-original skills (no external source) are marked as "HaaS-original".

## 6. Core Interfaces and Data Model

### 6.1 Skill file structure

Every skill follows the structure defined by the `skill-creator-for-work` convention:

```text
.agents/skills/<skill-name>/
├── SKILL.md          (required: YAML frontmatter + Markdown body)
├── scripts/          (optional: executable code)
├── references/       (optional: documentation loaded on demand)
└── assets/           (optional: files used in output)
```

SKILL.md frontmatter contains exactly two fields: `name` and `description`. The `description` is the primary trigger mechanism and must include both what the skill does and when to use it. No additional frontmatter fields unless required by an existing convention (e.g., `license`, `allowed-tools` used by vendored skills).

SKILL.md body must be under 500 lines. Detailed reference material moves to `references/` files linked from SKILL.md.

### 6.2 Naming conventions

- Skill names use kebab-case: `haas-debug-workflow`, `tauri-react-render-perf`, `parallel-code-review`.
- HaaS-specific skills may use the `haas-` prefix when the skill is not technology-specific (e.g., `haas-debug-workflow`).
- Technology-specific skills use the technology prefix (e.g., `tauri-react-render-perf`).
- No skill name duplicates an existing skill name.

### 6.3 Provenance tracking

`.agents/skills/SOURCES.md` is the authoritative provenance record. Each entry contains: skill name, installed from (URL + revision), license, adaptation notes.

## 7. Runtime Model and State Machine

Skills are loaded by the coding agent runtime based on frontmatter `description` matching. There is no persistent state. Skills are read-only knowledge packages; they do not mutate repository state except through the agent's normal file editing operations.

The `.claude/skills` symlink is a filesystem-level compatibility layer. It does not create a separate skill inventory; it points to the same `.agents/skills` directory.

## 8. Security and Permissions

- Skills must not contain real credentials, API keys, tokens, or presigned URLs. Examples must use placeholders.
- Skill scripts must not exfiltrate data or access network resources without explicit user instruction.
- The `.claude/skills` symlink must not expose `.claude/settings.json` (which may contain local configuration) — the symlink points only to the `skills` subdirectory, not the entire `.claude` directory.
- Vendored skills retain their upstream license headers. Adapted skills must note the source and license in `SOURCES.md`.

## 9. Observability

- Skill usage is not instrumented at the repository level. The coding agent runtime may track skill triggers, but this is outside the repository's scope.
- Skill quality is verified through: frontmatter validation (name + description present), structure validation (SKILL.md exists, no extraneous README/CHANGELOG), trigger accuracy review (description matches skill content), and cross-reference check (no broken internal links).

## 10. Failure, Recovery, Compatibility, and Rollback

- If a new skill conflicts with an existing skill's scope, the overlapping content must be merged into the more specific skill, and the less specific one must be narrowed or removed. No two skills may cover the same primary workflow without a clear delegation relationship.
- If the `.claude/skills` symlink causes issues with a tool that does not follow symlinks, the symlink can be removed without affecting `.agents/skills`. Rollback is `rm .claude/skills`.
- If an adapted skill's upstream source changes significantly, the local adaptation is independent and does not auto-update. `SOURCES.md` records the revision at adoption time.
- No runtime, protocol, or build compatibility impact. Skills are developer tooling only.
- Rollback of any skill addition is `rm -rf .agents/skills/<skill-name>` and removal of its `SOURCES.md` entry.

## 11. Test Plan and Acceptance

### 11.1 Structural validation

For each new or modified skill:

1. SKILL.md exists and has valid YAML frontmatter with exactly `name` and `description` (plus any convention-required fields for vendored skills).
2. `description` is non-empty and contains both what the skill does and when to use it.
3. SKILL.md body is under 500 lines.
4. No extraneous files (README.md, CHANGELOG.md, INSTALLATION.md) in the skill directory.
5. Internal references (links to other skills, specs, files) resolve correctly.
6. No real credentials or secrets in skill content.

### 11.2 Content validation

1. `haas-debug-workflow`: defines all three HaaS runtimes with selection criteria; enforces evidence-closed workflow; lists HaaS log locations; defines required output structure; does not reference Logseq-specific REPL or CLI.
2. `tauri-react-render-perf`: covers identity stability, Context boundaries, and Profiler verification without React Compiler; references `manager-gui-performance` P0-2; uses HaaS local state patterns (not Redux/react-query); does not duplicate `react-best-practices`.
3. `code-automation` enhancement: existing content preserved; cross-layer check uses HaaS layer names; spec sync check references AGENTS.md; does not duplicate `spec-coding`.
4. `parallel-code-review`: defines all six HaaS lenses; aligns with two-round code-review + brooks-review + brooks-test; defines findings deduplication; does not replace brooks gates.
5. `.claude/skills` symlink: points to `../.agents/skills`; `.gitignore` does not exclude it; existing `.claude/settings.json` preserved.
6. `SOURCES.md`: all new skills have provenance entries; licenses recorded.
7. `fastapi-backend`: covers Annotated dependency injection, async/sync rules, response_model filtering; `references/streaming.md` covers EventSourceResponse + ServerSentEvent aligned with haas-protocol SSE conventions; references HaaS commands; does not duplicate code-automation or haas-debug-workflow.
8. `pydantic-modeling`: covers Field metadata classification, Annotated patterns, union metadata traps; references HaaS protocol schema locations; does not duplicate fastapi-backend.
9. `adapter-extension`: covers "find existing abstraction first" framework, default-on vs opt-in decision tree, completeness checklist (streaming/non-streaming, sync/async, roundtrip); references harness-adapter spec; does not duplicate parallel-code-review.

### 11.3 Dedup validation

1. No new skill duplicates the primary workflow of an existing skill.
2. `tauri-react-render-perf` does not duplicate `react-best-practices` or `react-typescript-kit`.
3. `haas-debug-workflow` does not duplicate `code-automation` (which covers verification command selection, not debugging workflow).
4. `parallel-code-review` does not duplicate `code-automation` or `spec-coding`.
5. Python/FastAPI wave skills do not duplicate Tauri/Rust wave skills or existing skills: `fastapi-backend` (API patterns) vs `code-automation` (verification commands); `pydantic-modeling` (schema design) vs `fastapi-backend` (API patterns); `adapter-extension` (design methodology) vs `parallel-code-review` (review lenses).

### 11.4 Acceptance criteria

- All structural validation checks pass for every new and modified skill.
- All content validation checks pass.
- All dedup validation checks pass.
- `make pre-commit` passes (including `make secret-scan`).
- Spec review is complete with no blocking items.
- Two rounds of code-review, brooks-review, and brooks-test are complete with findings dispositioned.
- No user uncommitted changes are overwritten or reverted.

## 12. Task Breakdown and Priority

| Order | Priority | Task | Deliverable | Dependency |
|---|---|---|---|---|
| 1 | P0 | Create spec (this document) + spec review | `specs/agent-skills/README.md` + `README.zh-CN.md` | None |
| 2 | P0 | Create `haas-debug-workflow` skill | `.agents/skills/haas-debug-workflow/SKILL.md` | Task 1 |
| 3 | P0 | Create `tauri-react-render-perf` skill | `.agents/skills/tauri-react-render-perf/SKILL.md` | Task 1 |
| 4 | P1 | Enhance `code-automation` with cross-layer + spec sync | `.agents/skills/code-automation/SKILL.md` (additive) | Task 1 |
| 5 | P1 | Create `parallel-code-review` skill | `.agents/skills/parallel-code-review/SKILL.md` | Task 1 |
| 6 | P1 | Create `.claude/skills` symlink | `.claude/skills -> ../.agents/skills` | Task 1; gitignore confirmed safe |
| 7 | P1 | Update `SOURCES.md` | `.agents/skills/SOURCES.md` | Tasks 2-5 |
| 8 | P0 | Python/FastAPI research complete; adopt 3 skills | Research report + `fastapi-backend` (SKILL.md + references/streaming.md), `pydantic-modeling`, `adapter-extension` | Task 1; research complete |
| 8a | P0 | Update spec 2.4 + 5.4 with Python findings | Spec sections populated | Task 8 research |
| 8b | P0 | Create `fastapi-backend` skill + `references/streaming.md` | `.agents/skills/fastapi-backend/` | Task 8a |
| 8c | P0 | Create `pydantic-modeling` skill | `.agents/skills/pydantic-modeling/SKILL.md` | Task 8a |
| 8d | P1 | Create `adapter-extension` skill | `.agents/skills/adapter-extension/SKILL.md` | Task 8a |
| 8e | P1 | Update SOURCES.md with Python provenance | `.agents/skills/SOURCES.md` | Tasks 8b-8d |
| 9 | P0 | Structural + content + dedup validation | Validation evidence | Tasks 2-8 |
| 10 | P0 | Two-round code-review + brooks-review + brooks-test | Review findings with disposition | Tasks 2-9 |
| 11 | P0 | `make pre-commit` + final delivery report | Pre-commit evidence + final report | Tasks 1-10 |

## 13. Component Impact Analysis

| Component | Impact | Required action | Compatibility conclusion |
|---|---|---|---|
| Root AGENTS.md | None; skills implement existing AI development rules | No change | Fully unchanged |
| specs/README.md | Add `agent-skills` to component index | Add one line | Additive only |
| .agents/skills/ | New skills added; code-automation enhanced additively | Create 6 new skill dirs (3 Tauri/Rust + 3 Python); modify 1 existing skill additively; update SOURCES.md | No existing skill removed or narrowed |
| .claude/ | Add skills symlink | `ln -s ../.agents/skills .claude/skills` | settings.json preserved; no behavior change |
| haas/ (runtime code) | None | No change | Fully unchanged |
| tests/ | None (skills are developer tooling; structural validation is by command, not pytest) | No change | Fully unchanged |
| Dockerfile / Makefile | None | No change | Fully unchanged |
| manager-gui-performance spec | Referenced by tauri-react-render-perf skill | No spec change; skill references existing P0-2 | Unchanged |
| Python/FastAPI wave | 3 new skills adopted (fastapi-backend, pydantic-modeling, adapter-extension) | Create 3 skill dirs (1 with references/); update SOURCES.md | Additive only; no existing skill conflict |

## 14. Delivery Sequence and Estimate

| Stage | Estimate | Work | Exit evidence |
|---|---:|---|---|
| S0 | 0.5 day | Spec creation + review | Spec reviewed, blockers resolved |
| S1 | 0.5 day | haas-debug-workflow + tauri-react-render-perf creation | Structural + content validation pass |
| S2 | 0.5 day | code-automation enhancement + parallel-code-review + symlink + SOURCES.md | Validation pass; git diff confirms additive only |
| S3 | 1 day | Python/FastAPI research + skill adoption (if applicable) | Research report; new skills validated |
| S4 | 0.5 day | Full validation + reviews + pre-commit | All gates pass; final report |

Expected window: 2.5-3 days including risk buffer. Python/FastAPI wave may extend by 0.5-1 day if multiple high-value skills are found.
