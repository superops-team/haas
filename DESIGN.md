# OpenHarness Manager Design System

**English** | [简体中文](DESIGN.zh-CN.md)

Normative design entry point for coding agents and contributors editing Manager UI.
Scope: desktop and browser Manager surfaces; conversation is the first migration scope.
This is a design contract, not a claim that every existing screen already complies.

## 1. Authority and product character

Read this file and the affected component spec before changing UI. Root `AGENTS.md` governs
development, security, and compatibility. The
[conversation spec](specs/manager-conversation-experience/README.md) owns lifecycle, commands,
data contracts, and acceptance cases. The [performance spec](specs/manager-gui-performance/README.md)
owns GUI performance requirements. [styles.css](manager/surfaces/gui/src/styles.css) owns runtime
token values. Update these sources together when a shared rule changes; do not invent a competing
local design system or use a visual rule to override a protocol contract.

Priority: **stability → design coherence → ease of use → task-management parity → breadth**.
OpenHarness is a calm, compact workspace for long-running Agent tasks. Make the user's request,
current work, required decision, and result easy to find. Color, motion, cards, and accounting
must not compete with that reading order.

User-facing product terminology calls a selectable specialized persona an **AI Assistant** in
English and **AI助手** in Chinese. Do not expose the legacy generic terms `Coworker` or `同事` for
that product object. Stable implementation identifiers (`coworker`, `.coworker/config.toml`, API
fields and package names), user-authored assistant names, and literal references to human colleagues
remain unchanged; presentation copy translates only at the UI boundary.

## 2. AI product objects

These are product responsibilities, not a requirement to add a new table or framework for every
noun. Reuse existing typed model and component boundaries.

| Object | Responsibility | Presentation rule |
|---|---|---|
| Session / task context | Workspace, configured harness, model, policy, history | Stable context; switching panes does not stop execution |
| Product turn | One accepted request and its work/result | One top-level unit; model rounds stay bounded inside its work region |
| Inference round | One canonical model stage and its safe progress summary | One compact row; never expose model-call id, ordinal, provider label, or raw reasoning |
| Work segment | Ordered tools before/after accepted guidance or a decision | Inline detail owned by its inference round; do not manufacture phases from prose |
| Assistant response | User-visible answer from first delta through sealing | Stable identity and DOM owner; never move text after a length threshold |
| Activity / tool | Typed action, safe summary, status, evidence reference | Summary first; detail and model evidence expand inline on demand |
| Pending interaction | Approval or structured input needing the user | One stable dock, identified decision, explicit scope and outcome |
| Input intent | Text, attachment/context references, model/mode, delivery | Preserve accepted input through admission, queue, execution, and history |
| Artifact / completion | Deliverable references and authoritative outcome | Openable result and factual footer; no invented success or counts |

Draft is editable local intent; accepted queue item is server-owned work; guidance changes current
work only when the backend explicitly supports it. These must not share an ambiguous Send behavior.
Unsupported capabilities are hidden or explained through existing availability contracts; the UI
must not simulate steering, pause, or successful delivery.

## 3. AI interaction invariants

| Rule | Required behavior | Existing acceptance contract |
|---|---|---|
| AI-01 Product turn first | Eight model calls still produce one turn and stable answer, with eight bounded summary rows inside work rather than peer cards | MCX-029/030/032/059 |
| AI-02 Separate facts, commands, and local state | ACK means accepted; terminal facts establish completion. Unknown acceptance reconciles identity before retry | MCX-001/002/003/009/010 |
| AI-03 Preserve user intent | Session-scoped drafts; clear only accepted revision; queue changes cannot lose context or duplicate work | MCX-004/005/006/007/008/028 |
| AI-04 One decision owner | Restore interaction before conflicting actions; retain resolved outcome without duplicating active form | MCX-011/014/015 |
| AI-05 Progressive evidence | Summary, safe preview, authorized detail, and artifact have distinct roles; explain unavailable/expired detail | MCX-017/025/033 |
| AI-06 Honest status | One lifecycle interpretation drives status and controls; omit missing usage; unknown progress is not 0% or success | MCX-012/013/023/031/034 |
| AI-07 User owns reading position | Stable streaming identity/selection; virtual history and stable live tail; Jump to latest restores following | MCX-020/022/027/030/038 |
| AI-08 View lifetime is not task lifetime | Session data outlives panes; narrow subscriptions isolate live text from drafts, navigation, and closed panels | MCX-004/010/021/024 |
| AI-09 Quiet accessible feedback | One motion owner and meaningful status announcement; both themes and keyboard paths remain usable | MCX-014/015/016/018/019/035/037/039 |

