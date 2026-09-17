# Session Runtime 组件规格

[English](README.md) | **简体中文**

Status: Draft
Last reviewed: 2026-09-15
Change ID: unified-runtime-approval-policy, long-task-model-proxy-stability
Related specs: [HaaS Protocol](../haas-protocol/README.zh-CN.md), [Harness Registry](../harness-registry/README.zh-CN.md), [Harness Profile](../harness-profile/README.zh-CN.md), [Harness Adapter](../harness-adapter/README.zh-CN.md), [Event Log & SSE](../event-log-sse/README.zh-CN.md), [Admission Control](../admission-control/README.zh-CN.md), [Manager Delegation](../manager-delegation/README.zh-CN.md)

## 1. 组件定位

Session Runtime 是 HaaS 的执行事实 owner。它管理 session、invocation（ADK 一次 `/run`）、turn、container、lease、idempotency、profile snapshot 和 terminal state，并通过 Harness Adapter 驱动具体 agent。

ADK 的 session 由 `(appName, userId, sessionId)` 三元组唯一标识；`invocation` 是 public 运行单元，`turn` 是 adapter 内部执行单元。首期两者一一对应，但 Session Runtime 必须保留未来一个 invocation 拆成多个内部 turn 或 replay turn 的空间。

### 1.1 长任务后台执行定位

HaaS 面向离线后台 agent 工作，正常 accepted invocation 不得因为短交互客户端超时而
失败。Runtime turn 默认 deadline 是 24 小时（`86400` 秒），同时也是
`haas.timeoutSeconds` 的最大可接受值，除非未来 capability 显式发布更长的 durable
execution class。客户端可以为单次 turn 请求更短 timeout，但 Manager local 默认不得把
HaaS-backed work 缩短到长任务默认值以下。

该 deadline 是最后安全兜底，不是 activity 或 stream-idle timeout。`/run_sse` 断线、
GUI WebSocket 断开、超过 model stream-idle 区间、或执行大量 tool call 本身都不得终止
invocation。Runtime 必须持续续租 active-turn lease，并保持 model/MCP capability 有效或
安全刷新，直到 invocation 达到权威 terminal、被显式取消，或到达 24 小时 deadline。

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
- 将 session 创建时的 active harness profile 冻结为 `EffectiveHarnessProfile`。
- 通过 Event Log 把 adapter event 映射为稳定 canonical `haas.*` event type，并维护 session `state`（ADK `stateDelta`）；terminal outcome 使用 canonical `type`，不得解析人类文本或依赖 stream close。
- 处理 streaming run（`/run_sse`）和 non-streaming run（`/run`）的一致终态。
- 对 delegated session，持久化 HaaS session、native session reference、delegated-session reference、approval wait 和后续恢复所需的 runtime generation metadata。
- 管理 cancel、长任务 invocation deadline、step budget、session expiry、session deletion。
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
| `GET /v1/haas/sessions/{sid}/invocations/{id}` | 读取恢复所需权威 invocation 状态 |
| `GET /v1/haas/sessions/{sid}/approvals` | 使用 status/cursor 分页列出脱敏 approval record |
| `GET /v1/haas/sessions/{sid}/input-requests` | 使用 status/cursor 分页列出脱敏 structured input record |
| `POST /v1/haas/sessions/{sid}/invocations/{id}/pause` | 中断 running invocation，并且只在观测到 native terminal 后提交为可恢复的 `interrupted` |
| `POST /v1/haas/sessions/{sid}/invocations/{id}/continue` | 从 `interrupted` 源在同一逻辑 session 上创建并流式执行新的 invocation/turn |
| `POST /v1/haas/sessions/{sid}/invocations/{id}/cancel` | 取消运行中 invocation，幂等（HaaS native） |
| `POST /v1/haas/sessions/{sid}/approvals/{approval_id}` | 解析等待中的 harness action 的 manager 审批决策 |
| `POST /v1/haas/sessions/{sid}/input-requests/{input_request_id}` | 回答 waiting structured input 并续接原 invocation |
| `POST /v1/haas/sessions/{sid}/policy` | 为后续 invocation 暂存已授权的 approval/network/workspace policy revision |

