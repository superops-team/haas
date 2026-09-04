# README Visual Storytelling Specification

**English** | [简体中文](VISUAL-STORYTELLING.zh-CN.md)

Status: Draft
Last reviewed: 2026-09-04
Change ID: `readme-guided-trace-gifs`

## 1. Background

The README already contains validated Archify architecture and sequence diagrams. They are precise and explorable, but a new reader must still inspect a dense static preview before understanding the product. This change adds short guided-trace GIFs that explain the core concept and request lifecycle before the detailed diagrams and specifications.

The GIFs are communication assets derived from the same architectural facts. They are not new protocol contracts and MUST NOT become a separate source of truth.

## 2. Goals

- Let a first-time reader understand the HaaS product boundary in about 30 seconds.
- Explain the stable northbound protocol, adapter isolation, sandbox boundary, and secretless processing model.
- Show the `/run_sse` execution path and the transformation from a raw request into a safe, canonical, persisted, and recoverable result.
- Provide matching English-default and Simplified Chinese assets.
- Preserve the existing Archify HTML diagrams as the detailed interactive destination.

## 3. Non-goals

- Do not change any HTTP/SSE API, event, state, error, permission, or runtime behavior.
- Do not replace the interactive Archify diagrams or component specifications.
- Do not depict Pi, OpenCode, or AMP as implemented runtimes.
- Do not imply that `/v1/codex-worker/*` is supported.
- Do not add hosted animation services, external image dependencies, telemetry, or README JavaScript.
- Do not include credentials, raw prompts, complete tool arguments, or production data.

## 4. Reader Scenarios

1. A new engineer understands what HaaS sits between, what it standardizes, and what remains harness-specific.
2. An integrator sees how `POST /run_sse` is admitted, executed, normalized, persisted, and streamed.
3. A reviewer sees where policy, credentials, redaction, storage, replay, and recovery are applied.
4. A reader clicks a GIF to open the corresponding Archify diagram or walkthrough.

## 5. Visual Language

All animations use the approved **Guided Trace** direction:

- One obvious primary path and one active stage at a time.
- Completed stages remain visible at lower emphasis so progress is cumulative.
- One short caption explains the architectural fact shown by each stage.
- No flashing, bouncing, parallax, or decorative particles.
- The complete final state pauses before the loop restarts.
- Dark canvas, high-contrast text, and the Archify cyan/emerald/rose/violet semantic palette.

Each animation MUST preserve complete meaning when paused on its final frame.

## 6. Animation Set

### 6.1 HaaS Concept

Outputs: `haas-concept.gif` and `haas-concept.zh-CN.gif`. Target: 9–11 seconds.

Storyboard:

1. Clients enter one stable **Google ADK 2.0 REST + SSE** surface.
2. HaaS resolves identity, configured harness, policy, and session facts.
3. The Harness Adapter boundary isolates native runtime protocols.
4. Codex app-server is implemented; Pi, OpenCode, and AMP remain explicitly planned.
5. Loopback model/MCP proxies and OpenSandbox AIO show secretless and execution isolation boundaries.
6. Final: “One stable protocol. Multiple isolated agent runtimes.”

Click target: the matching interactive system architecture HTML.

### 6.2 `/run_sse` Request Flow

Outputs: `run-sse-flow.gif` and `run-sse-flow.zh-CN.gif`. Target: 11–13 seconds.

Storyboard:

1. Client sends `POST /run_sse`.
2. HaaS authenticates, resolves `appName`, and performs admission control.
3. Idempotency and the session lease prevent duplicate or concurrent starts.
4. Policy is compiled into a sandbox specification.
5. The Codex adapter performs `initialize -> initialized -> turn`.
6. Native harness events return to Session Runtime.
7. Events are redacted, appended, projected to ADK Events, and emitted as SSE frames.
8. The terminal state is persisted before the stream closes.

Click target: the matching interactive `/run_sse` sequence HTML.

### 6.3 Secure Request Processing

Outputs: `request-processing.gif` and `request-processing.zh-CN.gif`. Target: 11–13 seconds.