These are required contracts. Establish implementation status through acceptance evidence;
this mapping does not mark a case as passed.

## 4. Data and React boundaries

```text
HaaS facts + Manager command receipts
  → Manager transport validation / normalization
  → session projection and identity-based reconciliation
  → canonical presentation selector + narrow component props
  → Timeline / Work / Inline Detail / Response / Decision Dock / Queue / Composer

User action → typed command intent → admission → authoritative projection
Local UI state → draft, disclosure, selection, focus, scroll anchor
```

HaaS HTTP/SSE events describe facts. Do not export UI rows, CSS states, card layout, or ZCode's
private protocol as a new public HaaS API. Follow the existing Manager transport boundary.

- Presentational components accept typed data and callbacks. They do not open sockets, parse
  harness payloads, infer lifecycle from text, or fetch diagnostic evidence eagerly.
- One projection owner applies facts; optimistic feedback never becomes execution truth.
  Replay/receipt recovery preserves identities and must not blindly resend side effects.
- Keep stable external-store snapshots and narrow subscriptions behind repository hooks.
  Separate high-frequency live text from metadata, drafts, and disclosure state.
- Use stable turn/response/activity keys, not index, content, timestamp, or current status.
  Memoization requires stable inputs and measured benefit.
- Coalesce streaming publications and flush terminal content as specified. Do not reparse the
  whole transcript for each token or mount hidden evidence readers.
- Separate historical virtualization and active-tail geometry. Programmatic measurement must
  not be mistaken for the user choosing to follow the bottom.
- Closing a pane releases view subscriptions; it never implicitly cancels execution.
  New subscription layers require a concrete consumer and explicit cleanup ownership.

## 5. Reusable AI components

These contracts apply to existing components; they do not mandate additional wrappers.

| Component / current home | Receives | Owns | Must not own |
|---|---|---|---|
| ConversationTimeline / ConversationView | Projected turns, live tail | Reading order, anchor, bounded history | Transport or runtime lifecycle |
| TurnWork | Work facts, inference rounds, presentation, disclosure | One safe summary row per round; latest-running motion; typed tool/evidence disclosures | Native model-call labels, raw reasoning transcript, or independent run-state inference |
| AssistantResponse / MessageContent | Stable response and content | Incremental readable answer | Relocation of answer text |
| PendingInteractionDock | Typed interaction and callbacks | Single decision surface and focus | Shadow approval state |
| ConversationComposer / ContextChips | Draft scope, availability, context | Editing, acceptance feedback, quiet controls | Treating ACK as completion |
| FollowUpQueue | Accepted entries and available actions | Order, edit/dispatch intent, recovery feedback | Destructive optimistic removal without recovery |
| ActivityInspector / ModelEvidenceInspector | Turn/activity identity, safe preview/reference | Inline lazy evidence and loading/error/expired states | Side-panel ownership or raw payload in timeline labels/logs |
| TurnCompletion | Terminal outcome and measured facts | One factual footer | Timer-based success or fabricated usage |

Implementations live under [conversation/components](manager/surfaces/gui/src/conversation/components).
Standard tool layout uses predictable slots: kind/icon, primary summary, optional secondary detail,
status, disclosure. Specialized command/file/diff renderers reuse that shell; unknown kinds use a
safe generic summary. Expanding a command must show an available redacted preview even when full
evidence is loading, absent, or expired. Never fill a missing preview by dumping raw arguments.

