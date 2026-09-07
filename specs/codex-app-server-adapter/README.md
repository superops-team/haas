# Codex App-Server Adapter Component Specification

**English** | [简体中文](README.zh-CN.md)

Status: Draft
Last reviewed: 2026-08-26
Related specs: [Harness Adapter](../harness-adapter/README.md), [Session Runtime](../session-runtime/README.md), [Event Log & SSE](../event-log-sse/README.md), [Model Proxy](../model-proxy/README.md)

## 1. Component Role

The Codex App-Server Adapter is the only P0 concrete harness adapter in the initial HaaS release. It connects to, initializes, drives, and recovers Codex app-server, and converts native Codex JSON-RPC notifications into canonical HaaS events that are ultimately projected as ADK `Event` objects.

Codex app-server is an internal implementation detail. Upstream systems MUST NOT connect directly to Codex WebSocket, Unix socket, or stdio transports, and MUST NOT depend on Codex `threadId`, `turnId`, notification methods, or rollout file paths.

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
3. `stdio://`: test fallback and minimal local smoke testing. The child process uses stdin/stdout NDJSON, with one JSON-RPC message per line.

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
  "approvalPolicy": "never",
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
- If Codex rejects a request made before initialization, the adapter MUST classify it as an adapter bug or startup race, not as an upstream request error.
- After app-server restarts, the generation increments, and the adapter reconnects and initializes again.
- If the native thread cannot be recovered, the HaaS session MUST enter `non_resumable` or a new session MUST be created; history MUST NOT be silently lost.

Turn rules:

- A successful `turn/start` response means only that the native turn was accepted; it does not mean the HaaS response is complete.
- Codex notifications are the streaming source; Session Runtime aggregates the final response.
- `turn/completed`, `turn/failed`, and `turn/interrupted` MUST map to exactly one HaaS terminal event.
- A HaaS session MUST NOT run two Codex turns concurrently.

## 8. Security and Authorization

- `CODEX_HOME` MUST be scoped to a session/workspace or be an explicitly isolated runtime home.
- The Codex model provider MUST NOT store real API keys; it SHOULD obtain a short-lived bearer through the model proxy and `auth.command`.
- Any Codex app-server child process started by the adapter MUST receive an explicit allowlisted environment. The default inherited allowlist is limited to process basics required to execute Codex (`PATH`), resolve an isolated home (`HOME`), create temporary files (`TMPDIR`/`TMP`/`TEMP`), and keep Unicode/locale behavior stable (`LANG`/`LC_ALL`/`LC_CTYPE`/`LC_MESSAGES`). Provider keys, cloud credentials, tokens, passwords, cookies, and other credential-like variables MUST NOT be inherited by construction; adding any new environment variable requires a spec delta documenting why it is required and why it is not a secret channel.
- `approvalPolicy=never` is the unattended default. Human approval MUST NOT be enabled until the HaaS approval bridge has been extended to support it.
- Codex sandbox policy is a projection from Sandbox Runtime, driven by the Policy Controller. The adapter MUST NOT infer or broaden it independently; through `sandbox_declaration()`, the adapter only declares requirements.
- Codex `turn/start.sandboxPolicy.networkAccess` MUST be fail-closed. It is `false` by default and MAY be `true` only when the frozen effective network policy explicitly sets `defaultAction=allow`; `defaultAction=deny`, missing policy data, or malformed policy data MUST project to `false`.
- A WebSocket authentication token may be supplied only through a file or secret handle and MUST NOT appear in command-line arguments, logs, or status.
- Raw Codex events, rollouts, and command output MUST be redacted before entering Event Log.

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

| Scenario | Behavior |
|------|------|
| Socket not created | `ready=false`; the execution path waits for a bounded interval and returns `haas_adapter_unavailable` on timeout |
| Initialization fails | Adapter becomes `degraded`; new turns fail closed |
| WebSocket queue overloaded | Map to retryable `haas_adapter_overloaded` or `rate_limited` |
| Codex process exits | Increment generation and attempt restart/reconnect; active turn terminates as failed or incomplete |
| Notification lacks a terminal event | After timeout, Session Runtime marks the invocation `failed` or `incomplete` |
| Cancellation requested | Invoke `turn/interrupt`; even if native cancellation is slow, the HaaS cancellation API MUST quickly return accepted/current state |
| Schema drift | Probe fails and blocks release; runtime returns `haas_adapter_incompatible` |

## 11. Test Plan and Acceptance Criteria

- Unit: JSON-RPC request-id matching, server-request recognition, event normalizer, usage normalizer, and safe rollout reference.
- Integration: stdio fake app-server handshake, thread/start, turn/start, and terminal event.
- Local E2E: run one complete turn through `codex app-server --listen stdio://` or `ws://127.0.0.1:<port>`.
- Schema: run `codex app-server generate-json-schema` and compare it with the pinned schema fixture.
- Cancellation: start a long-running turn, invoke cancellation, and verify that the final invocation status is `cancelled`.
- Security: Codex env/config/rollout/log/event contains no real provider key, Authorization value, or raw prompt.

### 11.1 Schema Fixture Contract

- Fixture path: `tests/fixtures/codex/schema/codex-cli-<version>.json`.
  `<version>` is the **semantic version number** output by `codex --version` (for example, `0.150.1`); `AdapterProbe.runtimeVersion` retains the complete string (for example, `codex-cli 0.150.1`).
- Fixture content: a single JSON object `{"codexCliVersion": "<version>", "files": {relative path: JSON content}}`. `files` covers every `.json` file in the `generate-json-schema` output directory, including the root bundle and the `v1/` and `v2/` subdirectories.
- Comparison procedure: during probe, rerun `codex app-server generate-json-schema` and perform a structural deep-equal comparison of `files`, independent of JSON serialization order. On success, `schema_drift(fixture, current) == []`. When upgrading the Codex version, regenerate the fixture before running the comparison.
- Failure semantics: non-empty drift → `probe.status=unavailable`, `safeReason=schema_mismatch`, and release is blocked; runtime returns `haas_adapter_incompatible`.
