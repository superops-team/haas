# Policy Controller Component Specification

**English** | [简体中文](README.zh-CN.md)

Status: Draft
Last reviewed: 2026-08-30
Related specs: [Security Boundary](../security-boundary/README.md), [Harness Registry](../harness-registry/README.md), [Session Runtime](../session-runtime/README.md)

## 1. Component Role

The Policy Controller compiles caller, tenant, workspace, configured harness, request override, and deployment defaults into the effective policy for a session/turn. It is the sole policy owner for HaaS runtime admission.

## 2. Sources and Rationale

| Source | Adopted Content |
|--------|-----------------|
| `mpa-codex-worker` policy controller | Dynamic policies for profile/workspace/model/MCP/tool/network/hook and the fail-closed principle |
| Security Boundary | Tool minimization, budget, object scope, and provider URL allowlist |
| Sandbox Runtime | Projection of workspace/network/tool/approval policies into OpenSandbox sandbox/egress configuration |
| OpenSandbox egress docs | Separation between network egress policy and credential vault |

## 3. Upstream and Downstream Relationships

| Direction | Component | Relationship |
|-----------|-----------|--------------|
| Upstream | HaaS Protocol | Supplies request metadata, headers, and task budgets |
| Upstream | Harness Registry | Provides configured harness policy |
| Upstream | Session Runtime | Requests the effective policy for a session/turn |
| Downstream | Harness Adapter | Receives adapter-specific policy projections |
| Downstream | Model Proxy | Restricts models and provider routes |
| Downstream | MCP / Tool / Skill Runtime | Restricts MCP, tools, and skills |
| Downstream | Container Runtime | Restricts workspace, network, mounts, and processes |

## 4. Responsibility Boundaries

Responsibilities:

- Merge organization-, workspace-, harness-, session-, and turn-level policies.
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

## 5. Core Interfaces

```python
async def compile_policy(input: PolicyCompileInput) -> EffectivePolicy: ...
async def authorize_request(ctx: RequestContext, action: str, resource: str) -> PolicyDecision: ...
async def authorize_tool(policy: EffectivePolicy, tool: ToolRequest) -> PolicyDecision: ...
async def authorize_network(policy: EffectivePolicy, url: str) -> PolicyDecision: ...
async def authorize_workspace_path(policy: EffectivePolicy, path: str, access: str) -> PolicyDecision: ...
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
    "defaultAction": "deny",
    "allow": ["https://api.openai.com", "https://mcp.example.com"]
  },
  "tools": {
    "disabled": ["web_search"],
    "approvalMode": "never"
  },
  "model": {
    "allowedModels": ["gpt-5.6-terra"],
    "fallbackModel": "gpt-5.6-terra"
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
      "network": {"defaultAction": "deny", "allow": ["https://api.openai.com"]},
      "tools": {"disabled": ["web_search"], "approvalMode": "never"},
      "model": {"allowedModels": ["gpt-5.6-terra"], "fallbackModel": "gpt-5.6-terra"},
      "delegation": false
    }
  ]
}
```

- `layers` are ordered from broadest to narrowest (platform/tenant → workspace → harness → session → turn).
- A field set to `null` in a layer means that the layer does not override that dimension, and the field is skipped during merging.
- `delegation: true` means that the layer explicitly authorizes all lower layers to widen its constraints. Without delegation, any widening by a lower layer fails closed (`PolicyWideningRejected`).
- Merge rules: workspace mode may only become stricter (`danger-full-access` → `workspace-write` → `read-only`); writableRoots / network.allow / model.allowedModels may only narrow; tools.disabled may only grow; approvalMode may only become stricter (`never` → `on-request` → `always`, where `always` means that every tool call requires human approval and is the strictest mode).

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
4. Harness policy
5. Session policy
6. Turn-scoped override

Lower levels may only narrow unless a higher level explicitly grants delegation.

## 8. Security and Permissions

- Unknown policy fields fail closed when they would affect security.
- The network allowlist is evaluated before outbound calls from the model/MCP proxy.
- Workspace paths are canonicalized before comparison.
- Policy compilation stores secret fingerprints, not values.
- Approval modes MUST map truthfully to adapter capabilities.
- Public error details use `safeReason`, not a raw denied path/header/URL when it is sensitive.

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
| Network URL fails allowlist | Reject before making an outbound connection |
| Approval required but no approval bridge exists | Reject or mark the task blocked; do not auto-approve |

## 11. Test Plan and Acceptance Criteria

- Unit: policy precedence, widening rejection, path canonicalization, and network URL validation.
- Adapter projection: Codex/Pi/OpenCode fixtures verify hard/advisory/unsupported declarations.
- Security: SSRF cases, private IP, metadata endpoint, and Unix socket and Docker socket denial.
- Integration: a session snapshot freezes policy, and a later harness update does not alter the active session.
- Review: any policy expansion requires a spec review and a security-boundary update.
