# Policy Controller Component Specification

**English** | [简体中文](README.zh-CN.md)

Status: Draft
Last reviewed: 2026-09-14
Change ID: unified-runtime-approval-policy
Related specs: [Security Boundary](../security-boundary/README.md), [Harness Registry](../harness-registry/README.md), [Harness Profile](../harness-profile/README.md), [Session Runtime](../session-runtime/README.md), [Manager Delegation](../manager-delegation/README.md)

## 1. Component Role

The Policy Controller compiles caller, tenant, workspace, configured harness, request override, and deployment defaults into the effective policy for a session/turn. It is the sole policy owner for HaaS runtime admission.

## 2. Sources and Rationale

| Source | Adopted Content |
|--------|-----------------|
| `mpa-codex-worker` policy controller | Dynamic policies for profile/workspace/model/MCP/tool/network/hook and the fail-closed principle |
| Harness Profile | Workspace/network/tool/approval/model policy layer in profile revisions |
| Security Boundary | Tool minimization, budget, object scope, and provider URL allowlist |
| Sandbox Runtime | Projection of workspace/network/tool/approval policies into OpenSandbox sandbox/egress configuration |
| OpenSandbox egress docs | Separation between network egress policy and credential vault |

## 3. Upstream and Downstream Relationships

| Direction | Component | Relationship |
|-----------|-----------|--------------|
| Upstream | HaaS Protocol | Supplies request metadata, headers, and task budgets |
| Upstream | Harness Registry / Harness Profile | Provides configured harness identity and active profile policy |
| Upstream | Session Runtime | Requests the effective policy for a session/turn |
| Downstream | Harness Adapter | Receives adapter-specific policy projections |
| Downstream | Model Proxy | Restricts models and provider routes |
| Downstream | MCP / Tool / Skill Runtime | Restricts MCP, tools, and skills |
| Downstream | Container Runtime | Restricts workspace, network, mounts, and processes |

## 4. Responsibility Boundaries

Responsibilities:

- Merge organization-, workspace-, harness-, session-, and turn-level policies.
- Merge delegation policy layers for manager-delegated sessions and freeze the
  effective delegation policy snapshot at delegated-session creation time.
- Validate that policy changes only narrow permissions; widening MUST have an explicit authorization source.
- Produce an `EffectivePolicy` and freeze it into the session/turn.
- Project generic policy into adapter-specific configuration.
- Reject unknown permission requests or requests that cannot be implemented safely.
- Record policy decisions and safe reasons.

Non-responsibilities:

- Does not execute tools or network requests.
- Does not store credential values.
- Does not dynamically grant permissions based on harness text output.
- Does not treat hard blocks unsupported by an adapter as enforced by default.
- Does not decide manager intent routing; it only validates the policy and mount contract after manager authorization.

## 5. Core Interfaces

```python
async def compile_policy(input: PolicyCompileInput) -> EffectivePolicy: ...
async def authorize_request(ctx: RequestContext, action: str, resource: str) -> PolicyDecision: ...
async def authorize_tool(policy: EffectivePolicy, tool: ToolRequest) -> PolicyDecision: ...
async def authorize_network(policy: EffectivePolicy, url: str) -> PolicyDecision: ...
async def authorize_workspace_path(policy: EffectivePolicy, path: str, access: str) -> PolicyDecision: ...
async def authorize_mount_manifest(policy: EffectivePolicy, manifest: MountManifest) -> PolicyDecision: ...
async def project_for_adapter(policy: EffectivePolicy, adapter_id: str) -> AdapterPolicyProjection: ...
```

## 6. Data Model

### 6.1 EffectivePolicy

