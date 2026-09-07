# Policy Controller 组件规格

[English](README.md) | **简体中文**

Status: Draft
Last reviewed: 2026-08-30
Related specs: [Security Boundary](../security-boundary/README.zh-CN.md), [Harness Registry](../harness-registry/README.zh-CN.md), [Session Runtime](../session-runtime/README.zh-CN.md)

## 1. 组件定位

Policy Controller 负责把 caller、tenant、workspace、configured harness、request override 和部署默认值编译成一次 session/turn 的有效策略。它是 HaaS 运行准入的唯一 policy owner。

## 2. 来源与依据

| 来源 | 采用内容 |
|------|----------|
| `mpa-codex-worker` policy controller | profile/workspace/model/MCP/tool/network/hook 动态策略和 fail-closed 原则 |
| Security Boundary | tool minimization、budget、object scope、provider URL allowlist |
| Sandbox Runtime | workspace/network/tool/approval 投影为 OpenSandbox sandbox/egress 配置 |
| OpenSandbox egress docs | 网络 egress policy 与 credential vault 分层 |

## 3. 上游与下游关系

| 方向 | 对象 | 关系 |
|------|------|------|
| 上游 | HaaS Protocol | 传入 request metadata、headers、task budgets |
| 上游 | Harness Registry | 读取 configured harness policy |
| 上游 | Session Runtime | 请求 session/turn effective policy |
| 下游 | Harness Adapter | 提供 adapter-specific policy projection |
| 下游 | Model Proxy | 限制模型和 provider route |
| 下游 | MCP / Tool / Skill Runtime | 限制 MCP、tools、skills |
| 下游 | Container Runtime | 限制 workspace、network、mount 和 process |

## 4. 职责边界

负责：

- 合并组织级、workspace 级、harness 级、session 级和 turn 级 policy。
- 校验 policy 只收窄权限；放宽必须有显式授权来源。
- 输出 `EffectivePolicy` 并冻结到 session/turn。
- 将通用 policy 投影为 adapter-specific 配置。
- 拒绝未知或不可安全实现的权限请求。
- 记录 policy decision 和 safe reason。

不负责：

- 不执行工具或网络请求。
- 不保存 credential value。
- 不根据 harness 文本输出动态放权。
- 不把 adapter 不支持的 hard block 默认为已 enforce。

## 5. 核心接口

```python
async def compile_policy(input: PolicyCompileInput) -> EffectivePolicy: ...
async def authorize_request(ctx: RequestContext, action: str, resource: str) -> PolicyDecision: ...
async def authorize_tool(policy: EffectivePolicy, tool: ToolRequest) -> PolicyDecision: ...
async def authorize_network(policy: EffectivePolicy, url: str) -> PolicyDecision: ...
async def authorize_workspace_path(policy: EffectivePolicy, path: str, access: str) -> PolicyDecision: ...
async def project_for_adapter(policy: EffectivePolicy, adapter_id: str) -> AdapterPolicyProjection: ...
```

## 6. 数据模型

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

### 6.1.1 PolicyCompileInput 与 PolicyLayer

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

- `layers` 从宽到窄排列（platform/tenant → workspace → harness → session → turn）。
- 每层的字段 `null` 表示该层不覆盖该维度，合并时跳过。
- `delegation: true` 表示该层显式授予其下所有层放宽该层约束的权利；未授予时，下层任何放宽都 fail closed（`PolicyWideningRejected`）。
- 合并规则：workspace mode 只能向更严格方向（`danger-full-access` → `workspace-write` → `read-only`）；workspace.root 和 writableRoots / network.allow / model.allowedModels 只能收窄；tools.disabled 只能增加；approvalMode 只能向更严格方向（`never` → `on-request` → `always`，`always` 表示每次工具调用都需人工批准，最严格）。只有下层 workspace.root 的规范化路径等于当前 workspace.root 的规范化路径，或位于其子路径内时，才视为收窄；`/ab` 相对 `/a` 这类字符串前缀匹配和 `/a/../..` 这类穿越形式不得绕过检查。

### 6.2 PolicyDecision


```json
{
  "allowed": false,
  "code": "haas_policy_denied",
  "safeReason": "network_host_not_allowed",
  "retryable": false
}
```

## 7. 运行模型与状态机

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

## 8. 安全与权限

- Unknown policy fields fail closed when they would affect security.
- Network allowlist is evaluated before model/MCP proxy outbound calls.
- Workspace paths are canonicalized before comparison.
- Policy compilation stores secret fingerprints, not values.
- Approval modes must map truthfully to adapter capabilities.
- Public error detail uses `safeReason`, not raw denied path/header/URL when sensitive.

## 9. 可观测性

- `haas.policy.compiled`
- `haas.policy.denied`
- `haas.policy.adapter_projection`
- `haas.policy.widening_rejected`
- `haas.policy.unknown_field`

Metrics:

- `haas_policy_decision_total{decision,reason}`
- `haas_policy_compile_duration_ms`
- `haas_policy_projection_total{adapterBase,status}`

## 10. 失败与恢复

| 场景 | 行为 |
|------|------|
| policy store unavailable before execution | fail closed |
| unknown security-affecting field | reject with `haas_policy_invalid` |
| adapter cannot enforce hard requirement | reject with `haas_policy_unsupported` |
| requested wider workspace root | reject unless higher-level delegation allows |
| network URL fails allowlist | reject before outbound connection |
| approval required but no approval bridge | reject or mark task blocked; do not auto-approve |

## 11. 测试计划与验收

- Unit：policy precedence、widening rejection、path canonicalization、network URL validation。
- Adapter projection：Codex/Pi/OpenCode fixtures verify hard/advisory/unsupported declarations。
- Security：SSRF cases、private IP、metadata endpoint、Unix socket and Docker socket denial。
- Integration：session snapshot freezes policy and later harness update does not alter active session。
- Review：any policy expansion requires spec review and security-boundary update。
