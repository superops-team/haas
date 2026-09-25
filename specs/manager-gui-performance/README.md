# Manager GUI Performance Specification

**English** | [简体中文](README.zh-CN.md)

Status: Reviewed; blockers resolved; baseline validated; hardening delta added
Last reviewed: 2026-09-23
Change ID: manager-gui-performance-convergence
Related specs: [Manager HaaS Sidecar Backend](../manager-haas-sidecar-backend/README.md), [Manager Product Identity](../manager-product-identity/README.md)

## 1. Component Role

Manager GUI Performance owns the runtime-efficiency contract of the OpenHarness React/Vite
surface. It covers initial JavaScript delivery, React update isolation, live transcript
reconciliation, and refresh ownership for Manager-local data. It does not redefine the HaaS or
ADK protocol.

The component exists because correctness-preserving optimizations need explicit contracts. A
poll may be removed only when missed-terminal recovery remains reliable; a render boundary may be
introduced only when live status, localization and disclosure state remain current.

## 2. Sources and Rationale

The 2026-09-22 local baseline used the repository-vendored Fallow and React performance rules,
then verified candidates against source and a production Vite build. The confirmed baseline is:

- the production entry chunk is 1,042.06 kB minified and 316.92 kB gzip; low-frequency product
  surfaces are statically imported by `App.tsx`; PDF and XLSX preview code is already split;
- while a session is running, the GUI fetches the complete persisted transcript every three
  seconds and transforms the complete message array; the endpoint has no cursor or terminal-only
  query and the interval has no single-flight guard;
- live text, reasoning and model-stage snapshots are coalesced to 33 ms, but their React state is
  owned by the top-level `App`, so every publication enters the complete app render function;
- the Connectors page and Inbox Configure page have multiple mounted owners polling the same
  resources, including overlapping fast and baseline intervals;
- `ApprovalCard.tsx` and `humanize.ts` form a production import cycle; and
- five production source files are unreachable from the current application entry points.

Fallow's score, complexity count and unused-export list are discovery evidence, not acceptance
facts. Dynamic CSS findings and exports used only by tests are not deletion authority. Runtime
performance claims require the focused tests below.

### 2.1 Validated local baseline

The candidates above were revalidated on 2026-09-22 with a production Vite build served by
`vite preview` and exercised through Chromium using the repository's Web-specific Playwright
transport fixture. The fixture intercepts Manager HTTP/WebSocket responses, so it does not require
a provider or mutate real user state, while browser timers, fetches, WebSocket delivery and React
rendering remain production code paths. The results were:

- the production build emitted one 1,042.06 kB minified / 316.92 kB gzip entry JS file. PDF and
  XLSX were the only application JS features split into separate chunks; no optional surface had a
  route chunk;
- after initial page settling, a visible Connectors surface made four `GET /v1/connectors` calls
  in 10.5 seconds, while MCP and Slack status each made two. This confirms two connector-list
  refresh owners;
- a visible Inbox Configure surface made four reads each for unrouted items, inbox routing and
  recent channels, plus six session-list reads in 10.5 seconds. The global session refresher
  contributes to the last count, while the configured cards duplicate the other resources;
- opening a running session and observing 7.2 seconds with a deliberately 3.5-second response time
  produced four full-transcript requests and a maximum concurrency of three. This confirms that
  the immediate/open read and interval reconciliation are not single-flight; and
- forty WebSocket assistant deltas caused 47 top-level `App` render participations and roughly 90
  each for `Sidebar`, `Transcript`, `Composer` and `RightRail` in the named development diagnostic.
  A production-preview rerun reproduced the same root/branch update shape (component names are
  minified there). Counts include React development `StrictMode` amplification, but the observed
  coupling of unchanged branches to live publications is invariant.

Fallow entry traversal and an independent import search also reproduced the two-file
`ApprovalCard.tsx -> humanize.ts -> ApprovalCard.tsx` cycle and the five production reachability
candidates. Reachability remains a deletion precondition, not proof that deletion is behaviorally
safe; route and package regressions are still required during implementation.

### 2.2 Review-driven hardening delta

The 2026-09-23 skill-based project review added four confirmed follow-up gaps:

- the root `make full-check` is the documented release gate, but it does not run GUI Vitest,
  TypeScript/Vite build, or any production-preview browser check. A release can therefore pass the
  authoritative gate while the desktop/web frontend is broken;
- production-preview performance evidence currently depends on manual temporary Playwright
  configuration. The spec requires this evidence, but the repository does not yet provide a stable
  command that rebuilds `dist` before measuring it;
- the artifact viewer has strong protocol and security tests, but the GUI read path needs an
  explicit state contract for failed reads, quick artifact/session switches, and stale async
  completions; and
- Fallow reports no current circular dependency after the latest cleanup, but complexity remains
  concentrated in session streaming, transcript replay, artifact viewing and top-level app state.
  Refactors in these areas need a small-slice budget and tests rather than another unbounded
  component growth cycle.

## 3. Upstream and Downstream Relationships

```text
Manager local HTTP/WebSocket API
  -> query/reconciliation owner
  -> App/session state
  -> route surface or live transcript boundary
  -> React DOM / Tauri WebView
```

- Upstream: Manager session, inbox, connector, automation, artifact and settings endpoints plus
  the session and app-wide WebSocket streams.
- Downstream: React route surfaces, transcript/activity rendering, sidebar, composer and right
  rail.
- Build boundary: Vite owns chunk generation; Tauri consumes the same generated assets. Browser
  and packaged desktop behavior must remain equivalent.

## 4. Goals, Non-goals, and User Scenarios

### 4.1 Goals

1. Keep foreground streaming responsive as transcript and session history grow.
2. Avoid duplicate or overlapping local API reads while preserving fresh visible state.
3. Remove low-frequency page code from the initial application chunk.
4. Make module ownership acyclic and remove confirmed unreachable production code.
5. Establish repeatable local performance gates that distinguish measurements from static
   candidates.

### 4.2 Non-goals

- No ADK or `/v1/haas/*` schema, event, error-code, credential, artifact or container change.
- No visual redesign, route rename, feature removal, transcript truncation or history loss.
- No new server-state framework by default. A query library may be considered only if the small
  repository-owned coordinator cannot satisfy deduplication and cancellation requirements.
- PDF and XLSX loading are already lazy and are not part of the initial-bundle defect.
- List virtualization is a separate follow-up and is not implied by this change.

### 4.3 User Scenarios

- Given a long-running session with a large transcript, when live deltas arrive, then current
  progress updates without repeatedly rendering unrelated navigation and settings surfaces.
- Given a visible Connectors or Inbox Configure page, when freshness polling occurs, then each
  resource has one request owner and at most one in-flight request.
- Given a normal healthy session WebSocket, when a turn runs, then the GUI does not repeatedly
  download the full transcript merely to discover its terminal state.
- Given the application opens on the session surface, when initial JavaScript loads, then code for
  unopened settings, inbox, audit, automation and connector surfaces is deferred.

## 5. Responsibility Boundaries and Functional Requirements

### P0-1: Bounded terminal reconciliation

- The session WebSocket remains the primary source for live and terminal events.
- Full-transcript readback is a recovery action, not an unconditional three-second heartbeat. It
  may run after connection loss, reconnect or process restoration with a persisted running turn, a
  contradiction between WebSocket `ready` and session-list liveness, or an explicit
  terminal-integrity suspicion. Ordinary event silence on an otherwise healthy transport never
  starts readback; a silence watchdog may do so only when an independent transport-health signal
  has also expired.
- Reconciliation is single-flight per session. A slow request cannot overlap the next attempt.
- A successful non-terminal read increases delay with bounded backoff; a relevant event or user
  action may reset the delay. A qualifying recovery trigger probes immediately, then retries after
  3, 6, 12 and at most 30 seconds while the turn is still believed to be running and trust has not
  been restored. Background windows pause nonessential retries. Only a session already in
  `probing` or `recovery_required` may perform one final integrity probe when entering the
  background; healthy sessions do not probe merely because the window was hidden.
- Readback must preserve FV-33 behavior: a missed `turn_done` is recovered without resubmitting
  work, and the latest local intent occurrence must match before clearing running state.
- Phase one may keep the current full-message endpoint with event-driven triggering. An additive
  Manager-local status/cursor endpoint requires a separate API delta before implementation.

### P0-2: Isolated live React projection

