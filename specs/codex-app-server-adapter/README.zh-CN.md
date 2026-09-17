# Codex App-Server Adapter 组件规格

[English](README.md) | **简体中文**

Status: Draft
Last reviewed: 2026-09-14
Change ID: unified-runtime-approval-policy
Related specs: [Harness Adapter](../harness-adapter/README.zh-CN.md), [Session Runtime](../session-runtime/README.zh-CN.md), [Event Log & SSE](../event-log-sse/README.zh-CN.md), [Model Proxy](../model-proxy/README.zh-CN.md)
固定 Codex CLI 版本：`0.152.1`

## 1. 组件定位

Codex App-Server Adapter 是首期 HaaS 的唯一 P0 concrete harness adapter。它负责连接、初始化、驱动和恢复 Codex app-server，并把 Codex 原生 JSON-RPC notification 转换成 canonical HaaS events，最终投影为 ADK `Event`。

Codex app-server 是内部实现细节。上游不得直接连接 Codex WebSocket、Unix socket 或 stdio，也不得依赖 Codex `threadId`、`turnId`、notification method 或 rollout 文件路径。

内置本地执行通过 `thread/start` 或 `thread/resume` 配置覆盖传入已应用 bare model
与 session-scoped、generationed loopback provider capability，遵循固定 0.152.1
schema。Resume 或 rebind 更新当前 proxy route/token，不替换 native thread identity；
`turn/start` 接收所选模型。Harness 只能获得 session-scoped proxy capability，不能获得
上游 key 或 credential-resolver descriptor，也不使用个人 Codex 认证。Proxy capability
跨单次 invocation terminal 继续有效，仅在 session 删除/撤销或 runtime shutdown 时撤销。

本地 proxy 集成采用保守的 Responses 工具集合：关闭原生多 agent namespace 和 provider 托管 web search，同时设置 `model_reasoning_summary=auto`，只请求过程时间线所需的 provider 安全推理摘要。Raw reasoning 继续保持私有且绝不投影。支持 Responses 不等于支持其他扩展。普通 function 工具仍受现有 sandbox/policy 管控；proxy 不得静默丢弃工具或改写原生工具调用。Adapter 在 start 和 resume 均应用该配置。真实 Codex wire 测试拒绝 namespace/web-search 声明、确认安全摘要请求与普通 function 工具仍存在；真实 provider smoke 必须通过此链路完成。

## 2. 来源与依据

| 来源 | 采用内容 |
|------|----------|
| Codex manual `Codex App Server` | transport、initialize/initialized handshake、thread/turn lifecycle、WebSocket auth |
| 本机 `codex app-server --help` | `--listen`、`--ws-auth`、schema generation 命令与当前 CLI 版本 |
| `mpa-codex-worker` Codex adapter spec | WebSocket-over-UDS、secretless auth.command、MCP config、event terminal contract |
| 本组件总览 | 首期 Codex app-server adapter 要求 |

## 3. 上游与下游关系

| 方向 | 对象 | 关系 |
|------|------|------|
| 上游 | Harness Adapter interface | adapter 实现统一接口 |
| 上游 | Session Runtime | 触发 session prepare、turn start、cancel、resume |
| 下游 | Codex app-server process | JSON-RPC over stdio/WebSocket/Unix socket |
| 下游 | Model Proxy | Codex model provider 指向 loopback proxy |
| 下游 | MCP / Tool / Skill Runtime | Codex config、skills root、MCP servers |
| 下游 | Event Log & SSE | 输出 canonical events |

## 4. 职责边界

负责：

- 管理 Codex app-server process 或连接到已存在 listener。
- 完成每个 connection 的 `initialize` request 和 `initialized` notification。
- 调用 `thread/start`、`thread/resume`、`thread/fork`、`turn/start`、`turn/steer`、`turn/interrupt`。
- 解析 Codex response、notification 和 server request。
- 将 Codex `item/*`、`turn/*`、tool、permission、usage 和 error event 转成 canonical event。
- 维护 app-server generation，供 restart/reconnect 后判断 native session 是否仍可恢复。
- 基于 pinned Codex version 生成或校验 app-server schema。
- 确保 Codex config 使用 sidecar model proxy 和 secretless auth path。

