# HaaS Protocol Component Specification

**English** | [简体中文](README.zh-CN.md)

Status: Draft
Last reviewed: 2026-08-30

## 1. Component Role

HaaS Protocol is the HTTP/JSON + SSE contract exposed by the system to upstream clients. The primary northbound protocol follows the REST API protocol layer of Google [Agent Development Kit (ADK) 2.0](https://adk.dev/2.0/), with HaaS providing `/v1/haas/*` control-plane extensions on top. (The legacy `mpa-codex-worker` migration shim is out of scope; see §5.3.)

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
| `mpa-codex-worker` Sidecar API | **Design reference** for health/ready/status; its legacy `/v1/codex-worker/*` semantics are outside this project's implementation scope (§5.3) |
| Codex app-server manual | Internal JSON-RPC lifecycle for the Codex adapter, not exposed upstream |
| OpenSandbox AIO | Constraints for base services and endpoints inside the container |

HaaS follows only the ADK **protocol layer**: HTTP paths, request/response shapes, event shapes, and SSE framing. It does not incorporate the ADK agent execution engine (`BaseAgent`/WorkflowGraph), graph workflows, or ADK Web UI. The compatibility target is any HTTP client that conforms to the ADK 2.0 REST protocol. Field naming follows the camelCase REST contract; compatibility with the Python SDK's snake_case server implementation is not guaranteed. See [WALKTHROUGH](../architecture/WALKTHROUGH.md) for the adaptation scope.

## 3. Upstream and Downstream Relationships

| Direction | Component | Relationship |
|------|------|------|
| Upstream | Manager / SDK / CLI / product backend / ADK web UI | Calls HaaS through ADK HTTP/SSE |
| Upstream | Harness Registry | Resolves appName -> configured harness, model availability, and capabilities |
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

`appName` resolution order is an exact match on harness `id` (`chrn_...`), followed by a match on `name`. Multiple matches or no match return `404 app_not_found`. `userId` and `sessionId` are caller-supplied opaque strings constrained by the scope of the `Authorization` principal.

### 5.2 HaaS Native Extension API

The HaaS native control plane provides only capabilities not covered by U and MUST NOT redefine ADK field semantics:

| Method | Path | Description |
|--------|------|------|
| GET | `/health` / `/ready` | Liveness/readiness aliases equivalent to `/v1/haas/health` and `/v1/haas/ready` |
| GET | `/v1/haas/health` | sidecar liveness |
| GET | `/v1/haas/ready?scope=control\|execution\|capability` | Readiness |
| GET | `/v1/haas/status` | Status summary for runtime, adapter, queue, proxy, AIO, and store |
| GET | `/v1/haas/diagnostics` | Redacted diagnostic summary |
| GET | `/v1/haas/harnesses` | Complete configured harness list (equivalent to the ID array from `/list-apps`, but with details) |
| POST | `/v1/haas/harnesses` | Creates a configured harness (supports `Idempotency-Key`) |
| PUT | `/v1/haas/harnesses/{harness_id}` | Updates a harness; `id`, `base`, and `createdAtMs` remain unchanged |
| DELETE | `/v1/haas/harnesses/{harness_id}` | Deletes a harness without deleting historical sessions |
| GET | `/v1/haas/models` | Global model catalog, grouped by base |
| GET | `/v1/haas/sessions` | Lists sessions across users with pagination (administrative view) |
| GET | `/v1/haas/sessions/{session_id}/events` | HaaS canonical SSE replay/live stream with cursor |
| GET | `/v1/haas/sessions/{session_id}/invocations/{invocation_id}/events` | Invocation-level canonical SSE replay/live stream |
| POST | `/v1/haas/sessions/{session_id}/invocations/{invocation_id}/cancel` | Idempotently cancels a running invocation (supports `Idempotency-Key`) |
| GET | `/v1/haas/sessions/{session_id}/artifacts` | Lists HaaS artifacts |
| GET | `/v1/haas/sessions/{session_id}/artifacts/archive` | Downloads a session artifact archive (zip) |
| POST | `/v1/haas/files` | Uploads an input file (multipart) and returns a `File` object |
| GET | `/v1/haas/files/{file_id}/content` | Downloads raw artifact bytes (`nosniff`) |
| GET | `/v1/haas/files/{file_id}/pdf` | Optional PDF preview; returns `501 haas_preview_unavailable` if not implemented |

See §5 of [Artifact Store](../artifact-store/README.md) for artifact endpoint details. The file model uses a single `File` schema.

### 5.3 Legacy Sidecar Shim (Out of Scope)

**Decision (2026-08-30):** The `/v1/codex-worker/*` shim **MUST NOT be implemented**.
HaaS and `mpa-codex-worker` are only architecturally isomorphic; HaaS does not assume
migration responsibility. Migration of legacy upstream systems requires a separate
project. See [specs/README §3.1.1](../README.md#311-scope-decision-do-not-implement-the-mpa-codex-worker-migration-shim).

The following table is retained only as a **historical design record** for reference in
a future project. It is **not a task list**, and implementations MUST NOT add
`/v1/codex-worker/*` routes based on it.

| Legacy path | HaaS target | Rule |
|-------------|-------------|------|
| `/v1/codex-worker/health` | `/v1/haas/health` | Preserve compatibility of response fields |
| `/v1/codex-worker/ready` | `/v1/haas/ready` | Preserve `scope` semantics |
| `/v1/codex-worker/status` | `/v1/haas/status` | Add a deprecation notice |
| `/v1/codex-worker/sessions` | `POST /run` | Map to configured harness base=`codex`; derive `userId` from the legacy actor |
| `/v1/codex-worker/sessions/{id}/turns` | `POST /run` | Set `sessionId=id`; map continuation semantics to ADK session continuation |
| `/v1/codex-worker/sessions/{id}/events` | `/v1/haas/sessions/{id}/events` | Support legacy event-name projection |

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
    "model": "gpt-5.6-terra",
    "instructions": "Use the repository AGENTS.md.",
    "metadata": { "haasTraceId": "tr_abc" },
    "maxOutputTokens": 4096,
    "maxStep": 40,
    "timeoutSeconds": 900
  }
}
```

Rules:

- `appName` is required. `userId` is required; it MAY be derived from `Authorization`, but an explicitly supplied value takes precedence. `sessionId` is optional; if omitted, HaaS generates `hsess_<rand>`.
- `newMessage.parts[]` supports `text` and `inlineData`. As an extension, HaaS also accepts `fileId`, which references an uploaded file.
- `streaming` applies only to `/run_sse` and is `false` by default.
- Unknown top-level fields are ignored on ADK-compatible paths. HaaS extensions MUST be placed only in the nested `haas` object and MUST NOT pollute ADK top-level fields.

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

### 6.3 ADK `Session`

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

### 6.4 HaaS Error

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
  -> invocation created
  -> adapter turn started
  -> canonical events appended
  -> events projected to ADK Event / SSE frames
  -> invocation terminal
  -> session state and artifacts committed
```

