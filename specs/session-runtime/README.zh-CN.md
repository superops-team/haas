# Session Runtime 组件规格

[English](README.md) | **简体中文**

Status: Draft
Last reviewed: 2026-09-07
Related specs: [HaaS Protocol](../haas-protocol/README.zh-CN.md), [Harness Registry](../harness-registry/README.zh-CN.md), [Harness Adapter](../harness-adapter/README.zh-CN.md), [Event Log & SSE](../event-log-sse/README.zh-CN.md), [Admission Control](../admission-control/README.zh-CN.md), [Manager Delegation](../manager-delegation/README.zh-CN.md)

## 1. 组件定位

Session Runtime 是 HaaS 的执行事实 owner。它管理 session、invocation（ADK 一次 `/run`）、turn、container、lease、idempotency 和 terminal state，并通过 Harness Adapter 驱动具体 agent。

ADK 的 session 由 `(appName, userId, sessionId)` 三元组唯一标识；`invocation` 是 public 运行单元，`turn` 是 adapter 内部执行单元。首期两者一一对应，但 Session Runtime 必须保留未来一个 invocation 拆成多个内部 turn 或 replay turn 的空间。

## 2. 来源与依据

| 来源 | 采用内容 |
|------|----------|
| ADK 2.0 | session 三元组、`/run`、`/run_sse`、stateDelta、invocation 生命周期 |
| `mpa-codex-worker` session registry | 运行态 session registry、checkpoint、恢复和 terminal 纪律 |
| Admission Control | 执行前准入（配额/限流/队列） |
| 总览要求 | adapter contract、event log 与 SSE |

## 3. 上游与下游关系

| 方向 | 对象 | 关系 |
|------|------|------|
| 上游 | HaaS Protocol | 接收 `/run`、`/run_sse`、session GET/PATCH/DELETE |
| 上游 | Admission Control | 执行前准入放行 |
| 下游 | Harness Registry | 解析 appName、冻结 effective harness config |
| 下游 | Harness Adapter | 执行 turn、取消、恢复、inspect |
| 下游 | Event Log & SSE | append lifecycle/progress/terminal event |
| 下游 | Artifact Store | 管理 container/file metadata |
| 下游 | Observability | 记录状态、队列、时延和失败 |

## 4. 职责边界

负责：

- 创建和读取 `Session`、`Invocation`、`Turn`、`Container` 对象。
- 解析 `(appName, userId, sessionId)`，校验 principal scope，创建或复用 session。
- 在 request validation 后、执行前完成 idempotency reservation。
- 保证同一 session 内一次只运行一个 active invocation/turn（`session_busy`）。
- 通过周期性续租维护整个 turn 期间的 active session lease，并对所有 turn-owned 写入使用 store fencing token。
- 将 session 创建时的 configured harness 冻结为 `EffectiveHarnessConfig`。
- 将 adapter events 写入 Event Log，并维护 session `state`（ADK `stateDelta`）。
- 处理 streaming run（`/run_sse`）和 non-streaming run（`/run`）的一致终态。
- 对 delegated session，持久化 HaaS session、native session reference、delegated-session reference、approval wait 和后续恢复所需的 runtime generation metadata。
- 管理 cancel、timeout、step budget、session expiry、session deletion。
- 在 sidecar restart 后根据持久状态恢复可恢复 session，或 fail closed 为不可恢复状态。

不负责：

- 不解析 harness 原生事件。
- 不保存 raw prompt 到默认日志。
- 不直接访问 provider 或 MCP server。
- 不决定 tool/network/workspace policy，只消费 Policy Controller 输出。
- 不做跨请求配额/限流（Admission Control 职责）。

## 5. 核心接口

### 5.1 Public API Ownership

| Endpoint | Runtime 行为 |
|----------|--------------|
| `POST /run` | 创建 invocation，非流式收集事件后一次性返回 |
| `POST /run_sse` | 创建 invocation，流式 SSE 返回 |
| `GET /apps/{app}/users/{user}/sessions/{sid}` | 返回 session（state + events） |
| `PATCH /apps/{app}/users/{user}/sessions/{sid}` | 应用 `stateDelta`，幂等 |
| `DELETE /apps/{app}/users/{user}/sessions/{sid}` | 删除 session，取消 active work |
| `POST /v1/haas/sessions/{sid}/invocations/{id}/cancel` | 取消运行中 invocation，幂等（HaaS native） |
| `POST /v1/haas/sessions/{sid}/approvals/{approval_id}` | 解析等待中的 delegated action 的 manager 审批决策 |

