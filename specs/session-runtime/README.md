# Session Runtime Component Specification

**English** | [简体中文](README.zh-CN.md)

Status: Draft
Last reviewed: 2026-09-07
Related specs: [HaaS Protocol](../haas-protocol/README.md), [Harness Registry](../harness-registry/README.md), [Harness Adapter](../harness-adapter/README.md), [Event Log & SSE](../event-log-sse/README.md), [Admission Control](../admission-control/README.md), [Manager Delegation](../manager-delegation/README.md)

## 1. Component Role

Session Runtime is HaaS's source of truth for execution. It manages sessions, invocations—one ADK `/run`—turns, containers, leases, idempotency, and terminal states, and drives concrete agents through Harness Adapter.

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
- Freeze the configured harness as `EffectiveHarnessConfig` when the session is created.
- Write adapter events to Event Log and maintain session `state` from ADK `stateDelta`.
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
| `POST /v1/haas/sessions/{sid}/invocations/{id}/cancel` | Idempotently cancels a running invocation through the HaaS native API |
| `POST /v1/haas/sessions/{sid}/approvals/{approval_id}` | Resolves a manager approval decision for a waiting delegated action |

### 5.2 Internal API

```python
async def run(app_name: str, user_id: str, session_id: str | None, message: Message, ext: RunExtensions) -> InvocationRecord: ...
async def get_session(app_name: str, user_id: str, session_id: str) -> SessionRecord: ...
async def apply_state_delta(app_name: str, user_id: str, session_id: str, delta: dict) -> SessionRecord: ...
async def delete_session(app_name: str, user_id: str, session_id: str) -> None: ...
async def start_turn(req: TurnStartRequest) -> TurnRecord: ...
async def mark_turn_terminal(turn_id: str, result: TurnTerminalResult) -> None: ...
async def cancel_invocation(session_id: str, invocation_id: str) -> InvocationRecord: ...
async def resolve_approval(session_id: str, approval_id: str, decision: ApprovalDecision) -> ApprovalRecord: ...
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
  "containerId": "cntr_abc",
  "sandboxId": "sbx_abc",
  "state": {},
  "effectiveHarnessConfig": {},
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

### 6.2 InvocationRecord

```json
{
  "id": "inv_abc",
  "object": "invocation",
  "sessionId": "hsess_abc",
  "appName": "chrn_codex_default",
  "turnId": "turn_abc",
  "status": "running",
  "startedAtMs": 1786400000000,
  "completedAtMs": null,
  "model": "gpt-5.6-terra",
  "requestedModel": "gpt-5.6-terra",
  "idempotencyKeyHash": "idem_sha256",
  "terminalEventId": null,
  "error": null
}
```

In the initial release, `turnId` and invocation are one-to-one, with distinct IDs and a persisted mapping in `InvocationRecord.turnId`. In a future one-to-many model, an invocation aggregates events from multiple turns, grouped by `turnId`. Internal timestamps uniformly use epoch milliseconds (`startedAtMs`/`completedAtMs`); the public surface is converted to ADK float seconds by the projection layer.

### 6.3 TurnRecord

```json
{
  "id": "turn_abc",
  "invocationId": "inv_abc",
  "sessionId": "hsess_abc",
  "status": "running",
  "startedAtMs": 1786400000000,
  "completedAtMs": null
}
```

`SessionRecord` MUST project losslessly to an ADK `Session` (`{id, appName, userId, state, events[], lastUpdateTime}`). Internal fields such as tenantId MUST NOT enter public output.

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
    "policyReason": "tool_requires_approval"
  },
  "decision": null,
  "createdAtMs": 1786400000000,
  "resolvedAtMs": null
}
```

Approval records are persisted so a stream disconnect or sidecar restart does not lose
the pending decision. The record stores only a safe summary and policy reason, not full
tool arguments or raw prompts.

### 6.6 State Merge Semantics

Session `state` has two write sources. Both are serialized through the same sessionKey write queue and use the same merge rules:

1. **Explicit client `PATCH stateDelta`**: deep-merge into `session.state`; recursively merge objects and overwrite scalars.
2. **Event `actions.stateDelta` during an invocation**: apply in arrival order through the same queue as PATCH, using deep merge.

Constraints: deletion is not allowed and requires an explicit extension field; on conflict, the last arriving scalar wins; merge is an idempotent pure function to support recovery replay.

### 6.7 Idempotency Replay Semantics

`IdempotencyStore.reserve(key_hash, request_hash)` records the first `request_hash`; see [Stores](../stores/README.md) §5. Subsequent requests with the same key behave as follows:

- Matching `request_hash` -> return the first result without starting the harness again (replay).
- Different `request_hash` -> fail closed and return `409 haas_idempotency_conflict`; do not silently return the old result, so the caller must handle the conflict explicitly.

