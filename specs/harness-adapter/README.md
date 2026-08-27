# Harness Adapter 组件规格

Status: Draft
Last reviewed: 2026-08-26
Related specs: [Harness Registry](../harness-registry/README.md), [Session Runtime](../session-runtime/README.md), [Event Log & SSE](../event-log-sse/README.md), [Sandbox Runtime](../sandbox-runtime/README.md)

## 1. 组件定位

Harness Adapter 是 HaaS 内部统一执行接口（Python async）。它把不同 agent harness 的原生协议转换为 HaaS canonical session、invocation、event、artifact 和 error，最终投影为 ADK `Event`。

首期必须实现 `codex-app-server` adapter。Pi、OpenCode、AMP 等后续 adapter 只能通过本接口接入，不能新增平行 public API。

## 2. 来源与依据

| 来源 | 采用内容 |
|------|----------|
| ADK 2.0 | Event schema（`content.role/parts`、`actions`、`invocationId`）、author 语义 |
| `mpa-codex-worker` adapter specs | Codex app-server 内部隔离、secretless、event terminal contract |
| Sandbox Runtime | adapter 声明 sandbox 需求，由 Sandbox Runtime 统一投影 |
| 本组件总览 | 统一 adapter contract |

## 3. 上游与下游关系

| 方向 | 对象 | 关系 |
|------|------|------|
| 上游 | Session Runtime | 调用 adapter 准备 session、执行 turn、取消、恢复 |
| 上游 | Harness Registry | 读取 adapter capabilities |
| 上游 | Sandbox Runtime | 提供 harness sandbox 声明，接收 sandbox 实例 |
| 下游 | Concrete harness runtime | Codex app-server、Pi CLI、OpenCode CLI、AMP runtime 等 |
| 下游 | Model Proxy | 获取 model endpoint 与短期 token |
| 下游 | MCP / Tool / Skill Runtime | 物化 MCP、tools、skills |
| 下游 | Event Log & SSE | 产出 canonical events（投影为 ADK Event） |

## 4. 职责边界

负责：

- 为每个 harness base 提供同一套 typed async interface。
- 将 HaaS `EffectiveHarnessConfig` 转换为 harness 原生配置。
- 将输入 item、文件、instructions、model、budget、policy 转换为 harness 可理解格式。
- 归一化原生 progress、text、reasoning、tool、usage 和 terminal 为 canonical event。
- 把原生错误映射为 HaaS 错误码和 safe reason。
- 实现或声明取消、恢复、MCP、skills、artifact、tool restriction 的支持级别。
- 声明 harness sandbox 需求（cwd、writableRoots、approvalMode），交给 Sandbox Runtime 投影。

不负责：

- 不负责 public HTTP path。
- 不负责配置存储或 session 事实持久化。
- 不负责长期 provider credential 保存。
- 不越过 policy controller 放权。
- 不把原生 event 直接作为 public event。

## 5. 核心接口

```python
class HarnessAdapter:
    base: str
    adapter_id: str
    version: str

    async def probe(self) -> AdapterProbe: ...
    async def prepare_session(self, request: PrepareSessionRequest) -> PreparedSession: ...
    async def start_turn(self, request: StartTurnRequest) -> TurnHandle: ...
    async def stream_events(self, handle: TurnHandle) -> AsyncIterator[HarnessEvent]: ...
    async def finalize_turn(self, handle: TurnHandle) -> AdapterTurnResult: ...
    async def cancel_turn(self, request: CancelTurnRequest) -> CancelResult: ...
    async def resume_session(self, request: ResumeSessionRequest) -> PreparedSession: ...
    async def inspect_session(self, request: InspectSessionRequest) -> SessionInspection: ...
    async def list_artifacts(self, request: ListArtifactsRequest) -> list[ArtifactRef]: ...
    async def cleanup_session(self, request: CleanupSessionRequest) -> CleanupResult: ...
    def sandbox_declaration(self) -> HarnessSandboxDecl: ...
```

### 5.1 Capability Matrix

| Capability | Type | 说明 |
|------------|------|------|
| `streaming` | bool | 是否能产出增量事件（对应 ADK `streaming:true`） |
| `sessionContinuation` | `native` / `emulated` / `unsupported` | session 续写方式 |
| `cancellation` | `hard` / `best_effort` / `unsupported` | cancel 语义 |
| `toolRestriction` | `hard` / `advisory` / `unsupported` | disabled tools 执行强度 |
| `mcp` | `native` / `proxy` / `advisory` / `unsupported` | MCP 接入方式 |
| `skills` | `native` / `instructions` / `unsupported` | skill 物化方式 |
| `files` | `native` / `workspace_scan` / `unsupported` | artifact 收集方式 |
| `usage` | `native` / `estimated` / `unavailable` | token usage 来源 |

### 5.2 Adapter Phases

| Phase | 输入 | 输出 |
|-------|------|------|
| `probe` | runtime binary/config | `AdapterProbe` |
| `prepare_session` | frozen harness config、workspace、policy、sandbox | native session ref |
| `start_turn` | invocation id、session id、input、model、budget | `TurnHandle` |
| `stream_events` | `TurnHandle` | `HarnessEvent` stream |
| `finalize_turn` | event accumulator、native terminal | `AdapterTurnResult` |
| `cancel_turn` | invocation/turn/session id | `CancelResult` |
| `cleanup_session` | retention/delete request | cleanup report |