Changed shared components define loading, empty, error, disabled, and recovery behavior where
applicable, plus accessible names, focus, and both themes. Reuse components first; extract only
when it removes a demonstrated duplicate responsibility.

## 6. Layout and control hierarchy

- Reading order: **request → compact work → answer → result references**. Background status,
  plan detail, telemetry, and evidence are secondary. One fact has one expanded owner; shortcuts
  elsewhere open that owner without duplicating its contents.
- Align user text, work, answer, navigation, and Composer to the shared conversation measure.
  Use normal-flow slots for navigation and decision dock. Floating controls must not obscure
  transcript lines, details, or Composer. Menus need viewport-safe layering, dismissal, and focus return.
- Work defaults to compact disclosure; preserve the user's expansion choice across updates.
  Failures and decisions remain discoverable without expanding every successful tool.
- Narrow layouts remove secondary chrome first. Long localized labels wrap; paths/commands may
  scroll inside details. Truncation must never hide the only available action.
- Search overlays use one rounded semantic input shell. Keyboard focus changes only its neutral
  border token; it never draws a rectangular brand outline inside the shell. Active result rows
  use neutral chrome rather than a full brand tint.
- On macOS overlay windows, the native traffic lights and every adjacent sidebar/panel
  collapse or reveal control share one titlebar center line. Tauri/tao's
  `traffic_light_position(..., y)` is an AppKit container inset, not either the button center or
  the CSS top of a simulated circle. The center must be measured from the native button frame in
  the packaged app. For the pinned Tauri/tao and macOS stack, `y=24` currently yields a 22 CSS px
  WebView-relative center, so the 12 px browser simulation uses `top: 16px`. Wordmarks and titles
  align optically inside the same 44 px strip but never redefine the interactive-control center.
  Browser geometry is only a fast regression gate; a packaged macOS screenshot is the final visual
  contract and overrides any inferred offset.
- A manual sidebar collapse is stable. The explicit titlebar reveal button is click/keyboard-only;
  keeping the pointer at the collapse-button coordinate must not immediately peek or reopen the
  sidebar. Hover peek belongs only to the narrow left-edge discovery zone below the titlebar.
- At most one filled primary action per local action group. The model's `primaryAction` expresses
  behavioral precedence, not a requirement for a colored button.
- Composer trailing controls form one non-wrapping cluster in the invariant order `model ->
  microphone -> Send/Stop`. They remain visible at every supported width and in recording/running
  states. Only the model control may shrink and ellipsize; microphone and lifecycle-action hit
  targets remain fixed, adjacent peers use one compact spacing token, and usage/secondary controls
  yield first.
- User and assistant messages are distinguished without visible speaker headings: user messages
  are right-aligned on a neutral filled surface; assistant responses remain left-aligned on the
  canvas. Accessible names remain available to assistive technology. The conversation does not
  own a Find/previous/next toolbar or intercept Cmd/Ctrl+F; native browser/WebView find remains.
- The Composer model menu is a capability surface, not a catalog. It renders only the backend's
  usable-model list and never injects an unavailable current/default model. Full discovery and
  credential setup remain in Settings.
- Desktop information architecture is project-first: a Project groups its conversations and one
  or more executable workspace bindings. Project expansion never changes the active conversation.
  Draft context presents Project, Work location, and Git branch as quiet peer controls; accepted
  work freezes those identities and later changes create a new conversation rather than silently
  retargeting history.
- The three draft-target controls form one context shelf visually attached behind the Composer,
  not three floating pills and not a detached metadata row. The shelf is inset 16 CSS px from each
  Composer edge, uses the neutral chrome surface with only its outer top corners visible, and is
  overlapped by the raised Composer surface by 12 CSS px. Project, Work location, and Git branch share one
  30 px centerline, compact peer spacing, 14 px single-weight icons, and the UI type role. Default
  controls have no individual border or fill; neutral hover/focus/open feedback may reveal one
  compact control surface. The local target uses a device glyph, not code brackets. All three
  remain on one line; long labels ellipsize without pushing peers outside the shelf or changing
  the shelf/Composer geometry. Non-Git workspaces omit Branch without reserving a fake third control.