- Mutable live text, reasoning and model-stage state is owned by a narrow live-turn boundary, not
  by the component that owns every application surface.
- A coalesced live publication updates the active transcript branch. Named React Profiler
  boundaries around the sidebar, inactive route, composer and right rail must not invoke
  `onRender` solely because a token delta arrived.
- Historical grouping remains cached by `items` identity and running boundary, as required by the
  Manager HaaS Sidecar Backend spec. Unchanged Markdown bodies are not reparsed.
- Terminal flush remains synchronous with respect to the canonical buffers and cannot drop the
  final delta. Existing event order, task status and recovery semantics remain unchanged.

### P1-1: Route-level code splitting

- Settings, Integrations, Scheduled, Audit, Inbox and Persona surfaces are lazy route boundaries.
  Opening one loads its code once and presents a stable, accessible loading state.
- Session-critical UI, approval/input controls and the current transcript remain in the initial
  route.
- The initial synchronous JavaScript graph (the entry plus every static JS dependency required
  before the session shell becomes interactive) must be at least 20% smaller than the recorded
  1,042.06 kB minified baseline: no more than 833.65 kB minified and 250 kB gzip. Async PDF/XLSX
  chunks are excluded. Each optional route chunk must stay independently identifiable in the Vite
  manifest.
- The build gate enables Vite `build.manifest`. Starting at the manifest entry, it recursively
  visits `imports`, counts every emitted JS file once, sums emitted file bytes for the minified
  total, and sums the gzip size of each file for the gzip total. `dynamicImports`, PDF and XLSX are
  excluded from the initial graph and are reported separately.
- `simple-icons` barrel cost must be measured with a bundle report before deep-import changes. A
  source-level import appearance alone is insufficient evidence.

### P1-2: One query owner per resource

- A mounted surface has one refresh coordinator keyed by resource and effective parameters.
- Requests with the same key are deduplicated and single-flight. Consumers receive the same
  snapshot instead of starting parallel intervals.
- The normal visible baseline is five seconds. A resource may declare one faster active policy;
  Connectors authorization uses one-second refresh and replaces the five-second timer.
- The Connectors surface has one baseline refresh and, while authorization is pending, one faster
  refresh policy that replaces rather than stacks with the baseline.
- Inbox Configure shares routing, sessions, recent channels and unrouted snapshots across its
  cards. The parent Inbox poll must stop fetching resources delegated to the active Configure tab.
- Polling pauses while the surface is backgrounded unless the result is required for correctness.
  Plain browsers derive this from Page Visibility plus window focus; the Tauri shell additionally
  uses its native window-focus signal because WebView document visibility alone is not authoritative.
  Foreground, mutation events and WebSocket facts trigger immediate revalidation.
- A failed request keeps the last safe snapshot, exposes an appropriate existing error/empty state,
  and retries after 5, 10, 20 and at most 30 seconds. Focus, a successful mutation, or a trusted
  WebSocket fact immediately returns the resource to active validation.

### P2-1: Dependency and dead-surface cleanup

- Pure tool-argument presentation helpers move below the React component layer so
  `ApprovalCard.tsx <-> humanize.ts` becomes acyclic.
- A production file may be deleted only when TypeScript import search, Vite entry reachability and
  tests confirm it is unreachable. The initial candidates are `GalleryModal.tsx`,
  `PersonaHero.tsx`, `TodoPanel.tsx`, `brandIcons.tsx` and `connectors/CloudSignIn.tsx`.
- Exports used by tests or retained as a documented public module API are not dead merely because
  production traversal does not import them.

### P0-3: GUI release gate coverage

- The root release gate must cover the browser/desktop GUI when `manager/surfaces/gui` or
  GUI-facing specs change. At minimum, `make full-check` runs the full GUI unit suite and a
  production Vite build, or delegates to a root target that does so.
- The Makefile exposes explicit, composable targets:
  - `make gui-test`: runs `cd manager/surfaces/gui && npm test -- --run`;
  - `make gui-build`: runs `cd manager/surfaces/gui && npm run build`;
  - `make gui-check`: runs `gui-test` and `gui-build`;
  - `make gui-preview-smoke`: runs the production-preview browser smoke described in P0-4.
