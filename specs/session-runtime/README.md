# Session Runtime Component Specification

**English** | [简体中文](README.zh-CN.md)

Status: Draft
Last reviewed: 2026-09-13
Change ID: unified-runtime-approval-policy
Related specs: [HaaS Protocol](../haas-protocol/README.md), [Harness Registry](../harness-registry/README.md), [Harness Profile](../harness-profile/README.md), [Harness Adapter](../harness-adapter/README.md), [Event Log & SSE](../event-log-sse/README.md), [Admission Control](../admission-control/README.md), [Manager Delegation](../manager-delegation/README.md)

## 1. Component Role

Session Runtime is HaaS's source of truth for execution. It manages sessions,
invocations—one ADK `/run`—turns, containers, leases, idempotency, profile
snapshots, and terminal states, and drives concrete agents through Harness Adapter.

An ADK session is uniquely identified by the `(appName, userId, sessionId)` tuple. An `invocation` is the public unit of execution, while a `turn` is an adapter-internal unit of execution. They have a one-to-one relationship in the initial release, but Session Runtime MUST preserve room to split one invocation into multiple internal turns or replay turns in the future.

## 2. Sources and Rationale

| Source | Adopted concepts |
|--------|------------------|
| ADK 2.0 | Session tuple, `/run`, `/run_sse`, stateDelta, and invocation lifecycle |
| `mpa-codex-worker` session registry | Runtime session registry, checkpoints, recovery, and terminal-state discipline |
| Admission Control | Pre-execution admission through quotas, rate limits, and queues |
| Overview requirements | Adapter contract, event log, and SSE |

## 3. Upstream and Downstream Relationships

| Direction | Component | Relationship |
|-----------|-----------|--------------|
| Upstream | HaaS Protocol | Receives `/run`, `/run_sse`, and session GET/PATCH/DELETE |
| Upstream | Admission Control | Grants pre-execution admission |
| Downstream | Harness Registry | Resolves appName and freezes the effective harness configuration |
| Downstream | Harness Adapter | Executes, cancels, resumes, and inspects turns |
| Downstream | Event Log & SSE | Appends lifecycle, progress, and terminal events |
| Downstream | Artifact Store | Manages container/file metadata |
| Downstream | Observability | Records state, queues, latency, and failures |

## 4. Responsibility Boundaries

Responsibilities:

- Create and read `Session`, `Invocation`, `Turn`, and `Container` objects.
- Resolve `(appName, userId, sessionId)`, validate principal scope, and create or reuse a session.
- Reserve idempotency after request validation and before execution.
- Ensure that only one active invocation/turn runs in a session at a time, returning `session_busy` otherwise.
- Maintain the active session lease for the whole turn through periodic renewal and use store fencing tokens for every turn-owned write.
- Freeze the active harness profile as `EffectiveHarnessProfile` when the session is created.
- Map adapter events into stable canonical `haas.*` event types through Event Log and maintain session `state` from ADK `stateDelta`; terminal outcome uses canonical `type`, not human text or stream closure.
- Ensure consistent terminal states for streaming `/run_sse` and non-streaming `/run`.
- For delegated sessions, persist the HaaS session, native session reference,
  delegated-session reference, approval waits, and runtime-generation metadata needed
  for follow-up restoration.
- Manage cancellation, timeout, step budget, session expiry, and session deletion.
- After a sidecar restart, recover recoverable sessions from persistent state or fail closed into a non-recoverable state.

Non-responsibilities:

- Does not parse native harness events.
- Does not store raw prompts in default logs.
- Does not directly access providers or MCP servers.
- Does not decide tool, network, or workspace policy; it consumes Policy Controller output.
- Does not enforce cross-request quotas or rate limits; that is Admission Control's responsibility.

## 5. Core Interfaces

### 5.1 Public API Ownership

