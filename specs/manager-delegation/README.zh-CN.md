# Manager Delegation 组件规格

[English](README.md) | **简体中文**

Status: Draft
Last reviewed: 2026-09-14
Change ID: manager-haas-delegation, unified-runtime-approval-policy
Related specs: [HaaS Protocol](../haas-protocol/README.zh-CN.md), [Session Runtime](../session-runtime/README.zh-CN.md), [Sandbox Runtime](../sandbox-runtime/README.zh-CN.md), [Policy Controller](../policy-controller/README.zh-CN.md), [Model Proxy](../model-proxy/README.zh-CN.md), [Container Runtime](../container-runtime/README.zh-CN.md), [Stores](../stores/README.zh-CN.md)

## 1. 组件定位

Manager Delegation 定义 manager 应用把 HaaS 作为完整远端执行后端使用时的合同。manager 继续负责面向用户的意图路由、项目授权、配置选择、审批 UI 和 session ownership。HaaS 负责通过 sidecar、Session Runtime、Codex app-server adapter、Sandbox Runtime、事件流、model proxy 和容器生命周期执行已接受的委派任务。

HaaS 不得作为 manager 内部的 shell `Executor` 接入。HaaS 是完整执行后端，拥有自己的 session、turn、event、cancel、approval relay、recovery 和 sandbox 边界。

首期委派目标：

```text
Manager session / turn
  -> deterministic delegation router
  -> HaaS delegated session binding
  -> one HaaS container per delegated session
  -> Codex app-server adapter inside the HaaS runtime
  -> project mounted at /workspace
```

## 2. 来源与依据

| 来源 | 采纳内容 |
|------|----------|
| manager 代码分析 | 当前 manager 直接创建本地 `TurnEngine`，并负责 provider setup、workspace roots、session API、WebSocket streaming、审批和本地权限检查。 |
| 已确认产品决策（2026-09-08） | 每个 delegated session 一个容器；连续追问复用容器/thread；空闲 TTL 只销毁运行资源；后续追问恢复之前权限/配置/mount；首次 HaaS invocation accepted 时固定 session binding；授权项目根目录 `rw` 挂到 `/workspace`；同一 canonical workspace 同时只允许一个 active `rw` delegated session。 |
| HaaS Architecture | HaaS 暴露 ADK-compatible HTTP/SSE 与 HaaS native control-plane API；Codex 是首期 P0 harness runtime。 |
| Security Boundary / Policy Controller | fail closed、secretless credential、canonical path 校验、权限扩大必须有显式授权。 |

## 3. 上游与下游关系

| 方向 | 组件 | 关系 |
|------|------|------|
| 上游 | Manager | 负责意图识别、用户授权、UI、审批和 delegated-session binding。 |
| 下游 | HaaS Protocol | 提供 manager-facing control-plane API 与 ADK `/run_sse` 执行。 |
| 下游 | Session Runtime | 拥有 HaaS session、invocation、Codex thread reference、idempotency 和终态。 |
| 下游 | Policy Controller | 校验 delegated policy snapshot，拒绝未授权扩大。 |
| 下游 | Sandbox Runtime | 把 manager 授权的 mount 和 policy 物化成执行 sandbox。 |
| 下游 | Model Proxy | 使用 manager 传入的 `credentialRef` 为 HaaS 容器提供 secretless 模型访问。 |
| 下游 | Event Log & SSE | 流式输出执行、排队、审批、恢复和终态事件。 |
| 下游 | Container Runtime | 按强制 `linux/amd64` 平台合同启动和停止 HaaS sidecar/container image。 |

## 4. 职责边界

Manager 职责：

