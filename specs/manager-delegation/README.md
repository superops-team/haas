# Manager Delegation Component Specification

**English** | [简体中文](README.zh-CN.md)

Status: Draft
Last reviewed: 2026-09-08
Change ID: manager-haas-delegation
Related specs: [HaaS Protocol](../haas-protocol/README.md), [Session Runtime](../session-runtime/README.md), [Sandbox Runtime](../sandbox-runtime/README.md), [Policy Controller](../policy-controller/README.md), [Model Proxy](../model-proxy/README.md), [Container Runtime](../container-runtime/README.md), [Stores](../stores/README.md)

## 1. Component Role

Manager Delegation defines the contract for using HaaS as a complete remote execution backend for a manager application. The manager remains responsible for user-facing intent routing, project authorization, configuration selection, approval UI, and session ownership. HaaS remains responsible for executing accepted delegated work through its sidecar, Session Runtime, Codex app-server adapter, Sandbox Runtime, event stream, model proxy, and container lifecycle.

HaaS MUST NOT be integrated as a shell `Executor` inside the manager. It is a full execution backend with its own sessions, turns, events, cancellation, approval relay, recovery, and sandbox boundaries.

Initial delegation target:

```text
Manager session / turn
  -> deterministic delegation router
  -> HaaS delegated session binding
  -> one HaaS container per delegated session
  -> Codex app-server adapter inside the HaaS runtime
  -> project mounted at /workspace
```

## 2. Sources and Rationale

| Source | Adopted content |
|--------|-----------------|
| Manager code analysis | Current manager creates a local `TurnEngine` directly and owns provider setup, workspace roots, session APIs, WebSocket streaming, approvals, and local permission checks. |
| Confirmed product decisions (2026-09-08) | One container per delegated session; follow-up turns reuse the container/thread; idle TTL destroys runtime resources only; later follow-up restores previous permissions/config/mounts; first successful delegation fixes the session binding; authorized project root is mounted `rw` at `/workspace`; same canonical workspace allows only one active `rw` delegated session. |
| HaaS Architecture | HaaS exposes ADK-compatible HTTP/SSE plus HaaS native control-plane APIs; Codex is the first P0 harness runtime. |
| Security Boundary / Policy Controller | Fail closed, secretless credentials, canonical path validation, explicit authorization for widening. |

## 3. Upstream and Downstream Relationships

| Direction | Component | Relationship |
|-----------|-----------|--------------|
| Upstream | Manager | Owns intent detection, user authorization, UI, approvals, and delegated-session binding. |
| Downstream | HaaS Protocol | Provides manager-facing control-plane APIs and ADK `/run_sse` execution. |
| Downstream | Session Runtime | Owns HaaS sessions, invocations, Codex thread references, idempotency, and terminal state. |
| Downstream | Policy Controller | Validates the delegated policy snapshot and rejects unauthorized widening. |
| Downstream | Sandbox Runtime | Materializes manager-approved mounts and policy into an execution sandbox. |
| Downstream | Model Proxy | Provides secretless model access from the HaaS container using manager-supplied `credentialRef`. |
| Downstream | Event Log & SSE | Streams execution, queue, approval, recovery, and terminal events. |
| Downstream | Container Runtime | Starts and stops the HaaS sidecar/container image under the required `linux/amd64` platform contract. |

## 4. Responsibility Boundaries

Manager responsibilities:

- Determine whether a turn should use local execution or HaaS delegation.
- Use deterministic rules first; after the first successful delegation, bind the manager session to HaaS and stop reclassifying follow-up turns.
- Persist the delegated-session contract: manager session id, HaaS session id, harness id, image digest, model/provider credential reference, mount manifest, delegation policy snapshot, and audit events.
- Canonicalize and authorize host paths before sending a mount manifest to HaaS.
- Preserve the user's explicit authorization for follow-up recovery, while revalidating path existence, canonical path, type, and symlink safety before each restore.
- Act as the only user approval entry point.
- Enforce workspace-level single-writer admission for `rw` delegated sessions.

HaaS responsibilities:

- Accept only manager-approved delegation manifests and policy snapshots.
- Execute delegated work through HaaS sessions, not through manager shell execution.
- Keep Codex native protocol details private to the Codex adapter.
- Preserve session, invocation, event, idempotency, approval, and recovery state in persistent storage.
- Provide live SSE events, replay, cancellation, approval relay, status, and safe errors.
- Fail closed when the delegated environment cannot be safely created or resumed.