不负责：

- 不定义 public API。
- 不保存 HaaS invocation/session/event 事实。
- 不把 raw Codex JSON-RPC message 暴露给上游。
- 不直接保存真实 provider key。
- 不自行决定 workspace、network、tool 或 approval policy。
- 不绕过 Codex sandbox；Codex sandbox 作为内层，外层由 Sandbox Runtime 统一提供。

## 5. 核心接口

### 5.1 Transport

首期支持顺序：

1. `unix://PATH`：生产默认。Unix socket 上使用标准 WebSocket HTTP Upgrade。
2. `ws://127.0.0.1:PORT`：本地调试和容器内 loopback。
3. `stdio://`：测试 fallback 和最小本机 smoke。子进程 stdin/stdout NDJSON（每行一个 JSON-RPC 消息）。Transport 必须显式配置 64 MiB 的有界帧上限，足以承载 8 MiB 解码后 evidence payload、JSON-RPC envelope 与最坏 JSON string 转义，不得沿用 asyncio 约 64 KiB 的默认逐行上限。超过显式上限或格式错误的 NDJSON 属于带稳定安全原因的 adapter protocol failure，不得表现成无法解释的正常连接结束。

非 loopback WebSocket 必须开启 `--ws-auth` 且置于 TLS 或可信隧道后。HaaS 不把 Codex app-server listener 直接暴露到公网。

### 5.2 JSON-RPC Methods

| Method | 方向 | 用途 |
|--------|------|------|
| `initialize` | HaaS -> Codex | 连接级初始化 |
| `initialized` | HaaS -> Codex | 初始化完成 notification |
| `thread/start` | HaaS -> Codex | 创建新 Codex thread |
| `thread/resume` | HaaS -> Codex | 恢复已有 thread |
| `thread/fork` | HaaS -> Codex | 可选，未来支持分支会话 |
| `turn/start` | HaaS -> Codex | 启动一次 turn |
| `turn/steer` | HaaS -> Codex | 可选，向运行中 turn 追加输入 |
| `turn/interrupt` | HaaS -> Codex | 取消运行中 turn |
| `thread/read` | HaaS -> Codex | 诊断或恢复校验 |
| `model/list` | HaaS -> Codex | 能力探测 |
| `mcpServerStatus/list` | HaaS -> Codex | MCP 感知验证 |
| `skills/list` | HaaS -> Codex | skill 感知验证 |

### 5.3 Internal Adapter Methods

```python
async def connect(endpoint: CodexEndpoint) -> CodexConnection: ...
async def initialize(conn: CodexConnection, capabilities: dict) -> CodexCapabilities: ...
async def start_thread(conn: CodexConnection, request: CodexThreadStart) -> CodexThreadRef: ...
async def resume_thread(conn: CodexConnection, ref: CodexThreadRef) -> CodexThreadRef: ...
async def start_turn(conn: CodexConnection, request: CodexTurnStart) -> CodexTurnRef: ...
async def interrupt_turn(conn: CodexConnection, turn_id: str, reason: str) -> None: ...
async def notifications(conn: CodexConnection) -> AsyncIterator[CodexWireMessage]: ...
```

## 6. 数据模型

### 6.1 CodexEndpoint

```json
{
  "transport": "unix_websocket",
  "listenUrl": "unix:///tmp/haas/codex.sock",
  "auth": {
    "type": "capability_token_file",
    "tokenFile": "/run/haas/codex-ws-token"
  },
  "schemaVersion": "codex-cli-0.149.1"
}
```

### 6.2 CodexThreadRef

```json
{
  "threadId": "thr_123",
  "source": "codex-app-server",
  "generation": 3,
  "rolloutRef": {
    "kind": "opaque",
    "safeId": "rollout_fingerprint_abc"
  }
}
```

`rolloutRef` 只能是不可反解的安全引用，不得包含本地绝对路径或 rollout 内容。

### 6.3 CodexTurnStart