- 判断某个 turn 使用本地执行还是 HaaS 委派。
- 使用确定性规则。创建 delegated-session contract 只产生 `PREPARED` candidate；首个 HaaS InvocationRecord 达到持久 `accepted` 时才把 manager session 持久化为 `HAAS_BOUND`，后续 turn 停止重新分类。
- 持久化 delegated-session contract：manager session id、HaaS session id、harness id、image digest、model/provider credential reference、mount manifest、delegation policy snapshot 和审计事件。
- 向 HaaS 发送 mount manifest 前，先 canonicalize 并授权 host path。
- 为后续恢复保留用户显式授权，但每次 restore 前重新校验路径存在性、canonical path、类型和 symlink 安全。
- 作为唯一用户审批入口。
- 对 `rw` delegated session 执行 workspace 级 single-writer admission。

HaaS 职责：

- 只接受 manager 已授权的 delegation manifest 与 policy snapshot。
- 通过 HaaS session 执行委派工作，而不是通过 manager shell execution。
- Codex 原生协议细节只存在于 Codex adapter 内。
- 用持久存储保留 session、invocation、event、idempotency、approval 与 recovery 状态。
- 提供 live SSE event、replay、cancel、approval relay、status 与安全错误。
- 当 delegated environment 无法安全创建或恢复时 fail closed。

非职责：

- HaaS 不做 manager 侧意图识别。
- HaaS 不持久化来自 manager 的真实 provider API key。
- HaaS 不自行扩大 mount、workspace 权限、network access、tool 或 approval grant。
- manager 不解析 Codex app-server JSON-RPC，也不把 Codex 原生 thread id 当作 public API 依赖。

## 5. 核心接口

### 5.1 Manager-facing HaaS Native API

以下 endpoint 是 HaaS native extension，不得重新定义 ADK 字段语义：

| Method | Path | 用途 |
|--------|------|------|
| POST | `/v1/haas/delegated-sessions` | 创建或幂等复用 `prepared` delegated-session candidate；不提交 manager chat binding。 |
| GET | `/v1/haas/delegated-sessions/{delegated_session_id}` | 读取 delegated-session 状态、policy snapshot、mount manifest 与 runtime status。 |
| POST | `/v1/haas/delegated-sessions/{delegated_session_id}/restore` | 在 TTL 清理或进程重启后，按持久合同重建运行资源。 |
| POST | `/v1/haas/delegated-sessions/{delegated_session_id}/policy` | 显式更新或 rebind 后续 turn 使用的 policy snapshot。 |
| GET | `/v1/haas/sessions/{session_id}/invocations/{invocation_id}` | 读取权威 accepted/running/terminal invocation state。 |
| GET | `/v1/haas/sessions/{session_id}/events-page` | 读取恢复所需有界 canonical history。 |
| GET | `/v1/haas/sessions/{session_id}/approvals?status=waiting` | 重建 pending approval；Codex P0 `unattended_only` 通常为空。 |
| POST | `/v1/haas/sessions/{session_id}/approvals/{approval_id}` | 把 manager 审批结果回传给等待中的 invocation/action。 |

执行通过 ADK-compatible `/run_sse` 或 `/run`，使用 prepared candidate 的 `appName`、`userId`、`sessionId`；首个 invocation 持久 acceptance 提交 binding。Manager 使用 HaaS native session/event API 做 replay、status、cancel、diagnostics。Local 与 delegated mode 的 Pause/Continue 使用相同 HaaS-native invocation API。Control sidecar 把 pause 转发到当前 worker generation，保留 session volume 与 native continuation state，并等待权威 interrupted readback。Continue 恢复记录的 generation 或经验证的后继 generation，再把新 worker execution 映射到新 public invocation；禁止 fallback 到 host-local execution。

delegated session 的运行配置由 manager 授权的 mount manifest、delegation policy snapshot 与 profile reference 拥有，仅通过 `POST /v1/haas/delegated-sessions/{delegated_session_id}/policy` 变更。该端点支持任意组合的部分更新：`profileRef`（provider/MCP/skills/AGENTS.md/workspace policy/budget 快照）、`delegationPolicySnapshot`（idle TTL、并发、队列、restore 策略）、`mountManifest`（已授权挂载）、`image`（已授权运行资源替换）。更新对同一 delegated session 与同一 manager chat binding 就地生效，manager 不得为改配置新建会话。非 delegated 的 `POST /v1/haas/sessions/{session_id}/profile-rebind` 路径不得用于 delegated session；该请求返回 `409 haas_profile_rebind_unsupported`。

