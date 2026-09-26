# Manager Conversation Experience Specification

**English** | [简体中文](README.zh-CN.md)

Status: Reviewed; blockers resolved; approved direction recorded
Last reviewed: 2026-09-26
Change ID: `manager-conversation-interaction-v2`
Related specs: [Manager HaaS Sidecar Backend](../manager-haas-sidecar-backend/README.md), [Manager Delegation](../manager-delegation/README.md), [Manager GUI Performance](../manager-gui-performance/README.md), [Event Log & SSE](../event-log-sse/README.md), [Session Runtime](../session-runtime/README.md), [Manager Product Identity](../manager-product-identity/README.md), [Security Boundary](../security-boundary/README.md)

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
- Below 760 CSS px, navigation and secondary status become drawers. Composer keeps attachment,
  primary mode indicator, and Send/Stop visible; model, usage, and secondary actions move into one
  labeled configuration menu.
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
| paused | Persistent paused label with retained partial result | Continue | Stop permanently |
| recovering | Stable recovery banner; existing content remains readable | Retry readback/reconnect | Safe diagnostic reference |
| failed/incomplete | First actionable failure expanded near its turn | Retry/resume only when allowed | Evidence Inspector |
| completed | Final assistant result plus quiet completion footer | Continue conversation | Usage, artifacts, activity details |

Each state has at most one filled/accent primary action. Destructive actions use danger semantics and
never become primary merely because they are time-sensitive. No critical recovery action is hidden
behind a disclosure.

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
interface ConversationSnapshot {
  sessionId: string;
  revision: number;
  lastEventId: string | null;
  phase: "idle" | "submitting" | "running" | "waiting" | "paused" |
    "recovering" | "completed" | "failed" | "cancelled";
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
  updatedAtMs: number;
}

