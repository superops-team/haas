# HaaS End-to-End Sequence

**English** | [简体中文](WALKTHROUGH.zh-CN.md)

Status: Draft
Last reviewed: 2026-09-10

This document connects the complete request flow for a `/run_sse` call and a session read, identifying the owner and transferred objects at each step. It eliminates gaps between component specs that would otherwise require AI inference. The numbering matches the request flow in `specs/architecture`.

## 1. `POST /run_sse` (Streaming Run)

```text
Client
  1. POST /run_sse  {appName, userId, sessionId?, newMessage, streaming, haas?}
     Idempotency-Key?  Last-Event-ID?
  2. FastAPI route (haas.api)
       -> Protocol Mapper: schema validation, camelCase parsing
  3. Identity.authenticate(authorization) -> Principal
     Identity.owns(principal, tenant/workspace/userId)  -> 404 if out of scope
  4. HarnessRegistry.resolve_app(principal, appName) -> HarnessConfig
  5. AdmissionControl.admit_run(ctx) -> AdmissionDecision (429/503 if rejected)
  6. SessionRuntime.run(...)
       a. IdempotencyStore.reserve(key_hash, request_hash)  // if a key is supplied
       b. SessionStore.get_session((appName,userId,sessionId))
          -> create, or default sessionId=hsess_<rand>
       c. SessionStore.acquire_lease(sessionKey, holder)  // session_busy if held
       d. HarnessRegistry.snapshot_for_session -> resolve active profile, freeze EffectiveHarnessProfile
       e. PolicyController.compile_policy -> EffectivePolicy
       f. Run side-effect-free adapter/runtime/provider/MCP preflight
          -> validate readiness, compatibility, references, routes, and sandbox projection
       g. Persist InvocationRecord(status=accepted, acceptedAtMs, inv_...)
          // durable acceptance boundary; no native turn/provider/tool/workspace side effect before this write
       h. SandboxRuntime.create_sandbox(sessionId, SandboxSpec)
       i. HarnessAdapter.prepare_session -> native session ref
       j. Create TurnRecord (1:1 with invocation)
       k. HarnessAdapter.start_turn -> TurnHandle
  7. for event in adapter.stream_events(turn):
       -> normalize + redact
       -> map normalized harness type -> stable haas.* type + typed safe metadata
       -> EventLogStore.append(CanonicalEventRecord)
       -> project_adk -> ADK Event for /run_sse
       -> project_haas -> CanonicalHaasEvent for native replay/live subscribers
       -> write SSE frame(s) progressively; correlate projections by eventId
  8. adapter finalize -> SessionRuntime.mark_turn_terminal
       -> invocation=completed|failed|incomplete|cancelled
       -> merge terminal session state and persist Session/Invocation/Turn under the lease/fencing guard
       -> append and publish the terminal event only after state commit, or commit both atomically
       -> complete the idempotency result and release the active-turn lease/admission slot
       -> close SSE stream (closure is the completion signal)
```

Key object transfers:

| Step | Input object | Output object |
|------|----------|----------|
| 6b | `(appName,userId,sessionId)` | `SessionRecord` |
| 6d | `HarnessConfig` + active `HarnessProfile` | `EffectiveHarnessProfile` |
| 6e | `EffectiveHarnessProfile` + request overrides | `EffectivePolicy` |
| 6f | `EffectiveHarnessProfile` + adapter/runtime declarations | validated side-effect-free preflight |
| 6h | `EffectivePolicy` + `HarnessSandboxDecl` | `SandboxSpec`/`SandboxHandle` |
| 6k | `StartTurnRequest` | `TurnHandle` |
| 7 | `HarnessEvent` | stable `CanonicalEventRecord` -> ADK `Event` + native `CanonicalHaasEvent` |

## 2. `POST /run` (Non-Streaming)

The only difference from `/run_sse` is that step 7 does not flush SSE frames; all events are accumulated. After step 8 terminates, the JSON array is returned in one response. **The array MUST equal the aggregated streaming output of `/run_sse` (parity)**—both use the same event accumulation path and differ only in output timing.