```json
{
  "threadId": "thr_123",
  "input": [
    {
      "type": "text",
      "text": "Summarise README.md"
    }
  ],
  "model": "gpt-5.6-terra",
  "cwd": "/workspace",
  "approvalPolicy": "on-request",
  "sandboxPolicy": {
    "mode": "workspace-write",
    "writableRoots": ["/workspace"],
    "networkAccess": false
  },
  "timeoutMs": 900000
}
```

## 7. 运行模型与状态机

```text
not_started
  -> starting_process
  -> connecting_transport
  -> initializing
  -> ready
  -> turn_running
  -> idle
  -> degraded
  -> restarting
  -> connecting_transport
```

连接规则：

- 每个 connection 只能 initialize 一次。
- Connection 建立与重新初始化必须串行化。共享 adapter 处于断线状态时，并发的
  session prepare 必须复用第一个成功完成初始化的 connection；后到调用不得关闭或
  替换仍在握手中的 connection。
- 请求在 initialize 前被 Codex 拒绝时，adapter 必须将其归类为 adapter bug 或 startup race，而不是上游请求错误。
- app-server 重启后 generation 增加，adapter 重新连接并重新 initialize。
- 若 native thread 无法恢复，HaaS session 必须进入 `non_resumable` 或创建新的 session，不能静默丢历史。
- Reader loop 异常必须保留为有界安全 transport failure，并由 active turn 消费。Adapter 必须区分 clean peer close 与 frame-limit、decode、transport failure；不得静默丢弃 reader exception，随后只报告笼统的 connection ended。
- Connection 级 Codex notification 与 server request 必须先投递给每个 active turn consumer，再按 turn/thread 过滤。共享 app-server connection 上可以同时存在多个 HaaS session 的运行中 turn；任何 consumer 都不得以破坏性消费方式取走并丢弃属于其他 turn 的消息。使用有界的订阅前 replay buffer 覆盖 `turn/start` 接受后到 stream 订阅前产生的 notification。
- 每个 active turn consumer 使用有界 delivery queue。慢 consumer/满队列不得阻塞共享 app-server reader、JSON-RPC response 或其他 session。Adapter 只断开该 consumer，并让其已接受 invocation 以可重试 `haas_adapter_overloaded` 终结；不得静默丢弃 native event，也不得让无关 turn 失败。

Turn 规则：

- `turn/start` 成功返回只表示 native turn accepted，不表示 HaaS response completed。
- Codex notification 是 streaming source，最终 response 由 Session Runtime 汇总。
- `turn/completed`、`turn/failed`、`turn/interrupted` 必须映射为唯一 HaaS terminal event。
- 同一 HaaS session 不并发发起两个 Codex turn。
- 不同 HaaS session 可以在不同 Codex thread 上并发执行；delta、tool call、usage 与 terminal event 必须按原生 thread/turn identity 完整隔离。

过程事件规则：