Non-responsibilities:

- HaaS does not perform manager-side intent detection.
- HaaS does not persist real provider API keys from manager.
- HaaS does not expand mounts, workspace permissions, network access, tools, or approval grants beyond the manager-approved contract.
- The manager does not parse Codex app-server JSON-RPC or rely on Codex native thread ids as public API.

## 5. Core Interfaces

### 5.1 Manager-Facing HaaS Native APIs

These endpoints are HaaS native extensions and MUST NOT redefine ADK field semantics:

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/v1/haas/delegated-sessions` | Create or bind a HaaS delegated session from a manager contract. |
| GET | `/v1/haas/delegated-sessions/{delegated_session_id}` | Read delegated-session state, policy snapshot, mount manifest, and runtime status. |
| POST | `/v1/haas/delegated-sessions/{delegated_session_id}/restore` | Recreate runtime resources after TTL cleanup or process restart using the persisted contract. |
| POST | `/v1/haas/delegated-sessions/{delegated_session_id}/policy` | Explicitly update or rebind the policy snapshot for future turns. |
| POST | `/v1/haas/sessions/{session_id}/approvals/{approval_id}` | Return a manager approval decision to a waiting invocation/action. |

Execution still uses ADK-compatible `/run_sse` or `/run` with the bound `appName`, `userId`, and `sessionId`. The manager uses HaaS native session/event APIs for replay, status, cancellation, and diagnostics.

### 5.2 Manager Provider Configuration

The manager provider catalog SHOULD distinguish:

| Provider id | Endpoint | Purpose |
|-------------|----------|---------|
| `ark` | `https://ark.ap-southeast.bytepluses.com/api/v3` | BytePlus Ark global data plane. |
| `volcengine-ark` | `https://ark.cn-beijing.volces.com/api/v3` | Volcengine Ark China standard data plane. |
| `ark-agent-plan-cn` | `https://ark.cn-beijing.volces.com/api/plan/v3` | Volcengine Ark Agent Plan API. |

`volcengine-ark` MUST be modeled as a separate provider identity from BytePlus Ark and Ark Agent Plan. Manager local execution and HaaS delegation both reference providers by provider id, model id, and `credentialRef`; neither side copies raw API keys into session contracts.

## 6. Data Model

### 6.1 DelegatedSessionContract

```json
{
  "id": "dgsess_abc",
  "object": "delegated_session",
  "managerSessionId": "mgr_sess_123",
  "haasSessionId": "hsess_abc",
  "haasUserId": "u_123",
  "harnessId": "chrn_codex_default",
  "harnessBase": "codex",
  "binding": "haas_bound",
  "image": {
    "reference": "registry.example.com/haas@sha256:...",
    "digest": "sha256:..."
  },
  "provider": {
    "providerId": "volcengine-ark",
    "model": "doubao-seed-1-6",
    "credentialRef": "secret://manager/provider/volcengine/default"
  },
  "mountManifest": {
    "version": 1,
    "primaryWorkspace": {
      "hostPathCanonical": "/Users/example/workspace/project",
      "containerPath": "/workspace",
      "access": "rw"
    },
    "extraMounts": [
      {
        "hostPathCanonical": "/Users/example/workspace/shared",
        "containerPath": "/mnt/extra/shared",
        "access": "ro"
      }
    ]
  },
  "delegationPolicySnapshot": {
    "version": 1,
    "idleTtlSeconds": 1800,
    "maxContainerLifetimeSeconds": 28800,
    "rwWorkspaceConcurrency": "single_writer",
    "queuePolicy": "fifo",
    "restorePolicy": "fail_closed",
    "mountPolicy": "project_rw_extra_ro"
  },
  "runtime": {
    "status": "idle",
    "containerId": null,
    "containerGeneration": 3,
    "lastStartedAtMs": 1786400000000,
    "lastActiveAtMs": 1786401000000
  },
  "createdAtMs": 1786400000000,
  "updatedAtMs": 1786401000000
}
```

Contract fields are durable. `containerId`, process id, transient port allocation, and in-memory locks are runtime observations and MUST NOT be the only recovery source.

### 6.2 DelegationPolicy

```json
{
  "version": 1,
  "idleTtlSeconds": 1800,
  "maxContainerLifetimeSeconds": 28800,
  "rwWorkspaceConcurrency": "single_writer",
  "queuePolicy": "fifo",
  "restorePolicy": "fail_closed",
  "policyChangeMode": "snapshot_per_session",
  "mountPolicy": "project_rw_extra_ro"
}
```