## 3. `GET /apps/{app}/users/{user}/sessions/{sid}` (Readback)

```text
Client
  1. GET /apps/{appName}/users/{userId}/sessions/{sessionId}
  2. Identity.authenticate + owns(principal, userId) -> 404 if out of scope
  3. SessionRuntime.get_session(key) -> SessionRecord
  4. EventLogStore.read_session(sessionId) -> events[]
  5. Project to ADK Session {id, appName, userId, state, events[], lastUpdateTime}
```

## 4. `PATCH /apps/.../sessions/{sid}` (`stateDelta`)

```text
  1-2. Authenticate as above
  3. SessionStore.get_session
  4. deep-merge(state, stateDelta) (scalars overwrite; objects merge recursively; no delete operation)
  5. SessionStore.put_session; update lastUpdateTime
```

The two state write sources (PATCH `stateDelta` and event `actions.stateDelta`) are serialized through the write queue for the same sessionKey. Both use the same merge rule (deep merge, with the later scalar overwriting the earlier one).

## 5. Cancellation (HaaS Native)

```text
  POST /v1/haas/sessions/{sid}/invocations/{invId}/cancel
  -> SessionRuntime.cancel_invocation
     -> HarnessAdapter.cancel_turn -> turn/interrupt
     -> mark invocation=cancelled -> persist terminal record to EventLog
  cancel is idempotent: repeated calls return the current status
```

## 6. `/run_sse` Resumption (`Last-Event-ID`) and HaaS Native Replay

```text
Client disconnects mid-stream
  -> server does not cancel the invocation; events continue to be appended to EventLogStore

ADK-side resumption (/run_sse + Last-Event-ID):
  -> client POSTs /run_sse again with the original appName/userId/sessionId + header Last-Event-ID: evt_...
  -> server locates the original invocation by event id (validating app/user/session scope)
       replays retained events after Last-Event-ID, then resumes live delivery; does not create a turn or restart the harness
  -> if the event id has expired -> 410 haas_offset_expired

HaaS native reconnection (after_event_id):
  -> GET /v1/haas/sessions/{sid}/invocations/{invId}/events?after_event_id=evt_...
  -> EventLogStore.read_invocation(invId, after) replays events after the cursor
  -> if there is no gap -> resume live stream; if cursor has expired -> 410 haas_offset_expired; implicit reconcile is prohibited
  -> if invocation is not terminal, continue through finalization; if terminal, replay terminal and close
```

## 7. Failure Paths (Non-Streaming `/run` and Adapter Failure)

```text
adapter.start_turn or stream_events raises an error
  -> SessionRuntime converges on a terminal state
       -> failed (readable error) / incomplete (budget/timeout truncation)
  -> persist InvocationRecord/TurnRecord/SessionRecord failure state first
  -> EventLogStore appends terminal failure evidence after state commit, or both commit atomically
  -> if Event Log persistence itself failed, retain failed state + safe idempotency envelope and emit fallback diagnostics without claiming a terminal event was stored

POST /run non-streaming: produces no SSE; returns the ADK event array once terminal
  -> accepted failure/incomplete/cancel: return HTTP 200 with partial events plus terminal ADK event
  -> pre-acceptance failure (auth/schema/app/admission/policy/preflight): return structured haasError (4xx/5xx), with no invocation or terminal event
  -> terminal-event store integrity failure: before headers return 503 with accepted=true/invocationId; never convert a normal accepted adapter failure to 502
```

## ADK Compatibility Scope (Critical Clarification)

HaaS supports only the **REST API protocol layer** of ADK 2.0: HTTP paths, request/response shapes, `Event` shape, SSE framing, and camelCase fields. It does **not** include the ADK execution engine, graph workflows, `BaseAgent/WorkflowGraph`, ADK Web UI, or the Python SDK's snake_case server implementation. The compatibility target is any HTTP client that conforms to the ADK 2.0 REST protocol.
l.