### 5.2 Internal API

```python
async def run(app_name: str, user_id: str, session_id: str | None, message: Message, ext: RunExtensions) -> InvocationRecord: ...
async def get_session(app_name: str, user_id: str, session_id: str) -> SessionRecord: ...
async def apply_state_delta(app_name: str, user_id: str, session_id: str, delta: dict) -> SessionRecord: ...
async def delete_session(app_name: str, user_id: str, session_id: str) -> None: ...
async def start_turn(req: TurnStartRequest) -> TurnRecord: ...
async def mark_turn_terminal(turn_id: str, result: TurnTerminalResult) -> None: ...
async def get_invocation(session_id: str, invocation_id: str) -> InvocationRecord: ...
async def list_approvals(session_id: str, status: str, cursor: str | None, limit: int) -> ApprovalPage: ...
async def list_input_requests(session_id: str, status: str, cursor: str | None, limit: int) -> InputRequestPage: ...
async def pause_invocation(session_id: str, invocation_id: str) -> InvocationRecord: ...
async def continue_invocation(session_id: str, invocation_id: str, instruction: str | None) -> InvocationRecord: ...
async def cancel_invocation(session_id: str, invocation_id: str) -> InvocationRecord: ...
async def resolve_approval(session_id: str, approval_id: str, decision: ApprovalDecision) -> ApprovalRecord: ...
async def answer_input_request(session_id: str, input_request_id: str, answers: InputAnswers) -> InputRequestRecord: ...
async def update_policy(session_id: str, expected_revision: int, policy: PolicyMutation) -> SessionRecord: ...
async def rebind_profile(session_key: SessionKey, profile_id: str, expected_version: int | None) -> SessionRecord: ...
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
  "controlState": "idle",
  "supportsResume": false,
  "resumableInvocationId": null,
  "containerId": "cntr_abc",
  "sandboxId": "sbx_abc",
  "state": {},
  "effectiveProfileSnapshot": {
    "profileId": "hprof_abc",
    "profileVersion": 12,
    "profileFingerprint": "sha256:profile",
    "providerFingerprint": "sha256:provider",
    "mcpVersion": "sha256:mcp",
    "skillsVersion": "sha256:skills",
    "agentsMdVersion": "sha256:agents-md",
    "workspacePolicyVersion": "sha256:workspace-policy"
  },
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

`effectiveProfileSnapshot`（Python 私有字段名为 `effectiveProfile`）是深拷贝的内部执行合同，不进入 ADK `Session` 输出。HaaS native
session/detail 或 manager binding 只能暴露 profile id/version/fingerprint 和各 domain
fingerprint，不得暴露 provider credential、MCP header、AGENTS.md 原文、host path 或
adapter-native config。

### 6.2 InvocationRecord

```json
{
  "id": "inv_abc",
  "object": "invocation",
  "sessionId": "hsess_abc",
  "appName": "chrn_codex_default",
  "turnId": "turn_abc",
  "status": "accepted",
  "acceptedAtMs": 1786400000000,
  "startedAtMs": null,
  "completedAtMs": null,
  "model": "gpt-5.6-terra",
  "requestedModel": "gpt-5.6-terra",
  "timeoutSeconds": 86400,
  "deadlineAtMs": 1786486400000,
  "idempotencyKeyHash": "idem_sha256",
  "terminalEventId": null,
  "continuedFromInvocationId": null,
  "continuedFromTurnId": null,
  "nativeTurnRef": {
    "adapterId": "codex-app-server",
    "threadId": "codex_thread_abc",
    "turnId": "codex_turn_abc",
    "generation": 1
  },
  "executionContext": {
    "sandbox": {"mode": "workspace-write"},
    "policy": {"approvalPolicy": "on-request", "network": {"defaultAction": "allow"}},
    "policyRevision": 1,
    "principalId": "p_123"
  },
  "sessionControl": {
    "controlState": "running",
    "supportsResume": false,
    "resumableInvocationId": null
  },
  "error": null
}
```

`turnId` 首期与 invocation 1:1（id 不同，映射持久化在 `InvocationRecord.turnId`）。
`acceptedAtMs` 与初始 `status=accepted` record 原子写入，是持久 execution acceptance
边界；adapter-owned execution 开始前 `startedAtMs` 保持 null。未来 1:N 时 invocation
聚合多个 turn 的事件（按 `turnId` 分组）。内部时间戳统一毫秒 epoch，公开面由投影层
转为 ADK float 秒。`executionContext` 是私有、深拷贝且不含 credential 的执行快照，
记录 native turn 实际使用的 sandbox、policy 与 principal identity。`nativeTurnRef`
是 adapter 私有恢复引用；对 Codex，它保存 app-server 原生 `threadId` 与 `turnId`，
用于同一 session 中断或重启后的补偿续接。二者都不得进入公开 invocation、ADK、
event、log 或 GUI 投影。

Fresh session 的 policy revision 1 默认使用 `workspace-write`、公网 allow 与
`on-request` approval，除非授权的更高层 policy 进一步收窄。每个 invocation 在私有
execution context 中保存实际 applied policy revision。Session policy mutation 使用 expected
revision 乐观并发与 idempotency key，持久化 desired/applied state，且绝不改变已 accepted
invocation。Revision pending 或 failed 时新工作等待。响应始终携带完整 desired 与 applied
snapshot，防止 client 把失败目标误显示为已生效。Action-scoped approval resolution 是独立
API，不递增 policy revision。

### 6.3 TurnRecord

```json
{
  "id": "turn_abc",
  "invocationId": "inv_abc",
  "sessionId": "hsess_abc",
  "status": "running",
  "continuedFromTurnId": null,
  "startedAtMs": 1786400000000,
  "completedAtMs": null,
  "nativeTurnRef": {
    "adapterId": "codex-app-server",
    "threadId": "codex_thread_abc",
    "turnId": "codex_turn_abc",
    "generation": 1
  }
}
```

`SessionRecord` 只有在完整 projection 不超过 1000 events 与序列化 8 MiB 时才能无损投影为 ADK `Session`（`{id, appName, userId, state, events[], lastUpdateTime}`）。更大读取返回 `413 haas_session_read_too_large`，禁止截断；client 使用 native `events-page`。内部字段（tenantId 等）不进入 public 输出。

`nativeSessionRef` 与 `nativeTurnRef` 是 harness 原生会话状态的持久补偿边界。
Session Runtime 必须在 prepare 或 resume 后持久化 `PreparedSession.nativeRef`，
在 `start_turn` 后把 `TurnHandle.opaque` 同时持久化到 invocation 与 turn；当
turn handle 携带更强的原生 session key（例如 Codex `threadId`）时，还必须更新
`nativeSessionRef`。同一逻辑 HaaS session 的后续 invocation 必须先把持久化的
native session reference 传入 `ResumeSessionRequest`，再启动下一次 native turn。
如果 adapter 返回 `nonResumable`，runtime 保留逻辑 HaaS session，并只在私有状态
记录不可恢复；不得伪装旧 native 上下文已恢复。

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
    "policyReason": "tool_requires_approval",
    "availableDecisions": ["approved", "denied", "cancelled"],
    "availableScopes": ["action"],
    "expiresAtMs": 1786400900000
  },
  "decision": null,
  "createdAtMs": 1786400000000,
  "resolvedAtMs": null
}
```

