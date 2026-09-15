# Event Log & SSE 组件规格

[English](README.md) | **简体中文**

Status: Draft
Last reviewed: 2026-09-12
Related specs: [HaaS Protocol](../haas-protocol/README.zh-CN.md), [Session Runtime](../session-runtime/README.zh-CN.md), [Harness Adapter](../harness-adapter/README.zh-CN.md), [Manager Delegation](../manager-delegation/README.zh-CN.md), [Security Boundary](../security-boundary/README.zh-CN.md)

## 1. 组件定位

Event Log & SSE 是 HaaS 的事件事实源和实时订阅层。它持久化 canonical events，投影为 ADK `Event`，提供 session/invocation 级 replay-then-live stream。

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

- 持久化带稳定 HaaS `type` 与 type-specific 安全 `haas` metadata 的 canonical event。
- 为每个 invocation 维护从 0 开始的 gapless `sequenceNumber`，并为 turn 外 event 维护独立 gapless session-lifecycle sequence。
- 为 HaaS native stream 维护 session-scoped `eventId`。
- 将 canonical event 投影为 ADK `Event` 或 HaaS event。
- 持久化并流式输出 manager-delegation lifecycle event，包括 restore、workspace-lock queue、approval request 和 approval resolution。
- 支持 replay missed events 后进入 live stream（`Last-Event-ID` / `after_event_id`）。
- 发送 heartbeat 且不产生事件、不推进 cursor。
- 使用 bounded per-subscriber delivery queue。持久 canonical event 绝不 drop 或 coalesce。Subscriber 无法跟上时，记录最后成功 delivery 的 event id 后断开，由 client 从持久 store replay。
- 禁止未脱敏 raw event 进入持久 event log。

不负责：

- 不执行 harness。
- 不修改 session/invocation terminal state。
- 不保存 raw prompt、完整 tool args/result 或 provider payload。
- 不保证跨 session 全局严格有序。Invocation 内通过 invocation sequence 保序，session lifecycle 通过独立 sequence 保序，合并 session replay 通过 session-scoped `eventId` 保序。

## 5. 核心接口

### 5.1 Public Streams

| Endpoint | 协议 | 语义 |
|----------|------|------|
| `POST /run_sse` | ADK SSE | invocation 的 ADK Event stream，完成即关闭 |
| `GET /apps/{app}/users/{user}/sessions/{sid}` | JSON | ADK 原生 replay 通道，返回全部 `events[]` |
| `GET /v1/haas/sessions/{session_id}/events-page` | JSON | 按 eventId 排序的有界 `CanonicalHaasEvent` page；默认 100、上限 1000，`nextCursor` 为最后返回 id 或 null |
| `GET /v1/haas/sessions/{session_id}/events` | HaaS SSE | session canonical event replay/live，带 `after_event_id` |
| `GET /v1/haas/sessions/{session_id}/invocations/{invocation_id}/events` | HaaS SSE | invocation canonical event replay/live |

### 5.2 Internal API

```python
async def append_event(event: CanonicalEvent) -> StoredEvent: ...
async def read_session_events_page(app_name: str, user_id: str, session_id: str, after_event_id: str | None, limit: int) -> EventPage: ...
async def list_approvals(app_name: str, user_id: str, session_id: str, status: str, cursor: str | None, limit: int) -> ApprovalPage: ...
async def read_invocation_events(app_name: str, user_id: str, session_id: str, invocation_id: str) -> list[StoredEvent]: ...
async def stream_invocation(app_name: str, user_id: str, session_id: str, invocation_id: str, after_event_id: str | None) -> AsyncIterator[SSEFrame]: ...
async def stream_session(app_name: str, user_id: str, session_id: str, after_event_id: str | None) -> AsyncIterator[SSEFrame]: ...
def project_adk(event: StoredEvent) -> AdkEvent: ...
def project_haas(event: StoredEvent) -> CanonicalHaasEvent: ...
```

## 6. 数据模型

### 6.1 CanonicalEventRecord（内部）

