# Architecture Component Specification

**English** | [简体中文](README.zh-CN.md)

Status: Draft
Last reviewed: 2026-09-10

## 1. Component Role

The Architecture specification defines HaaS system boundaries, dependency direction, ownership of authoritative facts, and the initial implementation sequence. It is the overarching design basis for all other component specifications under `specs/`.

HaaS uses a sidecar-first architecture with Python + FastAPI as its runtime stack:

```text
Client / Manager / SDK / CLI / ADK web UI
  |
  | ADK 2.0 HTTP JSON + SSE
  v
HaaS Sidecar API (FastAPI)
  |
  +-- Protocol Mapper (ADK <-> internal)
  +-- Harness Registry
  +-- Harness Profile
  +-- Session Runtime
  +-- Admission Control
  +-- Event Log & SSE Replay
  +-- Policy Controller
  +-- Manager Delegation
  +-- Artifact Store
  +-- Security Boundary
  +-- Observability
  |
  +-- Model Proxy ---------> Model providers
  +-- MCP / Tool / Skill Runtime --> MCP servers / local tools / skills
  |
  v
Harness Adapter Interface
  |
  +-- Codex app-server adapter  (first implementation)
  +-- Pi adapter                (future)
  +-- OpenCode adapter          (future)
  +-- AMP adapter               (future)
  |
  v
Sandbox Runtime (common policy contract: Lite Docker or OpenSandbox AIO)
  |
  v
OpenSandbox AIO container runtime
```

Core layers:

1. Upstream consumers depend only on the ADK 2.0 **REST API protocol layer**, not on any harness-native protocol. They also do not depend on the ADK execution engine (`BaseAgent`/WorkflowGraph), graph workflows, or ADK Web UI.
2. A `harness adapter` represents a complete agent runtime, not an LLM provider.
3. A `configured harness` is the unit of execution capability; `appName` is the configured harness `id`.
4. `Sandbox Runtime` provides one policy/isolation contract implemented by Lite Docker (default) and optional OpenSandbox AIO. AIO-specific nodes below apply to AIO only; Lite uses the private worker/broker network and persistent session volume defined by Container Runtime. The host control sidecar owns public execution facts.
5. `Admission Control` manages service-level quota, rate limiting, concurrency, and queue admission.
6. `Manager Delegation` defines how an upstream manager binds sessions, policies, mount manifests, approvals, and container restore to HaaS as a full execution backend.
7. `Stores` is the sole persistent source of truth; `Identity` is the authentication boundary; and `Config` is the assembly contract (see their respective specs).

See [WALKTHROUGH](WALKTHROUGH.md) for the end-to-end request sequence.
See [README Visual Storytelling](VISUAL-STORYTELLING.md) for the bilingual
Guided Trace GIF contract used by the repository landing page.
See [Brand and Repository Metrics](BRAND-AND-REPOSITORY-METRICS.md) for the
logo, README masthead, metric definitions, and badge publication contract.

## 2. Sources and Rationale

| Source | Adopted content |
|------|----------|
| ADK 2.0 REST API | `/list-apps`, `/run`, `/run_sse`, session paths, Event schema, SSE framing |
| `mpa-codex-worker` experience | Sidecar API, Codex app-server, event log, SSE replay, session registry, secretless runtime |
| Current Codex app-server behavior | `initialize`, `initialized`, `thread/start`, `thread/resume`, `turn/start`, `turn/interrupt` |
| OpenSandbox AIO | AIO image, port `8080`, `/opt/gem/run.sh`, sandbox lifecycle, execd, credential vault, egress policy |

## 3. Upstream and Downstream Relationships

| Direction | Object | Relationship |
|------|------|------|
| Upstream | Client / Manager / SDK / CLI | Depends only on ADK 2.0 HTTP/SSE |
| Downstream | HaaS Protocol | Defines the public contract |
| Downstream | Harness Registry | Manages configured harnesses (ADK apps) |
| Downstream | Harness Profile | Manages versioned provider/MCP/skills/AGENTS.md/workspace profile revisions |
| Downstream | Session Runtime | Manages session/invocation/turn |
| Downstream | Admission Control | Quota, rate limiting, concurrency, and queue admission |
| Downstream | Manager Delegation | Manager-facing delegated session binding, restore, approval relay, and workspace single-writer contract |
| Downstream | Harness Adapter | Isolates concrete harnesses |
| Downstream | Sandbox Runtime | Projects one policy contract into Lite Docker or OpenSandbox AIO |
| Downstream | Container Runtime | Owns the OpenSandbox AIO image and process topology |
| Downstream | Security Boundary | Constrains all data crossing boundaries |

## 4. Responsibility Boundaries

Responsibilities:

- Define system-level component boundaries and dependency direction.
- Define the relationship between the primary ADK protocol and HaaS native extensions.
- Define the initial P0/P1/P2 implementation order.
- Identify the authoritative owner of each fact.
- Define negative paths, recovery strategies, and verification gates.

Non-responsibilities:

- Does not replace individual component specs.
- Does not describe runtime code implementation details.
- Does not retain temporary research or verification logs.

## 5. Core Interfaces

This component exposes no runtime API. It defines the public architectural entry points:

| Surface | Path | Owner |
|---------|------|-------|
| ADK-compatible API | `/list-apps`, `/run`, `/run_sse`, `/apps/{app}/users/{user}/sessions/{sid}` | HaaS Protocol |
| HaaS native API | `/v1/haas/*` | HaaS Protocol |
| Adapter interface | internal Python async interface | Harness Adapter |
| Sandbox projection | internal SandboxRuntime API | Sandbox Runtime |
| Container entrypoint | `/opt/haas/run.sh` | Container Runtime |

## 6. Data Model

Key objects:

| Object | Public? | Authority | Notes |
|--------|---------|-----------|-------|
| Harness | yes | Harness Registry | configured harness; `id`=ADK `appName`; `base` is an open string |
| HarnessProfile | yes on HaaS native API | Harness Profile / Harness Registry | versioned execution configuration frozen into sessions as `EffectiveHarnessProfile` |
| Run/Invocation | yes | Session Runtime | One ADK `/run`/`/run_sse`; `invocationId` |
| Session | yes | Session Runtime | Unique `(appName, userId, sessionId)` tuple |
| Turn | internal | Session Runtime | Adapter execution unit; 1:1 with an invocation in the initial release |
| Event | yes | Event Log & SSE | Internal stable `CanonicalEventRecord`; ADK projection for ADK surfaces; typed `CanonicalHaasEvent` projection for session/invocation native streams |
| DelegatedSession | yes on HaaS native API | Manager Delegation / Session Runtime | Manager-to-HaaS binding, policy snapshot, mount manifest, and runtime restore contract |
| File | yes | Artifact Store | input files and produced artifacts |
| Policy | internal/public summary | Policy Controller | Effective runtime constraints |
| RuntimeToken | internal | Security Boundary / Model Proxy | short TTL scoped token |

## 7. Runtime Model and State Machine

```text
request received
  -> protocol/auth/scope validation (Identity -> Principal)
  -> appName resolution (harness id/name)
  -> admission control (quota/rate/queue)
  -> optional manager delegation binding/restore validation
  -> session/run admission (Idempotency + lease)
  -> policy compilation
  -> sandbox projection (workspace/network/tool -> OpenSandbox)
  -> adapter execution
  -> stable canonical event append + ADK/native projection
  -> invocation finalization
  -> artifact publication
```

See [WALKTHROUGH](WALKTHROUGH.md) for the complete object-level sequence.

**Deployment topology**: The initial release uses a single-process sidecar (one worker). The persistent Stores backend is the source of truth, while in-memory state is a secondary cache. Deployment-wide shared admission state is implemented through the Stores backend. Active-turn mutual exclusion is enforced through a `SessionStore` lease. Multi-replica deployment is reserved for the future and is not implemented in the initial release.

Readiness model:

```text
process alive -> health ok
control plane initialized -> control ready
selected adapter ready + sandbox runtime ready -> execution ready
optional MCP/skill/browser warmup -> capability ready
```

## 8. Security and Permissions

- Public API is authenticated except discovery/health/readiness probes.
- Object scope is enforced on every read, write, cancel, delete, and artifact access; both `userId` and `sessionId` are isolated by principal scope.
- Secretless applies before adapter execution; AIO uses its vault and Lite uses the trusted external broker memory. Harness processes receive only short-lived scoped tokens.
- Adapter native protocol data is never public by default.
- Sandbox isolation is a hard runtime boundary. A harness sandbox provided by an adapter MUST run within it and does not replace the HaaS boundary.

## 9. Observability

System status must be able to answer:

- Which ADK protocol version and HaaS native version are served.
- Which harness bases/apps are installed and ready.
- Which adapters are degraded and why.
- How many sessions, invocations and turns are active.
- Whether event log, model proxy, MCP proxy, sandbox runtime and AIO service are healthy.
- Which conformance/test gates were last run.

## 10. Failure and Recovery

| Failure | Recovery |
|---------|----------|
| Public protocol incompatible | reject by schema/header, do not best-effort parse |
| Adapter unavailable | mark selected harness unavailable and fail new runs |
| Accepted run loses stream client | continue server-side and persist terminal state |
| Session runtime loses process | recover from store or mark non-resumable |
| Sandbox instance fails | recreate from session snapshot or fail closed |
| Container receives SIGTERM | drain, flush, settle active work, exit |
| Secret leakage detected | fail closed and block release |

## 11. Test Plan and Acceptance

- Architecture review confirms every component has one owner and no reverse dependency.
- Protocol tests cover ADK-compatible API before HaaS native expansion.
- Adapter contract tests run against fake adapter and Codex app-server adapter.
- Sandbox tests verify common isolation for Lite and AIO; AIO additionally verifies sandbox/execd/vault projection.
- Admission tests verify quota/rate/queue boundaries.
- Security tests cover every public surface and adapter env/config/log output.
