# Event Log & SSE Component Specification

**English** | [简体中文](README.zh-CN.md)

Status: Draft
Last reviewed: 2026-09-12
Related specs: [HaaS Protocol](../haas-protocol/README.md), [Session Runtime](../session-runtime/README.md), [Harness Adapter](../harness-adapter/README.md), [Manager Delegation](../manager-delegation/README.md), [Security Boundary](../security-boundary/README.md)

## 1. Component Role

Event Log & SSE is HaaS's event source of truth and real-time subscription layer. It persists canonical events, projects them into ADK `Event` objects, and provides session- and invocation-level replay-then-live streams.

SSE is a delivery channel, not the sole source of truth. Disconnects, client timeouts, or proxy reconnections MUST NOT stop a task or lose its result.

## 2. Sources and Rationale

| Source | Adopted concepts |
|--------|------------------|
| ADK 2.0 | `Event` shape, `/run_sse` SSE `data:` frames, stream-close semantics, and `events[]` returned by `GET session` as a replay channel |
| `mpa-codex-worker` Event Log & SSE Replay | `after_event_id`, `Last-Event-ID`, heartbeats that do not advance the cursor, and projection |
| Component overview | HaaS event-log and SSE requirements |

## 3. Upstream and Downstream Relationships

| Direction | Component | Relationship |
|-----------|-----------|--------------|
| Upstream | Session Runtime | Appends lifecycle, output, and terminal events |
| Upstream | Harness Adapter | Supplies adapter-native events, which Session Runtime converts to canonical events before appending |
| Upstream | HaaS Protocol | Reads the event log and emits ADK SSE |
| Downstream | Persistence Store | Stores event records |
| Downstream | Observability | Records stream-client, lag, drop, and replay metrics |

## 4. Responsibility Boundaries

Responsibilities:

- Persist canonical events with a stable HaaS `type` and type-specific safe `haas` metadata.
- Maintain a gapless, zero-based `sequenceNumber` for each invocation and a separate gapless session-lifecycle sequence for events outside a turn.
- Maintain a session-scoped `eventId` for HaaS native streams.
- Project canonical events into ADK `Event` or HaaS events.
- Persist and stream manager-delegation lifecycle events, including restore,
  workspace-lock queueing, approval requests, and approval resolutions.
- Replay missed events before entering a live stream using `Last-Event-ID` / `after_event_id`.
- Send heartbeats without producing events or advancing the cursor.
- Use a bounded per-subscriber delivery queue. Persistent canonical events are never dropped or coalesced. When a subscriber cannot keep up, disconnect it with its last successfully delivered event id; the client replays from persistent storage.
- Prevent unredacted raw events from entering the persistent event log.

Non-responsibilities:

- Does not execute a harness.
- Does not modify session/invocation terminal state.
- Does not store raw prompts, complete tool arguments/results, or provider payloads.
- Does not guarantee strict global ordering across sessions. It guarantees invocation ordering by invocation sequence, session-lifecycle ordering by its own sequence, and merged session replay ordering by session-scoped `eventId`.

## 5. Core Interfaces

### 5.1 Public Streams

| Endpoint | Protocol | Semantics |
|----------|----------|-----------|
| `POST /run_sse` | ADK SSE | ADK Event stream for an invocation; closes on completion |
| `GET /apps/{app}/users/{user}/sessions/{sid}` | JSON | Native ADK replay channel returning all `events[]` |
| `GET /v1/haas/sessions/{session_id}/events-page` | JSON | Bounded `CanonicalHaasEvent` page ordered by eventId; default 100, maximum 1000, `nextCursor` is last returned id or null |
| `GET /v1/haas/sessions/{session_id}/events` | HaaS SSE | Session canonical-event replay/live stream with `after_event_id` |
| `GET /v1/haas/sessions/{session_id}/invocations/{invocation_id}/events` | HaaS SSE | Invocation canonical-event replay/live stream |