```json
{
  "policyId": "pol_abc",
  "version": 3,
  "scope": {
    "tenantId": "tenant_1",
    "workspaceId": "workspace_1",
    "harnessId": "chrn_codex_default",
    "sessionId": "hsess_abc"
  },
  "workspace": {
    "mode": "workspace-write",
    "root": "/workspace",
    "writableRoots": ["/workspace"]
  },
  "network": {
    "defaultAction": "allow",
    "allow": []
  },
  "tools": {
    "disabled": ["web_search"],
    "approvalMode": "on-request"
  },
  "model": {
    "allowedModels": ["gpt-5.6-terra"],
    "fallbackModel": "gpt-5.6-terra"
  },
  "delegation": {
    "idleTtlSeconds": 1800,
    "maxContainerLifetimeSeconds": 28800,
    "rwWorkspaceConcurrency": "single_writer",
    "queuePolicy": "fifo",
    "restorePolicy": "fail_closed",
    "policyChangeMode": "snapshot_per_session",
    "mountPolicy": "project_rw_extra_ro"
  }
}
```

### 6.1.1 PolicyCompileInput and PolicyLayer

```json
{
  "scope": {"tenantId": "tenant_1", "workspaceId": "workspace_1", "harnessId": "chrn_codex_default", "sessionId": "hsess_abc"},
  "layers": [
    {
      "name": "tenant",
      "workspace": {"mode": "workspace-write", "root": "/workspace", "writableRoots": ["/workspace"]},
      "network": {"defaultAction": "allow", "allow": []},
      "tools": {"disabled": ["web_search"], "approvalMode": "on-request"},
      "model": {"allowedModels": ["gpt-5.6-terra"], "fallbackModel": "gpt-5.6-terra"},
      "delegationPolicy": {"idleTtlSeconds": 1800, "maxContainerLifetimeSeconds": 28800},
      "delegation": false
    }
  ]
}
```

- `layers` are ordered from broadest to narrowest (platform/tenant → workspace → harness → session → turn).
- A field set to `null` in a layer means that the layer does not override that dimension, and the field is skipped during merging.
- `delegation: true` means that the layer explicitly authorizes all lower layers to widen its constraints. Without delegation, any widening by a lower layer fails closed (`PolicyWideningRejected`).
- Fresh interactive sessions default to `workspace-write`, public network
  `defaultAction=allow`, and `approvalMode=on-request`. Public-network allow still blocks
  loopback, private/link-local, metadata, control-plane and cross-session destinations unless
  an independently authorized internal route requires them. It never implies credential access.
- `approvalMode` is not a linear permission rank. `always` asks before every approval-eligible
  action; `on-request` asks only when the effective sandbox/policy cannot authorize the proposed
  action directly; `never` forbids asking and therefore denies any action that would require a
  grant. In particular, `never` does not mean unrestricted execution. Changing from `never` to
  `on-request`, or reducing mandatory prompts, requires explicit caller authority and a recorded
  policy revision. Changing to `never` is a fail-closed narrowing of the approval channel.
- Merge rules: workspace mode may only become stricter (`danger-full-access` →
  `workspace-write` → `read-only`); workspace.root and writableRoots / network.allow /
  model.allowedModels may only narrow; tools.disabled may only grow. Approval mode changes use
  the authorization matrix above instead of rank comparison. A lower-layer workspace.root is
  narrower only when its canonical path is equal to, or a child of, the current canonical
  workspace.root; string-prefix matches such as `/ab` under `/a` and traversal forms such as
  `/a/../..` MUST NOT bypass this check.

### 6.3 Approval Request and Grant

An approval request is a typed authorization challenge, not harness prose. It contains a stable
`approvalId`, invocation/action identity, redacted action/resource summary, policy reason,
advertised decisions/scopes, expiry, and the effective policy revision. P0 defaults to
`scope=action`; an adapter may advertise a bounded `scope=invocation` only when it can enforce an
exact action/resource fingerprint. No approval grants cross-session authority.

An approved decision appends an immutable grant to the current invocation's private approval
ledger and resumes the same native request exactly once. It does not rewrite the session policy.
Denied, expired, cancelled, duplicate-conflicting, or unverifiable requests fail closed. Platform
hard denies, credential boundaries, host/private-network protection, mount roots, and unsupported
adapter capabilities are not approval-eligible.

### 6.2 PolicyDecision

```json
{
  "allowed": false,
  "code": "haas_policy_denied",
  "safeReason": "network_host_not_allowed",
  "retryable": false
}
```

## 7. Runtime Model and State Machine