- `item/reasoning/summaryTextDelta` 可以映射为脱敏的 `harness.reasoning.delta`。Raw `item/reasoning/textDelta` 保持私有，不得持久化或投影。没有安全 summary 时，Manager 根据 tool/lifecycle fact 展示通用类型化进度，不得重建 chain-of-thought。
- `item/agentMessage/delta` 与 `item/reasoning/summaryTextDelta` 携带 native `itemId`；reasoning 还保留 `summaryIndex`。Adapter 将其作为安全关联事实透传，并为每次真实模型 round trip 分配 invocation-scoped 稳定 `modelCallId`。Commentary、reasoning summary、其触发的 tool lifecycle 与该 round trip 的 usage 共享此 id；tool result 后的新 model output 开启下一个 id。这些 id 不包含 prompt 或 provider payload。
- `item/agentMessage/delta` 没有权威 phase。Adapter 立即携带 `itemId` 流式发送并保持未分类，直到匹配的 agent-message `item/completed` 提供 `phase=commentary|final_answer`；随后发送带同一 `itemId`、`modelCallId` 和 phase 的 `harness.output.item.completed`，且不重复 message 文本。Consumer 原地重新分类既有 item，不得根据自然语言猜测 phase。
- Reasoning `item/completed` 同样发送带 `itemId` 与 `modelCallId` 的 `harness.output.item.completed` fact，但不复制 summary 或 raw reasoning 内容。Consumer 使用该 lifecycle 边界冻结有界首屏 reasoning preview，同时只在显式详情中保留已经流式接收的 canonical summary 文本。
- Codex 0.152.1 的 `thread/tokenUsage/updated.tokenUsage` 是含 `last`、`total` 与可选 `modelContextWindow` 的对象，不是扁平 token counter。Adapter 发送 `scope=model_call` 的 `haas.usage.updated`：`last` 映射为 `usage`，`total` 映射为 `cumulativeUsage`，并关联当前 `modelCallId`。保留 `inputTokens`、`outputTokens`、`totalTokens`，将 `cachedInputTokens` 映射为 `cacheReadTokens`、`cacheWriteInputTokens` 映射为 `cacheWriteTokens`，并保留 `reasoningOutputTokens`。Native 未提供的 counter 保持缺失，不补零；`total` 只是 snapshot，不能再次与 model-call 数值相加。
- `cacheReadTokens` 是 `inputTokens` 中的缓存子集，`reasoningOutputTokens` 是 `outputTokens` 中的 reasoning 子集；计算总消耗时二者都不能再次相加。Usage notification 只为 model call 计量，不单独终结 stage：关联该调用的 tool 可能随后才开始或结束。只有所有关联 tool 进入终态，或后续 model output 开启下一次 model call 时，该 stage 才完成。
- Command execution、file change、MCP tool call 和受支持 function tool 的 `item/started`/`item/completed`，按 native item id 关联并映射为 `harness.tool.started` 与唯一的 `harness.tool.completed|failed`；output/progress delta 映射为有界 `harness.tool.output`。
- 对 command execution，adapter 在规范化 public event 前，将 native `command`、`cwd`、
  `aggregatedOutput`、`durationMs`、`exitCode` 与 best-effort `commandActions` 捕获到 scoped
  in-memory execution-evidence store。Sink 在 8 MiB（8,388,608 UTF-8 bytes）以内完整保留
  command output；中间 accumulator 不得使用更小上限。stdio frame 上限另行计入 JSON 开销。
  Public event 只携带安全动作摘要、有界脱敏 preview
  和 opaque evidence ref。Codex 0.152.1 将 turn command 的 stdout/stderr 合并在
  `aggregatedOutput`，且 `item/commandExecution/outputDelta` 没有 stream discriminator；
  adapter 必须如实标为“命令输出”，不得猜测拆成 stdout/stderr。
  Codex 可能用 argv 数组，也可能用 `/bin/zsh -lc \"<payload>\"` 这类序列化命令
  字符串表达 shell 调用。Adapter 必须为 `commandPreview` 与 execution evidence 解包可识别的
  `sh|bash|zsh -c|-lc|-cl` 外壳，让用户看到实际 payload；无法识别的命令字符串保持原样。
  该归一化只用于展示与证据，不得改变发送给 Codex 的实际命令。
  私有增量 accumulator 在 command 完成时清理，并在 turn finalize 或 adapter shutdown 时
  兜底清理；command 结束后的有界保留期只由 scoped、已脱敏的 evidence record 承担。
- 只有 command 将 HTTPS URL 明确输出为用户操作且具备可识别的授权语义时，adapter 才可
  将其标记为用户授权 URL；即使 URL query 不显式包含 expiry，evidence record 也强制
  有界 expiry，native 提供可信且更早的 expiry 时取更早者。完整 URL 只存于 execution
  evidence；未知 signed URL 按普通规则脱敏，绝不进入 public event。
- Assistant message 的 `phase=commentary` 是过程信息，不是最终答案；`phase=final_answer` 是最终答案证据；`phase=null` 属于 legacy/unknown，不能覆盖 failed/incomplete turn。
- Command/file approval 与 `item/tool/requestUserInput` server request 必须和 notification 并发 drain。Blocking request 暂停 turn，但不消耗 model-proxy capability，也不生成 terminal event。Manager 作出决策后，HaaS 使用原 JSON-RPC request id 回应；cancel/turn terminal 时所有 pending request 必须恰好 resolve/cancel 一次。