### 5.2 Internal API

```python
async def append_event(event: CanonicalEvent) -> StoredEvent: ...
async def read_session_events_page(app_name: str, user_id: str, session_id: str, after_event_id: str | None, limit: int) -> EventPage: ...
async def list_approvals(app_name: str, user_id: str, session_id: str, status: str, cursor: str | None, limit: int) -> ApprovalPage: ...
async def read_invocation_events(app_name: str, user_id: str, session_id: str, invocation_id: str) -> list[StoredEvent]: ...
async def stream_invocation(app_name: str, user_id: str, session_id: str, invocation_id: str, after_event_id: str | None) -> AsyncIterator[SSEFrame]: ...
async def stream_session(app_name: str, user_id: str, session_id: str, after_event_id: str | None) -> AsyncIterator[SSEFrame]: ...
def project_adk(event: StoredEvent) -> AdkEvent: ...
def project_haas(event: StoredEvent) -> CanonicalHaasEvent: ...
```

## 6. Data Model

### 6.1 CanonicalEventRecord (Internal)

```json
{
  "schemaVersion": 2,
  "type": "haas.output.text.delta",
  "eventId": "evt_0000000001042",
  "invocationId": "inv_abc",
  "sessionId": "hsess_abc",
  "appName": "chrn_codex_default",
  "userId": "u_123",
  "turnId": "turn_abc",
  "harnessId": "chrn_codex_default",
  "adapterId": "codex-app-server",
  "author": "codex",
  "sequenceNumber": 7,
  "content": {
    "role": "model",
    "parts": [{ "text": "text" }]
  },
  "actions": {
    "stateDelta": {},
    "artifactDelta": {}
  },
  "haas": {},
  "observedAtMs": 1786400000000,
  "redactionApplied": true
}
```

Timestamp convention: internal canonical events use `observedAtMs`, in epoch
milliseconds. ADK `Event.timestamp` is generated by the projection layer as
`observedAtMs / 1000.0`, in float seconds.

**`HarnessEvent` -> `CanonicalEventRecord` mapping**: the adapter emits
`HarnessEvent`; see [harness-adapter](../harness-adapter/README.md) §6.3. Session
Runtime invokes Event Log to normalize and persist it:

| HarnessEvent field | CanonicalEventRecord | Rule |
|--------------------|----------------------|------|
| normalized `type` | stable `type` | MUST map through the catalog in §6.3; arbitrary adapter values MUST NOT be persisted |
| `nativeType` | Not persisted | Native runtime detail used only during adapter normalization |
| `invocationId` / `sessionId` / `turnId` | Preserved under the same names | MUST match the execution context |
| Execution context `appName` / `userId` | `appName` / `userId` | Persisted for store-level scope isolation; MUST match the ADK session tuple |
| `author` | `author` | Preserved |
| `content` / `actions` / `usage` | `content` / `actions` / safe `haas` fields | Preserved or summarized after `redact()` according to the stable event type |
| `safe` | Not persisted | Replaced by `redactionApplied` |
| — | `eventId` / `sequenceNumber` / `observedAtMs` / `harnessId` / `adapterId` | Generated by Event Log or execution context |

For invocation-scoped types, `invocationId` and `turnId` are non-null and
`sequenceNumber` starts at 0 and is gapless within that invocation. Session-scoped
lifecycle types that occur outside a turn use `invocationId=null` and `turnId=null`;
their `sequenceNumber` is gapless in the session lifecycle namespace. The session
stream orders all records by session-scoped `eventId`, which remains the replay
cursor across both namespaces. `observedAtMs` is the epoch-millisecond append time.
A raw event that has not passed through `redact()` MUST NOT be persisted; the
operation fails closed.

### 6.2 ADK Projection (Public)