interface FollowUpQueueItem {
  queueItemId: string;
  clientCommandId: string;
  position: number;
  state: "queued" | "dispatching" | "running";
  submissionRef: string;
  requestedDelivery: "enqueue" | "interrupt_then_start";
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

### 6.3 Projection update rules

- Initial load applies one bounded snapshot, then replay events after `lastEventId`, then live data.
- An event already applied by `eventId` is a no-op.
- A lower revision cannot replace a higher revision.
- Text deltas append once in sequence order; final text seals the live row and does not create a
  second answer.
- One started work item receives at most one terminal state.
- Terminal state cannot return to running without a new `turnId`/`invocationId`.
- Pending interactions are restored before Composer enables conflicting commands.
- Reconciliation replaces uncertain derived state only with an authoritative snapshot or event
  page; it never resubmits the original user command automatically.

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

The initial client/server handshake advertises `conversationProtocolVersion: 2`. A mismatched
cached browser client receives a structured `client_upgrade_required` response and reload action;
the server does not silently downgrade to the unacknowledged sender. Hashed production assets and
packaged-app atomic replacement make this a bounded deployment transition, not a permanent legacy
protocol.

`error`, when present, is `{code, safeMessage, retryable, recoveryAction?}`. It never contains raw
prompt, full tool arguments, credential material, signed URLs, or backend stack traces. Queue
mutation commands carry `queueItemId`, `expectedQueueRevision`, and their own idempotency key;
stale revision returns a structured conflict plus the latest queue snapshot.

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
3. progressively disclosed reasoning and work rows;
4. final assistant result;
5. completion summary and durable recovery actions.

Normal completed work defaults collapsed. Running work shows one compact active summary. Failed,
interrupted, or incomplete work defaults expanded to the first actionable failure. User selection
and disclosure remain stable during live updates.

### 8.2 Activity summaries and evidence

- The summary title prioritizes safe task/tool summary, command preview, action summary, then a
  localized neutral fallback.
- One count covers all visible work kinds; a visible running stage can never coexist with a zero-
  activity summary.
- Running state includes a persistent text label. Motion is optional and disabled under reduced
  motion.
- Activity rows show status, safe title, optional key result, and duration. Raw arguments never
  appear by default.
- Selecting a row opens the existing secure evidence path in an Inspector. Narrow layouts use a
  non-modal bottom drawer that does not cover Composer or Interaction Dock.

### 8.3 Pending interaction dock

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

### 8.4 Completion summary

A completed turn may show one quiet footer containing duration, activity count, token usage when
authoritative, changed/produced artifact count, and a direct artifact action. Missing usage is
omitted in the footer; it is not rendered as a persistent warning. The final assistant result
remains the dominant content.

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
    ActivitySummary.tsx
    ActivityRow.tsx
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
  `:focus-visible` indicator.
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
| Component chunk fails | Keep session and draft alive; show retryable surface loading error |

### 12.2 Compatibility

- ADK routes, ADK Event semantics, and existing `/v1/haas/*` routes remain unchanged.
- Existing canonical HaaS event ids and correlation fields are reused. Any missing Manager-local
  identity is added to the Manager projection or internal WebSocket envelope, not inferred by UI.
- Internal Manager command fields are additive during migration. The final GUI uses only the
  acknowledged command path; the old unacknowledged sender is then deleted.
- Browser and Tauri share the same component, projection, queue, and draft contracts. Platform
  wrappers may add native file picking, notifications, and window lifecycle only.
- Persisted old transcripts are converted by a versioned persistence decoder or one-time migration
  into the new typed model. This compatibility code is isolated outside React and has an explicit
  retained-data sunset. No old renderer, UI item model, dual writer, or old CSS remains.

### 12.3 Rollback

Each migration wave has one temporary internal flag and a documented rollback commit. A wave may
roll back to the previous implementation only before its parity and cutover gate. After a wave is
accepted, its flag and superseded code are removed in the next wave. The final release has no user-
visible old/new toggle and no permanent dual-write path.

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
| MCX-012 | P0 | Status accuracy | Visible stage/tool count and summary cannot contradict |
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
| MCX-027 | P2 | Search and turn navigation | Matches remain reachable across virtualized history and navigation preserves the reading anchor |
| MCX-028 | P2 | Semantic context chips | Skill/file/session/context refs render, copy and open correctly without exposing raw transport syntax |

### 14.3 Requirement-to-case traceability

| Requirement | Acceptance cases |
|---|---|
| MCX-R01 | MCX-009, MCX-010, MCX-012 |
| MCX-R02 | MCX-001, MCX-002, MCX-003 |
| MCX-R03 | MCX-002, MCX-004, MCX-017 |
| MCX-R04 | MCX-005, MCX-006, MCX-007, MCX-008 |
| MCX-R05 | MCX-003, MCX-008, MCX-009, MCX-010, MCX-011, MCX-013 |
| MCX-R06 | MCX-014, MCX-015, MCX-016, MCX-018, MCX-019 |
| MCX-R07 | MCX-024, MCX-025, MCX-026 |
| MCX-R08 | MCX-011, MCX-012, MCX-018, MCX-024 |
| MCX-R09 | MCX-020, MCX-021, MCX-022 |
| MCX-R10 | MCX-013, MCX-023 |
| MCX-R11 | MCX-020, MCX-027 |
| MCX-R12 | MCX-025, MCX-028 |
| MCX-R13 | MCX-026 |

### 14.4 Commands

Implementation uses focused commands first, then the repository gates:

```bash
cd manager/surfaces/gui
npm test -- --run
npm run build
npx playwright test \
  e2e/conversation-submission.spec.ts \
  e2e/conversation-queue.spec.ts \
  e2e/conversation-projection.spec.ts \
  e2e/conversation-accessibility.spec.ts \
  e2e/conversation-visual.spec.ts \
  e2e/conversation-performance.spec.ts

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
| 10 | Cut over timeline components | Turn/activity/result/completion/inspector | MCX-009/012/013/023 | 3,5 |
| 11 | Add virtualization/live tail | Bounded list and scroll anchoring | MCX-020-022 | 10 |
| 12 | Add conversation navigation and semantic context | Search/turn navigation plus typed context chips | MCX-027/028 | 10-11 |
| 13 | Responsive and theme polish | Focused Workbench at all target widths/themes | MCX-016/018/019/024 | 9-12 |
| 14 | Delete legacy | Remove old owners, CSS, tests and migration flag | MCX-026 | 2-13 |
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

## 16. Component Impact Analysis

| Component | Impact | Required action | Compatibility conclusion |
|---|---|---|---|
| Manager GUI Performance | New projection/selectors, virtual history and component boundaries | Extend profiler, DOM, scroll and bundle gates | Existing budgets remain; requirements become stricter |
| Manager HaaS Sidecar Backend | Acknowledged Manager commands, queue persistence and normalized projection | Add internal command receipts and reuse canonical HaaS ids/cursors | HaaS public protocol unchanged |
| Manager Delegation | Durable local send queue becomes visible and operable | Keep policy-application gate and accepted invocation semantics | Existing delegation contract clarified, not weakened |
| Event Log & SSE | Supplies canonical correlation and replay facts | Reuse existing event/turn/invocation/tool ids | No new public event required for initial cutover |
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