```json
{
  "schemaVersion": 2,
  "type": "haas.output.text.delta",
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
  "haas": {},
  "observedAtMs": 1786400000000,
  "redactionApplied": true
}
```

时间戳约定：内部 canonical event 使用毫秒 epoch `observedAtMs`；ADK
`Event.timestamp` 由投影层生成，为 `observedAtMs / 1000.0` 的 float 秒。

**`HarnessEvent` → `CanonicalEventRecord` 映射**：adapter 产出 `HarnessEvent`
（[harness-adapter](../harness-adapter/README.zh-CN.md) §6.3），由 Session Runtime 调用
Event Log 归一化后落库：

| HarnessEvent 字段 | CanonicalEventRecord | 规则 |
|-------------------|----------------------|------|
| normalized `type` | 稳定 `type` | 必须按 §6.3 catalog 映射；任意 adapter 值不得直接持久化 |
| `nativeType` | 不落库 | 只在 adapter normalization 内使用的原生 runtime 细节 |
| `invocationId` / `sessionId` / `turnId` | 同名保留 | 必须与执行上下文一致 |
| 执行上下文 `appName` / `userId` | `appName` / `userId` | 为 store 层 scope 隔离持久化；必须匹配 ADK session 三元组 |
| `author` | `author` | 保留 |
| `content` / `actions` / `usage` | `content` / `actions` / 安全 `haas` 字段 | 按稳定 event type 经 `redact()` 后保留或摘要 |
| `safe` | 不落库 | 由 `redactionApplied` 替代 |
| — | `eventId` / `sequenceNumber` / `observedAtMs` / `harnessId` / `adapterId` | 由 Event Log 或执行上下文生成 |

对 invocation-scoped type，`invocationId`、`turnId` 非空，且 `sequenceNumber`
从 0 起、在 invocation 内无空洞。Turn 外发生的 session-scoped lifecycle type 使用
`invocationId=null`、`turnId=null`，其 `sequenceNumber` 在 session lifecycle
namespace 内无空洞。Session stream 使用 session-scoped `eventId` 对两个 namespace
中的全部 record 排序和 replay。`observedAtMs` 为 append 时刻毫秒 epoch。未经
`redact()` 的 raw event 不得落库（fail closed）。

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

稳定 HaaS `type`、`haas`、`sequenceNumber`、`sessionId`、`turnId`、`harnessId`
以及 `adapterId` 等内部字段都不得进入 ADK 投影。ADK `/run` 与 `/run_sse` 保持纯
ADK Event surface。

### 6.3 稳定 HaaS Event Type 与 Metadata

HaaS native stream 发布 `CanonicalHaasEvent`。顶层 `type` 是稳定事实名称；`haas`
对象是该 event 的 typed、redacted payload，不得作为任意 debug-data bag。