```json
{
  "id": "evt_0000000001042",
  "invocationId": "inv_abc",
  "author": "codex",
  "timestamp": 1743712220.385936,
  "content": { "role": "model", "parts": [{ "text": "text" }] },
  "actions": { "stateDelta": {}, "artifactDelta": {}, "requestedAuthConfigs": {} },
  "longRunningToolIds": []
}
```

Stable HaaS `type`, `haas`, `sequenceNumber`, `sessionId`, `turnId`, `harnessId`,
and internal fields such as `adapterId` MUST NOT enter the ADK projection. ADK
`/run` and `/run_sse` remain pure ADK Event surfaces.

### 6.3 Stable HaaS Event Types and Metadata

HaaS native streams publish `CanonicalHaasEvent`. The top-level `type` is a stable
fact name. The `haas` object is a typed, redacted payload for that event and MUST
NOT be used as an arbitrary debug-data bag.

| Stable `type` | Required `haas` fields | Semantics |
|---------------|------------------------|-----------|
| `haas.output.text.delta` | optional `itemId`, `modelCallId`, `messagePhase` | Incremental model-visible text; text remains in `content.parts[]`; authoritative `messagePhase=commentary|final_answer` distinguishes progress from a result without parsing prose |
| `haas.output.reasoning.delta` | optional `itemId`, `summaryIndex`, `modelCallId` | Redacted provider reasoning summary permitted by policy; never ordinary commentary or raw chain-of-thought |
| `haas.output.item.completed` | `itemId`; optional `modelCallId`, `messagePhase` | Model-visible item lifecycle completed; lets clients finalize an initially unknown message phase without repeating text |
| `haas.tool.started` | `toolCallId`, `toolName`, `safeSummary`; optional `activityKind`, `commandPreview`, `workingDirectory`, `evidenceRef`, `evidenceExpiresAtMs` | Tool execution started |
| `haas.tool.output` | `toolCallId`, `toolName`, `safeSummary`; optional `activityKind`, `commandPreview`, `workingDirectory`, `outputPreview`, `omittedLineCount`, `evidenceRef`, `evidenceExpiresAtMs` | Redacted intermediate tool output/status; never an unbounded or unredacted tool result |
| `haas.tool.completed` | `toolCallId`, `toolName`, `status`; optional `activityKind`, `commandPreview`, `workingDirectory`, `durationMs`, `exitCode`, `outputPreview`, `omittedLineCount`, `evidenceRef`, `evidenceExpiresAtMs` | Tool execution completed successfully |
| `haas.tool.failed` | `toolCallId`, `toolName`, `status`, `safeReason`, `retryable`; optional `activityKind`, `commandPreview`, `workingDirectory`, `durationMs`, `exitCode`, `outputPreview`, `omittedLineCount`, `evidenceRef`, `evidenceExpiresAtMs` | Tool execution failed |
| `haas.usage.updated` | `usage`; optional `scope`, `modelCallId`, `cumulativeUsage` | Actual normalized usage for the declared scope. New model-call-aware producers use `scope=model_call`, bind `usage` to `modelCallId`, and may include the thread/turn cumulative snapshot separately; no provider payload, estimates, allocation across child steps, or fabricated zero event |
| `haas.turn.started` | `status` | Invocation/turn started |
| `haas.turn.completed` | `status` | Successful terminal event |
| `haas.turn.failed` | `status`, `code`, `safeReason`, `retryable` | Failed terminal event |
| `haas.turn.incomplete` | `status`, `code`, `safeReason`, `retryable` | Budget/timeout truncation terminal event |
| `haas.turn.interrupted` | `status`, `controlIntent` | Resumable pause terminal for the source invocation |
| `haas.turn.cancelled` | `status` | Cancelled terminal event |
| `haas.delegation.session_created` | `delegatedSessionId` | Delegated contract created |
| `haas.delegation.session_bound` | `delegatedSessionId` | Manager/HaaS session binding established |
| `haas.delegation.policy_snapshot` | `delegatedSessionId`, `policyVersion` | Policy snapshot fixed |
| `haas.delegation.policy_updated` | `delegatedSessionId`, `policyVersion` | Explicit policy update committed |
| `haas.delegation.policy_update_pending` | `delegatedSessionId`, `updateId`, `revision`, `fields` | Desired snapshot durably accepted; reconciliation runs independently of future turns |
| `haas.delegation.policy_update_applied` | `delegatedSessionId`, `updateId`, `revision`, `fields` | Runtime readback verified application and appliedRevision advanced |
| `haas.delegation.policy_update_failed` | `delegatedSessionId`, `updateId`, `revision`, `fields`, `code`, `safeReason` | Application failed; appliedRevision unchanged and future turns gated |
| `haas.delegation.restore_started` | `delegatedSessionId`, `containerGeneration` | Runtime restore started |
| `haas.delegation.restore_failed` | `delegatedSessionId`, `safeReason`, `retryable` | Runtime restore failed |
| `haas.delegation.workspace_lock_queued` | `delegatedSessionId`, `canonicalWorkspaceHash`, `queuePosition` | Waiting for workspace writer lock |
| `haas.delegation.workspace_lock_acquired` | `delegatedSessionId`, `canonicalWorkspaceHash` | Workspace writer lock acquired |
| `haas.delegation.workspace_lock_released` | `delegatedSessionId`, `canonicalWorkspaceHash` | Workspace writer lock released |
| `haas.delegation.container_ttl_destroyed` | `delegatedSessionId`, `containerGeneration` | Idle runtime resources destroyed |
| `haas.profile.created` | `profileId`, `profileVersion`, `profileFingerprint` | Profile revision created |
| `haas.profile.validation_failed` | `profileId`, `profileVersion`, `safeReason` | Profile revision validation failed |
| `haas.profile.activated` | `profileId`, `profileVersion`, `profileFingerprint` | Profile revision became the harness active profile |
| `haas.profile.retired` | `profileId`, `profileVersion`, `retiredByProfileId` | Profile revision was retired by a newly activated revision |
| `haas.profile.rebind_requested` | `profileId`, `profileVersion`, `sessionId` | Existing session requested explicit profile rebind |
| `haas.profile.rebind_applied` | `profileId`, `profileVersion`, `sessionId` | Rebind committed; future invocations use the new snapshot |
| `haas.approval.required` | `approvalId`, `kind`, `safeSummary`, `policyReason`, `availableDecisions`, `expiresAtMs` | Explicit manager decision required |
| `haas.approval.resolved` | `approvalId`, `status` | Approval resolved |
| `haas.input.required` | `inputRequestId`, `questions`, `blocking`, `expiresAtMs` | Structured user input is required; distinct from side-effect approval |
| `haas.input.resolved` | `inputRequestId`, `status` | Structured input was answered, expired, or cancelled |
| `haas.plan.updated` | `counts.pending`, `counts.inProgress`, `counts.completed`, `total` | Structured task-progress counts changed; plan text is intentionally omitted |
| `haas.adapter.event_unparsed` | `safeReason`, `retryable` | Native adapter event could not be mapped safely |

