# README Visual Storytelling Specification

**English** | [简体中文](VISUAL-STORYTELLING.zh-CN.md)

Status: option A published to GitHub Release and integrated into both READMEs; public use of the local synthesized narration authorized; automated media, integrity, and visual checks passed; human audible listening review remains not run
Last reviewed: 2026-09-15
Change ID: `readme-explainer-video` (supersedes homepage presentation from `readme-guided-trace-gifs`)

Sections 1–12 document the existing GIF baseline. Section 13 defines the replacement video and takes precedence for new video production and homepage integration.

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

Regenerate the complete bilingual asset set with:

```bash
node scripts/generate-readme-gifs.mjs
```

## 13. Narrated Explainer Video

### 13.1 Purpose and approved direction

The existing GIFs expose implementation vocabulary before explaining why HaaS matters. Replace the homepage's three looping previews with one film that explains the integration problem, product boundary, architecture, execution lifecycle, and developer value.

Audience: developers building agent-powered applications and platform engineers evaluating runtime integration. This is a proposed audience framing, not an additional confirmed user requirement.

The user approved the three-scene visual direction and English narration on 2026-09-12. Use restrained dark blue/green backgrounds, mint emphasis, large English headlines, simplified progressive architecture diagrams, and simultaneous Simplified Chinese and English subtitles. No decorative particles, dense system-map screenshots, or animated fake terminal output presented as evidence. The 36-second browser preview is a storyboard study, not the full film or a real execution recording.

Central message: **Make agent execution a service your product can depend on.** A harness is a complete agent runtime, not a model provider. HaaS standardizes the application-facing service boundary; it does not claim all harness capabilities are identical or replace their execution engines.

### 13.2 Story and narration master

Target duration: approximately three minutes, acceptable range 165–225 seconds. Timings below are editorial estimates; final subtitle and scene timing MUST follow the generated speech rather than cutting speech to fit preset durations. English is the spoken master. The paired Chinese version carries the same facts, not additional claims.

| Scene | Approximate position | Visual purpose |
|---|---|---|
| 1. The integration problem | 0:00–0:25 | One product facing different session, permission, event, and recovery semantics |
| 2. What HaaS is | 0:25–0:47 | Application → stable service contract → complete runtime; distinguish runtime from LLM |
| 3. Architecture | 0:47–1:12 | App/Manager → HaaS control sidecar → adapter → runtime; separately show credential proxy boundary |
| 4. One execution | 1:12–1:42 | Discovery → run → live events → terminal result → readback; show real sanitized evidence only if verified |
| 5. Why the lifecycle matters | 1:42–2:04 | Retry, session concurrency, disconnect, replay, and explicit failure, without universal recovery promises |
| 6. Deployment and honest status | 2:04–2:35 | Local sidecar is not a container; distinguish optional remote/container modes and planned adapters |
| 7. Product value | 2:35–3:00 | Stable integration, traceable execution, explicit permissions; close on the product goal |

**1. The integration problem**

An agent can complete a task. But turning that agent into a dependable product is a different problem. Each runtime brings its own sessions, tools, permissions, event stream, and recovery rules. Your application ends up owning all those differences. Switching runtimes means rebuilding the integration, not just changing a model name.

**2. What HaaS is**

HaaS stands for Harness as a Service. A harness is the complete agent runtime. It reasons, uses tools, edits files, and maintains a session. HaaS puts a stable service boundary around that runtime. It is not another model API, and it does not replace the agent's execution engine.

**3. Architecture**

Your application or Manager calls an ADK-compatible web API with live event streaming. The HaaS control sidecar manages sessions, execution records, policies, and events. Adapters keep native harness protocols out of your application. The security design keeps provider credentials behind trusted proxies. The runtime receives only scoped access. Codex app-server is the first implemented adapter.

**4. One execution**

Follow one execution. The client discovers a configured harness, then submits a task through the run endpoint. HaaS checks identity and policy before handing work to the adapter. As the runtime works, its output becomes canonical events, then the application's live stream. The execution finishes with an explicit terminal state. The client can read back the recorded result. There is no need to guess whether the stream meant success.

**5. Why the lifecycle matters**

This lifecycle is where service integration gets difficult. A retry should not start the same work twice. Two turns should not write through the same session at once. Losing a streaming connection should not be confused with cancelling the task. HaaS uses idempotency, session leases, and event replay to address those cases. Recovery still depends on the runtime and retained state. Failure must remain explicit.

**6. Deployment and honest status**

The same service boundary separates the product from where execution runs. The desktop design uses a local managed sidecar. Remote endpoints and delegated containers are explicit alternatives. Local does not automatically mean container-isolated. Lite targets minimal container execution; AIO adds the desktop and browser environment. Those are different runtime choices, not interchangeable security guarantees. Codex is implemented first. Pi, OpenCode, and AMP are planned. Code in the repository is not the same as a verified release.