Configuration layers:

1. Global default policy.
2. Workspace/project override.
3. Delegated session creation-time snapshot.

Existing delegated sessions use their creation-time snapshot by default. Later global or workspace config changes apply only to new sessions. Changing an existing delegated session requires an explicit policy update/rebind operation and an audit event.

Manager-side delegation is controlled by an independent `[haas_delegation]`
configuration namespace. It defaults to disabled and MAY only be enabled from
user-global configuration or another trusted operator-controlled source.
Repository-local workspace configuration MUST NOT enable HaaS delegation, change
the HaaS endpoint, change image/harness defaults, or relax mount policy. A first
successful delegation records a non-secret configuration snapshot in the manager
session binding (`haas_base_url`, HaaS user id, harness id/base, image reference,
image digest, provider id/model, mount manifest, and delegation policy snapshot).
The API token or other credentials MUST NOT be persisted in the binding; runtime
calls resolve those from current user-owned config/secret state.

Manager implementations MAY expose user-facing settings APIs for this namespace.
Those APIs MUST split secret and non-secret material:

- Non-secret fields (`enabled`, endpoint URL, HaaS user id, harness id/base, image
  reference/digest, local-development unpinned-image override, strategy, allowlist,
  trigger keywords, and TTL defaults) may be stored in user-owned preferences or
  user-global config.
- `api_token` or equivalent bearer credentials MUST be stored through the manager's
  secret store and MUST NOT be returned by settings read APIs.
- Settings changes apply to new unbound sessions only. Existing `HAAS_BOUND`
  sessions keep their binding snapshot unless an explicit policy update/rebind
  operation is performed.

Manager implementations MAY also supervise a local HaaS sidecar for GUI launches
so the desktop app and browser-dev app can connect without a separate manual
HaaS process. Local supervision is controlled by an explicit non-secret setting
(`local_autostart`, default `false`) and MUST only apply to loopback HaaS URLs
(`127.0.0.1` or `localhost`). The manager MUST pass only non-secret process
configuration through environment variables; the delegated API token remains in
the manager secret store and is used only as an outbound Authorization header.
If local autostart is enabled and the sidecar cannot be started or does not pass
`/v1/haas/health`, the manager settings API MUST report a safe local status while
delegated turns continue to fail closed through the existing HaaS delegation error
path. Production delegation MUST continue to require an image digest. A local
development flow MAY set `allow_unpinned_local_image=true` to run a local tag such
as `haas:local`; this override must be explicit and must not be enabled by
workspace-local configuration.

### 6.3 Mount Manifest Rules

- The authorized project root is mounted `rw` at `/workspace`.
- Extra authorized directories default to `ro` and use deterministic paths under `/mnt/extra/*`.
- Escalating an extra mount from `ro` to `rw` requires new explicit authorization and a policy update/rebind.
- The manager MUST NOT authorize user HOME, parent directories that exceed the project grant, Docker socket, SSH directories, credential stores, or unrequested paths.
- The HaaS container MUST have an independent writable HOME, cache root, and `/tmp` so builds, dependency installs, and tool execution can complete without mounting host HOME.

## 7. Runtime Model and State Machine

### 7.1 Manager Binding

```text
UNBOUND
  -> LOCAL_BOUND
  -> HAAS_BOUND

HAAS_BOUND persists across TTL cleanup, process restart, and follow-up turns.
```

Rules:

- Deterministic rules run before model-based intent recognition.
- Once the first delegation succeeds, the manager session is fixed to `HAAS_BOUND`.
- A `HAAS_BOUND` session does not silently fall back to local execution.
- Manual user action may start a new local session or explicitly rebind policy, but that is a new auditable decision.

### 7.2 Delegated Container Lifecycle

```text
no_runtime
  -> creating
  -> running
  -> idle
  -> ttl_destroyed
  -> restoring
  -> running
  -> draining
  -> destroyed
```

Rules:

- One delegated session owns one active container at a time.
- Follow-up turns in the same delegated session reuse the same container and Codex thread while it is alive.
- Idle TTL defaults to 30 minutes and is configurable.
- Maximum container lifetime defaults to 8 hours and is configurable. An active turn may finish, but the container MUST refuse new turns after the maximum lifetime is reached.
- TTL cleanup destroys runtime resources only. It does not delete the delegated-session contract, HaaS session, event log, approval history, or host files.
- A later follow-up turn restores the container from the stored contract after revalidating the mount manifest and policy snapshot.

