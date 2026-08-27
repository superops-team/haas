# HaaS 端到端时序

Status: Draft
Last reviewed: 2026-08-26

本文件把一次 `/run_sse` 与 session 读取的完整请求链路串起来，标注每个环节的
owner 与传递对象，消解「各组件 spec 之间由 AI 脑补衔接」的问题。序号与
`specs/architecture` 的请求流一致。

## 1. `POST /run_sse`（流式运行）

```text
Client
  1. POST /run_sse  {appName, userId, sessionId?, newMessage, streaming, haas?}
     Idempotency-Key?  Last-Event-ID?
  2. FastAPI route (haas.api)
       -> Protocol Mapper: schema 校验, camelCase 解析
  3. Identity.authenticate(authorization) -> Principal
     Identity.owns(principal, tenant/workspace/userId)  -> 404 若越权
  4. HarnessRegistry.resolve_app(principal, appName) -> HarnessConfig
  5. AdmissionControl.admit_run(ctx) -> AdmissionDecision（429/503 若拒绝）
  6. SessionRuntime.run(...)
       a. IdempotencyStore.reserve(key_hash, request_hash)  // 若带 key
       b. SessionStore.get_session((appName,userId,sessionId))
          -> 创建或缺省 sessionId=hsess_<rand>
       c. SessionStore.acquire_lease(sessionKey, holder)  // session_busy 若被占
       d. InvocationRecord 创建 (inv_...)
       e. HarnessRegistry.snapshot_for_session -> EffectiveHarnessConfig 冻结
       f. PolicyController.compile_policy -> EffectivePolicy
       g. SandboxRuntime.create_sandbox(sessionId, SandboxSpec)
       h. HarnessAdapter.prepare_session -> native session ref
       i. TurnRecord 创建（与 invocation 1:1）
       j. HarnessAdapter.start_turn -> TurnHandle
  7. for event in adapter.stream_events(turn):
       -> 归一化 + redact
       -> EventLogStore.append(CanonicalEventRecord)
       -> Event projection project_adk -> ADK Event
       -> SSE frame写入响应（progressive flush）
  8. adapter finalize -> SessionRuntime.mark_turn_terminal
       -> invocation=completed|failed|incomplete|cancelled
       -> SessionStore 落终态 + Invocation/turn 更新
       -> SSE stream 关闭（关闭即完成信号）
  9. session state 合并（事件 actions.stateDelta + state 更新）落 SessionStore
```

关键对象传递：

| 步骤 | 输入对象 | 输出对象 |
|------|----------|----------|
| 6b | `(appName,userId,sessionId)` | `SessionRecord` |
| 6e | `HarnessConfig` | `EffectiveHarnessConfig` |
| 6f | `EffectiveHarnessConfig` + request overrides | `EffectivePolicy` |
| 6g | `EffectivePolicy` + `HarnessSandboxDecl` | `SandboxSpec`/`SandboxHandle` |
| 6j | `StartTurnRequest` | `TurnHandle` |
| 7 | `HarnessEvent` | `CanonicalEventRecord` -> ADK `Event` |

## 2. `POST /run`（非流式）

与 `/run_sse` 唯一差异：第 7 步不刷 SSE，事件全部累积；第 8 步终止后一次性
返回 JSON 数组。**数组必须等于 `/run_sse` 流式聚合输出（parity）**——两者共用
同一事件累积路径，只差输出时序。

## 3. `GET /apps/{app}/users/{user}/sessions/{sid}`（读回）

```text
Client
  1. GET /apps/{appName}/users/{userId}/sessions/{sessionId}
  2. Identity.authenticate + owns(principal, userId) -> 404 若越权
  3. SessionRuntime.get_session(key) -> SessionRecord
  4. EventLogStore.read_session(sessionId) -> events[]
  5. 投影为 ADK Session {id, appName, userId, state, events[], lastUpdateTime}
```

## 4. `PATCH /apps/.../sessions/{sid}`（stateDelta）

```text
  1-2. 同上鉴权
  3. SessionStore.get_session
  4. deep-merge(state, stateDelta)（标量覆盖，对象递归合并；无删除操作）
  5. SessionStore.put_session；lastUpdateTime 更新
```

state 双写源（PATCH `stateDelta` 与事件 `actions.stateDelta`）串行化于同一
sessionKey 的写队列，合并规则一致（deep-merge，标量后到覆盖）。

## 5. 取消（HaaS native）

```text
  POST /v1/haas/sessions/{sid}/invocations/{invId}/cancel
  -> SessionRuntime.cancel_invocation
     -> HarnessAdapter.cancel_turn -> turn/interrupt
     -> mark invocation=cancelled -> EventLog terminal 落盘
  cancel 幂等：重复调用返回当前状态
```

## 6. `/run_sse` 断线重连（Last-Event-ID）

```text
Client disconnects mid-stream
  -> 服务端不取消 invocation，事件继续 append 到 EventLogStore
  -> Client 用原 invocationId 重连 GET /v1/haas/sessions/{sid}/invocations/{invId}/events
       header: Last-Event-ID: evt_...
  -> EventLogStore.read_invocation(invId, after) 回放 cursor 之后的事件
  -> 若无 gap -> 续接 live stream；若 cursor 已过期 -> 410 offset_expired 或 reconcile event
  -> invocation 未终止则继续收尾；已终止则回放 terminal 后关闭
```

## 7. 失败路径（非流式 `/run` 与 adapter 失败）

```text
adapter.start_turn 或 stream_events 抛错
  -> SessionRuntime 收敛 terminal state
       -> failed（错误可读）/ incomplete（预算/超时截断）
  -> EventLogStore 写 terminal failure evidence
  -> InvocationRecord.status 落盘

POST /run 非流式：不产 SSE，terminal 后一次性返回事件 JSON 数组
  -> 若失败：返回事件数组（含错误事件）+ 公开面可读的错误；HTTP 200（数组语义）
  -> 若请求前置失败（auth/schema/admission）：返回结构化 haas_error（4xx/5xx）
```

## ADK 适配范围（关键澄清）

HaaS 只适配 ADK 2.0 的 **REST API 协议层**：HTTP 路径、请求/响应 shape、
`Event` shape、SSE framing、camelCase 字段。**不**包含 ADK 执行引擎、图
工作流、`BaseAgent/WorkflowGraph`、ADK Web UI 或 Python SDK 的 snake_case
server 实现。compatibility 目标是「任何遵守 ADK 2.0 REST 协议的 HTTP 客户端」。