- `make full-check` includes `gui-check` by default. `gui-preview-smoke` may remain an explicit
  release/performance gate if it is too slow for every local full-check, but skipped preview smoke
  must be reported as `not_run` for GUI performance, routing or artifact-viewer changes.
- README, local skills and final delivery notes must describe the same gate split. No document may
  imply that Python-only `make full-check` is sufficient for GUI release readiness.

### P0-4: Reusable production-preview and artifact-viewer resilience

- Production-preview evidence is repository-owned, not an ad-hoc temporary script. The reusable
  command must build or prove freshness of `dist`, start `vite preview` on a strict local port, run
  focused Playwright specs, and clean up processes and temporary artifacts.
- The preview smoke covers at least rail default, first-click artifact opening, Inbox, Connectors,
  and lazy-route navigation. It records request counts, chunk names, console errors and long-task
  summary without prompt, transcript, credential, signed URL or tool-argument content.
- The artifact viewer uses an explicit state machine:

```text
idle -> loading(request_id, session_id, path)
loading current success -> ready(content)
loading current failure -> unavailable(error)
loading stale completion -> ignored
session/path switch -> cancelled -> loading(new_request)
```

- A failed `readArtifact` call must render a clear unavailable/download-only state instead of an
  indefinite loading placeholder. The state must retain safe metadata (`name`, `path`,
  `preview_status`, `download_status`) and must not fall back from a HaaS-bound artifact to local
  workspace reads.
- A slower read for a previous artifact, path or session must not overwrite the currently selected
  artifact. The viewer may use `AbortController`, request ids, or an equivalent guard.
- Remote HaaS artifacts continue to offer only protocol-safe actions: preview when readable,
  download when allowed, and no local reveal/open shell-out.

### P1-3: Complexity budget for session and artifact surfaces

- Refactors must reduce risk in small, behavior-preserving slices. A change that touches
  top-level session streaming, transcript replay, artifact viewing or API-client state must either
  keep the touched unit within the current complexity budget or extract one named behavior boundary
  with focused tests.
- The target boundaries are:
  - session WebSocket event reduction and terminal flush;
  - transcript replay mapping from persisted messages;
  - artifact viewer selection/read/download state;
  - route shell/lazy surface ownership; and
  - query refresh coordination.
- Fallow complexity, duplication and dead-code output is triage evidence only. A refactor target is
  accepted when the new boundary has stable observable tests and the old component no longer owns
  unrelated state transitions.
- No new feature may add another long-lived polling owner, top-level WebSocket branch, or artifact
  action path without declaring its owner and acceptance case in this spec or a more specific
  component spec.

## 6. Core Interfaces and Data Model

No public wire interface changes are required for the first implementation. Internal concepts are:

```ts
type QueryKey = readonly [
  resource: string,
  endpoint: string,
  canonicalParameters: string,
];
type RefreshMode = "idle" | "baseline" | "active" | "backoff" | "paused";

interface RefreshState<T> {
  key: QueryKey;
  data: T | undefined;
  mode: RefreshMode;
  inFlight: boolean;
  lastSuccessAt: number | null;
  consecutiveFailures: number;
}
```

The coordinator is an internal GUI mechanism owned by one in-memory API-client lifetime. It is not
persisted and is never shared across WebViews. When the API endpoint/token client is reconstructed,
the old coordinator is disposed: it cancels timers and in-flight ownership and clears all snapshots
before new consumers subscribe. An application/WebView reload therefore also creates a clean
coordinator. This contract does not require a new credential-generation API. Session ids and other
effective parameters are encoded in `canonicalParameters` with deterministic ordering. The
coordinator must not persist response bodies, credentials, authorization URLs or raw prompts.
Existing browser storage keys are unchanged.

## 7. Runtime Model and State Machine

Refresh state transitions are:

```text
mount/focus/mutation/event -> active -> request
request success           -> baseline
request failure           -> backoff
browser or native background/unmount -> paused
relevant event/focus      -> active
```

Only one `request` state may exist for a query key. An active fast interval replaces the baseline
timer; it does not create another timer. Unmount aborts or ignores completion safely.

Terminal reconciliation is separately scoped per session:

```text
healthy event flow -> dormant
disconnect/restored running/liveness conflict/integrity suspicion -> probing
non-terminal readback -> delayed probing
matching terminal readback -> reconciled -> dormant
session change/unmount -> cancelled
```

Event silence alone leaves a healthy transport in `dormant`. A qualifying trigger enters
`probing` immediately; only that state uses the 3/6/12/30-second retry sequence.

## 8. Security and Permissions

- Existing authentication, session scope, redaction and no-store behavior are unchanged.
- Shared caches are in-memory, scoped to one API-client/WebView lifetime, and keyed by endpoint plus
  canonical effective parameters. API-client reconstruction, endpoint change or WebView reload
  disposes the coordinator before new consumers subscribe; connector/account mutations invalidate
  their affected keys. No data may cross session or endpoint boundaries.
- Performance instrumentation records counts, timings and component labels only. It must not record
  prompt text, transcript content, tool arguments, credentials or signed URLs.
- Lazy loading must not create unauthenticated alternate routes or fetch protected data before the
  user opens the owning surface.

## 9. Observability

Development/test instrumentation must make these facts measurable without production content:

- request count and maximum concurrency per query key;
- full-transcript reconciliation count and trigger reason;
- React Profiler-boundary `onRender` count for the app shell, historical transcript and live turn
  during scripted deltas;
- Vite initial and async chunk minified/gzip sizes; and
- refresh state transitions and backoff under fake timers.

Production logging remains content-free. No always-on telemetry is introduced.

## 10. Failure, Recovery, Compatibility, and Rollback

- A lazy chunk load failure is caught outside the session shell, preserves the current session, and
  presents a retry action. One retry creates a fresh lazy loader attempt; a repeated failure offers a
  full application reload while persisted session identity remains recoverable. Tests inject an
  initial rejected import followed by success and assert both the error state and recovery.
- A refresh coordinator failure falls back to bounded polling, never to duplicate intervals.
- A GUI gate failure blocks release even when the Python sidecar gate passes. The failure is
  reported under the exact sub-gate (`gui-test`, `gui-build`, or `gui-preview-smoke`) rather than
  collapsed into a generic `full-check` failure.
- A production-preview smoke timeout cleans up its preview server and browser processes before
  returning failure.
- An artifact viewer read failure is recoverable by retrying the same artifact or selecting another
  artifact; it does not wedge the rail in a loading state.
- If event-driven reconciliation misses a terminal during rollout, one GUI-internal, default-off
  switch may temporarily restore periodic readback while retaining single-flight protection. Its
  activation records only a content-free reason. It must be removed after FV-33, healthy-silence and
  reconnect/missed-terminal packaged gates pass for the next release; it is not a second permanent
  recovery mode.
- Rollback may restore eager route imports without changing persisted data.
- ADK and HaaS native compatibility surfaces are unaffected. Manager-local endpoint additions, if
  later chosen, must be additive and separately specified.
- Session, artifact, credential, policy, MCP, container and event schemas are unaffected.

## 11. Test Plan and Acceptance

### 11.1 Baseline validation before implementation

The problem was accepted as objectively present after all applicable checks reproduced it on the
current checkout:

1. production Vite build records the entry and async chunks;
2. browser fixture counts requests for Connectors, Inbox Configure and a running session for at
   least two polling periods, including maximum concurrency;
3. diagnostic Fiber instrumentation sends at least 30 coalesced live updates with stable history
   and records render participation; implementation replaces this discovery probe with persistent,
   named React Profiler-boundary assertions;
4. import-graph analysis reproduces the cycle and entry-point reachability candidates; and
5. existing GUI unit tests pass before any implementation.

The observed results are recorded in section 2.1. If a later runtime observation contradicts static
analysis, the runtime result still wins and this spec must be updated before implementation.

### 11.2 TDD implementation order

1. Add failing healthy-silence, request-count, single-flight and terminal-reconciliation tests.
2. Implement the bounded recovery controller without changing wire behavior.
3. Add failing named React Profiler-boundary assertions, then isolate the live-turn state and
   stabilize props.
4. Implement the refresh coordinator with request budgets, background behavior and fake-timer
   backoff tests.
5. Add manifest graph and route-load assertions, then introduce route boundaries.
6. Break the import cycle independently; remove only confirmed unreachable files after route
   regression.
