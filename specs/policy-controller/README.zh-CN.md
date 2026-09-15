# Policy Controller 组件规格

[English](README.md) | **简体中文**

Status: Draft
Last reviewed: 2026-09-14
Change ID: unified-runtime-approval-policy
Related specs: [Security Boundary](../security-boundary/README.zh-CN.md), [Harness Registry](../harness-registry/README.zh-CN.md), [Harness Profile](../harness-profile/README.zh-CN.md), [Session Runtime](../session-runtime/README.zh-CN.md), [Manager Delegation](../manager-delegation/README.zh-CN.md)

## 1. 组件定位

Policy Controller 负责把 caller、tenant、workspace、configured harness、request override 和部署默认值编译成一次 session/turn 的有效策略。它是 HaaS 运行准入的唯一 policy owner。

## 2. 来源与依据

| 来源 | 采用内容 |
|------|----------|
| `mpa-codex-worker` policy controller | profile/workspace/model/MCP/tool/network/hook 动态策略和 fail-closed 原则 |
| Harness Profile | profile revision 中的 workspace/network/tool/approval/model policy layer |
| Security Boundary | tool minimization、budget、object scope、provider URL allowlist |
| Sandbox Runtime | workspace/network/tool/approval 投影为 OpenSandbox sandbox/egress 配置 |
| OpenSandbox egress docs | 网络 egress policy 与 credential vault 分层 |

## 3. 上游与下游关系

| 方向 | 对象 | 关系 |
|------|------|------|
| 上游 | HaaS Protocol | 传入 request metadata、headers、task budgets |
| 上游 | Harness Registry / Harness Profile | 读取 configured harness identity 与 active profile policy |
| 上游 | Session Runtime | 请求 session/turn effective policy |
| 下游 | Harness Adapter | 提供 adapter-specific policy projection |
| 下游 | Model Proxy | 限制模型和 provider route |
| 下游 | MCP / Tool / Skill Runtime | 限制 MCP、tools、skills |
| 下游 | Container Runtime | 限制 workspace、network、mount 和 process |

## 4. 职责边界

负责：

- 合并组织级、workspace 级、harness 级、session 级和 turn 级 policy。
- 对 manager-delegated session，合并 delegation policy 层，并在 delegated session
  创建时冻结 effective delegation policy snapshot。
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
- 不决定 manager 侧意图路由；manager 授权后只校验 policy 与 mount contract。

## 5. 核心接口

```python
async def compile_policy(input: PolicyCompileInput) -> EffectivePolicy: ...
async def authorize_request(ctx: RequestContext, action: str, resource: str) -> PolicyDecision: ...
async def authorize_tool(policy: EffectivePolicy, tool: ToolRequest) -> PolicyDecision: ...
async def authorize_network(policy: EffectivePolicy, url: str) -> PolicyDecision: ...
async def authorize_workspace_path(policy: EffectivePolicy, path: str, access: str) -> PolicyDecision: ...
async def authorize_mount_manifest(policy: EffectivePolicy, manifest: MountManifest) -> PolicyDecision: ...
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

### 6.1.1 PolicyCompileInput 与 PolicyLayer

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

- `layers` 从宽到窄排列（platform/tenant → workspace → harness → session → turn）。
- 每层的字段 `null` 表示该层不覆盖该维度，合并时跳过。
- `delegation: true` 表示该层显式授予其下所有层放宽该层约束的权利；未授予时，下层任何放宽都 fail closed（`PolicyWideningRejected`）。
- 新建交互式 session 默认使用 `workspace-write`、公网
  `defaultAction=allow` 与 `approvalMode=on-request`。公网允许仍阻断 loopback、
  private/link-local、metadata、control-plane 和跨 session 目标，除非另有独立授权的
  内部路由；它绝不隐含 credential 访问权。
- `approvalMode` 不是线性权限等级。`always` 对每个可审批动作询问；`on-request`
  只在当前 sandbox/policy 无法直接放行动作时询问；`never` 禁止询问，因此任何需要 grant
  的动作都直接拒绝。尤其是 `never` 不代表无限制执行。从 `never` 改成 `on-request`，或减少
  强制询问，都需要显式 caller authority 和持久 policy revision；改成 `never` 是关闭审批通道的
  fail-closed 收窄。
- 合并规则：workspace mode 只能向更严格方向（`danger-full-access` →
  `workspace-write` → `read-only`）；workspace.root 和 writableRoots / network.allow /
  model.allowedModels 只能收窄；tools.disabled 只能增加。approval mode 使用上述授权矩阵，
  不再按 rank 比较。只有下层 workspace.root 的规范化路径等于当前 workspace.root 的规范化路径，
  或位于其子路径内时，才视为收窄；`/ab` 相对 `/a` 这类字符串前缀匹配和
  `/a/../..` 这类穿越形式不得绕过检查。

### 6.3 Approval Request 与 Grant

Approval request 是类型化授权挑战，不是 harness 自然语言。它包含稳定 `approvalId`、
invocation/action identity、脱敏 action/resource 摘要、policy reason、服务端声明的
decision/scope、expiry 与 effective policy revision。P0 默认 `scope=action`；只有 adapter
能按精确 action/resource fingerprint 执行时，才可声明有界 `scope=invocation`。任何 approval
grant 都不能跨 session。

批准后，只向当前 invocation 的私有 approval ledger 追加 immutable grant，并恰好一次恢复同一
native request；它不改写 session policy。拒绝、过期、取消、重复冲突或无法验证的请求均
fail closed。Platform hard deny、credential 边界、host/private-network 保护、mount root 和
adapter 不支持的能力均不可通过审批绕过。

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
4. Harness profile policy
5. Session policy
6. Turn-scoped override

Lower levels may only narrow unless a higher level explicitly grants delegation.
Harness Profile policy 是 harness-level 动态配置的唯一入口。激活新 profile 只影响新
session；已有 session 使用冻结的 EffectivePolicy。显式 profile rebind 必须重新编译
policy，并按同样的 widening 规则校验；失败时保持旧 snapshot。

Delegation policy 使用相同的宽到窄顺序。编译后的 delegation policy 会快照进
delegated session。后续全局/workspace 配置变更不会改变已有 delegated session，
除非 HaaS native policy update/rebind 请求被显式授权并记录。

Workspace 写入准入属于 delegation policy：同一 canonical workspace 同时只允许一个
active `rw` delegated session。其他 `rw` turn 按 `queuePolicy=fifo` 排队；`ro`
session 可以并发。

Local 与 delegated session 统一使用同一个 policy revision barrier。授权 mutation 持久化
`desiredRevision`、预期旧 revision、完整变更 domain、actor 与 safe reason。Running invocation
保持 immutable `appliedRevision`；下一 invocation 必须等待 `appliedRevision >= desiredRevision`。
应用失败保留旧 applied snapshot，并阻断排队工作，不能用旧 policy 偷跑。Mode、network 和
workspace 变更都走该路径，不能只是 frontend 本地状态。

## 8. 安全与权限

- Unknown policy fields fail closed when they would affect security.
- Network allowlist is evaluated before model/MCP proxy outbound calls.
- Allowlist entry 和请求 URL 在比较前必须规范化。若 allowlist entry
  显式指定端口，请求的有效端口（显式端口或 scheme 默认端口：HTTP 为
  `80`，HTTPS 为 `443`）必须严格相等。例如，`http://host:8080`
  不得放行 `http://host` 或 `http://host:80`。若 allowlist entry
  未指定端口，则只允许该 scheme 的默认端口：`http://host` 允许
  `http://host` 与 `http://host:80`，但不得允许 `http://host:8080`；
  `https://host` 允许 `https://host` 与 `https://host:443`，但不得允许
  `https://host:8443`。
