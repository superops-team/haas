# Manager HaaS Sidecar Backend Specification

**English** | [简体中文](README.zh-CN.md)

Status: Draft
Last reviewed: 2026-09-10
Change ID: manager-haas-sidecar-backend
Related specs: [HaaS Protocol](../haas-protocol/README.md), [Manager Delegation](../manager-delegation/README.md), [Manager Product Identity](../manager-product-identity/README.md), [Model Proxy](../model-proxy/README.md), [MCP / Tool / Skill Runtime](../mcp-tool-skill-runtime/README.md), [Codex App-Server Adapter](../codex-app-server-adapter/README.md), [Config](../config/README.md), [Security Boundary](../security-boundary/README.md), [Observability](../observability/README.md)

## 1. Component Role

Manager HaaS Sidecar Backend defines how the OpenHarness manager uses HaaS as its standard execution backend in the desktop product.

The manager MAY supervise a local HaaS sidecar process for standalone desktop distribution, and it MAY connect to a remote HaaS sidecar configured by the user or operator. In both cases, manager-to-HaaS communication MUST use the same HaaS HTTP/SSE protocol and the same manager-facing client contract. The manager MUST NOT bypass HaaS by directly calling Codex app-server, Codex CLI, HaaS Python service objects, model providers, MCP servers, or skill materialization internals.

Canonical execution path:

```text
OpenHarness GUI
  -> manager local API / session owner
  -> HaasClient
       -> local managed sidecar  (http://127.0.0.1:<port>)
       -> remote sidecar         (https://...)
  -> HaaS ADK /run_sse + HaaS native /v1/haas/*
  -> HaaS Session Runtime / Event Log / Policy / Model Proxy / MCP / Skills
  -> Codex app-server adapter
  -> Codex runtime
```

Codex remains a HaaS adapter implementation detail. Its stdio, Unix socket, or WebSocket transport is configured inside HaaS and MUST NOT become a manager public contract.

## 2. Sources and Rationale

| Source | Adopted content |
|--------|-----------------|
| Product decision | Local managed HaaS sidecar is the official desktop execution path; remote HaaS sidecar remains selectable through configuration. |
| Product decision | Manager-to-local and manager-to-remote protocols must stay identical to continuously validate the standard HaaS sidecar entry layer. |
| Product decision | Direct embedded Codex is not the default path because it bypasses HaaS sidecar, model proxy, MCP, skills, policy, event log, and session/runtime contracts. |
| Manager Delegation | First successful delegation fixes the manager session binding; follow-up turns reuse the same HaaS delegated session and restore runtime resources as needed. |
| HaaS Protocol | ADK `/run_sse` is the execution stream; `/v1/haas/*` carries HaaS native control-plane operations. |
| Model Proxy | Provider credentials must remain secretless and be resolved by HaaS, not exposed to Codex or manager-visible logs. |
| MCP / Tool / Skill Runtime | MCP and skills must be materialized by HaaS so proxy, policy, and audit semantics are tested on the real sidecar path. |
| Manager Product Identity | OpenHarness is local-first and has no product cloud login; local HaaS sidecar uses a local token and user-owned configuration. |

## 3. Upstream and Downstream Relationships

| Direction | Component | Relationship |
|-----------|-----------|--------------|
| Upstream | OpenHarness GUI | Selects local managed or remote sidecar mode, shows status, and sends user turns to the manager local API. |
| Upstream | Manager session store | Owns manager session ids, user-visible transcript state, and HaaS binding metadata. |
| Upstream | Manager MCP / skills / provider settings | Supplies user-authorized configuration intent and secret references for HaaS materialization. |
| Downstream | HaaS Protocol | Provides the only execution and control-plane protocol used by manager for HaaS-backed sessions. |
| Downstream | HaaS local sidecar supervisor | Starts, stops, and monitors the local sidecar process for desktop distribution. |
| Downstream | Remote HaaS sidecar | Provides the same protocol over a configured HTTPS endpoint. |
| Downstream | HaaS Model Proxy | Owns provider credential resolution and provider compatibility transforms. |
| Downstream | HaaS MCP / Tool / Skill Runtime | Owns MCP proxy, skill snapshot materialization, and adapter-specific tool config. |
| Downstream | HaaS Codex Adapter | Owns Codex native transport, JSON-RPC, thread/turn lifecycle, and event normalization. |

## 4. Responsibility Boundaries

