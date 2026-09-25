# Codex App-Server Adapter Component Specification

**English** | [简体中文](README.zh-CN.md)

Status: Draft
Last reviewed: 2026-09-14
Change ID: unified-runtime-approval-policy
Related specs: [Harness Adapter](../harness-adapter/README.md), [Session Runtime](../session-runtime/README.md), [Event Log & SSE](../event-log-sse/README.md), [Model Proxy](../model-proxy/README.md)
Pinned Codex CLI: `0.152.1`

## 1. Component Role

The Codex App-Server Adapter is the only P0 concrete harness adapter in the initial HaaS release. It connects to, initializes, drives, and recovers Codex app-server, and converts native Codex JSON-RPC notifications into canonical HaaS events that are ultimately projected as ADK `Event` objects.

Codex app-server is an internal implementation detail. Upstream systems MUST NOT connect directly to Codex WebSocket, Unix socket, or stdio transports, and MUST NOT depend on Codex `threadId`, `turnId`, notification methods, or rollout file paths.

Embedded local execution supplies the applied bare model and a session-scoped,
generationed loopback provider capability through `thread/start` or
`thread/resume` configuration overrides, using the pinned 0.152.1 schema.
Resuming or rebinding applies the current proxy route/token without replacing
native thread identity; `turn/start` receives the selected model. Only the
session-scoped proxy capability may reach the harness, never the upstream key or
credential-resolver descriptor. Personal Codex authentication is not used. Proxy
capabilities outlive individual invocation terminals and are revoked on session
delete/revoke or runtime shutdown.

The local proxy integration uses a conservative Responses tool surface: native multi-agent namespaces and provider-hosted web search are disabled, while `model_reasoning_summary=auto` requests only the provider-authored safe reasoning summary needed by the process timeline. Raw reasoning remains private and is never projected. Responses support alone does not imply support for other extensions. Ordinary function tools remain available under the existing sandbox/policy; the proxy must not silently drop tools or rewrite native tool calls. The adapter applies this configuration on both start and resume. Real Codex wire tests reject namespace/web-search declarations, verify the safe-summary request and ordinary function tools, and real-provider smoke must complete through this path.

## 2. Sources and Rationale

| Source | Adopted elements |
|------|----------|
| Codex manual `Codex App Server` | Transport, initialize/initialized handshake, thread/turn lifecycle, and WebSocket authentication |
| Local `codex app-server --help` | `--listen`, `--ws-auth`, schema-generation commands, and the current CLI version |
| `mpa-codex-worker` Codex adapter spec | WebSocket-over-UDS, secretless auth.command, MCP configuration, and terminal-event contract |
| Component overview | Requirements for the initial Codex app-server adapter |

## 3. Upstream and Downstream Relationships

| Direction | Component | Relationship |
|------|------|------|
| Upstream | Harness Adapter interface | The adapter implements the unified interface |
| Upstream | Session Runtime | Triggers session preparation, turn start, cancellation, and resume |
| Downstream | Codex app-server process | JSON-RPC over stdio/WebSocket/Unix socket |
| Downstream | Model Proxy | Points the Codex model provider to the loopback proxy |
| Downstream | MCP / Tool / Skill Runtime | Supplies Codex configuration, skills root, and MCP servers |
| Downstream | Event Log & SSE | Receives canonical events |

## 4. Responsibility Boundaries

Responsibilities:

- Manage the Codex app-server process or connect to an existing listener.
- Complete the `initialize` request and `initialized` notification for every connection.
- Invoke `thread/start`, `thread/resume`, `thread/fork`, `turn/start`, `turn/steer`, and `turn/interrupt`.
- Parse Codex responses, notifications, and server requests.
- Convert Codex `item/*`, `turn/*`, tool, permission, usage, and error events into canonical events.
- Track the app-server generation to determine whether a native session remains recoverable after restart/reconnect.
- Generate or validate the app-server schema against the pinned Codex version.
- Ensure that Codex configuration uses the sidecar model proxy and a secretless authentication path.

Non-responsibilities:

- Does not define public APIs.
- Does not persist HaaS invocation/session/event facts.
- Does not expose raw Codex JSON-RPC messages upstream.
- Does not store real provider keys directly.
- Does not independently determine workspace, network, tool, or approval policy.
- Does not bypass the Codex sandbox. The Codex sandbox is the inner layer; Sandbox Runtime uniformly provides the outer layer.

## 5. Core Interfaces

### 5.1 Transport

Initial support order:

1. `unix://PATH`: production default. Uses standard WebSocket HTTP Upgrade over a Unix socket.
2. `ws://127.0.0.1:PORT` / `ws://[::1]:PORT` / `ws://localhost:PORT`:
   local debugging and in-container loopback. When `transport` is
   `loopback_websocket`, the adapter MUST parse `listenUrl` and reject any host
   other than `127.0.0.1`, `::1`, or `localhost` before opening a connection.
3. `stdio://`: test fallback and minimal local smoke testing. The child process uses stdin/stdout NDJSON, with one JSON-RPC message per line. The transport MUST configure an explicit bounded 64 MiB frame limit, large enough for an 8 MiB decoded evidence payload plus the JSON-RPC envelope and worst-case JSON string escaping; it MUST NOT inherit asyncio's approximately 64 KiB default line limit. A frame beyond the configured limit or malformed NDJSON is an adapter protocol failure with a stable safe reason, not an unexplained clean connection end.

Non-loopback WebSocket transports MUST enable `--ws-auth` and MUST be placed behind TLS or a trusted tunnel. HaaS MUST NOT expose the Codex app-server listener directly to the public network.

### 5.2 JSON-RPC Methods

| Method | Direction | Purpose |
|--------|------|------|
| `initialize` | HaaS -> Codex | Connection-level initialization |
| `initialized` | HaaS -> Codex | Initialization-complete notification |
| `thread/start` | HaaS -> Codex | Creates a new Codex thread |
| `thread/resume` | HaaS -> Codex | Resumes an existing thread |
| `thread/fork` | HaaS -> Codex | Optional; supports branched sessions in the future |
| `turn/start` | HaaS -> Codex | Starts a turn |
| `turn/steer` | HaaS -> Codex | Optional; appends input to a running turn |
| `turn/interrupt` | HaaS -> Codex | Cancels a running turn |
| `thread/read` | HaaS -> Codex | Diagnostics or recovery validation |
| `model/list` | HaaS -> Codex | Capability probing |
| `mcpServerStatus/list` | HaaS -> Codex | MCP-awareness validation |
| `skills/list` | HaaS -> Codex | Skill-awareness validation |

### 5.3 Internal Adapter Methods

```python
async def connect(endpoint: CodexEndpoint) -> CodexConnection: ...
async def initialize(conn: CodexConnection, capabilities: dict) -> CodexCapabilities: ...
async def start_thread(conn: CodexConnection, request: CodexThreadStart) -> CodexThreadRef: ...
async def resume_thread(conn: CodexConnection, ref: CodexThreadRef) -> CodexThreadRef: ...
async def start_turn(conn: CodexConnection, request: CodexTurnStart) -> CodexTurnRef: ...
async def interrupt_turn(conn: CodexConnection, turn_id: str, reason: str) -> None: ...
async def notifications(conn: CodexConnection) -> AsyncIterator[CodexWireMessage]: ...
```

## 6. Data Model

### 6.1 CodexEndpoint

```json
{
  "transport": "unix_websocket",
  "listenUrl": "unix:///tmp/haas/codex.sock",
  "auth": {
    "type": "capability_token_file",
    "tokenFile": "/run/haas/codex-ws-token"
  },
  "schemaVersion": "codex-cli-0.149.1"
}
```

### 6.2 CodexThreadRef

```json
{
  "threadId": "thr_123",
  "source": "codex-app-server",
  "generation": 3,
  "rolloutRef": {
    "kind": "opaque",
    "safeId": "rollout_fingerprint_abc"
  }
}
```

`rolloutRef` MUST be a safe, non-reversible reference and MUST NOT contain a local absolute path or rollout content.

### 6.3 CodexTurnStart

```json
{
  "threadId": "thr_123",
  "input": [
    {
      "type": "text",
      "text": "Summarise README.md"
    }
  ],
  "model": "gpt-5.6-terra",
  "cwd": "/workspace",
  "approvalPolicy": "on-request",
  "sandboxPolicy": {
    "mode": "workspace-write",
    "writableRoots": ["/workspace"],
    "networkAccess": false
  },
  "timeoutMs": 900000
}
```