7. Run component, browser, production-build and packaged desktop regression gates.

### 11.3 Acceptance criteria

- A healthy WebSocket running silently for 60 seconds performs zero full-transcript fetches. Forced
  disconnect/missed-terminal recovery still passes FV-33; with 3.5-second read latency its maximum
  concurrency is one and it never resubmits work.
- After counters are reset following `turn_start`, thirty live projection publications with stable
  history produce zero `onRender` calls in named sidebar, inactive-route, composer and right-rail
  Profiler boundaries. The live-turn boundary updates, and historical Markdown render invocation
  count remains zero. Terminal flush is asserted separately.
- After the initial load settles and counters reset, a visible Connectors or Inbox Configure
  surface issues at most two scheduled reads per resource during a 10.5-second window, with maximum
  concurrency one and one timer policy per key. Mutation/event/focus revalidations are tagged and
  counted separately. Background surfaces issue zero ordinary polling requests; only an already
  probing recovery session may make its one final integrity read. Foreground resumes immediate
  revalidation.
- The production initial entry satisfies the P1-1 budget; PDF/XLSX previews still load on demand;
  each lazy surface opens successfully in browser and packaged-app smoke tests.
- The production import graph has no `ApprovalCard`/`humanize` cycle and reports no confirmed
  unreachable source file retained by this change.
- Root `make full-check` includes GUI unit and build gates. Focused production-preview Playwright is
  available as a stable command and is either run for GUI-facing changes or reported as `not_run`
  with residual risk.
- Artifact viewer read failures and stale async completions render deterministic unavailable or
  current-content states; no previous read overwrites a newer selection.
- Complexity work leaves the session streaming, transcript replay and artifact viewer behavior
  covered by focused tests before any large component extraction is accepted.
- `npm test -- --run`, `npm run build`, focused Playwright tests, `make pre-commit`, and for final
  integration `make full-check` pass.

## 12. Task Breakdown and Priority

### 12.1 Functional verification cases

| Case | Priority | Requirement coverage | Execution path | Pass criteria | Evidence |
|---|---|---|---|---|---|
| FV-GUI-PERF-01 | P0 | P0-1 healthy transport silence | Vitest renders `App`, starts a running turn over the real GUI `Session` abstraction, advances fake timers for 60 seconds without disconnect/reconnect/liveness conflict | `getSessionMessages` is not called after counters reset; no task is resubmitted | Vitest output and request counter assertion |
| FV-GUI-PERF-02 | P0 | P0-1 missed-terminal recovery and single-flight | Vitest or Playwright uses a running session with a qualifying recovery trigger, 3.5-second delayed `getSessionMessages`, and a transcript containing the latest matching terminal outcome | max full-transcript concurrency is one; terminal state clears running; outbound `user_message` count remains one | Vitest/Playwright output plus max-concurrency counter |
| FV-GUI-PERF-03 | P0 | P0-2 live projection isolation | Focused React test wraps named shell branches with Profiler boundaries and sends at least 30 assistant deltas after `turn_start` | sidebar, inactive-route, composer and right-rail boundaries record zero `onRender` after counters reset; live transcript updates and final flush keeps the last delta | Vitest output with Profiler counters |
| FV-GUI-PERF-04 | P1 | P1-2 shared refresh owner | Playwright opens Connectors and Inbox Configure in production preview with HTTP counters reset after settle | each resource has at most two scheduled reads in 10.5 seconds and max concurrency one; authorization fast policy replaces the baseline timer | Playwright output and per-resource counters |
| FV-GUI-PERF-05 | P1 | P1-1 route-level splitting | `npm run build` with Vite manifest enabled, then manifest traversal from the entry chunk | initial synchronous JS graph is <= 833.65 kB minified and <= 250 kB gzip; optional routes appear only under `dynamicImports`; PDF/XLSX remain async | build output and manifest budget script |
| FV-GUI-PERF-06 | P1 | P1-1 lazy route runtime behavior | Playwright production preview opens Settings, Integrations, Scheduled, Audit, Inbox and Persona surfaces | first route open succeeds, retry boundary survives one injected chunk failure, session shell is not destroyed | Playwright route smoke output |
| FV-GUI-PERF-07 | P2 | P2-1 import cycle cleanup | Fallow or repository import-graph check plus TypeScript build | no `ApprovalCard.tsx <-> humanize.ts` cycle; deleted candidates have no production or test import references | graph output, `npm run build`, `npm test -- --run` |
| FV-GUI-PERF-08 | P0 | P0-3 GUI release gate coverage | From the repository root, run `make gui-check` and `make full-check`; inspect `make -n full-check` or equivalent shell output | GUI unit suite and `npm run build` are part of the root release gate; a GUI build/test failure fails the gate before release closeout | Make output plus intentional local dry-run/fixture failure evidence when feasible |
| FV-GUI-PERF-09 | P0 | P0-4 reusable production-preview smoke | Remove or invalidate `manager/surfaces/gui/dist`, then run the stable preview smoke command | The command rebuilds or rejects stale `dist`, starts `vite preview`, runs focused Playwright, reports request/chunk/error metrics and cleans up the server | Command output, Playwright output and cleanup evidence |
| FV-GUI-PERF-10 | P0 | P0-4 artifact viewer state resilience | Vitest renders `RightRail`, opens one artifact, delays its read, switches to another artifact/session, then resolves both success and failure paths | stale completions are ignored; failed current reads render an explicit unavailable/download-only state; remote HaaS artifacts never reveal/open locally | Vitest output and mocked API call assertions |
| FV-GUI-PERF-11 | P1 | P1-3 complexity boundary budget | Fallow health plus focused tests for any extracted boundary touched by the change | no new large session/artifact owner is introduced without a named boundary and tests; extracted boundaries preserve observable behavior | Fallow summary, focused test output and changed-file review |