### 5.1.1 配置应用屏障

至少提供一个完整域：`profileRef`、`delegationPolicySnapshot`、`mountManifest`、`image`。提供即完整替换，省略保留，空数组清列表，null 非法；`expectedRevision` 对 legacy caller 可选，Manager 控制的交互式变更必须携带，stale 值返回 `haas_policy_revision_conflict`；profileRef 包含完整 id/version/fingerprint。接受前验证 scope、harness/base、内容身份与授权。有效 delegated provider 从管控覆盖后的选中 profile 派生；create 输入必须匹配，应用 profile 时原子刷新。

`desiredRevision`、`appliedRevision` 初始为 1。每次接受不同更新递增 desiredRevision，持久化完整解析目标及授权证据。已应用返回 200，否则 202 并含 `pendingPolicyUpdate={updateId,revision,requestedAtMs,fields}`。仅因忙碌不返回 409；非法输入、越权与幂等冲突仍明确失败。

提交后立即启动受 fencing 保护的 per-session reconciler，不依赖下一 turn。等待 accepted/running/cancelling 工作、restore 和前序应用结束；材料化内容、更新 proxy route，必要时重启 native process/重建资源，保留逻辑 session/history/volume。只有 runtime readback 验证成功才推进 appliedRevision，原子更新全部 profile/provider/materialization fingerprint。记录 `lastPolicyUpdateResult={updateId,revision,status:applied|failed,code?,safeReason?}`；失败必带 code/safeReason，不推进 appliedRevision。

并发目标串行接受，完整域按最后到达覆盖，合并后整体验证。等待 N 的条件是 appliedRevision >= N，表示包含 N 的最新管控配置生效，不表示每个中间值执行过。Update id/revision/hash receipt 保留至 session retention，24h HTTP cache 过期后旧重试不能重施旧配置。

新发送在 acceptance/native execution 前等待 desiredRevision 应用。Manager 使用持久本地 send queue；直接请求竞态走有界 admission。Reconciliation 不等下一 turn 触发，避免死锁。应用默认最多等待 300 秒；失败/超时保留旧 applied 配置、记录现有稳定 HaaS 错误并阻止未来 turn。修正/重试意图产生更高 revision，不回滚 profile version、不新建 chat。Pending 为 null 不代表成功，应比较 revision/result；delete 取消 pending；restart 检查持久 runtime generation/receipt，不靠内存标记。

### 5.2 Manager Provider 配置

manager provider catalog 应区分：

| Provider id | Endpoint | 用途 |
|-------------|----------|------|
| `ark` | `https://ark.ap-southeast.bytepluses.com/api/v3` | BytePlus Ark global data plane。 |
| `volcengine-ark` | `https://ark.cn-beijing.volces.com/api/v3` | 火山方舟中国区标准数据面。 |
| `ark-agent-plan-cn` | `https://ark.cn-beijing.volces.com/api/plan/v3` | 火山方舟 Agent Plan API。 |

`volcengine-ark` 必须作为独立 provider identity 建模，不得与 BytePlus Ark 或 Ark Agent Plan 共用配置身份。manager 本地执行与 HaaS 委派都只通过 provider id、model id 和 `credentialRef` 引用 provider；双方都不把 raw API key 复制进 session contract。

