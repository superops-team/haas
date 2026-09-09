# Manager Delegation 组件规格

[English](README.md) | **简体中文**

Status: Draft
Last reviewed: 2026-09-08
Change ID: manager-haas-delegation
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
| 已确认产品决策（2026-09-08） | 每个 delegated session 一个容器；连续追问复用容器/thread；空闲 TTL 只销毁运行资源；后续追问恢复之前权限/配置/mount；首次成功委派后固定 session binding；授权项目根目录 `rw` 挂到 `/workspace`；同一 canonical workspace 同时只允许一个 active `rw` delegated session。 |
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
- 确定性规则优先；首次成功委派后，把 manager session 绑定到 HaaS，后续 turn 不再重新分类。
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
| POST | `/v1/haas/delegated-sessions` | 从 manager contract 创建或绑定 HaaS delegated session。 |
| GET | `/v1/haas/delegated-sessions/{delegated_session_id}` | 读取 delegated-session 状态、policy snapshot、mount manifest 与 runtime status。 |
| POST | `/v1/haas/delegated-sessions/{delegated_session_id}/restore` | 在 TTL 清理或进程重启后，按持久合同重建运行资源。 |
| POST | `/v1/haas/delegated-sessions/{delegated_session_id}/policy` | 显式更新或 rebind 后续 turn 使用的 policy snapshot。 |
| POST | `/v1/haas/sessions/{session_id}/approvals/{approval_id}` | 把 manager 审批结果回传给等待中的 invocation/action。 |

执行仍使用 ADK-compatible `/run_sse` 或 `/run`，并使用已绑定的 `appName`、`userId` 与 `sessionId`。manager 使用 HaaS native session/event API 做 replay、status、cancel 和 diagnostics。

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

合同字段是持久事实。`containerId`、进程 id、临时端口和内存锁是运行时观测值，不能成为唯一恢复依据。

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

配置分三层：

1. 全局默认策略。
2. Workspace/project override。
3. Delegated session 创建时策略快照。

已有 delegated session 默认使用创建时快照。后续全局或 workspace 配置变更只影响新 session。改变已有 delegated session 需要显式 policy update/rebind 操作，并记录审计事件。

Manager 侧委派由独立的 `[haas_delegation]` 配置命名空间控制。默认关闭，
只允许从用户全局配置或其他可信运维配置源开启。项目本地 workspace 配置
不得开启 HaaS 委派、修改 HaaS endpoint、修改镜像或 harness 默认值，也不得
放宽 mount policy。首次委派成功后，manager 必须在 session binding 中记录
非 secret 的配置快照（`haas_base_url`、HaaS user id、harness id/base、image
reference、image digest、provider id/model、mount manifest、delegation policy
snapshot）。API token 或其他凭证不得持久化到 binding；运行时调用从当前
用户拥有的 config/secret 状态解析。

Manager 实现可以暴露面向用户的 settings API 管理该命名空间。这些 API
必须拆分 secret 与非 secret 材料：

- 非 secret 字段（`enabled`、endpoint URL、HaaS user id、harness id/base、
  image reference/digest、本地开发未 pin 镜像开关、strategy、allowlist、
  trigger keywords 和 TTL 默认值）可以存入用户拥有的 prefs 或用户全局配置。
- `api_token` 或等价 bearer credential 必须通过 manager 的 secret store
  保存，settings 读取 API 不得返回这些值。
- settings 变更只影响新的未绑定 session。已有 `HAAS_BOUND` session 保留
  binding 快照，除非执行显式 policy update/rebind 操作。

Manager 实现也可以在 GUI 启动路径中监督一个本地 HaaS sidecar，使桌面 app
和浏览器开发模式无需用户手动启动第二个 HaaS 进程即可接入。该本地监督能力由
显式非 secret 设置（`local_autostart`，默认 `false`）控制，并且只允许作用于
loopback HaaS URL（`127.0.0.1` 或 `localhost`）。Manager 传给本地进程的环境
变量只能包含非 secret 运行配置；委派 API token 仍保存在 manager secret store，
只作为出站 `Authorization` header 使用。如果启用了 local autostart 但 sidecar
无法启动或 `/v1/haas/health` 不通过，manager settings API 必须返回安全的本地
状态；委派 turn 继续通过既有 HaaS 委派错误路径 fail closed。生产委派仍必须
要求镜像 digest。本地开发流程可以显式设置 `allow_unpinned_local_image=true` 来
运行 `haas:local` 这类本地 tag；该 override 必须显式开启，且不得由 workspace
本地配置启用。

### 6.3 Mount Manifest 规则

- 已授权项目根目录 `rw` 挂载到 `/workspace`。
- 额外授权目录默认 `ro`，使用 `/mnt/extra/*` 下的确定性路径。
- 额外 mount 从 `ro` 升级到 `rw` 需要新的显式授权和 policy update/rebind。
- manager 不得授权用户 HOME、超出项目授权的父目录、Docker socket、SSH 目录、credential store 或未请求路径。
- HaaS 容器必须有独立可写 HOME、cache root 和 `/tmp`，确保 build、依赖安装和工具执行不依赖挂载 host HOME。

## 7. 运行模型与状态机

### 7.1 Manager Binding

```text
UNBOUND
  -> LOCAL_BOUND
  -> HAAS_BOUND

HAAS_BOUND persists across TTL cleanup, process restart, and follow-up turns.
```

规则：

- 确定性规则优先于模型意图识别。
- 首次成功委派后，manager session 固定为 `HAAS_BOUND`。
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

- manager 是唯一用户审批入口。HaaS 发出 `approval_required`；manager 展示、记录决策并回传结果。
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

## 11. 测试计划与验收

- Unit：delegation policy merge、session snapshot 行为、path canonicalization、mount manifest validation 和 deterministic route decision。
- Unit：provider catalog 包含独立的 `volcengine-ark`、`ark`、`ark-agent-plan-cn` 身份，且不共享 credential config。
- Integration：创建 delegated session，通过 `/run_sse` 执行 turn，实时流式输出事件，读取终态，并继续同一 session。
- Integration：TTL 销毁容器，后续追问用相同 mount/policy/provider contract 重建容器，并延续 Codex 的 HaaS session。
- Concurrency：同一 canonical workspace 的两个 `rw` delegated session 串行；`ro` session 并发。
- Security：拒绝 HOME、Docker socket、SSH、父目录、symlink escape 和未授权 mount widening。
- Approval：HaaS 发出 `approval_required`，manager 回传 approve/deny，结果持久化并体现在事件流里。
- Failure：HaaS unavailable、image unavailable、mount drift、container startup failure、queue timeout 都 fail closed，不本地 fallback。
- E2E：显式开启 Docker/Codex flag 后，对授权项目目录以 `/workspace:rw` 运行真实 delegated coding task。
