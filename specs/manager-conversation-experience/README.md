# Manager Conversation Experience Specification

**English** | [简体中文](README.zh-CN.md)

Status: MCX-001 through MCX-052 implemented; owner visual acceptance pending
Last reviewed: 2026-09-28
Change ID: `manager-conversation-interaction-v2`
Related specs: [Manager HaaS Sidecar Backend](../manager-haas-sidecar-backend/README.md), [Manager Project Workbench Experience](../manager-project-workspace-experience/README.md), [Manager Delegation](../manager-delegation/README.md), [Manager GUI Performance](../manager-gui-performance/README.md), [Event Log & SSE](../event-log-sse/README.md), [Session Runtime](../session-runtime/README.md), [Manager Product Identity](../manager-product-identity/README.md), [Security Boundary](../security-boundary/README.md)

## 1. Component Role and Product Priority

Manager Conversation Experience owns the end-to-end user experience for creating, running,
controlling, recovering, and reviewing an Agent task in OpenHarness. It defines the Manager-side
conversation projection, command admission feedback, per-session draft and follow-up queue,
standard React AI component boundaries, visual tokens, responsive behavior, accessibility, and the
incremental migration that replaces the current conversation implementation without leaving a
permanent legacy path.

The component priority is fixed and normative:

1. system and task-execution stability;
2. coherent, restrained, professional design quality;
3. obvious and forgiving interaction behavior;
4. parity with strong mainstream Agent task-management experiences;
5. feature breadth.

Later items MUST NOT weaken earlier items. A visually improved flow that can lose a draft, duplicate
a turn, misstate task progress, hide a recovery action, or regress long-session rendering does not
satisfy this specification.

### 1.1 Shared design authority

The [ZCode alignment delivery contract](ALIGNMENT.md) defines the full project scope, matched
comparison method, ordered slices, coverage, and native acceptance. It supplements rather than
replaces the requirements below.

[DESIGN.md](../../DESIGN.md) is the shared entry point for Manager UI product semantics,
component responsibilities, visual hierarchy, tokens, and interaction review. It distills the
ZCode reference into HaaS rules and maps them to this specification's acceptance cases.
This component spec remains authoritative for lifecycle, data, command, compatibility, and
acceptance contracts; `manager/surfaces/gui/src/styles.css` owns runtime token values.
GUI contributors MUST read DESIGN.md and the affected component specs before editing UI.
Changes to a shared design rule update both language variants and the affected spec before code.

Design compliance requires reusable component defaults, focused behavior checks, and visual
review of affected states in both themes. A document or green unit suite alone is not proof
of compliance. Do not claim repository-wide token enforcement or native validation without
corresponding evidence. Existing acceptance cases remain release requirements; this design
entry point does not declare their implementation complete.

Component impact: this governance and control styling delta affects Manager presentation only.
It introduces no ADK or HaaS native API, event, state transition, persistence, permission,
adapter, proxy, MCP/skill, container, or observability change. Diagnostic details remain subject
to the existing authorized-evidence and redaction contracts.

## 2. Evidence and Approved Direction

### 2.1 Current implementation evidence

The 2026-09-26 review of the current React/Vite Manager established the following baseline:

- the production GUI build succeeds and the entry chunk is 659.65 kB minified / 216.99 kB gzip;
- 19 focused Playwright cases pass for send/stream, approvals, retry, live-turn restore, Stop,
  stage display, scroll anchoring, and jump-to-latest;
- the current GUI provides one top-down transcript, compact task-named model stages, a composer-
  adjacent approval surface, an execution-evidence inspector, responsive 390/1440 layouts, and
  terminal readback recovery;
- `Composer` clears text and attachments immediately after invoking a void `onSend`, rather than
  after authoritative acceptance;
- switching sessions intentionally clears the unsent draft instead of preserving one draft per
  session;
- normal input is blocked while a turn is running, except where typed text is itself the answer to
  a specific proposal gate;
- `WsEvent.data` and several transcript fields are weakly typed, while `Transcript` reconstructs
  turn boundaries from adjacent UI item kinds;
- the current transcript renders all history blocks and has no bounded list virtualization;
- a live model stage can be visible while the summary says `Working · 0 activities`, because stage
  and tool counts have different sources;
- the composer textarea removes the native outline without an equivalent focus-visible indicator;
  and
- the declared faint text token measures 4.43:1 on the light canvas and 3.06:1 on the dark canvas,
  below the 4.5:1 requirement for the small text that consumes it.

### 2.2 Reference analysis

ZCode is a design and architecture reference, not a source-code dependency. Adopted ideas are:

- authoritative turn/row/work identities and a projection between transport facts and rendering;
- per-session durable drafts and acceptance-aware submission cleanup;
- a first-class queue for input accepted while work is busy;
- virtualized history separated from the non-virtual live tail;
- standardized conversation, turn, activity, interaction, queue, composer, status, and completion
  components;
- narrow selectors that prevent token deltas from re-rendering unrelated surfaces; and
- semantic visual tokens shared by light and dark themes.

HaaS does not adopt ZCode's repository structure, workflow graph, product-specific command system,
or large monolithic session/composer implementations.

### 2.3 Approved product direction

The approved direction is **Focused Workbench**:

- conversation remains the primary surface and reading order;
- current work is summarized in one compact, expandable activity area;
- background or cross-turn work and produced artifacts may use a secondary status surface;
- a follow-up queue attaches visually to the composer;
- blocking human interactions occupy one stable dock above the composer;
- detailed execution evidence opens in an inspector without replacing or duplicating the timeline;
  and
- narrow layouts progressively collapse secondary controls instead of shrinking every element.

### 2.4 Post-implementation product evidence and corrective decision

A 2026-09-26 packaged-desktop run exposed a design failure that functional, accessibility, and
performance gates did not catch. The running turn filled the primary viewport with eight peer
model-call cards. Each card repeated status, step count, command or internal commentary, and token
accounting; completed calls remained expanded while the next call ran. The user request and final
answer were displaced, an unavailable usage value was more visually prominent than current work,
and the timeline reported running while the Composer simultaneously offered Continue and Stop.
The assistant stream also changed containers after a word-count threshold, producing delayed text
appearance and layout movement.

The corrective product decision is normative:

- the primary projection unit is a **product turn**, never a model call;
- model-call boundaries and identifiers are correlation/evidence data and MUST NOT create peer
  timeline cards, headings, counts, or user-facing workflow phases;
- one turn owns one work summary, zero or more progressively disclosed work segments, at most one
  active interaction, and one stable assistant-response surface;
- assistant content occupies that response surface from its first classified user-visible delta
  until terminal sealing; word count, elapsed time, adjacency, or tool arrival MUST NOT relocate it;
- repeated or multi-model-call `assistant_message` facts for the same product turn update that
  response owner instead of appending another assistant row. An authoritative non-empty message
  replaces provisional text; a metadata-only message preserves the current text while updating
  usage, reasoning, evidence, and terminal state;
- timeline state, header state, loading treatment, and Composer controls consume one canonical
  presentation selector; and
- internal reasoning/commentary and absent accounting data are never promoted to titles, warnings,
  or primary status.

This is a projection correction, not a new conversation mode. The existing stage-card hierarchy,
word-threshold stream gate, and independent lifecycle selectors are transitional implementation
defects to remove, not compatibility behavior to preserve.

## 3. Goals, Non-goals, and Success Measures

### 3.1 Goals

1. Never lose accepted or unsent user intent during send, reconnect, session switch, refresh,
   backend restart, or configuration application.
2. Never execute one user submission more than once because of transport uncertainty or retry.
3. Render live, replayed, and restored conversation facts through one deterministic projection.
4. Let users continue composing while the Agent is busy and explicitly manage queued follow-ups.
5. Make running, waiting-for-user, paused, failed, recovering, and completed states distinguishable
   by text and structure, not color or motion alone.
6. Keep the main transcript calm and readable while retaining one-click access to technical detail.
7. Support 10,000 persisted rows with bounded DOM size and stable scroll/selection behavior.
8. Provide reusable, independently testable React AI components with narrow state subscriptions.
9. Ship equivalent behavior in browser production preview and the packaged Tauri desktop app.
10. Ship complete light and dark themes through one semantic token contract.
11. End migration with no permanent compatibility renderer, duplicate state owner, legacy CSS path,
    or long-lived feature flag.
12. Preserve the user's reading context by keeping work telemetry subordinate to intent and answer.
13. Keep the assistant response in one stable DOM and visual owner from first visible delta through
    completion.

### 3.2 Non-goals

- Replacing ADK 2.0 or `/v1/haas/*` as the northbound protocols.
- Exposing Codex app-server or another harness-native event to the GUI.
- Building ZCode's workflow graph, plugin system, multi-pane editor, or command vocabulary.
- Redesigning Settings, Connectors, Automations, Inbox, or artifact content renderers except where
  they share tokens or open from the conversation surface.
- Adding cloud identity, cloud draft synchronization, or cross-device draft synchronization.
- Keeping both old and new conversation implementations as supported user choices.
- Changing Lite/AIO container contracts, provider routing, or credential ownership.

### 3.3 Success measures

- zero lost-draft and duplicate-execution failures across the acceptance suite;
- every pending or failed state presents one explicit next action or a truthful terminal statement;
- no contradictory task status/count appears in a rendered frame;
- all supported interaction paths complete using keyboard only;
- light and dark theme text/control contrast passes WCAG 2.2 AA;
- 10,000-row fixtures keep the mounted conversation row count at or below 200;
- after counters reset, 30 live publications cause zero React commits in Sidebar, inactive routes,
  Composer, and closed Status/Inspector branches;
- one live publication is committed at most once per animation frame; and
- the final legacy-removal gate finds no imports, selectors, CSS hooks, tests, or flags owned only by
  the previous conversation path.
- a running turn renders no model-call card in the primary timeline and no internal
  reasoning/commentary text as an activity title;
- a first assistant delta becomes visible within one coalesced publication and remains in the same
  semantic response element through terminal sealing;
- one rendered frame never offers mutually exclusive lifecycle actions such as Continue and Stop;
  and
- expanding work is an explicit user action, remains stable during streaming, and never displaces
  the live answer above the reading anchor.

### 3.4 User and system scenarios

- A user writes a detailed request, switches to another session, restarts the desktop app, and returns. The exact draft and staged references return only in the original session.
- A user submits while the backend is slow. The UI shows submitting until durable acceptance; a lost ACK is reconciled without either losing the draft or running the task twice.
- A user types a follow-up during a long turn. The input becomes an editable queued item and runs after success, or remains paused after stop/failure until the user decides.
- A turn requests approval or structured input. One dock presents the decision, preserves the composer draft, prevents duplicate resolution, and restores correctly after reconnect.
- A user reads older output while tokens continue arriving. The viewport and text selection remain fixed until the user chooses Jump to latest.
- A user opens a 10,000-row session. Initial history, search/navigation, current work, and Composer remain responsive without rendering the whole transcript.
- A keyboard or screen-reader user can understand and operate the same lifecycle in both themes, including errors and recovery.
- Browser and packaged desktop clients consume the same Manager projection and produce equivalent task behavior even when desktop-only file/window capabilities differ.

