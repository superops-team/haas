# Event Log & SSE 组件规格

[English](README.md) | **简体中文**

Status: Draft
Last reviewed: 2026-09-07
Related specs: [HaaS Protocol](../haas-protocol/README.zh-CN.md), [Session Runtime](../session-runtime/README.zh-CN.md), [Harness Adapter](../harness-adapter/README.zh-CN.md), [Security Boundary](../security-boundary/README.zh-CN.md)

## 1. 组件定位

Event Log & SSE 是 HaaS 的事件事实源和实时订阅层。它持久化 canonical events，投影为 ADK `Event`，提供 session/invocation 级 replay-then-live stream。（legacy sidecar projection 不在本项目范围，见 [specs/README §3.1.1](../README.zh-CN.md#311-范围决策不实现-mpa-codex-worker-迁移-shim)。）

SSE 是 delivery channel，不是唯一事实源。断线、客户端超时或代理重连不得导致任务停止或结果丢失。

## 2. 来源与依据

| 来源 | 采用内容 |
|------|----------|
| ADK 2.0 | `Event` shape、`/run_sse` SSE `data:` 帧、stream 关闭语义、`GET session` 返回 `events[]` 作为 replay 通道 |
| `mpa-codex-worker` Event Log & SSE Replay | `after_event_id`、`Last-Event-ID`、heartbeat 不推进 cursor、projection |
| 本组件总览 | HaaS event log 和 SSE 要求 |

## 3. 上游与下游关系

| 方向 | 对象 | 关系 |
|------|------|------|
| 上游 | Session Runtime | append lifecycle、output、terminal events |
| 上游 | Harness Adapter | 提供 adapter-native event，由 Session Runtime 转成 canonical event 后 append |
| 上游 | HaaS Protocol | 读取 event log 并输出 ADK SSE |
| 下游 | Persistence Store | 保存 event records |
| 下游 | Observability | 记录 stream client、lag、drop、replay metrics |

## 4. 职责边界

负责：

- 持久化 canonical event。
- 为每个 invocation 维护从 0 开始的 gapless `sequenceNumber`（内部字段）。
- 为 HaaS native stream 维护 session-scoped `eventId`。
- 将 canonical event 投影为 ADK `Event` 或 HaaS event。
- 支持 replay missed events 后进入 live stream（`Last-Event-ID` / `after_event_id`）。
- 发送 heartbeat 且不产生事件、不推进 cursor。
- 对慢客户端执行 bounded queue、delta drop 或断开；terminal state 通过 read-back 保证可得。
- 禁止未脱敏 raw event 进入持久 event log。

不负责：

- 不执行 harness。
- 不修改 session/invocation terminal state。
- 不保存 raw prompt、完整 tool args/result 或 provider payload。
- 不保证跨 session 全局严格有序；只保证 invocation 内顺序。

## 5. 核心接口

### 5.1 Public Streams

| Endpoint | 协议 | 语义 |
|----------|------|------|
| `POST /run_sse` | ADK SSE | invocation 的 ADK Event stream，完成即关闭 |
| `GET /apps/{app}/users/{user}/sessions/{sid}` | JSON | ADK 原生 replay 通道，返回全部 `events[]` |
| `GET /v1/haas/sessions/{session_id}/events` | HaaS SSE | session canonical event replay/live，带 `after_event_id` |
| `GET /v1/haas/sessions/{session_id}/invocations/{invocation_id}/events` | HaaS SSE | invocation canonical event replay/live |
| ~~`GET /v1/codex-worker/sessions/{session_id}/events`~~ | ~~Legacy SSE~~ | **不实现**（见 [specs/README §3.1.1](../README.zh-CN.md#311-范围决策不实现-mpa-codex-worker-迁移-shim)） |

### 5.2 Internal API

```python
async def append_event(event: CanonicalEvent) -> StoredEvent: ...
async def read_session_events(app_name: str, user_id: str, session_id: str, after_event_id: str | None) -> list[StoredEvent]: ...
async def read_invocation_events(app_name: str, user_id: str, session_id: str, invocation_id: str) -> list[StoredEvent]: ...
async def stream_invocation(app_name: str, user_id: str, session_id: str, invocation_id: str, after_event_id: str | None) -> AsyncIterator[SSEFrame]: ...
async def stream_session(app_name: str, user_id: str, session_id: str, after_event_id: str | None) -> AsyncIterator[SSEFrame]: ...
def project_adk(event: StoredEvent) -> AdkEvent: ...
# project_legacy(): 不实现，legacy shim 不在本项目范围（specs/README §3.1.1）
```

## 6. 数据模型

### 6.1 CanonicalEvent（内部）

```json
{
  "eventId": "evt_0000000001042",
  "invocationId": "inv_abc",
  "sessionId": "hsess_abc",
  "appName": "chrn_codex_default",
  "userId": "u_123",
  "turnId": "turn_abc",
  "harnessId": "chrn_codex_default",
  "adapterId": "codex-app-server",
  "author": "codex",
  "sequenceNumber": 7,
  "content": {
    "role": "model",
    "parts": [{ "text": "text" }]
  },
  "actions": {
    "stateDelta": {},
    "artifactDelta": {}
  },
  "observedAtMs": 1786400000000,
  "redactionApplied": true
}
```

时间戳统一：内部 canonical event 用 `observedAtMs`（毫秒 epoch）；ADK `Event.timestamp`
为 `observedAtMs / 1000.0`（float 秒），由投影层生成。

**`HarnessEvent` → `CanonicalEvent` 映射**：adapter 产出 `HarnessEvent`
（[harness-adapter](../harness-adapter/README.zh-CN.md) §6.3），由 Session Runtime 调用
Event Log 归一化后落库：

| HarnessEvent 字段 | CanonicalEvent | 规则 |
|-------------------|----------------|------|
| `type` / `nativeType` | 不落库 | 仅归一化时用于判定 part 类型与 terminal |
| `invocationId` / `sessionId` / `turnId` | 同名保留 | 必须与执行上下文一致 |
| 执行上下文 `appName` / `userId` | `appName` / `userId` | 为 store 层 scope 隔离而持久化；必须匹配 ADK session 三元组 |
| `author` | `author` | 保留 |
| `content` / `actions` / `usage` | 同名字段 | 经 `redact()` 后保留 |
| `safe` | 不落库 | 由 `redactionApplied` 替代 |
| — | `eventId` / `sequenceNumber` / `observedAtMs` / `harnessId` / `adapterId` | Event Log 生成 |

`sequenceNumber` 从 0 起、invocation 内无空洞；`observedAtMs` 为 append 时刻毫秒
epoch；未经 `redact()` 的 raw event 不得落库（fail closed）。

### 6.2 ADK Projection（公共）

```json
{
  "id": "evt_0000000001042",
  "invocationId": "inv_abc",
  "author": "codex",
  "timestamp": 1743712220.385936,
  "content": { "role": "model", "parts": [{ "text": "text" }] },
  "actions": { "stateDelta": {}, "artifactDelta": {}, "requestedAuthConfigs": {} },
  "longRunningToolIds": []
}
```

内部 `sequenceNumber`/`sessionId`/`adapterId` 等字段不进入 ADK 投影；HaaS native stream 可额外输出 `haas` 元数据。

### 6.3 SSE Frame

```text
data: {"id":"evt_...","invocationId":"inv_abc","author":"codex","timestamp":1743712220.385936,"content":{...},"actions":{...},"longRunningToolIds":[]}

```

Heartbeat（SSE comment，不产生事件）：

```text
: keep-alive

```

## 7. 运行模型与状态机

```text
append canonical event
  -> assign invocation sequence
  -> persist
  -> publish to live subscribers
  -> projection requested by stream
  -> SSE frame flush

client reconnect
  -> validate cursor (Last-Event-ID / after_event_id)
  -> replay retained events after cursor
  -> emit reconcile event when gap exists (HaaS native only)
  -> subscribe live
```

Ordering invariants:

- invocation 内事件按产生顺序、无空洞输出。
- 同一 item 的事件顺序稳定。
- 非流式 `/run` 返回的事件数组必须等于 `/run_sse` 流式聚合结果（parity）。
- stream 关闭本身即 invocation 完成信号。

## 8. 安全与权限

- 读取事件需要 session/invocation ownership；跨 scope 返回 404 或 auth error。
- Event log 读取和 replay 必须按完整 ADK session 三元组
  `(appName, userId, sessionId)` 作用域查询。由于 `sessionId` 由调用方控制，
  session replay 与 invocation replay 都不得使用裸 `sessionId` 作为持久化查询 key。
- HaaS native `/v1/haas/sessions/{session_id}/events` 在读取前必须把裸路径 id
  解析为唯一一个调用方可见的 `(appName, userId, sessionId)`；无匹配或匹配多个可见
  session 时返回 `404 session_not_found`，不得跨 scope 合并或猜测。
- Raw adapter event 必须 redact 后才能 append。
- `include_debug=true` 需要显式 debug/admin scope，仍不能包含 credentials。
- Tool input/output payloads 默认摘要；full payload 需要单独的已批准 debug 设计。

## 9. 可观测性

Metrics：

- `haas_sse_clients{scope}`
- `haas_sse_event_total{adapterBase}`
- `haas_sse_replay_total{scope}`
- `haas_sse_replay_gap_total{scope}`
- `haas_sse_client_disconnect_total{scope,reason}`
- `haas_event_log_append_duration_ms`
- `haas_event_log_lag_ms`

Logs：

- `haas.event.appended`
- `haas.event.replay_started`
- `haas.event.replay_gap`
- `haas.sse.client_connected`
- `haas.sse.client_disconnected`

## 10. 失败与恢复

| 场景 | 行为 |
|------|------|
| client disconnect | 不取消 invocation；event log 继续写 |
| Last-Event-ID 已过期 | 返回 `410 haas_offset_expired` 或从最早 retained event 开始并记录 gap |
| event queue 满 | 优先丢弃非关键 delta；terminal state 必须持久化并可 read-back |
| append 持久化失败 | 当前 invocation 不得宣称 completed；Session Runtime 生成 terminal failure evidence |
| proxy buffering | response 设置 `Cache-Control: no-cache`；测试验证 progressive flush |
| adapter 重复 terminal | 保留第一条 terminal，后续写 diagnostic warning |

## 11. 测试计划与验收

- Unit：sequence 分配、terminal 唯一、heartbeat 不推进 cursor、ADK 投影映射。
- Integration：`/run` vs `/run_sse` parity、断线后 `GET session` read-back、reconnect replay。
- Backpressure：慢客户端不阻塞 adapter terminal 写入。
- Security：raw prompt、Authorization、cookie、完整 tool args/result 不进入 event log。
- Security：不同 user 或 app 使用同一裸 `sessionId` 时事件互不混读；跨 user
  native event replay 返回 404。
- Compatibility：ADK client 对 `/run_sse` 输出逐条解析成功，stream 关闭语义正确。