### 5.2 Internal API

```python
async def run(app_name: str, user_id: str, session_id: str | None, message: Message, ext: RunExtensions) -> InvocationRecord: ...
async def get_session(app_name: str, user_id: str, session_id: str) -> SessionRecord: ...
async def apply_state_delta(app_name: str, user_id: str, session_id: str, delta: dict) -> SessionRecord: ...
async def delete_session(app_name: str, user_id: str, session_id: str) -> None: ...
async def start_turn(req: TurnStartRequest) -> TurnRecord: ...
async def mark_turn_terminal(turn_id: str, result: TurnTerminalResult) -> None: ...
async def cancel_invocation(session_id: str, invocation_id: str) -> InvocationRecord: ...
async def resolve_approval(session_id: str, approval_id: str, decision: ApprovalDecision) -> ApprovalRecord: ...
async def reserve_idempotency(key: str, request_hash: str) -> IdempotencyReservation: ...
```

## 6. 数据模型

### 6.1 SessionRecord

```json
{
  "id": "hsess_abc",
  "object": "session",
  "appName": "chrn_codex_default",
  "userId": "u_123",
  "tenantId": "tenant_1",
  "workspaceId": "workspace_1",
  "harnessId": "chrn_codex_default",
  "harnessBase": "codex",
  "status": "active",
  "containerId": "cntr_abc",
  "sandboxId": "sbx_abc",
  "state": {},
  "effectiveHarnessConfig": {},
  "nativeSessionRef": {
    "adapterId": "codex-app-server",
    "opaque": "encrypted-or-private-ref",
    "generation": 1
  },
  "delegatedSessionRef": {
    "delegatedSessionId": "dgsess_abc",
    "managerSessionId": "mgr_sess_123",
    "containerGeneration": 3,
    "runtimeStatus": "idle"
  },
  "createdAtMs": 1786400000000,
  "updatedAtMs": 1786400000000,
  "expiresAtMs": null
}
```

`lastUpdateTime`（ADK 公开字段）= `updatedAtMs / 1000.0`，由投影层生成；内部记录
只存毫秒 epoch。

### 6.2 InvocationRecord

```json
{
  "id": "inv_abc",
  "object": "invocation",
  "sessionId": "hsess_abc",
  "appName": "chrn_codex_default",
  "turnId": "turn_abc",
  "status": "running",
  "startedAtMs": 1786400000000,
  "completedAtMs": null,
  "model": "gpt-5.6-terra",
  "requestedModel": "gpt-5.6-terra",
  "idempotencyKeyHash": "idem_sha256",
  "terminalEventId": null,
  "error": null
}
```

`turnId` 首期与 invocation 1:1（id 不同，映射持久化在 `InvocationRecord.turnId`）；
未来 1:N 时 invocation 聚合多个 turn 的事件（按 `turnId` 分组）。内部时间戳统一
毫秒 epoch（`startedAtMs`/`completedAtMs`），公开面由投影层转为 ADK float 秒。

### 6.3 TurnRecord

```json
{
  "id": "turn_abc",
  "invocationId": "inv_abc",
  "sessionId": "hsess_abc",
  "status": "running",
  "startedAtMs": 1786400000000,
  "completedAtMs": null
}
```

`SessionRecord` 必须能无损投影为 ADK `Session`（`{id, appName, userId, state, events[], lastUpdateTime}`）；内部字段（tenantId 等）不进入 public 输出。

### 6.4 Delegated Session Reference

对 manager-delegated session，`SessionRecord.delegatedSessionRef` 把 HaaS session
关联到 [Manager Delegation](../manager-delegation/README.zh-CN.md) 中定义的持久
delegated-session contract。Session Runtime 只存 HaaS 侧 reference 与 runtime
generation。完整 mount manifest、policy snapshot、manager session id、image digest
和 provider credential reference 由 delegated-session store contract 持久化，不得进入
ADK `Session` 投影。