```text
inputs collected
  -> validate authority
  -> merge from broad to narrow
  -> reject unauthorized widening
  -> compile EffectivePolicy
  -> freeze into session/turn
  -> project to adapter/runtime/proxy
```

Policy precedence:

1. Platform hard deny
2. Tenant policy
3. Workspace policy
4. Harness profile policy
5. Session policy
6. Turn-scoped override

Lower levels may only narrow unless a higher level explicitly grants delegation.
Harness Profile policy is the only entry point for dynamic harness-level
configuration. Activating a new profile affects only new sessions; existing
sessions use the frozen EffectivePolicy. Explicit profile rebind MUST recompile
policy and apply the same widening rules; failures keep the previous snapshot.

Delegation policy precedence uses the same broad-to-narrow order. The compiled
delegation policy is snapshotted into the delegated session. Later global/workspace
configuration changes do not change existing delegated sessions unless a HaaS native
policy update/rebind request is explicitly authorized and recorded.

Workspace write admission is part of the delegation policy: a canonical workspace may
have only one active `rw` delegated session at a time. Other `rw` turns queue according
to `queuePolicy=fifo`; `ro` sessions may run concurrently.

Policy changes use one revision barrier for local and delegated sessions. An authorized mutation
records `desiredRevision`, expected prior revision, the complete changed domains, actor and safe
reason. A running invocation retains its immutable `appliedRevision`; the next invocation waits
until `appliedRevision >= desiredRevision`. Apply failure preserves the prior applied snapshot and
blocks queued work rather than silently running it with stale policy. Mode changes and network or
workspace changes follow this same path; they are not frontend-only state.

## 8. Security and Permissions

- Unknown policy fields fail closed when they would affect security.
- The network allowlist is evaluated before outbound calls from the model/MCP proxy.
- Allowlist entries and request URLs are normalized before comparison. If an
  allowlist entry specifies a port, the request's effective port (explicit or
  scheme default: `80` for HTTP, `443` for HTTPS) MUST exactly equal it. For
  example, `http://host:8080` MUST NOT allow `http://host` or `http://host:80`.
  If an allowlist entry omits a port, it only allows the scheme's default port:
  `http://host` allows `http://host` and `http://host:80`, but MUST NOT allow
  `http://host:8080`; `https://host` allows `https://host` and
  `https://host:443`, but MUST NOT allow `https://host:8443`.
- Workspace paths are canonicalized before comparison.
- Policy compilation stores secret fingerprints, not values.
- Approval modes MUST map truthfully to adapter capabilities.
- Approval grants can authorize only actions marked approval-eligible by the compiled policy.
  Platform hard denies and an adapter/runtime inability cannot be overridden by approval.
- Public error details use `safeReason`, not a raw denied path/header/URL when it is sensitive.

### 8.1 Residual Risk: Network Egress Authorization and DNS

`PolicyController.authorize_network` (`haas/policy/controller.py`) performs
**hostname string matching plus an IP-literal private-range check**. It does
**not** resolve DNS before admitting a request. This is a known residual risk
from the Python backend optimization review (finding O-SSRF-1 / S3-009 +
S4-003), recorded here rather than fixed in code for this wave:

- **Attack surface**: a caller-controlled public hostname whose DNS record
  resolves to a loopback, private/link-local, or cloud-metadata address passes
  the string-based allowlist and the IP-literal private check, because the check
  inspects the hostname text, not the resolved socket address. A DNS-rebinding
  or "DNS pinning" attack could therefore make an in-process egress call target
  an otherwise-blocked internal address.
- **Hard boundary**: delegated (containerized) sessions run with
  `--network none` (plus `--cap-drop ALL`, `no-new-privileges`, non-root). In
  that posture the harness cannot make arbitrary outbound connections at all, so
  the in-process check is defense-in-depth, not the primary control. The
  residual risk applies only to the standalone in-process policy controller when
  it is used to admit egress without a network-isolated container.
- **Current mitigations**: (1) loopback/private/link-local/metadata hosts are
  blocked unless independently allowlisted; (2) only `http`/`https` schemes and
  an explicit/derived port are admitted; (3) allowlist entries and request URLs
  are normalized and port-exact matched (see §8); (4) delegated containers add
  the `--network none` hard isolation on top.