## 6. 数据模型

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
  "binding": "prepared",
  "image": {
    "reference": "registry.example.com/haas@sha256:...",
    "digest": "sha256:...",
    "variant": "lite"
  },
  "profileRef": {
    "profileId": "hprof_abc",
    "profileVersion": 12,
    "profileFingerprint": "sha256:profile"
  },
  "provider": {
    "providerId": "volcengine-ark",
    "name": "volcengine-ark",
    "wireApi": "responses",
    "model": "doubao-seed-1-6",
    "credentialRef": "secret://manager/provider/volcengine/default"
  },
  "workspaceMode": "bind_mount",
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
    "mountPolicy": "project_rw_extra_ro",
    "network": {"defaultAction": "allow", "allow": []},
    "tools": {"disabled": [], "approvalMode": "on-request"}
  },
  "acceptedInvocationId": null,
  "bindingAcceptedAtMs": null,
  "bindingFailureSafeReason": null,
  "runtime": {
    "status": "idle",
    "containerId": null,
    "containerGeneration": 3,
    "lastStartedAtMs": 1786400000000,
    "lastActiveAtMs": 1786401000000
  },
  "desiredRevision": 1,
  "appliedRevision": 1,
  "lastPolicyUpdateResult": null,
  "pendingPolicyUpdate": null,
  "createdAtMs": 1786400000000,
  "updatedAtMs": 1786401000000
}
```

必填 desiredRevision/appliedRevision 初始为 1，lastPolicyUpdateResult 初始 null。Pending summary 包含 updateId/revision/requestedAtMs/fields，private store 保存完整目标。Pending null 不能证明成功，应比较 appliedRevision/desiredRevision 和 last result。Reconciliation 不等下一 invocation 触发。

合同字段是持久事实。Binding invariant：

- `prepared`：`acceptedInvocationId`、`bindingAcceptedAtMs`、failure reason 都为 null；
- `haas_bound`：accepted invocation id 与 acceptance timestamp 必填，failure reason 为 null；
- `bind_failed`：不得记录 accepted invocation，且安全 failure reason 必填；
- `prepared` 到 `haas_bound` 的提升与观察/持久化首个 invocation acceptance 原子且幂等；
- accepted terminal failure 不得把 `haas_bound` 降级。

`containerId`、进程 id、临时端口和内存锁是运行时观测值，不能成为唯一恢复依据。

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
  "mountPolicy": "project_rw_extra_ro",
  "network": {"defaultAction": "allow", "allow": []},
  "tools": {"disabled": [], "approvalMode": "on-request"}
}
```

配置分三层：

1. 全局默认策略。
2. Workspace/project override。
3. Delegated session 创建时策略快照。

已有 delegated session 默认使用创建时快照。后续全局或 workspace 配置变更只影响新 session。改变已有 delegated session 需要显式 policy update/rebind 操作，并记录审计事件。
新 delegated session 使用 OpenHarness 默认值：允许公网访问且
`approvalMode=on-request`；P0 primary workspace mount 为 `rw`。这不允许
private/link-local/metadata/control-plane/cross-session 路由，也不允许 mount manifest
以外的 host path。网络与审批仍是用户拥有的显式设置。已有 delegated session 的变更必须在下一次
invocation 前通过 revision 屏障完整替换 policy snapshot。运行时若不能执行所请求的网络
模式，必须返回 `haas_policy_unsupported`，不得替换为无限制 Docker 网络。
为保证向后安全，已经创建且缺少 `network` 的旧 snapshot 仍按 deny 与空 allow list
归一化，缺少 `tools` 的旧 snapshot 保持 no-prompt；只有新 session 创建和显式的一次性
manager config migration 使用新默认值。
已持久化但缺少完整 policy snapshot 的 delegated binding 不得按当前较宽松默认值重新解释；
Manager 必须 fail closed，并要求显式 rebind/migration。