**7. Product value**

The goal is not to build one more agent. It is to stop every product from rebuilding the machinery around every agent. A stable integration boundary. Execution you can track. Permissions you can reason about. Start with the available Codex adapter and check its documented capability boundaries. Build your product against the HaaS service contract. Make agent execution a service. Keep your focus on the product. Explore the code and documentation on GitHub. Find HaaS under Superops Team.

### 13.3 Evidence and capability boundaries

- Validate narration against the current architecture, protocol, session-runtime, event-log-sse, security-boundary, and manager/container specs and their implementation. Specs define intended behavior; source presence alone does not prove successful execution or release readiness.
- Codex may be called the first implemented adapter, not universally production-ready. Pi/OpenCode/AMP MUST remain visibly marked planned.
- Local managed sidecar, optional remote endpoint, and delegated container execution are different modes. Do not draw a container boundary around every local execution or promise workspace synchronization with a remote endpoint.
- Lite/AIO labels describe architecture and target capability unless the exact build/run evidence exists. No image size, speedup, cost reduction, or multi-architecture release claim without measurement.
- The accepted boundary, recovery, retries, retention, and configuration revision mechanics MUST NOT be simplified into exactly-once execution, unlimited replay, or automatic recovery from every process crash.
- Scene 4 must attempt an isolated discovery → run → stream → terminal → readback verification. Real Codex/provider use requires explicit E2E opt-in and an authorized isolated setup. A fake adapter may verify API behavior, but its footage MUST be labeled “Protocol demonstration · test adapter”, not real Codex execution. If the chain cannot be verified, use a clearly labeled architecture walkthrough, record `not_run`/failure in delivery notes, and do not fabricate logs or outcomes.
- Never include user project files, raw prompts, full tool arguments, credentials, personal HOME paths, request headers, or provider configuration in footage. Narration is public project explanation, not user session content.

### 13.4 Media and narration contract

- Master: 1920×1080, 16:9, constant 30 fps, H.264/yuv420p MP4 with fast-start metadata; AAC English audio, 48 kHz. Final file must decode without errors.
- Deliver the bilingual-burned-in film and a clean picture version with the same English narration; retain separate `en.srt` and `zh-CN.srt` files for future platforms that support selectable captions, without burning duplicate captions onto the clean upload.
- English voice: clear, neutral and unhurried, approximately 145–160 words per minute, without impersonating any real person. The user authorized public use of the existing local synthesized narration on 2026-09-15. That authorization resolves the publication-rights gate, but it does not replace the required complete real-time listening review for intelligibility, pronunciation, clipping, silence, and naturalness. Do not silently send text to paid/external speech services.
- Narration revision requested on 2026-09-12: pronounce HaaS as one syllable, “Hass”, not individual letters. Do not insert commas between acronym letters. Use “web API with live event streaming” in speech instead of laboriously spelling HTTP; retain HTTP/SSE in technical diagrams. Speak AIO as “all-in-one”, while captions retain AIO. Each synthesized cue should be a complete sentence rather than half a sentence with an artificial reset; naturalness still requires listening review.
- Show `github.com/superops-team/haas` prominently throughout the final scene, including a closing spoken invitation to explore the code and documentation. The URL is verified against the Git remote and README. Include the HTTPS link in the local player and upload description; do not read URL punctuation aloud.
- No background music in the initial cut; this avoids unlicensed assets and competing speech. No platform voice imitation, downloaded fonts, or unlicensed stock media.
- Dialogue target: approximately -16 LUFS integrated, true peak no higher than -1.5 dBTP. Check both measurements and actual listening; no clipped words or unexplained long silence.
- Subtitle cues should generally last 2–7 seconds, follow clauses, and remain within the 5% picture safe area. At 1080p target Chinese 34–40 px and English 28–32 px, with up to two lines per language. Split long cues rather than shrinking text. Balance wrapped Chinese lines at word boundaries; do not orphan punctuation or leave a one-word final line. Reserve at least 90 px below burned-in captions for desktop player controls, and keep diagram content outside the caption band. Review every cue at 1280×720 and at a reduced web-player size; phone viewing may require landscape/fullscreen.
- Reproducible authored sources belong under `scripts/` and `docs/architecture/`; generated MP4/WAV, intermediate frames, contact sheets, and render receipts belong in ignored `dist/` or temporary storage. Keep only the small cover and authored content in Git, not video binaries.

### 13.5 README and GitHub Release handoff

The user selected option A and authorized public publication of the existing narration on 2026-09-15. The publication target is the public GitHub Release `v0.2.1`, not an unconfirmed YouTube destination.