Manager responsibilities:

- Offer a backend selector with `local_managed` and `remote` HaaS modes.
- Start and monitor the local HaaS sidecar when `mode=local_managed` and autostart is enabled.
- Store the local/remote sidecar endpoint and non-secret preferences in manager settings.
- Store HaaS bearer tokens or equivalent credentials only in the manager secret store.
- Use one `HaasClient` implementation for local and remote sidecars.
- Create or restore a manager-session binding to a HaaS delegated session.
- Submit manager-effective MCP, skill, provider, workspace, and policy configuration to HaaS as intent, not as Codex-native config.
- Keep UI approvals and user-facing recovery decisions in the manager.
- Surface safe sidecar health, readiness, capability, and binding status to GUI.

HaaS responsibilities:

- Expose the same ADK-compatible and HaaS native HTTP/SSE protocol for local and remote callers.
- Own session runtime, event log, idempotency, cancellation, approval relay, policy, model proxy, MCP proxy, skill materialization, artifact store, and adapter execution.
- Own Codex app-server transport and lifecycle inside the Codex adapter.
- Validate materialized MCP, skills, provider route, and policy snapshots before a turn runs.
- Provide capability discovery so manager can decide whether local bind mount, remote workspace, MCP, skills, and model proxy are available.
- Fail closed with structured HaaS errors when a requested capability is unavailable or unsafe.

Non-responsibilities:

- The manager does not directly call Codex app-server, Codex CLI, model provider APIs, MCP servers, or skill loaders for HaaS-backed sessions.
- The local sidecar supervisor does not define HaaS execution semantics; it only manages process lifecycle.
- HaaS does not decide manager UX intent routing or user consent copy.
- Remote HaaS does not implicitly receive host bind mounts; remote workspace transfer is a separate capability contract.

## 5. Core Interfaces

### 5.1 Manager Backend Configuration

```toml
[execution]
backend = "haas"

[haas]
mode = "local_managed" # local_managed | remote
base_url = "http://127.0.0.1:8092"
auth = "bearer"
token_ref = "secret://manager/haas/default"
request_timeout_seconds = 30

[haas.local_managed]
autostart = true
host = "127.0.0.1"
port = 8092
port_selection = "fixed" # fixed | auto
data_dir = "<manager-state>/haas"
log_file = "<manager-state>/logs/haas-sidecar.log"

[haas.remote]
base_url = "https://haas.example.com"
tls_verify = true
capability_probe = true
```

Rules:

- `execution.backend="haas"` is the default HaaS execution backend. It does not imply local or remote by itself.
- `haas.mode` selects endpoint ownership only. It MUST NOT change manager-to-HaaS protocol semantics.
- `local_managed` requires loopback `base_url` (`127.0.0.1` or `localhost`). Remote URLs MUST NOT use local autostart.
- `api_token`, bearer token, OAuth token, or equivalent credentials MUST be stored by reference and MUST NOT be returned by read APIs.
- Settings changes apply to new unbound manager sessions. Existing HaaS-bound sessions keep their binding unless explicitly rebound.

### 5.2 HaasClient Contract

One client contract is used for both local and remote HaaS:

```python
class HaasClient:
    async def health() -> HealthResult: ...
    async def ready(scope: str = "execution") -> ReadyResult: ...
    async def status() -> StatusResult: ...
    async def capabilities() -> CapabilityResult: ...
    async def list_apps() -> list[str]: ...
    async def create_delegated_session(body: dict, idempotency_key: str) -> dict: ...
    async def restore_delegated_session(delegated_session_id: str) -> dict: ...
    async def update_delegated_policy(delegated_session_id: str, patch: dict) -> dict: ...
    async def run_sse(app_name: str, user_id: str, session_id: str, message: dict) -> AsyncIterator[dict]: ...
    async def cancel(session_id: str, invocation_id: str) -> dict: ...
    async def get_session(app_name: str, user_id: str, session_id: str) -> dict: ...
```

The client MUST use only HTTP/SSE and HaaS errors. Local mode MUST NOT import HaaS Python objects or call Codex adapters directly.

### 5.3 Sidecar Capability Discovery

HaaS SHOULD expose a native capability endpoint or include equivalent fields in `/v1/haas/status`:

```json
{
  "object": "haas_capabilities",
  "protocolVersion": "2026-08-26",
  "adkCompatible": true,
  "supportsRunSse": true,
  "supportsDelegatedSessions": true,
  "workspaceModes": ["bind_mount", "snapshot_upload", "remote_workspace"],
  "supportsLocalBindMount": true,
  "supportsModelProxy": true,
  "supportsMcpProxy": true,
  "supportsSkillMaterialization": true,
  "harnesses": [
    {
      "id": "chrn_codex_default",
      "base": "codex",
      "adapter": "codex-app-server",
      "adapterTransport": "stdio",
      "streaming": true
    }
  ]
}
```

Capability discovery MUST be safe to call before a session exists and MUST NOT reveal secrets, host absolute paths, raw MCP headers, provider credentials, or native Codex ids.

### 5.4 Configuration Materialization API

Manager-effective configuration is submitted to HaaS as HaaS-native objects, not Codex-native files. HaaS then materializes adapter-specific files and loopback services internally.

The initial implementation MAY use `PUT /v1/haas/harnesses/{harness_id}` for configured harness updates and `POST /v1/haas/delegated-sessions` for session-specific snapshots. If additional native routes are introduced, they MUST be additive and documented in HaaS Protocol.

Required logical payload:

```json
{
  "provider": {
    "providerId": "openai",
    "model": "gpt-5.2-codex",
    "credentialRef": "secret://manager/provider/openai/default"
  },
  "mcpServers": [
    {
      "name": "github",
      "transport": "http",
      "url": "http://127.0.0.1:18081/mcp/github",
      "requiresApproval": true,
      "includeTools": ["search_issues"]
    }
  ],
  "skills": [
    {
      "name": "security-fix-pr",
      "files": [
        {"path": "SKILL.md", "contentRef": "artifact://skill/security-fix-pr/SKILL.md"}
      ],
      "fingerprint": "sha256:..."
    }
  ],
  "policy": {
    "workspaceRoots": [{"path": "/workspace", "access": "rw"}],
    "approvalPolicy": "never"
  }
}
```

### 5.5 Product Interaction Surfaces

The manager GUI MUST expose HaaS backend selection as an execution setting, not
as a model provider and not as a Codex transport selector.

Required settings surface:

| UI area | Requirement |
|---------|-------------|
| Backend mode selector | Presents `Local managed sidecar` and `Remote sidecar` as mutually exclusive choices. The default is `Local managed sidecar`. |
| Local managed card | Shows start/stop state, control readiness, execution readiness, selected port, log location, and a safe last-error reason. |
| Remote sidecar card | Shows base URL, TLS verification state, credential status, capability probe result, and safe last-error reason. |
| Capability summary | Shows whether the selected sidecar supports `bind_mount`, `snapshot_upload`, `remote_workspace`, model proxy, MCP proxy, and skill materialization. |
| Current-session binding | Shows when an open chat is already bound to a sidecar and that changing global backend settings will affect new sessions only. |
| Rebind affordance | Requires explicit user action and confirmation; must explain that rebind changes execution backend/config for future turns in that chat. |

Interaction rules:

- Switching backend mode in Settings MUST NOT mutate any existing HaaS-bound
  manager session.
- If the user opens a bound chat after changing backend mode, the chat header
  MUST show the bound mode and sidecar fingerprint rather than the new global
  default.
- If a remote sidecar capability probe fails, the GUI MAY save the configuration
  but MUST mark it unavailable for new delegated turns until the next successful
  probe.
- If local managed sidecar autostart fails, the GUI MUST expose a safe retry
  action and log path; it MUST NOT silently fall back to local manager execution
  for a HaaS-bound chat.
- Backend mode controls execution only. Provider key setup, connector setup,
  MCP OAuth, and skill management remain local manager settings whose effective
  values are submitted to HaaS through materialization.

### 5.6 Chat Routing and Filtering Policy

When a user sends a chat message, manager MUST apply a deterministic routing
policy before contacting any harness:

```text
1. Existing HaaS binding?
   yes -> route to bound sidecar; do not reclassify.
2. HaaS backend enabled and selected?
   no -> use local manager engine.
3. Agent/persona allowed for HaaS?
   no -> use local manager engine.
4. Workspace available and authorized?
   no -> use local manager engine or request authorization.
5. Selected sidecar capability supports required workspace mode?
   no -> fail closed with safe reason or request a supported workspace transfer mode.
6. Content type supported by HaaS materialization?
   no -> use local manager engine or fail with unsupported-content reason.
7. Deterministic trigger matches?
   yes -> create/bind HaaS delegated session and route via /run_sse.
   no -> use local manager engine.
```