## 4. Product and Interaction Principles

1. **Facts before decoration.** UI status comes from authoritative Manager/HaaS facts, never from a
   spinner, socket-open boolean, elapsed timer, or text heuristic.
2. **One fact, one owner, one primary surface.** Current turn work belongs to the timeline. Cross-
   turn/background work belongs to Status Panel. Pending human action belongs to Interaction Dock.
3. **Input is a durable user asset.** Drafts, attachments, and accepted commands are not disposable
   component state.
4. **Progressive disclosure.** The default view shows intent, present work, result, and recovery.
   Raw command, arguments, full output, and accounting detail are one deliberate action away.
5. **Stable geometry.** Streaming text may grow downward; it MUST NOT repeatedly insert, remove, or
   relocate large blocks above the user's reading anchor.
6. **Calm operational character.** Neutral surfaces, one accent, restrained status color, compact
   typography, and minimal motion take priority over decorative cards or gradients.
7. **Keyboard parity.** Every pointer action has a keyboard path; Escape stops only the focused
   conversation when stopping is allowed and yields to an open dialog or command palette.
8. **No silent fallback.** Remote/local routing, queue disposition, approval mode, retry, and
   recovery are explicit. A HaaS failure never silently executes locally.

### 4.1 Requirement inventory and delivery priority

P0, P1, and P2 define implementation order, not optional scope.

| Requirement | Priority | Contract |
|---|---|---|
| MCX-R01 Authoritative projection | P0 | One typed projection owns turn, row, work, interaction, queue, and outcome state |
| MCX-R02 Acknowledged submission | P0 | Persisted command receipt and idempotency precede draft cleanup and visible acceptance |
| MCX-R03 Durable drafts | P0 | Per-session drafts survive switch/refresh/restart and never enter telemetry |
| MCX-R04 Follow-up queue | P0 | Busy input is accepted into an operable, persisted, restart-safe queue |
| MCX-R05 Recovery integrity | P0 | Unknown acceptance, reconnect, gap, stop, and terminal mismatch converge without blind resend |
| MCX-R06 Accessible dual-theme foundation | P0 | Semantic tokens, WCAG AA contrast, focus visibility, keyboard flow, and reduced motion are release gates |
| MCX-R07 Standard React component cutover | P1 | Named AI components replace current monolithic rendering with one owner per fact |
| MCX-R08 Focused Workbench layout | P1 | One primary timeline, adaptive status surface, stable interaction dock, and responsive composer |
| MCX-R09 Bounded rendering | P1 | Virtual history, isolated live tail, stable selectors, and scroll anchoring meet performance budgets |
| MCX-R10 Completion and evidence | P1 | Final result stays primary while authoritative usage/artifacts and bounded evidence remain reachable |
| MCX-R11 Conversation navigation | P2 | Search, turn navigation, and Jump-to-latest use stable row/turn identities |
| MCX-R12 Semantic context display | P2 | Skills, files, sessions, and other supported context render as typed chips instead of raw syntax |
| MCX-R13 Legacy zero | P0 final gate | Old writers/renderers/styles/tests/flags are deleted after parity; no permanent dual path remains |
| MCX-R14 Product-turn projection | P0 | Model calls remain evidence metadata; one product turn owns one work summary and one assistant response |
| MCX-R15 Stable assistant stream | P0 | First user-visible delta mounts the final response owner; heuristics never hide or relocate content |
| MCX-R16 Canonical lifecycle presentation | P0 | Timeline, header, loading, interaction dock, and Composer actions use one selector and cannot contradict |
| MCX-R17 Safe work disclosure | P0 | Work is compact by default; the latest reasoning/tool action uses one safe transient line while tool details and telemetry use bounded semantic disclosures |
| MCX-R18 Motion and density discipline | P1 | One animation owner per state, stable geometry, semantic tokens, and measured information density govern the surface |

## 5. Information Architecture and Responsive Layout

### 5.1 Stable regions

| Region | Owns | Must not own |
|---|---|---|
| Session navigation | Session identity, attention, liveness, search/new task | Turn details, duplicate plan/todo content |
| Conversation header | Title, configured harness/persona, active model, workspace identity | Live tool status, transient token deltas |
| Conversation timeline | User inputs, turn work, assistant result, durable notices, completion summary | Cross-session inbox, duplicate background directory |
| Conversation status panel | Plan summary and cross-turn/background terminal, Agent, or workflow work | Current-turn tool rows already visible in timeline |
| Interaction dock | Exactly one active approval/input/policy/recovery request plus queued successors | Historical resolved interactions |
| Follow-up queue | Accepted but not started user submissions | Deployment admission queue or other users' work |
| Composer | Current per-session draft, attachments/context, mode/model controls, send/stop | Authoritative running state or accepted queue ownership |
| Evidence inspector | Selected activity's bounded detail and secure evidence read | A second transcript or raw unredacted event feed |

On wide layouts, Conversation Status Panel replaces the current rail's Progress section and shares
that rail with Artifacts, Files, and Access; it is not an additional fourth column. When the rail is
hidden or width is constrained, the same model appears as the compact capsule/drawer. The timeline,
rail, and capsule never render the same current-turn step list concurrently.

### 5.2 Width behavior

- At an available conversation width of 1200 CSS px or greater, the transcript remains centered and
  the Status Panel may expand inline to at most 320 px.
- From 760 through 1199 CSS px, Status Panel defaults to a compact capsule and opens as an anchored
  overlay or non-modal drawer without covering Composer or Interaction Dock.
- Below 760 CSS px, navigation and secondary status become drawers. Composer keeps attachment and
  the primary mode indicator visible. Model, microphone, and Send/Stop remain an inseparable
  trailing control cluster at every supported width; usage and other secondary actions yield or
  move before any member of that cluster disappears.
- The trailing cluster order is always `model -> microphone -> Send/Stop`, with one compact spacing
  token between peers and no wrapping. The model control is the only flexible member: it uses
  `min-width: 0` and trailing ellipsis for long localized/model labels. Microphone and Send/Stop
  retain fixed hit targets. Recording may replace the middle content area with a waveform, but the
  model, microphone/record-stop, and lifecycle action remain visible and adjacent.
- At 320 CSS px and 200% zoom, no required content or action is clipped, overlapped, or reachable
  only past an unhinted scroll edge.
- Breakpoints are container-based where the available chat width can differ from window width.

### 5.3 Reading order

The DOM and visual order is: header context, durable timeline, current live tail, follow-up queue,
pending interaction, composer. Status Panel and Inspector are complementary landmarks and MUST NOT
interrupt the timeline's semantic heading order.

### 5.4 State presentation matrix

| State | Primary visible treatment | Primary action | Secondary detail |
|---|---|---|---|
| empty/idle | Focused prompt and three task-relevant suggestions | Send | Harness/model/workspace in header or composer controls |
| submitting | Frozen user intent plus `Submitting` in composer dock | Stop waiting only when safe | No optimistic success or fabricated turn |
| running | One compact activity summary and live tail | Stop; Pause only when capability is available | Expand current work or open evidence |
| waiting for user | Interaction Dock replaces the active composer control surface | Decision-specific action | Bounded details and queued-request count |
| queued follow-up | Queue tray attached above Composer | Edit or leave queued | Reorder, delete, interrupt-and-send |
| paused | Persistent paused label with retained partial result | Continue | End task |
| recovering | Stable recovery banner; existing content remains readable | Retry readback/reconnect | Safe diagnostic reference |
| failed/incomplete | First actionable failure expanded near its turn | Retry/resume only when allowed | Evidence Inspector |
| completed | Final assistant result plus quiet completion footer | Continue conversation | Usage, artifacts, activity details |

Each state has at most one lifecycle command in the primary control slot. The slot is not
necessarily accent-filled: destructive commands use danger semantics and never borrow accent fill
merely because they are time-sensitive. No critical recovery action is hidden behind a disclosure.

### 5.5 Conversation measure and alignment

- User and assistant prose share one leading alignment edge and a readable maximum measure of
  72ch; code, tables, and evidence may scroll within their own bounded container rather than widen
  the transcript.
- Work summary, response, completion footer, notices, and errors align to that same conversation
  measure. Inspector and Status Panel use separate landmarks and do not create stray text edges in
  the timeline.
- Group spacing is at least twice item spacing. Space establishes hierarchy before a border or
  tinted surface is added.
- Text containers use minimum height and wrapping rather than fixed height. Pseudo-localized action
  labels and long safe summaries must wrap without moving the primary action beyond the viewport.
- Direction-dependent spacing uses logical properties. Mixed-direction paths, ids, commands, and
  counts preserve order through explicit `dir`/`bdi` handling where needed.

## 6. Authoritative State and Projection Model

### 6.1 Ownership

| Fact | Authoritative owner | Manager projection behavior |
|---|---|---|
| Session/invocation/turn terminal state | HaaS Session Runtime and canonical Event Log for delegated work; Manager engine for local work | Preserve source identity and normalize to one `ConversationTurnState` |
| Canonical event ordering | HaaS Event Log or Manager local journal | Deduplicate by source scope + event id and apply monotonically |
| Manager chat binding and command receipt | Manager backend | Persist before showing a submission as accepted |
| Follow-up queue | Manager backend | Persist ordered queue items; renderer never invents them |
| Pending approval/input request | Manager backend plus HaaS canonical request where delegated | Rebuild after reconnect before enabling conflicting input |
| Unsent draft | Manager `ConversationDraftStore` on the local device | Scope by endpoint/workspace/session; never send to telemetry |
| Disclosure, hover, selection, panel size | React local UI state | Never persisted as execution truth |

Manager persists command receipts and queue payloads transactionally in its existing local state
database. `conversation_commands` owns command identity, idempotency key, status, disposition,
turn/queue references, safe error code, and timestamps. `conversation_queue` owns ordered payload
references, delivery intent, revision, and drain policy. Acceptance plus the running/queued
disposition commits in one transaction before ACK. Raw prompt content is encrypted when the
existing Manager store offers encryption; otherwise it inherits the existing owner-only state-file
permissions and is still excluded from diagnostics and telemetry.

### 6.2 Conversation projection

The GUI consumes a typed `ConversationSnapshot` rather than mutable `Item[]` plus independent live
buffers. The internal TypeScript contract is:

```ts
type ConversationRunState =
  | "idle" | "submitting" | "running" | "pausing" | "paused"
  | "resuming" | "stopping" | "waiting" | "recovering"
  | "completed" | "failed" | "cancelled";

interface ConversationSnapshot {
  sessionId: string;
  revision: number;
  lastEventId: string | null;
  phase: ConversationRunState;
  turns: ConversationTurn[];
  activeTurnId: string | null;
  activeWork: WorkSummary[];
  backgroundWork: WorkSummary[];
  pendingInteractions: PendingInteraction[];
  pendingSubmission: SubmissionState | null;
  queue: FollowUpQueueItem[];
  outcome: ConversationOutcome | null;
}
```

Every `ConversationTurn` has a stable `turnId`, ordered stable `rowId` values, an optional
`invocationId`, explicit work segments, and at most one terminal outcome. Tool rows correlate by
`toolCallId`; interaction rows by `interactionId`; queued submissions by `queueItemId` and
`clientCommandId`. The renderer MUST NOT infer these identities from adjacent content, timestamps,
tool names, or display strings.

The product-facing turn shape is explicit:

```ts
interface ConversationTurn {
  turnId: string;
  invocationId: string | null;
  phase: ConversationRunState;
  userRows: ConversationRow[];
  work: TurnWorkProjection;
  assistantResponse: AssistantResponseProjection | null;
  interactionIds: string[];
  outcome: ConversationOutcome | null;
}

interface TurnWorkProjection {
  state: "idle" | "working" | "waiting" | "succeeded" |
    "failed" | "cancelled";
  safeSummary: string;
  startedAtMs: number | null;
  completedAtMs: number | null;
  segments: WorkSegment[];
  aggregate: {
    activityCount: number;
    durationMs?: number;
    usage?: AuthoritativeUsage;
    artifactCount?: number;
  };
}

interface WorkSegment {
  segmentId: string;
  kind: "reasoning" | "tool" | "progress" | "recovery";
  state: "pending" | "running" | "succeeded" | "failed" | "cancelled";
  safeTitle: string;
  safeSummary?: string;
  activityRefs: string[];
  evidenceRef?: string;
}

interface AssistantResponseProjection {
  rowId: string;
  text: string;
  state: "streaming" | "sealed" | "interrupted";
  firstVisibleDeltaAtMs: number;
}
```

`segmentId` is a Manager product identity. A segment may correlate to one or many model calls or
tool calls, but `modelCallId`, provider request id, native reasoning item id, and token-usage
arrival boundaries are never display identities. They remain private correlation metadata behind
the evidence adapter. Splitting or combining model calls therefore cannot change the primary
timeline shape.

The remaining persisted Manager records are explicit and versioned:

```ts
interface ConversationDraftRecord {
  schemaVersion: 1;
  scopeKey: string;
  revision: number;
  text: string;
  editorState?: string;
  attachmentRefs: string[];
  contextRefs: string[];
  context?: ContextReference[];
  skill?: { name: string; description: string; scope: "global" | "project"; enabled: boolean };
  model?: string;
  mode?: string;
  updatedAtMs: number;
}

interface ContextReference {
  kind: "skill" | "file" | "session";
  id: string;
  label: string;
  path?: string;
  unavailable?: boolean;
}

interface FollowUpQueueItem {
  queueItemId: string;
  clientCommandId: string;
  position: number;
  state: "queued" | "dispatching" | "running";
  requestedDelivery: "start_now" | "enqueue" | "interrupt_then_start";
  safePreview: string;
  attachmentCount: number;
  contextCount: number;
  revision: number;
  createdAtMs: number;
}
```

Raw queued content remains in the Manager's scoped local store and is never returned by list,
diagnostic, metric, or notification APIs. `safePreview` is a local display projection, not a log
field.

HaaS canonical `eventId`, `sequenceNumber`, `sessionId`, `turnId`, `invocationId`, and `toolCallId`
remain authoritative. Manager-local execution must project equivalent identities without exposing
runtime-native details. Unknown events increment a content-free diagnostic and remain invisible;
they never become assistant output or guessed success.


Manager persists `_managerTurnId` and `_managerRowId` as display-only message sidecars and projects them as `turnId`/`rowId` on its internal WebSocket. These fields never enter model input. The single history migration adapter assigns deterministic identities to older records at durable user/connector intent boundaries. Model-call boundaries never allocate product turns. The last completed turn stays in the live-tail slot until a subsequent turn starts, preserving its response DOM during sealing.

### 6.3 Projection update rules

- Initial load applies one bounded snapshot, then replay events after `lastEventId`, then live data.
- An event already applied by `eventId` is a no-op.
- A lower revision cannot replace a higher revision.
- Text deltas append once in sequence order; final text seals the live row and does not create a
  second answer.
- The first canonical user-visible assistant delta creates `assistantResponse`; later deltas mutate
  only its text/state. It is never held behind a word, time, activity, or adjacency threshold and
  never moves between work, narration, and answer containers.
- User-visible answer, user-visible progress, internal reasoning, and tool evidence are classified
  by typed transport facts. When the source cannot prove a subtype, safe assistant text is treated
  as answer content; the GUI does not infer a subtype from prose.
- Model-call start/finish and usage updates may update evidence correlation and aggregate facts but
  MUST NOT append primary timeline rows or toggle disclosure.
- One started work item receives at most one terminal state.
- Terminal state cannot return to running without a new `turnId`/`invocationId`.
- A terminal parent turn is authoritative over incomplete child snapshots during live sealing and
  historical replay. A `running`, `pending`, or `waiting` activity without its own terminal event
  normalizes to failed for completed/failed/incomplete turns, with the existing safe missing-event
  reason, or to cancelled for a cancelled turn; completion never fabricates tool success. A stale
  running model stage normalizes to completed only when the parent completed, otherwise to the
  parent's failed/cancelled terminal state. Persisted source evidence remains unchanged, and
  non-terminal/paused turns retain their actual child state.
- A reconnect `ready` snapshot with `running=false` and idle/cancelled execution control MUST NOT
  revive a terminal transcript merely because an older persisted task outcome still says running.
  After history loads, its terminal outcome wins regardless of ready/history arrival order. Only
  `running=true`, an active non-idle control state, or a new turn identity may reopen execution.
- Pending interactions are restored before Composer enables conflicting commands.
- Reconciliation replaces uncertain derived state only with an authoritative snapshot or event
  page; it never resubmits the original user command automatically.

### 6.4 Canonical presentation selector

One pure selector derives `ConversationPresentation` from the authoritative snapshot and pending
command receipt. Conversation header, Turn status, loading slot, Interaction Dock, and Composer
MUST consume this result rather than independently interpreting `running`, `taskPhase`, model
stages, socket state, or local button state.

```ts
interface ConversationPresentation {
  phase: ConversationRunState;
  statusLabel: string;
  primaryAction: "send" | "stop" | "continue" | "resolve" | "retry" | null;
  secondaryActions: Array<"pause" | "end_task" | "discard" | "open_evidence">;
  composerMode: "compose" | "queue" | "blocked";
  showWorkingIndicator: boolean;
}
```

The selector enforces these invariants:

- exactly zero or one primary action is visible;
- `continue` is available only in authoritative `paused` state;
- `stop` is available only while submitting, running, pausing, resuming, or stopping remains
  cancellable. In paused state, `continue` is primary and permanent termination is a separately
  labelled secondary danger action, never a peer `Stop` button;
- an active interaction owns the primary action and suppresses ordinary loading duplication;
- socket disconnection alone cannot change a terminal turn back to running; and
- phase changes update all consuming surfaces in the same React commit.

Composer lifecycle controls use a quiet toolbar treatment (MCX-031/015): Pause is a neutral
text action and Stop is a 32 px icon button with a square glyph, localized accessible name,
and tooltip. Neither has a resting border, filled accent/danger background, glow, or spinner.
Continue and End task use the same compact text-control family in the paused state; permanent
termination remains explicitly labelled. Processing/transition labels are non-interactive
secondary text. Hover adds only a neutral surface, while keyboard focus remains visible.
Reuse the canonical selector, handlers, capability gates and disconnected/transition disabling;
this changes no lifecycle semantics. Verify callbacks and running/paused/transition controls,
plus browser visibility and focus at 390/1440 px in both themes. No public API, event, persistence,
permission, adapter, container, or observability component is affected.

### 6.5 Live-tail lifecycle

The last active product turn is rendered as one non-virtual live tail. Historical turns remain in
the bounded virtual list. On terminal sealing, the whole turn projection is atomically transferred
to history without changing its `turnId`, row identities, disclosure preference, or assistant
response element semantics. A new turn creates a new live tail; it never reuses the previous
turn's response node.

## 7. Submission, Draft, and Follow-up Queue Contracts

### 7.1 Per-session draft

`ConversationDraftStore` persists text, editor structure, attachment references, selected context,
and the intended model/mode for each `(endpointId, workspaceIdentity, sessionId-or-draftId)`.

The browser implementation uses origin-scoped IndexedDB; Tauri uses the same interface and may use
the Manager local store when available. Raw draft content MUST NOT be placed in `localStorage`.
Each draft is limited to 256 KiB of text/editor metadata and eight attachment references. Oversize
content remains editable in memory and produces an explicit local-persistence warning; it is never
silently truncated. Orphan draft scopes expire after 30 days, while drafts for existing sessions
remain until send, explicit discard, or session deletion.

- Writes are debounced no longer than 500 ms and flushed on session switch, page hide, and app
  close when the platform permits.
- Draft text and attachment names/refs are local-only, excluded from logs, metrics, traces, crash
  reports, and analytics.
- Attachment bytes remain in the existing bounded staging owner; a draft stores opaque local refs,
  not duplicate payloads.
- A successful accepted/duplicate receipt clears only the frozen submission revision. Text or
  attachments added while awaiting the receipt remain in the next draft.
- An explicit server rejection or a client-side validation failure restores the frozen draft.
- A timeout, disconnect, or any other result that does not prove rejection enters `reconciling`;
  the frozen draft remains visible but cannot be submitted again until the Manager queries the
  command receipt. The Manager MUST NOT automatically resend.
- Session deletion clears its draft and staged attachments. Other drafts remain untouched.

### 7.2 Manager internal command envelope

The Manager WebSocket command is extended additively during migration:

```json
{
  "type": "user_message",
  "clientCommandId": "cmd_...",
  "idempotencyKey": "idem_...",
  "delivery": "start_now",
  "text": "synthetic example",
  "attachmentRefs": [],
  "model": "provider:model",
  "skill": null
}
```

`delivery` is `start_now`, `enqueue`, or `interrupt_then_start`. `steer` is deferred until every
supported backend exposes an honest, tested steering capability; it MUST NOT alias enqueue.