- Project navigation uses progressive disclosure. The section header keeps a plain `+` for creation
  and reveals one organization menu on hover/focus. Each project row uses a primary disclosure
  button plus sibling New conversation/`...` actions; conversation rows use a primary selection button plus
  sibling Pin/Archive actions. Their reserved trailing areas prevent layout shift. Project hover
  cards may expose Pin/New conversation shortcuts, while conversation cards remain informational.
  New conversation binds a fresh draft to that Project's primary workspace/default execution target;
  project metadata editing has exactly one owner in the Project `...` menu. All cards and
  menus share one collision-aware overlay family, use neutral surfaces, preserve keyboard paths,
  and never change row geometry, selection, or expansion as they appear.
- Project and conversation previews are mutually exclusive states of one hover controller. Entering
  a different anchor dismisses the previous preview before the new 300 ms dwell begins; a delayed
  close can preserve the corridor to the current card, but can never keep a stale card beside the
  next one. Hover metadata and row actions occupy the same reserved trailing grid cell and switch
  only opacity/visibility, never `display` or row width.
- Project ordering is a pure selector over one authoritative projection. Project/Conversation rows
  are memoized leaves with stable callbacks; a single controller owns mutations and a single overlay
  host owns hover timers, viewport positioning, dismissal, and focus return.
- Initial navigation distinguishes “project projection pending” from an authoritative empty project
  list. While pending it renders one fixed, quiet project skeleton and never flashes the legacy flat
  conversation hierarchy; later background refreshes preserve the current projection until the new
  result settles.
- Project management is non-destructive by default. Removing a project means hiding/archiving its
  sidebar record with an explicit restore path; it never deletes files, worktrees, conversations,
  transcripts, artifacts, or accepted bindings. Pinned projects keep precedence over the chosen
  project sorting mode; selecting a project never reorders it. Active/running conversations may
  retain precedence inside their owning project.
- Command work uses a compact chronological row list. A collapsed command is exactly one visual
  line: the complete redacted value remains in the model, while the visible label uses width-based
  trailing ellipsis and omits the redundant category subtitle. Terminal state and disclosure stay
  fixed at the trailing edge. Clicking the row expands one inline Shell panel directly beneath it
  with the complete safely wrapped `$ command` and bounded output; it never opens a second Inspector
  owner or repeats full evidence plus preview.
- Every collapsed activity kind uses that same one-line row shell; safe summary ellipsizes while
  status and disclosure remain fixed, and category metadata never creates a second line. A collapsed
  work group shows no child rows, including failures. Expanded work is capped at 320 CSS px and
  scrolls internally; complete details remain available in the one inline inspector.
- Terminal history disclosures never seize scroll ownership. Expanding work, reasoning, activity,
  or evidence does not call `scrollIntoView`; after the user scrolls either the bounded work region
  or the transcript, late detail rendering preserves that position. Only explicit Jump to latest,
  a session switch, or a new foreground turn may resume transcript following.
- Active work renders one single-line, ellipsized safe-summary row per canonical inference round.
  Multiple reasoning chunks update only their owning row; tool lifecycle never supplies that label.
  Historical terminal rows default collapsed, while the newest running row defaults expanded and
  owns the only rotating status indicator. Reduced motion keeps the indicator static. Native model
  ids/ordinals and raw reasoning stay hidden. A terminal failure/outcome summary follows the bounded
  round/activity list, and the assistant response remains the last substantive content.
- The document root is fixed and non-scrollable. Trackpad/wheel gestures over blank chrome never
  move or rubber-band the whole WebView; only explicit scroll containers consume the gesture and
  they contain overscroll at their boundaries.
- Every full-page route shares the native title drag contract. Route titles and non-interactive
  top chrome are drag regions; buttons, links, form controls, menus, selectable content and
  scrollbars remain no-drag. Double-click on a route drag region performs one maximize/restore.
- The 44 px desktop chrome is a native interaction surface, not decorative padding. Empty chrome
  is draggable, interactive controls are no-drag islands, and double-clicking draggable chrome
  performs exactly one platform maximize/restore action.