Functional validation uses only count, timing, route label and component label evidence. It must not store prompt text, transcript content, tool arguments, credentials or signed URLs.

### 12.2 OpenSpec task mapping

| Order | Priority | Task | Deliverable | Dependency |
|---|---|---|---|---|
| 1 | P0 | Reproduce and freeze request/render/bundle baselines | Focused tests and build budget | None |
| 2 | P0 | Bound terminal reconciliation | Single-flight event-triggered recovery | Task 1; FV-GUI-PERF-01, FV-GUI-PERF-02 |
| 3 | P0 | Isolate live projection renders | Live-turn state boundary and profiler gate | Task 1; FV-GUI-PERF-03 |
| 4 | P1 | Consolidate visible-surface queries | One coordinator and refresh policy per key | Task 1; FV-GUI-PERF-04 |
| 5 | P1 | Split optional routes | Manifest budget, lazy boundaries and retryable loading state | Task 1; FV-GUI-PERF-05, FV-GUI-PERF-06 |
| 6 | P2 | Break the import cycle | Pure helper module and acyclic graph | Task 1; FV-GUI-PERF-07 |
| 7 | P2 | Remove only confirmed dead surfaces | Per-file reachability and route/package evidence | Tasks 5-6; FV-GUI-PERF-07 |
| 8 | P0 | Wire GUI release gates into root Makefile and docs | `gui-test`, `gui-build`, `gui-check`, full-check integration and README/skill alignment | FV-GUI-PERF-08 |
| 9 | P0 | Add reusable production-preview smoke | Checked-in preview config/command with fresh-build guard and cleanup | FV-GUI-PERF-09 |
| 10 | P0 | Harden artifact viewer read state | Request guard, explicit unavailable state and remote-action constraints | FV-GUI-PERF-10 |
| 11 | P1 | Establish complexity-boundary refactor budget | Named session/transcript/artifact boundaries with tests for touched code | FV-GUI-PERF-11 |
| 12 | P0 | Regression and release review | Browser/package evidence and required reviews | Tasks 2-11; all FV-GUI-PERF cases |

Alignment review result: every P0/P1 requirement has at least one executable case, every task has
an acceptance reference, and no task requires public API/schema changes. The first implementation
slice is S1 bounded reconciliation because it removes duplicate full-transcript reads while
preserving FV-33 correctness.

### 12.3 Verification execution plan

Run the cases in this order:

1. `npm test -- --run src/App.lifecycle.test.tsx` for FV-GUI-PERF-01 and FV-GUI-PERF-02.
2. Focused React profiler unit tests for FV-GUI-PERF-03 after the live boundary is introduced.
3. `npm run build`, then production-preview Playwright counters for FV-GUI-PERF-04 through
   FV-GUI-PERF-06.