Filtering inputs:

| Input | Effect |
|-------|--------|
| Existing binding | Highest priority. Forces reuse of the previously bound HaaS sidecar/session. |
| Backend global mode | Selects local managed or remote sidecar only for unbound sessions. |
| Persona/agent allowlist | Prevents non-code or unsupported personas from being routed to HaaS. |
| Workspace trust | Required before project-local MCP, skills, and bind mounts can be used. |
| Workspace mode capability | Local managed may use `bind_mount`; remote may require `snapshot_upload` or `remote_workspace`. |
| Content shape | Text-only is the initial supported path. File/image/multimodal support requires explicit HaaS capability. |
| Trigger keywords / explicit command | Deterministic trigger selects HaaS for first delegation. |
| User override | Explicit “run locally”, “run with HaaS”, or “rebind” actions override only unbound sessions unless confirmed. |

Chat UI requirements:

- The composer or chat header MUST show the effective backend for the current
  session: `Local`, `HaaS local sidecar`, or `HaaS remote sidecar`.
- The first HaaS-routed turn MUST show that a HaaS delegated session is being
  created and which workspace mode is used.
- Follow-up turns in a HaaS-bound session MUST show “bound to HaaS” even if the
  current message no longer matches trigger keywords.
- If routing chooses local execution because filters do not match, no HaaS
  binding is created.
- If routing fails after binding exists, the chat shows a recoverable HaaS error
  with retry/rebind/new-session actions. It MUST NOT transparently execute the
  same turn locally.

### 5.7 Streaming Bridge Contract

HaaS streams ADK events over `/run_sse`. Manager surfaces stream session events
to GUI over the existing manager WebSocket. The bridge between those protocols
is a product contract: local managed and remote sidecars MUST produce the same
manager WebSocket event sequence for equivalent HaaS event streams.

```text
HaaS /run_sse data: ADK Event
  -> HaasClient parses SSE frame
  -> manager stream bridge validates and normalizes
  -> manager session WebSocket broadcasts EventType payload
  -> GUI transcript renders through the same local-run components
```

Required mapping:

| HaaS ADK event signal | Manager WebSocket event | GUI behavior |
|-----------------------|-------------------------|--------------|
| First accepted delegated turn | `turn_start` with `data.delegated` | Start live run row and show HaaS binding/backend context. |
| `content.parts[].text` delta or message text | `assistant_delta` | Append to current streaming answer using the existing stream gate. |
| Final accumulated assistant text | `assistant_message` | Persist one assistant message with `data.delegated`. |
| `actions.stateDelta.status in completed/failed/cancelled/incomplete` | `turn_end` | Close live stream and show terminal state. |
| HaaS approval request | `permission_required` or a HaaS-delegated approval event variant | Show the same approval card surface used by local runs; decisions route back through HaaS. |
| HaaS tool/activity metadata | `tool_started` / `tool_finished` when safely representable, otherwise delegated status metadata | Show activity in the same transcript group without exposing raw tool arguments. |
| HaaS structured error before terminal event | `error` | Show safe HaaS error with retry/rebind/new-session actions. |

Bridge rules:

- Manager MUST validate every SSE `data:` frame as an ADK event before projection.
- SSE comments and heartbeats MUST NOT create GUI transcript items.
- Manager MUST preserve event order from HaaS within a single invocation.
- Manager MUST make `assistant_delta` idempotent at the GUI layer by tracking
  HaaS event ids; reconnect replay MUST NOT duplicate visible text.
- Manager MUST aggregate streamed text into one final assistant message, matching
  local manager behavior, while preserving delegated metadata for audit and UI.
- Manager MUST NOT expose HaaS internal fields such as adapter id, container id,
  host mount path, native Codex thread id, raw tool arguments, or credentials to GUI.
- If `/run_sse` disconnects, manager MUST treat it as transport loss, not turn
  cancellation. It SHOULD resume through HaaS replay (`Last-Event-ID` or native
  event stream) when the sidecar supports replay; otherwise it MUST read back the
  HaaS session before deciding whether retry is safe.
- Manager WebSocket disconnects MUST NOT cancel the HaaS invocation. A returning
  GUI client receives manager-persisted transcript state plus any HaaS replay the
  manager can reconcile.