审批记录必须持久化，确保 SSE 断线或 sidecar restart 不会丢失等待中的决策。记录只保存安全摘要、policy reason、server-advertised decision/scope 与不晚于 invocation deadline 的 expiry，不保存完整 tool argument 或 raw prompt。P0 唯一 scope 是 `action`：审批只授权该记录标识的精确 native action，并仅恢复同一 invocation 一次；不得授权后续 turn/session，也不得修改长期 policy。长期变更只能走带 revision 的 `/policy` API。即使调用方丢失了原始 idempotency key，对已解析 approval 重复提交相同决定也必须幂等返回既有记录，且不得再次回复原生 harness request；提交相反决定仍返回 `409 haas_approval_state_conflict`。

invocation 进入非成功终态（`failed|incomplete|interrupted|cancelled`）时，必须以同一 terminal timestamp 将属于该 invocation 的所有 waiting approval 和 input request 原子关闭为 `cancelled`。这些记录继续作为审计/恢复证据保留，但不得出现在 `status=waiting` 列表或被恢复成交互卡。成功终态在 blocking interaction 解决前可以继续保持 waiting。interaction read 也必须执行该 invariant 的对账，防止旧版本进程遗留的数据在重启后重新暴露 stale card。

### 6.5.1 InputRequestRecord