| Endpoint | Runtime behavior |
|----------|------------------|
| `POST /run` | Creates an invocation, collects events without streaming, and returns them in one response |
| `POST /run_sse` | Creates an invocation and returns streaming SSE |
| `GET /apps/{app}/users/{user}/sessions/{sid}` | Returns the session, including state and events |
| `PATCH /apps/{app}/users/{user}/sessions/{sid}` | Applies `stateDelta` idempotently |
| `DELETE /apps/{app}/users/{user}/sessions/{sid}` | Deletes the session and cancels active work |
| `GET /v1/haas/sessions/{sid}/invocations/{id}` | Reads authoritative invocation state for recovery |
| `GET /v1/haas/sessions/{sid}/approvals` | Lists redacted approval records with status/cursor pagination |
| `GET /v1/haas/sessions/{sid}/input-requests` | Lists redacted structured input records with status/cursor pagination |
| `POST /v1/haas/sessions/{sid}/invocations/{id}/pause` | Interrupts a running invocation and commits resumable `interrupted` only after observing its native terminal |
| `POST /v1/haas/sessions/{sid}/invocations/{id}/continue` | Creates and streams a new invocation/turn on the same logical session from an `interrupted` source |
| `POST /v1/haas/sessions/{sid}/invocations/{id}/cancel` | Idempotently cancels a running invocation through the HaaS native API |
| `POST /v1/haas/sessions/{sid}/approvals/{approval_id}` | Resolves a manager approval decision for a waiting harness action |
| `POST /v1/haas/sessions/{sid}/input-requests/{input_request_id}` | Answers a waiting structured input request and resumes the original invocation |
| `POST /v1/haas/sessions/{sid}/policy` | Stages an authorized approval/network/workspace policy revision for later invocations |

### 5.2 Internal API

```python
async def run(app_name: str, user_id: str, session_id: str | None, message: Message, ext: RunExtensions) -> InvocationRecord: ...
async def get_session(app_name: str, user_id: str, session_id: str) -> SessionRecord: ...
async def apply_state_delta(app_name: str, user_id: str, session_id: str, delta: dict) -> SessionRecord: ...
async def delete_session(app_name: str, user_id: str, session_id: str) -> None: ...
async def start_turn(req: TurnStartRequest) -> TurnRecord: ...
async def mark_turn_terminal(turn_id: str, result: TurnTerminalResult) -> None: ...
async def get_invocation(session_id: str, invocation_id: str) -> InvocationRecord: ...
async def list_approvals(session_id: str, status: str, cursor: str | None, limit: int) -> ApprovalPage: ...
async def list_input_requests(session_id: str, status: str, cursor: str | None, limit: int) -> InputRequestPage: ...
async def pause_invocation(session_id: str, invocation_id: str) -> InvocationRecord: ...
async def continue_invocation(session_id: str, invocation_id: str, instruction: str | None) -> InvocationRecord: ...
async def cancel_invocation(session_id: str, invocation_id: str) -> InvocationRecord: ...
async def resolve_approval(session_id: str, approval_id: str, decision: ApprovalDecision) -> ApprovalRecord: ...
async def answer_input_request(session_id: str, input_request_id: str, answers: InputAnswers) -> InputRequestRecord: ...
async def update_policy(session_id: str, expected_revision: int, policy: PolicyMutation) -> SessionRecord: ...
async def rebind_profile(session_key: SessionKey, profile_id: str, expected_version: int | None) -> SessionRecord: ...
async def reserve_idempotency(key: str, request_hash: str) -> IdempotencyReservation: ...
```

## 6. Data Model

### 6.1 SessionRecord

```json
{
  "id": "hsess_abc",
  "object": "session",
  "appName": "chrn_codex_default",
  "userId": "u_123",
  "tenantId": "tenant_1",
  "workspaceId": "workspace_1",
  "harnessId": "chrn_codex_default",
  "harnessBase": "codex",
  "status": "active",
  "controlState": "idle",
  "supportsResume": false,
  "resumableInvocationId": null,
  "containerId": "cntr_abc",
  "sandboxId": "sbx_abc",
  "state": {},
  "effectiveProfileSnapshot": {
    "profileId": "hprof_abc",
    "profileVersion": 12,
    "profileFingerprint": "sha256:profile",
    "providerFingerprint": "sha256:provider",
    "mcpVersion": "sha256:mcp",
    "skillsVersion": "sha256:skills",
    "agentsMdVersion": "sha256:agents-md",
    "workspacePolicyVersion": "sha256:workspace-policy"
  },
  "nativeSessionRef": {
    "adapterId": "codex-app-server",
    "opaque": "encrypted-or-private-ref",
    "generation": 1
  },
  "delegatedSessionRef": {
    "delegatedSessionId": "dgsess_abc",
    "managerSessionId": "mgr_sess_123",
    "containerGeneration": 3,
    "runtimeStatus": "idle"
  },
  "createdAtMs": 1786400000000,
  "updatedAtMs": 1786400000000,
  "expiresAtMs": null
}
```