| State | Composer/control treatment |
|---|---|
| Idle / terminal | One Send action; unavailable Send disabled; recovery follows the spec |
| Running, empty draft | Neutral Pause text when supported; neutral 32 px Stop icon |
| Running, edited draft | Same run controls plus explicitly named Queue follow-up action |
| Pausing / resuming / stopping / submitting / recovering | Plain secondary status text; no duplicate lifecycle button |
| Paused | Continue and End task from the same quiet text-control family |
| Waiting for decision | Decision dock owns resolution; no conflicting Composer send |
| Disconnected | Recognizable controls remain; undeliverable commands disabled |

Stop has a square glyph, localized accessible name and tooltip, and visible keyboard focus.
Neutral hover reveals its surface; End task may use danger text on hover. Ordinary processing
status is not a pill or disabled-looking button. Do not add a second spinner for work already
indicated in the timeline.

## 7. Tokens, themes, and motion

Use HaaS semantic tokens, not ZCode names or palette values. CSS declarations own runtime values;
these roles constrain usage.

| Role | Tokens / scale | Rule |
|---|---|---|
| Structure | `--color-chrome`, `--color-canvas` | Navigation recedes; conversation is primary |
| Content / overlay | `--color-surface`, `--color-surface-raised`, `--color-popover` | Distinguish structure, content, overlays; avoid nested peer cards |
| Text | `--color-text-primary/secondary/tertiary/inverse` | Required status/actions remain readable |
| Typography | `--text-title/heading/body/ui/navigation/mono/caption` | 20/16/14/13/12/12/11 px roles; navigation is dense sidebar content and caption is metadata |
| Spacing | `--space-1` through `--space-6` | 4 px scale; group spacing at least twice item spacing |
| Radius | `--radius-surface/control/compact` | 12/8/6 px; inner visible containers should not have larger radii |
| Accent / feedback | `--color-accent`, success/warning/danger roles | Sparse emphasis or actual semantic state |
| Focus | `--color-focus-ring`, `--color-field-focus-border` | Text fields and Composer use one neutral border with no outer ring, glow, tint, or shadow; discrete controls retain the accessible focus ring |
| Motion | `--motion-fast`, `--motion-work` | Brief feedback; one compact work indicator when appropriate |

New/migrated conversation components must not add raw colors, arbitrary font sizes, radii,
shadows, or an alias family. Reuse aliases where an existing shared primitive requires them;
do not expand that compatibility layer. Shared token additions require both themes and a spec
update. Do not change root font size to shrink the UI. Mobile editable text uses the spec's
16 px compatibility rule, not a new general typography scale.

Typography and spacing establish hierarchy before borders/fills. Default controls are neutral;
brand color is reserved for meaningful emphasis. Avoid gradients, glowing input rings, giant
processing buttons, repeated badges, and state-colored cards for ordinary activity.

Editable text controls across conversation and full-page routes share one focus treatment. A
bordered input, textarea, or select replaces its idle border with `--color-field-focus-border` and
adds no outline or shadow. Borderless controls delegate the same treatment to their owning shell
through `:focus-within`. Brand-blue rings remain reserved for keyboard focus on discrete controls
such as buttons, links, checkboxes, and custom interactive widgets.

Project navigation is a dense control surface, not reading copy. Project names and conversation
titles use `--text-navigation` at 12 px with a 1.35 line height; relative time remains the 11 px
caption role with tabular numerals. An active project may rise to weight 500, but never 600/700;
selection is carried primarily by color and the existing low-contrast row fill. Row hit targets
remain at least 28 CSS px, so compact type never reduces pointer or keyboard usability.
Section labels use the 11 px caption role at weight 500. Inactive conversation labels use secondary
text color and weight 400; only the selected conversation may use primary text and weight 500.

Both themes preserve hierarchy, disabled behavior, and focus. Normal text meets 4.5:1 contrast,
large text 3:1, required UI/focus indicators 3:1 against adjacent surfaces. Controls have at least
a 24×24 CSS px target; compact run controls are 32 px.