- **Deferred hardening** (not done in this wave): resolved-IP validation at the
  outbound transport layer — verify the connected socket address is outside the
  private/metadata ranges after `connect()`, and reject DNS-rebinding by pinning
  the resolved address for the request lifetime. This belongs with the model/MCP
  proxy transport, not the policy controller.

### 8.2 Sandbox Network Default Action (M-02 closed as false positive)

The review flagged `SandboxNetwork.defaultAction = "allow"` as a potential
fail-open default (finding S3-008 / measurement M-02). Verification
(`grep -rn 'SandboxSpec(' haas/ tests/`, 2026-09-24) confirms the **only
production construction** of the container `SandboxSpec` is
`haas/runtime/compiler.py`, which always overrides `network.defaultAction` from
the compiled `EffectivePolicy.network.defaultAction`. No production path
relies on the `"allow"` dataclass default; direct `SandboxSpec(...)`
constructions exist only in tests. M-02 is therefore closed as a
**false_positive**: the default is unreachable in production and no deny-by-default
change is required. A regression test should continue to assert that every
production sandbox projection flows through the compiler rather than a bare
default-constructed spec.

## 9. Observability

- `haas.policy.compiled`
- `haas.policy.denied`
- `haas.policy.adapter_projection`
- `haas.policy.widening_rejected`
- `haas.policy.unknown_field`

Metrics:

- `haas_policy_decision_total{decision,reason}`
- `haas_policy_compile_duration_ms`
- `haas_policy_projection_total{adapterBase,status}`

## 10. Failure and Recovery

| Scenario | Behavior |
|----------|----------|
| Policy store unavailable before execution | Fail closed |
| Unknown security-affecting field | Reject with `haas_policy_invalid` |
| Adapter cannot enforce a hard requirement | Reject with `haas_policy_unsupported` |
| Requested wider workspace root | Reject unless higher-level delegation allows it |
| Delegated mount manifest widens access | Reject with `haas_delegation_mount_invalid` |
| Existing delegated session receives implicit policy drift | Reject; require explicit policy update/rebind |
| Profile rebind would widen policy without authorization | Reject and keep the previous effective policy snapshot |
| Same canonical workspace already has active `rw` delegated session | Queue or return `haas_workspace_lock_busy` / `haas_workspace_lock_timeout` according to policy |
| Network URL fails allowlist | Reject before making an outbound connection |
| Approval required but no approval bridge exists | Reject or mark the task blocked; do not auto-approve |
| `approvalMode=never` action needs a grant | Reject with a stable policy-denied reason; do not emit a request |
| Policy update races a running invocation | Keep the invocation on its applied revision; gate the next invocation |
| Approval expires or conflicts with a terminal invocation | Close it as expired/cancelled and never answer the native request twice |

## 11. Test Plan and Acceptance Criteria

- Unit: policy precedence, widening rejection, path canonicalization, and network URL validation.
- Adapter projection: Codex/Pi/OpenCode fixtures verify hard/advisory/unsupported declarations.
- Security: SSRF cases, private IP, metadata endpoint, and Unix socket and Docker socket denial.
- Integration: a session snapshot freezes policy, and a later harness update does not alter the active session.
- Integration: a delegated-session policy snapshot is unaffected by later global/workspace config changes until an explicit policy update/rebind.
- Integration: profile activation affects only new sessions; profile rebind
  recompiles policy and rejects unauthorized widening.
- Concurrency: one active `rw` delegated session per canonical workspace; `ro` delegated sessions remain concurrent.
- Review: any policy expansion requires a spec review and a security-boundary update.
- Defaults: a fresh session compiles to `workspace-write + public network allow + on-request`,
  while private/metadata/control routes and platform hard denies remain blocked.
- Approval matrix: `always`, `on-request`, and `never` produce the specified ask/execute/deny
  behavior; `never` never becomes unrestricted execution.
- Revision barrier: change approval, network, and workspace settings during a running turn; prove
  the current invocation retains the old revision and the next waits for and uses the new one.
- End-to-end: a command requiring escalation produces one durable redacted request, one decision,
  one native response and same-invocation continuation across disconnect/reconnect.