`lastUpdateTime`, the public ADK field, equals `updatedAtMs / 1000.0` and is generated by the projection layer. Internal records store only epoch milliseconds.

`effectiveProfileSnapshot` (stored as the private Python field `effectiveProfile`) is a deep-copied internal execution contract and MUST NOT enter
ADK `Session` output. HaaS native session details or manager bindings may expose
only profile id/version/fingerprint and per-domain fingerprints. They MUST NOT
expose provider credentials, MCP headers, AGENTS.md content, host paths, or
adapter-native configuration.

### 6.2 InvocationRecord

```json
{
  "id": "inv_abc",
  "object": "invocation",
  "sessionId": "hsess_abc",
  "appName": "chrn_codex_default",
  "turnId": "turn_abc",
  "status": "accepted",
  "acceptedAtMs": 1786400000000,
  "startedAtMs": null,
  "completedAtMs": null,
  "model": "gpt-5.6-terra",
  "requestedModel": "gpt-5.6-terra",
  "idempotencyKeyHash": "idem_sha256",
  "terminalEventId": null,
  "continuedFromInvocationId": null,
  "continuedFromTurnId": null,
  "nativeTurnRef": {
    "adapterId": "codex-app-server",
    "threadId": "codex_thread_abc",
    "turnId": "codex_turn_abc",
    "generation": 1
  },
  "executionContext": {
    "sandbox": {"mode": "workspace-write"},
    "policy": {"approvalPolicy": "on-request", "network": {"defaultAction": "allow"}},
    "policyRevision": 1,
    "principalId": "p_123"
  },
  "sessionControl": {
    "controlState": "running",
    "supportsResume": false,
    "resumableInvocationId": null
  },
  "error": null
}
```

In the initial release, `turnId` and invocation are one-to-one, with distinct IDs and a persisted mapping in `InvocationRecord.turnId`. `acceptedAtMs` is written atomically with the initial `status=accepted` record and is the durable execution-acceptance boundary. `startedAtMs` remains null until adapter-owned execution begins. In a future one-to-many model, an invocation aggregates events from multiple turns, grouped by `turnId`. Internal timestamps uniformly use epoch milliseconds; the public surface is converted to ADK float seconds by the projection layer. `executionContext` is a private, deep-copied, non-credential snapshot of the effective sandbox, policy, and principal identity used for the native turn. `nativeTurnRef` is an adapter-owned private recovery reference. For Codex it includes the native app-server `threadId` and `turnId` needed to compensate an interrupted or restarted same-session execution. It MUST NOT enter public invocation, ADK, event, log, or GUI projections.

A fresh session starts at policy revision 1 with `workspace-write`, public network allow, and
`on-request` approval unless an authorized higher-level policy narrows it. Each invocation stores
the exact applied policy revision in its private execution context. A session policy mutation uses
optimistic expected-revision concurrency plus an idempotency key, persists desired/applied state,
and never changes an accepted invocation. New work waits while the revision is pending or failed.
The response always carries complete desired and applied snapshots so clients never render a
failed target as effective. Action-scoped approval resolution is separate from this API and never
increments policy revision.

### 6.3 TurnRecord

```json
{
  "id": "turn_abc",
  "invocationId": "inv_abc",
  "sessionId": "hsess_abc",
  "status": "running",
  "continuedFromTurnId": null,
  "startedAtMs": 1786400000000,
  "completedAtMs": null,
  "nativeTurnRef": {
    "adapterId": "codex-app-server",
    "threadId": "codex_thread_abc",
    "turnId": "codex_turn_abc",
    "generation": 1
  }
}
```

`SessionRecord` MUST project losslessly to an ADK `Session` (`{id, appName, userId, state, events[], lastUpdateTime}`) only when the complete projection stays within 1000 events and 8 MiB serialized. Larger reads return `413 haas_session_read_too_large` without truncation; clients use native `events-page`. Internal fields such as tenantId MUST NOT enter public output.

`nativeSessionRef` and `nativeTurnRef` are the durable compensation boundary for
harness-native conversation state. Session Runtime MUST persist `PreparedSession.nativeRef`
after prepare or resume, persist `TurnHandle.opaque` on both the invocation and turn after
`start_turn`, and update `nativeSessionRef` when the turn handle carries a stronger native
session key such as Codex `threadId`. A later invocation on the same logical HaaS session
MUST pass the stored native session reference to `ResumeSessionRequest` before starting the
next native turn. If the adapter reports `nonResumable`, the runtime keeps the logical HaaS
session and starts a new native conversation only with an explicit non-resumable marker in
the private state; it does not pretend that the old native context was restored.

