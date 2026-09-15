# HaaS Protocol 组件规格

[English](README.md) | **简体中文**

Status: Draft
Last reviewed: 2026-09-14
Change ID: unified-runtime-approval-policy

## 1. 组件定位

HaaS Protocol 是系统对上游暴露的 HTTP/JSON + SSE 合同。Northbound 主协议遵循 Google [Agent Development Kit (ADK) 2.0](https://adk.dev/2.0/) 的 REST API 协议层，HaaS 在此之上提供 `/v1/haas/*` 控制面扩展。

协议目标是让上游用 ADK 2.0 标准客户端即可运行任意 harness。Codex app-server、Pi、OpenCode、AMP 等具体 runtime 只存在于 adapter 内部，不成为上游协议的必需知识。

约定映射：

| 概念 | ADK 2.0 术语 | HaaS 内部术语 |
|------|---------------|---------------|
| 可执行的 agent app | `appName` | configured harness `id`（`chrn_...`，`name` 可作别名） |
| 调用方身份 | `userId` | caller principal 下的 sub-user scope |
| 会话 | `sessionId` | HaaS Session（`(appName, userId, sessionId)` 三元组唯一） |
| 一次运行 | `/run`、`/run_sse` | HaaS Run/Invocation（内部 `inv_...`）+ 内部 Turn |
| 事件 | ADK `Event` | canonical event 的 ADK 投影 |

## 2. 来源与依据

| 来源 | 采用内容 |
|------|----------|
| ADK 2.0 docs（`/runtime/api-server/`，2026-08-26 抓取） | `/list-apps`、`/run`、`/run_sse`、`/apps/{app}/users/{user}/sessions/{sid}`、camelCase、`newMessage{role,parts}`、`streaming:true` token 级增量、SSE `data:` 帧 |
| ADK 2.0 release notes | Event 新增 `nodeInfo`、`output`、`routes`、`requestedInput`、`isolationScope` 字段 |
| `mpa-codex-worker` Sidecar API | 仅作为 health/ready/status 行为的**设计参考** |
| Codex app-server manual | Codex adapter 内部 JSON-RPC lifecycle，不对上游公开 |
| OpenSandbox AIO | 容器内基础 service 和 endpoint 约束 |

HaaS 只 follow ADK 的 **协议层**（HTTP 路径、请求/响应 shape、事件 shape、SSE framing），不引入 ADK 的 agent 执行引擎（`BaseAgent`/WorkflowGraph）、图工作流或 ADK Web UI。compatibility 目标是「任何遵守 ADK 2.0 REST 协议的 HTTP 客户端」，字段命名以 camelCase REST 契约为准，不承诺 Python SDK 的 snake_case server 实现。适配范围详见 [WALKTHROUGH](../architecture/WALKTHROUGH.zh-CN.md)。

## 3. 上游与下游关系

| 方向 | 对象 | 关系 |
|------|------|------|
| 上游 | Manager / SDK / CLI / product backend / ADK web UI | 通过 ADK HTTP/SSE 调用 HaaS |
| 上游 | Harness Registry / Harness Profile | 解析 appName -> configured harness，读取 active profile、model availability、capability |
| 上游 | Session Runtime | 创建 run/invocation、session/turn、幂等和并发 |
| 上游 | Event Log & SSE | 读取和推送事件 |
| 上游 | Harness Adapter | 执行具体 harness |
| 上游 | Observability | 提供 health/ready/status、trace、metrics |

本组件内部负责「Protocol Mapper」：将 ADK 请求映射为内部对象、把内部 canonical event 投影为 ADK `Event`。Protocol Mapper 不再作为独立组件存在，而是本 spec 的职责。

## 4. 职责边界

负责：

- 定义 public ADK API 的 path、method、headers、request/response schema。
- 维护 ADK-compatible 主协议与 HaaS native 扩展的分层。
- 统一错误 detail、`Idempotency-Key` 规则、SSE framing 和事件投影。
- 明确哪些字段是公共兼容承诺，哪些是 HaaS 扩展。

不负责：

- 不直接启动或管理 harness 进程。
- 不保存 session、event、artifact 或 credential。
- 不执行模型请求、MCP 请求或 tool 调用。
- 不暴露 Codex thread/turn id、Pi session file、OpenCode config path 等原生 runtime 细节。

## 5. 核心接口

### 5.1 ADK-Compatible Public API（主协议）

ADK 2.0 根路径，drop-in 兼容：

| Method | Path | 说明 |
|--------|------|------|
| GET | `/list-apps` | 列出 caller scope 内的 configured harness app 名（返回 id 字符串数组） |
| POST | `/run` | 运行 harness，收集全部事件后在单个 JSON 数组返回 |
| POST | `/run_sse` | 运行 harness，以 SSE 流式返回事件；`streaming:true` 开 token 级增量 |
| GET | `/apps/{app_name}/users/{user_id}/sessions/{session_id}` | 读取 session（`state` + `events[]`，ADK 原生 replay 通道） |
| PATCH | `/apps/{app_name}/users/{user_id}/sessions/{session_id}` | 用 `stateDelta` 更新 session state（`Idempotency-Key` 支持） |
| DELETE | `/apps/{app_name}/users/{user_id}/sessions/{session_id}` | 删除 session 及其数据 |

`appName` 解析顺序：先按 harness `id`（`chrn_...`）精确匹配，再按 `name` 匹配；多命中或未命中返回 `404 app_not_found`。`sessionId` 是 caller-supplied opaque 字符串。`userId` 是受控业务子身份：省略时使用 authenticated principal 的 `defaultUserId`；显式值必须命中 principal 的精确 `allowedUserIds` 或通过独立 delegated-user authorization，绝不覆盖 bearer identity。跨 scope 返回 404。

### 5.2 HaaS Native Extension API

HaaS 自有控制面，只做 U 未覆盖能力，不改写 ADK 字段语义：

| Method | Path | 说明 |
|--------|------|------|
| GET | `/health` / `/ready` | liveness/readiness alias，等同 `/v1/haas/health`、`/v1/haas/ready` |
| GET | `/v1/haas/health` | sidecar liveness |
| GET | `/v1/haas/ready?scope=control\|execution\|capability` | readiness |
| GET | `/v1/haas/status` | runtime、adapter、queue、proxy、AIO、store 状态摘要 |
| GET | `/v1/haas/capabilities` | 面向 caller scope 的稳定能力发现，覆盖 HaaS feature、workspace mode 与 configured harness |
| GET | `/v1/haas/diagnostics` | 脱敏诊断摘要 |
| GET | `/v1/haas/harnesses` | configured harness 完整列表（与 `/list-apps` 的 id 数组等价但含详情） |
| POST | `/v1/haas/harnesses` | 创建 configured harness（`Idempotency-Key` 支持） |
| PUT | `/v1/haas/harnesses/{harness_id}` | 更新 harness，`id`/`base`/`createdAtMs` 不变 |
| DELETE | `/v1/haas/harnesses/{harness_id}` | 删除 harness，不删历史 session |
| GET | `/v1/haas/profiles?harnessId=&status=&cursor=&limit=` | 分页列出 profile revision |
| POST | `/v1/haas/profiles` | 创建 draft profile revision（`Idempotency-Key` 支持） |
| GET | `/v1/haas/profiles/{profile_id}` | 读取 profile revision |
| PUT | `/v1/haas/profiles/{profile_id}` | 更新 draft profile revision（`Idempotency-Key` 支持） |
| POST | `/v1/haas/profiles/{profile_id}/validate` | 无副作用校验 profile revision |
| POST | `/v1/haas/profiles/{profile_id}/activate` | 激活 validated profile revision |
| GET | `/v1/haas/models` | 全局 model catalog（按 base 分组） |
| POST | `/v1/haas/delegated-sessions` | 创建/复用 manager 拥有的 `prepared` delegated-session candidate；首个 accepted invocation 时才提交 binding（支持 `Idempotency-Key`） |
| GET | `/v1/haas/delegated-sessions/{delegated_session_id}` | 读取 delegated-session contract、policy snapshot、mount manifest 与 runtime status |
| POST | `/v1/haas/delegated-sessions/{delegated_session_id}/restore` | 按持久 delegated-session contract 重建运行资源 |
| POST | `/v1/haas/delegated-sessions/{delegated_session_id}/policy` | 显式更新或 rebind 后续 delegated turn 使用的 policy snapshot |
| GET | `/v1/haas/sessions` | 跨 user 分页列出 session（管理视角） |
| GET | `/v1/haas/sessions/{session_id}/invocations/{invocation_id}` | 读取断线/完整性恢复所需的权威 invocation 状态 |
| GET | `/v1/haas/sessions/{session_id}/approvals?status=waiting` | 分页列出 caller-visible approval record；Codex P0 `unattended_only` 通常返回空页 |
| GET | `/v1/haas/sessions/{session_id}/input-requests?status=waiting` | 列出 caller-visible 结构化问题，使断线 UI 能重建 pending input |
| GET | `/v1/haas/sessions/{session_id}/events-page` | 使用 `after_event_id` + `limit` 读取有界 `CanonicalHaasEvent` JSON page |
| GET | `/v1/haas/sessions/{session_id}/events` | HaaS `CanonicalHaasEvent` replay/live SSE（带 cursor） |
| GET | `/v1/haas/sessions/{session_id}/invocations/{invocation_id}/events` | invocation 级 `CanonicalHaasEvent` replay/live |
| GET | `/v1/haas/sessions/{session_id}/invocations/{invocation_id}/tools/{tool_call_id}/evidence?evidence_ref=` | 读取单条 scoped、短期 command evidence，不持久化、不缓存 |
| POST | `/v1/haas/sessions/{session_id}/invocations/{invocation_id}/cancel` | 取消运行中 invocation，幂等（`Idempotency-Key` 支持） |
| POST | `/v1/haas/sessions/{session_id}/invocations/{invocation_id}/pause` | 请求可恢复中断，仅在权威 `interrupted` readback 后返回 |
| POST | `/v1/haas/sessions/{session_id}/invocations/{invocation_id}/continue` | 关联 interrupted 源创建新 invocation/turn，并流式返回 ADK projection |
| POST | `/v1/haas/sessions/{session_id}/profile-rebind` | 显式为已有 session 的后续 turn rebind profile snapshot |
| POST | `/v1/haas/sessions/{session_id}/policy` | 为后续 invocation 暂存带 revision 的审批、网络与 workspace policy 更新 |
| POST | `/v1/haas/sessions/{session_id}/approvals/{approval_id}` | 把 manager 审批决策回传给等待中的 harness action |
| POST | `/v1/haas/sessions/{session_id}/input-requests/{input_request_id}` | 将结构化答案回传给原 blocking harness request |
| GET | `/v1/haas/sessions/{session_id}/artifacts` | HaaS artifact listing |
| GET | `/v1/haas/sessions/{session_id}/artifacts/archive` | 下载 session artifact 归档（zip） |
| POST | `/v1/haas/files` | 上传 input file（multipart），支持 `Idempotency-Key`，返回 `File` 对象 |
| GET | `/v1/haas/files/{file_id}/content` | 下载 artifact 原始 bytes（`nosniff`） |
| GET | `/v1/haas/files/{file_id}/pdf` | 可选 PDF preview；未实现返回 `501 haas_preview_unavailable` |

artifact 端点详情见 [Artifact Store](../artifact-store/README.zh-CN.md) 第 5 节；文件模型统一
`File` schema。

### 5.3 Capability Discovery 合同

`GET /v1/haas/capabilities` 是 session 创建前使用的唯一稳定能力发现 API。它与
readiness、diagnostics 的职责不同：

- `/ready` 回答某个 scope 当前能否接收工作；
- `/status` 面向人和诊断报告运行状态；
- `/capabilities` 以版本化 schema 报告 caller-visible 的支持情况、当前可用性、
  实现机制和执行强度。

响应：

```json
{
  "data": {
    "object": "haas_capabilities",
    "protocolVersion": "2026-09-10",
    "adkProtocolVersion": "2.0",
    "features": {
      "runSse": {"status": "available", "mode": "native", "enforcement": "hard"},
      "delegatedSessions": {"status": "available", "mode": "native", "enforcement": "hard"},
      "modelProxy": {"status": "available", "mode": "proxy", "enforcement": "hard"},
      "mcpProxy": {"status": "available", "mode": "proxy", "enforcement": "hard"},
      "skillMaterialization": {"status": "available", "mode": "native", "enforcement": "hard"},
      "approvalHandling": {"status": "available", "mode": "unattended_only", "enforcement": "hard"},
      "inputHandling": {"status": "unsupported", "mode": "none", "enforcement": "none"},
      "artifacts": {"status": "available", "mode": "native", "enforcement": "hard"}
    },
    "workspaceModes": [
      {"id": "bind_mount", "status": "available", "mode": "native", "enforcement": "hard"},
      {"id": "snapshot_upload", "status": "unsupported", "mode": "none", "enforcement": "none"},
      {"id": "remote_workspace", "status": "unsupported", "mode": "none", "enforcement": "none"}
    ],
    "harnesses": [
      {
        "id": "chrn_codex_default",
        "base": "codex",
        "activeProfileId": "hprof_abc",
        "activeProfileVersion": 12,
        "activeProfileFingerprint": "sha256:profile",
        "status": "available",
        "capabilities": {
          "streaming": {"status": "available", "mode": "native", "enforcement": "hard"},
          "sessionContinuation": {"status": "available", "mode": "native", "enforcement": "hard"},
          "cancellation": {"status": "available", "mode": "best_effort", "enforcement": "hard"},
          "approval": {"status": "available", "mode": "unattended_only", "enforcement": "hard"},
          "input": {"status": "unsupported", "mode": "none", "enforcement": "none"},
          "toolRestriction": {"status": "degraded", "mode": "advisory", "enforcement": "advisory"},
          "mcp": {"status": "available", "mode": "proxy", "enforcement": "hard"},
          "skills": {"status": "available", "mode": "native", "enforcement": "hard"},
          "files": {"status": "available", "mode": "workspace_scan", "enforcement": "hard"},
          "usage": {"status": "available", "mode": "native", "enforcement": "hard"}
        }
      }
    ],
    "generatedAtMs": 1786400000000
  },
  "traceId": "tr_abc"
}
```

`CapabilityState` 规则：

- `status` 只允许 `available`、`degraded`、`unavailable`、`unsupported`。
  `unavailable` 表示机制已实现但当前不可用；`unsupported` 表示实现不提供该能力。
- `mode` 使用 OpenAPI 定义的稳定机制。`unsupported` 必须搭配 `mode=none`；
  `unavailable` 保留已实现 mode，使 client 能区分暂时不可用与能力缺失。
- `enforcement` 只允许 `hard`、`advisory`、`none`。仅靠 prompt/instruction 的限制
  必须标为 `advisory`，不得标为 `hard`。
- `safeReason` 与 `retryable` 是可选安全诊断，不得包含原生协议数据、内部地址、路径、
  credential 或未脱敏异常。
- Client 遇到未知 feature key、mode、status、enforcement 或 workspace-mode id 时，
  必须按 unsupported 处理。必需能力只有在 status/mode/enforcement 满足 caller 声明需求
  时才可使用；不得仅凭字段存在推断支持。
- 响应只包含 authenticated principal 可见的 configured harness。可以暴露 harness
  `id`/`base`，但不得暴露 adapter transport、adapter id/version、native session/thread
  id、container id、内部 endpoint、provider credential、MCP header 或 host path。
- 部分组件失败时返回 HTTP 200，并把受影响能力标为 `degraded` 或 `unavailable`；只有
  无法安全构造 caller-scoped snapshot 时才返回 `503 haas_store_unavailable`。

## 6. 数据模型

### 6.1 RunRequest（`/run`、`/run_sse` 请求体）

```json
{
  "appName": "chrn_codex_default",
  "userId": "u_123",
  "sessionId": "s_123",
  "newMessage": {
    "role": "user",
    "parts": [
      { "text": "Summarise README.md" },
      { "inlineData": { "displayName": "a.png", "mimeType": "image/png", "data": "base64..." } }
    ]
  },
  "streaming": true,
  "haas": {
    "profileId": "hprof_abc",
    "profileVersion": 12,
    "model": "gpt-5.6-terra",
    "instructions": "Use the repository AGENTS.md.",
    "metadata": { "haasTraceId": "tr_abc" },
    "maxOutputTokens": 4096,
    "maxStep": 40,
    "timeoutSeconds": 900
  }
}
```

规则：

- `appName` 必填。只有 authentication 提供 `defaultUserId` 时才可省略 `userId`；显式值只是请求 sub-scope，不优先于 token identity。`sessionId` 可选，缺省由 HaaS 生成 `hsess_<rand>`。
- `newMessage.parts[]` 支持 `text`、`inlineData`；HaaS 额外接受 `fileId`（引用已上传 file）作为扩展。
- `streaming` 仅 `/run_sse` 生效，默认 `false`。
- 顶层未知字段在 ADK-compatible path 上忽略。HaaS 扩展只能放入 `haas` 嵌套对象，不得污染 ADK 顶层字段。
- `haas.profileId` / `haas.profileVersion` 可在新 session 上显式 pin profile。已有 session 已冻结
  `EffectiveHarnessProfile`；请求不同 profile 时返回 `409 haas_profile_rebind_required`，
  不启动 invocation。客户端必须通过 native `profile-rebind` 或新建 session 改变已有
  session 的未来 turn 配置。

### 6.1.1 Profile 与动态配置合同

HaaS native profile API 使用 [Harness Profile](../harness-profile/README.zh-CN.md)
定义的 `HarnessProfile` 与 `EffectiveHarnessProfile`。Profile 是跨 harness 的
provider/model、MCP、skill、AGENTS.md、workspace/policy 和 budget 配置单位：

- `Harness` 是 ADK app identity；active profile 是该 app 新 session 的默认执行配置。
- 已激活 revision 不可改；draft 编辑使 validation 失效，activate 重验当前内容并原子推进 per-harness version。复用旧内容仅能复制为更高 revision；只保留最新 profile 时仍保留完整 applied/pending session snapshot 及引用内容。
- 新 session 冻结当前 active profile；已有 session 不随 active profile 漂移。
- 显式 `profile-rebind` 只影响 rebind 之后的新 invocation，不改变运行中的 invocation。
  它只适用于非 delegated session；delegated session 拒绝 `profile-rebind`，返回
  `409 haas_profile_rebind_unsupported`，改用 `POST /v1/haas/delegated-sessions/{id}/policy` 重新配置。
- Public capability 与 harness read 可暴露 `activeProfileId`、`activeProfileVersion`
  和 `activeProfileFingerprint`；不得暴露 provider credential、MCP header、AGENTS.md
  原文、host path 或 adapter-native config。
- local session 与 delegated session 使用相同的 policy mutation 语义。本地端点接受
  `{expectedRevision, policy:{workspace?,network?,tools?}}` 与 `Idempotency-Key`；
  delegated `/policy` 在既有更新合同中携带相同的完整 policy domain。响应公开
  完整 `desiredPolicy`/`appliedPolicy` 快照、对应 desired/applied revision 与类型化的
  pending/applied/failed 状态。已 accepted 的
  invocation 冻结原 applied revision；新工作在 desired revision 应用完成前不得被接受。

### 6.2 ADK `Event`（公共事件投影）

```json
{
  "id": "evt_0000000001042",
  "invocationId": "inv_abc",
  "author": "codex",
  "timestamp": 1743712220.385936,
  "content": {
    "role": "model",
    "parts": [
      { "text": "OK." }
    ]
  },
  "actions": {
    "stateDelta": {},
    "artifactDelta": {},
    "requestedAuthConfigs": {}
  },
  "longRunningToolIds": [],
  "nodeInfo": null,
  "output": null
}
```

`author` 填 harness `id` 或 base（`codex`）；`content.role` 为 `model` 或 `user`；`parts[]` 元素类型为 `text`、`inlineData`、`functionCall`、`functionResponse`。`nodeInfo`/`output` 只在 adapter 产出对应信息时出现。`actions.stateDelta`/`artifactDelta` 由 Session Runtime 汇总 harness 状态/产物写入。

### 6.3 HaaS Native `CanonicalHaasEvent`

HaaS native event stream 返回 OpenAPI 与 [Event Log & SSE](../event-log-sse/README.zh-CN.md)
§6.3–§6.4 定义的强类型 `CanonicalHaasEvent`。每个 SSE `data:` frame 包含一个对象，
具备稳定顶层 `type`、type-specific 安全 `haas` payload、scope/order id、ADK-compatible
`content`/`actions` 与 `observedAtMs`。

稳定 `type` 和 `haas` payload 只存在于 HaaS native event stream，不得加入 ADK
`/run`、`/run_sse` 或 ADK Session `events[]`。Adapter `nativeType`、adapter
id/transport/version、caller `userId`、container id、内部地址、host path、credential、
raw prompt/reasoning、完整 tool argument/result 都不得进入 public native projection。

Client 必须按 `type` 消费 native event，不得从人类文本推断 queue、approval、tool、
restore 或 terminal 语义。对于当前 client 不认识的 public event type，除非新版本合同
明确支持，否则只能忽略其渲染；绝不能把未知类型解释为 approval 或成功 terminal。
每个 invocation 的 terminal outcome 必须且只能由 `haas.turn.completed`、
`haas.turn.failed`、`haas.turn.incomplete`、`haas.turn.interrupted`、
`haas.turn.cancelled` 之一表达。

Output、reasoning、tool 与 usage fact 可携带 Event Log & SSE 定义的加法关联字段
`itemId` 与 `modelCallId`。`modelCallId` 标识一次实测模型 round trip，不是 UI row。
`scope=model_call` 的 `haas.usage.updated` 将 `usage` 绑定到该 id，并可携带独立的
`cumulativeUsage` snapshot。`usage` 保留 harness 实际报告的 input、output、reasoning-output
与 cache counter。Client 不得把这些数值分配给关联的 commentary、reasoning-summary、
result 或 tool fact。关联或 usage 缺失时保持 unknown；禁止补零或估算 per-step token。
这些仅 native stream 使用的加法字段不改变 ADK Event schema。
Agent-message delta 的 phase 尚未权威确定时保持未分类。后续
`haas.output.item.completed` fact 通过相同 `itemId` 和可选
`messagePhase=commentary|final_answer` 使 client 原地重新分类已流式展示的 item，不重复其
文本。Model-call usage 只计量该调用；关联 tool 尚未终态时，它不是 stage terminal 信号。
Cache-read token 是 input token 的子集，reasoning token 是 output token 的子集，二者都作为
“其中”数值展示，不得重复计入总数。

结构化副作用授权与结构化用户输入是两类独立 native fact。`haas.approval.required|resolved` 携带 approval id 和安全 action summary；`haas.input.required|resolved` 携带 input-request id 和可渲染 question metadata。二者都不是 terminal。Client 必须通过对应 resource endpoint 回答，不得把问题转换为审批 decision，也不得从 assistant prose 推测答案。

### 6.4 ADK `Session`

```json
{
  "id": "s_123",
  "appName": "chrn_codex_default",
  "userId": "u_123",
  "state": { "visitCount": 5 },
  "events": [],
  "lastUpdateTime": 1743711430.022186
}
```

### 6.5 HaaS Error

ADK 兼容路径返回带 `detail` 的错误（FastAPI 惯例），并附加结构化 `haasError` 扩展：

```json
{
  "detail": "The session is busy.",
  "haasError": {
    "type": "invalid_request_error",
    "code": "session_busy",
    "param": "sessionId",
    "safeReason": "session_busy",
    "retryable": true,
    "traceId": "tr_abc"
  }
}
```

`code` 使用 HaaS 稳定错误码，`haasError` 为 HaaS 扩展，ADK 客户端只读 `detail`。
错误码唯一目录见 [ERROR-CODES](ERROR-CODES.zh-CN.md)，OpenAPI 的 `haasError.code`
与之对齐；新增错误码必须先更新目录。

### 6.6 恢复与有界读取模型

`GET /v1/haas/sessions/{session_id}/invocations/{invocation_id}` 返回权威 `Invocation`，包括 `accepted|running|pausing|completed|failed|incomplete|interrupted|cancelling|cancelled`、accepted/start/completion timestamp、continuation link、terminal event id、model、安全 error summary，以及当前 `sessionControl` 投影（`controlState`、`supportsResume`、`resumableInvocationId`）。Transport loss 或 `haasError.accepted=true` 后优先使用该接口恢复。

`GET /v1/haas/sessions/{session_id}/events-page?after_event_id=&limit=` 按 session-scoped `eventId` 返回 JSON page；`limit` 默认 100、上限 1000。只有当前立即还有 retained page 时，`nextCursor` 才是本页最后一条 event id，否则为 null。轮询 consumer 仍必须把每个非空 `data` page 的最后一个 event id 保存为下一次 `after_event_id` checkpoint，使后续新追加事件从增量位置读取而不是从 session 开头重读。该接口不进入 live delivery；现有 `/events` route 继续提供 replay-then-live SSE。

`GET /v1/haas/sessions/{session_id}/approvals?status=waiting&limit=&cursor=` 只返回 caller-visible、已脱敏 approval record，用于断线后重建 approval UI。Codex P0 声明 `unattended_only`，因此通常返回空页；capability mode 不是 `human_bridge` 时 client 不得显示 human approval 控件。

`GET /v1/haas/sessions/{session_id}/input-requests?status=waiting&limit=&cursor=` 独立于 approval 返回结构化、已脱敏的问题。`POST /v1/haas/sessions/{session_id}/input-requests/{input_request_id}` 对普通问题接受 `{answers:{questionId:{values:[...]}}}`，对 secret question 使用 private `secretRef` 替代 values，并续接原 waiting invocation。Answer 只绑定一个 input request，并按 `Idempotency-Key` 幂等；跨 session、已解决 request、未知 question id 或不允许的自由文本必须 fail closed。Secret answer 不得进入 event、log、task state 或 transcript。

`GET /v1/haas/sessions/{session_id}/invocations/{invocation_id}/tools/{tool_call_id}/evidence?evidence_ref=` 是对单条内存 command-evidence record 的 authenticated、仅展示读取。Bearer principal、由 `session_id` 解析出的完整 session key、invocation、tool call 与 opaque `evidence_ref` 必须全部匹配。缺失、不可用或越权 record 统一返回 `404 haas_execution_evidence_not_found`；匹配但已过期返回 `410 haas_execution_evidence_expired`。响应必须带 `Cache-Control: no-store` 与 `Referrer-Policy: no-referrer`，且不得写入 access log、analytics、trace、transcript、event、artifact、crash report、browser storage 或 service-worker cache。

`ExecutionEvidence` body 包含已做 value-aware credential 遮蔽的实际命令、容器工作目录、有界命令输出及识别出的链接。每条 record 的 output 上限为 8 MiB（8,388,608 UTF-8 bytes）；未超过该上限时必须完整返回，超过上限时显式截断，完整内容必须通过 Artifact Store 获取。除非 adapter 有权威 stdout/stderr 标识，否则 `outputStream` 为 `combined`。Credential value 始终遮蔽；普通 HTTP(S) 链接保持完整。由 adapter 根据明确授权语义分类的 HTTPS 用户授权链接，在 evidence 强制过期前逐字节保持完整且可点击；它必须绕过 redirect/analytics log，client 使用 `noopener,noreferrer` 打开。URL 不要求显式携带 expiry 参数；native 能提供可信且更早的 expiry 时取更早者。Evidence 最晚在授权链接过期或命令结束后 15 分钟时失效（取更早者），并在过期、session 删除或 runtime 退出时删除。Canonical/ADK event 只可携带 optional opaque `evidenceRef` 与 `evidenceExpiresAtMs`，绝不携带 evidence body 或 signed URL。

ADK Session GET 仅用于兼容并包含 `events[]`，但完整响应上限为 1000 events 或序列化 8 MiB（先达到者）。若完整 ADK Session 超过任一上限，返回 `413 haas_session_read_too_large` 并安全提示使用 `events-page`；禁止静默截断 `events[]`。

### 6.7 Delegated Session

HaaS native delegated-session API 使用
[Manager Delegation](../manager-delegation/README.zh-CN.md) 定义的
`DelegatedSessionContract`。该合同只在 HaaS native surface 上公开；
ADK-compatible path 不得暴露 manager session id、host mount path、image digest、
credential reference、runtime container id 或 approval id，除非它们以已脱敏/安全的
HaaS event 明确表达。

`POST /v1/haas/delegated-sessions` 要求传入 manager 已授权的 P0 `bind_mount` manifest 与 delegation policy snapshot。创建返回 `binding=prepared`，不绑定 manager chat。首个 InvocationRecord 持久 accepted 时，HaaS 原子提升 contract 为 `haas_bound`，记录 `acceptedInvocationId`/`bindingAcceptedAtMs`，并发出 `haas.delegation.session_bound`。相同 `Idempotency-Key` 且 request hash 相同的重放返回
第一次 delegated-session 结果，不得创建第二个容器。

`POST /v1/haas/delegated-sessions/{id}/restore` 只能按持久合同恢复。恢复前必须重新
校验 mount manifest 与 policy；任何漂移都用稳定错误码 fail closed。

`POST /v1/haas/sessions/{session_id}/approvals/{approval_id}` 只接受 manager 显式审批决策；bridge 不可用时不得 auto-approve。Human approval 与 structured input 都是 release-gated capability；Codex capability mode 仍为 `unattended_only` 或 input handling unsupported 时，对应 API 保持 dormant，UI 不得声称功能已启用。

新 interactive session 默认使用 `workspace-write`、允许公网访问并设置
`approvalMode=on-request`。审批决定默认只覆盖服务端明确告知的当前动作，恢复同一
invocation，且不修改 session 长期 policy。`approvalMode=never` 明确表示禁止询问，
绝不授予 effective sandbox/policy 之外的权限。

### 6.8 配置与 Replay 更新合同

Delegated `/policy` 使用完整替换域（`profileRef`、`delegationPolicySnapshot`、`mountManifest`、`image`）、持久 desiredRevision/appliedRevision 屏障及 typed pending/applied/failed 结果。应用不依赖新 turn，不改已 accepted 工作，runtime 验证前阻止未来 acceptance；禁止 delegated profile-rebind。Merge/timeout/recovery 见 Manager Delegation §5.1.1。

带 Idempotency-Key 的 accepted 执行返回 `Idempotency-Expires-At`（epoch 毫秒，acceptedAtMs + 24h），invocation readback 提供 idempotencyExpiresAtMs。非终态 reservation 不过期。终态结果过期后 key tombstone 保留到 session delete/retention，旧 key 返回 `410 haas_idempotency_expired`，不静默新执行。Manager 下次使用自动持久化/提交一个关联新 attempt；打开完成历史、cursor expiry、404 或 transport failure 都不能触发新执行。控制 mutation 单独保留 operation receipt。

`X-HaaS-Trace-ID` 是可选 opaque correlation header（最多 128 个 ASCII 字母/数字/下划线/短横线）；非法输入返回 invalid_input，不传递身份或权限。ADK shape 不变；native schema/acceptance header 在未发布的 2026-09-10 draft 内收敛。已有记录按 Stores 前向迁移，不静默忽略缺字段。Schema/event/migration/client/real-runtime 门禁通过前不得宣告该版本。

### 6.9 版本声明门禁

Normative schema 为 `haas-2026-09-10.openapi.yaml`。旧 2026-08-26 文件是未发布 draft
baseline，被替换而不是作为 supported version 保留。只有 Implementation Roadmap 中
route、schema、event migration、acceptance、pagination、identity、manager-client
conformance gate 全部通过后，runtime response 才能声明 `HaaS-Version: 2026-09-10`。
仅修改代码常量或 OpenAPI 文件不构成 release evidence。

## 7. 运行模型与状态机

```text
request received
  -> auth and principal scope checked
  -> appName resolved (harness id/name)
  -> schema validated
  -> Idempotency-Key reservation (when present)
  -> Last-Event-ID present? -> resolve invocation by event id + scope -> replay then live（不新建 turn）
  -> admission decision (quota/rate/queue)
  -> session resolved or created
  -> freeze harness config + compile policy + side-effect-free adapter/runtime preflight
  -> persist invocation status=accepted (durable acceptance boundary)
  -> create sandbox/native session/turn and start adapter execution
  -> canonical events appended
  -> events projected to ADK Event / SSE frames
  -> invocation terminal
  -> session state and artifacts committed
```

Invocation（内部 Run）状态：

| Status | Terminal | 语义 |
|--------|----------|------|
| `accepted` | no | Invocation record 已持久化，尚未开始 native execution 副作用 |
| `running` | no | 已接受并执行中 |
| `completed` | yes | harness 正常完成，stream 关闭 |
| `failed` | yes | harness 或服务失败，错误可读 |
| `incomplete` | yes | 预算/超时截断，保留部分输出 |
| `interrupted` | yes | caller 暂停；源 turn 不可变，可作为新 invocation 的续接来源 |
| `cancelled` | yes | caller 取消，保留已提交事件 |

同一个 session 同一时刻只允许一个 running invocation；第二个 `/run` 返回
`409 session_busy`。

**执行接受边界：** `InvocationRecord` 成功持久化后，请求进入 accepted execution。
Authentication、scope、schema、app/model resolution、idempotency reservation、
admission、policy compilation，以及无需产生用户工作副作用即可完成的 adapter/runtime
preflight，都必须在该边界前完成。Invocation record 存在前，HaaS 不得启动 native
turn、provider call、tool call 或 workspace mutation。

接受前失败返回结构化 4xx/5xx，不创建 invocation，也不产生 terminal event。接受后，
adapter start/stream/finalize、provider、MCP、sandbox、timeout、budget、cancel、runtime
失败都必须收敛为唯一持久化 canonical terminal event 与对应 ADK projection：

| Surface | Accepted terminal result |
|---------|--------------------------|
| `/run` | HTTP 200 + 完整 ADK Event array（含 terminal event） |
| `/run_sse` | HTTP 200；发送 terminal ADK Event 后关闭 stream |
| Native event stream | 唯一 `haas.turn.completed|failed|incomplete|interrupted|cancelled` |

成功 `/run`、`/run_sse` response 必须包含 `X-HaaS-Invocation-ID` 与
`X-HaaS-Session-ID`。对 `/run_sse`，200 response header 是 Manager 可见的 acceptance
acknowledgement，在 event data 前到达；client 必须先持久化这些 header，再处理 stream。

失败前已经提交的 partial text/event 按原顺序保留在 terminal event 之前。ADK terminal
projection 使用 `actions.stateDelta.status`，值为 `completed`、`failed`、`incomplete`、
`interrupted` 或 `cancelled`；failed/incomplete 还使用稳定安全的 `actions.stateDelta.reason`。需要
结构化 terminal 语义的 client 使用 native canonical type。

对 accepted execution，`Idempotency-Key` replay 始终重放 HTTP 200 与完全相同的 ADK
event sequence；不得重启 harness，也不得把 accepted terminal failure 转换成 HTTP
502。接受前失败释放 reservation，不存在可重放的 accepted result。

唯一的接受后例外是 required terminal event 无法持久化。HaaS 不得伪造 terminal event，
也不得把它表达成未接受的 adapter 502。权威 state store 将 invocation/turn/session
持久化为 `failed`。响应头发送前返回 `503 haas_store_unavailable`，并携带
`haasError.accepted=true` 与 `haasError.invocationId`；SSE 响应头发送后只能异常关闭
stream，client 使用已知 invocation id 通过 session/native readback reconcile。该场景
是完整性故障，不是正常 execution outcome。

SSE framing（`/run_sse`）：

```text
data: {"id":"evt_...","invocationId":"inv_abc",...}

```
heartbeat 使用 SSE comment `: keep-alive`，不产生事件。invocation 完成即关闭 stream；`/run` 则收集后一次性返回 JSON 数组。

## 8. 安全与权限

- `GET /list-apps` 需鉴权，只返回 caller scope 内 harness。
- `GET /v1/haas/capabilities` 需鉴权，使用与 `/list-apps` 相同的 caller scope，且不得返回原生或含 secret 的细节。
- `Authorization: Bearer <caller token>` 对非 health/ready 路径必填。
- `userId`/`sessionId` 上均做 principal scope：跨 user/session 访问返回 `404`，不用 `403` 暴露存在性。
- ADK 兼容路径不在顶层接收 caller 提供的上游 URL/密钥；HaaS 扩展字段必须通过 registry allowlist 校验。
- Error `detail`/`haasError` 不得含 secret、内部 host、绝对路径、stack trace。

## 9. 可观测性

每个请求关联：`traceId`、`requestId`、`principalHash`、`appName`(harnessId)、`userId` hash、`sessionId`、`invocationId`、`adapterBase`、`status`。

不得记录：raw prompt、provider API key、Authorization/Cookie、MCP runtime header value、artifact 文件内容、未脱敏 stack trace。

## 10. 失败与恢复

| 场景 | 行为 |
|------|------|
| appName 无法解析 | `404 app_not_found`（`haasError.code=app_not_found`） |
| profile 不存在或越权 | `404 haas_profile_not_found` |
| profile 状态冲突或非 draft 更新 | `409 haas_profile_conflict` |
| 已有 session 请求不同 profile | `409 haas_profile_rebind_required`，不启动 invocation |
| `profile-rebind` 作用于 delegated session | `409 haas_profile_rebind_unsupported`；改用 `/delegated-sessions/{id}/policy` |
| Policy expected revision 已过期 | `409 haas_policy_revision_conflict`；返回安全的当前 revision，且不应用任何变更 |
| idempotency store 不可用 | `503 haas_idempotency_store_unavailable`，不得执行任务 |
| session busy | `409 session_busy`，可带 `retryAfterMs` |
| `/run_sse` 断线 | 不取消 invocation；client 用 `POST /run_sse` + `Last-Event-ID` 续接（服务端按 event id 定位原 invocation，回放其后事件并续 live，不新建 turn），或通过 `GET /apps/.../sessions/{sid}` 读回 events |
| adapter 在 invocation 持久化前失败 | 返回结构化 pre-acceptance 5xx，例如 `haas_adapter_unavailable`、`haas_adapter_incompatible`、`haas_adapter_error`；不存在 invocation 或 terminal event |
| adapter/provider/runtime 在接受后失败 | 持久化匹配 terminal event；`/run` 与 `/run_sse` 保持 HTTP 200，并保留 partial output |
| accepted failure 的幂等重试 | 重放 HTTP 200 与完全相同的 ADK event sequence（含 terminal event），不重启执行 |
| 接受后 terminal event 持久化失败 | 持久化权威 failed state；响应头前返回带 accepted/invocation metadata 的 `503 haas_store_unavailable`，否则异常关闭 SSE 并要求 readback；不得伪造 terminal evidence |
| cancel retry | 幂等成功，不删除 session（见 `POST /v1/haas/.../invocations/{id}/cancel` 扩展见 session-runtime） |

## 11. 测试计划与验收

文档阶段：

- `git diff --check`
- 所有 public path 在本文件和组件 spec 中一致；OpenAPI 只覆盖 ADK 与 HaaS native 两面。

实现阶段：

- ADK API server 兼容：用 ADK 官方 client / curl `list-apps`、`run`、`run_sse` 对比真实 ADK 行为。
- SSE：progressive flush、stream 关闭语义、heartbeat 不产生事件。
- 对 completed、failed、incomplete、interrupted、cancelled 的 accepted invocation，`/run` 非流式输出与 `/run_sse` 流式聚合输出一致；均为 HTTP 200 和相同有序 ADK events。
- 生命周期控制：Pause 等待唯一权威 interrupted terminal；Continue 以 HTTP 200 流式返回唯一关联新 invocation 的 ADK projection，并携带 acceptance identity header。重复 key 只 replay/reattach，不启动第二个 native turn；过期 source、native state 缺失、paused 状态普通 `/run` 及 Pause/Stop/自然完成竞态均返回目录定义的结果。
- Event：ADK projection 保持 `content.role`/`parts`/`actions`/`invocationId` 完整且不含 HaaS-only 字段；native projection 校验稳定 `type` 与 type-specific `haas` metadata、剥离内部/原生字段，并为每个 invocation 持久化唯一 terminal type。
- Auth/scope：两 principal 交叉访问 session/user 全部 404。
- Idempotency-Key：所有 mutating API 都接受该 header；reservation 按已认证 principal 与 operation path 隔离，相同 caller key 不得跨 principal 或 mutation 冲突/重放。accepted execution 重试 replay HTTP 200 与完全相同 event sequence，pre-acceptance failure 不保留 execution result，不同 request hash 返回 `409 haas_idempotency_conflict`；multipart 上传 hash 包含规范化 metadata 与上传字节摘要。
- Versioning：每个成功的 `/v1/haas/*` 响应都携带 `HaaS-Version`，OpenAPI 在对应 success response 上逐一声明该 header。
- Capability discovery：校验 availability/mechanism/enforcement enum 组合、caller-scoped harness 过滤、client 对未知值 fail closed、degraded/unsupported 区分，以及响应中不含原生/secret-bearing 字段。