## 7. Runtime Model and State Machine

```text
not_started
  -> starting_process
  -> connecting_transport
  -> initializing
  -> ready
  -> turn_running
  -> idle
  -> degraded
  -> restarting
  -> connecting_transport
```

Connection rules:

- Each connection MUST be initialized only once.
- Connection establishment and re-initialization MUST be serialized. Concurrent session
  preparation against a disconnected shared adapter reuses the first successfully initialized
  connection; a later caller MUST NOT close or replace a connection whose handshake is in flight.
- If Codex rejects a request made before initialization, the adapter MUST classify it as an adapter bug or startup race, not as an upstream request error.
- After app-server restarts, the generation increments, and the adapter reconnects and initializes again.
- If the native thread cannot be recovered, the HaaS session MUST enter `non_resumable` or a new session MUST be created; history MUST NOT be silently lost.
- A reader-loop exception MUST be retained as a bounded safe transport failure and consumed by the active turn. The adapter MUST distinguish a clean peer close from frame-limit, decode, and transport failures; it MUST never silently drop the reader exception and later report only a generic connection end.
- Connection-level Codex notifications and server requests MUST be delivered to every active turn consumer before turn/thread filtering. Multiple HaaS sessions may have turns in flight on the shared app-server connection; one consumer MUST NOT destructively take and discard another turn's message. A bounded pre-subscription replay buffer covers notifications emitted between `turn/start` acceptance and stream subscription.
- Each active turn consumer uses a bounded delivery queue. Publishing to a slow/full consumer MUST NOT block the shared app-server reader, JSON-RPC responses, or other sessions. The adapter disconnects only that consumer and terminates its accepted invocation with retryable `haas_adapter_overloaded`; it MUST NOT silently drop native events or fail unrelated turns.

Turn rules:

- A successful `turn/start` response means only that the native turn was accepted; it does not mean the HaaS response is complete.
- Codex notifications are the streaming source; Session Runtime aggregates the final response.
- `turn/completed`, `turn/failed`, and `turn/interrupted` MUST map to exactly one HaaS terminal event.
- A HaaS session MUST NOT run two Codex turns concurrently.
- Distinct HaaS sessions MAY run concurrently on separate Codex threads; their deltas, tool calls, usage and terminal events MUST remain isolated by native thread/turn identity.

Process-event rules:

- `item/reasoning/summaryTextDelta` may map to redacted `harness.reasoning.delta`. Raw `item/reasoning/textDelta` stays private and MUST NOT be persisted or projected. If no safe summary exists, Manager shows generic typed progress derived from tool/lifecycle facts rather than reconstructing chain-of-thought.
- `item/agentMessage/delta` and `item/reasoning/summaryTextDelta` carry their native `itemId`; reasoning additionally preserves `summaryIndex`. The adapter copies these as safe correlation facts and assigns a stable invocation-scoped `modelCallId` to each actual model round trip. Commentary, reasoning summary, triggered tool lifecycle and that round trip's usage share the id; model output after a tool result opens the next id. The ids contain no prompt or provider payload.
- `item/agentMessage/delta` has no authoritative phase. It is streamed immediately with `itemId` and remains unclassified until the matching agent-message `item/completed` supplies `phase=commentary|final_answer`. The adapter then emits `harness.output.item.completed` with the same `itemId`, `modelCallId`, and phase, without repeating message text. Consumers reclassify the existing item in place and MUST NOT infer phase from prose.
- A reasoning `item/completed` emits the same `harness.output.item.completed` fact with `itemId` and `modelCallId`, without copying summary or raw reasoning content. Consumers use that lifecycle boundary to freeze the bounded first-screen reasoning preview while retaining already streamed canonical summary text for explicit detail.
- Codex 0.152.1 `thread/tokenUsage/updated.tokenUsage` is an object with `last`, `total`, and optional `modelContextWindow`; it is not a flat token counter. The adapter emits `haas.usage.updated` with `scope=model_call`, maps `last` to `usage`, maps `total` to `cumulativeUsage`, and correlates the event to the current `modelCallId`. It preserves `inputTokens`, `outputTokens`, `totalTokens`, `cachedInputTokens` as `cacheReadTokens`, `cacheWriteInputTokens` as `cacheWriteTokens`, and `reasoningOutputTokens`. Missing native counters remain absent rather than zero-filled. `total` is a snapshot and MUST NOT be added to the model-call values.
- `cacheReadTokens` is the cached subset of `inputTokens`, and `reasoningOutputTokens` is the reasoning subset of `outputTokens`; neither is added again when computing total consumption. A usage notification meters the model call but does not by itself complete its stage: tools correlated to that call may start or finish afterward. The stage completes only after its correlated tools are terminal or when later model output opens the next model call.
- `item/started` and `item/completed` for command execution, file change, MCP tool call, and supported function tools map to `harness.tool.started` and exactly one `harness.tool.completed|failed`, correlated by native item id. Output/progress deltas map to bounded `harness.tool.output`.
- For command execution, the adapter captures the native `command`, `cwd`,
  `aggregatedOutput`, `durationMs`, `exitCode` and best-effort `commandActions` into the
  scoped in-memory execution-evidence store before normalizing public events. The sink keeps
  command output complete through 8 MiB (8,388,608 UTF-8 bytes); it MUST NOT apply a smaller
  intermediate accumulator limit. The stdio frame bound separately includes JSON overhead. Public events
  carry only a safe action summary, bounded redacted preview and opaque evidence reference.
  Codex 0.152.1 combines turn-command stdout and stderr in `aggregatedOutput` and its
  `item/commandExecution/outputDelta` has no stream discriminator; the adapter labels this
  honestly as combined command output and MUST NOT guess a stdout/stderr split.
  Codex may encode a shell invocation either as an argv array or as one serialized command
  string such as `/bin/zsh -lc \"<payload>\"`. For `commandPreview` and execution
  evidence, the adapter unwraps a recognized `sh|bash|zsh -c|-lc|-cl` envelope so the user
  sees the actual payload; unrecognized command strings remain unchanged. This normalization
  is presentation-only and MUST NOT alter the command sent to Codex.
  The private incremental accumulator is cleared when the command completes and, fail-safe, whenever
  its turn is finalized or the adapter shuts down; the scoped redacted evidence record owns the bounded
  post-command retention window.
- The adapter may mark a detected HTTPS URL as a user-authorization URL only when it is
  emitted by the command as an explicit user action and has recognizable authorization
  semantics. The evidence record enforces the bounded expiry even when the URL does not expose
  one in its query; a trustworthy earlier native expiry wins. It stores the full URL only in
  execution evidence. Unknown signed URLs use normal redaction and are never copied into public
  events.
- Assistant message `phase=commentary` is progress, not a final answer. `phase=final_answer` is final-answer evidence; `phase=null` remains legacy/unknown and cannot override a failed or incomplete turn.
- Server requests for command/file approval and `item/tool/requestUserInput` are drained concurrently with notifications. A blocking request pauses the turn without consuming the model-proxy capability or producing a terminal event. HaaS responds on the original JSON-RPC request id after the manager decision; cancel/turn termination resolves or cancels every pending request exactly once.

## 8. Security and Authorization

- `CODEX_HOME` MUST be scoped to a session/workspace or be an explicitly isolated runtime home.
- The Codex model provider MUST NOT store real API keys. Local API supplies a
  session-scoped, generationed loopback proxy capability through app-server
  overrides; it is revoked on session delete/revoke or runtime shutdown. Codex
  0.152.1 requires `thread/unsubscribe` before `thread/resume` to replace a
  loaded thread's provider override while preserving the native thread id.
  Runtime shutdown closes the owned app-server transport.