- Backpressure is handled independently on both legs: a slow GUI client MUST NOT
  block HaaS event consumption long enough to lose terminal state.
- Local managed and remote sidecars MUST use the same bridge implementation and
  test vectors. Differences in URL, auth, or TLS MUST NOT change event semantics.

## 6. Data Model

### 6.1 ManagerHaasBackendConfig

```json
{
  "object": "manager_haas_backend_config",
  "mode": "local_managed",
  "baseUrl": "http://127.0.0.1:8092",
  "auth": {"type": "bearer_ref", "ref": "secret://manager/haas/default"},
  "localManaged": {
    "autostart": true,
    "host": "127.0.0.1",
    "port": 8092,
    "portSelection": "fixed",
    "dataDir": "<manager-state>/haas",
    "logFile": "<manager-state>/logs/haas-sidecar.log"
  },
  "remote": {
    "tlsVerify": true,
    "capabilityProbe": true
  }
}
```

### 6.2 ManagerHaasSessionBinding

```json
{
  "backend": "haas",
  "mode": "local_managed",
  "baseUrlFingerprint": "sha256:...",
  "delegatedSessionId": "dgsess_abc",
  "haasSessionId": "hsess_abc",
  "haasUserId": "manager",
  "harnessId": "chrn_codex_default",
  "configSnapshot": {
    "providerFingerprint": "sha256:...",
    "mcpVersion": "sha256:...",
    "skillsVersion": "sha256:...",
    "policyVersion": "sha256:...",
    "workspaceMode": "bind_mount"
  },
  "createdAtMs": 1786400000000,
  "updatedAtMs": 1786401000000
}
```

Binding rules:

- A manager session with a HaaS binding MUST continue through the bound sidecar unless the user explicitly starts a new session or performs an audited rebind.
- Switching global backend settings affects new unbound sessions only.
- The binding stores fingerprints and ids, not bearer tokens, provider keys, MCP headers, raw skill content, or native Codex ids.

### 6.3 LocalManagedSidecarStatus

```json
{
  "mode": "local_managed",
  "status": "running",
  "pid": 12345,
  "baseUrl": "http://127.0.0.1:8092",
  "controlReady": true,
  "executionReady": true,
  "lastErrorSafeReason": null,
  "managed": true
}
```

Status values: `disabled`, `starting`, `running`, `degraded`, `stopped`, `blocked`, `failed`.

### 6.4 ChatRoutingDecision

```json
{
  "object": "manager_haas_routing_decision",
  "sessionId": "mgr_sess_123",
  "decision": "haas",
  "reason": "trigger_keyword",
  "backendMode": "local_managed",
  "sidecarFingerprint": "sha256:...",
  "workspaceMode": "bind_mount",
  "bindingState": "new",
  "safeMessage": "This turn will run through the local HaaS sidecar."
}
```

Decision values: `local`, `haas`, `request_authorization`, `unsupported`, `blocked`.

Reason values include `existing_binding`, `disabled`, `agent_not_allowed`,
`workspace_not_trusted`, `capability_missing`, `unsupported_content`,
`trigger_keyword`, `explicit_user_choice`, and `rebind_required`.

The routing decision is an internal manager record and may be projected into GUI
status text. It MUST NOT include raw prompt content, provider credentials, MCP
headers, or full tool arguments.

### 6.5 ManagerStreamProjection

```json
{
  "object": "manager_stream_projection",
  "source": "haas",
  "haasEventId": "evt_0000000001042",
  "invocationId": "inv_abc",
  "managerEventType": "assistant_delta",
  "sequence": 7,
  "dedupeKey": "haas:evt_0000000001042",
  "delegated": {
    "backend": "haas",
    "mode": "remote",
    "delegatedSessionId": "dgsess_abc"
  },
  "safePayload": {
    "text": "partial text"
  }
}
```

`ManagerStreamProjection` is not necessarily persisted as a separate object, but
the bridge MUST behave as if this normalized record exists: every visible GUI
event has a source HaaS event id or a manager-generated synthetic id, a stable
dedupe key, delegated metadata, and a safe payload.

## 7. Runtime Model and State Machine

### 7.1 Backend Selection

```text
manager starts
  -> load backend config
  -> if mode=local_managed and autostart=true: ensure local sidecar
  -> probe configured HaaS endpoint
  -> expose status to GUI
```

### 7.2 Local Managed Sidecar Lifecycle