### 6.4 Delegated Session Reference

For manager-delegated sessions, `SessionRecord.delegatedSessionRef` links the HaaS
session to the durable delegated-session contract defined in
[Manager Delegation](../manager-delegation/README.md). Session Runtime stores only the
HaaS-owned reference and runtime generation. The full mount manifest, policy snapshot,
manager session id, image digest, and provider credential reference are persisted by
the delegated-session store contract and MUST NOT be exposed through the ADK `Session`
projection.

The presence of `delegatedSessionRef` means:

- continuation uses the HaaS delegated-session restore path before starting a new turn;
- TTL cleanup may destroy the container but MUST NOT expire the HaaS session;
- `session_expired` is reserved for HaaS session retention expiry, not idle container TTL;
- failed restore moves the invocation to failed/non-resumable evidence and does not
  fall back to local execution.

### 6.5 ApprovalRecord

```json
{
  "id": "appr_abc",
  "sessionId": "hsess_abc",
  "invocationId": "inv_abc",
  "turnId": "turn_abc",
  "status": "waiting",
  "request": {
    "kind": "tool",
    "safeSummary": "Run shell command in /workspace",
    "policyReason": "tool_requires_approval",
    "availableDecisions": ["approved", "denied", "cancelled"],
    "availableScopes": ["action"],
    "expiresAtMs": 1786400900000
  },
  "decision": null,
  "createdAtMs": 1786400000000,
  "resolvedAtMs": null
}
```

Approval records are persisted so a stream disconnect or sidecar restart does not lose the pending decision. The record stores only a safe summary, policy reason, server-advertised decisions/scopes and an expiry no later than the invocation deadline, not full tool arguments or raw prompts. The only P0 scope is `action`: approval authorizes exactly the native action identified by this record and resumes the same invocation once. It never authorizes the rest of the turn/session and never mutates durable policy; durable changes use the revisioned `/policy` API. Repeating the same decision for an already-resolved approval is idempotent even when the caller lost its original idempotency key; the service returns the stored record and MUST NOT answer the native harness request again. A different decision remains `409 haas_approval_state_conflict`.

A non-success invocation terminal transition (`failed|incomplete|interrupted|cancelled`) atomically closes every still-waiting approval and input request owned by that invocation as `cancelled`, with the same terminal timestamp. These retained records are audit/recovery evidence but MUST NOT appear in `status=waiting` lists or be restored as interactive cards. A successful terminal may remain waiting until its blocking interaction resolves. This invariant is also reconciled on interaction reads so records written by an older process cannot expose a stale card after restart.

### 6.5.1 InputRequestRecord

```json
{
  "id": "inreq_abc",
  "sessionId": "hsess_abc",
  "invocationId": "inv_abc",
  "turnId": "turn_abc",
  "status": "waiting",
  "questions": [
    {
      "id": "scope",
      "header": "Review scope",
      "question": "Which change should be reviewed?",
      "options": ["Current diff", "Last two commits"],
      "allowText": true,
      "multi": false,
      "secret": false
    }
  ],
  "blocking": true,
  "createdAtMs": 1786400000000,
  "expiresAtMs": 1786400900000,
  "resolvedAtMs": null
}
```

Input requests are separate from approvals: an approval authorizes a proposed side effect, while an input request supplies task data. The record stores only renderable question metadata and an expiry no later than the invocation deadline. Non-secret answers may be retained in the manager-owned transcript; secret answers are delivered through a private handle and MUST NOT enter HaaS events, task state, logs, or transcripts.

### 6.6 State Merge Semantics

Session `state` has two write sources. Both are serialized through the same sessionKey write queue and use the same merge rules:

1. **Explicit client `PATCH stateDelta`**: deep-merge into `session.state`; recursively merge objects and overwrite scalars.
2. **Event `actions.stateDelta` during an invocation**: apply in arrival order through the same queue as PATCH, using deep merge.

Constraints: deletion is not allowed and requires an explicit extension field; on conflict, the last arriving scalar wins; merge is an idempotent pure function to support recovery replay.

### 6.7 Idempotency Replay Semantics

`IdempotencyStore.reserve(key_hash, request_hash)` records the first `request_hash`; see [Stores](../stores/README.md) §5. Subsequent requests with the same key behave as follows:

- Matching `request_hash` -> return the first result without starting the harness again (replay).
- Different `request_hash` -> fail closed and return `409 haas_idempotency_conflict`; do not silently return the old result, so the caller must handle the conflict explicitly.

Release is idempotent: every failure before `InvocationRecord(status=accepted)` is
persisted releases the reservation. Once accepted, the reservation is retained and
stores `accepted=true`, `invocationId`, HTTP status 200, and the ordered projected ADK
events. A matching retry while the invocation is running replays retained events from
the beginning and attaches to live delivery without creating another turn. A matching
retry after terminal state returns HTTP 200 and the identical complete event sequence,
including failed/incomplete/cancelled terminal events.

If required terminal-event persistence fails after acceptance, idempotency stores an
integrity-failure envelope with `accepted=true`, `invocationId`, HTTP 503, and the safe
`haas_store_unavailable` body. This exception is replayed as the same integrity error;
it MUST NOT be converted to `haas_adapter_error` or treated as permission to restart
the harness.

## 7. Runtime Model and State Machines

Delegated configuration updates use Manager Delegation §5.1.1: accepted/running/cancelling invocations retain the applied snapshot, while later acceptance waits for desiredRevision to be verified and applied. The fenced reconciler runs independently of new turns. Native state and worker receipts survive TTL on the session volume (Container Runtime §6); a native id alone is insufficient. Execution replay expiry follows Protocol §6.8: nonterminal reservations stay protected, expired terminal keys return haas_idempotency_expired, and a new Manager attempt is a separate invocation in the same logical session.

### 7.1 Invocation

```text
request_validated -> accepted -> running -> completed
request_validated -> accepted -> running -> incomplete
request_validated -> accepted -> running -> failed
request_validated -> accepted -> running -> pausing -> interrupted
request_validated -> accepted -> running -> cancelling -> cancelled
```

`interrupted` is terminal for the source invocation and turn, but resumable at
the session level. Pause and Stop are distinct intents even though the Codex
adapter uses the same native `turn/interrupt` primitive. Session Runtime records
the intent before sending the request, treats the RPC result as acknowledgement
only, and commits `interrupted` only after the matching native terminal is
observed. If natural completion wins the race, its terminal is preserved and
pause returns `409 haas_invocation_not_running`.

Continue accepts only the latest resumable `interrupted` invocation. It
validates/restores the native session, creates a new accepted invocation and
turn, and sets `continuedFromInvocationId` and `continuedFromTurnId` on the new
records. It inherits the source invocation's private execution context so workspace,
network, approval, and principal constraints cannot silently reset or be widened by a
Continue request. If an authorized policy revision was applied after the source invocation,
Continue uses that newer applied snapshot and records the change; otherwise it inherits the
source snapshot. Credentials are freshly resolved from the frozen session profile and
are never persisted in that context. Source records stay immutable. Reusing an `Idempotency-Key` returns the
same new invocation; concurrent control/run mutations are serialized by the
session lease. Continue never reuses the source invocation id.

Cancel also accepts the latest resumable `interrupted` source. In that case it
does not rewrite the immutable source invocation/turn or append a second terminal;
it atomically sets session `controlState=cancelled`, `supportsResume=false`, and
clears `resumableInvocationId`. Invocation readback and pause/cancel responses include
the current `sessionControl` projection so a reconnecting Manager can distinguish a
paused source from a dismissed one.

A Manager Stop that races invocation acceptance is delivered only after the accepted
`sessionId` and `invocationId` are known. The cancel HTTP response acknowledges current
state; it does not replace the terminal event. Session Runtime remains responsible for
driving the adapter cancellation and persisting exactly one `haas.turn.cancelled` event.
Consumers MUST continue replay/readback until that terminal is observed or expose a
recoverable cancellation failure. Closing the originating SSE connection alone never
transitions the invocation to `cancelled`.

The transition to `accepted` occurs only after the initial InvocationRecord is
durably persisted and before any native turn/provider/tool/workspace side effect.
Preflight failures before that write return structured HTTP errors and create no
invocation. All failures after that write converge on terminal state/events.

