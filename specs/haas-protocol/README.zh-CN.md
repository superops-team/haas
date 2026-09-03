# HaaS Protocol 组件规格

[English](README.md) | **简体中文**

Status: Draft
Last reviewed: 2026-08-30

## 1. 组件定位

HaaS Protocol 是系统对上游暴露的 HTTP/JSON + SSE 合同。Northbound 主协议遵循 Google [Agent Development Kit (ADK) 2.0](https://adk.dev/2.0/) 的 REST API 协议层，HaaS 在此之上提供 `/v1/haas/*` 控制面扩展。（旧 `mpa-codex-worker` 迁移 shim 不在本项目范围，见 §5.3。）

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
| `mpa-codex-worker` Sidecar API | health/ready/status 的**设计参考**；其 legacy `/v1/codex-worker/*` 语义不在本项目实现范围（§5.3） |
| Codex app-server manual | Codex adapter 内部 JSON-RPC lifecycle，不对上游公开 |
| OpenSandbox AIO | 容器内基础 service 和 endpoint 约束 |

HaaS 只 follow ADK 的 **协议层**（HTTP 路径、请求/响应 shape、事件 shape、SSE framing），不引入 ADK 的 agent 执行引擎（`BaseAgent`/WorkflowGraph）、图工作流或 ADK Web UI。compatibility 目标是「任何遵守 ADK 2.0 REST 协议的 HTTP 客户端」，字段命名以 camelCase REST 契约为准，不承诺 Python SDK 的 snake_case server 实现。适配范围详见 [WALKTHROUGH](../architecture/WALKTHROUGH.zh-CN.md)。

## 3. 上游与下游关系

| 方向 | 对象 | 关系 |
|------|------|------|
| 上游 | Manager / SDK / CLI / product backend / ADK web UI | 通过 ADK HTTP/SSE 调用 HaaS |
| 上游 | Harness Registry | 解析 appName -> configured harness、model availability、capability |
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

`appName` 解析顺序：先按 harness `id`（`chrn_...`）精确匹配，再按 `name` 匹配；多命中或未命中返回 `404 app_not_found`。`userId`/`sessionId` 是 caller-supplied opaque 字符串，受 `Authorization` 主体 scope 约束。

### 5.2 HaaS Native Extension API

HaaS 自有控制面，只做 U 未覆盖能力，不改写 ADK 字段语义：

| Method | Path | 说明 |
|--------|------|------|
| GET | `/health` / `/ready` | liveness/readiness alias，等同 `/v1/haas/health`、`/v1/haas/ready` |
| GET | `/v1/haas/health` | sidecar liveness |
| GET | `/v1/haas/ready?scope=control\|execution\|capability` | readiness |
| GET | `/v1/haas/status` | runtime、adapter、queue、proxy、AIO、store 状态摘要 |
| GET | `/v1/haas/diagnostics` | 脱敏诊断摘要 |
| GET | `/v1/haas/harnesses` | configured harness 完整列表（与 `/list-apps` 的 id 数组等价但含详情） |
| POST | `/v1/haas/harnesses` | 创建 configured harness（`Idempotency-Key` 支持） |
| PUT | `/v1/haas/harnesses/{harness_id}` | 更新 harness，`id`/`base`/`createdAtMs` 不变 |
| DELETE | `/v1/haas/harnesses/{harness_id}` | 删除 harness，不删历史 session |
| GET | `/v1/haas/models` | 全局 model catalog（按 base 分组） |
| GET | `/v1/haas/sessions` | 跨 user 分页列出 session（管理视角） |
| GET | `/v1/haas/sessions/{session_id}/events` | HaaS canonical SSE replay/live（带 cursor） |
| GET | `/v1/haas/sessions/{session_id}/invocations/{invocation_id}/events` | invocation 级 canonical SSE replay/live |
| POST | `/v1/haas/sessions/{session_id}/invocations/{invocation_id}/cancel` | 取消运行中 invocation，幂等（`Idempotency-Key` 支持） |
| GET | `/v1/haas/sessions/{session_id}/artifacts` | HaaS artifact listing |
| GET | `/v1/haas/sessions/{session_id}/artifacts/archive` | 下载 session artifact 归档（zip） |
| POST | `/v1/haas/files` | 上传 input file（multipart），返回 `File` 对象 |
| GET | `/v1/haas/files/{file_id}/content` | 下载 artifact 原始 bytes（`nosniff`） |
| GET | `/v1/haas/files/{file_id}/pdf` | 可选 PDF preview；未实现返回 `501 haas_preview_unavailable` |

artifact 端点详情见 [Artifact Store](../artifact-store/README.zh-CN.md) 第 5 节；文件模型统一
`File` schema。

### 5.3 Legacy Sidecar Shim（不在本项目范围）

**决策（2026-08-30）**：`/v1/codex-worker/*` shim **不实现**。HaaS 与
`mpa-codex-worker` 只是架构同构，不承担其迁移职责；如确需迁移旧上游，单独
立项。详见 [specs/README §3.1.1](../README.zh-CN.md#311-范围决策不实现-mpa-codex-worker-迁移-shim)。

下表仅作为**历史设计记录**保留，供将来立项时参考，**不是待办项**；实现代码
不得据此新增 `/v1/codex-worker/*` 路由。

| Legacy path | HaaS target | 规则 |
|-------------|-------------|------|
| `/v1/codex-worker/health` | `/v1/haas/health` | 响应字段保持旧兼容 |
| `/v1/codex-worker/ready` | `/v1/haas/ready` | `scope` 语义保留 |
| `/v1/codex-worker/status` | `/v1/haas/status` | 增加 deprecation notice |
| `/v1/codex-worker/sessions` | `POST /run` | 映射为 configured harness base=`codex`，`userId` 按旧 actor |
| `/v1/codex-worker/sessions/{id}/turns` | `POST /run` | `sessionId=id`，续写语义映射为 ADK session continuation |
| `/v1/codex-worker/sessions/{id}/events` | `/v1/haas/sessions/{id}/events` | 支持旧 event name projection |

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

- `appName` 必填；`userId` 必填（可由 `Authorization` 派生但显式传入优先）；`sessionId` 可选，缺省生成 `hsess_<rand>`。
- `newMessage.parts[]` 支持 `text`、`inlineData`；HaaS 额外接受 `fileId`（引用已上传 file）作为扩展。
- `streaming` 仅 `/run_sse` 生效，默认 `false`。
- 顶层未知字段在 ADK-compatible path 上忽略。HaaS 扩展只能放入 `haas` 嵌套对象，不得污染 ADK 顶层字段。

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

### 6.3 ADK `Session`

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

### 6.4 HaaS Error

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
  -> invocation created
  -> adapter turn started
  -> canonical events appended
  -> events projected to ADK Event / SSE frames
  -> invocation terminal
  -> session state and artifacts committed
```

Invocation（内部 Run）状态：

| Status | Terminal | 语义 |
|--------|----------|------|
| `running` | no | 已接受并执行中 |
| `completed` | yes | harness 正常完成，stream 关闭 |
| `failed` | yes | harness 或服务失败，错误可读 |
| `incomplete` | yes | 预算/超时截断，保留部分输出 |
| `cancelled` | yes | caller 取消，保留已提交事件 |

同一个 session 同一时刻只允许一个 running invocation；第二个 `/run` 返回 `409 session_busy`。

SSE framing（`/run_sse`）：

```text
data: {"id":"evt_...","invocationId":"inv_abc",...}

```
heartbeat 使用 SSE comment `: keep-alive`，不产生事件。invocation 完成即关闭 stream；`/run` 则收集后一次性返回 JSON 数组。

## 8. 安全与权限

- `GET /list-apps` 需鉴权，只返回 caller scope 内 harness。
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
| idempotency store 不可用 | `503 haas_idempotency_store_unavailable`，不得执行任务 |
| session busy | `409 session_busy`，可带 `retryAfterMs` |
| `/run_sse` 断线 | 不取消 invocation；client 用 `POST /run_sse` + `Last-Event-ID` 续接（服务端按 event id 定位原 invocation，回放其后事件并续 live，不新建 turn），或通过 `GET /apps/.../sessions/{sid}` 读回 events |
| adapter crash | invocation 进入 `failed`/`incomplete`，持久化后可读 |
| cancel retry | 幂等成功，不删除 session（见 `POST /v1/haas/.../invocations/{id}/cancel` 扩展见 session-runtime） |

## 11. 测试计划与验收

文档阶段：

- `git diff --check`
- 所有 public path 在本文件和组件 spec 中一致；OpenAPI 覆盖 ADK 与 HaaS native 两面（legacy 条目保留但不实现，见 §5.3）。

实现阶段：

- ADK API server 兼容：用 ADK 官方 client / curl `list-apps`、`run`、`run_sse` 对比真实 ADK 行为。
- SSE：progressive flush、stream 关闭语义、heartbeat 不产生事件。
- `/run` 非流式输出与 `/run_sse` 流式聚合输出一致（parity）。
- Event：`content.role`/`parts`/`actions`/`invocationId` 字段完整；ADK 2.0 字段 `nodeInfo` 按需出现。
- Auth/scope：两 principal 交叉访问 session/user 全部 404。
- Idempotency-Key：重复 `POST /run` 不重复启动 harness。