```json
{
  "id": "inreq_abc",
  "sessionId": "hsess_abc",
  "invocationId": "inv_abc",
  "turnId": "turn_abc",
  "status": "waiting",
  "questions": [
    {
      "id": "scope",
      "header": "Review 范围",
      "question": "需要 review 哪个变更？",
      "options": ["当前 diff", "最近两个 commit"],
      "allowText": true,
      "multi": false,
      "secret": false
    }
  ],
  "blocking": true,
  "createdAtMs": 1786400000000,
  "expiresAtMs": 1786400900000,
  "resolvedAtMs": null
}
```

Input request 与 approval 语义分离：approval 授权拟执行的副作用，input request 提供任务数据。Record 只保存可渲染的问题 metadata，以及不晚于 invocation deadline 的 expiry。非 secret answer 可保存在 Manager-owned transcript；secret answer 必须通过 private handle 投递，不得进入 HaaS event、task state、log 或 transcript。

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

Release 幂等：`InvocationRecord(status=accepted)` 持久化前的任意失败都释放
reservation。Accepted 后 reservation 保留，并存储 `accepted=true`、`invocationId`、
HTTP 200 与有序 ADK events。Invocation 运行中的 matching retry 从头 replay 已持久化
事件并 attach live delivery，不创建第二个 turn；terminal 后 matching retry 返回 HTTP
200 与完全相同的完整 event sequence，包括 failed/incomplete/interrupted/cancelled terminal event。

若接受后 required terminal-event 持久化失败，idempotency 存储带
`accepted=true`、`invocationId`、HTTP 503 与安全 `haas_store_unavailable` body 的
integrity-failure envelope。重试 replay 同一完整性错误；不得转为 `haas_adapter_error`，
也不得据此重启 harness。

## 7. 运行模型与状态机

Delegated 配置更新遵循 Manager Delegation §5.1.1：accepted/running/cancelling invocation 保留 applied snapshot，后续 acceptance 等待 desiredRevision 验证应用，fenced reconciler 不依赖新 turn。Native state/worker receipt 跨 TTL 保存在 session volume（Container Runtime §6），只有 native id 不足以恢复。Replay expiry 见 Protocol §6.8：非终态 reservation 持续保护，过期终态 key 返回 haas_idempotency_expired，Manager 新 attempt 是同一逻辑 session 内独立 invocation。

### 7.1 Invocation

```text
request_validated -> accepted -> running -> completed
request_validated -> accepted -> running -> incomplete
request_validated -> accepted -> running -> failed
request_validated -> accepted -> running -> pausing -> interrupted
request_validated -> accepted -> running -> cancelling -> cancelled
```

`interrupted` 对源 invocation/turn 是终态，但在 session 层仍可恢复。Pause 与 Stop 是不同
意图，即使 Codex adapter 都使用 native `turn/interrupt` 原语。Session Runtime 必须先持久化
意图，再发送 native request；RPC 返回只算 acknowledgement，只有观测到匹配的 native terminal
后才能提交 `interrupted`。若自然完成先赢得竞态，必须保留原终态并返回
`409 haas_invocation_not_running`。