The Manager returns:

```json
{
  "type": "command_ack",
  "clientCommandId": "cmd_...",
  "status": "accepted",
  "disposition": "running",
  "turnId": "turn_...",
  "queueItemId": null,
  "outcomeRef": null,
  "error": null
}
```

`status` is `accepted`, `duplicate`, or `rejected`. `disposition` is `running`, `queued`, or
`terminal` for an accepted/duplicate submission; a duplicate of completed work returns the original
turn identity and terminal `outcomeRef`. A rejected response contains a structured safe error and no
turn or queue identity. Acceptance is persisted before ACK. HaaS-backed execution reuses the existing
operation-scoped `Idempotency-Key`; the GUI never generates a second HaaS attempt to resolve an
uncertain Manager transport result.

Receipt reconciliation uses the Manager-internal, content-free readback
`GET /v1/sessions/{sessionId}/conversation-commands/{idempotencyKey}`. A found receipt returns the
original command/turn/queue/outcome identities with `status=duplicate`; `404 command_not_found` is
the only readback that proves the command was not accepted. The readback never returns prompt or
attachment content.

The initial client/server handshake advertises `conversationProtocolVersion: 2`. A mismatched
cached browser client receives a structured `client_upgrade_required` response and reload action;
the server does not silently downgrade to the unacknowledged sender. Hashed production assets and
packaged-app atomic replacement make this a bounded deployment transition, not a permanent legacy
protocol.

`error`, when present, is `{code, safeMessage, retryable, recoveryAction?}`. It never contains raw
prompt, full tool arguments, credential material, signed URLs, or backend stack traces. Queue
mutation commands carry `queueItemId`, `expectedRevision`, and their own idempotency key;
stale revision returns a structured conflict plus the latest queue snapshot.
The command set is `queue_edit`, `queue_delete`, `queue_move(targetPosition)`, and
`queue_send_now`. A failed stop during send-now returns `queue_send_now_failed` and leaves the
item queued without enabling automatic drain.

Submission state transitions are:

```text
draft -> submitting -> running -> terminal
                    -> queued -> dispatching -> running -> terminal
                    -> terminal (duplicate receipt of completed work)
                    -> rejected -> draft_restored
                    -> reconciling -> running | queued | terminal | rejected
```

`submitting` and `reconciling` retain the frozen submission. Only `accepted` or `duplicate` may
enter `running`/`queued`; only `rejected` may restore directly. A timeout by itself is never proof of
rejection.

### 7.3 Follow-up queue

- Users may compose while a turn is running.
- Default Enter follows the configured delivery policy. The default policy is `enqueue` while busy
  and `start_now` while idle.
- A visible modifier hint offers `interrupt_then_start`; the destructive consequence is stated in
  full and never triggered by an undisclosed shortcut.
- Queue items show ordered position, concise content preview, attachment/context count, and state.
- Queued items may be edited back into Composer, deleted, reordered, or sent now. Only queued items
  are mutable; dispatching/running items are locked.
- `send now` atomically stops the active foreground turn and starts the selected item. Failure to
  stop keeps the item queued and shows a recovery action.
- Stop or failure pauses automatic queue drain by default. The user explicitly resumes or sends one
  selected item. Successful completion drains according to the stored policy.
- Queue order and mutation receipts survive Manager restart. The GUI does not use optimistic order
  as authority after reconnect.
- This user follow-up queue is distinct from HaaS deployment Admission Control and workspace-writer
  queues. UI copy MUST NOT merge those concepts.

Queue item state transitions are:

```text
queued -> editing -> queued
queued -> deleted
queued -> dispatching -> running -> terminal
                      -> queued (pre-start stop/admission failure)
```

`dispatching` is immutable. A failure after execution acceptance belongs to the running turn and
does not recreate the queue item.

## 8. Turn, Work, Interaction, and Completion Behavior

### 8.1 Turn presentation

Each visible turn follows one stable order:

1. user intent and structured context chips;
2. compact current/completed activity summary;
3. progressively disclosed tool activity and execution evidence;
4. final assistant result;
5. completion summary and durable recovery actions.

Normal completed work defaults collapsed. Running work shows one compact active summary. Failed,
interrupted, or incomplete work defaults expanded to the first actionable failure. User selection
and disclosure remain stable during live updates.

The default running Turn has a strict visual budget:

- one user-intent block;
- one single-line work summary with state text and optional disclosure control;
- at most one currently relevant tool or first actionable failure when immediate awareness/action
  is required; and
- one assistant-response block that appears with the first visible delta.

Completed model calls, provider rounds, reasoning chunks, usage arrival, and cache accounting do
not consume peer cards in this default flow. Expanding work adds detail below the work summary and
above the answer without replacing either. A live update cannot automatically reopen a disclosure
that the user closed or close one the user opened. The completed state may auto-collapse only a
never-touched disclosure.

Expanded work preserves the canonical occurrence order of user-relevant tool activities. Reasoning
summaries never accumulate as historical disclosure rows. While a turn is active, only the latest
safe reasoning or tool summary may replace the work header's single-line current-action label; the
next step replaces it in place. Model-call cards and ordinals remain absent from the primary timeline.

### 8.2 Activity summaries and evidence

- The summary title prioritizes an explicit localized product action, safe tool/object summary,
  bounded command preview, then a localized neutral fallback.
- Raw reasoning, raw commentary, model/provider prose, prompts, arguments, stack traces, transport
  names, and model-call ordinals MUST NOT be used as a title fallback.
- One count covers product work segments or activities. Model-call count and reasoning-chunk count
  are diagnostics and never appear as task progress.
- The running summary does not lead with a numeric count. An optional completed aggregate belongs
  in the quiet completion footer or expanded detail.
- Running state includes a persistent text label. Motion is optional and disabled under reduced
  motion.
- Activity rows show status, safe title, optional key result, and duration. Raw arguments never
  appear by default.
- Selecting an activity row toggles one inline detail disclosure immediately below that row. It
  uses the existing secure evidence path and preserves focus/scroll context; it MUST NOT open a
  right-side Inspector or bottom drawer. Model-call evidence follows the same inline disclosure
  rule when explicitly requested.
- Expanded work rows retain the backend-provided safe command preview or object summary, so
  multiple commands can be distinguished. Inspector shows that preview immediately, including
  while evidence loads or is absent, expired, or unavailable. Complete commands still come only
  from authorized evidence; raw tool arguments are never serialized as a fallback. Inspector
  selection follows the stable activity id, so status, output, and late evidence references update
  without closing/reopening. MCX-013 regression covers preview-only, evidence expiry/failure, and
  running-to-terminal updates while details stay open; implement projection selection and preview
  rendering before refreshing browser baselines. The explicit local-engine path supplies the same
  safe command preview and stable tool identity through the Manager display boundary described in
  the sidecar spec. HaaS/ADK, tool execution permissions, and persistence formats remain unchanged.

Public activity previews do not expose absolute host paths. When command output contains the
owning command's authorized working directory, the adapter substitutes `workspace/` plus a safe
relative suffix before credential/URL/path redaction. Thus `pwd` at the workspace root displays
`workspace/`, while paths outside that root remain `[REDACTED_PATH]`. Exact paths remain available
only through scoped, unexpired execution evidence.

Reasoning is transient progress, not durable transcript chrome. While a turn is active, the latest
safe reasoning summary may occupy the single-line current-action label and is replaced in place by
the next reasoning or tool step. Reasoning rows and their disclosure state are not rendered in
completed, failed, cancelled, or paused history. The final assistant response remains the last
substantive content in the turn.

Tool work uses a shared `ToolActivity` contract with header, safe input summary, bounded result,
status, duration, and evidence action. Only the active tool or first actionable failure may default
open. Completed successful tools default collapsed. Tool input/output and evidence are not nested
inside model-call cards.

### 8.3 Stable assistant response

- The assistant response has one `rowId`, one semantic article/container, and one insertion point
  for its entire lifecycle.
- The first user-visible text delta renders in that container after the next coalesced publication;
  there is no minimum word count or intentional 1-2 second hold.
- The work summary may update beside/above it, but answer text never moves from a loading area or
  activity area into a separate bubble.
- Terminal text seals the same node. Replayed/restored text produces the same semantic DOM and
  ordering as a live turn.
- When no answer delta exists, one compact 16 px status slot may show `Working`; when the first
  answer delta arrives, that redundant loading indicator is removed without adding a second status
  announcement.
- Streaming cursor treatment is optional decoration. It cannot be the only indication of streaming
  and is disabled when reduced motion is requested.

### 8.4 Pending interaction dock

Approval, input request, directory permission, tool installation, plan/team/item proposal, policy
conflict, and recoverable execution failure use one `PendingInteractionDock` shell.

- Only one request is primary; later requests are counted and queued.
- The title states the requested decision; primary buttons repeat the consequence.
- Details are bounded and expandable. Outbound or destructive actions receive distinct treatment.
- Resolving a request locks duplicate actions until the authoritative result arrives.
- While a request is primary, the normal Composer remains mounted so its draft survives, but is
  visually hidden and `inert`. Free-text feedback is entered in a dock-owned response field; it
  never consumes or overwrites the normal draft.
- Focus moves to the dock heading on arrival when appropriate and returns to the initiating control
  or Composer after resolution.
- An error names a recovery action when one exists. Terminal non-retryable errors say that the task
  cannot continue rather than presenting a dead Retry button.

### 8.5 Persistent mode context and durable notices

Execution modes such as bypass approvals, sandbox/runtime identity, and policy posture belong to a
persistent header or Composer context chip. They do not render as large timeline notices on every
turn. A timeline notice is reserved for a durable state transition that changes the interpretation
of prior or later work; it is compact, left-aligned with the conversation measure, and never
becomes the primary visual block.

### 8.6 Completion summary

A completed turn may show one quiet footer containing duration, activity count, token usage when
authoritative, changed/produced artifact count, and a direct artifact action. Missing usage is
omitted in the footer; it is not rendered as a persistent warning. The final assistant result
remains the dominant content.

Per-model-call input/output/reasoning/cache usage belongs to Evidence Inspector only. Aggregate
turn usage may appear in the completion footer when authoritative. `Pending`, `unreported`, zero,
and missing are distinct facts; missing or pending accounting is silently omitted from ordinary
conversation layout unless it blocks billing, policy, or task completion.

### 8.7 Lifecycle vocabulary and product copy

Lifecycle copy uses one sentence-case vocabulary across timeline, header, Composer, notifications,
and accessibility names: `Working`, `Waiting for approval`, `Waiting for input`, `Pausing`,
`Paused`, `Continuing`, `Stopping`, `Completed`, `Failed`, and `Cancelled`. Localized strings convey
the same distinction; labels are not assembled from fragments around counts.

