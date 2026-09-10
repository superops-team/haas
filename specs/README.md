# HaaS Component Specification Overview

**English** | [简体中文](README.zh-CN.md)

Status: Draft
Last reviewed: 2026-09-02
Change ID: haas-platform-foundation

`specs/` is the entry point for HaaS long-term technical specifications. It defines the Harness As A Service protocol, component boundaries, state machines, security, and container runtime as implementable, testable, and reviewable engineering contracts.

This directory MUST be self-contained: component specs MAY reference peer component documents under `specs/`, but understanding the design MUST NOT depend on paths outside the repository or temporary reports.

## 1. System Role

HaaS is a runtime-hosting sidecar for multiple harnesses. Its northbound protocol follows the Google ADK 2.0 REST API protocol layer:

```text
Client / Manager / SDK / CLI / ADK web UI
  |
  |  ADK 2.0 HTTP + SSE
  v
HaaS Sidecar API
  |
  +-- Protocol Mapper (ADK <-> internal)
  +-- Harness Registry
  +-- Session Runtime
  +-- Admission Control
  +-- Event Log & SSE Replay
  +-- Policy Controller
  +-- Manager Delegation
  +-- Model Proxy
  +-- MCP / Tool / Skill Runtime
  +-- Artifact Store
  +-- Security Boundary
  +-- Observability
  |
  v
Harness Adapter Interface
  |
  +-- Codex app-server adapter  (P0)
  +-- Pi adapter                (future)
  +-- OpenCode adapter          (future)
  +-- AMP adapter               (future)
  +-- Other harness adapters    (future)
  |
  v
Sandbox Runtime (OpenSandbox sandbox/execd/credential vault projection)
  |
  v
OpenSandbox AIO container runtime
```

HaaS is neither a model proxy itself nor a single Codex Worker. The model proxy is an internal HaaS capability for secretless operation and compatibility; Codex is the initial harness adapter; and Sandbox Runtime is the substrate for standardizing multi-harness runtime environments.

### 1.1 Research Baseline

This design uses the following external facts as inputs; they are not in-repository file dependencies:

| Source | Revision / version | Key findings |
|------|--------------------|----------|
| ADK 2.0 docs | fetched 2026-08-26 | REST API protocol layer: `/list-apps`, `/run`, `/run_sse`, `/apps/{app}/users/{user}/sessions/{sid}`, camelCase, `newMessage{role,parts}`, Event with `nodeInfo`/`output`, and SSE `data:` frames |
| Local `mpa-codex-worker` reference | local checkout on 2026-08-26 | The predecessor project has validated the sidecar API, Codex app-server, event log, SSE replay, session registry, model proxy, MCP proxy, secretless runtime, and containerization boundaries |
| Codex manual | fetched 2026-08-26 | `codex app-server` supports `stdio://`, `ws://IP:PORT`, and `unix://`; a connection MUST send `initialize` before `initialized` |
| Local Codex CLI | `codex-cli 0.149.1` | `codex app-server --help` exposes `--listen`, `--ws-auth`, `generate-ts`, and `generate-json-schema` |
| OpenSandbox | commit `cfca10537a0af7afd11e67b8574e55b2bb2603ad` | Sandbox lifecycle, execd, ingress, egress, credential vault, SDK/CLI/MCP |
| AIO Sandbox | commit `89186e8c9fb3f78b2f67cc472a36c1cb63e25ccb` | AIO image `ghcr.io/agent-infra/sandbox`, port `8080`, entrypoint `/opt/gem/run.sh`, including browser, shell, file, VSCode, Jupyter, and MCP |

## 2. Documentation Layers

| Document layer | Location | Lifecycle | Responsibility |
|--------|------|----------|------|
| Component spec | `specs/<component>/README.md` | Long-term maintenance | Establish component responsibility, interface, state-machine, security, recovery, and test contracts |
| Protocol schema | `specs/haas-protocol/*.openapi.yaml` | Tracks protocol versions | Define HTTP schemas from which types can be generated |
| Implementation code | `haas/`, `docker/`, `scripts/` | Versioned evolution | Implement the contracts in the specs |
| Temporary evidence | Not committed | One release gate | Record commands, results, and risks in the final delivery notes or PR/MR description |