Motion follows the spec's state-owner matrix: compact indicator before content, optional answer
cursor, brief disclosure feedback, quiet terminal transition. No token update animates layout.
Avoid `transition: all`, springs, and theme-wide color smearing. Reduced motion retains complete
static meaning. One polite live region announces phase changes, not tokens.

## 8. Applying and reviewing a change

1. Identify affected product objects, transitions, and acceptance IDs. Update affected spec before
   code when behavior or shared design rules change; review before implementation.
2. Reuse components/tokens. Keep one state owner and active renderer. Migrate a complete vertical
   slice; remove superseded renderer, CSS, dual writes, and flags at cutover (MCX-026).
3. For behavior changes, reproduce failure and verify user outcomes. For local styling, reuse
   lifecycle tests and inspect geometry, themes, focus, readability; avoid tests mirroring CSS.
4. Check affected states in both themes at 390/1440 px, plus 320 px and 200% zoom for layout changes.
   Include long EN/ZH text, keyboard, and reduced motion where affected. The full fixture set
   remains defined by MCX-014 through MCX-039.
5. Run relevant GUI unit/build and production-preview checks; rebuild `dist` before preview.
   Apply code-review, brooks-review, brooks-test, and pre-commit. Final merge/release requires
   `make full-check`; desktop parity requires actual packaged validation.
6. Report verified scope and missing evidence. Browser mocks validate presentation, not provider
   recovery or native Tauri behavior. Do not refresh screenshots blindly to accept regressions.
   Temporary observations/reports stay outside tracked docs.

Enforcement has four parts: GUI `AGENTS.md` points here; shared components encode defaults;
behavior tests check state contracts; theme/geometry review checks visual outcomes. This change
adds the normative entry point, not a repository-wide automatic CSS/token linter. Existing
noncompliant surfaces remain migration work and cannot be marked compliant from this file alone.

## 9. ZCode reference map

Local ZCode checkout reviewed on 2026-09-27. Paths below are relative to its root, not HaaS
dependencies. They identify observed patterns, not proof that ZCode is defect-free.

| Source | Pattern adopted | HaaS adaptation |
|---|---|---|
| `DESIGN.md`, `AGENTS.md` | One normative design entry point | This file + GUI instructions + spec; retain HaaS tokens |
| `packages/shared/src/zcode-protocol-v4/snapshot.ts`, `rows.ts` | Explicit availability, input routing, stable typed rows | Manager model/presentation; no UI projection in public HTTP/SSE |
| `packages/shared/src/zcode-protocol-v4/input-intent.ts` | Complete intent survives delivery | Draft/context and command/queue contracts; capability-gated guidance |
| `packages/ui/src/v4/conversationProjectionStore.ts`, `ackActivationBarrier.ts` | Facts vs optimism; ordering/gap reconciliation | Existing HaaS cursor/receipt recovery, not the private wire protocol |
| `packages/ui/src/v4/sessionDataLayer.ts` | Session subscriptions outlive views | Store lifetime/cleanup without another general framework |
| `packages/ui/src/v4/conversationTurnRenderUnits.ts`, `conversationTurnWorkSegments.ts` | Product turns and factual timing | Product-turn projection and stable response owner |
| `packages/ui/src/v4/conversationTimelineLiveTail.ts`, `timelineScrollAnchor.ts` | Separate live tail; user controls following | Geometry, selection, navigation contracts |
| `packages/shared/src/zcode-protocol-v4/toolDisplay.ts`, `packages/ui/src/ToolCallBlocks/ToolLayout.tsx`, `ToolSummaryRow.tsx` | Typed standardized tool disclosure | Safe summary/preview and authorized lazy inline detail |
| `packages/ui/src/v4/conversationStatusPanelModel.ts`, `pendingInteractionAdapter.ts` | Decision/status models and explicit missing facts | Single dock/status owners; omit legacy adapters |

Do not copy the giant SessionPane, compatibility adapters, fallback theme matrix, specialized
workflow graph, or store shape wholesale. Adopt responsibility boundaries and verifiable
invariants; preserve HaaS protocol, security boundary, existing stack, and restrained identity.