Terminal event types are exactly `haas.turn.completed`, `haas.turn.failed`,
`haas.turn.incomplete`, `haas.turn.interrupted`, and `haas.turn.cancelled`. Exactly one terminal type is
persisted per invocation. Consumers MUST use `type`, not human text or stream
closure, to distinguish terminal outcome. Stream closure remains only the delivery
completion signal.

Adapter normalized types map as follows in the initial contract:

| Adapter-normalized type | Stable HaaS type |
|-------------------------|------------------|
| `harness.text.delta` | `haas.output.text.delta` |
| `harness.reasoning.delta` | `haas.output.reasoning.delta` |
| `harness.output.item.completed` | `haas.output.item.completed` |
| `harness.tool.started` | `haas.tool.started` |
| `harness.plan.updated` | `haas.plan.updated` |
| `harness.tool.output` | `haas.tool.output` |
| `harness.tool.completed` | `haas.tool.completed` |
| `harness.tool.failed` | `haas.tool.failed` |
| `harness.usage` | `haas.usage.updated` |
| `harness.turn.started` | `haas.turn.started` |
| `harness.turn.completed` | `haas.turn.completed` |
| `harness.turn.failed` | `haas.turn.failed` |
| `harness.turn.incomplete` | `haas.turn.incomplete` |
| `harness.turn.interrupted` | `haas.turn.interrupted` |
| `harness.turn.cancelled` | `haas.turn.cancelled` |