Action labels are verb-first and unambiguous: `Send`, `Stop`, `Pause`, `Continue`, `End task`,
`Retry`, `Reconnect`, `Show work`, `Hide work`, and `Open evidence`. `Continue` means resume an
authoritatively paused task only. Consequential confirmation repeats the consequence. Errors state
the safe cause and next available action; no actionable error ends with only `Something went
wrong`. Missing telemetry never uses warning language.

## 9. Standard React AI Component Architecture

The target source layout is:

```text
manager/surfaces/gui/src/conversation/
  model/          # typed snapshot, turn/work/interaction/queue models
  transport/      # Manager HTTP/WS adapter and command receipts
  store/          # projection reducer, draft store, selectors
  hooks/          # narrow state and command hooks
  components/
    ConversationShell.tsx
    ConversationTimeline.tsx
    TurnGroup.tsx
    TurnWorkSummary.tsx
    WorkDisclosure.tsx
    ReasoningDisclosure.tsx
    ToolActivity.tsx
    AssistantResponse.tsx
    PendingInteractionDock.tsx
    FollowUpQueue.tsx
    ConversationComposer.tsx
    ConversationStatusPanel.tsx
    TurnCompletion.tsx
    EvidenceInspector.tsx
  tokens/
    conversation.css
```

Component rules:

- presentational components do not open WebSockets, call REST endpoints, mutate canonical stores,
  inspect backend type, or translate transport payloads;
- transport adapters emit typed facts; one reducer owns projection writes;
- the projection store uses React's `useSyncExternalStore` contract behind repository-owned hooks;
  a new general state-framework dependency is not introduced for this component;
- hooks expose minimal selector results and stable command callbacks;
- exported components have explicit props, loading/empty/error/disabled states, keyboard behavior,
  accessible names, and a focused test;
- component names describe product semantics, not one harness or current visual treatment;
- tool-specific rendering uses a registry keyed by canonical activity kind, with a safe generic
  fallback;
- `TurnGroup` owns ordering only. `TurnWorkSummary`, `WorkDisclosure`, `ReasoningDisclosure`,
  `ToolActivity`, and `AssistantResponse` each receive already projected product props and never
  inspect model-call arrays;
- no exported or internal primary-timeline component is keyed, named, or visually grouped by
  `modelCallId`; model-call correlation is restricted to the evidence adapter/Inspector;
- `AssistantResponse` stays mounted from first visible delta through sealing and preserves the
  same accessible name, semantic role, and row identity;
- disclosure preference is keyed by `turnId + disclosureKind`, is not reset by token/stage updates,
  and is discarded only with the owning turn/session lifecycle;
- `React.memo` is used only with stable inputs and measured benefit; callbacks and collection props
  crossing live boundaries remain referentially stable;
- context providers are split by update rate. Token-level/live projection state never shares a
  provider value with shell navigation, Composer draft, or closed panels;
- `ConversationTimeline` uses `@tanstack/react-virtual` behind a repository-owned adapter so row
  measuring, selection anchoring, and library replacement remain localized;
- the running live tail is outside the virtual history until sealed, preventing token deltas from
  invalidating the historical measurement cache; and
- all public component behavior is tested through semantics and user actions, not internal class
  names.

## 10. Visual Token and Theme Contract

### 10.1 Product character

The conversation surface is calm, dense, operational, and desktop-native. It uses neutral
background hierarchy, restrained borders and shadows, one cobalt interaction accent, and semantic
status colors. It avoids marketing-scale whitespace, decorative gradients, excessive card nesting,
or color used merely to create emphasis.

### 10.2 Tokens

Components consume semantic tokens only:

- surfaces: `--color-canvas`, `--color-chrome`, `--color-surface`,
  `--color-surface-raised`, `--color-popover`;
- text: `--color-text-primary`, `--color-text-secondary`, `--color-text-tertiary`,
  `--color-text-inverse`;
- structure: `--color-border`, `--color-border-strong`, `--color-focus-ring`;
- interaction: `--color-accent`, `--color-accent-hover`, `--color-accent-soft`;
- status: `--color-success`, `--color-warning`, `--color-danger`, plus matching surface/text roles;
- typography: `--text-title`, `--text-heading`, `--text-body`, `--text-ui`, `--text-mono`,
  `--text-caption`;
- spacing: a 4 px base scale, with group spacing at least twice its internal item spacing;
- radius: surface 12 px, nested surface/control 8 px, compact 6 px, pill 999 px; and
- motion: high-frequency feedback at 150 ms or less, explicit transitioned properties, no layout
  animation during token streaming.

Raw colors, arbitrary font sizes, and new one-off radius/shadow values are prohibited in migrated
conversation components. Existing values migrate to tokens before their component cutover.

The initial light/dark baseline is fixed below. Changes require contrast measurement and paired
visual-regression approval rather than component-local overrides.

| Role | Light | Dark |
|---|---|---|
| canvas | `#FAFBFC` | `#191B1F` |
| chrome | `#F4F6F8` | `#15171A` |
| surface | `#FFFFFF` | `#1F2227` |
| surface-raised / popover | `#FFFFFF` | `#272B31` |
| text-primary | `#17191C` | `#EEF0F3` |
| text-secondary | `#4D535C` | `#B4BAC3` |
| text-tertiary | `#626A73` | `#A0A7B0` |
| accent / focus | `#2563EB` | `#78A8FF` |
| success | `#217A50` | `#70D6A2` |
| warning | `#8A4B08` | `#F0B35D` |
| danger | `#B42318` | `#FF8A80` |

Typography roles are equally fixed at the initial baseline: title 20/25 px weight 600, heading
16/22 px weight 600, body 14/22 px weight 400, UI 13/18 px weight 400, mono 12/19 px weight 400,
and caption 11/15 px weight 600. Mobile editable input text remains 16 px. Font families remain the
bundled Inter and JetBrains Mono assets; the product wordmark remains the only Manrope use.

Conversation density follows these additional rules:

- primary assistant and user content uses `body`; controls/status use `ui`; `caption` is limited to
  genuinely secondary metadata and never carries required recovery or lifecycle information;
- one semantic level does not combine more than one enclosing border and one nested divider;
- successful/completed work does not receive a tinted full-card background;
- metadata wraps below content on constrained widths rather than reserving a competing fixed
  column; and
- raw hex/RGB colors, arbitrary `text-[Npx]`, and one-off shadows/radii are release-blocking in
  migrated conversation components.
- changing duration, count, and usage values use tabular numerals; primary prose and evidence text
  remain selectable; truncation always exposes the full safe value through disclosure or Inspector.
- hover-only treatment is gated by pointer capability, transitions name exact properties rather
  than `all`, and theme switching suppresses color/background/border/shadow transitions for the
  swap frame.

Motion has one owner per state:

| State/change | Allowed motion owner | Prohibited competing motion |
|---|---|---|
| Waiting before first answer delta | Compact working indicator | Pulsing card, animated border, and background gradient |
| Streaming answer | Optional terminal cursor/fade | Relocating answer container or animating layout above it |
| Tool running | Tool status glyph when expanded | Whole-card shimmer or multiple stage spinners |
| Disclosure open/close | Height/opacity transition up to 180 ms | Spring/bounce and scroll-anchor movement |
| Turn completion | One status cross-fade up to 150 ms | Persistent glow or completed-item animation |
| Reduced motion | No nonessential motion | State that is understandable only through animation |

Adding a second simultaneous animation for the same state is a spec violation even when each
animation individually uses a valid token.

Visual-regression fixtures cover empty/idle, running with tools, waiting for approval, queued
follow-up, recoverable failure, completed-with-artifacts, and long-content states at 390 and 1440 px
in both themes. A 320 px / 200% zoom functional pass supplements the screenshot set. Review rejects
duplicate facts, more than one filled primary action in a state, inaccessible truncation, arbitrary
tokens, or a layout shift that changes the reading anchor.

### 10.3 Themes and accessibility

- Light and dark themes implement the same complete semantic token set. Missing dark tokens cannot
  fall back to light values.
- Normal text below 18 pt passes 4.5:1; large text passes 3:1; focus and non-text UI indicators pass
  3:1 against adjacent colors.
- All interactive elements use native semantics where available and a visible 2 px minimum
  `:focus-visible` indicator. The text Composer uses its enclosing 1 px neutral focus border
  plus the native caret instead; its controls retain individual keyboard focus indicators.
  Composer focus must not add a brand-colored ring, glow, background tint, or shadow. Use one
  shared semantic border token with explicit light/dark values and at least 3:1 contrast against
  adjacent surfaces; focus must not move the layout. MCX-015 visual checks cover idle/editing
  states and keyboard navigation at 390/1440 px in both themes. This is a presentation-only
  correction with no change to input delivery, persistence, permissions, events, or public APIs.
- Controls have a minimum 24×24 CSS px target and aim for 40×40 on desktop where density permits.
- State is never communicated by color or motion alone.
- Dynamic status announcements use one stable polite live region and publish only meaningful phase
  transitions; token, timer, and progress increments are not announced individually.
- `prefers-reduced-motion` removes nonessential movement; streaming remains understandable without
  cursor blinking or animated spinners.
- Content remains usable at 320 px and 200% zoom. Inputs render at 16 px on mobile Web to avoid
  involuntary iOS zoom.

## 11. Performance and Rendering Contract

- Historical DOM is bounded to at most 200 mounted conversation rows for a 10,000-row fixture,
  including overscan but excluding the live tail and open Inspector.
- Live text/reasoning/activity updates are coalesced to at most one projection publication per
  animation frame and preserve the final delta on terminal/error/cancel.
- After profiler counters reset, 30 live publications produce zero commits in Sidebar, inactive
  routes, Composer, closed Status Panel, and closed Inspector.
- The active Turn subtree may update; completed Turn groups with unchanged selector inputs do not.
- Scrolling up, selecting text, opening a disclosure, or opening Inspector disables automatic
  bottom following. Jump-to-latest explicitly re-enables it.
- Virtual row replacement preserves the selected row or visible semantic anchor within 2 CSS px
  when practical; it never jumps to top on terminal compaction.
- Initial bundle budgets and route splitting remain governed by Manager GUI Performance. This
  feature MUST NOT move optional artifact, settings, or connector code back into the entry graph.
- Request coordination remains single-flight and visibility-aware. Conversation components never
  introduce their own polling loops.

## 12. Failure, Recovery, Compatibility, and Rollback

### 12.1 Failure and recovery