Continue 只接受该 session 最新且可恢复的 `interrupted` invocation。它先验证/恢复 native
session，再创建新的 accepted invocation 与 turn，并在新记录写入
`continuedFromInvocationId`、`continuedFromTurnId`；新 invocation 必须继承 source 的
私有 execution context，确保 workspace、network、approval 与 principal 限制不会静默
重置或被 Continue 请求扩大。若源 invocation 后已有授权 policy revision 成功 applied，
Continue 使用并记录该新 snapshot；否则继承源 snapshot。Credential 从冻结的 session profile
重新解析，不进入该快照。
源记录保持不可变。同一
`Idempotency-Key` 重试返回同一个新 invocation；并发 control/run mutation 由 session lease
串行化。Continue 不复用源 invocation id。

Cancel 也接受最新可恢复的 `interrupted` 源。此时不得改写不可变的源 invocation/turn，
也不得追加第二个 terminal；只原子设置 session `controlState=cancelled`、
`supportsResume=false` 并清空 `resumableInvocationId`。Invocation readback 与 pause/cancel
response 都携带当前 `sessionControl` 投影，使 Manager 重连后能区分 paused 源和已放弃源。

Manager Stop 与 invocation acceptance 发生竞态时，只能在 accepted `sessionId` 和
`invocationId` 已知后投递。Cancel HTTP response 只确认当前状态，不能代替 terminal event。
Session Runtime 仍负责驱动 adapter cancel，并且只持久化一个 `haas.turn.cancelled`。消费方
必须继续 replay/readback，直到看到该 terminal；否则显示可恢复的取消失败。仅关闭发起请求
的 SSE 连接绝不能把 invocation 转为 `cancelled`。

只有初始 InvocationRecord 持久化成功后才能进入 `accepted`，且必须发生在 native
turn/provider/tool/workspace 副作用之前。该写入前的 preflight failure 返回结构化 HTTP
错误，不创建 invocation；该写入后的所有失败都收敛为 terminal state/event。

每个 accepted invocation 必须存储实际生效的 timeout budget 与计算出的 deadline。
默认值为 `86400` 秒。服务端必须把超过 24 小时的请求 clamp 到 `86400` 秒，拒绝
非正数，并在显示 timeout 信息的 readback/diagnostics 中暴露实际生效值。Deadline
按 accepted invocation 的 wall-clock 执行时间计算，不按单个 SSE client 连接计算。
到达 deadline 时，HaaS 先尝试 adapter-native interrupt/cancel，然后持久化唯一
terminal event，`haas.code=haas_request_timeout`、`haas.safeReason=long_task_deadline_exceeded`；
如果 native session 仍可恢复或操作可安全 replay，`haas.retryable=true`。Partial output
和 tool evidence 必须保持可读。

Terminal states are immutable。正常到达 terminal 的 accepted invocation 一律 HTTP 200：
`/run` 返回有序 ADK event array，`/run_sse` 发送 terminal ADK event 后关闭。每个 invocation 必须且只能持久化一个匹配的 canonical terminal type（`haas.turn.completed|failed|incomplete|interrupted|cancelled`），其 `haas.status` 必须等于 `InvocationRecord.status`。Terminal event 写入与对外发送前，Session Runtime 必须在同一 lease/fencing guard 下先持久化对应 invocation/turn 终态并合并 session 终态，除非 store 提供覆盖这些写入的单一原子边界；恢复流程不得看到 terminal event 已完成而 invocation/turn 仍为 `running`。

Invocation terminal 只描述一次 harness turn，不足以证明用户的多步骤任务完成。Session Runtime 持久化权威 invocation/turn outcome 与 pending interaction；Manager 另行拥有 task-level plan、verification 和有界 continuation 状态，不得把 `failed`、`incomplete`、`interrupted`、`cancelled` 或 pending blocking request 转换为任务完成。

### 7.2 Session

Session Runtime 使用短 TTL 的 active-turn lease 来串行化同一 `(appName, userId, sessionId)` 的写入。默认 lease TTL 为 30 秒，默认续租周期为 10 秒；续租周期必须小于 TTL 的一半。配置超时时间超过一个 lease TTL 的 turn，必须持续续租直到进入终态。turn 正常结束、失败、取消或超时时，续租后台任务必须被取消并等待结束，不能残留后台任务。

