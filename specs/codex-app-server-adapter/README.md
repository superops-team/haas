# Codex App-Server Adapter 组件规格

Status: Draft
Last reviewed: 2026-08-26
Related specs: [Harness Adapter](../harness-adapter/README.md), [Session Runtime](../session-runtime/README.md), [Event Log & SSE](../event-log-sse/README.md), [Model Proxy](../model-proxy/README.md)

## 1. 组件定位

Codex App-Server Adapter 是首期 HaaS 的唯一 P0 concrete harness adapter。它负责连接、初始化、驱动和恢复 Codex app-server，并把 Codex 原生 JSON-RPC notification 转换成 canonical HaaS events，最终投影为 ADK `Event`。

Codex app-server 是内部实现细节。上游不得直接连接 Codex WebSocket、Unix socket 或 stdio，也不得依赖 Codex `threadId`、`turnId`、notification method 或 rollout 文件路径。

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
3. `stdio://`：测试 fallback 和最小本机 smoke。

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
  "approvalPolicy": "never",
  "sandboxPolicy": {
    "mode": "workspace-write",
    "writableRoots": ["/workspace"]
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
- 请求在 initialize 前被 Codex 拒绝时，adapter 必须将其归类为 adapter bug 或 startup race，而不是上游请求错误。
- app-server 重启后 generation 增加，adapter 重新连接并重新 initialize。
- 若 native thread 无法恢复，HaaS session 必须进入 `non_resumable` 或创建新的 session，不能静默丢历史。

Turn 规则：

- `turn/start` 成功返回只表示 native turn accepted，不表示 HaaS response completed。
- Codex notification 是 streaming source，最终 response 由 Session Runtime 汇总。
- `turn/completed`、`turn/failed`、`turn/interrupted` 必须映射为唯一 HaaS terminal event。
- 同一 HaaS session 不并发发起两个 Codex turn。

## 8. 安全与权限

- `CODEX_HOME` 必须是 session/workspace scoped 或明确隔离的 runtime home。
- Codex model provider 不得保存真实 API key；优先通过 model proxy 和 `auth.command` 获取短期 bearer。
- `approvalPolicy=never` 是无人值守默认；需要人工审批必须通过 HaaS approval bridge 扩展后再启用。
- Codex sandbox policy 来自 Sandbox Runtime 的投影（由 Policy Controller 驱动），不由 adapter 自行推断扩大；adapter 通过 `sandbox_declaration()` 仅声明需求。
- WebSocket auth token 只能通过文件或 secret handle 提供，不出现在命令行参数、日志或 status。
- Codex raw event、rollout、command output 在进入 Event Log 前必须脱敏。

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

| 场景 | 行为 |
|------|------|
| socket 未创建 | `ready=false`，execution path bounded wait；超时返回 `haas_adapter_unavailable` |
| initialize 失败 | adapter `degraded`，新 turn fail closed |
| WebSocket queue overloaded | 映射为 retryable `haas_adapter_overloaded` 或 `rate_limited` |
| Codex process exit | generation 增加，尝试重启/重连；active turn terminal failed 或 incomplete |
| notification 缺 terminal | timeout 后由 Session Runtime 生成 invocation `failed` 或 `incomplete` |
| cancel 请求 | 调用 `turn/interrupt`；即使 native cancel 慢，HaaS cancel API 需快速返回 accepted/current state |
| schema drift | probe 失败，阻塞 release；运行时返回 `haas_adapter_incompatible` |

## 11. 测试计划与验收

- Unit：JSON-RPC request id 匹配、server request 识别、event normalizer、usage normalizer、safe rollout ref。
- Integration：stdio fake app-server handshake、thread/start、turn/start、terminal event。
- Local E2E：`codex app-server --listen stdio://` 或 `ws://127.0.0.1:<port>` 完整跑一次 turn。
- Schema：运行 `codex app-server generate-json-schema` 并与 pinned schema fixture 比对。
- Cancellation：启动长 turn 后调用 cancel，最终 invocation status 为 `cancelled`。
- Security：Codex env/config/rollout/log/event 不包含真实 provider key、Authorization 或 raw prompt。