| 稳定 `type` | `haas` 必填字段 | 语义 |
|-------------|------------------|------|
| `haas.output.text.delta` | 可选 `itemId`、`modelCallId`、`messagePhase` | model-visible 增量文本；文本仍放 `content.parts[]`；权威 `messagePhase=commentary|final_answer` 区分过程说明与阶段结论，不解析自然语言猜测 |
| `haas.output.reasoning.delta` | 可选 `itemId`、`summaryIndex`、`modelCallId` | policy 允许的脱敏 provider reasoning summary；绝不是普通 commentary 或 raw chain-of-thought |
| `haas.output.item.completed` | `itemId`；可选 `modelCallId`、`messagePhase` | model-visible item lifecycle 完成；无需重复文本即可让 client 确认起初未知的 message phase |
| `haas.tool.started` | `toolCallId`, `toolName`, `safeSummary`；可选 `activityKind`、`commandPreview`、`workingDirectory`、`evidenceRef`、`evidenceExpiresAtMs` | tool execution 开始 |
| `haas.tool.output` | `toolCallId`, `toolName`, `safeSummary`；可选 `activityKind`、`commandPreview`、`workingDirectory`、`outputPreview`、`omittedLineCount`、`evidenceRef`、`evidenceExpiresAtMs` | 脱敏的中间 tool output/status；绝不是无界或未脱敏的完整 tool result |
| `haas.tool.completed` | `toolCallId`, `toolName`, `status`；可选 `activityKind`、`commandPreview`、`workingDirectory`、`durationMs`、`exitCode`、`outputPreview`、`omittedLineCount`、`evidenceRef`、`evidenceExpiresAtMs` | tool execution 成功完成 |
| `haas.tool.failed` | `toolCallId`, `toolName`, `status`, `safeReason`, `retryable`；可选 `activityKind`、`commandPreview`、`workingDirectory`、`durationMs`、`exitCode`、`outputPreview`、`omittedLineCount`、`evidenceRef`、`evidenceExpiresAtMs` | tool execution 失败 |
| `haas.usage.updated` | `usage`；可选 `scope`、`modelCallId`、`cumulativeUsage` | 声明 scope 的真实 normalized usage。新的 model-call-aware producer 使用 `scope=model_call`，将 `usage` 绑定到 `modelCallId`，并可单独携带 turn/thread 累计 snapshot；不含 provider payload，不估算、不向子步骤分摊，usage 未知时不得伪造 zero event |
| `haas.turn.started` | `status` | invocation/turn 开始 |
| `haas.turn.completed` | `status` | 成功 terminal event |
| `haas.turn.failed` | `status`, `code`, `safeReason`, `retryable` | 失败 terminal event |
| `haas.turn.incomplete` | `status`, `code`, `safeReason`, `retryable` | budget/timeout 截断 terminal event |
| `haas.turn.interrupted` | `status`, `controlIntent` | 源 invocation 的可恢复 Pause terminal |
| `haas.turn.cancelled` | `status` | cancelled terminal event |
| `haas.delegation.session_created` | `delegatedSessionId` | delegated contract 创建 |
| `haas.delegation.session_bound` | `delegatedSessionId` | manager/HaaS session binding 建立 |
| `haas.delegation.policy_snapshot` | `delegatedSessionId`, `policyVersion` | policy snapshot 固定 |
| `haas.delegation.policy_updated` | `delegatedSessionId`, `policyVersion` | 显式 policy update 已提交 |
| `haas.delegation.policy_update_pending` | `delegatedSessionId`, `updateId`, `revision`, `fields` | 目标 snapshot 持久接受，reconciliation 不依赖未来 turn |
| `haas.delegation.policy_update_applied` | `delegatedSessionId`, `updateId`, `revision`, `fields` | Runtime readback 验证应用并推进 appliedRevision |
| `haas.delegation.policy_update_failed` | `delegatedSessionId`, `updateId`, `revision`, `fields`, `code`, `safeReason` | 应用失败，appliedRevision 不变，未来 turn 等待修复 |
| `haas.delegation.restore_started` | `delegatedSessionId`, `containerGeneration` | runtime restore 开始 |
| `haas.delegation.restore_failed` | `delegatedSessionId`, `safeReason`, `retryable` | runtime restore 失败 |
| `haas.delegation.workspace_lock_queued` | `delegatedSessionId`, `canonicalWorkspaceHash`, `queuePosition` | 等待 workspace writer lock |
| `haas.delegation.workspace_lock_acquired` | `delegatedSessionId`, `canonicalWorkspaceHash` | workspace writer lock 已获得 |
| `haas.delegation.workspace_lock_released` | `delegatedSessionId`, `canonicalWorkspaceHash` | workspace writer lock 已释放 |
| `haas.delegation.container_ttl_destroyed` | `delegatedSessionId`, `containerGeneration` | idle runtime 资源销毁 |
| `haas.profile.created` | `profileId`, `profileVersion`, `profileFingerprint` | profile revision 创建 |
| `haas.profile.validation_failed` | `profileId`, `profileVersion`, `safeReason` | profile revision 校验失败 |
| `haas.profile.activated` | `profileId`, `profileVersion`, `profileFingerprint` | profile revision 成为 harness active profile |
| `haas.profile.retired` | `profileId`, `profileVersion`, `retiredByProfileId` | profile revision 被新激活的 revision 退役 |
| `haas.profile.rebind_requested` | `profileId`, `profileVersion`, `sessionId` | 已有 session 请求显式 profile rebind |
| `haas.profile.rebind_applied` | `profileId`, `profileVersion`, `sessionId` | rebind 已提交，后续 invocation 使用新 snapshot |
| `haas.approval.required` | `approvalId`, `kind`, `safeSummary`, `policyReason`, `availableDecisions`, `expiresAtMs` | 需要 manager 显式决策 |
| `haas.approval.resolved` | `approvalId`, `status` | approval 已解决 |
| `haas.input.required` | `inputRequestId`, `questions`, `blocking`, `expiresAtMs` | 需要结构化用户输入；与副作用 approval 语义分离 |
| `haas.input.resolved` | `inputRequestId`, `status` | structured input 已回答、过期或取消 |
| `haas.plan.updated` | `counts.pending`、`counts.inProgress`、`counts.completed`、`total` | 结构化任务进度计数发生变化；有意不携带计划正文 |
| `haas.adapter.event_unparsed` | `safeReason`, `retryable` | adapter 原生 event 无法安全映射 |