Terminal states are immutable. Every normally terminal accepted invocation returns
HTTP 200: `/run` returns the ordered ADK event array and `/run_sse` emits the terminal
ADK event before closing. Exactly one matching
canonical terminal type (`haas.turn.completed|failed|incomplete|interrupted|cancelled`) is
persisted per invocation; its `haas.status` MUST equal `InvocationRecord.status`. Before a terminal event
is appended to Event Log or yielded to an SSE/non-streaming caller, Session
Runtime MUST persist the corresponding invocation and turn terminal state and
merge terminal session state under the same lease/fencing guard, unless the
store backend provides a single atomic boundary that covers both terminal state
and terminal event writes. This ordering prevents recovery from observing a
completed/failed terminal event while invocation or turn records still read as
`running`.

An invocation terminal describes one harness turn; it is not sufficient evidence that the user's multi-step task is complete. Session Runtime persists the authoritative invocation/turn outcome and pending interaction state. Manager separately owns task-level plan, verification, and bounded-continuation state and MUST NOT convert `failed`, `incomplete`, `interrupted`, `cancelled`, or a pending blocking request into task completion.

### 7.2 Session

Session Runtime uses a short-lived active-turn lease to serialize writes for one `(appName, userId, sessionId)`. The default lease TTL is 30 seconds and the default renewal interval is 10 seconds; the renewal interval MUST remain less than half of the TTL. A turn whose configured timeout is longer than one lease TTL MUST keep renewing until it reaches a terminal state. Renewal tasks MUST be cancelled and awaited when the turn finishes normally, fails, is cancelled, or times out, so no background task survives the invocation.

Every event append and session/invocation/turn terminal write performed by the active turn MUST pass the store's holder/token fencing check. A stale holder whose lease expired or was taken over MUST NOT append more events, merge `stateDelta`, or overwrite terminal state. Because the invocation is already accepted, fencing loss converges on `haas.turn.failed` with a stable safe reason if the current fencing owner can persist it; it MUST NOT surface as an unaccepted HTTP 502. If no holder can persist terminal evidence, use the post-acceptance store-integrity failure contract.

```text
new -> active -> idle -> active
new -> active -> pausing -> paused -> resuming -> active
new -> active -> cancelling -> idle
new -> active -> expired
new -> active -> deleted
new -> active -> non_resumable
```

Rules:

- When the first `/run` omits `sessionId`, create `hsess_<rand>`; subsequent requests reuse the same `(appName, userId, sessionId)`.
- Only one invocation MAY run in a session at a time; concurrent requests return `409 session_busy`.
- `controlState`, `supportsResume`, and `resumableInvocationId` are persisted HaaS-native control facts. `controlState` uses `idle|running|pausing|paused|resuming|cancelling|cancelled|control_degraded`; none changes the ADK Session schema.
- A paused session rejects ordinary `/run` with `409 haas_resume_required`; only `continue` may create the next invocation.
- Cancel is irreversible for the logical session (`supportsResume=false`); pause preserves native state and `supportsResume=true`.
- `model` MAY change from run to run within the same session.
- `profile` does not drift when the harness active-profile pointer changes. If an
  existing session request specifies a different `haas.profileId/profileVersion`,
  return `409 haas_profile_rebind_required`. Explicit `profile-rebind` affects
  only new invocations after the rebind and MUST NOT modify a running invocation.
  A manager-delegated session (with `delegatedSessionRef`) MUST reject
  `profile-rebind` with `409 haas_profile_rebind_unsupported`; it is reconfigured
  only through the delegated policy update path, which accepts a new `profileRef`
  and replaces the effective snapshot in-place without a new session. If a
  delegated policy update arrives while an invocation is running, Session Runtime
  persists it as `pendingPolicyUpdate` and applies it before the next invocation
  starts; the running invocation is not modified and is never rejected because
  of the update.
- Session deletion first cancels active work, then makes history and artifacts unreachable.
- A manager-delegated session may transition between `active` and `idle` while its
  container transitions through `running`, `ttl_destroyed`, and `restoring`. Container
  TTL does not by itself expire the HaaS session or release the delegated binding.
- `/run_sse` MUST stream while the invocation is running by consuming Session Runtime's
  live event stream. It MUST NOT wait for `run()` to complete and then replay all
  events as a completed batch.

## 8. Security and Authorization

- Sessions, invocations, turns, containers, and files are isolated by principal scope; `userId` MUST belong to the authenticated principal.
- Cross-scope access returns 404, not 403.
- Input MAY be persisted, but raw secrets MUST first be rejected or redacted.
- `nativeSessionRef` is an internal opaque field and MUST NOT enter ADK Session output.
- Only a hash of the idempotency key is stored, never the original value.
- The session workspace MUST be confined to the session root supplied by Sandbox Runtime.