- Any Codex app-server child process started by the adapter MUST receive an explicit allowlisted environment. The default inherited allowlist is limited to process basics required to execute Codex (`PATH`), resolve an isolated home (`HOME`), create temporary files (`TMPDIR`/`TMP`/`TEMP`), and keep Unicode/locale behavior stable (`LANG`/`LC_ALL`/`LC_CTYPE`/`LC_MESSAGES`). Provider keys, cloud credentials, tokens, passwords, cookies, and other credential-like variables MUST NOT be inherited by construction; adding any new environment variable requires a spec delta documenting why it is required and why it is not a secret channel.
- `approvalPolicy=on-request` is the fresh interactive-session default. HaaS uses it only when
  command approval, file-change approval, and blocking input response paths are all live and
  advertised as `human_bridge`; otherwise the turn is rejected before acceptance rather than
  silently downgraded. `never` is an explicit no-prompt mode: Codex may execute actions already
  authorized by the sandbox/policy, but any native escalation request is denied and must converge
  to a stable policy failure. It never means danger-full-access.
- The adapter forwards only server-advertised, policy-eligible decisions/scopes. The default grant
  is the exact current action. Resolution answers the original JSON-RPC request id exactly once;
  it does not restart the turn, create a replacement invocation, or mutate session policy.
- Dynamic session policy changes are projected only at a subsequent `thread/start`,
  `thread/resume`, or `turn/start` after the HaaS revision barrier applies. An active native turn
  keeps its original approval and sandbox projection.
- With Codex 0.152.1, `human_bridge` in Default collaboration mode also sets the documented `features.default_mode_request_user_input=true` thread configuration. HaaS MUST NOT switch the session to Plan mode merely to expose the tool.
- Codex sandbox policy is a projection from Sandbox Runtime, driven by the Policy Controller. The adapter MUST NOT infer or broaden it independently; through `sandbox_declaration()`, the adapter only declares requirements.
- Codex `turn/start.sandboxPolicy.networkAccess` MUST be fail-closed. It is `false` by default and MAY be `true` only when the frozen effective network policy explicitly sets `defaultAction=allow`; `defaultAction=deny`, missing policy data, or malformed policy data MUST project to `false`.
- A WebSocket authentication token may be supplied only through a file or secret handle and MUST NOT appear in command-line arguments, logs, or status.
- Raw Codex events, rollouts, and command output MUST be redacted before entering Event Log.
- Raw command evidence is never sent to Event Log. The adapter writes it only to the bounded
  in-memory evidence sink supplied by Session Runtime; an unavailable sink degrades to the
  existing safe summary/preview rather than leaking details into events.

### 8.1 Capability Discovery Projection

For the initial Codex app-server release, the protocol capability snapshot MUST
project `streaming=native`, `sessionContinuation=native`, `pausing=native`,
`cancellation=best_effort`, `approval=unattended_only`, `input=unsupported`,
`toolRestriction=advisory`, `mcp=native`, `skills=native`,
`files=workspace_scan`, and `usage=native`, subject to live probe state. A failed
probe changes implemented capabilities to `unavailable`; it MUST NOT advertise a
harder enforcement level, human approval, or structured input support than this adapter provides.
The public snapshot MUST NOT expose `transport`, `schemaVersion`, socket path,
app-server generation, or native thread/turn ids.

## 9. Observability

The adapter exposes at least the following status fields:

| Field | Description |
|------|------|
| `status` | `ready`, `degraded`, or `unavailable` |
| `runtimeVersion` | `codex --version` or pinned package version |
| `transport` | `unix_websocket`, `loopback_websocket`, or `stdio` |
| `generation` | App-server process generation |
| `lastConnectedAt` | Most recent successful connection time |
| `lastErrorSafeReason` | Safe reason for the most recent error |
| `activeTurns` | Number of currently running turns |
| `pendingRequests` | Number of pending JSON-RPC requests |

Log events:

- `haas.codex.process_started`
- `haas.codex.connected`
- `haas.codex.initialized`
- `haas.codex.thread_started`
- `haas.codex.turn_started`
- `haas.codex.turn_terminal`
- `haas.codex.reconnect`
- `haas.codex.schema_mismatch`

## 10. Failure and Recovery

### 10.1 Pause and Continue

For both Pause and Stop, the adapter sends `turn/interrupt` for the exact native
thread/turn pair. The `{}` JSON-RPC result is acknowledgement only. Completion is
confirmed exclusively by the matching `turn/completed` notification with
`status=interrupted`; the adapter emits `harness.turn.interrupted` without rewriting
it to cancelled. Continue first calls `thread/resume` with `excludeTurns=true`, then
starts a new native turn on that thread. A missing thread returns non-resumable and
MUST NOT silently start a context-free replacement thread.