The Event Log MUST map the adapter's explicit normalized `type`; it MUST NOT infer a canonical type only from `content` or `actions`. In particular, a policy-approved redacted summary part marked `thought=true` remains `haas.output.reasoning.delta` and MUST NOT become assistant output; raw reasoning never reaches Event Log. An `artifactDelta` does not turn a tool event into `haas.adapter.event_unparsed`. `item/started` and `item/completed` are the authoritative lifecycle boundaries for a tool call; output deltas are optional intermediate updates correlated by `toolCallId`. Every started tool has exactly one completed or failed terminal record, including cancellation and adapter failure.

`itemId` and `modelCallId` are presentation-neutral correlation facts. `itemId` keeps
all deltas for one model-visible message or reasoning-summary item together.
`modelCallId` identifies one actual model round trip within the turn and is stable in
stored replay; for a runtime without a native call id the adapter assigns a deterministic
invocation-scoped ordinal such as `mcall_0002`. Output, reasoning, the tool calls they
trigger, and the corresponding model-call usage snapshot carry the same id. A later
model output after tool completion opens a new model call. Consumers MUST preserve event
order and MUST NOT concatenate reasoning into commentary or final-answer text. Missing
correlation on legacy events remains unknown; consumers may form a single legacy group
but MUST NOT invent per-call usage.
If an agent-message delta arrives before its phase is known, it remains unclassified. The
adapter emits `haas.output.item.completed` with the same `itemId` when the native lifecycle
later supplies an authoritative `commentary|final_answer` phase. This fact contains no repeated
message text. Consumers reclassify the existing item in place; they MUST NOT duplicate output
or delay all streaming until item completion.

`haas.usage.updated` reports measured values only. `scope=model_call` means `usage` is
the actual usage for that model call; `cumulativeUsage`, when present, is a separate
turn/thread snapshot and is not added again when computing the sum of model calls.
`reasoningOutputTokens` and cache read/write counters are retained when the harness
reports them. Tool execution has no model-token charge of its own unless a harness emits
a separately scoped measured usage record. A consumer MUST NOT distribute a call's input
or output tokens over commentary, reasoning, tool, or result child events by text length,
time, count, or any other estimate.
Canonical `inputTokens` includes all prompt input reported for the call; `cacheReadTokens` is
the cached subset, not an extra amount to add. `outputTokens` includes reasoning output when
the provider does so; `reasoningOutputTokens` is the reasoning subset. Stage and turn totals
therefore sum input plus output once and present cache/reasoning as `of which` detail.

Public process events carry only bounded, redacted facts. A reasoning event contains a policy-approved progress summary, not raw chain-of-thought. Tool metadata MAY include `activityKind=command|read|search|edit|tool`; this classifies the observed operation without prescribing UI layout or copy. It MUST be produced by the adapter or policy catalog, not inferred later from tool-name strings, arguments, or summaries. Unknown operations use `tool`.
For command activities, `commandPreview` is a one-line bounded preview with credential values and signed-URL material removed; `workingDirectory` is a container-relative or otherwise non-host-sensitive hint. Neither field may contain an absolute host path. They are durable identification hints, not substitutes for the transient evidence body.