Manager 侧委派由独立的 `[haas_delegation]` 配置命名空间控制。在 OpenHarness
桌面产品 profile 中，HaaS 默认启用，使用 `mode=local_managed`、
`execution_mode=local_api` 且 `local_autostart=true`；该默认路径使用内置非容器化
HaaS local API，不得创建 delegated session 或容器。Delegated container execution
需要显式选择 `execution_mode=delegated_session`。独立部署的通用 HaaS 服务可在
operator 启用前保持 delegated container backend 关闭；只有显式 delegated-session
启动 profile 会启用所需的 delegated-container backend。项目本地 workspace 配置不得启用或禁用 HaaS 委派、
修改 HaaS endpoint、修改镜像或 harness 默认值、选择本地执行，也不得放宽 mount
policy。首个 HaaS invocation accepted 时，prepared contract 原子提升为 `haas_bound`，manager 必须在 session binding 中记录
非 secret 的配置快照（`haas_base_url`、HaaS user id、harness id/base、image
reference、image digest、provider id/model、mount manifest、delegation policy
snapshot）。API token 或其他凭证不得持久化到 binding；运行时调用从当前
用户拥有的 config/secret 状态解析。

Manager 实现可以暴露面向用户的 settings API 管理该命名空间。这些 API
必须拆分 secret 与非 secret 材料：

- 非 secret 字段（`enabled`、endpoint URL、HaaS user id、harness id/base、
  image reference/digest、本地开发未 pin 镜像开关、strategy、allowlist、显式本地
  执行偏好和 TTL 默认值）可以存入用户拥有的 prefs 或用户全局配置。Trigger
  keywords 可作为 deprecated migration input 保留，但不得在默认 OpenHarness
  profile 中作为路由门禁。
- `api_token` 或等价 bearer credential 必须通过 manager 的 secret store
  保存，settings 读取 API 不得返回这些值。
- settings 变更只影响新的未绑定 session。已有 `HAAS_BOUND` session 保留
  binding 快照，除非执行显式 policy update/rebind 操作。

Manager 实现必须在默认 OpenHarness GUI 启动路径中监督一个本地 HaaS sidecar，
使桌面 app 无需用户手动启动第二个 HaaS 进程即可接入。该本地监督能力由非 secret
设置（`local_autostart`，在 OpenHarness 产品 profile 中默认 `true`）控制，并且只
允许作用于 loopback HaaS URL（`127.0.0.1` 或 `localhost`）。Manager 传给本地进程的环境
变量只能包含非 secret 运行配置；委派 API token 仍保存在 manager secret store，
只作为出站 `Authorization` header 使用。如果启用了 local autostart 但 sidecar
无法启动或 `/v1/haas/health` 不通过，manager settings API 必须返回安全的本地
状态；委派 turn 继续通过既有 HaaS 委派错误路径 fail closed。生产委派仍必须
要求镜像 digest。本地开发流程可以显式设置 `allow_unpinned_local_image=true` 来
运行 `haas:local` 这类本地 tag；该 override 必须显式开启，且不得由 workspace
本地配置启用。

### 6.3 Mount Manifest 规则

- **P0 workspace mode 仅支持 `bind_mount`**，且只适用于与 manager 同 host 的 `local_managed` sidecar。已授权项目根目录 `rw` 挂载到 `/workspace`。
- 额外授权目录默认 `ro`，使用 `/mnt/extra/*` 下的确定性路径。
- 额外 mount 从 `ro` 升级到 `rw` 需要新的显式授权和 policy update/rebind。
- manager 不得授权用户 HOME、超出项目授权的父目录、Docker socket、SSH 目录、credential store 或未请求路径。
- HaaS 容器必须有独立可写 HOME、cache root 和 `/tmp`，确保 build、依赖安装和工具执行不依赖挂载 host HOME。
- `snapshot_upload`、`remote_workspace` 是 P1/spec-only。P0 remote sidecar 必须把它们标为 `unsupported`；OpenHarness 在 delegated-session create 前阻断 local-project execution，UI 不提供这些 mode 的选择动作。

## 7. 运行模型与状态机

### 7.1 Manager Binding

```text
UNBOUND -> PREPARING_HAAS -> PREPARED
PREPARED -> HAAS_BOUND          // 首个 InvocationRecord accepted
PREPARED -> BIND_FAILED         // pre-acceptance failure
BIND_FAILED -> PREPARING_HAAS   // retry 同一 idempotent contract
UNBOUND -> LOCAL_BOUND          // 显式 local choice

HAAS_BOUND 跨 TTL cleanup、process restart、accepted terminal failure、follow-up turn 持续。
```