4. `make gui-check` and `make full-check` for FV-GUI-PERF-08.
5. Stable production-preview smoke for FV-GUI-PERF-09.
6. Focused `RightRail`/artifact viewer tests for FV-GUI-PERF-10.
7. Fallow health plus focused boundary tests for FV-GUI-PERF-11 when refactors touch the listed
   surfaces.
8. Import graph, `npm test -- --run`, `npm run build`, `make pre-commit` and final
   `make full-check` for FV-GUI-PERF-07 and release readiness.

## 13. Component Impact Analysis

| Component | Impact | Required action | Compatibility conclusion |
|---|---|---|---|
| Manager HaaS Sidecar Backend | Terminal readback scheduling and GUI projection ownership change; FV-33 semantics do not | Reuse the existing full-message endpoint and existing intent-occurrence guard | No API, event, session or persisted-binding change |
| Manager Product Identity | None; the GUI remains local-first and no-login | Keep the coordinator inside the current WebView/API-client lifetime | No cloud identity or login dependency is introduced |
| ADK and `/v1/haas/*` | None | No route or schema work in phase one | Fully unchanged |
| Session/event projection | Live React ownership moves, canonical event ordering does not | Preserve canonical refs, terminal flush and replay parity | Additive internal refactor only |
| Artifact viewer | Optional route loading may change; artifact protocol does not | Retain PDF/XLSX on-demand loading and first-click artifact behavior | Artifact URLs, metadata and security headers are unchanged |
| Root development gates | GUI evidence becomes part of root release readiness | Add GUI Makefile targets and align README/skills | No runtime compatibility impact; release signal becomes stricter |
| Production preview automation | Temporary performance scripts become a stable repo command | Add checked-in preview config/command with fresh-build guard and cleanup | No shipped code path changes |
| Frontend maintainability | Large session/artifact owners gain extraction budget | Refactor only behind focused tests and named boundaries | Behavior-preserving internal changes only |
| Credentials and redaction | In-memory snapshots gain a shared owner | Dispose on API-client/WebView replacement and keep instrumentation content-free | No credential value is cached, logged or persisted |
| Container, model proxy, MCP and skills | None | Run regression gates only | No runtime or protocol change |

## 14. Delivery Sequence and Estimate

| Stage | Estimate | Work | Entry dependency | Exit evidence |
|---|---:|---|---|---|
| S0 | 0.5 day | Freeze persistent request, Profiler and manifest helpers | This reviewed spec | Failing baseline gates reproduce section 2.1 |
| S1 | 1-1.5 days | Bounded reconciliation controller and FV-33 TDD | S0 | Healthy 60-second silence is zero-read; missed terminal is recovered single-flight |
| S2 | 1.5-2 days | Live-turn boundary and stable shell props | S0 | Named Profiler gates pass; terminal flush remains correct |
| S3 | 1.5-2 days | Query coordinator, visibility/focus and backoff | S0 | Request budgets, background and mutation tests pass |
| S4 | 1-1.5 days | Route splitting, manifest budget and retry boundary | S0 | Bundle budget and browser/package route smoke pass |
| S5 | 0.5-1 day | Break cycle and verify/delete dead candidates individually | S4 for deletion only | Acyclic graph and per-file evidence |
| S6 | 0.5-1 day | Root GUI release gates and preview smoke command | S0 | `gui-check` is included in `full-check`; preview smoke is reusable |
| S7 | 0.5-1 day | Artifact viewer explicit error/race state | S0 | stale completions ignored; unavailable state renders |
| S8 | 1-2 days | Complexity-boundary extractions for touched session/artifact code | S1-S7 as needed | Focused boundary tests pass; no new large owner added |
| S9 | 1 day | Full regression and required review gates | S1-S8 | `code-review`, `brooks-review`, `brooks-test`, packaged smoke and `make full-check` pass |

The expected engineering window is 8-11 days including approximately one day of risk buffer. P0
reconciliation, GUI release-gate wiring and artifact-viewer resilience remain independently
revertible. P1 complexity extraction does not block P0 correctness delivery, and dead-file deletion
must not delay a correctness fix.