Release is idempotent: failures before execution release the reservation. Once
execution begins, the reservation is retained and subsequent retries replay the
first result. The retained idempotency result is a response envelope, not just an
event array: it MUST include HTTP status, structured error body when present,
and projected events. Adapter failures that first returned `502
haas_adapter_error` therefore replay as HTTP 502 with the same error semantics.

## 7. Runtime Model and State Machines

### 7.1 Invocation

```text
accepted -> running -> completed
accepted -> running -> incomplete
accepted -> running -> failed
accepted -> running -> cancelling -> cancelled
```

Terminal states are immutable. `/run` returns the event array after a terminal
state; `/run_sse` closes the stream at a terminal state. Before a terminal event
is appended to Event Log or yielded to an SSE/non-streaming caller, Session
Runtime MUST persist the corresponding invocation and turn terminal state and
merge terminal session state under the same lease/fencing guard, unless the
store backend provides a single atomic boundary that covers both terminal state
and terminal event writes. This ordering prevents recovery from observing a
completed/failed terminal event while invocation or turn records still read as
`running`.

### 7.2 Session

Session Runtime uses a short-lived active-turn lease to serialize writes for one `(appName, userId, sessionId)`. The default lease TTL is 30 seconds and the default renewal interval is 10 seconds; the renewal interval MUST remain less than half of the TTL. A turn whose configured timeout is longer than one lease TTL MUST keep renewing until it reaches a terminal state. Renewal tasks MUST be cancelled and awaited when the turn finishes normally, fails, is cancelled, or times out, so no background task survives the invocation.

Every event append and session/invocation/turn terminal write performed by the active turn MUST pass the store's holder/token fencing check. A stale holder whose lease expired or was taken over MUST NOT append more events, merge `stateDelta`, or overwrite terminal state. Stale-write rejection maps to the existing adapter-failure path (`haas_adapter_error`) rather than introducing a public error code.

```text
new -> active -> idle -> active
new -> active -> cancelling -> idle
new -> active -> expired
new -> active -> deleted
new -> active -> non_resumable
```

Rules:

- When the first `/run` omits `sessionId`, create `hsess_<rand>`; subsequent requests reuse the same `(appName, userId, sessionId)`.
- Only one invocation MAY run in a session at a time; concurrent requests return `409 session_busy`.
- `model` MAY change from run to run within the same session.
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

Adapter calls (`prepare_session`, `start_turn`, `stream_events`, and `finalize_turn`) execute inside a single invocation timeout budget. The default timeout is 900 seconds and is configurable per `SessionRuntime`; the value MUST be greater than the lease TTL so normal long turns renew at least once before timing out. On timeout, Runtime cancels the in-flight adapter await, persists terminal invocation/turn/session state, writes a terminal `failed` event with `actions.stateDelta.status = "failed"` and `actions.stateDelta.reason = "timeout"`, releases admission quota in the API layer, and completes any idempotency reservation with the failed response envelope so retries no longer observe a pending key or downgrade the HTTP status.

| Scenario | Behavior |
|----------|----------|
| Request fails before execution | Release the idempotency reservation |
| Request has entered execution | Retain the idempotency reservation; subsequent retries replay the first result |
| Sidecar process restarts | Read the running state from the store and call adapter inspect/resume; fail closed if the state cannot be confirmed |
| Adapter produces no terminal state | Cancel the adapter await and write terminal `failed` after timeout |
| Disconnect after cancellation | Persist cancellation intent; the final state MUST remain readable |
| Session lease renew fails or lease is fenced out | Stop writing through the stale holder, write failure only if fencing still allows it, and surface the existing adapter-error path |
| Event log write fails | The run MUST NOT claim success; an accepted task MUST produce terminal failure evidence |
| Delegated container was destroyed by idle TTL | Restore runtime resources from the delegated-session contract before starting the next turn |
| Delegated restore fails | Return `haas_delegation_restore_failed` or a more specific delegation error; do not run locally |
| Approval bridge is waiting | Persist `ApprovalRecord`; resume the adapter only after manager resolves it |

## 11. Test Plan and Acceptance Criteria

- Unit: ID generation, tuple parsing, scope, state transitions, terminal immutability, request hashes, and idempotency replay.
- Integration: parity between non-streaming `/run` and streaming `/run_sse`, session read-back, PATCH stateDelta, cancellation, and DELETE.
- Concurrency: concurrent requests in the same session return `session_busy`; different sessions MAY run concurrently; a long turn exceeding one lease TTL remains protected by renewal.
- Recovery: simulate sidecar restart, adapter reconnect, missing native reference, expired session, stale holder fencing rejection, and adapter timeout terminalization.
- Integration: `/run_sse` emits events progressively before invocation terminal state, and native event replay can reconnect while the turn is still running.
- Integration: delegated session continuation after container TTL restore reuses the HaaS session and native Codex reference when recoverable.
- Approval: pending approval survives SSE disconnect and is resolved through the HaaS native approval API.
- Compatibility: align ADK client behavior for session GET/PATCH/DELETE.
- Security: all cross-principal access returns 404; secret-shaped input does not enter default logs.