Invocation (internal Run) states:

| Status | Terminal | Semantics |
|--------|----------|------|
| `running` | no | Accepted and executing |
| `completed` | yes | Harness completed normally; stream closes |
| `failed` | yes | Harness or service failed; error is readable |
| `incomplete` | yes | Truncated by budget/timeout; partial output is retained |
| `cancelled` | yes | Cancelled by caller; committed events are retained |

Only one running invocation is allowed per session at a time. A second `/run` returns `409 session_busy`.

SSE framing (`/run_sse`):

```text
data: {"id":"evt_...","invocationId":"inv_abc",...}

```
Heartbeats use the SSE comment `: keep-alive` and do not produce events. The stream closes when the invocation completes. `/run` instead collects the events and returns them once as a JSON array.

## 8. Security and Authorization

- `GET /list-apps` requires authentication and returns only harnesses within the caller scope.
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
| Idempotency store unavailable | `503 haas_idempotency_store_unavailable`; the task MUST NOT execute |
| Session busy | `409 session_busy`, optionally with `retryAfterMs` |
| `/run_sse` disconnects | The invocation is not cancelled. The client reconnects with `POST /run_sse` + `Last-Event-ID`; the server locates the original invocation by event ID, replays subsequent events, and continues live without creating a new turn. Alternatively, the client reads events through `GET /apps/.../sessions/{sid}`. |
| Adapter crashes | The invocation enters `failed`/`incomplete` and remains readable after persistence |
| Cancellation retried | Succeeds idempotently and does not delete the session (see the `POST /v1/haas/.../invocations/{id}/cancel` extension in session-runtime) |

## 11. Test Plan and Acceptance Criteria

Documentation stage:

- `git diff --check`
- All public paths are consistent between this file and component specifications. OpenAPI covers both the ADK and HaaS native surfaces; legacy entries are retained but not implemented (see §5.3).

Implementation stage:

- ADK API server compatibility: use the official ADK client / curl to compare `list-apps`, `run`, and `run_sse` against actual ADK behavior.
- SSE: progressive flush, stream-close semantics, and heartbeats that do not produce events.
- Non-streaming output from `/run` matches the aggregated streaming output from `/run_sse` (parity).
- Event: `content.role`, `parts`, `actions`, and `invocationId` fields are complete; the ADK 2.0 field `nodeInfo` appears when applicable.
- Auth/scope: cross-access by two principals to each other's sessions/users returns 404 in all cases.
- Idempotency-Key: repeated `POST /run` requests do not start the harness more than once.