Storyboard:

1. Raw request enters the identity and scope boundary.
2. Policy and admission create an allowed envelope or fail closed before harness startup.
3. Effective configured-harness and session configuration is frozen.
4. The adapter converts the canonical turn into a harness-native invocation without leaking it upstream.
5. Model, MCP, tool, and credential access uses scoped loopback handles.
6. Native output is normalized and redacted.
7. Canonical events and terminal state are persisted.
8. ADK projection serves live SSE and supports `Last-Event-ID` replay.
9. Final: “Safe in. Canonical through. Recoverable out.”

Click target: the matching architecture walkthrough specification.

## 7. Asset and Rendering Contract

| Property | Requirement |
|---|---|
| Canvas | `1440x810`, 16:9 |
| Language | English at the default filename; Chinese at `*.zh-CN.gif` |
| Loop | Infinite, without an abrupt semantic jump |
| Stage dwell | Approximately 1.2–1.8 seconds |
| Final dwell | At least 2 seconds |
| Frame rate | 12–15 fps after optimization |
| Size | Target <= 4 MiB; 5 MiB is a hard failure |
| Text | Readable at GitHub README width; no body paragraphs |
| Fallback | Existing static PNG and interactive HTML remain available |

The semantic source MUST be an Archify specification with `meta.animation: "trace"` and curated `meta.views`, or a dedicated Archify diagram when an existing source cannot express the story without distorting its original purpose. Every source MUST pass showcase validation before capture. Captured frames MUST NOT add facts absent from the Archify source.

## 8. README Integration

Both root README files gain a section after the introductory status block:

- English: `## HaaS in 30 seconds`
- Chinese: `## 30 秒了解 HaaS`

The section contains the three GIFs in narrative order: concept, request flow, processing pipeline. Each GIF has one short explanatory sentence and is clickable. English uses default assets and destinations; Chinese uses `*.zh-CN.gif` and Chinese destinations. Detailed static sections remain in place.

## 9. Impact and Implementation Boundaries

- ADK API, HaaS API, SSE, state, storage, adapter, proxy, sandbox, permissions, and credentials: no behavioral impact.
- Reuse existing architecture and sequence sources when their topology matches the story.
- Add only the minimum dedicated source required for secure request processing.
- Keep generation scripts under `scripts/` and assets under `docs/architecture/`.
- Do not hand-edit GIFs or fetch remote fonts, icons, images, or runtime data.
- Do not commit temporary frames, browser receipts, contact sheets, or verification reports.

## 10. Failure Handling

- Stop before capture when showcase validation fails.
- Never assemble stale frames after incomplete browser capture.
- If an asset exceeds 5 MiB, reduce redundant frames or palette complexity before text size.
- Fail delivery when English and Chinese stage counts or claims diverge.
- Retain working static PNG links while repairing any GitHub GIF rendering defect.

## 11. Test Plan and Acceptance

1. Validate all Archify sources at 9/9 showcase, 0 errors, 0 warnings.
2. Deliver and run browser evidence for changed interactive HTML.
3. Inspect both languages at native size and README width.
4. Verify dimensions, duration, frame rate, looping, final dwell, and file size.
5. Compare bilingual stage counts and machine identifiers.
6. Check README links, `git diff --check`, and `make pre-commit`.

Acceptance requires six GitHub-renderable GIFs, equivalent bilingual facts, complete readable final frames, assets at or below 4 MiB unless a reviewed exception remains below 5 MiB, valid interactive destinations, and no contradiction with component specs. Runtime, Docker, and provider tests are `not_run` because runtime behavior is unchanged.

## 12. Task Breakdown

1. Author or adapt bilingual Archify sources and story views.
2. Review this spec and clear blocking findings.
3. Deliver validated interactive artifacts.
4. Implement deterministic capture and GIF assembly.
5. Generate and optimize six GIFs.
6. Perform automated and perceptual review.
7. Embed assets in both READMEs and verify links.
8. Run two-round change review and pre-commit gates.
