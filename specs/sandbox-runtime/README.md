# Sandbox Runtime Component Specification

**English** | [简体中文](README.zh-CN.md)

Status: Draft
Last reviewed: 2026-09-14
Change ID: unified-runtime-approval-policy
Related specs: [Architecture](../architecture/README.md), [Policy Controller](../policy-controller/README.md), [Harness Adapter](../harness-adapter/README.md), [Container Runtime](../container-runtime/README.md), [Security Boundary](../security-boundary/README.md), [Manager Delegation](../manager-delegation/README.md)

## 1. Component Role

Sandbox Runtime projects `EffectivePolicy` and adapter declarations into one isolation contract implemented by Lite Docker (default) or OpenSandbox AIO. Harness-native sandboxing remains an inner layer; both implementations enforce the outer workspace, resource, network and credential boundary.

OpenSandbox sandbox/execd/vault APIs below are AIO-specific. Lite uses Docker lifecycle, a private worker/broker network and retained session volume; it does not emulate unavailable AIO endpoints.

## 2. Sources and Rationale

| Source | Adopted content |
|--------|-----------------|
| OpenSandbox docs | sandbox lifecycle (create/start/stop/delete), execd command execution, credential vault, egress policy |
| OpenSandbox AIO | in-image shell/file/browser capabilities, sandbox API port `8080` |
| Codex app-server | built-in sandbox policy, which can only be an inner layer and does not replace the outer HaaS sandbox |
| Policy Controller | effective policy projection for workspace/network/tool/approval |

## 3. Upstream and Downstream Relationships

| Direction | Component | Relationship |
|-----------|-----------|--------------|
| Upstream | Policy Controller | Provides `EffectivePolicy` (workspace/network/tool/approval) |
| Upstream | Harness Adapter | Provides harness-specific sandbox declarations (writableRoots, cwd, approvalMode) |
| Upstream | Session Runtime | Requests creation of a sandbox instance for a session/invocation |
| Upstream | Artifact Store | Reports workspace file indexes and artifact paths |
| Downstream | Lite Docker runtime | Default container lifecycle, mounts, resource limits and private worker/broker network |
| Downstream | OpenSandbox AIO | Optional sandbox/execd/vault/egress projection |

## 4. Responsibility Boundaries

Responsibilities:

- Compile `EffectivePolicy` into `SandboxSpec` (workspace mounts, writable roots, network egress, resource limit).
- Validate and project manager-approved mount manifests for delegated sessions.
- Create and track the selected Lite or AIO sandbox implementation for each delegated session.
- Narrowly project the harness adapter's sandbox declaration (the projected scope MUST be less than or equal to the policy scope and MUST NOT expand it).
- Provision real secrets only to AIO vault or trusted Lite broker memory; adapters receive short-lived scoped tokens.
- Run harness processes through the selected private worker transport and normalize results for the adapter.
- Record sandbox lifecycle and security events.

Non-responsibilities:

- It does not decide workspace/network/tool policy; it only consumes Policy Controller output.
- It does not accept arbitrary host paths from a harness or adapter; delegated-session host paths must already be authorized by manager and validated by Policy Controller.
- It does not execute a harness-native protocol; that is the adapter's responsibility.
- It does not replace the HaaS protocol public API.
- It does not store long-lived provider credentials in plaintext; vault contents are references.

## 5. Core Interfaces

```python
async def compile_sandbox_spec(policy: EffectivePolicy, decl: HarnessSandboxDecl) -> SandboxSpec: ...
async def create_sandbox(session_id: str, spec: SandboxSpec) -> SandboxHandle: ...
async def run(sandbox_id: str, command: list[str], cwd: str, env: dict) -> ExecStream: ...
async def write_secret(session_id: str, audience: str, ref: str, ttl: int) -> VaultRef: ...
async def project_egress(spec: SandboxSpec) -> EgressPolicy: ...
async def validate_mount_manifest(manifest: MountManifest, policy: EffectivePolicy) -> MountValidation: ...
async def destroy_sandbox(sandbox_id: str) -> None: ...
async def inspect_sandbox(sandbox_id: str) -> SandboxInspection: ...
```

### 5.1 Lite Isolation and Common Runtime Guarantees