## 8. 安全与权限

- `CODEX_HOME` 必须是 session/workspace scoped 或明确隔离的 runtime home。
- Codex model provider 不得保存真实 API key。Local API 通过 app-server override 传入
  session-scoped、generationed loopback proxy capability，并在 session 删除/撤销或
  runtime shutdown 时撤销。Codex 0.152.1 必须先 `thread/unsubscribe` 再
  `thread/resume` 才能替换已加载 thread 的 provider override，同时保留原 native
  thread id。Runtime 退出必须关闭所属 app-server transport。
- Adapter 启动的任何 Codex app-server 子进程都必须接收显式 allowlist 环境变量。默认继承 allowlist 仅限执行 Codex 所需的进程基础项（`PATH`）、解析隔离 home（`HOME`）、创建临时文件（`TMPDIR`/`TMP`/`TEMP`）以及保持 Unicode/locale 行为稳定（`LANG`/`LC_ALL`/`LC_CTYPE`/`LC_MESSAGES`）。Provider key、云凭据、token、password、cookie 和其他 credential-like 变量必须从构造上不被继承；新增任何环境变量都必须先有 spec delta，说明其必要性以及为什么它不是 secret channel。
- `approvalPolicy=on-request` 是 fresh interactive session 默认值。只有 command approval、
  file-change approval 与 blocking input response 链路都可用且 capability advertise
  `human_bridge` 时才可使用；否则必须在 acceptance 前拒绝，不能静默降级。`never` 是显式
  no-prompt 模式：Codex 可执行 sandbox/policy 已授权的动作，但任何 native escalation request
  都被拒绝并收敛为稳定 policy failure；它绝不表示 danger-full-access。
- Adapter 只转发 server advertise 且 policy 标记 approval-eligible 的 decision/scope。默认 grant
  仅覆盖当前精确动作；resolution 对原 JSON-RPC request id 恰好回答一次，不重启 turn、不创建
  replacement invocation，也不修改 session policy。
- 动态 session policy 只能在 HaaS revision barrier applied 后，于后续 `thread/start`、
  `thread/resume` 或 `turn/start` 投影；active native turn 保持原 approval/sandbox projection。
- Codex 0.152.1 在 Default collaboration mode 下启用 `human_bridge` 时，还必须在线程配置中设置已声明的 `features.default_mode_request_user_input=true`。HaaS 不得为了暴露该工具而把会话切换到 Plan mode。
- Codex sandbox policy 来自 Sandbox Runtime 的投影（由 Policy Controller 驱动），不由 adapter 自行推断扩大；adapter 通过 `sandbox_declaration()` 仅声明需求。
- Codex `turn/start.sandboxPolicy.networkAccess` 必须 fail-closed。默认值为 `false`，只有冻结后的有效网络策略显式设置 `defaultAction=allow` 时才可为 `true`；`defaultAction=deny`、缺少策略数据或策略数据格式错误都必须投影为 `false`。
- WebSocket auth token 只能通过文件或 secret handle 提供，不出现在命令行参数、日志或 status。
- Codex raw event、rollout、command output 在进入 Event Log 前必须脱敏。
- Raw command evidence 不得发送到 Event Log。Adapter 只能写入 Session Runtime 提供的
  有界 in-memory evidence sink；sink 不可用时退化为既有安全摘要/preview，不能为展示
  详情而把内容泄露到 event。

### 8.1 Capability Discovery 投影

Codex app-server 首期在 protocol capability snapshot 中必须投影：
`streaming=native`、`sessionContinuation=native`、`pausing=native`、
`cancellation=best_effort`、`approval=unattended_only`、`input=unsupported`、
`toolRestriction=advisory`、`mcp=native`、`skills=native`、
`files=workspace_scan`、`usage=native`，并受 live probe 状态约束。Probe 失败时，
已实现能力改为 `unavailable`；不得声明比 adapter 实际能力更强的 enforcement、
human approval 或 structured input 支持。Public snapshot 不得暴露 `transport`、`schemaVersion`、socket
path、app-server generation 或 native thread/turn id。

