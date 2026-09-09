# Sandbox Runtime Component Specification

**English** | [简体中文](README.zh-CN.md)

Status: Draft
Last reviewed: 2026-08-26
Related specs: [Architecture](../architecture/README.md), [Policy Controller](../policy-controller/README.md), [Harness Adapter](../harness-adapter/README.md), [Container Runtime](../container-runtime/README.md), [Security Boundary](../security-boundary/README.md), [Manager Delegation](../manager-delegation/README.md)

## 1. Component Role

Sandbox Runtime is the standardized execution substrate for HaaS multi-harness environments. It projects the `EffectivePolicy` compiled by Policy Controller and the sandbox requirements declared by harness adapters into a unified set of OpenSandbox AIO sandbox/execd/credential vault configurations, allowing different harnesses (Codex, Pi, OpenCode, and AMP) to execute under the same isolation model.

It addresses the fact that each harness carries its own sandbox semantics (Codex sandbox policy, OpenCode permission config, and Pi workspace isolation). Sandbox Runtime converges these differences onto one set of underlying OpenSandbox capabilities instead of requiring HaaS to assemble a different isolation policy for every harness.

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
| Downstream | OpenSandbox sandbox API | Creates/destroys sandbox instances |
| Downstream | OpenSandbox execd | Executes commands inside the sandbox with SSE result support |
| Downstream | OpenSandbox credential vault | Stores provider/key references and issues short-lived tokens per session |
| Downstream | OpenSandbox egress | Network egress policy |

## 4. Responsibility Boundaries

Responsibilities:

- Compile `EffectivePolicy` into `SandboxSpec` (workspace mounts, writable roots, network egress, resource limit).
- Validate and project manager-approved mount manifests for delegated sessions.
- Create and track the lifecycle of an OpenSandbox sandbox instance for each session/invocation.
- Narrowly project the harness adapter's sandbox declaration (the projected scope MUST be less than or equal to the policy scope and MUST NOT expand it).
- Write provider/key secrets to the credential vault; adapters receive only vault references or short-lived tokens.
- Run harness runtime processes inside the sandbox and bridge execd output to the adapter.
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
    "defaultAction": "deny",
    "allow": ["https://api.openai.com"]
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
  "approvalMode": "never",
  "nativeSandbox": {
    "supported": true,
    "mode": "workspace-write"
  }
}
```

In the initial release, `compile_sandbox_spec` consumes only `cwd`, `writableRoots`, and `approvalMode`; `base` and `nativeSandbox` are adapter capability declarations for subsequent OpenSandbox projection (S5.3).

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
  -> sandbox created
  -> secret written to vault
  -> harness runtime started inside sandbox
  -> turn executes (execd streams back)
  -> sandbox destroyed at session close
```

Sandbox instance states:

```text
requested -> creating -> running -> draining -> stopped -> destroyed
                  |                     +-> failed
```

Rules:

- A sandbox instance follows its session; deleting a session MUST synchronously destroy its sandbox.
- If writableRoots declared by the adapter exceed the policy, the compile phase MUST reject the declaration and MUST NOT silently expand the scope.
- A provider key MUST only be written to the vault and MUST NOT appear in the sandbox env or startup command arguments.
- After a sandbox restart, `generation` increases; the adapter determines and reports whether the harness thread is recoverable.

## 8. Security and Permissions

- Sandbox isolation is a hard runtime boundary, but not the only boundary (defense in depth).
- A harness's built-in sandbox can only serve as an inner layer and MUST NOT bypass the OpenSandbox sandbox/egress layer.
- Credential vault scope is per session/audience, with a short TTL and revocation support.
- All workspace paths MUST be canonicalized before comparison with policy.
- Network egress is constrained by both OpenSandbox egress policy and the HaaS URL validator; neither MAY be omitted.
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
| vault write fails | turn does not start and returns `haas_vault_unavailable` |
| egress blocks network access | handle as deny; record a security event with a redacted host |
| destruction fails | retain a cleanup queue and retry; MUST NOT block convergence of session state |

## 11. Test Plan and Acceptance

- Unit: SandboxSpec compilation, delegated mount validation, rejection of narrow-only projection violations, path canonicalization, and egress compilation.
- Integration: OpenSandbox sandbox create/run/destroy, execd SSE result, credential vault writes, and short-lived token revocation.
- Security: provider keys do not enter the sandbox env/startup commands/logs; all widening is rejected.
- E2E: a Codex turn completes file reads/writes inside the sandbox and produces an artifact; verify that paths are constrained.

## 12. Next Validation Steps

- Confirm the locally available OpenSandbox sandbox API and execd version and exact endpoints (`Unknown`; to be verified against the pinned commit during implementation). The initial `OpenSandboxClient` assumes the REST endpoints `POST/GET/DELETE /sandboxes[/{id}]`, `POST /sandboxes/{id}/exec`, and `POST /vault/secrets`, exposed as class constants so they can be corrected after probing.
- Confirm that the Codex app-server process can run reliably as non-root inside an OpenSandbox sandbox and connect point-to-point to the loopback model/MCP proxy.
- Real OpenSandbox probing uses the explicit `HAAS_E2E_OPEN_SANDBOX=1` switch; without it, OpenSandbox client tests use an offline mock (`httpx.MockTransport`).