## 9. Observability

Session Runtime emits:

- `haas.run.accepted`
- `haas.run.idempotency_replayed`
- `haas.session.created`
- `haas.session.continued`
- `haas.session.busy`
- `haas.turn.started`
- `haas.turn.terminal`
- `haas.turn.cancel_requested`
- `haas.session.recovery_failed`

Metrics:

- `haas_sessions_active`
- `haas_invocations_running`
- `haas_turn_duration_ms`
- `haas_idempotency_replay_total`
- `haas_session_busy_total`
- `haas_turn_terminal_total{status,adapterBase}`

## 10. Failure and Recovery

Adapter calls (`prepare_session`, `start_turn`, `stream_events`, and `finalize_turn`) execute inside a single invocation timeout budget. The default timeout is 900 seconds and is configurable per `SessionRuntime`; the value MUST be greater than the lease TTL so normal long turns renew at least once before timing out. On timeout, Runtime cancels the in-flight adapter await, persists terminal invocation/turn/session state, writes canonical `type=haas.turn.failed` with `haas={status:"failed", safeReason:"timeout", retryable:false}` and the compatible ADK `actions.stateDelta.status/reason` projection, releases admission quota in the API layer, and completes any idempotency reservation with the failed response envelope so retries no longer observe a pending key or downgrade the HTTP status.

| Scenario | Behavior |
|----------|----------|
| Request fails before InvocationRecord acceptance write | Release the idempotency reservation; return structured 4xx/5xx; create no invocation/terminal event |
| InvocationRecord accepted write succeeds | Retain idempotency reservation with accepted=true/invocationId; all normal terminal outcomes replay as HTTP 200 plus identical ADK events |
| Sidecar process restarts | On the first authoritative invocation readback, inspect a persisted `running` invocation before returning it. If the adapter can prove ownership and resume/inspect the native turn, restore that exact invocation. Otherwise acquire the session lease with a fresh fencing token and converge the orphaned invocation and turn to non-resumable `incomplete`, append exactly one `haas.turn.incomplete` with `code=safeReason=sidecar_restart_execution_lost` and `retryable=true`, close its pending interactions, return the session control state to `idle`, and complete its accepted idempotency reservation from the canonical event history. A live unexpired lease owned by another process blocks reconciliation; readback MUST NOT overwrite that owner. Never classify this case as resumable `interrupted`, which is reserved for a confirmed native Pause terminal. |
| Cancel targets a persisted `running` invocation after restart, but the current process has no active adapter handle | First require the invocation to belong to the path session and require its linked session, turn, and harness records to exist. Then acquire the session lease with a new fencing token, persist matching invocation/turn `cancelled` state, merge the session `cancelled` projection only when no newer terminal invocation has superseded it, and append exactly one `haas.turn.cancelled` terminal event before returning. Never return the stale `running` record as if cancellation succeeded. Missing or mismatched records and lease conflict fail closed without mutating state or overwriting the current owner. |
| Adapter produces no terminal state | Cancel the adapter await and write terminal `failed` after timeout |
| Disconnect after cancellation | Persist cancellation intent; the final state MUST remain readable |
| Session lease renew fails or lease is fenced out | Stop stale writes; current fencing owner persists `haas.turn.failed` when possible. Do not return unaccepted adapter 502 for an accepted invocation; use integrity-failure recovery if terminal persistence is impossible |
| Required terminal event write fails after acceptance | Persist invocation/turn/session as failed in the authoritative state store, complete idempotency with accepted=true/invocationId and `503 haas_store_unavailable`, and emit fallback diagnostics. Before headers return that integrity error; after SSE headers abort and require readback. Do not fabricate terminal evidence. |
| Delegated container was destroyed by idle TTL | Restore runtime resources from the delegated-session contract before starting the next turn |
| Delegated restore fails | Return `haas_delegation_restore_failed` or a more specific delegation error; do not run locally |
| Approval bridge is waiting | Persist `ApprovalRecord`; resume the adapter only after manager resolves it |
| Invocation becomes failed/incomplete/cancelled with pending interactions | Atomically mark its waiting approvals and input requests `cancelled`; never restore their cards |
| Policy update conflicts with current revision | Return `409 haas_policy_revision_conflict` with safe current revision; do not partially apply |
| Policy application fails | Preserve prior applied revision, expose safe failure, and block new invocation start until corrected or superseded |
| Structured input is waiting | Persist an `InputRequestRecord`; keep the same invocation/turn active and keep its model capability valid; resume only after an exact manager answer |
| Adapter already emitted a terminal | Commit that event once; `finalize_turn` returns state but does not append another terminal |
| Active model-proxy capability is rejected | Attempt one exact-scope refresh; otherwise persist a stable failed terminal and partial progress |
| Explicit profile rebind validation fails | Reject the rebind and keep the previous `effectiveProfileSnapshot` |
| Active profile changes after session creation | Mark drift only; the existing session continues on its old snapshot until explicit rebind or a new session |