Terminal event type 仅允许 `haas.turn.completed`、`haas.turn.failed`、
`haas.turn.incomplete`、`haas.turn.interrupted`、`haas.turn.cancelled`。每个 invocation 必须且只能持久化一个
terminal type。Consumer 必须使用 `type` 区分 terminal outcome，不得解析人类文本或
依赖 stream close；stream close 只表示 delivery 完成。

首期 adapter-normalized type 映射：

| Adapter-normalized type | 稳定 HaaS type |
|-------------------------|----------------|
| `harness.text.delta` | `haas.output.text.delta` |
| `harness.reasoning.delta` | `haas.output.reasoning.delta` |
| `harness.output.item.completed` | `haas.output.item.completed` |
| `harness.tool.started` | `haas.tool.started` |
| `harness.plan.updated` | `haas.plan.updated` |
| `harness.tool.output` | `haas.tool.output` |
| `harness.tool.completed` | `haas.tool.completed` |
| `harness.tool.failed` | `haas.tool.failed` |
| `harness.usage` | `haas.usage.updated` |
| `harness.turn.started` | `haas.turn.started` |
| `harness.turn.completed` | `haas.turn.completed` |
| `harness.turn.failed` | `haas.turn.failed` |
| `harness.turn.incomplete` | `haas.turn.incomplete` |
| `harness.turn.interrupted` | `haas.turn.interrupted` |
| `harness.turn.cancelled` | `haas.turn.cancelled` |

Event Log 必须使用 adapter 显式提供的 normalized `type` 做映射，不能只根据 `content` 或 `actions` 猜 canonical type。尤其是经过 policy 允许的脱敏 summary part 在标记 `thought=true` 后必须保持为 `haas.output.reasoning.delta`，不得成为 assistant output；raw reasoning 永远不得进入 Event Log。带 `artifactDelta` 的 tool event 也不得退化为 `haas.adapter.event_unparsed`。`item/started` 与 `item/completed` 是 tool call 的权威生命周期边界；output delta 只是按 `toolCallId` 关联的可选中间更新。每个 started tool 必须且只能有一个 completed 或 failed terminal record，包括取消与 adapter failure。

`itemId` 与 `modelCallId` 是与渲染无关的关联事实。`itemId` 把同一条 model-visible
message 或 reasoning-summary item 的全部 delta 关联起来；`modelCallId` 标识 turn 内一次
真实模型 round trip，并在持久 replay 中保持稳定。Runtime 没有原生 call id 时，adapter
按 invocation 生成确定性序号，例如 `mcall_0002`。Output、reasoning、由其触发的 tool
call 与对应 model-call usage snapshot 使用同一 id；tool 完成后出现的新 model output
开启下一次 model call。Consumer 必须保持事件顺序，不得把 reasoning 拼接进 commentary
或 final-answer 文本。Legacy event 缺少关联字段时保持 unknown；consumer 可组成单个
legacy group，但不得补造 per-call usage。
Agent-message delta 在 phase 未知时保持未分类。Native lifecycle 后续提供权威
`commentary|final_answer` phase 时，adapter 使用相同 `itemId` 发送
`haas.output.item.completed`；该事实不重复 message 文本。Consumer 原地重新分类已有 item，
不得重复 output，也不得为了等待 item completion 而取消全部流式展示。