规则：

- Existing binding、显式 local choice、workspace authorization 与 capability check 都采用确定性规则。模型意图识别与 trigger keyword 不得作为默认 HaaS 路由门禁。
- 符合条件的 unbound session 按产品默认值选择 HaaS。Delegated-session create/restore 本身不绑定 chat；首个 InvocationRecord 持久 accepted 时 binding 才提交。Pre-acceptance failure 使 chat 保持 unbound 或 `BIND_FAILED`，retry 复用同一 prepared contract/idempotency key。Accepted failed/incomplete/cancelled turn 仍保持 `HAAS_BOUND`，因为执行可能已产生副作用。
- `HAAS_BOUND` session 不静默回退本地执行。
- 用户可手动新建本地 session 或显式 rebind policy，但这是新的可审计决策。

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

规则：

- 一个 delegated session 同时拥有一个 active container。
- 同一 delegated session 的连续追问在容器存活时复用同一个容器和 Codex thread。
- idle TTL 默认 30 分钟，可配置。
- 最大容器存活默认 8 小时，可配置。active turn 可以完成，但达到最大存活时间后容器必须拒绝新 turn。
- TTL 清理只销毁运行资源，不删除 delegated-session contract、HaaS session、event log、approval history 或 host file。
- 后续追问在校验 mount manifest 和 policy snapshot 后，按存储合同恢复容器。

### 7.3 Workspace Single-Writer Admission

```text
rw turn requested
  -> canonical workspace lock available? yes -> execute
  -> no -> queued_for_workspace_lock
```

规则：

- 一个 canonical workspace 同时只能有一个 active `rw` delegated session。
- 同一 canonical workspace 的其他 `rw` turn 按 FIFO 排队。
- `ro` delegated session 可以并发。
- Queue status 必须通过结构化事件可见，包含 queue reason 和可用时的 position。
- crashed 或 expired container 必须在 store 确认 terminal 或 cleanup 状态后释放 workspace lock。

## 8. 安全与权限

- manager 是唯一用户审批入口。HaaS 发出带 validated safe `haas` metadata 的 native `type=haas.approval.required`；manager 展示、记录决策并回传结果。
- HaaS 不得存储 raw provider key；只接收 `credentialRef` 或短期 runtime token。
- HaaS 不得自行扩大 mount、workspace 权限、network access 或 tool 权限。
- Restore 重新校验 canonical path、路径存在性、类型、symlink 边界和 access mode。任意不匹配都 fail closed。
- HaaS 不可用、image 不可用、container startup failure、mount validation failure、path drift 和 workspace-lock denial 都用结构化错误与下一步动作 fail closed。
- delegated container 不得挂载 Docker socket、用户 HOME、SSH 目录、credential 目录或未批准 host path。

## 9. 可观测性

Events/logs：

- `haas.delegation.session_created`
- `haas.delegation.session_bound`
- `haas.delegation.policy_snapshot`
- `haas.delegation.policy_updated`
- `haas.delegation.policy_update_pending`
- `haas.delegation.policy_update_applied`
- `haas.delegation.policy_update_failed`
- `haas.delegation.restore_started`
- `haas.delegation.restore_failed`
- `haas.delegation.container_ttl_destroyed`
- `haas.delegation.workspace_lock_queued`
- `haas.delegation.workspace_lock_acquired`
- `haas.delegation.workspace_lock_released`
- `haas.approval.required`
- `haas.approval.resolved`

Metrics：

- `haas_delegated_sessions_total{status}`
- `haas_delegated_containers_active`
- `haas_delegated_restore_total{status,reason}`
- `haas_workspace_lock_queue_depth`
- `haas_workspace_lock_wait_ms`
- `haas_approval_wait_ms{status}`

日志和事件必须使用安全 id、credential fingerprint、必要时脱敏的路径与 safe reason。不得包含 raw prompt、完整 tool argument、provider key、Authorization 值、cookie 或 presigned URL。