```text
disabled
  -> starting
  -> running
  -> degraded
  -> restarting
  -> running

running
  -> stopped
  -> failed
```

Rules:

- Local managed sidecar is a process managed by the desktop manager, not an alternative protocol implementation.
- `/v1/haas/health` proves process liveness. `/v1/haas/ready?scope=execution` proves the selected harness can accept execution.
- If the sidecar process is running but execution readiness fails, GUI MUST show degraded execution while keeping local settings accessible.
- Sidecar logs are stored in the manager state directory and must be redacted.

### 7.3 Session Execution

```text
manager session unbound
  -> deterministic HaaS decision
  -> create delegated session through HaaS
  -> persist ManagerHaasSessionBinding
  -> restore delegated runtime through HaaS
  -> run turn via /run_sse

manager session bound
  -> restore delegated runtime through same HaaS binding
  -> run follow-up via /run_sse
```

### 7.4 Local vs Remote Workspace Modes

Local managed sidecar may support host bind mounts after explicit user authorization:

```text
host project path -> /workspace rw
extra authorized paths -> /mnt/extra/* ro
```

Remote sidecar MUST NOT assume access to local host paths. It must advertise a supported workspace mode:

- `bind_mount`: sidecar runs on the same host and can bind mount manager-approved paths.
- `snapshot_upload`: manager uploads an archive/snapshot through HaaS artifact APIs.
- `remote_workspace`: manager references a pre-existing remote workspace id or git ref.

If the selected sidecar does not support the workspace mode needed for the current project, manager MUST fail closed before creating a delegated session.

### 7.5 Streaming State Machine

```text
idle
  -> ws_turn_start_sent
  -> haas_sse_opening
  -> haas_sse_streaming
  -> terminal_seen
  -> manager_turn_done_sent

haas_sse_streaming
  -> haas_sse_disconnected
  -> replay_reconcile
  -> haas_sse_streaming

haas_sse_streaming
  -> gui_ws_disconnected
  -> continue_consuming_haas
  -> gui_ws_reconnected
  -> replay_manager_state
```

Rules:

- `turn_start` is sent once per manager turn, before the first visible HaaS
  output, even if HaaS replay already contains earlier events.
- `turn_done` is sent once after a terminal HaaS status or a safe unrecoverable
  bridge error.
- A terminal HaaS event wins over transient transport errors observed after it.
- If manager receives a HaaS terminal failure and text deltas in the same
  invocation, GUI shows the accumulated text plus the terminal failure state.

## 8. Security and Permissions

- Manager-to-HaaS traffic requires bearer auth except for health/ready probes.
- Local managed sidecar token MUST be generated locally, stored in the manager secret store or a user-private token file, and never returned through settings read APIs.
- Remote sidecar tokens MUST be stored in the manager secret store.
- Manager MUST NOT persist tokens, provider keys, MCP headers, cookies, raw prompts, or full tool arguments in HaaS bindings.
- Local managed sidecar autostart is allowed only for loopback URLs.
- Remote sidecar MUST use HTTPS by default; disabling TLS verification requires an explicit insecure-development setting and MUST be visible in GUI.
- MCP and skills are submitted as HaaS materialization intent; HaaS owns proxying, secret resolution, and adapter-specific config rendering.
- Manager MUST not direct Codex to provider endpoints or MCP servers outside HaaS.
- Any sidecar capability response is untrusted input and MUST be schema-validated by manager before use.
- A HaaS-bound session MUST NOT silently fall back to local manager execution after sidecar failure.

## 9. Observability

Manager emits safe events/logs:

- `manager.haas.backend_selected`
- `manager.haas.local_sidecar_starting`
- `manager.haas.local_sidecar_ready`
- `manager.haas.local_sidecar_degraded`
- `manager.haas.remote_probe_failed`
- `manager.haas.binding_created`
- `manager.haas.binding_reused`
- `manager.haas.materialization_requested`

HaaS emits the existing sidecar, session, event, policy, MCP, skill, model proxy, and adapter events. Trace ids should flow from manager to HaaS through `X-HaaS-Trace-ID` when available.

GUI status surfaces:

- Selected backend mode (`local_managed` or `remote`).
- Sidecar URL with token redacted.
- Control readiness.
- Execution readiness.
- Capability summary.
- Current session binding, including delegated session id and workspace mode.
- MCP/skill materialization version or safe degraded reason.