`haas.usage.updated` 只报告实测值。`scope=model_call` 表示 `usage` 是该模型调用的真实
用量；可选 `cumulativeUsage` 是独立的 turn/thread 累计 snapshot，汇总 model call 时不能
再次相加。Harness 提供时保留 `reasoningOutputTokens` 与 cache read/write counter。Tool
执行本身没有模型 token；除非 harness 另行提供有 scope 的实测 usage，否则不得声明
tool 自身 token。Consumer 不得按文本长度、耗时、数量或其他估算把一次调用的 input/output
token 分摊给 commentary、reasoning、tool 或 result 子事件。
Canonical `inputTokens` 包含该调用报告的全部 prompt input；`cacheReadTokens` 是其中的
缓存子集，不是额外相加量。Provider 将 reasoning 计入 output 时，`outputTokens` 包含它，
`reasoningOutputTokens` 是其中的 reasoning 子集。因此 stage/turn total 只对 input 与 output
各求和一次，cache/reasoning 以“其中”明细展示。

公开过程事件只携带有界、脱敏事实。Reasoning event 只包含 policy 允许的进度摘要，不包含 raw chain-of-thought。Tool metadata 可包含 `activityKind=command|read|search|edit|tool`；它只分类已观察到的操作，不规定 UI 布局或文案。该字段必须由 adapter 或 policy catalog 产生，后续不得从 tool name 字符串、argument 或 summary 猜测；未知操作统一使用 `tool`。
Command activity 的 `commandPreview` 是单行有界预览，必须移除 credential value 与 signed-URL 材料；`workingDirectory` 是 container-relative 或其他不敏感的非 host 路径提示。两者都不得包含绝对 host path，只用于持久识别动作，不能替代短期 evidence body。

Tool start 包含 `toolCallId`、稳定 `toolName` 和句子式 `safeSummary`。已知时，summary 描述安全操作；不得序列化 metadata（`key=value`）、重复 `Used <toolName>`、包含 host 绝对路径，或编造 native event 中不存在的目标。没有安全细节的通用操作可以使用 `Run tool` 等稳定中性摘要；客户端使用本地化 fallback title，不能显示协议字段名。

Tool output 与 terminal metadata 可携带 `outputPreview`、`omittedLineCount`、`durationMs` 与 `exitCode`。`outputPreview` 在持久化前完成脱敏，最多 20 个逻辑行和 4096 UTF-8 bytes；删减内容时用 `omittedLineCount` 保留 head/tail 语义。这些是可观察执行事实，不是渲染指令；客户端可在普通视图选择更小预览。Command exit code 非零时必须发 `haas.tool.failed`，不能发 `haas.tool.completed`。完整 command array、完整 stdout/stderr、文件内容、host path 与 model-provider payload 保持私有。8 MiB execution-evidence 上限内的 command output 只能通过独立鉴权的短期 evidence endpoint 读取；超过该上限的完整输出需要 Artifact Store object。两条路径都不能放宽 event payload。Token 级 text/reasoning delta 可以在投递和存储时合并，但必须保持相对 tool/terminal event 的顺序。

Command lifecycle event 还可携带 opaque `evidenceRef` 和
`evidenceExpiresAtMs`。这些字段只是定位符，不包含 command 或 URL 内容；它们指向
Security Boundary §7.1 定义的非持久 execution-evidence response。Event replay 可以保留
已过期 ref，使客户端能解释证据为何不可用；consumer 必须把 `410` 解释为 evidence
过期，而不是 tool result 缺失。Evidence ref 不放宽 `safeSummary` 或 `outputPreview` 的
脱敏规则。

Terminal write 必须按 invocation 幂等。如果 adapter 已发出 normalized terminal event，Session Runtime 必须用同一个事件更新 invocation/turn 状态，不得再次追加 synthetic terminal。只有 adapter 没有终态时才允许生成 synthetic terminal。重复 terminal 是 release-blocking integrity failure。