- Lite uses the existing runtime interface, Docker CPU/memory/PID limits, a read-only root filesystem, dedicated writable workspace/home/tmp roots, non-root worker identity, dropped capabilities and no-new-privileges. AIO must satisfy the same effective constraints in addition to its service projection.
- Workers join only the internal per-session network from Container Runtime. The external broker is not a router: it accepts only authenticated model/MCP operations for the frozen invocation revision. Worker internet, DNS-based bypass, host/metadata access and cross-session broker access must be denied by the network boundary, not instructions. Verify IPv4/IPv6, redirects and DNS rebinding.
- Lite P0 supports shell/file work and brokered model/MCP HTTP/SSE; arbitrary tool internet access and browser/VNC are unsupported. A request requiring unsupported egress or execution fails `haas_policy_unsupported`; enabling an unrestricted Docker network is not a fallback.
- The broker is the only process that resolves long-lived credentials. Loopback relays inside the worker carry short-lived session/audience/generation tokens; native processes never receive real upstream credentials. Broker failure blocks execution rather than opening direct routes.
- `create_sandbox` attaches the retained session volume, validates all authorized mount roots including overlaps, and verifies required enforcement before admission. Mount changes trigger a drained resource recreation under the same logical session. A native reference without retained native state is not a successful restore.
- The control sidecar owns public acceptance, event ids and terminal state. A worker start has a durable deduplicated execution id and fenced generation. Private inspect/cancel/replay reconcile controller and worker state; they cannot create a second public invocation.
- Workspace writer ownership covers every writable mount and overlapping parent/child roots across sessions. Hold ownership until old worker processes cannot write, not merely until a terminal database flag. Queue bounds are 100 waiting turns and 300 seconds by default; timeout/cancel removes the waiter. Admission, policy application and volume cleanup use the same ownership boundary.

## 6. Data Models

### 6.1 SandboxSpec

```json
{
  "sessionId": "hsess_abc",
  "workspaceRoot": "/workspace",
  "writableRoots": ["/workspace"],
  "readOnlyRoots": ["/mnt/extra/shared"],
  "mounts": [
    {
      "hostPathCanonical": "/Users/example/workspace/project",
      "containerPath": "/workspace",
      "access": "rw"
    },
    {
      "hostPathCanonical": "/Users/example/workspace/shared",
      "containerPath": "/mnt/extra/shared",
      "access": "ro"
    }
  ],
  "isolatedWritableRoots": ["/home/haas", "/tmp", "/data/haas/cache"],
  "network": {
    "defaultAction": "allow",
    "allow": []
  },
  "resources": {
    "cpu": 2,
    "memoryMb": 4096,
    "timeoutSeconds": 1800
  },
  "credentialVault": {
    "providerKeyRef": "secret://tenant/workspace/provider/default"
  }
}
```

### 6.2 HarnessSandboxDecl (adapter declaration)

```json
{
  "base": "codex",
  "cwd": "/workspace",
  "writableRoots": ["/workspace"],
  "approvalMode": "on-request",
  "nativeSandbox": {
    "supported": true,
    "mode": "workspace-write"
  }
}
```

In the initial release, `compile_sandbox_spec` consumes only `cwd`, `writableRoots`, and `approvalMode`; `base` and `nativeSandbox` are adapter capability declarations for subsequent OpenSandbox projection (S5.3).

The fresh-session product default is `workspace-write + public network allow + on-request`.
These dimensions remain independent: changing approval mode does not change mounts or egress,
and approving one action does not rebuild or widen the outer sandbox. Public-network allow is not
host-network access; private/link-local/metadata/control-plane/cross-session routes remain blocked.
An action that requires an outer-sandbox capability unavailable in the selected Lite/AIO variant
is non-approvable and fails with `haas_policy_unsupported`.

### 6.3 Delegated Mount Manifest

Manager-delegated sessions use the mount manifest defined by
[Manager Delegation](../manager-delegation/README.md). Sandbox Runtime validates the
manifest before container creation and before every restore:

- the primary project mount is exactly `/workspace:rw`;
- extra mounts are `ro` by default and use deterministic paths under `/mnt/extra/*`;
- host paths are canonicalized and compared with the authorized snapshot;
- symlink escapes, path disappearance, path type changes, Docker socket, user HOME,
  SSH directories, credential stores, and parent-directory widening fail closed;
- the sandbox supplies independent writable HOME, cache, and `/tmp` roots without
  mounting host HOME.

### 6.4 SandboxHandle

```json
{
  "sandboxId": "sbx_abc",
  "sessionId": "hsess_abc",
  "status": "running",
  "generation": 1,
  "createdAtMs": 1786400000000
}
```

## 7. Runtime Model and State Machine

```text
policy compiled
  -> harness sandbox decl collected
  -> sandbox spec compiled (narrow-only projection)
  -> selected Lite/AIO sandbox created
  -> short token issued; real secret provisioned only to broker/vault
  -> harness runtime started through private worker transport
  -> turn executes and normalized events stream back
  -> sandbox destroyed at session close
```

Sandbox instance states:

```text
requested -> creating -> running -> draining -> stopped -> destroyed
                  |                     +-> failed
```

Rules:

- A sandbox instance follows its session; deletion immediately revokes access/admission and schedules fenced physical cleanup. Cleanup failure must not permit another writer.
- The running invocation uses an immutable sandbox/policy revision. A dynamic approval, network,
  workspace, mount, or image policy update is staged through the session revision barrier and is
  applied only before a later invocation. If the outer sandbox must change, drain and rebuild it
  before advancing `appliedRevision`; never mutate the running sandbox in place.
- A current-action approval resumes the waiting harness request through its adapter bridge. It
  never changes the outer sandbox, mount set, network namespace, platform hard denies, or future
  invocation defaults.
- If writableRoots declared by the adapter exceed the policy, the compile phase MUST reject the declaration and MUST NOT silently expand the scope.
- A provider key may exist only in AIO vault or trusted Lite broker memory and MUST NOT enter worker env, mounts, startup arguments, or session volume.
- After a sandbox restart, `generation` increases; the adapter determines and reports whether the harness thread is recoverable.

## 8. Security and Permissions

- Sandbox isolation is a hard runtime boundary, but not the only boundary (defense in depth).
- A harness's built-in sandbox can only serve as an inner layer and MUST NOT bypass selected Lite/AIO runtime enforcement.
- Runtime-token scope is per session/audience/generation with short TTL and revocation; real secrets remain in AIO vault or Lite broker memory.
- All workspace paths MUST be canonicalized before comparison with policy.
- Network egress is constrained by Lite isolated broker networking or AIO egress plus the HaaS URL validator; neither runtime enforcement nor URL validation may be omitted.
- The sandbox env, startup commands, and execd output MUST be redacted before entering logs/events.

## 9. Observability

Events/logs:

- `haas.sandbox.spec_compiled`
- `haas.sandbox.created`
- `haas.sandbox.destroyed`
- `haas.sandbox.run_failed`
- `haas.sandbox.vault_write`
- `haas.sandbox.widening_rejected`

Metrics:

- `haas_sandbox_active`
- `haas_sandbox_create_duration_ms{status}`
- `haas_sandbox_run_total{adapterBase,status}`
- `haas_sandbox_vault_write_total{audience}`

## 10. Failures and Recovery

| Scenario | Behavior |
|----------|----------|
| sandbox creation fails | invocation fails; MUST NOT fall back to execution without a sandbox |
| adapter declaration exceeds policy | `haas_sandbox_widening_rejected`; fail closed |
| sandbox restarts | `generation` increases; adapter inspection determines whether the thread is recoverable |
| delegated mount manifest drifts | return `haas_delegation_mount_invalid`; require manager reauthorization or policy rebind |
| vault/broker provisioning fails | turn does not start and returns `haas_vault_unavailable` |
| egress blocks network access | handle as deny; record a security event with a redacted host |
| approval asks for unavailable outer-sandbox capability | reject as non-approvable with `haas_policy_unsupported` |
| sandbox rebuild for policy revision fails | keep the previous applied revision, block new turns, and expose a safe retry action |
| destruction fails | retain a cleanup queue and retry; MUST NOT block convergence of session state |

## 11. Test Plan and Acceptance

- Unit: SandboxSpec compilation, delegated mount validation, rejection of narrow-only projection violations, path canonicalization, and egress compilation.
- Integration: common lifecycle/token/replay tests on Lite and AIO; AIO additionally covers sandbox/execd/vault APIs.
- Security: provider keys do not enter the sandbox env/startup commands/logs; all widening is rejected.
- E2E: a Codex turn completes file reads/writes inside the sandbox and produces an artifact; verify that paths are constrained.
- Defaults: fresh Lite and AIO sessions have workspace write, public egress and on-request
  approval, while host/private/metadata/control-plane paths remain unreachable.
- Revision: update network/workspace/approval while a command is active; prove the current
  sandbox is unchanged and the next invocation waits for the replacement generation.
- Approval boundary: approving an action cannot add a host mount, join host networking, disclose
  a credential, or enable a capability the selected runtime cannot enforce.

## 12. Next Validation Steps

- Confirm the locally available OpenSandbox sandbox API and execd version and exact endpoints (`Unknown`; to be verified against the pinned commit during implementation). The initial `OpenSandboxClient` assumes the REST endpoints `POST/GET/DELETE /sandboxes[/{id}]`, `POST /sandboxes/{id}/exec`, and `POST /vault/secrets`, exposed as class constants so they can be corrected after probing.
- Confirm Codex runs non-root in both Lite and AIO and connects only through the scoped model/MCP relay.
- Real OpenSandbox probing uses the explicit `HAAS_E2E_OPEN_SANDBOX=1` switch; without it, OpenSandbox client tests use an offline mock (`httpx.MockTransport`).