active turn 写入 event、session/invocation/turn 终态前，都必须通过 store 的 holder/token fencing 校验。lease 已过期或已被接管的 stale holder 不得继续 append event、合并 `stateDelta` 或覆盖终态。由于 invocation 已 accepted，fencing loss 在当前 fencing owner 可持久化时收敛为带稳定 safe reason 的 `haas.turn.failed`；不得表现成未接受的 HTTP 502。若没有 holder 能持久化 terminal evidence，使用 post-acceptance store-integrity failure 合同。

```text
new -> active -> idle -> active
new -> active -> pausing -> paused -> resuming -> active
new -> active -> cancelling -> idle
new -> active -> expired
new -> active -> deleted
new -> active -> non_resumable
```

规则：

- 首次 `/run` 缺省 `sessionId` 时创建 `hsess_<rand>`；后续相同 `(appName, userId, sessionId)` 复用。
- 同一 session 内一次只运行一个 invocation；并发返回 `409 session_busy`。
- `controlState`、`supportsResume`、`resumableInvocationId` 是持久化的 HaaS native control fact。`controlState` 取值为 `idle|running|pausing|paused|resuming|cancelling|cancelled|control_degraded`；它们都不修改 ADK Session schema。
- paused session 对普通 `/run` 返回 `409 haas_resume_required`；只有 `continue` 可以创建下一 invocation。
- Cancel 对逻辑 session 不可逆（`supportsResume=false`）；Pause 保留 native state 且 `supportsResume=true`。
- `model` 可在同一 session 内逐 run 变化。
- `profile` 不随 active profile 指针自动变化。已有 session 请求不同
  `haas.profileId/profileVersion` 时返回 `409 haas_profile_rebind_required`；显式
  `profile-rebind` 只影响 rebind 后的新 invocation，不能修改 running invocation。
  带 `delegatedSessionRef` 的 manager-delegated session 必须拒绝 `profile-rebind`，
  返回 `409 haas_profile_rebind_unsupported`；只能通过 delegated policy 更新路径重新配置，
  该路径接受新的 `profileRef` 并就地替换有效快照，不需要新建 session。若 delegated
  policy 更新在 running invocation 期间到达，Session Runtime 将其持久化为
  `pendingPolicyUpdate` 并在下一次 invocation 开始前应用；running invocation 不被
  修改，也不会因该更新被拒绝。
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

Adapter 调用（`prepare_session`、`start_turn`、`stream_events`、`finalize_turn`）运行在同一个 invocation deadline 内。默认 deadline 为 24 小时（`86400` 秒），并可在 `SessionRuntime` 中按 `1..86400` 的支持范围配置。该值必须大于 lease TTL，保证正常长 turn 在 deadline 前持续续租。Stream-idle、HTTP response-header 和 GUI WebSocket timeout 是独立 transport budget，不得取消已 accepted work。

Deadline 到达后，Runtime 会取消正在等待的 adapter 调用，写入 canonical `type=haas.turn.failed` 与 `haas={status:"failed", code:"haas_request_timeout", safeReason:"long_task_deadline_exceeded"}`，并生成兼容的 ADK `actions.stateDelta.status/reason` 投影，持久化 invocation/turn/session 终态，由 API 层释放 admission quota，并用该 failed terminal event 完成幂等 reservation，使重试不再看到 pending key 或降级 HTTP 状态。若 adapter 能证明 native state 可恢复，terminal 应为 `incomplete` 且 `retryable=true`；否则保持 `failed`，但必须保留 partial progress。