A tool start contains `toolCallId`, stable `toolName`, and a sentence-like `safeSummary`. The summary describes the safe operation when known; it MUST NOT serialize metadata (`key=value`), repeat `Used <toolName>`, include an absolute host path, or invent a target absent from the native event. A generic operation with no safe detail MAY use a stable neutral summary such as `Run tool`; clients localize their own fallback title rather than displaying protocol field names.

Tool output and terminal metadata MAY carry `outputPreview`, `omittedLineCount`, `durationMs`, and `exitCode`. `outputPreview` is sanitized before persistence, limited to 20 logical lines and 4096 UTF-8 bytes, and preserves head/tail meaning with `omittedLineCount` when content is removed. These are observable execution facts, not rendering instructions; clients choose a smaller normal-view preview. A command with a non-zero exit code emits `haas.tool.failed`, not `haas.tool.completed`. Full command arrays, complete stdout/stderr, file contents, host paths, and model-provider payloads remain private. Command output up to the 8 MiB execution-evidence bound may be read only through the separately authorized transient evidence endpoint; output beyond that bound requires an Artifact Store object. Neither path weakens the event payload. Token-level text/reasoning deltas MAY be coalesced for delivery and storage, but ordering relative to tool and terminal events MUST be preserved.

Command lifecycle events MAY additionally carry an opaque `evidenceRef` and
`evidenceExpiresAtMs`. These fields are locators, never command or URL content. They refer to
the non-durable execution-evidence response defined by Security Boundary §7.1. Event replay
may retain an expired reference so clients can explain why evidence is unavailable; consumers
MUST treat `410` as expired evidence rather than a missing tool result. An evidence reference
does not weaken the redaction rules for `safeSummary` or `outputPreview`.

The terminal write is idempotent per invocation. If the adapter already emitted a normalized terminal event, Session Runtime updates invocation/turn state from that same event and MUST NOT append a second synthetic terminal. A synthetic terminal is permitted only when no adapter terminal exists. Duplicate terminal events are a release-blocking integrity failure.

Unknown native runtime messages MUST NOT be copied into `type` or `haas`. The
adapter first attempts to map them to a known normalized type. If safe mapping is
impossible, Event Log writes `haas.adapter.event_unparsed` with only a stable safe
reason and retryability. If terminal outcome cannot be determined, Session Runtime
fails the invocation rather than forwarding the native event.

Host paths, full tool arguments/results, raw prompts or reasoning, credentials,
provider response bodies, native event names, and internal container addresses MUST
NOT appear in public native events.

#### 6.3.1 Stored-Event Migration

`CanonicalEventRecord` schema version 2 adds required `type` and `haas`. A store
opening version-1 records MUST migrate forward before serving native replay:

- a trusted historical terminal `actions.stateDelta.status` maps to the matching
  `haas.turn.*` type and typed status metadata;
- an otherwise non-terminal record containing model text maps to
  `haas.output.text.delta` with empty `haas`;
- any other record maps to `haas.adapter.event_unparsed` with
  `safeReason=historical_event_type_unknown` and `retryable=false`;
- migration MUST NOT reconstruct tool, approval, delegation, or native runtime types
  from human text, adapter ids, or other heuristics;
- migrated records retain `eventId`, scope ids, ordering, timestamps, content, and
  actions. Migration is idempotent and forward-only.

The optional tool activity fields introduced after schema version 2 do not require a
stored-record rewrite. Older records replay unchanged; consumers treat a missing or
unknown `activityKind` as `tool` and leave unavailable duration, exit code, preview, and
omission count absent. Consumers MUST NOT reconstruct those facts from `safeSummary`.
Missing `evidenceRef` means that detailed transient evidence was never captured or is no
longer addressable; old events require no migration.

### 6.4 HaaS Native Projection and SSE Frame

`project_haas()` emits the public `CanonicalHaasEvent` and strips internal
`userId`, `adapterId`, `schemaVersion`, and `redactionApplied` fields:

```json
{
  "type": "haas.approval.required",
  "eventId": "evt_0000000001042",
  "invocationId": "inv_abc",
  "sessionId": "hsess_abc",
  "turnId": "turn_abc",
  "appName": "chrn_codex_default",
  "harnessId": "chrn_codex_default",
  "author": "codex",
  "sequenceNumber": 7,
  "content": {"role": "model", "parts": []},
  "actions": {"stateDelta": {}, "artifactDelta": {}},
  "haas": {
    "approvalId": "appr_abc",
    "kind": "tool",
    "safeSummary": "Run a command in the workspace",
    "policyReason": "tool_requires_approval"
  },
  "observedAtMs": 1786400000000
}
```

Each HaaS native SSE `data:` frame contains exactly one such JSON object. ADK
`/run_sse` continues to use the ADK projection from §6.2 and never emits these
HaaS-only top-level fields. For session-scoped lifecycle events outside a turn,
`invocationId` and `turnId` are `null`.

Heartbeat, as an SSE comment that produces no event:

```text
: keep-alive

```

## 7. Runtime Model and State Machine

```text
append canonical event
  -> assign invocation sequence
  -> persist
  -> publish to live subscribers
  -> projection requested by stream
  -> SSE frame flush

client reconnect
  -> validate cursor (Last-Event-ID / after_event_id)
  -> replay retained events after cursor
  -> expired/missing cursor -> 410 haas_offset_expired; do not subscribe or guess
  -> subscribe live
```

Ordering invariants:

- Persisted events within an invocation are emitted in production order without gaps. Subscriber delivery MUST NOT silently drop or merge them.
- Event ordering for the same item is stable.
- Terminal state persistence is ordered before terminal event publication:
  Session Runtime MUST commit invocation/turn/session terminal state before
  appending/yielding a terminal event, or use a backend transaction that makes
  the terminal state and terminal event visible atomically.
- The ADK event array returned by non-streaming `/run` MUST equal the complete ordered ADK event sequence from `/run_sse` (parity), including accepted failure/incomplete/cancel terminal events.
- `/run_sse` MUST publish events while the invocation is running. It MUST NOT wait
  for invocation terminal state and then replay a completed batch.
- Stream closure signals delivery completion only; canonical terminal `type` determines execution outcome.
- `haas.output.text.delta` is an append-only UTF-8 text fragment. Consumers concatenate fragments once in `sequenceNumber` order after deduplication by `eventId`; it is never a replacement snapshot. A final assistant message is a client projection of accumulated deltas, not a second canonical text event.
- Tool events correlate by `haas.toolCallId`; started precedes zero or more output events and exactly one completed/failed event.

## 8. Security and Authorization

- Reading events requires session/invocation ownership; cross-scope access returns 404 or an auth error.
- Event-log reads and replay MUST be scoped by the full ADK session tuple
  `(appName, userId, sessionId)`. Because `sessionId` is caller-controlled,
  neither session replay nor invocation replay may use a bare `sessionId` as
  the persistence lookup key.
- HaaS native `/v1/haas/sessions/{session_id}/events` resolves the bare path id
  to exactly one caller-visible `(appName, userId, sessionId)` before reading.
  If no visible session or more than one visible session matches, it returns
  `404 session_not_found`; it MUST NOT merge or guess between scopes.
- Raw adapter events MUST be redacted before append.
- `include_debug=true` requires explicit debug/admin scope and still MUST NOT include credentials.
- Tool input/output payloads are summarized by default; full payloads require a separately approved debug design.

## 9. Observability

Metrics:

- `haas_sse_clients{scope}`
- `haas_sse_event_total{adapterBase}`
- `haas_sse_replay_total{scope}`
- `haas_sse_replay_gap_total{scope}`
- `haas_sse_client_disconnect_total{scope,reason}`
- `haas_event_log_append_duration_ms`
- `haas_event_log_lag_ms`

Logs:

- `haas.event.appended`
- `haas.event.replay_started`
- `haas.event.replay_gap`
- `haas.sse.client_connected`
- `haas.sse.client_disconnected`

## 10. Failure and Recovery

| Scenario | Behavior |
|----------|----------|
| Client disconnects | Do not cancel the invocation; continue writing the event log |
| ADK `/run_sse` `Last-Event-ID` expired | Return `410 haas_offset_expired`; never guess or restart the turn |
| Native SSE/page `after_event_id` expired | Return `410 haas_offset_expired` by default. A future explicit `replay_policy=reconcile` extension may emit a typed gap event; implicit reconcile is prohibited |
| Subscriber delivery queue is full | Do not drop or coalesce persisted events. Disconnect the slow subscriber after recording its last delivered event id; client reconnects/replays. Invocation execution and terminal persistence continue |
| Required terminal append fails after acceptance | The invocation MUST NOT claim `completed`. Session Runtime persists failed authoritative state and an idempotent integrity envelope (`accepted=true`, invocationId, HTTP 503 `haas_store_unavailable`). Before headers return it; after SSE headers abort and require readback. Do not fabricate a terminal event. |
| Active stream has no event before client idle timeout | Emit heartbeat comments periodically at an interval below every supported client/proxy idle timeout; do not close or cancel the invocation |
| Proxy buffering | Set `Cache-Control: no-cache` on the response; tests verify progressive flushing |
| Adapter emits duplicate terminal events | Retain the first terminal event and write a diagnostic warning for subsequent ones |
| Delegated approval is pending | Keep the stream open with heartbeats; terminal state is written only after approval resolution, cancellation, or timeout |

## 11. Test Plan and Acceptance Criteria

- Unit: stable type mapping, type-specific `haas` required fields, sequence allocation, terminal uniqueness, heartbeat without cursor advancement, and both ADK/native projection mappings.
- Integration: accepted completed/failed/incomplete/cancelled `/run` versus `/run_sse` HTTP-200 event parity, pre-acceptance HTTP error behavior, terminal-store integrity failure behavior, bounded `events-page` ordering/cursors/limits, over-budget ADK Session 413, invocation status readback, paginated waiting approvals, and reconnect replay.
- Integration: `/run_sse` progressively flushes output before terminal state.
- Heartbeat: a silent tool or model phase longer than Manager's 90-second idle timeout keeps the stream open with repeated comments, does not advance event cursors, and does not trigger invocation cleanup.
- Integration: output/tool/usage/terminal plus manager-delegation restore, queue, and approval events appear in native streams with stable `type` and validated redacted `haas` metadata.
- Interaction: approval and input-required events stay non-terminal while waiting, survive disconnect/replay, resolve exactly once, and preserve causal ordering before the continued tool/model events.
- Golden mapping: mixed text/reasoning/tool lifecycle input retains explicit adapter types; thought text never becomes assistant output; each `toolCallId` has one start and one terminal; unknown events contain no raw native payload.
- Integrity: an adapter terminal plus `finalize_turn` produces one canonical terminal; replay and reconnect cannot create a second terminal.
- Scale: a long tool-heavy turn preserves causal order while bounded coalescing prevents token-level events from growing the event store without limit.
- Backpressure: a slow client does not block adapter terminal-event persistence and receives no silently dropped/coalesced events; disconnect plus replay reconstructs the identical sequence.
- Folding: duplicate replay ids produce no duplicate text; ordered text deltas concatenate exactly once; toolCallId transitions are valid; final assistant projection is identical for live and replay paths.
- Security: raw prompts, Authorization, cookies, native event names, and complete tool arguments/results do not enter public native events; internal-only fields are stripped by `project_haas()`.
- Security: same bare `sessionId` in different users or apps never mixes events;
  cross-user native event replay returns 404.
- Compatibility: an ADK client successfully parses each `/run_sse` event, with correct stream-close semantics.