| Failure | Required behavior |
|---|---|
| Rejected before acceptance | Restore frozen draft, keep attachments, show structured reason |
| Transport lost with unknown acceptance | Enter reconciling, query receipt/snapshot, never auto-resend |
| Duplicate command | Reuse original turn/queue identity and render once |
| Event gap or expired cursor | Stop live projection, perform bounded authoritative readback, show recovery state |
| Manager restart with queued items | Restore queue order and paused/draining policy before enabling conflicting action |
| HaaS/backend restart during turn | Restore accepted invocation and cursor; do not create a replacement turn unless the lifecycle contract explicitly requires one |
| Stop fails | Keep current state and queued item, show retry/readback action |
| Projection invariant violation | Fail the affected conversation surface closed, preserve raw durable facts, expose content-free diagnostics |
| Unclassified assistant text | Render as the stable assistant response after redaction; do not hide, promote by word count, or expose it as reasoning |
| Usage absent or delayed | Omit usage from ordinary conversation UI; update aggregate completion metadata when authoritative data arrives without changing layout ownership |
| Component chunk fails | Keep session and draft alive; show retryable surface loading error |

### 12.2 Compatibility

- ADK routes, ADK Event semantics, and existing `/v1/haas/*` routes remain unchanged.
- Existing canonical HaaS event ids and correlation fields are reused. Any missing Manager-local
  identity is added to the Manager projection or internal WebSocket envelope, not inferred by UI.
- Existing model-call/stage payloads may remain an internal evidence input while the projection is
  corrected, but they are not a GUI compatibility surface. They are consumed only by the evidence
  adapter and may be removed once product work segments have canonical inputs.
- Internal Manager command fields are additive during migration. The final GUI uses only the
  acknowledged command path; the old unacknowledged sender is then deleted.
- Browser and Tauri share the same component, projection, queue, and draft contracts. Platform
  wrappers may add native file picking, notifications, and window lifecycle only.
- Persisted old transcripts are converted by a versioned persistence decoder or one-time migration
  into the new typed model. This compatibility code is isolated outside React and has an explicit
  retained-data sunset. No old renderer, UI item model, dual writer, or old CSS remains.

### 12.3 Rollback

Each migration wave has a documented rollback commit. The initial migration may use one temporary
internal flag before its cutover gate. The post-evidence C0-C4 correction changes the existing v2
path in place and MUST NOT add a second renderer flag: rollback reverts the complete slice. After a
slice is accepted, superseded code is deleted in the same or immediately following slice. The
final release has no user-visible old/new toggle and no permanent dual-write path.

## 13. Progressive Delivery and Legacy Removal

| Wave | Scope | Entry gate | Exit gate |
|---|---|---|---|
| W0 Baseline | Freeze current behavior, screenshots, state transitions, render/request counts | Approved spec | Reproducible failing tests exist for draft loss, queue absence, identity inference, focus, contrast, and long DOM |
| W1 Foundations | Semantic tokens, two themes, focus primitives, component shell primitives | W0 | Token lint, theme screenshots, keyboard/contrast gates pass; no visual behavior regression |
| W2 Projection shadow | Typed model, projector, store, selectors; compare with old view without changing output | W0 | Live/replay/reconnect golden parity and invariant tests pass; divergence is zero on fixtures |
| W3 Input lifecycle | DraftStore, command ACK, idempotency, follow-up queue, Stop/Escape/recovery | W2 | No lost draft, duplicate execution, stale queue or cross-session restore in fault tests |
| W4 Component cutover | Timeline, Turn, Activity, Interaction Dock, Composer, Queue, Status, Completion, Inspector | W1-W3 | Browser and Tauri behavior/visual parity; 320/390/1440 and both themes pass |
| W5 Performance | Virtual history/live tail, selector isolation, scroll anchor, bundle checks | W4 | 10,000-row DOM bound and profiler/request/scroll budgets pass |
| W6 Legacy zero | Delete old `Item` inference, live buffers, old Transcript/Composer path, obsolete CSS/tests/flag | W2-W5 | Import/grep/coverage evidence proves no old owner, dual write, compatibility renderer, or permanent flag remains |

No wave may mark itself complete while required old and new writers both mutate the same fact. A
temporary shadow projector is read-only and produces comparison diagnostics only.

W6 explicitly removes or replaces the current `WsEvent.data: any`, transcript `Item` adjacency
grouping, top-level `streamingRef`/`reasoningRef`/`modelStagesRef` ownership, `resetKey` draft
clearing, old `Transcript`/`Composer` render paths, their exclusive CSS hooks, and the temporary
conversation-v2 migration flag. The gate is based on import/usage evidence, not filenames alone.

### 13.1 Corrective evolution after packaged-product evidence

The first implementation passed the original technical gates but failed the product hierarchy in
Section 2.4. Correction proceeds in vertical slices on the existing v2 path; it MUST NOT introduce
a v3 renderer, a second transcript, or a long-lived UI flag.

| Slice | Scope | Failing evidence required first | Exit gate |
|---|---|---|---|
| C0 Projection contract | Product-turn/work/answer models and canonical presentation selector | Fixture currently produces peer model-stage cards and contradictory actions | Selector/invariant tests prove one turn, one response owner, and one valid primary action |
| C1 Stable live answer | Remove word-count gate and bind first delta to `AssistantResponse` | Test proves short response is hidden or relocates | Short/long/tool-interleaved streams keep one row and semantic node |
| C2 Calm work disclosure | Replace stage timeline with one current-action summary, tool disclosures, and evidence correlation | Eight-call fixture dominates the viewport | Default view renders one work summary, no stage/reasoning history rows, and only actionable open detail |
| C3 Hierarchy and motion | Move persistent mode context, remove token warnings/raw colors, unify type/layout/motion | Paired screenshot reproduces dense cards and competing animation | Both themes and target widths pass visual, motion, contrast, and reading-anchor review |
| C4 Legacy correction | Delete `streamGate`, stage-card UI/CSS/copy, independent lifecycle selectors, and obsolete tests | Grep/import inventory identifies every old owner | No old heuristic, component, style, translation key, selector, or dual projection remains |

Each slice updates the same typed projection and component tree. C1 and C2 may land separately only
when the intermediate state still renders a single stable response owner and no model-call card in
the primary timeline.

## 14. Test Plan and Acceptance Cases

### 14.1 Test levels

- Unit: reducers, selectors, state machines, token validation, queue ordering, draft revisions,
  idempotency and rendering variants.
- Integration: Manager WebSocket command ACK, persistence, reconnect, HaaS/local normalization,
  interaction recovery and terminal convergence.
- Hermetic GUI E2E: all user flows with repository fixtures in light/dark and narrow/wide viewports.
- Production preview: fresh Vite build, request/console/chunk/long-task observation and screenshots.
- Packaged desktop: real Tauri shell, sidecar restart, session restoration, native focus and file
  actions.

### 14.2 Executable acceptance matrix