| 场景 | 行为 |
|------|------|
| request 在 InvocationRecord acceptance 写入前失败 | 释放 idempotency reservation；返回结构化 4xx/5xx；不创建 invocation/terminal event |
| InvocationRecord accepted 写入成功 | 保留带 accepted=true/invocationId 的 idempotency reservation；所有正常 terminal outcome 都 replay HTTP 200 + 相同 ADK events |
| sidecar 进程重启 | 首次权威 invocation readback 返回前检查持久化的 `running` invocation。若 adapter 能证明 owner 并 inspect/resume 原 native turn，则恢复同一 invocation；否则使用新的 fencing token 获取 session lease，把 orphan invocation/turn 收敛为不可原生续接的 `incomplete`，追加且只追加一个 `code=safeReason=sidecar_restart_execution_lost`、`retryable=true` 的 `haas.turn.incomplete`，关闭其 pending interaction，把 session control 恢复为 `idle`，并用 canonical event history 完成已 accepted 的 idempotency reservation。若其他进程仍持有未过期 lease，则阻断该次对账，readback 不得覆盖 live owner。此场景不得标记为可恢复的 `interrupted`；后者只用于已确认的 native Pause terminal。 |
| 重启后取消持久化状态仍为 `running`、但当前进程已无 active adapter handle 的 invocation | 先要求 invocation 确实属于路径 session，且其关联 session、turn、harness 记录均存在；然后使用新的 fencing token 获取 session lease，持久化一致的 invocation/turn `cancelled` 状态；仅在没有更新的 terminal invocation 已取代它时，才合并 session `cancelled` 投影；最后追加且只追加一个 `haas.turn.cancelled` 终态事件后返回。不得把陈旧的 `running` 记录当作取消成功返回；记录缺失/不匹配或 lease conflict 均 fail closed，不修改状态，也不得覆盖当前 owner。 |
| adapter 在 24 小时 deadline 前无终态 | 取消 adapter await，并写入唯一 `haas_request_timeout` / `long_task_deadline_exceeded` 的 `failed` 或可恢复 `incomplete` terminal |
| cancel 后断线 | cancel intent 持久化；最终状态仍必须可读 |
| session lease 续租失败或被 fencing out | 停止 stale write；当前 fencing owner 尽可能持久化 `haas.turn.failed`。Accepted invocation 不得返回未接受 adapter 502；terminal 无法持久化时走 integrity-failure recovery |
| 接受后 required terminal event 写失败 | 权威 state store 将 invocation/turn/session 持久化 failed，idempotency 完成 accepted=true/invocationId + `503 haas_store_unavailable`，并输出 fallback diagnostics。响应头前返回该 integrity error；SSE 响应头后异常关闭并要求 readback。不得伪造 terminal evidence |
| delegated container 被 idle TTL 销毁 | 启动下一 turn 前，按 delegated-session contract 恢复运行资源 |
| delegated restore 失败 | 返回 `haas_delegation_restore_failed` 或更具体的 delegation 错误；不本地执行 |
| approval bridge 正在等待 | 持久化 `ApprovalRecord`；只有 manager 解析后才恢复 adapter |
| invocation 已进入 failed/incomplete/cancelled 且仍有 pending interaction | 原子地将其 waiting approval/input 标为 `cancelled`，不得恢复对应交互卡 |
| Policy update 与当前 revision 冲突 | 返回 `409 haas_policy_revision_conflict` 和安全的当前 revision，不部分应用 |
| Policy application 失败 | 保留旧 applied revision、暴露安全失败，并阻断新 invocation，直到修正或被新 revision 取代 |
| structured input 正在等待 | 持久化 `InputRequestRecord`；保持同一 invocation/turn active 和 model capability 有效，仅在收到精确 manager answer 后恢复 |
| adapter 已发出 terminal | 只提交该事件一次；`finalize_turn` 返回状态但不得再追加 terminal |
| active model-proxy capability 被拒绝 | 不重新提交 invocation，先尝试 exact-session refresh/rebind；否则持久化稳定 failed terminal 和 partial progress |
| explicit profile rebind validation failed | 拒绝 rebind，保留旧 `effectiveProfileSnapshot` |
| active profile changed after session creation | 仅标记 drift；已有 session 继续旧 snapshot，直到显式 rebind 或新建 session |

## 11. 测试计划与验收