未知原生 runtime message 不得复制到 `type` 或 `haas`。Adapter 先尝试映射到已知
normalized type；无法安全映射时，Event Log 只写带稳定 safe reason 与 retryability
的 `haas.adapter.event_unparsed`。若无法判断 terminal outcome，Session Runtime 必须
使 invocation 失败，不得透传原生 event。

Public native event 不得包含 host path、完整 tool argument/result、raw prompt 或
reasoning、credential、provider response body、native event name 或内部 container
address。

#### 6.3.1 Stored Event 迁移

`CanonicalEventRecord` schema version 2 新增 required `type` 与 `haas`。Store 打开
version-1 record 时，必须先完成 forward migration 才能提供 native replay：

- 可信历史 terminal `actions.stateDelta.status` 映射为对应 `haas.turn.*` type 与 typed
  status metadata；
- 其他包含 model text 的 non-terminal record 映射为 `haas.output.text.delta`，`haas` 为空；
- 其余 record 映射为 `haas.adapter.event_unparsed`，使用
  `safeReason=historical_event_type_unknown`、`retryable=false`；
- migration 不得从人类文本、adapter id 或其他 heuristic 重建 tool、approval、
  delegation 或 native runtime type；
- migrated record 保留 `eventId`、scope id、ordering、timestamp、content、actions。
  Migration 必须幂等且只向前。

Schema version 2 之后新增的可选 tool activity 字段不要求重写已存记录。旧记录原样
replay；consumer 将缺失或未知的 `activityKind` 视为 `tool`，不可用的 duration、exit
code、preview 与 omission count 保持缺失。Consumer 不得从 `safeSummary` 重建这些事实。
缺少 `evidenceRef` 表示详细短期证据从未被采集或已不可寻址；旧事件无需迁移。

### 6.4 HaaS Native 投影与 SSE Frame

`project_haas()` 输出 public `CanonicalHaasEvent`，并移除内部 `userId`、`adapterId`、
`schemaVersion`、`redactionApplied` 字段：

```json
{
  "type": "haas.approval.required",
  "eventId": "evt_0000000001042",
  "invocationId": "inv_abc",
  "sessionId": "hsess_abc",
  "turnId": "turn_abc",
  "appName": "chrn_codex_default",
  "harnessId": "chrn_codex_default",
  "author": "codex",
  "sequenceNumber": 7,
  "content": {"role": "model", "parts": []},
  "actions": {"stateDelta": {}, "artifactDelta": {}},
  "haas": {
    "approvalId": "appr_abc",
    "kind": "tool",
    "safeSummary": "Run a command in the workspace",
    "policyReason": "tool_requires_approval"
  },
  "observedAtMs": 1786400000000
}
```

每个 HaaS native SSE `data:` frame 只包含一个该 JSON 对象。ADK `/run_sse` 继续使用
§6.2 的 ADK projection，绝不输出这些 HaaS-only 顶层字段。 Turn 外的 session-scoped lifecycle event 使用 `invocationId=null`、`turnId=null`。

Heartbeat 是 SSE comment，不产生 event：

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
  -> expired/missing cursor -> 410 haas_offset_expired; do not subscribe or guess
  -> subscribe live