### 7.3 Workspace Single-Writer Admission

```text
rw turn requested
  -> canonical workspace lock available? yes -> execute
  -> no -> queued_for_workspace_lock
```

Rules:

- A canonical workspace may have only one active `rw` delegated session at a time.
- Additional `rw` turns for the same canonical workspace queue in FIFO order.
- `ro` delegated sessions may run concurrently.
- Queue status MUST be visible through structured events, including queue reason and position when available.
- A crashed or expired container MUST release its workspace lock after the store confirms terminal or cleanup state.

## 8. Security and Permissions

- Manager is the only user approval surface. HaaS emits `approval_required`; manager displays it, records the decision, and returns the result.
- HaaS MUST NOT store raw provider keys; it receives `credentialRef` or a short-lived runtime token.
- HaaS MUST NOT widen mounts, workspace permissions, network access, or tool permissions on its own.
- Restore revalidates canonical paths, path existence, type, symlink boundaries, and access mode. Any mismatch fails closed.
- HaaS unavailable, image unavailable, container startup failure, mount validation failure, path drift, and workspace-lock denial all fail closed with structured errors and next action hints.
- No delegated container may mount Docker socket, user HOME, SSH directories, credential directories, or unapproved host paths.

## 9. Observability

Events/logs:

- `haas.delegation.session_created`
- `haas.delegation.session_bound`
- `haas.delegation.policy_snapshot`
- `haas.delegation.policy_updated`
- `haas.delegation.restore_started`
- `haas.delegation.restore_failed`
- `haas.delegation.container_ttl_destroyed`
- `haas.delegation.workspace_lock_queued`
- `haas.delegation.workspace_lock_acquired`
- `haas.delegation.workspace_lock_released`
- `haas.approval.required`
- `haas.approval.resolved`

Metrics:

- `haas_delegated_sessions_total{status}`
- `haas_delegated_containers_active`
- `haas_delegated_restore_total{status,reason}`
- `haas_workspace_lock_queue_depth`
- `haas_workspace_lock_wait_ms`
- `haas_approval_wait_ms{status}`

Logs and events MUST use safe ids, credential fingerprints, redacted paths when necessary, and safe reasons. They MUST NOT include raw prompts, full tool arguments, provider keys, Authorization values, cookies, or presigned URLs.

## 10. Failure and Recovery

| Scenario | Behavior |
|----------|----------|
| HaaS unavailable for a `HAAS_BOUND` session | Fail closed; do not run locally; return retryable delegated-backend error. |
| Container image unavailable | Fail closed; report image unavailable and next action. |
| Container start fails | Fail closed; retain contract and terminal error evidence. |
| Idle TTL reached | Destroy runtime resources; keep durable session contract and events. |
| Follow-up after TTL cleanup | Restore from contract after validating mounts, policy, provider reference, and image digest. |
| Mount path missing or canonical path changed | Fail closed; require reauthorization or policy rebind. |
| Same workspace has active `rw` session | Queue FIFO or return queue-full/timeout according to policy. |
| Approval bridge unavailable | Mark invocation blocked or failed; do not auto-approve. |
| Policy config changed globally | Existing sessions keep snapshots; new sessions use new policy. |
| Explicit policy update fails validation | Reject; keep the previous policy snapshot. |

## 11. Test Plan and Acceptance Criteria

- Unit: delegation policy merging, session snapshot behavior, path canonicalization, mount manifest validation, and deterministic route decisions.
- Unit: provider catalog includes separate `volcengine-ark`, `ark`, and `ark-agent-plan-cn` identities without sharing credential config.
- Integration: create delegated session, run a turn via `/run_sse`, stream live events, read terminal state, and continue the same session.
- Integration: TTL destroys the container, follow-up recreates it with the same mount/policy/provider contract, and Codex continues the HaaS session.
- Concurrency: two `rw` delegated sessions for the same canonical workspace serialize; `ro` sessions run concurrently.
- Security: reject HOME, Docker socket, SSH, parent-directory, symlink escape, and unapproved mount widening cases.
- Approval: HaaS emits `approval_required`, manager returns approve/deny, and the result is persisted and reflected in the event stream.
- Failure: HaaS unavailable, image unavailable, mount drift, container startup failure, and queue timeout all fail closed without local fallback.
- E2E: with explicit Docker/Codex flags enabled, run a real delegated coding task against an authorized project mounted `rw` at `/workspace`.
