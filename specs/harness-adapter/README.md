# Harness Adapter Component Specification

**English** | [简体中文](README.zh-CN.md)

Status: Draft
Last reviewed: 2026-08-26
Related specs: [Harness Registry](../harness-registry/README.md), [Session Runtime](../session-runtime/README.md), [Event Log & SSE](../event-log-sse/README.md), [Sandbox Runtime](../sandbox-runtime/README.md)

## 1. Component Role

The Harness Adapter is HaaS's unified internal execution interface (Python async). It converts the native protocols of different agent harnesses into canonical HaaS sessions, invocations, events, artifacts, and errors, which are ultimately projected as ADK `Event` objects.

The initial release MUST implement the `codex-app-server` adapter. Future adapters such as Pi, OpenCode, and AMP MUST integrate through this interface and MUST NOT add parallel public APIs.

## 2. Sources and Rationale

| Source | Adopted elements |
|------|----------|
| ADK 2.0 | Event schema (`content.role/parts`, `actions`, `invocationId`) and author semantics |
| `mpa-codex-worker` adapter specs | Internal isolation of Codex app-server, secretless operation, and the terminal-event contract |
| Sandbox Runtime | Adapters declare sandbox requirements, which Sandbox Runtime projects uniformly |
| Component overview | Unified adapter contract |

## 3. Upstream and Downstream Relationships

| Direction | Component | Relationship |
|------|------|------|
| Upstream | Session Runtime | Calls the adapter to prepare sessions, execute turns, cancel, and resume |
| Upstream | Harness Registry | Reads adapter capabilities |
| Upstream | Sandbox Runtime | Provides the harness sandbox declaration and receives a sandbox instance |
| Downstream | Concrete harness runtime | Codex app-server, Pi CLI, OpenCode CLI, AMP runtime, and others |
| Downstream | Model Proxy | Obtains model endpoints and short-lived tokens |
| Downstream | MCP / Tool / Skill Runtime | Materializes MCP, tools, and skills |
| Downstream | Event Log & SSE | Produces canonical events (projected as ADK Events) |

## 4. Responsibility Boundaries

Responsibilities:

- Provide the same typed async interface for every harness base.
- Convert HaaS `EffectiveHarnessConfig` into native harness configuration.
- Convert input items, files, instructions, model, budget, and policy into a format understood by the harness.
- Normalize native progress, text, reasoning, tool, usage, and terminal data into canonical events.
- Map native errors to HaaS error codes and safe reasons.
- Implement or declare the level of support for cancellation, recovery, MCP, skills, artifacts, and tool restrictions.
- Declare harness sandbox requirements (cwd, writableRoots, approvalMode) for projection by Sandbox Runtime.

Non-responsibilities:

- Does not own public HTTP paths.
- Does not own configuration storage or persistence of session facts.
- Does not store long-lived provider credentials.
- Does not bypass the policy controller to broaden permissions.
- Does not expose native events directly as public events.

## 5. Core Interfaces

```python
class HarnessAdapter:
    base: str
    adapter_id: str
    version: str

    async def probe(self) -> AdapterProbe: ...
    async def prepare_session(self, request: PrepareSessionRequest) -> PreparedSession: ...
    async def start_turn(self, request: StartTurnRequest) -> TurnHandle: ...
    async def stream_events(self, handle: TurnHandle) -> AsyncIterator[HarnessEvent]: ...
    async def finalize_turn(self, handle: TurnHandle) -> AdapterTurnResult: ...
    async def cancel_turn(self, request: CancelTurnRequest) -> CancelResult: ...
    async def resume_session(self, request: ResumeSessionRequest) -> PreparedSession: ...
    async def inspect_session(self, request: InspectSessionRequest) -> SessionInspection: ...
    async def list_artifacts(self, request: ListArtifactsRequest) -> list[ArtifactRef]: ...
    async def cleanup_session(self, request: CleanupSessionRequest) -> CleanupResult: ...
    def sandbox_declaration(self) -> HarnessSandboxDecl: ...
```

### 5.1 Capability Matrix

| Capability | Type | Description |
|------------|------|------|
| `streaming` | bool | Whether incremental events can be produced (corresponding to ADK `streaming:true`) |
| `sessionContinuation` | `native` / `emulated` / `unsupported` | Session continuation mechanism |
| `cancellation` | `hard` / `best_effort` / `unsupported` | Cancellation semantics |
| `toolRestriction` | `hard` / `advisory` / `unsupported` | Enforcement strength for disabled tools |
| `mcp` | `native` / `proxy` / `advisory` / `unsupported` | MCP integration mechanism |
| `skills` | `native` / `instructions` / `unsupported` | Skill materialization mechanism |
| `files` | `native` / `workspace_scan` / `unsupported` | Artifact collection mechanism |
| `usage` | `native` / `estimated` / `unavailable` | Source of token usage |

### 5.2 Adapter Phases

| Phase | Input | Output |
|-------|------|------|
| `probe` | Runtime binary/config | `AdapterProbe` |
| `prepare_session` | Frozen harness config, workspace, policy, sandbox | Native session ref |
| `start_turn` | Invocation id, session id, input, model, budget | `TurnHandle` |
| `stream_events` | `TurnHandle` | `HarnessEvent` stream |
| `finalize_turn` | Event accumulator, native terminal | `AdapterTurnResult` |
| `cancel_turn` | Invocation/turn/session id | `CancelResult` |
| `cleanup_session` | Retention/delete request | Cleanup report |