| ID | Priority | Scenario | Expected evidence |
|---|---|---|---|
| MCX-001 | P0 | Send accepted | Draft clears only after persisted ACK; one user row and one turn |
| MCX-002 | P0 | Send rejected | Exact frozen draft and attachment refs restore; structured recovery shown |
| MCX-003 | P0 | ACK lost after acceptance | Receipt reconciliation finds original identity; no second execution |
| MCX-004 | P0 | Session switch/refresh/app restart | Each session restores only its own draft and queue |
| MCX-005 | P0 | Busy Enter | Submission becomes one visible queued item and does not start concurrently |
| MCX-006 | P0 | Queue edit/delete/reorder | Persisted order matches UI after restart; locked items reject mutation |
| MCX-007 | P0 | Send now | Stop + selected dispatch is atomic; failure leaves queue intact |
| MCX-008 | P0 | Stop/error with queue | Auto-drain pauses and explicit Resume works |
| MCX-009 | P0 | Live/replay parity | Same canonical fixture produces equal typed turns and terminal result |
| MCX-010 | P0 | Reconnect mid-turn | Existing turn resumes from cursor; no duplicate user row or tool |
| MCX-011 | P0 | Pending interaction restore | Dock appears before conflicting Composer action is enabled |
| MCX-012 | P0 | Status accuracy | Visible work/tool state, summary, and canonical lifecycle actions cannot contradict |
| MCX-013 | P0 | Failure recovery | Retry only appears when permitted and creates/reuses the contractually correct identity |
| MCX-014 | P0 | Keyboard flow | Compose, send, stop, queue, approval, disclosure, inspector, recovery work without pointer |
| MCX-015 | P0 | Focus | Every focusable control has visible focus; modal/dock focus returns correctly |
| MCX-016 | P0 | Theme contrast | Automated token pairs plus browser-computed light/dark surfaces pass WCAG AA |
| MCX-017 | P0 | Secretless | Logs/events/metrics/telemetry contain no draft, raw prompt, full tool args, credential or signed URL |
| MCX-018 | P1 | Responsive | 320/390/760/1200/1440 widths and 200% zoom retain every required action |
| MCX-019 | P1 | Reduced motion | No required state depends on animation; streaming and progress remain legible |
| MCX-020 | P1 | 10,000 rows | Mounted conversation rows <=200; selection and scroll anchor remain stable |
| MCX-021 | P1 | 30 live publications | Unrelated Profiler boundaries commit zero times after reset |
| MCX-022 | P1 | Long streaming turn | Publication rate <= one/frame and terminal flush preserves final delta |
| MCX-023 | P1 | Completion summary | Duration/count/usage/artifact facts render once; missing usage is omitted |
| MCX-024 | P1 | Browser/Tauri parity | Same fixture yields equivalent semantic DOM and actions on both surfaces |
| MCX-025 | P1 | Component API | Every exported AI component has focused states/keyboard/theme tests |
| MCX-026 | P0 | Legacy-zero gate | Old symbols, CSS hooks, renderer imports, dual writes and migration flag are absent |
| MCX-027 | Retired by MCX-049 | Conversation-local search and previous/next controls are absent; Cmd/Ctrl+F remains owned by the browser/WebView and global session search remains available |
| MCX-028 | P2 | Semantic context chips | Skill/file/session/context refs render, copy and open correctly without exposing raw transport syntax |
| MCX-029 | P0 | Product-turn hierarchy | Fixture with eight model calls renders one Turn, one work summary, one assistant response, and zero model-call cards in the primary timeline |
| MCX-030 | P0 | First-delta stability | A 1-39 word answer is visible after one coalesced publication; its `rowId` and semantic DOM owner remain unchanged past 40 words, tool arrival, and terminal sealing |
| MCX-031 | P0 | Lifecycle action matrix | Every phase/receipt/interaction combination renders at most one primary action; running never shows Continue and paused never presents Stop as a peer primary action |
| MCX-032 | P0 | Work disclosure ownership | Running and completed multi-call work defaults to one summary; user disclosure choice survives all live updates and completion rules |
| MCX-033 | P0 | Safe activity copy | Raw reasoning/commentary/provider prose and seeded secret-like arguments never appear in title, summary, status, notification, or accessible name |
| MCX-034 | P0 | Usage hierarchy | Missing/pending usage produces no warning; authoritative aggregate appears once in completion; per-call usage is Inspector-only |
| MCX-035 | P1 | Motion ownership | Each state has at most one allowed animation owner; no token update animates layout; transitions avoid `all`; theme swap does not smear; reduced-motion screenshots and behavior remain complete |
| MCX-036 | P1 | Persistent mode context | Bypass/policy/runtime mode renders once in header/Composer context and does not create a repeated or dominant timeline notice |
| MCX-037 | P1 | Narrow work layout | Long and pseudo-localized command/title/metadata at 320/390 px and 200% zoom wraps within the 72ch conversation measure without a fixed metadata column, clipped action, or card-height explosion |
| MCX-038 | P0 | Live-tail geometry | Reading older content during 100 deltas, tool updates, and completion moves the semantic anchor by at most 2 CSS px until Jump to latest |
| MCX-039 | P0 | Dynamic accessibility owner | One polite live region announces meaningful phase changes; token, usage, stage, and timer updates cause no duplicate announcement |
| MCX-040 | P0 | Assistant response idempotency | Two assistant-message facts for one turn, including different transport row ids or a replay, render one response owner and one copy of authoritative text; reload matches live output |
| MCX-041 | P0 | Chronological tool activity | Tool activities render once in canonical occurrence order without model-call or reasoning-parent containers. Reasoning stays available to projection as transient progress but does not become a durable disclosure row |
| MCX-042 | P0 | Inline safe activity detail | Clicking a command row expands detail directly below it without opening a side/bottom Inspector; workspace-owned paths use `workspace/`, outside host paths remain redacted, and expired evidence retains the safe command/preview |
| MCX-043 | P1 | Search overlay focus | Global search uses a rounded semantic input shell and neutral focus border in both themes; no inner rectangular brand outline or brand-filled active row appears |
| MCX-044 | P1 | macOS titlebar alignment | In overlay mode, native traffic lights and sidebar/panel collapse or reveal controls share the center measured from the packaged AppKit button frame and differ by at most 1 CSS px before and after sidebar collapse. With the pinned `traffic_light_position(..., y=24)` stack, the measured WebView-relative center is currently 22 CSS px and the 12 px browser simulator uses `top:16px`; inferred geometry must never override packaged evidence. Text blocks keep their typographic baseline and are not used as the control-center reference. Browser and packaged screenshots must cover expanded, collapsed, maximize, and restore states |
| MCX-045 | P0 | Composer trailing cluster | At 320/390/760/1440 px in both themes, model, microphone, and Send/Stop remain visible in that order, share one unwrapped trailing cluster with peer gaps <=8 CSS px, and keep fixed mic/action hit targets while only the long model label ellipsizes; idle, running, and recording fixtures preserve the same ownership |
| MCX-046 | P0 | Terminal child-state convergence | Live sealing and historical replay of a completed/failed/cancelled turn never render a child activity or model stage as running/pending/waiting; a dangling tool becomes failed unless cancelled, a stale model stage follows the parent terminal state, and persisted evidence is not mutated |
| MCX-047 | P0 | Reconnect terminal monotonicity | For both `ready -> history` and `history -> ready` ordering, `running=false` plus idle/cancelled control cannot overwrite a terminal transcript with a stale non-terminal task outcome; the UI shows no working indicator or Stop action and a true running snapshot still restores them |
| MCX-048 | P0 | Selectable model availability | The Composer model menu contains only models whose routed provider is currently usable: credential-backed providers require configured credentials, OAuth providers require a signed-in profile, and keyless local providers require live discovery. An unavailable current/default model may remain visible as an immutable session fact but is never injected into selectable options. With no usable model the Composer shows Connect a model; with another usable model it offers only that list. Settings retains the complete catalog for configuration. No credential material enters the response or GUI |
| MCX-049 | P0 | Focused runtime presentation | User messages align right on a neutral filled surface while assistant responses align left on the canvas; visible `You`/`Assistant` headings are absent but accessible response names remain. Model-switch and lifecycle markers use one quiet divider treatment. Collapsed work shows only its canonical summary—even when a child failed—and expanded work has a 320 CSS px maximum with internal scrolling. The conversation-local Find/previous/next toolbar and Cmd/Ctrl+F interception are removed; browser find remains available |
| MCX-050 | P0 | Terminal disclosure scroll ownership | Expanding an activity, evidence, or work disclosure in a completed, failed, cancelled, or paused turn MUST NOT schedule `scrollIntoView` or re-enable transcript auto-follow. After the user scrolls the bounded work region to any position, late detail rendering and parent re-renders preserve both the work-region and transcript scroll offsets within 2 CSS px for at least 1 second. Only a new foreground turn, a session switch, or an explicit Jump to latest action may resume transcript following |
| MCX-051 | P0 | Single-line current action | While work is active, the work header replaces the generic running label with the latest safe reasoning/tool action in one ellipsized line. Step changes update that line in place. A restrained gradient text treatment is the sole continuous motion owner and is disabled under reduced motion. Terminal work stops the gradient, uses the terminal label, omits reasoning rows, places a failure/outcome summary after expanded activity detail, and leaves the final assistant summary as the turn's last substantive content |
| MCX-052 | P0 | Desktop root scroll containment | `html`, `body`, `#root`, and the application shell never become scroll containers or expose WebView rubber-band movement. Trackpad/wheel input over non-scrollable blank chrome leaves the document at offset zero. Transcript, sidebar, settings, and bounded work-detail containers remain independently scrollable and stop overscroll chaining at their boundaries |

### 14.3 Requirement-to-case traceability

| Requirement | Acceptance cases |
|---|---|
| MCX-R01 | MCX-009, MCX-010, MCX-012 |
| MCX-R02 | MCX-001, MCX-002, MCX-003 |
| MCX-R03 | MCX-002, MCX-004, MCX-017 |
| MCX-R04 | MCX-005, MCX-006, MCX-007, MCX-008 |
| MCX-R05 | MCX-003, MCX-008, MCX-009, MCX-010, MCX-011, MCX-013, MCX-046, MCX-047 |
| MCX-R06 | MCX-014, MCX-015, MCX-016, MCX-018, MCX-019 |
| MCX-R07 | MCX-024, MCX-025, MCX-026 |
| MCX-R08 | MCX-011, MCX-012, MCX-018, MCX-024 |
| MCX-R09 | MCX-020, MCX-021, MCX-022 |
| MCX-R10 | MCX-013, MCX-023 |
| MCX-R11 | MCX-020, MCX-027 |
| MCX-R12 | MCX-025, MCX-028 |
| MCX-R13 | MCX-026 |
| MCX-R14 | MCX-009, MCX-029, MCX-032, MCX-038 |
| MCX-R15 | MCX-022, MCX-030, MCX-038 |
| MCX-R16 | MCX-011, MCX-012, MCX-031, MCX-039, MCX-047 |
| MCX-R17 | MCX-023, MCX-029, MCX-032, MCX-033, MCX-034, MCX-046 |
| MCX-R18 | MCX-016, MCX-018, MCX-019, MCX-035, MCX-036, MCX-037, MCX-045 |

### 14.4 Commands

Implementation uses focused commands first, then the repository gates:

```bash
cd manager/surfaces/gui
npm test -- --run
npm run build
npx playwright test \
  e2e/conversation-queue.spec.ts \
  e2e/conversation-product-turn.spec.ts \
  e2e/conversation-reconnect.spec.ts \
  e2e/conversation-accessibility.spec.ts \
  e2e/conversation-visual.spec.ts \
  e2e/conversation-performance.spec.ts \
  e2e/haas-activity.spec.ts \
  e2e/transcript-scroll.spec.ts

cd ../../../
make gui-preview-smoke
make pre-commit
make full-check
```

Packaged desktop evidence uses `manager/packaging/build_dmg.sh` and
`manager/packaging/smoke_packaged_app.sh`. Real provider/HaaS E2E remains opt-in and uses synthetic
content only.

## 15. Task Breakdown and SDD/TDD Sequence

| Order | Task | Primary deliverable | TDD entry | Depends on |
|---:|---|---|---|---|
| 1 | Freeze baseline | State/visual/performance/failure fixtures | W0 failing cases | None |
| 2 | Define projection types | `model/` contracts and invariant tests | MCX-009/010/012 | 1 |
| 3 | Implement projector/store | Deterministic reducer and narrow selectors | Golden live/replay tests | 2 |
| 4 | Add token system | Semantic CSS tokens, themes, token lint | MCX-015/016/019 | 1 |
| 5 | Add component primitives | Button/disclosure/dock/status/focus foundations | keyboard/theme component tests | 4 |
| 6 | Add acknowledged commands | Manager receipts, idempotency and reconciliation | MCX-001/002/003 | 2-3 |
| 7 | Add DraftStore | Per-session revisioned draft and attachment refs | MCX-004 | 6 |
| 8 | Add FollowUpQueue | Persisted queue commands and policy | MCX-005-008 | 6-7 |
| 9 | Cut over interaction dock/composer | Unified pending state and stable input | MCX-011/014/015 | 5-8 |
| 10 | Cut over product-turn components | Turn/work/reasoning/tool/response/completion/inspector; no model-call cards | MCX-009/012/013/023/029-034 | 3,5 |
| 11 | Add virtualization/stable live tail | Bounded history, first-delta response owner and scroll anchoring | MCX-020-022/030/038 | 10 |
| 12 | Add conversation navigation and semantic context | Search/turn navigation plus typed context chips | MCX-027/028 | 10-11 |
| 13 | Responsive, hierarchy, and motion polish | Focused Workbench at all target widths/themes | MCX-016/018/019/024/035-037/039/045 | 9-12 |
| 14 | Delete legacy | Remove old owners, stream heuristic, model-stage UI/CSS/copy, obsolete tests and migration flag | MCX-026/030/031 | 2-13 |
| 15 | Release review | Required reviews and full gates | all cases | 14 |

Every implementation task starts with a failing unit, contract, or E2E assertion mapped above,
then implements the minimum behavior, then refactors within the named component boundary.

### 15.1 Indicative engineering schedule

| Stage | Estimate | Exit milestone |
|---|---:|---|
| W0 Baseline | 1 day | Reproducible behavior, fault and visual baselines |
| W1 Foundations | 2 days | Token/theme/accessibility primitives pass |
| W2 Projection shadow | 3-4 days | Zero fixture divergence and stable selectors |
| W3 Input lifecycle | 4-5 days | ACK/draft/queue/recovery fault suite passes |
| W4 Component cutover | 5-7 days | Focused Workbench behavior and visual parity |
| W5 Performance | 2-3 days | 10k DOM, Profiler and scroll gates pass |
| W6 Legacy zero and release review | 2 days | Old path deleted and all release gates pass |