```

Ordering invariants:

- 持久 event 在 invocation 内按产生顺序、无空洞输出；subscriber delivery 不得静默 drop 或 merge。
- 同一 item 的事件顺序稳定。
- 非流式 `/run` 返回的 ADK event array 必须等于 `/run_sse` 的完整有序 ADK event sequence，包括 accepted failure/incomplete/cancel terminal event。
- `/run_sse` 必须在 invocation 运行中发布事件，不得等到 invocation 终态后再 replay 完成批次。
- Stream close 只表示 delivery 完成；execution outcome 由 canonical terminal `type` 决定。
- `haas.output.text.delta` 是 append-only UTF-8 text fragment。Consumer 先按 `eventId` 去重，再按 `sequenceNumber` 顺序只拼接一次；它绝不是 replacement snapshot。Final assistant message 是累计 delta 的 client projection，不是第二个 canonical text event。
- Tool event 通过 `haas.toolCallId` 关联；started 后可有零或多个 output，最终必须且只能有一个 completed/failed。

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
| ADK `/run_sse` `Last-Event-ID` 过期 | 返回 `410 haas_offset_expired`；不得猜测或重启 turn |
| Native SSE/page `after_event_id` 过期 | 默认返回 `410 haas_offset_expired`。未来可增加显式 `replay_policy=reconcile` typed gap extension；禁止隐式 reconcile |
| Subscriber delivery queue 满 | 不 drop/coalesce 持久 event；记录最后 delivered event id 后断开慢 subscriber，由 client reconnect/replay。Invocation execution 与 terminal persistence 继续 |
| 接受后 required terminal append 失败 | invocation 不得宣称 completed。Session Runtime 持久化 authoritative failed state 与幂等 integrity envelope（`accepted=true`、invocationId、HTTP 503 `haas_store_unavailable`）。响应头前返回该错误；SSE 响应头后异常关闭并要求 readback。不得伪造 terminal event |
| Active stream 在 client idle timeout 前没有 event | 以小于所有受支持 client/proxy idle timeout 的周期发送 heartbeat comment；不得关闭或取消 invocation |
| proxy buffering | response 设置 `Cache-Control: no-cache`；测试验证 progressive flush |
| adapter 重复 terminal | 保留第一条 terminal，后续写 diagnostic warning |
| delegated approval pending | stream 保持打开并发送 heartbeat；只有 approval resolution、cancel 或 timeout 后写 terminal state |

## 11. 测试计划与验收

- Unit：稳定 type 映射、type-specific `haas` 必填字段、sequence 分配、terminal 唯一、heartbeat 不推进 cursor，以及 ADK/native 两种投影映射。
- Integration：accepted completed/failed/incomplete/cancelled `/run` vs `/run_sse` HTTP-200 event parity、pre-acceptance HTTP error、terminal-store integrity failure、有界 `events-page` ordering/cursor/limit、超预算 ADK Session 413、invocation status readback、paginated waiting approval、reconnect replay。
- Integration：`/run_sse` 在 terminal state 前渐进 flush 输出。
- Heartbeat：静默 tool/model 阶段超过 Manager 的 90 秒 idle timeout 时，重复 comment 保持 stream，且不推进 event cursor、不触发 invocation cleanup。
- Integration：output/tool/usage/terminal 以及 manager-delegation restore、queue、approval event 以稳定 `type` 和经过校验的脱敏 `haas` metadata 出现在 native stream 中。
- Interaction：approval 与 input-required event 在等待期间保持 non-terminal，支持断线 replay，只 resolve 一次，并在续接的 tool/model event 前保持因果顺序。
- Golden mapping：混合 text/reasoning/tool lifecycle 输入保留 adapter 显式类型；thought text 不成为 assistant output；每个 `toolCallId` 只有一个 start 和一个 terminal；unknown event 不含 raw native payload。
- Integrity：adapter terminal 加 `finalize_turn` 只产生一个 canonical terminal；replay/reconnect 不得生成第二个终态。
- Scale：长时间 tool-heavy turn 保持因果顺序，同时通过有界合并避免 token 级 event 无限制增长 event store。
- Backpressure：慢客户端不阻塞 adapter terminal 写入，也不会静默丢失/合并 event；disconnect + replay 必须重建相同 sequence。
- Folding：重复 replay id 不产生重复文本；有序 text delta 只拼接一次；toolCallId transition 合法；live/replay 的 final assistant projection 完全一致。
- Security：raw prompt、Authorization、cookie、native event name、完整 tool args/result 不进入 public native event；`project_haas()` 会剥离 internal-only 字段。
- Security：不同 user 或 app 使用同一裸 `sessionId` 时事件互不混读；跨 user
  native event replay 返回 404。
- Compatibility：ADK client 对 `/run_sse` 输出逐条解析成功，stream 关闭语义正确。