## 9. 可观测性

Adapter 至少暴露以下状态：

| 字段 | 说明 |
|------|------|
| `status` | `ready`、`degraded`、`unavailable` |
| `runtimeVersion` | `codex --version` 或 pinned package version |
| `transport` | `unix_websocket`、`loopback_websocket`、`stdio` |
| `generation` | app-server process generation |
| `lastConnectedAt` | 最近连接成功时间 |
| `lastErrorSafeReason` | 最近错误安全原因 |
| `activeTurns` | 当前运行 turn 数 |
| `pendingRequests` | JSON-RPC pending request 数 |

日志事件：

- `haas.codex.process_started`
- `haas.codex.connected`
- `haas.codex.initialized`
- `haas.codex.thread_started`
- `haas.codex.turn_started`
- `haas.codex.turn_terminal`
- `haas.codex.reconnect`
- `haas.codex.schema_mismatch`

## 10. 失败与恢复

### 10.1 暂停与继续

Pause 与 Stop 都由 adapter 对确切 native thread/turn 发送 `turn/interrupt`。JSON-RPC
返回 `{}` 只代表 acknowledgement；只有匹配的 `turn/completed(status=interrupted)`
notification 才确认完成。Adapter 输出 `harness.turn.interrupted`，不得改写成 cancelled。
Continue 先执行 `thread/resume(excludeTurns=true)`，随后在同一 thread 启动新的 native turn。
若 thread 缺失则返回 non-resumable，禁止静默创建丢失上下文的新 thread。

| 场景 | 行为 |
|------|------|
| 无副作用 preflight 时 socket 未创建 | `ready=false`；返回 pre-acceptance `haas_adapter_unavailable`，不持久化 invocation |
| preflight initialize 失败 | adapter `degraded`；返回 pre-acceptance `haas_adapter_unavailable` 或 `haas_adapter_incompatible`，不创建 invocation |
| WebSocket queue overloaded | 接受前返回 retryable `haas_adapter_overloaded`；接受后收敛为带该稳定 code 的 `haas.turn.failed`，HTTP 200 |
| 单 turn notification consumer queue overloaded | 只隔离并断开慢 consumer；其已接受 invocation 以可重试 `haas.turn.failed` 和 `haas_adapter_overloaded` 收敛；共享 reader 与其他 session 继续运行 |
| Stdio notification 大于 asyncio 默认逐行上限 | 在显式 adapter 帧上限内正常读取，并按顺序保留 tool terminal 与后续 turn terminal |
| Stdio frame 超过显式上限或格式错误 | accepted invocation 恰好一次以 `haas_adapter_unavailable` 和有界安全 transport reason 结束；不得误报为 tool failure 或笼统 clean EOF |
| 接受后 Codex process exit | generation 增加并尝试重启/重连；active turn 使用 failed/incomplete terminal event 结束，HTTP 200 |
| notification 缺 terminal | timeout 后由 Session Runtime 生成 invocation `failed` 或 `incomplete` |
| active 或可恢复 session 中 model-proxy token 失效 | session/harness/provider scope 与 owned credential channel 仍匹配时只刷新/rebind 一次 session-scoped capability；否则以稳定 `model_proxy_token_invalid` 进入 failed，并保留阶段性进展 |
| Blocking server request 不支持或无法恢复 | 以稳定 interaction-unsupported/recovery code fail closed；不得伪造答案或猜测选择后继续 |
| cancel 请求 | 调用 `turn/interrupt`；即使 native cancel 慢，HaaS cancel API 需快速返回 accepted/current state |
| `turn/interrupt` 返回 `{}` | 仅视为确认收到；继续 drain native notification，不能伪造 terminal |
| `turn/completed(status=interrupted)` | 生成一个 normalized `harness.turn.interrupted`；Session Runtime 按已持久 Pause 意图映射为 canonical `haas.turn.interrupted`，按 Stop 意图映射为 `haas.turn.cancelled`；session-scoped model proxy capability 持续可用直到 session 删除/撤销或 runtime shutdown |
| schema drift | probe 失败，阻塞 release；运行时返回 `haas_adapter_incompatible` |