## 3. Architecture Baseline

### 3.1 Northbound Protocol

HaaS exposes three tiers of HTTP/SSE surfaces upstream:

| Surface | Path | Compatibility level | Purpose |
|---------|------|----------|------|
| ADK-compatible | `/list-apps`, `/run`, `/run_sse`, `/apps/{app}/users/{user}/sessions/{sid}` | Public API | Long-term primary protocol, drop-in compatible with ADK 2.0 clients |
| HaaS native | `/v1/haas/*` | Public extension | ADK-uncovered capabilities such as health/ready/status, diagnostics, harness CRUD, session/event management, and artifacts |
| ~~Legacy sidecar shim~~ | ~~`/v1/codex-worker/*`~~ | **Not implemented by this project** | See §3.1.1 |

Rules:

1. The ADK-compatible surface MUST NOT expose concrete harness-native fields. HaaS extensions belong only in the nested `haas` object or under `/v1/haas/*`.
2. HaaS native extensions MUST be additive only and MUST NOT change ADK field semantics.
3. All public surfaces use a `detail` + structured `haasError` error shape.

### 3.1.1 Scope Decision: Do Not Implement the `mpa-codex-worker` Migration Shim

**Decision (2026-08-30)**: HaaS and `mpa-codex-worker` are only architecturally isomorphic; HaaS does not assume migration responsibility for it. The `/v1/codex-worker/*` shim is **out of scope for this project**: it will not be implemented, tested, or required by release gates. If migration of legacy upstream consumers becomes necessary, it will be handled as a **separate initiative**.

Implications and handling:

- Residual legacy/shim descriptions in component specs are **historical context and future options**, not pending tasks. They MUST NOT be used as grounds to add `/v1/codex-worker/*` routes during implementation.
- `mpa-codex-worker` MAY still serve as a **design reference** because it has validated the sidecar API, event log, SSE replay, model proxy, secretless boundaries, and other patterns. This does not conflict with the decision not to implement the shim.
- The `haas_legacy_request_invalid` error code and legacy entries in OpenAPI are retained to avoid changing a published compatibility surface; they are **unused** in this project.
- All new capabilities MUST be defined on either the ADK surface or the HaaS native surface.

### 3.2 Runtime Boundary

HaaS runs on the OpenSandbox AIO base image. AIO provides foundational capabilities such as shell, file, browser, exec, sandbox lifecycle, and credential vault; HaaS adds the sidecar, adapters, proxies, event log, policy, and Sandbox Runtime.

Default container ports:

| Port | Owner | Purpose |
|------|------|------|
| `8080` | OpenSandbox AIO | AIO service / shell / browser / file / sandbox API |
| `8092` | HaaS sidecar | HaaS ADK/HTTP/SSE API |
| `18080` | HaaS model proxy | Harness-facing model proxy loopback |
| `18081` | HaaS MCP/tool proxy | Harness-facing MCP/tool proxy loopback |

### 3.3 Adapter Boundary

Each harness adapter is the sole owner of its native runtime:

- The Codex adapter owns Codex app-server JSON-RPC/WebSocket/stdio details.
- A future Pi adapter will own Pi CLI JSON stream, session-id, and configuration details.
- A future OpenCode adapter will own OpenCode JSON stream, permission configuration, and MCP configuration details.
- A future AMP adapter will own AMP native protocol, session, and tool-permission details.

Upstream consumers MAY see only ADK `Event`/`Session` objects, errors, and artifacts; they MUST NOT see any native runtime details.

### 3.4 Sandbox Standardization Boundary

Sandbox Runtime uniformly projects the Policy Controller's workspace/network/tool policy and the harness adapter's sandbox declaration into OpenSandbox sandbox/execd configuration. A harness-provided sandbox (such as the Codex sandbox) MUST run within it; provider credentials flow through the credential vault; and network egress is constrained by both the OpenSandbox egress policy and the HaaS URL validator. See [Sandbox Runtime](sandbox-runtime/README.md).

## 4. Component Plan