存在 `delegatedSessionRef` 表示：

- continuation 在启动新 turn 前使用 HaaS delegated-session restore 路径；
- TTL 清理可以销毁容器，但不得使 HaaS session 过期；
- `session_expired` 只表示 HaaS session 保留期过期，不表示 idle container TTL；
- restore 失败时，invocation 进入 failed/non-resumable 证据，不回退本地执行。

### 6.5 ApprovalRecord

```json
{
  "id": "appr_abc",
  "sessionId": "hsess_abc",
  "invocationId": "inv_abc",
  "turnId": "turn_abc",
  "status": "waiting",
  "request": {
    "kind": "tool",
    "safeSummary": "Run shell command in /workspace",
    "policyReason": "tool_requires_approval"
  },
  "decision": null,
  "createdAtMs": 1786400000000,
  "resolvedAtMs": null
}
```

审批记录必须持久化，确保 SSE 断线或 sidecar restart 不会丢失等待中的决策。记录只保存
安全摘要和 policy reason，不保存完整 tool argument 或 raw prompt。

### 6.6 State 合并语义

session `state` 有两个写入源，串行化于同一 sessionKey 写队列，合并规则一致：

1. **`PATCH stateDelta`（客户端显式）**：deep-merge 到 `session.state`——对象递归合并、标量覆盖。
2. **事件 `actions.stateDelta`（invocation 运行中）**：与 PATCH 同队列按到达顺序应用，deep-merge。

约束：不允许删除操作（删除需显式扩展字段）；冲突时标量「后到覆盖」；合并是幂等的纯函数，便于恢复重放。

### 6.7 幂等 replay 语义

`IdempotencyStore.reserve(key_hash, request_hash)` 记录首个 `request_hash`（见
[Stores](../stores/README.zh-CN.md) §5）。后续同一 key 的请求：

- `request_hash` 一致 -> 返回首次结果，不重复启动 harness（replay）。
- `request_hash` 不一致 -> fail closed，返回 `409 haas_idempotency_conflict`，不静默返回旧结果，由调用方显式处理。

release 幂等：执行前失败释放 reservation；已进入执行的 reservation 保留，后续重试走 replay。

## 7. 运行模型与状态机

### 7.1 Invocation

```text
accepted -> running -> completed
accepted -> running -> incomplete
accepted -> running -> failed
accepted -> running -> cancelling -> cancelled
```

Terminal states are immutable。`/run` 在终态后返回事件数组；`/run_sse` 在终态时关闭 stream。

### 7.2 Session

Session Runtime 使用短 TTL 的 active-turn lease 来串行化同一 `(appName, userId, sessionId)` 的写入。默认 lease TTL 为 30 秒，默认续租周期为 10 秒；续租周期必须小于 TTL 的一半。配置超时时间超过一个 lease TTL 的 turn，必须持续续租直到进入终态。turn 正常结束、失败、取消或超时时，续租后台任务必须被取消并等待结束，不能残留后台任务。

active turn 写入 event、session/invocation/turn 终态前，都必须通过 store 的 holder/token fencing 校验。lease 已过期或已被接管的 stale holder 不得继续 append event、合并 `stateDelta` 或覆盖终态。stale-write 拒绝沿用现有 adapter failure 路径（`haas_adapter_error`），不新增公开错误码。

```text
new -> active -> idle -> active
new -> active -> cancelling -> idle
new -> active -> expired
new -> active -> deleted
new -> active -> non_resumable
```

规则：

- 首次 `/run` 缺省 `sessionId` 时创建 `hsess_<rand>`；后续相同 `(appName, userId, sessionId)` 复用。
- 同一 session 内一次只运行一个 invocation；并发返回 `409 session_busy`。
- `model` 可在同一 session 内逐 run 变化。
- Session delete 先取消 active work，再使 history/artifacts 不可达。
- manager-delegated session 可在 `active` 与 `idle` 之间切换，同时其容器在
  `running`、`ttl_destroyed`、`restoring` 等状态间变化。容器 TTL 本身不使 HaaS
  session 过期，也不解除 delegated binding。