## 6. Data Model

### 6.1 AdapterProbe

```json
{
  "adapterId": "codex-app-server",
  "base": "codex",
  "status": "ready",
  "runtimeVersion": "codex-cli 0.149.1",
  "transport": "unix_websocket",
  "capabilities": {
    "streaming": true,
    "sessionContinuation": "native",
    "cancellation": "best_effort",
    "toolRestriction": "advisory",
    "mcp": "native",
    "skills": "native",
    "files": "workspace_scan",
    "usage": "native"
  },
  "safeDetails": {}
}
```

### 6.2 StartTurnRequest

```json
{
  "invocationId": "inv_abc",
  "sessionId": "hsess_abc",
  "turnId": "turn_abc",
  "appName": "chrn_codex_default",
  "harness": {},
  "input": [],
  "instructions": null,
  "model": "gpt-5.6-terra",
  "maxStep": 40,
  "timeoutSeconds": 900,
  "sandbox": {
    "sandboxId": "sbx_abc",
    "workspaceRoot": "/workspace",
    "writableRoots": ["/workspace"]
  },
  "policy": {},
  "credentials": {
    "modelProxyTokenRef": "secret://runtime/session/abc"
  }
}
```

### 6.3 HarnessEvent (Internal Canonical Form, Projected as an ADK Event)

```json
{
  "type": "harness.text.delta",
  "nativeType": "item/agentMessage/delta",
  "invocationId": "inv_abc",
  "sessionId": "hsess_abc",
  "turnId": "turn_abc",
  "author": "codex",
  "content": {
    "role": "model",
    "parts": [{ "text": "hello" }]
  },
  "actions": {
    "stateDelta": {},
    "artifactDelta": {}
  },
  "usage": null,
  "safe": true
}
```

Event Log projects `HarnessEvent` as an ADK `Event`, removing internal fields while retaining `id`/`invocationId`/`author`/`timestamp`/`content`/`actions`/`longRunningToolIds`/`nodeInfo`/`output`.

## 7. Runtime Model and State Machine

```text
adapter unavailable
  -> probing
  -> ready
  -> preparing_session
  -> turn_running
  -> idle
  -> degraded
  -> unavailable
```

Turn states:

```text
queued -> starting -> running -> completing -> completed
queued -> starting -> running -> cancelling -> cancelled
queued -> starting -> running -> incomplete
queued -> starting -> running -> failed
```

State-machine rules:

- If the adapter `probe` does not pass, the registry MUST NOT mark that base as `ready`.
- If `stream_events` raises an exception, Session Runtime MUST converge on a terminal state.
- After a terminal event, the adapter MUST NOT emit further events that change invocation state.
- A session MUST have no more than one active turn at any time.

## 8. Security and Authorization

- Adapter input MUST already have passed protocol schema validation, admission, and policy validation.
- An adapter may receive only a secret reference or short-TTL token; it MUST NOT receive a long-lived raw provider key.
- Adapter event payloads MUST be redacted before being passed to Event Log.
- If a native harness configuration file MUST contain a token, it may use only an ephemeral session directory, and that path MUST NOT be included in an artifact/archive.
- Tool-restriction enforcement MUST be labeled accurately; prompt-only constraints MUST NOT be represented as hard blocks.
- Sandbox Runtime creates sandboxes uniformly. An adapter may only declare requirements and MUST NOT broaden them itself.

## 9. Observability

Each adapter reports at least:

- `haas.adapter.probe`
- `haas.adapter.session.prepare`
- `haas.adapter.turn.start`
- `haas.adapter.turn.event`
- `haas.adapter.turn.terminal`
- `haas.adapter.turn.cancel`
- `haas.adapter.error`

Metric dimensions MUST have low cardinality: `adapterId`, `base`, `status`, `errorCode`, and `capability`. Raw model prompts, file paths, and tokens MUST NOT be used as labels.

## 10. Failure and Recovery

| Scenario | Behavior |
|------|------|
| Runtime binary missing | Adapter sets `probe.status=unavailable`; harness is not marked ready |
| Native schema incompatible | Fail closed and return `haas_adapter_incompatible` |
| Native event cannot be parsed | Write a safe `haas.adapter.event_unparsed` event; if the terminal state cannot be determined, the turn fails |
| Adapter process/connection lost | Attempt reconnection; if recovery is impossible, the invocation is failed or incomplete |
| Cancellation unsupported | Mark the capability `unsupported`; the API returns `haas_cancel_unsupported` |
| Session cannot be resumed | `non_resumable` or `session_expired` |

## 11. Test Plan and Acceptance Criteria

- Contract tests: every adapter MUST pass the same fake-harness test suite.
- Golden events: every adapter maintains mapping tests from native event fixtures to ADK Event projections.
- Cancellation: an adapter that supports cancellation MUST prove that the final state is `cancelled`; returning only 200 is insufficient.
- Recovery: session inspection and stored-state semantics are explicit after an adapter crash/restart.
- Sandbox: Sandbox Runtime correctly projects adapter declarations, and widening is rejected.
- Security: negative assertions verify that adapter env/config/output contains no secret patterns.
- Initial Codex release: a real app-server handshake and turn-streaming E2E are P0 release gates.