| Priority | Component | Path | Primary responsibility |
|--------|------|------|----------|
| P0 | Architecture | `specs/architecture/README.md` | System-level layering, fact ownership, dependency direction, and initial implementation order |
| P0 | HaaS Protocol | `specs/haas-protocol/README.md` | ADK-compatible API, HaaS native API, errors, and versioning strategy |
| P0 | Harness Registry | `specs/harness-registry/README.md` | Configured harness catalog, appName resolution, and base/capability/model/provider discovery |
| P0 | Harness Adapter | `specs/harness-adapter/README.md` | Multi-harness adapter abstraction, capability matrix, and ADK event normalization |
| P0 | Codex App-Server Adapter | `specs/codex-app-server-adapter/README.md` | Initial Codex app-server connection, thread/turn, JSON-RPC, cancel, and schema pin |
| P0 | Session Runtime | `specs/session-runtime/README.md` | Session/invocation/turn/container lifecycle, idempotency, leases, and continuation |
| P0 | Admission Control | `specs/admission-control/README.md` | Quota, rate limiting, concurrency, and queue admission |
| P0 | Event Log & SSE | `specs/event-log-sse/README.md` | Event log, SSE live/replay, and ADK Event projection |
| P0 | Security Boundary | `specs/security-boundary/README.md` | Secretless operation, object scope, SSRF, artifact paths, redaction, and audit |
| P0 | Policy Controller | `specs/policy-controller/README.md` | Workspace, network, tool, approval, and model policy compilation and admission |
| P0 | Manager Delegation | `specs/manager-delegation/README.md` | Manager-facing delegated-session binding, mount manifest, restore, workspace single-writer policy, approval relay, and provider delegation contract |
| P0 | Manager HaaS Sidecar Backend | `specs/manager-haas-sidecar-backend/README.md` | OpenHarness local-managed and remote HaaS sidecar backend selection, unified HaaS client protocol, session binding, and MCP/skill/model materialization handoff |
| P0 | Manager Product Identity | `specs/manager-product-identity/README.md` | OpenHarness product identity, no-login desktop behavior, and local-only account/connector boundaries |
| P0 | Stores | `specs/stores/README.md` | Persistent source of truth: registry/session/event/idempotency/admission interfaces, schemas, and migrations |
| P0 | Identity | `specs/identity/README.md` | Bearer -> principal, tenant/workspace/userId scope, and `IdentityProvider` interface |
| P0 | Config | `specs/config/README.md` | Env/config assembly, port table, and `load_config`/`create_app` contracts |
| P1 | Sandbox Runtime | `specs/sandbox-runtime/README.md` | Uniformly project harness sandboxes into OpenSandbox sandbox/execd/credential vault |
| P1 | Model Proxy | `specs/model-proxy/README.md` | Provider credential isolation, OpenAI-compatible relay, and usage normalization |
| P1 | MCP / Tool / Skill Runtime | `specs/mcp-tool-skill-runtime/README.md` | MCP servers, MCP proxy, tools, skill materialization, and tool restrictions |
| P1 | Artifact Store | `specs/artifact-store/README.md` | Input files, session outputs, downloads, archives, and path security |
| P1 | Container Runtime | `specs/container-runtime/README.md` | OpenSandbox AIO Dockerfile, entrypoint, ports, health/ready, and shutdown |
| P1 | Runtime Trim | `specs/runtime-trim/README.md` | Use official AIO `DISABLE_*`/`NODE_VERSION` controls to disable unneeded AIO services and reduce runtime resource usage |
| P1 | Startup | `specs/startup/README.md` | nginx front door, sidecar readiness, Codex Unix socket handshake probe, startup DAG, asynchronous warmup, and latency budget |
| P1 | Observability | `specs/observability/README.md` | Logs, metrics, traces, diagnostics, and conformance evidence |
| P1 | Implementation Roadmap | `specs/implementation-roadmap/README.md` | Subsequent implementation stages, dependencies, exit evidence, and risk convergence |

## 5. Standard Structure for a Component Spec