## 6. 数据模型

### 6.1 AdapterProbe

```json
{
  "adapterId": "codex-app-server",
  "base": "codex",
  "status": "ready",
  "runtimeVersion": "codex-cli 0.149.1",
  "transport": "unix_websocket",
  "capabilities": {
    "streaming": true,
    "sessionContinuation": "native",
    "cancellation": "best_effort",
    "toolRestriction": "advisory",
    "mcp": "native",
    "skills": "native",
    "files": "workspace_scan",
    "usage": "native"
  },
  "safeDetails": {}
}
```

### 6.2 StartTurnRequest

```json
{
  "invocationId": "inv_abc",
  "sessionId": "hsess_abc",
  "turnId": "turn_abc",
  "appName": "chrn_codex_default",
  "harness": {},
  "input": [],
  "instructions": null,
  "model": "gpt-5.6-terra",
  "maxStep": 40,
  "timeoutSeconds": 900,
  "sandbox": {
    "sandboxId": "sbx_abc",
    "workspaceRoot": "/workspace",
    "writableRoots": ["/workspace"]
  },
  "policy": {},
  "credentials": {
    "modelProxyTokenRef": "secret://runtime/session/abc"
  }
}
```

### 6.3 HarnessEvent（内部 canonical，投影为 ADK Event）

```json
{
  "type": "harness.text.delta",
  "nativeType": "item/agentMessage/delta",
  "invocationId": "inv_abc",
  "sessionId": "hsess_abc",
  "turnId": "turn_abc",
  "author": "codex",
  "content": {
    "role": "model",
    "parts": [{ "text": "hello" }]
  },
  "actions": {
    "stateDelta": {},
    "artifactDelta": {}
  },
  "usage": null,
  "safe": true
}
```

`HarnessEvent` 由 Event Log 投影为 ADK `Event`（去掉内部字段，保留 `id`/`invocationId`/`author`/`timestamp`/`content`/`actions`/`longRunningToolIds`/`nodeInfo`/`output`）。

## 7. 运行模型与状态机

```text
adapter unavailable
  -> probing
  -> ready
  -> preparing_session
  -> turn_running
  -> idle
  -> degraded
  -> unavailable
```

Turn 状态：

```text
queued -> starting -> running -> completing -> completed
queued -> starting -> running -> cancelling -> cancelled
queued -> starting -> running -> incomplete
queued -> starting -> running -> failed
```

状态机规则：

- adapter `probe` 未通过时，registry 不能把该 base 标为 `ready`。
- `stream_events` 抛异常时，Session Runtime 必须收敛为 terminal state。
- adapter 不得在 terminal 后继续发会改变 invocation 状态的事件。
- 同一 session 同一时刻只能有一个 active turn。

## 8. 安全与权限

- Adapter 输入必须已经过 protocol schema validation、admission 和 policy validation。
- Adapter 只能收到 secret ref 或短 TTL token；不得收到长期 raw provider key。
- Adapter event payload 必须先脱敏再交给 event log。
- Harness 原生 config 文件如果必须包含 token，只能使用 ephemeral session dir，且该路径不得进入 artifact/archive。
- Tool restriction enforcement 必须如实标注，不能把 prompt-only 约束宣传成 hard block。
- 沙箱由 Sandbox Runtime 统一创建；adapter 只能声明需求，不能自行扩大。

## 9. 可观测性

每个 adapter 至少报告：

- `haas.adapter.probe`
- `haas.adapter.session.prepare`
- `haas.adapter.turn.start`
- `haas.adapter.turn.event`
- `haas.adapter.turn.terminal`
- `haas.adapter.turn.cancel`
- `haas.adapter.error`

指标维度必须低基数：`adapterId`、`base`、`status`、`errorCode`、`capability`。不得使用 raw model prompt、file path 或 token 作为 label。

## 10. 失败与恢复

| 场景 | 行为 |
|------|------|
| runtime binary 缺失 | adapter `probe.status=unavailable`，harness 不标 ready |
| native schema 不兼容 | fail closed，返回 `haas_adapter_incompatible` |
| 原生事件无法解析 | 写 `haas.adapter.event_unparsed` safe event；若无法判断终态则 turn failed |
| adapter 进程/连接断开 | 尝试重连；无法恢复则 invocation failed 或 incomplete |
| cancel 不支持 | capability 标 `unsupported`，API 返回 `haas_cancel_unsupported` |
| session 不可恢复 | `non_resumable` 或 `session_expired` |

## 11. 测试计划与验收

- Contract tests：所有 adapter 必须通过同一 fake harness test suite。
- Golden events：每个 adapter 维护原生 event fixture 到 ADK Event 投影的映射测试。
- Cancellation：能取消的 adapter 必须证明最终状态是 `cancelled`，不能只返回 200。
- Recovery：adapter crash/restart 后 session inspect 与 stored state 语义明确。
- Sandbox：adapter 声明被 Sandbox Runtime 正确投影，widening 被拒绝。
- Security：adapter env/config/output 反向断言不包含 secret pattern。
- Codex 首期：真实 app-server handshake 和 turn streaming E2E 作为 P0 准出。