| Scenario | Behavior |
|------|------|
| Socket not created during side-effect-free preflight | `ready=false`; return pre-acceptance `haas_adapter_unavailable`; do not persist an invocation |
| Preflight initialization fails | Adapter becomes `degraded`; return pre-acceptance `haas_adapter_unavailable` or `haas_adapter_incompatible`; create no invocation |
| WebSocket queue overloaded | Before acceptance return retryable `haas_adapter_overloaded`; after acceptance converge on `haas.turn.failed` with that stable code and HTTP 200 |
| Per-turn notification consumer queue overloaded | Isolate and disconnect only the slow consumer; its accepted invocation converges on retryable `haas.turn.failed` with `haas_adapter_overloaded`; the shared reader and other sessions continue |
| Stdio notification is larger than asyncio's default line limit | Read it successfully up to the explicit adapter frame limit; preserve the tool terminal and following turn terminal in order |
| Stdio frame exceeds the explicit limit or is malformed | End the accepted invocation exactly once with `haas_adapter_unavailable` and a bounded safe transport reason; never misclassify it as a tool failure or generic clean EOF |
| Codex process exits after acceptance | Increment generation and attempt restart/reconnect; active turn terminates with failed/incomplete terminal events and HTTP 200 |
| Notification lacks a terminal event | After timeout, Session Runtime marks the invocation `failed` or `incomplete` |
| Model-proxy token becomes invalid during an active or resumable session | Refresh/rebind the session-scoped capability once when session/harness/provider scope and the owned credential channel still match; otherwise emit `failed` with stable `model_proxy_token_invalid`, retaining partial progress |
| Blocking server request is unsupported or cannot be restored | Fail closed with a stable interaction-unsupported/recovery code; never fabricate an answer or continue with a guessed choice |
| Cancellation requested | Invoke `turn/interrupt`; even if native cancellation is slow, the HaaS cancellation API MUST quickly return accepted/current state |
| `turn/interrupt` returns `{}` | Treat as acknowledgement only; keep draining native notifications and do not synthesize a terminal |
| `turn/completed(status=interrupted)` | Emit one normalized `harness.turn.interrupted`; Session Runtime maps recorded Pause intent to canonical `haas.turn.interrupted` and Stop intent to `haas.turn.cancelled`; session-scoped model proxy capabilities remain usable until session revoke/delete or runtime shutdown |
| Schema drift | Probe fails and blocks release; runtime returns `haas_adapter_incompatible` |

### 10.2 Start-Path Error Mapping (delta)

Every exception raised before `turn/start` returns (connect/initialize,
`thread/start`, `thread/resume`, and the `turn/start` call itself) MUST converge on
a structured `AdapterTurnStartError` carrying a stable `haas_*` code, a safe
`detail` (native method + numeric JSON-RPC code only - never the native message,
prompt, params, or credentials), and a `retryable` flag. Unmapped native
failures previously escaped to the session runtime's generic handler and produced
an undiagnosable terminal with `code="failed"` / `safeReason="failed"`.

JSON-RPC error responses from Codex app-server are surfaced as
`CodexRPCError(CodexConnectionError)` that preserves `error.code`; the message is
redacted and retained only in `safe_message` (and the legacy `str()` form) for
`thread not found` detection.

| Start-path failure | Stable code | retryable | detail (safe) |
|------|------|------|------|
| `initialize`/connect/transport spawn fails | `haas_adapter_unavailable` | true | connect/transport |
| `thread/start` or `thread/resume` transport send/recv failure | `haas_adapter_unavailable` | true | thread lifecycle |
| Native JSON-RPC `-32601` method_not_found / `-32602` invalid_params | `haas_adapter_config_error` | false | native numeric code only |
| Native JSON-RPC `-32603` internal_error / `-32xxx` server errors | `haas_adapter_unavailable` | true | native numeric code only |
| `turn/start` request timeout | `haas_request_timeout` | true | turn/start timeout |
| Any other native JSON-RPC error | `haas_codex_rpc_error` | true | native method + code |
| `thread not found` on resume | (non-resumable, no error) | - | resume dropped |