- Unit：id 生成、三元组解析、scope、持久 acceptance transition、acceptance 前无副作用 invariant、state transition、terminal immutability、request hash、accepted HTTP-200 idempotency replay、in-progress attach/replay、terminal-store integrity failure replay。
- Integration：对 completed、failed、incomplete、interrupted、cancelled accepted invocation，`/run` 与 `/run_sse` 均返回 HTTP 200 且 event parity；pre-acceptance failure 保持结构化 4xx/5xx；session readback、PATCH stateDelta、pause/continue、cancel、DELETE 继续对齐。
- Lifecycle：pause 在 native interrupt 前持久化意图，等待匹配的 interrupted terminal，并保持幂等。Continue 在同一 native session 上只创建一个关联 invocation/turn；普通 `/run`、过期/非最新 source id、native state 缺失、重复 key 及 Pause/Stop/自然完成竞态均按合同失败或唯一收敛，不得创建替代 turn。
- 控制等待失败：active turn 的每个退出路径都必须结束 control waiter。若 stream 在无法提交 terminal 前退出（包括取消或 fencing loss），等待中的 Pause 必须立即失败，readback 收敛到已持久化的非过渡态，不能永远等待进程内 future。
- Concurrency：同 session 并发返回 `session_busy`；不同 session 可并发；超过一个 lease TTL 的长 turn 仍必须因续租受到保护。
- Integration：`/run_sse` 在 invocation 运行中渐进输出 ADK event，native replay 输出稳定 canonical type；唯一 canonical terminal type 与持久 invocation status 一致，且 native replay 可在 turn 运行中重连。
- Integration：delegated session 在容器 TTL restore 后继续使用 HaaS session，并在可恢复时复用 native Codex reference。
- Integration：profile update 后新 session 使用新 active revision，旧 session 保持 frozen snapshot；显式 rebind 后下一 invocation 使用新 snapshot，running invocation 不受影响。
- Approval gate：pending approval 在 SSE 断线后仍保留，可通过 paginated native approvals API 列出，并通过 HaaS native approval API 解析。完整 bridge 通过 release gate 前，Codex 报告 `unattended_only`、返回空 waiting page，且不暴露 human approval UI。
- Interaction（P1）：pending command/file approval 与 structured input 在 Manager/UI 断线后保留，且仅在所属 invocation 非终态时重连恢复一张可见卡片。相同 approval decision 重复提交幂等且不再次回复 native request；冲突决定或已被终态取消的 stale answer fail closed。
- Policy revision：local/delegated session 使用相同 approval/network/workspace mutation 语义；
  running invocation 保持冻结，queued/continued invocation 只能使用 applied revision。并发
  expected-revision 冲突和 apply failure 都不能让 stale work 执行。
- Task completion：invocation terminal 不能清除仍 pending 的 plan/verification work。Manager 验收覆盖 completed、verifying、可恢复 incomplete、failed、cancelled 和 continuation-budget-exhausted task 状态。
- Long turn：24 小时受控时钟 turn、超过 stream-idle timeout 的 turn 与 tool-heavy turn 都必须保持 lease、model capability，并只生成一个 terminal event。
- Recovery：模拟 sidecar restart、adapter reconnect、missing native ref、expired session、stale holder fencing rejection、adapter timeout terminalization，以及发起进程的本地 adapter handle 已不存在时取消持久化 `running` invocation。后者 read-back 必须为 `cancelled`，仅有一个匹配终态事件，重试仍保持幂等。
- Compatibility：ADK client bounded session GET/PATCH/DELETE 行为对齐；超预算 session GET 明确返回 413，native pagination 不静默截断。
- Security：跨 principal 访问全部返回 404；secret-shaped input 不落默认日志。

## stream-timeout-approval-recovery

invocation deadline 必须作用于当前等待 adapter 的任务。绑定任务的 timeout 上下文不得跨越异步生成器 yield：首事件与后续事件可能由不同任务消费。prepare、start、每次事件等待与 finalize 共用单调时钟 deadline，默认 24 小时。超时持久化唯一 terminal，关闭等待交互并释放资源。测试首事件后的静默执行、长下载、tool-heavy loop 与审批等待。