Every component spec MUST contain the following sections. Sections MAY be concise but MUST NOT be omitted. When no conclusion is available, the spec MUST state `Unknown`, the impact, and the next verification step.

1. `Component Role`
2. `Sources and Rationale`
3. `Upstream and Downstream Relationships`
4. `Responsibility Boundaries`
5. `Core Interfaces`
6. `Data Model`
7. `Runtime Model and State Machine`
8. `Security and Permissions`
9. `Observability`
10. `Failure and Recovery`
11. `Test Plan and Acceptance`

## 6. Global Object Model

| Object | `object` value | Id | Authority | Lifecycle |
|--------|----------------|-----|-----------|----------|
| Harness | `harness` | `chrn_...` (the ADK `appName`) | Harness Registry | Creation through deletion |
| Invocation | `invocation` | `inv_...` | Session Runtime | One `/run`; readable during the retention period |
| Session | `session` | Caller-supplied `sessionId` (default `hsess_...`) | Session Runtime | `(appName, userId, sessionId)` tuple |
| Turn | `turn` | `turn_...` | Session Runtime | One harness execution turn |
| Container | `container` | `cntr_...` | Container Runtime | Follows the session |
| File | `file` | `file_...` | Artifact Store | Follows the container/session |
| Event | none | invocation-scoped | Event Log | Replayable during the retention period |

An `invocation` is the public run unit; a `turn` is the internal execution unit. They are 1:1 in the initial release, but the protocol does not assume that they will always remain so.

## 7. Global HTTP Conventions

### 7.1 Headers

| Header | Required | Description |
|--------|------|------|
| `Authorization: Bearer <token>` | Required except for health/ready probes | HaaS caller token |
| `Idempotency-Key` | Optional (server supports deduplication) | Idempotency for mutating APIs; a repeated key returns the first result and does not restart the harness |
| `Last-Event-ID` | Optional | Reconnection replay cursor for `/run_sse` and HaaS native streams |
| `X-HaaS-Tenant-ID` | Conditional | Required for multi-tenant deployments or derived from the token |
| `X-HaaS-Workspace-ID` | Conditional | Workspace scope, derived from the token or header |
| `X-HaaS-Trace-ID` | Optional | End-to-end trace id; generated by the server if absent |

### 7.2 ADK Field Naming

Requests and responses on ADK-compatible paths consistently use `camelCase` (`appName`, `userId`, `sessionId`, `newMessage`, `invocationId`, `lastUpdateTime`). HaaS native paths use the HaaS envelope.

### 7.3 HaaS Envelope

HaaS native endpoints use:

```json
{
  "data": {},
  "traceId": "tr_abc"
}
```

Pagination:

```json
{
  "data": [],
  "nextCursor": null,
  "traceId": "tr_abc"
}
```

Single resources (Harness, File, Invocation, and others), lists, and diagnostic responses are consistently wrapped in `data`; only paginated lists additionally carry `nextCursor`. Health/ready/status/diagnostics also return this envelope.

Errors (common to all public surfaces):

```json
{
  "detail": "requested operation is not allowed",
  "haasError": {
    "type": "invalid_request_error",
    "code": "haas_policy_denied",
    "param": null,
    "safeReason": "tool_not_allowed",
    "retryable": false,
    "traceId": "tr_abc"
  }
}
```

Stable HaaS error codes use the `haas_` prefix or ADK semantic codes (such as `session_busy` and `app_not_found`). See [ERROR-CODES](haas-protocol/ERROR-CODES.md) for the sole error-code catalog; OpenAPI `haasError.code` values map to it one-to-one.

### 7.4 Timestamp Conventions

| Surface | Format | Description |
|------|------|------|
| ADK-compatible public fields | Float seconds since epoch (`1743712220.385936`) | `Event.timestamp`, `Session.lastUpdateTime`; this is an ADK contract and MUST NOT change |
| HaaS internal records + HaaS native API | Integer milliseconds since epoch (`1786400000000`) | `createdAtMs`/`updatedAtMs`/`observedAtMs`/`expiresAtMs` (see [Stores](stores/README.md)) |

The projection layer is responsible for converting `ms -> float seconds` (`ms / 1000.0`). No component may expose timestamps that mix the two formats on a public surface.