- `/run_sse` 必须在 invocation 运行中消费 Session Runtime 的 live event stream
  并实时发送事件；不得等待 `run()` 完成后再把全部事件作为完成批次 replay。

## 8. 安全与权限

- session、invocation、turn、container、file 按 principal scope 隔离；`userId` 必须属于认证 principal。
- 跨 scope 访问返回 404，不返回 403。
- Input 可被持久化，但 raw secret 必须先被拒绝或脱敏。
- `nativeSessionRef` 是内部 opaque 字段，不进入 ADK Session 输出。
- Idempotency key 只保存 hash，不保存原文。
- Session workspace 必须限定在 Sandbox Runtime 提供的 session root 下。

## 9. 可观测性

Session Runtime 产生：

- `haas.run.accepted`
- `haas.run.idempotency_replayed`
- `haas.session.created`
- `haas.session.continued`
- `haas.session.busy`
- `haas.turn.started`
- `haas.turn.terminal`
- `haas.turn.cancel_requested`
- `haas.session.recovery_failed`

指标：

- `haas_sessions_active`
- `haas_invocations_running`
- `haas_turn_duration_ms`
- `haas_idempotency_replay_total`
- `haas_session_busy_total`
- `haas_turn_terminal_total{status,adapterBase}`

## 10. 失败与恢复

Adapter 调用（`prepare_session`、`start_turn`、`stream_events`、`finalize_turn`）运行在同一个 invocation timeout budget 内。默认 timeout 为 900 秒，并可在 `SessionRuntime` 配置；该值必须大于 lease TTL，保证正常长 turn 能在 timeout 前至少续租一次。超时后，Runtime 会取消正在等待的 adapter 调用，写入 terminal `failed` event（`actions.stateDelta.status = "failed"` 且 `actions.stateDelta.reason = "timeout"`），持久化 invocation/turn/session 终态，由 API 层释放 admission quota，并用该 failed terminal event 完成幂等 reservation，使重试不再看到 pending key。

| 场景 | 行为 |
|------|------|
| request 在执行前失败 | release idempotency reservation |
| request 已进入执行 | idempotency reservation 保留，后续重试 replay 首次结果 |
| sidecar 进程重启 | 从 store 读取 running 状态并调用 adapter inspect/resume；无法确认则 fail closed |
| adapter 无终态 | timeout 后取消 adapter await 并写 terminal `failed` |
| cancel 后断线 | cancel intent 持久化；最终状态仍必须可读 |
| session lease 续租失败或被 fencing out | stale holder 停止写入；仅在 fencing 仍允许时写失败终态，并走现有 adapter-error 路径 |
| event log 写失败 | 不能宣称 run 成功；已接受任务必须生成 terminal failure evidence |
| delegated container 被 idle TTL 销毁 | 启动下一 turn 前，按 delegated-session contract 恢复运行资源 |
| delegated restore 失败 | 返回 `haas_delegation_restore_failed` 或更具体的 delegation 错误；不本地执行 |
| approval bridge 正在等待 | 持久化 `ApprovalRecord`；只有 manager 解析后才恢复 adapter |

## 11. 测试计划与验收

- Unit：id 生成、三元组解析、scope、state transition、terminal immutability、request hash、idempotency replay。
- Integration：`/run` 非流式与 `/run_sse` 流式 parity、read back session、PATCH stateDelta、cancel、DELETE。
- Concurrency：同 session 并发返回 `session_busy`；不同 session 可并发；超过一个 lease TTL 的长 turn 仍必须因续租受到保护。
- Integration：`/run_sse` 在 invocation 终态前渐进输出事件，native event replay 可在 turn 运行中重连。
- Integration：delegated session 在容器 TTL restore 后继续使用 HaaS session，并在可恢复时复用 native Codex reference。
- Approval：pending approval 在 SSE 断线后仍保留，并通过 HaaS native approval API 解析。
- Recovery：模拟 sidecar restart、adapter reconnect、missing native ref、expired session、stale holder fencing rejection 和 adapter timeout terminalization。
- Compatibility：ADK client session GET/PATCH/DELETE 行为对齐。
- Security：跨 principal 访问全部返回 404；secret-shaped input 不落默认日志。