## 10. Failure and Recovery

| Scenario | Behavior |
|----------|----------|
| Local sidecar process cannot start | Manager reports `local_sidecar_start_failed`; HaaS-bound turns fail closed. |
| Local sidecar health ok but execution not ready | GUI shows degraded execution; new HaaS-bound turns fail or queue according to HaaS readiness policy. |
| Remote sidecar unreachable | Manager reports safe remote probe failure; existing HaaS-bound sessions do not fall back locally. |
| Remote capability lacks local bind mount | Manager rejects local project delegation before session creation unless snapshot/remote workspace mode is configured. |
| HaaS token missing or invalid | HaaS returns 401; manager prompts for sidecar credential update without exposing the token. |
| Materialization validation fails | HaaS rejects with structured error; manager records safe reason and does not start the turn. |
| HaaS `/run_sse` disconnects mid-turn | Manager reconnects/replays when possible; otherwise reads back the HaaS session and surfaces a safe recoverability state. |
| GUI WebSocket disconnects mid-turn | Manager continues consuming HaaS SSE and persists transcript state; reconnecting GUI receives the current state without duplicating deltas. |
| Duplicate HaaS replay event | Manager drops duplicates by HaaS event id / dedupe key before emitting GUI-visible deltas. |
| HaaS emits malformed SSE data | Manager closes the bridge, emits a safe `error`, and does not mark the turn successful. |
| MCP optional source unavailable | HaaS may degrade if the MCP source is optional and records the degraded capability event. |
| MCP required source unavailable | HaaS fails session or turn preparation with `haas_mcp_unavailable`. |
| Skill materialization partial write | HaaS rolls back partial materialization and fails closed. |
| User changes backend settings mid-session | Existing bound sessions continue using their binding; new sessions use the new backend. |
| User requests rebind | Manager creates an explicit audit event and new HaaS binding; no silent migration. |

## 11. Test Plan and Acceptance Criteria

- Unit: parse and validate manager HaaS backend config, including local/remote mode, token ref, loopback autostart rule, TLS verification, and endpoint fingerprinting.
- Unit: `HaasClient` maps local and remote base URLs to identical request/response handling.
- Unit: local managed supervisor generates no plaintext secret in config or process env and reports safe status values.
- Unit: sidecar capability discovery is schema-validated and rejects unknown unsafe workspace modes.
- Unit: chat routing policy prioritizes existing binding, backend mode, persona allowlist, workspace trust, sidecar capabilities, content shape, trigger keywords, and explicit user overrides in that order.
- Unit: streaming bridge maps ADK text, terminal, approval, and error events to manager WebSocket events with stable dedupe keys.
- Unit: SSE heartbeat/comment frames do not create GUI transcript items.
- Integration: local managed sidecar starts, passes `/v1/haas/health`, `/v1/haas/ready?scope=control`, and `/v1/haas/ready?scope=execution`.
- Integration: remote fake sidecar and local managed sidecar both pass the same manager backend contract tests.
- Integration: first delegated turn creates a HaaS binding; follow-up turns reuse it even after backend settings change.
- Integration: manager-effective MCP config is submitted to HaaS and materialized through HaaS MCP runtime, not directly to Codex.
- Integration: manager-effective skills are snapshotted through HaaS skill materialization, including bundled resources.
- Integration: provider config routes through HaaS model proxy and raw provider keys are not visible to Codex, sidecar logs, manager logs, events, or bindings.
- Integration: local managed and remote fake sidecars replay the same canned HaaS SSE stream and produce byte-equivalent manager WebSocket event sequences after normalization.
- E2E: GUI can switch between local managed and remote sidecar modes; both use the same HaaS `/run_sse` path.
- E2E: switching backend mode while viewing a HaaS-bound chat preserves the existing binding and shows that new settings apply only to new sessions.
- E2E: first-turn filter miss runs locally without creating a HaaS binding; first-turn filter hit creates a HaaS binding; follow-up turns reuse it regardless of trigger keyword match.
- E2E: a remote sidecar without `bind_mount` support rejects local project delegation with a safe reason.
- Security: secret scan, log redaction assertions, and no raw Authorization/provider/MCP values in persisted bindings.
- UI: streamed HaaS text uses the same transcript streaming component and stream gate as local manager execution.
- Compatibility: ADK `/run` and `/run_sse` parity remains unchanged; no `/v1/codex-worker/*` shim is introduced.
