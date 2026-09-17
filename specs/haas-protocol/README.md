# HaaS Protocol Component Specification

**English** | [简体中文](README.zh-CN.md)

Status: Draft
Last reviewed: 2026-09-15
Change ID: unified-runtime-approval-policy, long-task-model-proxy-stability

## 1. Component Role

HaaS Protocol is the HTTP/JSON + SSE contract exposed by the system to upstream clients. The primary northbound protocol follows the REST API protocol layer of Google [Agent Development Kit (ADK) 2.0](https://adk.dev/2.0/), with HaaS providing `/v1/haas/*` control-plane extensions on top.

The protocol enables upstream systems to run any harness using a standard ADK 2.0 client. Concrete runtimes such as Codex app-server, Pi, OpenCode, and AMP exist only inside their adapters and are not required knowledge for the upstream protocol.

Contract mapping:

| Concept | ADK 2.0 term | HaaS internal term |
|------|---------------|---------------|
| Executable agent app | `appName` | configured harness `id` (`chrn_...`; `name` MAY be used as an alias) |
| Caller identity | `userId` | Sub-user scope under the caller principal |
| Session | `sessionId` | HaaS Session, uniquely identified by `(appName, userId, sessionId)` |
| One execution | `/run`, `/run_sse` | HaaS Run/Invocation (internal `inv_...`) + internal Turn |
| Event | ADK `Event` | ADK projection of a canonical event |

## 2. Sources and Rationale

| Source | Adopted content |
|------|----------|
| ADK 2.0 docs (`/runtime/api-server/`, retrieved 2026-08-26) | `/list-apps`, `/run`, `/run_sse`, `/apps/{app}/users/{user}/sessions/{sid}`, camelCase, `newMessage{role,parts}`, token-level deltas with `streaming:true`, and SSE `data:` frames |
| ADK 2.0 release notes | New Event fields: `nodeInfo`, `output`, `routes`, `requestedInput`, and `isolationScope` |
| `mpa-codex-worker` Sidecar API | **Design reference** for health/ready/status behavior only |
| Codex app-server manual | Internal JSON-RPC lifecycle for the Codex adapter, not exposed upstream |
| OpenSandbox AIO | Constraints for base services and endpoints inside the container |

HaaS follows only the ADK **protocol layer**: HTTP paths, request/response shapes, event shapes, and SSE framing. It does not incorporate the ADK agent execution engine (`BaseAgent`/WorkflowGraph), graph workflows, or ADK Web UI. The compatibility target is any HTTP client that conforms to the ADK 2.0 REST protocol. Field naming follows the camelCase REST contract; compatibility with the Python SDK's snake_case server implementation is not guaranteed. See [WALKTHROUGH](../architecture/WALKTHROUGH.md) for the adaptation scope.

## 3. Upstream and Downstream Relationships

| Direction | Component | Relationship |
|------|------|------|
| Upstream | Manager / SDK / CLI / product backend / ADK web UI | Calls HaaS through ADK HTTP/SSE |
| Upstream | Harness Registry / Harness Profile | Resolves appName -> configured harness, active profile, model availability, and capabilities |
| Upstream | Session Runtime | Creates run/invocation and session/turn objects; manages idempotency and concurrency |
| Upstream | Event Log & SSE | Reads and publishes events |
| Upstream | Harness Adapter | Executes the concrete harness |
| Upstream | Observability | Provides health/ready/status, traces, and metrics |

This component owns the Protocol Mapper: it maps ADK requests to internal objects and projects internal canonical events as ADK `Event` objects. Protocol Mapper is not a separate component; it is a responsibility of this specification.

## 4. Responsibility Boundaries

Responsibilities:

- Define paths, methods, headers, and request/response schemas for the public ADK API.
- Maintain the layering between the primary ADK-compatible protocol and HaaS native extensions.
- Standardize error detail, `Idempotency-Key` rules, SSE framing, and event projection.
- Identify which fields are public compatibility commitments and which are HaaS extensions.

Non-responsibilities:

- Does not directly start or manage harness processes.
- Does not store sessions, events, artifacts, or credentials.
- Does not execute model requests, MCP requests, or tool calls.
- MUST NOT expose native runtime details such as Codex thread/turn IDs, Pi session files, or OpenCode configuration paths.

## 5. Core Interfaces

### 5.1 ADK-Compatible Public API (Primary Protocol)

ADK 2.0 root paths, with drop-in compatibility:

| Method | Path | Description |
|--------|------|------|
| GET | `/list-apps` | Lists configured harness app names within the caller scope (returns an array of ID strings) |
| POST | `/run` | Runs a harness, collects all events, and returns them in a single JSON array |
| POST | `/run_sse` | Runs a harness and streams events over SSE; `streaming:true` enables token-level deltas |
| GET | `/apps/{app_name}/users/{user_id}/sessions/{session_id}` | Reads a session (`state` + `events[]`; the native ADK replay channel) |
| PATCH | `/apps/{app_name}/users/{user_id}/sessions/{session_id}` | Updates session state using `stateDelta` (supports `Idempotency-Key`) |
| DELETE | `/apps/{app_name}/users/{user_id}/sessions/{session_id}` | Deletes a session and its data |

`appName` resolution order is an exact match on harness `id` (`chrn_...`), followed by a match on `name`. Multiple matches or no match return `404 app_not_found`. `sessionId` is a caller-supplied opaque string. `userId` is a controlled business sub-identity: omitted values use the authenticated principal's `defaultUserId`; explicit values MUST pass the principal's exact `allowedUserIds` or independent delegated-user authorization. It never overrides bearer identity. Cross-scope values return 404.

### 5.2 HaaS Native Extension API

The HaaS native control plane provides only capabilities not covered by U and MUST NOT redefine ADK field semantics:

| Method | Path | Description |
|--------|------|------|
| GET | `/health` / `/ready` | Liveness/readiness aliases equivalent to `/v1/haas/health` and `/v1/haas/ready` |
| GET | `/v1/haas/health` | sidecar liveness |
| GET | `/v1/haas/ready?scope=control\|execution\|capability` | Readiness |
| GET | `/v1/haas/status` | Status summary for runtime, adapter, queue, proxy, AIO, and store |
| GET | `/v1/haas/capabilities` | Stable caller-scoped capability discovery for HaaS features, workspace modes, and configured harnesses |
| GET | `/v1/haas/diagnostics` | Redacted diagnostic summary |
| GET | `/v1/haas/harnesses` | Complete configured harness list (equivalent to the ID array from `/list-apps`, but with details) |
| POST | `/v1/haas/harnesses` | Creates a configured harness (supports `Idempotency-Key`) |
| PUT | `/v1/haas/harnesses/{harness_id}` | Updates a harness; `id`, `base`, and `createdAtMs` remain unchanged |
| DELETE | `/v1/haas/harnesses/{harness_id}` | Deletes a harness without deleting historical sessions |
| GET | `/v1/haas/profiles?harnessId=&status=&cursor=&limit=` | Lists profile revisions with pagination |
| POST | `/v1/haas/profiles` | Creates a draft profile revision (supports `Idempotency-Key`) |
| GET | `/v1/haas/profiles/{profile_id}` | Reads one profile revision |
| PUT | `/v1/haas/profiles/{profile_id}` | Updates a draft profile revision (supports `Idempotency-Key`) |
| POST | `/v1/haas/profiles/{profile_id}/validate` | Validates a profile revision without side effects |
| POST | `/v1/haas/profiles/{profile_id}/activate` | Activates a validated profile revision |
| GET | `/v1/haas/models` | Global model catalog, grouped by base |
| POST | `/v1/haas/delegated-sessions` | Creates/reuses a manager-owned `prepared` delegated-session candidate; binding commits only at first accepted invocation (supports `Idempotency-Key`) |
| GET | `/v1/haas/delegated-sessions/{delegated_session_id}` | Reads delegated-session contract, policy snapshot, mount manifest, and runtime status |
| POST | `/v1/haas/delegated-sessions/{delegated_session_id}/restore` | Recreates runtime resources from the persisted delegated-session contract |
| POST | `/v1/haas/delegated-sessions/{delegated_session_id}/policy` | Explicitly updates/rebinds the policy snapshot for future delegated turns |
| GET | `/v1/haas/sessions` | Lists sessions across users with pagination (administrative view) |
| GET | `/v1/haas/sessions/{session_id}/invocations/{invocation_id}` | Reads authoritative invocation state for disconnect/integrity recovery |
| GET | `/v1/haas/sessions/{session_id}/approvals?status=waiting` | Lists caller-visible approval records with pagination; P0 Codex `unattended_only` normally returns an empty page |
| GET | `/v1/haas/sessions/{session_id}/input-requests?status=waiting` | Lists caller-visible structured questions so a disconnected UI can reconstruct pending input |
| GET | `/v1/haas/sessions/{session_id}/events-page` | Reads bounded JSON pages of `CanonicalHaasEvent` using `after_event_id` + `limit` |
| GET | `/v1/haas/sessions/{session_id}/events` | HaaS `CanonicalHaasEvent` replay/live SSE stream with cursor |
| GET | `/v1/haas/sessions/{session_id}/invocations/{invocation_id}/events` | Invocation-level `CanonicalHaasEvent` replay/live stream |
| GET | `/v1/haas/sessions/{session_id}/invocations/{invocation_id}/tools/{tool_call_id}/evidence?evidence_ref=` | Reads one scoped, short-lived command-evidence record without persistence or caching |
| POST | `/v1/haas/sessions/{session_id}/invocations/{invocation_id}/cancel` | Idempotently cancels a running invocation (supports `Idempotency-Key`) |
| POST | `/v1/haas/sessions/{session_id}/invocations/{invocation_id}/pause` | Requests resumable interruption and returns only after authoritative `interrupted` readback |
| POST | `/v1/haas/sessions/{session_id}/invocations/{invocation_id}/continue` | Creates a new invocation/turn linked to an interrupted source and streams its ADK projection |
| POST | `/v1/haas/sessions/{session_id}/profile-rebind` | Explicitly rebinds future turns in an existing session to a profile snapshot |
| POST | `/v1/haas/sessions/{session_id}/policy` | Stages a revisioned approval/network/workspace policy update for future invocations |
| POST | `/v1/haas/sessions/{session_id}/approvals/{approval_id}` | Returns a manager approval decision to a waiting harness action |
| POST | `/v1/haas/sessions/{session_id}/input-requests/{input_request_id}` | Returns structured answers to the original blocking harness request |
| GET | `/v1/haas/sessions/{session_id}/artifacts` | Lists HaaS artifacts |
| GET | `/v1/haas/sessions/{session_id}/artifacts/archive` | Downloads a session artifact archive (zip) |
| POST | `/v1/haas/files` | Uploads an input file (multipart), supports `Idempotency-Key`, and returns a `File` object |
| GET | `/v1/haas/files/{file_id}/content` | Downloads raw artifact bytes (`nosniff`) |
| GET | `/v1/haas/files/{file_id}/pdf` | Optional PDF preview; returns `501 haas_preview_unavailable` if not implemented |

See §5 of [Artifact Store](../artifact-store/README.md) for artifact endpoint details. The file model uses a single `File` schema.

### 5.3 Capability Discovery Contract

`GET /v1/haas/capabilities` is the single stable discovery API used before a
session is created. It is distinct from readiness and diagnostics:

- `/ready` answers whether a scope can accept work now.
- `/status` reports operational state for humans and diagnostics.
- `/capabilities` reports caller-visible support, current availability,
  implementation mechanism, and enforcement strength in a versioned schema.

Response:

```json
{
  "data": {
    "object": "haas_capabilities",
    "protocolVersion": "2026-09-10",
    "adkProtocolVersion": "2.0",
    "features": {
      "runSse": {"status": "available", "mode": "native", "enforcement": "hard"},
      "delegatedSessions": {"status": "available", "mode": "native", "enforcement": "hard"},
      "modelProxy": {"status": "available", "mode": "proxy", "enforcement": "hard"},
      "mcpProxy": {"status": "available", "mode": "proxy", "enforcement": "hard"},
      "skillMaterialization": {"status": "available", "mode": "native", "enforcement": "hard"},
      "approvalHandling": {"status": "available", "mode": "unattended_only", "enforcement": "hard"},
      "inputHandling": {"status": "unsupported", "mode": "none", "enforcement": "none"},
      "artifacts": {"status": "available", "mode": "native", "enforcement": "hard"}
    },
    "workspaceModes": [
      {"id": "bind_mount", "status": "available", "mode": "native", "enforcement": "hard"},
      {"id": "snapshot_upload", "status": "unsupported", "mode": "none", "enforcement": "none"},
      {"id": "remote_workspace", "status": "unsupported", "mode": "none", "enforcement": "none"}
    ],
    "harnesses": [
      {
        "id": "chrn_codex_default",
        "base": "codex",
        "activeProfileId": "hprof_abc",
        "activeProfileVersion": 12,
        "activeProfileFingerprint": "sha256:profile",
        "status": "available",
        "capabilities": {
          "streaming": {"status": "available", "mode": "native", "enforcement": "hard"},
          "sessionContinuation": {"status": "available", "mode": "native", "enforcement": "hard"},
          "cancellation": {"status": "available", "mode": "best_effort", "enforcement": "hard"},
          "approval": {"status": "available", "mode": "unattended_only", "enforcement": "hard"},
          "input": {"status": "unsupported", "mode": "none", "enforcement": "none"},
          "toolRestriction": {"status": "degraded", "mode": "advisory", "enforcement": "advisory"},
          "mcp": {"status": "available", "mode": "proxy", "enforcement": "hard"},
          "skills": {"status": "available", "mode": "native", "enforcement": "hard"},
          "files": {"status": "available", "mode": "workspace_scan", "enforcement": "hard"},
          "usage": {"status": "available", "mode": "native", "enforcement": "hard"}
        }
      }
    ],
    "generatedAtMs": 1786400000000
  },
  "traceId": "tr_abc"
}
```

`CapabilityState` rules:

- `status` is one of `available`, `degraded`, `unavailable`, or `unsupported`.
  `unavailable` means the mechanism is implemented but cannot be used now;
  `unsupported` means the implementation does not provide it.
- `mode` is one of the stable mechanisms defined by OpenAPI. `unsupported` MUST
  use `mode=none`; `unavailable` retains the implemented mode so clients can
  distinguish transient unavailability from absence.
- `enforcement` is `hard`, `advisory`, or `none`. Prompt/instruction-only
  restrictions MUST be `advisory`, never `hard`.
- `safeReason` and `retryable` are optional safe diagnostics. They MUST NOT
  contain native protocol data, internal addresses, paths, credentials, or
  unredacted exceptions.
- Unknown feature keys, modes, statuses, enforcement values, and workspace-mode
  ids MUST be treated as unsupported by clients. A required capability is usable
  only when its status/mode/enforcement satisfy the caller's declared need;
  clients MUST NOT infer support from field presence alone.
- The response includes only configured harnesses visible to the authenticated
  principal. It exposes harness `id` and `base`, but not adapter transport,
  adapter id/version, native session/thread ids, container ids, internal
  endpoints, provider credentials, MCP headers, or host paths.
- Partial component failures return HTTP 200 with affected capability states set
  to `degraded` or `unavailable`. Return `503 haas_store_unavailable` only when a
  caller-scoped snapshot cannot be constructed safely.

## 6. Data Models

### 6.1 RunRequest (`/run` and `/run_sse` Request Body)

```json
{
  "appName": "chrn_codex_default",
  "userId": "u_123",
  "sessionId": "s_123",
  "newMessage": {
    "role": "user",
    "parts": [
      { "text": "Summarise README.md" },
      { "inlineData": { "displayName": "a.png", "mimeType": "image/png", "data": "base64..." } }
    ]
  },
  "streaming": true,
  "haas": {
    "profileId": "hprof_abc",
    "profileVersion": 12,
    "model": "gpt-5.6-terra",
    "instructions": "Use the repository AGENTS.md.",
    "metadata": { "haasTraceId": "tr_abc" },
    "maxOutputTokens": 4096,
    "maxStep": 40,
    "timeoutSeconds": 86400
  }
}
```

Rules:

- `appName` is required. `userId` is optional only when authentication supplies `defaultUserId`; an explicit value is a requested sub-scope and does not take precedence over token identity. `sessionId` is optional; if omitted, HaaS generates `hsess_<rand>`.
- `newMessage.parts[]` supports `text` and `inlineData`. As an extension, HaaS also accepts `fileId`, which references an uploaded file.
- `streaming` applies only to `/run_sse` and is `false` by default.
- Unknown top-level fields are ignored on ADK-compatible paths. HaaS extensions MUST be placed only in the nested `haas` object and MUST NOT pollute ADK top-level fields.
- `haas.profileId` / `haas.profileVersion` may pin a profile for a **new session**.
  Existing sessions already have a frozen `EffectiveHarnessProfile`; if a request
  asks for a different profile, HaaS returns `409 haas_profile_rebind_required`
  and does not start an invocation. Clients must use native `profile-rebind` or
  create a new session to change future-turn configuration for an existing
  session.
- `haas.timeoutSeconds` is an optional per-invocation deadline override. The
  default and maximum supported value are `86400` seconds. It is a long-task
  safety guard, not an SSE idle timeout or client HTTP timeout. Values less than
  or equal to zero are invalid; values above 24 hours are clamped to `86400`.

### 6.1.1 Profile and Dynamic Configuration Contract

HaaS native profile APIs use `HarnessProfile` and `EffectiveHarnessProfile` as
defined by [Harness Profile](../harness-profile/README.md). A profile is the
cross-harness configuration unit for provider/model, MCP, skills, AGENTS.md,
workspace/policy, and budgets:

- `Harness` is the ADK app identity. The active profile is the default execution
  configuration for new sessions on that app.
- An activated revision is immutable. Draft edits invalidate validation; activation revalidates current content and atomically advances the per-harness version. Old content is reused only by copying to a higher revision. Retaining only the latest profile must preserve complete applied/pending session snapshots and referenced content.
- New sessions freeze the current active profile. Existing sessions do not drift
  when the active profile changes.
- Explicit `profile-rebind` affects only new invocations after the rebind; running
  invocations are not modified. It applies to non-delegated sessions only; a
  delegated session rejects `profile-rebind` with `409 haas_profile_rebind_unsupported`
  and is reconfigured through `POST /v1/haas/delegated-sessions/{id}/policy`.
- Public capability and harness read responses may expose `activeProfileId`,
  `activeProfileVersion`, and `activeProfileFingerprint`. They MUST NOT expose
  provider credentials, MCP headers, AGENTS.md content, host paths, or
  adapter-native configuration.
- Both local and delegated sessions expose the same policy mutation semantics. The local route
  accepts `{expectedRevision, policy:{workspace?,network?,tools?}}` plus `Idempotency-Key`;
  delegated `/policy` carries the same complete policy domain inside its existing update contract.
  Responses expose complete `desiredPolicy`/`appliedPolicy` snapshots, their desired/applied
  revisions, and typed pending/applied/failed state. Accepted
  invocations remain frozen and new work is gated until the desired revision applies.

### 6.2 ADK `Event` (Public Event Projection)

```json
{
  "id": "evt_0000000001042",
  "invocationId": "inv_abc",
  "author": "codex",
  "timestamp": 1743712220.385936,
  "content": {
    "role": "model",
    "parts": [
      { "text": "OK." }
    ]
  },
  "actions": {
    "stateDelta": {},
    "artifactDelta": {},
    "requestedAuthConfigs": {}
  },
  "longRunningToolIds": [],
  "nodeInfo": null,
  "output": null
}
```

`author` contains the harness `id` or base (`codex`). `content.role` is `model` or `user`. Elements of `parts[]` have type `text`, `inlineData`, `functionCall`, or `functionResponse`. `nodeInfo` and `output` appear only when the adapter produces the corresponding information. Session Runtime writes `actions.stateDelta` and `artifactDelta` by aggregating harness state and artifacts.

### 6.3 HaaS Native `CanonicalHaasEvent`

HaaS native event streams return the strongly typed `CanonicalHaasEvent` defined by
OpenAPI and [Event Log & SSE](../event-log-sse/README.md) §6.3–§6.4. Each SSE
`data:` frame contains one object with a stable top-level `type`, a type-specific
safe `haas` payload, scope/order identifiers, ADK-compatible `content`/`actions`,
and `observedAtMs`.

The stable `type` and `haas` payload exist only on HaaS native event streams. They
MUST NOT be added to ADK `/run`, `/run_sse`, or ADK Session `events[]`. Adapter
`nativeType`, adapter id/transport/version, caller `userId`, container ids, internal
addresses, host paths, credentials, raw prompts/reasoning, and full tool arguments or
results MUST NOT enter the public native projection.

Clients consume native events by `type`; they MUST NOT infer queue, approval, tool,
restore, or terminal semantics from human text. Unknown public event types are
incompatible for that behavior and MUST be ignored for rendering unless a newer
client contract explicitly supports them; they MUST never be interpreted as approval
or successful terminal events. Terminal outcome is represented by exactly one of
`haas.turn.completed`, `haas.turn.failed`, `haas.turn.incomplete`,
`haas.turn.interrupted`, or `haas.turn.cancelled` per invocation.

Output, reasoning, tool, and usage facts MAY carry the additive correlation fields
`itemId` and `modelCallId` defined by Event Log & SSE. `modelCallId` identifies one measured
model round trip, not a UI row. A `haas.usage.updated` event with `scope=model_call` binds its
`usage` to that id and may carry a separate `cumulativeUsage` snapshot. `usage` preserves actual
input, output, reasoning-output, and cache counters reported by the harness. Clients MUST NOT
allocate those values among the correlated commentary, reasoning-summary, result, or tool facts.
When correlation or usage is absent, it remains unknown; zero and per-step estimates are forbidden.
These native-only additions do not change the ADK Event schema.
An agent-message delta whose phase is not yet authoritative stays unclassified. A later
`haas.output.item.completed` fact identifies the same `itemId` and optional
`messagePhase=commentary|final_answer`; clients reclassify the already streamed item in place and
do not duplicate its text. Model-call usage meters the call but is not a stage-terminal signal while
correlated tools remain non-terminal. Cache-read tokens are a subset of input tokens and reasoning
tokens are a subset of output tokens, so both are presented as `of which` values and never double-counted.

Structured side-effect authorization and structured user input are separate native facts. `haas.approval.required|resolved` carry approval identifiers and safe action summaries; `haas.input.required|resolved` carry input-request identifiers and renderable question metadata. Neither kind is terminal. A client MUST answer through its matching resource endpoint and MUST NOT translate a question into an approval decision or infer an answer from assistant prose.

### 6.4 ADK `Session`

```json
{
  "id": "s_123",
  "appName": "chrn_codex_default",
  "userId": "u_123",
  "state": { "visitCount": 5 },
  "events": [],
  "lastUpdateTime": 1743711430.022186
}
```

### 6.5 HaaS Error

ADK-compatible paths return errors with `detail`, following the FastAPI convention, plus a structured `haasError` extension:

```json
{
  "detail": "The session is busy.",
  "haasError": {
    "type": "invalid_request_error",
    "code": "session_busy",
    "param": "sessionId",
    "safeReason": "session_busy",
    "retryable": true,
    "traceId": "tr_abc"
  }
}
```

`code` uses a stable HaaS error code. `haasError` is a HaaS extension; ADK clients read only `detail`. See [ERROR-CODES](ERROR-CODES.md) for the sole error-code catalog. OpenAPI `haasError.code` values MUST align with that catalog, and the catalog MUST be updated before adding an error code.

### 6.6 Recovery and Bounded Read Models

`GET /v1/haas/sessions/{session_id}/invocations/{invocation_id}` returns the authoritative `Invocation`, including `accepted|running|pausing|completed|failed|incomplete|interrupted|cancelling|cancelled`, accepted/start/completion timestamps, continuation links, terminal event id, model, safe error summary, and the current `sessionControl` projection (`controlState`, `supportsResume`, `resumableInvocationId`). It is the primary recovery query after transport loss or `haasError.accepted=true`.

`GET /v1/haas/sessions/{session_id}/events-page?after_event_id=&limit=` returns a JSON page ordered by session-scoped `eventId`; `limit` defaults to 100 and is capped at 1000. `nextCursor` is the last returned event id only when another retained page is immediately available, otherwise null. Polling consumers nevertheless retain the last event id of every non-empty `data` page as their next `after_event_id` checkpoint, so a later append is read incrementally rather than restarting from the session beginning. It never switches to live delivery. The existing `/events` routes remain replay-then-live SSE.

`GET /v1/haas/sessions/{session_id}/approvals?status=waiting&limit=&cursor=` returns only caller-visible, redacted approval records. It is used to rebuild approval UI after disconnect. P0 Codex advertises `unattended_only`, so this normally returns an empty page; a client MUST NOT show human approval controls unless capability mode is `human_bridge`.

`GET /v1/haas/sessions/{session_id}/input-requests?status=waiting&limit=&cursor=` returns structured, redacted questions independently of approvals. `POST /v1/haas/sessions/{session_id}/input-requests/{input_request_id}` accepts `{answers:{questionId:{values:[...]}}}` for ordinary questions or a private `secretRef` instead of values for a secret question, and resumes the original waiting invocation. An answer is scoped to one input request and is idempotent by `Idempotency-Key`; an answer for another session, a resolved request, an unknown question id, or a disallowed free-text value fails closed. Secret answers are never placed in events, logs, task state, or transcript.

`GET /v1/haas/sessions/{session_id}/invocations/{invocation_id}/tools/{tool_call_id}/evidence?evidence_ref=` is an authenticated display-only read of one in-memory command-evidence record. The bearer principal, complete session key resolved by `session_id`, invocation, tool call, and opaque `evidence_ref` MUST all match. A missing, unavailable, or out-of-scope record returns the same `404 haas_execution_evidence_not_found`; an expired matching record returns `410 haas_execution_evidence_expired`. The response MUST include `Cache-Control: no-store` and `Referrer-Policy: no-referrer` and MUST NOT be written to access logs, analytics, traces, transcripts, events, artifacts, crash reports, browser storage, or service-worker caches.

The `ExecutionEvidence` body contains the redacted actual command, container working directory, bounded command output and detected links. Output is bounded to 8 MiB (8,388,608 UTF-8 bytes) per record and MUST be complete when it fits that bound; larger output is explicitly truncated and requires Artifact Store for complete retrieval. `outputStream` is `combined` unless the adapter has authoritative stdout/stderr identity. Credential values remain masked. Ordinary HTTP(S) links remain intact. An adapter-classified HTTPS user-authorization link with explicit authorization semantics remains byte-for-byte intact and clickable until the evidence-enforced expiry; it MUST bypass redirect/analytics logging and clients open it with `noopener,noreferrer`. The URL need not expose an expiry parameter; a trustworthy earlier native expiry wins when available. Evidence expires no later than the authorization link or 15 minutes after command termination, whichever is earlier, and is deleted on expiry, session deletion, or runtime shutdown. Canonical/ADK events carry only optional opaque `evidenceRef` and `evidenceExpiresAtMs`; they never carry this body or a signed URL.

ADK Session GET remains compatibility-only and includes `events[]`, but is bounded to 1000 events or 8 MiB serialized response, whichever is reached first. If the full ADK Session exceeds either limit, return `413 haas_session_read_too_large` with a safe instruction to use `events-page`; never silently truncate `events[]`.

### 6.7 Delegated Session

HaaS native delegated-session APIs use the `DelegatedSessionContract` defined by
[Manager Delegation](../manager-delegation/README.md). This contract is public only on
the HaaS native surface; ADK-compatible paths MUST NOT expose manager session ids,
host mount paths, image digests, credential references, runtime container ids, or
approval ids unless they are explicitly represented as redacted/safe HaaS events.

`POST /v1/haas/delegated-sessions` requires a manager-approved P0 `bind_mount` manifest and delegation policy snapshot. Creation returns `binding=prepared`; it does not bind the manager chat. HaaS atomically promotes the contract to `haas_bound`, records `acceptedInvocationId`/`bindingAcceptedAtMs`, and emits `haas.delegation.session_bound` when the first InvocationRecord is durably accepted. A repeated `Idempotency-Key` with the same request hash
returns the first delegated-session result and MUST NOT create another container.

`POST /v1/haas/delegated-sessions/{id}/restore` performs recovery only from the
persisted contract. Restore revalidates the mount manifest and policy before runtime
creation; any drift fails closed with a stable error code.

`POST /v1/haas/sessions/{session_id}/approvals/{approval_id}` accepts only explicit manager approval decisions. HaaS MUST NOT auto-approve if this bridge is unavailable. Human approval and structured input are release-gated capabilities; while Codex capability mode remains `unattended_only` or input handling is unsupported, the corresponding APIs remain dormant and the UI MUST NOT claim they are active.

Fresh interactive sessions default to `workspace-write`, public network allow and
`approvalMode=on-request`. Approval decisions are scoped to the server-advertised current action
by default, resolve the same invocation, and do not mutate session policy. `approvalMode=never` is
an explicit no-prompt mode and never grants access beyond the effective sandbox/policy.

### 6.8 Configuration and Replay Update Contract

Delegated `/policy` uses complete supplied domains (`profileRef`, `delegationPolicySnapshot`, `mountManifest`, `image`), a durable desiredRevision/appliedRevision barrier and typed pending/applied/failed results. Application runs independently of new turns, never modifies accepted work, and gates future acceptance until runtime verification. No delegated profile-rebind is allowed. Manager Delegation §5.1.1 owns merge, timeout and recovery behavior.

Accepted execution with Idempotency-Key returns `Idempotency-Expires-At` (epoch milliseconds, acceptedAtMs + 24h); invocation readback exposes idempotencyExpiresAtMs. Nonterminal reservations never expire. After a terminal result expires, its key tombstone remains until session deletion/retention and reusing it returns `410 haas_idempotency_expired`, never a silent new execution. Manager automatically persists/submits one fresh linked attempt on next use; opening completed history, cursor expiry, 404 or transport failure cannot trigger a new execution. Retain operation-specific control mutation receipts separately.

`X-HaaS-Trace-ID` is an optional opaque correlation header (maximum 128 ASCII letters/digits/underscore/hyphen); invalid input is rejected as invalid_input and it never conveys identity or authority. ADK request/response shapes remain unchanged; native schemas and acceptance headers evolve under the unpublished 2026-09-10 draft. Already persisted records require the Stores forward migration, not silent reuse of missing fields. Runtime must not advertise this version until schema, event, migration, client and real runtime gates pass.

### 6.9 Version Advertisement Gate

The normative schema is `haas-2026-09-10.openapi.yaml`. The earlier 2026-08-26 file
was an unpublished draft baseline and is replaced, not retained as a supported
version. Runtime responses may advertise `HaaS-Version: 2026-09-10` only after route,
schema, event migration, acceptance, pagination, identity, and manager-client
conformance gates in Implementation Roadmap pass. A code constant or OpenAPI file
alone is not release evidence.

## 7. Runtime Model and State Machine

```text
request received
  -> auth and principal scope checked
  -> appName resolved (harness id/name)
  -> schema validated
  -> Idempotency-Key reservation (when present)
  -> Last-Event-ID present? -> resolve invocation by event id + scope -> replay then live (do not create a new turn)
  -> admission decision (quota/rate/queue)
  -> session resolved or created
  -> freeze harness config + compile policy + side-effect-free adapter/runtime preflight
  -> persist invocation status=accepted (durable acceptance boundary)
  -> create sandbox/native session/turn and start adapter execution
  -> canonical events appended
  -> events projected to ADK Event / SSE frames
  -> invocation terminal
  -> session state and artifacts committed
```

Invocation (internal Run) states:

| Status | Terminal | Semantics |
|--------|----------|------|
| `accepted` | no | Invocation record is durable; no native execution side effect has started yet |
| `running` | no | Accepted and executing |
| `completed` | yes | Harness completed normally; stream closes |
| `failed` | yes | Harness or service failed; error is readable |
| `incomplete` | yes | Truncated by budget/deadline or orphaned by restart when recoverable; partial output is retained |
| `interrupted` | yes | Paused by caller; source turn is immutable and may be continued as a new invocation |
| `cancelled` | yes | Cancelled by caller; committed events are retained |

Only one running invocation is allowed per session at a time. A second `/run`
returns `409 session_busy`.

**Execution acceptance boundary:** a request becomes accepted execution when its
`InvocationRecord` is durably persisted. All authentication, scope, schema,
app/model resolution, idempotency reservation, admission, policy compilation, and
adapter/runtime preflight that can run without user-work side effects MUST finish
before this boundary. HaaS MUST NOT start a native turn, provider call, tool call,
or workspace mutation before the invocation record exists.

Failures before acceptance return a structured 4xx/5xx response and create neither
an invocation nor a terminal event. Once accepted, adapter start/stream/finalize,
provider, MCP, sandbox, timeout, budget, cancellation, and runtime failures converge
on exactly one persisted canonical terminal event and its ADK projection:

The default accepted invocation deadline is 24 hours. Reaching it uses stable
`haas_request_timeout` with `safeReason=long_task_deadline_exceeded`; the event
is retryable only when the native session or operation is proven replay-safe.
Transport timeouts and stream disconnects are delivery failures and MUST NOT by
themselves terminate an accepted invocation.

| Surface | Accepted terminal result |
|---------|--------------------------|
| `/run` | HTTP 200 with the complete ADK Event array, including the terminal event |
| `/run_sse` | HTTP 200; emit the terminal ADK Event and close the stream |
| Native event stream | Emit exactly one `haas.turn.completed|failed|incomplete|interrupted|cancelled` |

Successful `/run` and `/run_sse` responses include `X-HaaS-Invocation-ID` and
`X-HaaS-Session-ID`. For `/run_sse`, the 200 response headers are the manager-visible
acceptance acknowledgement and arrive before event data; a client MUST persist them
before processing the stream.

Partial text/events committed before failure remain in order before the terminal
event. ADK terminal projection uses `actions.stateDelta.status` with one of
`completed`, `failed`, `incomplete`, `interrupted`, or `cancelled`; failure/incomplete additionally
uses a stable safe `actions.stateDelta.reason`. Clients that require structured
terminal semantics consume the native canonical type.

For accepted execution, `Idempotency-Key` replay always reproduces HTTP 200 and the
same complete ADK event sequence; it never re-runs the harness and never converts an
accepted terminal failure into HTTP 502. Pre-acceptance failures release the
reservation and have no accepted result to replay.

The only post-acceptance exception is failure to persist the required terminal event.
HaaS MUST NOT fabricate a terminal event or represent this as an unaccepted adapter
502. It persists invocation/turn/session `failed` in the authoritative state store.
Before response headers, return `503 haas_store_unavailable` with
`haasError.accepted=true` and `haasError.invocationId`. After SSE headers, abort the
stream; the client reconciles through session/native readback using the known
invocation id. This is an integrity failure, not a normal execution outcome.

SSE framing (`/run_sse`):

```text
data: {"id":"evt_...","invocationId":"inv_abc",...}

```
Heartbeats use the SSE comment `: keep-alive` and do not produce events. The stream closes when the invocation completes. `/run` instead collects the events and returns them once as a JSON array.

## 8. Security and Authorization

- `GET /list-apps` requires authentication and returns only harnesses within the caller scope.
- `GET /v1/haas/capabilities` requires authentication, applies the same caller scope as `/list-apps`, and returns no native or secret-bearing details.
- `Authorization: Bearer <caller token>` is required for every path except health/ready.
- Principal scope is enforced for both `userId` and `sessionId`. Cross-user/session access returns `404`, not `403`, to avoid revealing existence.
- ADK-compatible paths MUST NOT accept caller-supplied upstream URLs or keys at the top level. HaaS extension fields MUST pass registry allowlist validation.
- Error `detail` and `haasError` MUST NOT contain secrets, internal hosts, absolute paths, or stack traces.

## 9. Observability

Each request is associated with `traceId`, `requestId`, `principalHash`, `appName` (harnessId), `userId` hash, `sessionId`, `invocationId`, `adapterBase`, and `status`.

The following MUST NOT be logged: raw prompts, provider API keys, Authorization/Cookie values, MCP runtime header values, artifact file contents, or unredacted stack traces.

## 10. Failure and Recovery

| Scenario | Behavior |
|------|------|
| appName cannot be resolved | `404 app_not_found` (`haasError.code=app_not_found`) |
| Profile does not exist or is unauthorized | `404 haas_profile_not_found` |
| Profile status conflict or non-draft update | `409 haas_profile_conflict` |
| Existing session requests a different profile | `409 haas_profile_rebind_required`; no invocation starts |
| `profile-rebind` targets a delegated session | `409 haas_profile_rebind_unsupported`; use `/delegated-sessions/{id}/policy` |
| Policy expected revision is stale | `409 haas_policy_revision_conflict`; return safe current revision and apply nothing |
| Idempotency store unavailable | `503 haas_idempotency_store_unavailable`; the task MUST NOT execute |
| Session busy | `409 session_busy`, optionally with `retryAfterMs` |
| `/run_sse` disconnects | The invocation is not cancelled. The client reconnects with `POST /run_sse` + `Last-Event-ID`; the server locates the original invocation by event ID, replays subsequent events, and continues live without creating a new turn. Alternatively, the client reads events through `GET /apps/.../sessions/{sid}`. |
| Adapter fails before invocation persistence | Return a structured pre-acceptance 5xx such as `haas_adapter_unavailable`, `haas_adapter_incompatible`, or `haas_adapter_error`; no invocation or terminal event exists |
| Adapter/provider/runtime fails after acceptance | Persist the matching terminal event; `/run` and `/run_sse` remain HTTP 200 and retain partial output |
| Idempotent retry after accepted failure | Replay HTTP 200 and the identical ADK event sequence, including the terminal event; do not restart execution |
| Terminal event persistence fails after acceptance | Persist authoritative failed state; before headers return `503 haas_store_unavailable` with accepted/invocation metadata, otherwise abort SSE and require readback; never fabricate terminal evidence |
| Cancellation retried | Succeeds idempotently and does not delete the session (see the `POST /v1/haas/.../invocations/{id}/cancel` extension in session-runtime) |

## 11. Test Plan and Acceptance Criteria

Documentation stage:

- `git diff --check`
- All public paths are consistent between this file and component specifications. OpenAPI covers only the ADK and HaaS native surfaces.

Implementation stage:

- ADK API server compatibility: use the official ADK client / curl to compare `list-apps`, `run`, and `run_sse` against actual ADK behavior.
- SSE: progressive flush, stream-close semantics, and heartbeats that do not produce events.
- Non-streaming output from `/run` matches the aggregated streaming output from `/run_sse` for completed, failed, incomplete, interrupted, and cancelled accepted invocations; all return HTTP 200 with identical ordered ADK events.
- Lifecycle control: Pause waits for one authoritative interrupted terminal; Continue returns HTTP 200 streaming ADK projections for exactly one linked invocation with accepted identity headers. Duplicate keys replay/reattach without a second native turn, while stale source, missing native state, ordinary `/run` on paused state, and Pause/Stop/completion races return their catalogued outcomes.
- Event: ADK projections keep `content.role`, `parts`, `actions`, and `invocationId` complete without HaaS-only fields; native projections validate stable `type` plus type-specific `haas` metadata, strip internal/native fields, and persist exactly one terminal type per invocation.
- Auth/scope: cross-access by two principals to each other's sessions/users returns 404 in all cases.
- Idempotency-Key: every mutating API accepts the header; reservations are namespaced by the authenticated principal and operation path, so the same caller key cannot conflict with or replay another principal or mutation. Accepted execution retries replay HTTP 200 plus the identical event sequence, pre-acceptance failures create no retained execution result, conflicting request hashes return `409 haas_idempotency_conflict`, and multipart upload hashing includes normalized metadata plus the uploaded byte digest.
- Versioning: every successful `/v1/haas/*` response carries `HaaS-Version`; OpenAPI declares the header on every corresponding success response.
- Capability discovery: validate all availability/mechanism/enforcement enum combinations, caller-scoped harness filtering, unknown-value fail-closed client behavior, degraded/unsupported distinction, and absence of native/secret-bearing fields.