## 10. 失败与恢复

| 场景 | 行为 |
|------|------|
| `HAAS_BOUND` session 对应 HaaS 不可用 | fail closed；不本地执行；返回 retryable delegated-backend error。 |
| 容器镜像不可用 | fail closed；报告 image unavailable 和下一步动作。 |
| 容器启动失败 | fail closed；保留 contract 与终态错误证据。 |
| idle TTL 到达 | 销毁运行资源；保留持久 session contract 与事件。 |
| TTL 清理后继续追问 | 校验 mount、policy、provider reference 与 image digest 后按 contract restore。 |
| Mount path 不存在或 canonical path 变化 | fail closed；要求重新授权或 policy rebind。 |
| 同一 workspace 已有 active `rw` session | 按 policy FIFO 排队或返回 queue-full/timeout。 |
| Approval bridge 不可用 | invocation 标记 blocked 或 failed；不得 auto-approve。 |
| 全局 policy config 变化 | 已有 session 保留 snapshot；新 session 使用新 policy。 |
| 显式 policy update 校验失败 | 拒绝；保留之前 policy snapshot。 |
| 显式 policy update 到达时有 running invocation | 接受已验证 desired revision 并返回 `202`；reconciler 等当前工作结束，不依赖下一 turn 完成应用并记录 applied/failed 结果；running invocation 不变。 |
| Manager 需在会话中变更 MCP/skill/AGENTS.md/provider | 更新 profile 后 `POST /policy` 附带新的 `profileRef`；HaaS 就地替换有效快照；manager chat binding、delegated session id、workspace lock 保持不变。 |
| Policy 应用失败或超时 | `appliedRevision` 不变，记录 `lastPolicyUpdateResult=failed`，发出 `haas.delegation.policy_update_failed`，修正后的更高 revision 生效前阻止新 turn。 |

## 11. 测试计划与验收

- Integration：暂停 delegated invocation，观测唯一 interrupted terminal，恢复同一 session/runtime volume，以关联的新 invocation 继续，并验证重复 key 不会启动第二个 worker execution。

- Unit：delegation policy merge、session snapshot 行为、path canonicalization、mount manifest validation 和 deterministic route decision，包括 OpenHarness 默认 HaaS `local_managed` + autostart 且不依赖 trigger keyword 的路由。
- Unit：provider catalog 包含独立的 `volcengine-ark`、`ark`、`ark-agent-plan-cn` 身份，且不共享 credential config。
- Integration：创建 PREPARED delegated session，启动 `/run_sse`，收到 accepted response header 后原子提升 HAAS_BOUND，实时流式输出 event，读取 invocation/terminal state 与有界 history，并继续同一 session。Pre-acceptance failure 记录 BIND_FAILED 且不绑定。
- Integration：TTL 销毁容器，后续追问用相同 mount/policy/provider contract 重建容器，并延续 Codex 的 HaaS session。
- Integration：running/cancelling 时接受的配置更新不依赖下一 turn 自动 reconciliation；重启后等待发送也只以验证后的 applied revision 执行一次。
- Failure：应用失败/超时保持 applied revision，记录 typed failed result/event，并阻止未来 turn，直到修正后的更高 revision 成功。
- Concurrency：同一 canonical workspace 的两个 `rw` delegated session 串行；`ro` session 并发。
- Security：拒绝 HOME、Docker socket、SSH、父目录、symlink escape 和未授权 mount widening。
- Approval（P1）：API/store/event 合同保留，但 Codex P0 声明 `unattended_only`；human approval UI/relay 不作为 P0 release gate，只有 capability mode=`human_bridge` 时才显示。
- Failure：HaaS unavailable、image unavailable、mount drift、container startup failure、queue timeout 都 fail closed，不本地 fallback。
- E2E：显式开启 Docker/Codex flag 后，对授权项目目录以 `/workspace:rw` 运行真实 delegated coding task。
