# Session Runtime 组件规格

Status: Draft
Last reviewed: 2026-08-30
Related specs: [HaaS Protocol](../haas-protocol/README.md), [Harness Registry](../harness-registry/README.md), [Harness Adapter](../harness-adapter/README.md), [Event Log & SSE](../event-log-sse/README.md), [Admission Control](../admission-control/README.md)

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
- 将 session 创建时的 configured harness 冻结为 `EffectiveHarnessConfig`。
- 将 adapter events 写入 Event Log，并维护 session `state`（ADK `stateDelta`）。
- 处理 streaming run（`/run_sse`）和 non-streaming run（`/run`）的一致终态。
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

### 5.2 Internal API

```python
async def run(app_name: str, user_id: str, session_id: str | None, message: Message, ext: RunExtensions) -> InvocationRecord: ...
async def get_session(app_name: str, user_id: str, session_id: str) -> SessionRecord: ...
async def apply_state_delta(app_name: str, user_id: str, session_id: str, delta: dict) -> SessionRecord: ...
async def delete_session(app_name: str, user_id: str, session_id: str) -> None: ...
async def start_turn(req: TurnStartRequest) -> TurnRecord: ...
async def mark_turn_terminal(turn_id: str, result: TurnTerminalResult) -> None: ...
async def cancel_invocation(session_id: str, invocation_id: str) -> InvocationRecord: ...
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

### 6.4 State 合并语义

session `state` 有两个写入源，串行化于同一 sessionKey 写队列，合并规则一致：

1. **`PATCH stateDelta`（客户端显式）**：deep-merge 到 `session.state`——对象递归合并、标量覆盖。
2. **事件 `actions.stateDelta`（invocation 运行中）**：与 PATCH 同队列按到达顺序应用，deep-merge。

约束：不允许删除操作（删除需显式扩展字段）；冲突时标量「后到覆盖」；合并是幂等的纯函数，便于恢复重放。

### 6.5 幂等 replay 语义

`IdempotencyStore.reserve(key_hash, request_hash)` 记录首个 `request_hash`（见
[Stores](../stores/README.md) §5）。后续同一 key 的请求：

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

| 场景 | 行为 |
|------|------|
| request 在执行前失败 | release idempotency reservation |
| request 已进入执行 | idempotency reservation 保留，后续重试 replay 首次结果 |
| sidecar 进程重启 | 从 store 读取 running 状态并调用 adapter inspect/resume；无法确认则 fail closed |
| adapter 无终态 | timeout 后写 terminal failed/incomplete |
| cancel 后断线 | cancel intent 持久化；最终状态仍必须可读 |
| session lease 过期 | 后续 continuation 返回 `session_expired` |
| event log 写失败 | 不能宣称 run 成功；已接受任务必须生成 terminal failure evidence |

## 11. 测试计划与验收

- Unit：id 生成、三元组解析、scope、state transition、terminal immutability、request hash、idempotency replay。
- Integration：`/run` 非流式与 `/run_sse` 流式 parity、read back session、PATCH stateDelta、cancel、DELETE。
- Concurrency：同 session 并发返回 `session_busy`；不同 session 可并发。
- Recovery：模拟 sidecar restart、adapter reconnect、missing native ref、expired session。
- Compatibility：ADK client session GET/PATCH/DELETE 行为对齐。
- Security：跨 principal 访问全部返回 404；secret-shaped input 不落默认日志。