- Workspace paths are canonicalized before comparison.
- Policy compilation stores secret fingerprints, not values.
- Approval modes must map truthfully to adapter capabilities.
- Approval grant 只能授权 compiled policy 明确标记为 approval-eligible 的动作；platform hard
  deny 与 adapter/runtime 无法实现的能力不可被审批覆盖。
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
| delegated mount manifest widens access | reject with `haas_delegation_mount_invalid` |
| existing delegated session receives implicit policy drift | reject; require explicit policy update/rebind |
| profile rebind would widen policy without authorization | reject; keep the previous effective policy snapshot |
| same canonical workspace already has active `rw` delegated session | queue or return `haas_workspace_lock_busy` / `haas_workspace_lock_timeout` according to policy |
| network URL fails allowlist | reject before outbound connection |
| approval required but no approval bridge | reject or mark task blocked; do not auto-approve |
| `approvalMode=never` 下动作需要 grant | 使用稳定 policy-denied reason 拒绝，不发 request |
| Policy update 与 running invocation 竞态 | 当前 invocation 保持 applied revision；下一 invocation 等待新 revision |
| Approval 过期或所属 invocation 已 terminal | 关闭为 expired/cancelled，原生 request 绝不重复回答 |

## 11. 测试计划与验收

- Unit：policy precedence、widening rejection、path canonicalization、network URL validation。
- Adapter projection：Codex/Pi/OpenCode fixtures verify hard/advisory/unsupported declarations。
- Security：SSRF cases、private IP、metadata endpoint、Unix socket and Docker socket denial。
- Integration：session snapshot freezes policy and later harness update does not alter active session。
- Integration：delegated-session policy snapshot 不受后续全局/workspace 配置变更影响，直到显式 policy update/rebind。
- Integration：profile activate 只影响新 session；profile rebind 重新编译 policy，并拒绝未授权放宽。
- Concurrency：每个 canonical workspace 一个 active `rw` delegated session；`ro` delegated session 仍可并发。
- Review：any policy expansion requires spec review and security-boundary update。
- Defaults：fresh session 编译为 `workspace-write + 公网 allow + on-request`，同时继续阻断
  private/metadata/control route 与 platform hard deny。
- Approval matrix：验证 `always`、`on-request`、`never` 的 ask/execute/deny 行为，且 `never`
  绝不变成 unrestricted execution。
- Revision barrier：running turn 中修改 approval、network 与 workspace，证明当前 invocation
  保持旧 revision，下一 invocation 等待并使用新 revision。
- E2E：需要 escalation 的命令产生一条持久脱敏 request、一次 decision、一次 native response，
  并在断线重连后续接同一 invocation。