### 7.5 ID Conventions

| Object | Prefix | Generator |
|------|------|--------|
| harness / ADK app | `chrn_` | Harness Registry |
| invocation | `inv_` | Session Runtime |
| turn | `turn_` | Session Runtime |
| container | `cntr_` | Container Runtime |
| file | `file_` | Artifact Store |
| event | `evt_` | Event Log |
| session | Caller-supplied, default `hsess_` | Client or Session Runtime |

`invocationId` and `turnId` are 1:1 in the initial release but are not equal. Their mapping is persisted in `InvocationRecord.turnId`.

## 8. Global Event Conventions

Public events are ADK `Event` objects. A canonical event MUST support lossless projection to an ADK `Event`:

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

Rules:

- `/run_sse` events are flushed in production order. With `streaming:true`, `text` parts appear incrementally.
- The stream closes when the invocation completes; `/run` returns the event array in one response.
- Heartbeats use the SSE comment `: keep-alive` and do not produce events.
- Internal HaaS canonical events MAY carry `sequenceNumber`/`eventId` for replay, but projection to an ADK `Event` emits only ADK fields (a HaaS native stream MAY additionally emit `haas` metadata).
- Non-streaming `/run` output MUST equal the aggregated streaming output of `/run_sse` (parity).

## 9. Compatibility and Versioning Strategy

1. The first HaaS protocol version is `2026-08-26`; its northbound protocol layer aligns with the ADK 2.0 REST API.
2. HaaS native responses MUST return `HaaS-Version: 2026-08-26`.
3. Within the same version, changes MAY only add optional fields, event part types, or `haas_`-prefixed error codes.
4. Removing, renaming, or changing semantics, adding required fields, or tightening constraints MUST require a new version.

## 10. Inter-Component Dependency Direction

```text
api -> protocol schemas
api -> identity / registry / session runtime / admission control / event log / observability
api -> config (create_app)
identity -> security-boundary
session runtime -> harness adapter interface
session runtime / registry / event log / admission control -> stores
admission control -> session runtime / registry / observability
harness adapter -> sandbox runtime / model proxy / mcp-tool-skill runtime / container runtime
sandbox runtime -> OpenSandbox sandbox/execd/credential vault
policy controller -> security-boundary
codex adapter -> Codex app-server native protocol only
model proxy -> provider clients
mcp-tool-skill runtime -> MCP servers / MCP proxy / skill stores
artifact store -> container/session workspace
observability <- all components through logging/metrics interfaces
security-boundary <- all public and adapter boundaries
```

Reverse dependencies are prohibited: adapters MUST NOT import API routes; the model proxy MUST NOT import concrete adapters; and observability MUST NOT change business state.

## 11. Testing and Release Gates

Documentation initialization stage:

- `git diff --check`
- Markdown structure/self-review

Implementation stage:

- ADK compatibility: validate `/list-apps`, `/run`, `/run_sse`, and session paths with the official ADK client.
- API/schema: FastAPI ASGI integration tests.
- SSE: progressive flush, stream-closure semantics, heartbeat, disconnect/replay, and parity.
- Codex app-server: real handshake, thread/start, turn/start, cancel, and schema generation/probe.
- Sandbox: verify OpenSandbox sandbox/execd/credential vault projection.
- Container: OpenSandbox AIO build, ports, health/ready, and SIGTERM drain.
- Runtime trim: `DISABLE_*` prevents code-server/jupyter from starting without regressing browser/VNC/sandbox or Codex readiness (container smoke).
- Security: secret scan, inverse redaction assertions, SSRF allowlist, and artifact traversal probes.

## 12. Maintenance Rules

1. Before adding a component spec, update the component plan table in this README.
2. Changes to the public protocol MUST update schemas, tests, and compatibility notes in the same change.
3. A new harness adapter MAY integrate only through the interface defined by `harness-adapter`.
4. Upgrading the Codex app-server version MUST revalidate the local `codex app-server --help` output and generated schema.
5. Upgrading the OpenSandbox AIO base image MUST update its digest, source, and Docker smoke evidence.