The session runtime generic fallback MUST map any residual exception: if it exposes
a string `.code`, use it; model/provider configuration errors
(`ModelRouteError`/`SecretResolutionError`/`RuntimeTokenError`) become
`haas_provider_error`; everything else becomes `haas_adapter_error`. The terminal
`reason` is the safe exception class name; the raw exception message is never
emitted on the wire.

## 11. Test Plan and Acceptance Criteria

- Unit: JSON-RPC request-id matching, server-request recognition, event normalizer, `itemId`/`modelCallId` correlation, nested `last`/`total` usage normalization, safe rollout reference, a single NDJSON notification larger than 64 KiB, and complete 100,001-byte command-evidence output.
- Integration: stdio fake app-server handshake and side-effect-free preflight occur before durable invocation acceptance; thread/start/turn/start occur only after acceptance, and any accepted failure converges on HTTP-200 terminal events.
- Local E2E: run one complete turn through `codex app-server --listen stdio://` or `ws://127.0.0.1:<port>`.
- Schema: run `codex app-server generate-json-schema` and compare it with the pinned schema fixture.
- Cancellation: start a long-running turn, invoke cancellation, and verify that the final invocation status is `cancelled`.
- Pause/Continue: verify interrupt acknowledgement alone is nonterminal, the matching native terminal stays `harness.turn.interrupted`, Pause becomes canonical `interrupted`, and `thread/resume(excludeTurns=true)` precedes exactly one linked new turn.
- Security: Codex env/config/rollout/log/event contains no real provider key, Authorization value, or raw prompt.
- Capability: before the interaction bridge passes, caller-visible projection reports `approval=unattended_only`, `input=unsupported`, and `toolRestriction=advisory`. After the complete bridge passes, approval and input change together to `human_bridge`; partial advertisement is forbidden. The projection follows live probe availability and omits transport/socket/schema/native ids.
- Golden event coverage: real 0.152.1 `item/started`, output/progress, `item/completed`, reasoning, assistant phase, nested token usage, approval, user-input and `serverRequest/resolved` fixtures map to stable types with correlation and redaction.
- Command evidence: a real 0.152.1 command fixture retains command/cwd/combined output in the
  scoped evidence sink, masks credential values, keeps an accepted expiring authorization URL
  byte-for-byte usable until expiry, and exposes only `evidenceRef`/expiry plus safe fields to
  Event Log.
- Interactive E2E: one command approval, one file approval and one blocking structured question pause and resume the same native turn; reconnect reconstructs the pending card and duplicate decisions are rejected.
- Defaults and modes: fresh turns project `workspace-write`, public network allow and
  `on-request`; `never` denies escalation without prompting; `always` asks for every action the
  pinned Codex schema supports as approval-eligible.
- Revision: change approval mode while a native turn is blocked/running and verify the active
  request stays on its old projection while the next invocation uses the applied revision.

### 11.1 Schema Fixture Contract

- Fixture path: `tests/fixtures/codex/schema/codex-cli-<version>.json`.
- Current fixture: `codex-cli-0.152.1.json` (302 generated JSON files). Compared with 0.151.0 it adds `v2/AuthRecoveryNotification.json` and changes eight existing schemas; adapters must ignore unknown additive notifications safely while retaining the checked mappings below.
  `<version>` is the **semantic version number** output by `codex --version` (for example, `0.150.1`); `AdapterProbe.runtimeVersion` retains the complete string (for example, `codex-cli 0.150.1`).
- Fixture content: a single JSON object `{"codexCliVersion": "<version>", "files": {relative path: JSON content}}`. `files` covers every `.json` file in the `generate-json-schema` output directory, including the root bundle and the `v1/` and `v2/` subdirectories.
- Comparison procedure: during probe, rerun `codex app-server generate-json-schema` and perform a structural deep-equal comparison of `files`, independent of JSON serialization order. On success, `schema_drift(fixture, current) == []`. When upgrading the Codex version, regenerate the fixture before running the comparison.
- Failure semantics: non-empty drift → `probe.status=unavailable`, `safeReason=schema_mismatch`, and release is blocked; runtime returns `haas_adapter_incompatible`.