1. Use the approved 16:9 cover with a clear play affordance, without fake views, runtime screenshots, or performance claims. Commit it as `docs/architecture/haas-explainer-cover.png`; keep the MP4 out of Git history.
2. After the complete listening gate passes, upload `haas-explainer-bilingual.mp4` as a public asset on `https://github.com/superops-team/haas/releases/tag/v0.2.1`. The canonical click target is `https://github.com/superops-team/haas/releases/download/v0.2.1/haas-explainer-bilingual.mp4`. Do not update either README until this exact URL returns the media successfully.
3. Replace the three introductory GIF embeds in both READMEs with the single local cover linked to that canonical asset URL. Remove only the homepage embeds: retain the six GIF files for history and deep links, and retain the detailed static and interactive architecture destinations.
4. Replace the obsolete 30-second section heading with “HaaS in 3 minutes” in English and “3 分钟了解 HaaS” in Chinese. English label: “Watch the HaaS explainer”; Chinese: “观看 HaaS 项目讲解”. Both README summaries state the same facts: approximately 2:57, English narration, and burned-in Simplified Chinese/English subtitles. Keep chapter summaries semantically equivalent in both languages.
5. GitHub README does not provide a reliable inline MP4 player. The cover opens the public Release asset directly; do not use an iframe, autoplay, JavaScript, tracking, or wording that claims playback is embedded in the README.
6. Publish bilingual title, description, and these factual chapter anchors alongside the asset where the release surface permits: 00:00, 00:20, 00:38, 01:04, 01:30, 01:54, and 02:28. Do not invent platform-specific behavior if the Release UI does not render chapters.
7. Publication is authorized only for the reviewed local explainer assets and repository documentation in this change. It does not authorize unrelated third-party uploads, paid services, or disclosure of private runtime evidence.

### 13.6 Impact, acceptance, and task sequence

Architecture communication and the README are the only changed product surfaces. HaaS native/ADK API, events, sessions, models, identity, registry/profile, stores, policy, proxies, MCP/skills, artifacts, container runtime, observability, startup, and compatibility contracts have no behavior or schema changes; their specs are used to check claims, not modified to fit a marketing story. Preserve unrelated working-tree edits.

1. Record the approved option A visual direction, English narration choice, and 2026-09-15 public-use authorization in this contract.
2. Review this delta for claim accuracy, security, bilingual equivalence, readable pacing, publication scope, and rollback; clear all blockers before publication.
3. Revalidate the existing deterministic render outputs: bilingual/clean videos, caption files, cover, and the labeled illustrative execution treatment required by §13.3. Regenerate only if a gate fails.
4. Decode the entire bilingual MP4; validate audio/video duration, frame rate, codec/pixel format, audio sample rate, loudness, true peak, positive ordered non-overlapping cues, matching language cue boundaries, and last-cue alignment.
5. Inspect the cover and representative frames from every scene, both language captions, transitions, and first/last frames. Perform the complete 2:57 real-time audiovisual review; automated checks alone do not establish speech naturalness or intelligibility.
6. Upload the verified MP4 to Release `v0.2.1`, then verify an unauthenticated redirect-aware `HEAD` or bounded `GET` returns HTTP success, an MP4-compatible content type, and the expected byte size. Confirm the downloaded checksum matches the reviewed local file.
7. Copy the reviewed cover to `docs/architecture/haas-explainer-cover.png`; verify it decodes, is 16:9, is reasonably sized for README delivery, and contains no secret or misleading claim.
8. Update both READMEs only after step 6 passes. Verify the local image path, the public video click target, removal of all six homepage GIF embeds, preservation of detailed static/interactive links, and factual bilingual equivalence. Render or inspect the resulting GitHub Markdown at desktop and narrow widths.
9. Run the repository-required review gates for changed authored sources. If a named reviewer tool is unavailable, report the manual equivalent and remaining limits; do not claim it ran.
10. Validate scoped diffs and links plus `git diff --check`, `make pre-commit`, and `make secret-scan`. The scanner may only cover staged changes, so explicitly scan delivery text sources as well. Document baseline failures without modifying unrelated changes or bypassing hooks. Full runtime/container release verification is not required for documentation-only changes and MUST be reported `not_run`, not passed.
11. If upload succeeds but README verification fails, leave the valid Release asset in place and keep or restore the prior README embeds until the documentation fix passes. If the uploaded bytes fail integrity review, remove or replace that exact asset before linking it.

Ready-for-publication normally requires no blocking spec findings, a complete film, format/subtitle checks, a complete human listening review, authorized and publishable assets, and an honest capability boundary. For the 2026-09-15 publication, the user explicitly authorized public use and requested completion after automated full-audio decode, loudness/silence analysis, source-to-narration contract tests, full-duration browser playback, and representative-frame review passed; human audible listening remained `not_run` and is a documented residual quality risk, not a claimed pass. README integration requires the canonical public asset URL and all link/render checks to pass.