## 11. Test Plan and Acceptance Criteria

- Unit: ID generation, tuple parsing, scope, durable acceptance transition, no-side-effects-before-acceptance invariant, state transitions, terminal immutability, request hashes, accepted HTTP-200 idempotency replay, in-progress attach/replay, and terminal-store integrity failure replay.
- Integration: `/run` and `/run_sse` both return HTTP 200 with event parity for completed, failed, incomplete, interrupted, and cancelled accepted invocations; pre-acceptance failures remain structured 4xx/5xx; session read-back, PATCH stateDelta, pause/continue, cancellation, and DELETE remain aligned.
- Lifecycle: pause persists intent before native interrupt, waits for the matching interrupted terminal, and is idempotent. Continue creates exactly one linked invocation/turn on the same native session; ordinary `/run`, stale/non-latest source ids, missing native state, duplicate keys, and Pause/Stop/natural-completion races fail or converge exactly as specified without a replacement turn.
- Control waiter failure: every active-turn exit resolves its control waiter. If the stream exits before a terminal can be committed (including cancellation or fencing loss), a pending Pause fails promptly and readback converges to the persisted non-transitional state; it never waits forever on a process-local future.
- Concurrency: concurrent requests in the same session return `session_busy`; different sessions MAY run concurrently; a long turn exceeding one lease TTL remains protected by renewal.
- Recovery: simulate sidecar restart, adapter reconnect, missing native reference, expired session, stale holder fencing rejection, adapter timeout terminalization, and cancellation of a durable `running` invocation whose originating process-local adapter handle no longer exists. The latter must read back as `cancelled` with one matching terminal event and must remain idempotent on retry.
- Integration: `/run_sse` emits ADK events progressively while native replay emits stable canonical types; exactly one canonical terminal type matches persisted invocation status, and native replay can reconnect while the turn is running.
- Integration: delegated session continuation after container TTL restore reuses the HaaS session and native Codex reference when recoverable.
- Integration: after a profile update, new sessions use the new active revision and
  old sessions keep their frozen snapshot; after explicit rebind, the next
  invocation uses the new snapshot while running invocations are unaffected.
- Approval gate: pending approval survives SSE disconnect, is listable through the paginated native approvals API, and resolves through the HaaS native approval API. Until the full bridge passes its release gate, Codex reports `unattended_only`, returns an empty waiting page, and does not expose human approval UI.
- Interaction (P1): pending command/file approvals and structured input survive Manager/UI disconnect and restore to one visible card only while their invocation is nonterminal. Repeating the same approval decision is idempotent without a second native response; conflicting or terminal-cancelled stale answers fail closed.
- Policy revision: local and delegated sessions accept the same approval/network/workspace mutation
  semantics; running invocations remain frozen and queued/continued invocations use only an applied
  revision. Concurrent expected-revision conflicts and apply failures never run stale work.
- Task completion: an invocation terminal cannot erase pending plan/verification work. Manager acceptance covers completed, verifying, resumable incomplete, failed, cancelled, and continuation-budget-exhausted task states.
- Long turn: a turn longer than the stream-idle timeout and a tool-heavy turn both retain lease, model capability, and exactly one terminal event.
- Compatibility: align ADK client behavior for bounded session GET/PATCH/DELETE; over-budget session GET returns explicit 413 and native pagination never silently truncates.
- Security: all cross-principal access returns 404; secret-shaped input does not enter default logs.

## stream-timeout-approval-recovery

The invocation deadline must apply to the task currently awaiting adapter work. A task-bound timeout context must never span an async-generator yield: the first event and subsequent events may be consumed by different tasks. Preparation, start, each event wait and finalization share one monotonic deadline. Timeout persists one failed terminal, closes waiting interactions and releases resources. Test silent execution and approval waits after the first streamed event.