The expected engineering window is 19-24 working days for one engineer, including a two-day risk
buffer across W2-W4. Waves remain separately reviewable, but W6 is part of completion rather than
optional cleanup.

### 15.2 Corrective implementation plan for the current worktree

Existing ACK, draft, queue, theme, virtualization, and performance work is retained only where it
passes the corrected product-turn contract. The remaining correction is estimated at 5-8 working
days for one engineer:

| Order | Work item | TDD red case | Deliverable | Estimate |
|---:|---|---|---|---:|
| 1 | Add product-turn projection invariants and presentation selector | MCX-029/031 fail against current stage/action output | Projector/selectors plus golden fixtures | 1-2 days |
| 2 | Replace heuristic stream ownership | MCX-030 fails for short/tool-interleaved streams | Stable `AssistantResponse` and deletion of word gate | 1 day |
| 3 | Replace model-stage timeline | MCX-032-034 fail against eight-call fixture | Current-action summary, tool disclosures, Inspector-only telemetry | 1-2 days |
| 4 | Correct hierarchy, mode context, narrow layout, and motion | MCX-035-037/039 visual and semantic failures | Tokenized EN/ZH UI at both themes and target widths | 1-2 days |
| 5 | Remove superseded code and run release reviews | MCX-026 plus grep/import failures | No legacy stage UI/selectors/styles/copy; full gate evidence | 1 day |

Tasks 2-4 may be implemented as small commits after Task 1. Task 5 is mandatory before this change
is described as complete. There is no compatibility promise for the current model-stage visual
hierarchy or word-threshold stream behavior.

## 16. Component Impact Analysis

| Component | Impact | Required action | Compatibility conclusion |
|---|---|---|---|
| Manager GUI Performance | New projection/selectors, virtual history and component boundaries | Extend profiler, DOM, scroll and bundle gates | Existing budgets remain; requirements become stricter |
| Manager HaaS Sidecar Backend | Acknowledged commands, queue persistence, normalized projection, and evidence-only model-call correlation | Add internal command receipts; project product turns/work; stop treating model stages as GUI rows | HaaS public protocol unchanged; internal GUI snapshot may change atomically with packaged assets |
| Manager Delegation | Durable local send queue becomes visible and operable | Keep policy-application gate and accepted invocation semantics | Existing delegation contract clarified, not weakened |
| Event Log & SSE | Supplies canonical correlation and replay facts | Reuse existing event/turn/invocation/tool ids; classify user-visible assistant text before GUI projection | No new public event required for initial cutover; no model-native event leaks northbound |
| Session Runtime | Source of terminal and idempotency truth | Preserve acceptance and terminal convergence | No lifecycle semantic change |
| Manager Product Identity | Focused Workbench becomes the primary OpenHarness conversation character | Preserve local-first/no-login and desktop-native behavior | No identity or cloud dependency change |
| Security Boundary | Draft/queue and evidence contain sensitive user context | Local-only draft storage; content-free diagnostics; existing evidence scope | Secretless guarantees preserved |
| Artifact Store | Completion and status surfaces link produced files | Reuse safe metadata and existing viewer | Artifact URL/path contract unchanged |
| Container/adapter/model/MCP | None beyond regression | Run relevant full gates | No runtime/platform contract change |

## 17. Risks, Mitigations, and Deferred Work

| Risk | Mitigation |
|---|---|
| Projection rewrite changes ordering | Shadow projection with golden live/replay comparison before visual cutover |
| Dual ownership creates races | New store is the sole writer at each cutover; shadow mode is read-only |
| Queue changes execution semantics | Persist command receipts first; fault-inject stop/restart/duplicate paths |
| Virtualization breaks selection or scroll | Separate live tail, selection-aware anchor, deterministic 10k fixture |
| Component abstraction becomes a generic framework | Export only components required by current OpenHarness surfaces; no plugin API in this change |
| Token migration creates broad visual churn | Migrate component by component with paired light/dark screenshots |
| Legacy removal breaks old persisted history | Keep one data migration adapter only where durable old records require it; delete old renderer |
| Harnesses differ in answer/reasoning classification | Normalize at transport/projection boundary; unknown safe assistant text becomes answer, never guessed from content |
| Collapsing work hides actionable failure | Keep the active interaction and first actionable failure outside default-collapsed successful detail |
| Removing stage UI reduces diagnostics | Preserve model-call correlation, per-call usage, and raw bounded evidence in Inspector/diagnostics, not the primary timeline |
| Stable answer DOM conflicts with virtualization | Keep active turn in the non-virtual live tail and atomically seal it into history |

Deferred until a separate approved spec:

- cross-device/cloud draft synchronization;
- arbitrary backend steering semantics;
- workflow graph and multi-pane conversation editing;
- user-selectable density themes beyond the single compact professional default; and
- public extraction of the React AI component kit as a separate package.

## 18. Review and Release Gates

The spec is implementation-ready only when review finds zero blockers in context consistency,
state ownership, interface completeness, recovery, compatibility, security, accessibility,
performance, testability, migration, and legacy deletion.

Implementation completion requires, in order:

1. all mapped P0/P1/P2 acceptance cases;
2. `code-review` with every finding fixed or explicitly resolved;
3. `brooks-review` architecture/maintainability review;
4. `brooks-test` test-quality review;
5. browser production-preview and packaged Tauri smoke evidence;
6. `make pre-commit`, including secret scan; and
7. `make full-check`.

No release may claim this change complete while W6 legacy-zero evidence is missing.

#### Cutover preservation rules

Reviewer denial remains an actionable, compact work detail even when successful work is folded.
A one-shot exact-action override is offered only when the existing permission event explicitly
allows it. Inspector retains approval provenance, standing-rule explanation, and privacy-filter
counts. Historical failures cannot retry a newer task. All migrated controls use semantic type
and color tokens. Completion duration must come from task timing, never a sum of overlapping tool
intervals. Usage is aggregated only when every persisted assistant accounting record in the turn
reports authoritative usage; partial or absent accounting is omitted. Manager display identity
sidecars are stripped before provider requests and shared by live and REST views.

The receipt client starts bounded reconciliation after 10 seconds without ACK, including when the
socket remains open. An accepted task starts even when writing its ACK fails. A late ACK from a
previous session cannot clear the active session draft. A queued request keeps its accepted model
selection until dispatch. Narrow windows initially close secondary panels regardless of the
remembered desktop preference; explicit panel actions remain available. Work disclosure pauses
following just like opening evidence. Error events project a failed phase until authoritative
terminal/readback, rather than falling back to completed when the transport turn ends.

Reconnect reconciliation is a Composer state transition, not merely a transport callback. A
correlated accepted/duplicate receipt clears `acceptanceUnknown` and clears the visible draft only
when its revision still matches; a correlated `command_not_found` clears the blocked state but
retains the current draft and shows the safe rejection. Newer edits and another session are never
cleared. Deleting the active session sends a one-shot discard signal through the same draft owner
before changing scope, so its cleanup effect cannot save the deleted text or attachments again.


### Final v2 closure contract

The remaining W4–W6 work shares the existing component tree and storage. It introduces no
alternative renderer or public protocol. Delivery includes MCX-004/009/010/024/026/027/028,
not only the corrective C0–C4 subset.

- **Typed transport boundary:** Decode WebSocket JSON from `unknown` into a discriminated
  event union before any handler runs. Validate fields and nested collections consumed by the
  GUI; reject malformed known frames and unknown types with content-free counters. Unknown
  payloads never enter React, logs, or fabricated terminal state. Optional future fields remain
  additive. Native tool arguments remain opaque records at this boundary.
  The decoder must accept the Manager's structured `delegated` attribution and explicit nulls
  for absent ready outcomes, rejected-ACK dispositions, restored draft options, and approval
  standing targets. These are existing producer shapes, not malformed frames. MCX-013/026
  verification must send these shapes through the decoder and open a delegated command's
  details in the production GUI; invalid field types must still be rejected. This correction
  changes no ADK/HaaS API, event producer, permission, persistence, or logging contract.
- **Navigation:** MCX-027's conversation-local Find and previous/next controls are retired by
  MCX-049. Cmd/Ctrl+F remains browser/WebView-native, while global session search remains the
  product-owned search surface. Selecting text in a virtual historical row pins only that row in
  the virtual range until browser selection is collapsed or leaves the timeline. Scrolling cannot
  unmount the selection owner, and the extra pinned row remains inside MCX-020's 200-row bound.
- **Semantic context:** User rows and Composer share typed context references for selected skills,
  staged files, and referenced sessions. Labels never contain provider framing. Copy uses the
  human-readable label; Open delegates only to existing authorized file/session/skill actions.
  Missing or revoked references stay readable with an unavailable state. Context, model, and mode
  survive draft hydration, rejection, queue edit, and reload; no text-prefix parsing in renderers.
  Older force-run records normalize once at the history boundary.
- **Durability:** IndexedDB writes resolve only after transaction commit; abort/error preserves
  the in-memory draft and reports persistence failure. No production memory-only success fallback.
  Deleting a session clears only its draft and staged context. Expiration removes only orphan
  scopes older than 30 days. Queue restart preserves ordering and pauses uncertain dispatch rather
  than replaying accepted work. A visible Resume queue action resumes queued work explicitly.
- **Readback:** Session load and terminal-readback results apply only to the session/request
  generation that requested them. Old responses cannot overwrite the newly selected conversation.
- **Legacy gate:** No untyped WebSocket payload, renderer-side identity inference, old live-buffer
  owner, duplicate status/step list, old renderer import, word threshold, or migration flag remains.
  One historical data migration adapter remains solely for previously persisted records.

Implementation order is transport/identity, navigation/context, durability/recovery, then final
parity/review/package. Each slice adds focused failing contract tests before changes, followed by
unit/build and production browser regression. Browser fixtures exercise real IndexedDB and restart;
packaged checks cover a visible interactive window, existing history, navigation, and both themes.
Tests and native observations distinguish unavailable external provider/platform conditions from
passes. ADK, public HaaS events, artifact permissions, and container variants remain unchanged.

### Project workbench boundary

[Manager Project Workbench Experience](../manager-project-workspace-experience/README.md) owns
project grouping, workspace/execution-target draft context, Git branch controls, and native window
chrome. This component continues to own the chronological tool-activity projection, transient
current-action summary, and inline ActivityInspector. Project surfaces may open that owner but MUST NOT render a second command detail
surface. Accepted project/workspace/endpoint identities are inputs to a conversation, not facts
inferred from transcript content.