## 11. 测试计划与验收

- Unit：JSON-RPC request id 匹配、server request 识别、event normalizer、`itemId`/`modelCallId` 关联、嵌套 `last`/`total` usage normalizer、safe rollout ref、单条超过 64 KiB 的 NDJSON notification，以及完整 100,001-byte command-evidence output。
- Integration：stdio fake app-server handshake 与无副作用 preflight 在持久 invocation acceptance 前完成；thread/start/turn/start 只能在 acceptance 后执行，accepted failure 均收敛为 HTTP-200 terminal event。
- Local E2E：`codex app-server --listen stdio://` 或 `ws://127.0.0.1:<port>` 完整跑一次 turn。
- Schema：运行 `codex app-server generate-json-schema` 并与 pinned schema fixture 比对。
- Cancellation：启动长 turn 后调用 cancel，最终 invocation status 为 `cancelled`。
- Pause/Continue：确认 interrupt acknowledgement 本身不是终态，匹配 native terminal 保持 `harness.turn.interrupted`，Pause 映射为 canonical `interrupted`，且 `thread/resume(excludeTurns=true)` 发生在唯一关联新 turn 之前。
- Security：Codex env/config/rollout/log/event 不包含真实 provider key、Authorization 或 raw prompt。
- Capability：interaction bridge 通过前，caller-visible 投影报告 `approval=unattended_only`、`input=unsupported` 与 `toolRestriction=advisory`；完整 bridge 通过后，approval/input 必须同时升级为 `human_bridge`，禁止部分 advertise。投影跟随 live probe availability，并省略 transport/socket/schema/native id。
- Golden event：真实 0.152.1 的 `item/started`、output/progress、`item/completed`、reasoning、assistant phase、嵌套 token usage、approval、user-input 和 `serverRequest/resolved` fixture 均映射为有关联关系且脱敏的稳定类型。
- Command evidence：真实 0.152.1 command fixture 在 scoped evidence sink 中保留
  command/cwd/合并输出，遮蔽 credential value，并在过期前逐字节保持已接受授权 URL
  可用；Event Log 只获得 `evidenceRef`/expiry 与安全字段。
- Interactive E2E：一次 command approval、一次 file approval 和一次 blocking 结构化问题都暂停并续接同一 native turn；重连重建 pending card，重复 decision 被拒绝。
- Defaults/modes：fresh turn 投影 `workspace-write`、公网 allow 和 `on-request`；`never`
  不询问并拒绝 escalation；`always` 对 pinned Codex schema 支持的每个可审批动作询问。
- Revision：native turn blocked/running 时修改 approval mode，验证 active request 保持旧投影，
  下一 invocation 使用 applied revision。

### 11.1 Schema fixture 契约

- fixture 路径：`tests/fixtures/codex/schema/codex-cli-<version>.json`。
- 当前 fixture：`codex-cli-0.152.1.json`（302 个生成的 JSON 文件）。相对 0.151.0 新增 `v2/AuthRecoveryNotification.json`，另有 8 个既有 schema 变化；adapter 必须安全忽略未知的加法 notification，并继续满足下文已验证的映射。
  `<version>` 是 `codex --version` 输出的**语义版本号**（如 `0.150.1`）；`AdapterProbe.runtimeVersion` 保留完整字符串（如 `codex-cli 0.150.1`）。
- fixture 内容：单 JSON 对象 `{"codexCliVersion": "<version>", "files": {相对路径: JSON 内容}}`。`files` 覆盖 `generate-json-schema` 输出目录内的全部 `.json`（含根 bundle 与 `v1/`、`v2/` 子目录）。
- 比对流程：probe 时重新运行 `codex app-server generate-json-schema`，对 `files` 做结构化 deep-equal（不依赖 JSON 序列化顺序）；success 时 `schema_drift(fixture, current) == []`。升级 Codex 版本先重新生成 fixture，再跑比对。
- 失败语义：drift 非空 → `probe.status=unavailable`，`safeReason=schema_mismatch`，阻塞 release；运行时返回 `haas_adapter_incompatible`。
