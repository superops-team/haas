# Manager HaaS Sidecar Backend 组件规格

[English](README.md) | **简体中文**

Status: Draft
Last reviewed: 2026-09-10
Change ID: manager-haas-sidecar-backend
Related specs: [HaaS Protocol](../haas-protocol/README.zh-CN.md), [Manager Delegation](../manager-delegation/README.zh-CN.md), [Manager Product Identity](../manager-product-identity/README.zh-CN.md), [Model Proxy](../model-proxy/README.zh-CN.md), [MCP / Tool / Skill Runtime](../mcp-tool-skill-runtime/README.zh-CN.md), [Codex App-Server Adapter](../codex-app-server-adapter/README.zh-CN.md), [Config](../config/README.zh-CN.md), [Security Boundary](../security-boundary/README.zh-CN.md), [Observability](../observability/README.zh-CN.md)

## 1. 组件定位

Manager HaaS Sidecar Backend 定义 OpenHarness manager 如何在桌面产品中把 HaaS 作为标准执行后端使用。

Manager 可以为了独立桌面分发托管本地 HaaS sidecar 进程，也可以连接用户或运维配置的远程 HaaS sidecar。两种模式下，manager 到 HaaS 的通信都必须使用同一套 HaaS HTTP/SSE 协议和同一个 manager-facing client 合同。Manager 不得绕过 HaaS 直接调用 Codex app-server、Codex CLI、HaaS Python service object、模型 provider、MCP server 或 skill materialization 内部实现。

规范执行路径：

```text
OpenHarness GUI
  -> manager local API / session owner
  -> HaasClient
       -> local managed sidecar  (http://127.0.0.1:<port>)
       -> remote sidecar         (https://...)
  -> HaaS ADK /run_sse + HaaS native /v1/haas/*
  -> HaaS Session Runtime / Event Log / Policy / Model Proxy / MCP / Skills
  -> Codex app-server adapter
  -> Codex runtime
```

Codex 始终是 HaaS adapter 的内部实现细节。它的 stdio、Unix socket 或 WebSocket transport 在 HaaS 内部配置，不得成为 manager 的公共合同。

## 2. 来源与依据

| 来源 | 采用内容 |
|------|----------|
| 产品决策 | Local managed HaaS sidecar 是正式桌面执行路径；remote HaaS sidecar 通过配置保留可选入口。 |
| 产品决策 | Manager 到本地/远程 sidecar 的协议必须一致，以持续验证标准 HaaS sidecar 入口层。 |
| 产品决策 | 直接 embedded Codex 不是默认路径，因为它会绕过 HaaS sidecar、model proxy、MCP、skills、policy、event log 和 session/runtime 合同。 |
| Manager Delegation | 首次成功 delegation 固定 manager session binding；后续 turn 复用同一个 HaaS delegated session，并按需恢复 runtime 资源。 |
| HaaS Protocol | ADK `/run_sse` 是执行流；`/v1/haas/*` 承载 HaaS native control-plane 操作。 |
| Model Proxy | Provider credential 必须保持 secretless，由 HaaS 解析，不暴露给 Codex 或 manager-visible logs。 |
| MCP / Tool / Skill Runtime | MCP 和 skills 必须由 HaaS materialize，确保 proxy、policy 与 audit 语义走真实 sidecar 路径。 |
| Manager Product Identity | OpenHarness local-first 且无产品云登录；local HaaS sidecar 使用本地 token 和用户拥有的配置。 |

## 3. 上游与下游关系

| 方向 | 组件 | 关系 |
|------|------|------|
| 上游 | OpenHarness GUI | 选择 local managed 或 remote sidecar mode，展示状态，并把用户 turn 发给 manager local API。 |
| 上游 | Manager session store | 拥有 manager session id、用户可见 transcript 状态和 HaaS binding metadata。 |
| 上游 | Manager MCP / skills / provider settings | 为 HaaS materialization 提供用户授权的配置意图与 secret reference。 |
| 下游 | HaaS Protocol | 提供 manager 用于 HaaS-backed session 的唯一执行与 control-plane 协议。 |
| 下游 | HaaS local sidecar supervisor | 为桌面分发启动、停止、监控本地 sidecar 进程。 |
| 下游 | Remote HaaS sidecar | 在配置的 HTTPS endpoint 上提供同一协议。 |
| 下游 | HaaS Model Proxy | 拥有 provider credential resolution 与 provider compatibility transforms。 |
| 下游 | HaaS MCP / Tool / Skill Runtime | 拥有 MCP proxy、skill snapshot materialization 和 adapter-specific tool config。 |
| 下游 | HaaS Codex Adapter | 拥有 Codex 原生 transport、JSON-RPC、thread/turn lifecycle 与 event normalization。 |

## 4. 职责边界

Manager 负责：

- 提供 `local_managed` 与 `remote` 两种 HaaS backend 选择。
- 当 `mode=local_managed` 且 autostart 开启时，启动并监控本地 HaaS sidecar。
- 将本地/远程 sidecar endpoint 和非 secret 偏好写入 manager settings。
- 只通过 manager secret store 存储 HaaS bearer token 或等价 credential。
- 对本地和远程 sidecar 使用同一个 `HaasClient` 实现。
- 为 manager session 创建或恢复到 HaaS delegated session 的 binding。
- 将 manager-effective MCP、skill、provider、workspace 和 policy 配置作为意图提交给 HaaS，不提交 Codex-native config。
- 保留 UI approval 与面向用户的恢复决策入口。
- 向 GUI 暴露安全的 sidecar health、readiness、capability 和 binding 状态。

HaaS 负责：

- 对本地和远程 caller 暴露同一 ADK-compatible 与 HaaS native HTTP/SSE 协议。
- 拥有 session runtime、event log、idempotency、cancellation、approval relay、policy、model proxy、MCP proxy、skill materialization、artifact store 和 adapter execution。
- 在 Codex adapter 内部拥有 Codex app-server transport 与 lifecycle。
- 在 turn 运行前验证 materialized MCP、skills、provider route 与 policy snapshot。
- 提供 capability discovery，使 manager 能判断 local bind mount、remote workspace、MCP、skills 和 model proxy 是否可用。
- 当请求的能力不可用或不安全时，以结构化 HaaS error fail closed。

不负责：

- Manager 不直接调用 Codex app-server、Codex CLI、model provider API、MCP server 或 skill loader 来执行 HaaS-backed session。
- Local sidecar supervisor 不定义 HaaS 执行语义；它只管理进程生命周期。
- HaaS 不决定 manager UX intent routing 或用户授权文案。
- Remote HaaS 不隐式接收本机 host bind mount；remote workspace transfer 是独立能力合同。

## 5. 核心接口

### 5.1 Manager Backend 配置

```toml
[execution]
backend = "haas"

[haas]
mode = "local_managed" # local_managed | remote
base_url = "http://127.0.0.1:8092"
auth = "bearer"
token_ref = "secret://manager/haas/default"
request_timeout_seconds = 30

[haas.local_managed]
autostart = true
host = "127.0.0.1"
port = 8092
port_selection = "fixed" # fixed | auto
data_dir = "<manager-state>/haas"
log_file = "<manager-state>/logs/haas-sidecar.log"

[haas.remote]
base_url = "https://haas.example.com"
tls_verify = true
capability_probe = true
```

规则：

- `execution.backend="haas"` 是 HaaS 执行后端默认选择，但本身不表示 local 或 remote。
- `haas.mode` 只选择 endpoint owner，不得改变 manager 到 HaaS 的协议语义。
- `local_managed` 要求 loopback `base_url`（`127.0.0.1` 或 `localhost`）。Remote URL 不得启用 local autostart。
- `api_token`、bearer token、OAuth token 或等价 credential 必须以引用存储，读 API 不得返回。
- Settings 变更只影响新的未绑定 manager session；已有 HaaS-bound session 保持 binding，除非显式 rebind。

### 5.2 HaasClient 合同

本地与远程 HaaS 使用同一个 client 合同：

```python
class HaasClient:
    async def health() -> HealthResult: ...
    async def ready(scope: str = "execution") -> ReadyResult: ...
    async def status() -> StatusResult: ...
    async def capabilities() -> CapabilityResult: ...
    async def list_apps() -> list[str]: ...
    async def create_delegated_session(body: dict, idempotency_key: str) -> dict: ...
    async def restore_delegated_session(delegated_session_id: str) -> dict: ...
    async def update_delegated_policy(delegated_session_id: str, patch: dict) -> dict: ...
    async def run_sse(app_name: str, user_id: str, session_id: str, message: dict) -> AsyncIterator[dict]: ...
    async def cancel(session_id: str, invocation_id: str) -> dict: ...
    async def get_session(app_name: str, user_id: str, session_id: str) -> dict: ...
```

Client 必须只使用 HTTP/SSE 和 HaaS errors。Local mode 不得 import HaaS Python object 或直接调用 Codex adapter。

### 5.3 Sidecar Capability Discovery

HaaS 应提供 native capability endpoint，或在 `/v1/haas/status` 中包含等价字段：

```json
{
  "object": "haas_capabilities",
  "protocolVersion": "2026-08-26",
  "adkCompatible": true,
  "supportsRunSse": true,
  "supportsDelegatedSessions": true,
  "workspaceModes": ["bind_mount", "snapshot_upload", "remote_workspace"],
  "supportsLocalBindMount": true,
  "supportsModelProxy": true,
  "supportsMcpProxy": true,
  "supportsSkillMaterialization": true,
  "harnesses": [
    {
      "id": "chrn_codex_default",
      "base": "codex",
      "adapter": "codex-app-server",
      "adapterTransport": "stdio",
      "streaming": true
    }
  ]
}
```

Capability discovery 必须能在 session 创建前安全调用，且不得泄漏 secret、host absolute path、raw MCP header、provider credential 或 Codex 原生 id。

### 5.4 配置物化 API

Manager-effective 配置以 HaaS-native object 提交给 HaaS，而不是 Codex-native 文件。HaaS 随后在内部 materialize adapter-specific 文件和 loopback service。

首期可使用 `PUT /v1/haas/harnesses/{harness_id}` 更新 configured harness，并使用 `POST /v1/haas/delegated-sessions` 创建 session-specific snapshot。若新增 native route，必须以加法形式写入 HaaS Protocol。

逻辑 payload：

```json
{
  "provider": {
    "providerId": "openai",
    "model": "gpt-5.2-codex",
    "credentialRef": "secret://manager/provider/openai/default"
  },
  "mcpServers": [
    {
      "name": "github",
      "transport": "http",
      "url": "http://127.0.0.1:18081/mcp/github",
      "requiresApproval": true,
      "includeTools": ["search_issues"]
    }
  ],
  "skills": [
    {
      "name": "security-fix-pr",
      "files": [
        {"path": "SKILL.md", "contentRef": "artifact://skill/security-fix-pr/SKILL.md"}
      ],
      "fingerprint": "sha256:..."
    }
  ],
  "policy": {
    "workspaceRoots": [{"path": "/workspace", "access": "rw"}],
    "approvalPolicy": "never"
  }
}
```

### 5.5 产品交互界面

Manager GUI 必须把 HaaS backend 选择呈现为执行设置，而不是模型 provider，也不是 Codex transport 选择器。

必要设置界面：

| UI 区域 | 要求 |
|---------|------|
| Backend mode selector | 以互斥选项展示 `Local managed sidecar` 和 `Remote sidecar`。默认值为 `Local managed sidecar`。 |
| Local managed card | 展示 start/stop 状态、control readiness、execution readiness、选定端口、日志位置和安全 last-error reason。 |
| Remote sidecar card | 展示 base URL、TLS verification 状态、credential 状态、capability probe 结果和安全 last-error reason。 |
| Capability summary | 展示选定 sidecar 是否支持 `bind_mount`、`snapshot_upload`、`remote_workspace`、model proxy、MCP proxy 和 skill materialization。 |
| Current-session binding | 当打开的聊天已绑定 sidecar 时展示绑定状态，并说明全局 backend 设置变更只影响新 session。 |
| Rebind affordance | 必须要求显式用户动作和确认；必须说明 rebind 会改变该聊天未来 turn 的执行 backend/config。 |

交互规则：

- 在 Settings 中切换 backend mode 不得修改任何已有 HaaS-bound manager session。
- 用户在切换 backend mode 后打开已绑定聊天时，聊天头部必须显示绑定 mode 和 sidecar fingerprint，而不是新的全局默认值。
- 如果 remote sidecar capability probe 失败，GUI 可以保存配置，但在下一次 probe 成功前必须标记为不可用于新的 delegated turn。
- 如果 local managed sidecar autostart 失败，GUI 必须提供安全 retry 操作和日志路径；不得让 HaaS-bound chat 静默 fallback 到 manager local execution。
- Backend mode 只控制执行路径。Provider key setup、connector setup、MCP OAuth 和 skill management 仍是本地 manager 设置，其生效值通过 materialization 提交给 HaaS。

### 5.6 聊天路由与筛选策略

用户发送聊天消息时，manager 必须先应用确定性路由策略，再联系任何 harness：

```text
1. Existing HaaS binding?
   yes -> route to bound sidecar; do not reclassify.
2. HaaS backend enabled and selected?
   no -> use local manager engine.
3. Agent/persona allowed for HaaS?
   no -> use local manager engine.
4. Workspace available and authorized?
   no -> use local manager engine or request authorization.
5. Selected sidecar capability supports required workspace mode?
   no -> fail closed with safe reason or request a supported workspace transfer mode.
6. Content type supported by HaaS materialization?
   no -> use local manager engine or fail with unsupported-content reason.
7. Deterministic trigger matches?
   yes -> create/bind HaaS delegated session and route via /run_sse.
   no -> use local manager engine.
```

筛选输入：

| 输入 | 影响 |
|------|------|
| Existing binding | 最高优先级。强制复用此前绑定的 HaaS sidecar/session。 |
| Backend global mode | 只为 unbound session 选择 local managed 或 remote sidecar。 |
| Persona/agent allowlist | 防止非 code 或不支持的 persona 被路由到 HaaS。 |
| Workspace trust | 使用 project-local MCP、skills 和 bind mount 前必须满足。 |
| Workspace mode capability | Local managed 可使用 `bind_mount`；remote 可能需要 `snapshot_upload` 或 `remote_workspace`。 |
| Content shape | 首期支持 text-only；file/image/multimodal 需要显式 HaaS capability。 |
| Trigger keywords / explicit command | 确定性 trigger 选择首次 HaaS delegation。 |
| User override | 显式 “run locally”、“run with HaaS” 或 “rebind” 只覆盖 unbound session，除非用户确认。 |

聊天 UI 要求：

- Composer 或聊天头部必须显示当前 session 的生效 backend：`Local`、`HaaS local sidecar` 或 `HaaS remote sidecar`。
- 首次 HaaS-routed turn 必须展示正在创建 HaaS delegated session，以及使用的 workspace mode。
- HaaS-bound session 的 follow-up turn 必须显示 “bound to HaaS”，即使当前消息不再匹配 trigger keywords。
- 如果路由因筛选不命中而选择 local execution，不得创建 HaaS binding。
- 如果已有 binding 后路由失败，聊天展示可恢复的 HaaS error，并提供 retry/rebind/new-session 操作；不得透明地把同一个 turn 本地执行。

### 5.7 Streaming Bridge 合同

HaaS 通过 `/run_sse` 输出 ADK events。Manager 通过现有 manager WebSocket 把 session events 发送给 GUI。两者之间的桥接是产品合同：local managed 与 remote sidecar 对等价 HaaS event stream 必须产生相同的 manager WebSocket event sequence。

```text
HaaS /run_sse data: ADK Event
  -> HaasClient parses SSE frame
  -> manager stream bridge validates and normalizes
  -> manager session WebSocket broadcasts EventType payload
  -> GUI transcript renders through the same local-run components
```

必要映射：

| HaaS ADK event signal | Manager WebSocket event | GUI 行为 |
|-----------------------|-------------------------|----------|
| First accepted delegated turn | `turn_start` with `data.delegated` | 创建 live run row，并显示 HaaS binding/backend context。 |
| `content.parts[].text` delta 或 message text | `assistant_delta` | 通过现有 stream gate 追加到当前 streaming answer。 |
| Final accumulated assistant text | `assistant_message` | 持久化一条带 `data.delegated` 的 assistant message。 |
| `actions.stateDelta.status in completed/failed/cancelled/incomplete` | `turn_end` | 关闭 live stream 并显示 terminal state。 |
| HaaS approval request | `permission_required` 或 HaaS-delegated approval event variant | 显示与 local run 相同的 approval card；decision 回传 HaaS。 |
| HaaS tool/activity metadata | 可安全表达时为 `tool_started` / `tool_finished`，否则为 delegated status metadata | 在同一 transcript group 中展示 activity，不暴露 raw tool arguments。 |
| Terminal event 前的 HaaS structured error | `error` | 展示安全 HaaS error，提供 retry/rebind/new-session 操作。 |

Bridge 规则：

- Manager 必须先把每个 SSE `data:` frame 校验为 ADK event，再投影。
- SSE comment 和 heartbeat 不得创建 GUI transcript item。
- Manager 必须保留单个 invocation 内 HaaS event 顺序。
- Manager 必须通过 HaaS event id 让 GUI 层的 `assistant_delta` 幂等；reconnect replay 不得造成可见文本重复。
- Manager 必须把 streamed text 聚合成一条 final assistant message，与 local manager 行为一致，同时保留 delegated metadata 供 audit 和 UI 使用。
- Manager 不得向 GUI 暴露 HaaS 内部字段，例如 adapter id、container id、host mount path、Codex thread id、raw tool arguments 或 credential。
- 如果 `/run_sse` 断开，manager 必须视为 transport loss，而非 turn cancellation。Sidecar 支持 replay 时应通过 HaaS replay（`Last-Event-ID` 或 native event stream）恢复；否则必须读回 HaaS session 后再判断 retry 是否安全。
- Manager WebSocket 断开不得取消 HaaS invocation。GUI 重新连接后收到 manager-persisted transcript state，以及 manager 能够 reconcile 的 HaaS replay。
- 两段链路分别处理背压：慢 GUI client 不得阻塞 HaaS event consumption 到丢失 terminal state。
- Local managed 和 remote sidecar 必须使用同一个 bridge implementation 与 test vectors。URL、auth、TLS 差异不得改变 event semantics。

## 6. 数据模型

### 6.1 ManagerHaasBackendConfig

```json
{
  "object": "manager_haas_backend_config",
  "mode": "local_managed",
  "baseUrl": "http://127.0.0.1:8092",
  "auth": {"type": "bearer_ref", "ref": "secret://manager/haas/default"},
  "localManaged": {
    "autostart": true,
    "host": "127.0.0.1",
    "port": 8092,
    "portSelection": "fixed",
    "dataDir": "<manager-state>/haas",
    "logFile": "<manager-state>/logs/haas-sidecar.log"
  },
  "remote": {
    "tlsVerify": true,
    "capabilityProbe": true
  }
}
```

### 6.2 ManagerHaasSessionBinding

```json
{
  "backend": "haas",
  "mode": "local_managed",
  "baseUrlFingerprint": "sha256:...",
  "delegatedSessionId": "dgsess_abc",
  "haasSessionId": "hsess_abc",
  "haasUserId": "manager",
  "harnessId": "chrn_codex_default",
  "configSnapshot": {
    "providerFingerprint": "sha256:...",
    "mcpVersion": "sha256:...",
    "skillsVersion": "sha256:...",
    "policyVersion": "sha256:...",
    "workspaceMode": "bind_mount"
  },
  "createdAtMs": 1786400000000,
  "updatedAtMs": 1786401000000
}
```

Binding 规则：

- 带 HaaS binding 的 manager session 必须继续通过已绑定 sidecar，除非用户显式新建 session 或执行可审计 rebind。
- 切换全局 backend 设置只影响新 unbound session。
- Binding 存储 fingerprint 和 id，不存 bearer token、provider key、MCP header、raw skill content 或 Codex 原生 id。

### 6.3 LocalManagedSidecarStatus

```json
{
  "mode": "local_managed",
  "status": "running",
  "pid": 12345,
  "baseUrl": "http://127.0.0.1:8092",
  "controlReady": true,
  "executionReady": true,
  "lastErrorSafeReason": null,
  "managed": true
}
```

Status values：`disabled`、`starting`、`running`、`degraded`、`stopped`、`blocked`、`failed`。

### 6.4 ChatRoutingDecision

```json
{
  "object": "manager_haas_routing_decision",
  "sessionId": "mgr_sess_123",
  "decision": "haas",
  "reason": "trigger_keyword",
  "backendMode": "local_managed",
  "sidecarFingerprint": "sha256:...",
  "workspaceMode": "bind_mount",
  "bindingState": "new",
  "safeMessage": "This turn will run through the local HaaS sidecar."
}
```

Decision values：`local`、`haas`、`request_authorization`、`unsupported`、`blocked`。

Reason values 包括：`existing_binding`、`disabled`、`agent_not_allowed`、
`workspace_not_trusted`、`capability_missing`、`unsupported_content`、
`trigger_keyword`、`explicit_user_choice` 和 `rebind_required`。

Routing decision 是 manager 内部记录，可投影为 GUI 状态文本。它不得包含 raw prompt content、provider credential、MCP header 或完整 tool argument。

### 6.5 ManagerStreamProjection

```json
{
  "object": "manager_stream_projection",
  "source": "haas",
  "haasEventId": "evt_0000000001042",
  "invocationId": "inv_abc",
  "managerEventType": "assistant_delta",
  "sequence": 7,
  "dedupeKey": "haas:evt_0000000001042",
  "delegated": {
    "backend": "haas",
    "mode": "remote",
    "delegatedSessionId": "dgsess_abc"
  },
  "safePayload": {
    "text": "partial text"
  }
}
```

`ManagerStreamProjection` 不一定作为独立对象持久化，但 bridge 必须表现得像存在这个 normalized record：每个 GUI 可见 event 都有 source HaaS event id 或 manager-generated synthetic id、稳定 dedupe key、delegated metadata 和安全 payload。

## 7. 运行模型与状态机

### 7.1 Backend 选择

```text
manager starts
  -> load backend config
  -> if mode=local_managed and autostart=true: ensure local sidecar
  -> probe configured HaaS endpoint
  -> expose status to GUI
```

### 7.2 Local Managed Sidecar 生命周期

```text
disabled
  -> starting
  -> running
  -> degraded
  -> restarting
  -> running

running
  -> stopped
  -> failed
```

规则：

- Local managed sidecar 是 desktop manager 管理的进程，不是替代协议实现。
- `/v1/haas/health` 证明进程存活；`/v1/haas/ready?scope=execution` 证明选定 harness 可执行。
- 如果 sidecar process 存活但 execution readiness 失败，GUI 必须显示 degraded execution，同时保持本地设置可访问。
- Sidecar 日志写入 manager state 目录，并必须脱敏。

### 7.3 Session 执行

```text
manager session unbound
  -> deterministic HaaS decision
  -> create delegated session through HaaS
  -> persist ManagerHaasSessionBinding
  -> restore delegated runtime through HaaS
  -> run turn via /run_sse

manager session bound
  -> restore delegated runtime through same HaaS binding
  -> run follow-up via /run_sse
```

### 7.4 Local vs Remote Workspace Modes

Local managed sidecar 在用户显式授权后可以支持 host bind mount：

```text
host project path -> /workspace rw
extra authorized paths -> /mnt/extra/* ro
```

Remote sidecar 不得假设能访问本机 host path。它必须声明支持的 workspace mode：

- `bind_mount`：sidecar 与 manager 在同一 host，可 bind mount manager 授权路径。
- `snapshot_upload`：manager 通过 HaaS artifact API 上传 archive/snapshot。
- `remote_workspace`：manager 引用已有 remote workspace id 或 git ref。

如果选定 sidecar 不支持当前项目所需的 workspace mode，manager 必须在创建 delegated session 前 fail closed。

### 7.5 Streaming 状态机

```text
idle
  -> ws_turn_start_sent
  -> haas_sse_opening
  -> haas_sse_streaming
  -> terminal_seen
  -> manager_turn_done_sent

haas_sse_streaming
  -> haas_sse_disconnected
  -> replay_reconcile
  -> haas_sse_streaming

haas_sse_streaming
  -> gui_ws_disconnected
  -> continue_consuming_haas
  -> gui_ws_reconnected
  -> replay_manager_state
```

规则：

- 每个 manager turn 只发送一次 `turn_start`，并且在第一个可见 HaaS output 前发送，即使 HaaS replay 已包含早前 events。
- HaaS terminal status 或安全不可恢复 bridge error 后，只发送一次 `turn_done`。
- 如果 terminal HaaS event 之后观察到 transient transport error，以 terminal event 为准。
- 如果 manager 在同一个 invocation 中收到 HaaS terminal failure 和 text deltas，GUI 展示已聚合文本和 terminal failure state。

## 8. 安全与权限

- 除 health/ready probe 外，manager 到 HaaS 的请求必须使用 bearer auth。
- Local managed sidecar token 必须本地生成，保存在 manager secret store 或用户私有 token file，settings read API 不得返回。
- Remote sidecar token 必须存入 manager secret store。
- Manager 不得在 HaaS binding 中持久化 token、provider key、MCP header、cookie、raw prompt 或完整 tool argument。
- Local managed sidecar autostart 只允许 loopback URL。
- Remote sidecar 默认必须使用 HTTPS；关闭 TLS verification 需要显式 insecure-development 设置，并必须在 GUI 中可见。
- MCP 和 skills 以 HaaS materialization intent 提交；HaaS 拥有 proxying、secret resolution 与 adapter-specific config rendering。
- Manager 不得让 Codex 直连 provider endpoint 或 MCP server，必须通过 HaaS。
- Sidecar capability response 是不可信输入，manager 使用前必须 schema validate。
- HaaS-bound session 在 sidecar 失败后不得静默 fallback 到 manager local execution。

## 9. 可观测性

Manager 记录安全事件/log：

- `manager.haas.backend_selected`
- `manager.haas.local_sidecar_starting`
- `manager.haas.local_sidecar_ready`
- `manager.haas.local_sidecar_degraded`
- `manager.haas.remote_probe_failed`
- `manager.haas.binding_created`
- `manager.haas.binding_reused`
- `manager.haas.materialization_requested`

HaaS 记录既有 sidecar、session、event、policy、MCP、skill、model proxy 和 adapter events。可用时，trace id 应通过 `X-HaaS-Trace-ID` 从 manager 传递到 HaaS。

GUI 状态面：

- 当前 backend mode（`local_managed` 或 `remote`）。
- 已脱敏的 sidecar URL。
- Control readiness。
- Execution readiness。
- Capability summary。
- 当前 session binding，包括 delegated session id 与 workspace mode。
- MCP/skill materialization version 或安全 degraded reason。

## 10. 失败与恢复

| 场景 | 行为 |
|------|------|
| Local sidecar process 无法启动 | Manager 报告 `local_sidecar_start_failed`；HaaS-bound turn fail closed。 |
| Local sidecar health ok 但 execution not ready | GUI 显示 degraded execution；新的 HaaS-bound turn 根据 HaaS readiness policy 失败或排队。 |
| Remote sidecar 不可达 | Manager 报告安全 remote probe failure；已有 HaaS-bound session 不 fallback 到本地。 |
| Remote capability 不支持 local bind mount | Manager 在 session creation 前拒绝本地项目 delegation，除非配置 snapshot/remote workspace mode。 |
| HaaS token 缺失或非法 | HaaS 返回 401；manager 提示更新 sidecar credential，不暴露 token。 |
| Materialization validation 失败 | HaaS 返回结构化错误；manager 记录 safe reason 且不启动 turn。 |
| HaaS `/run_sse` 中途断开 | Manager 在可能时 reconnect/replay；否则读回 HaaS session 并展示安全 recoverability state。 |
| GUI WebSocket 中途断开 | Manager 继续消费 HaaS SSE 并持久化 transcript state；GUI 重连后收到当前状态且不重复 delta。 |
| HaaS replay event 重复 | Manager 通过 HaaS event id / dedupe key 去重，再发出 GUI-visible delta。 |
| HaaS 输出 malformed SSE data | Manager 关闭 bridge，发出安全 `error`，且不得把 turn 标记为成功。 |
| Optional MCP source 不可用 | 如果 MCP source 是 optional，HaaS 可降级并记录 degraded capability event。 |
| Required MCP source 不可用 | HaaS 以 `haas_mcp_unavailable` 失败 session 或 turn preparation。 |
| Skill materialization partial write | HaaS 回滚 partial materialization 并 fail closed。 |
| 用户在 session 中途切换 backend settings | 已绑定 session 继续使用原 binding；新 session 使用新 backend。 |
| 用户请求 rebind | Manager 创建显式 audit event 和新的 HaaS binding；不得静默迁移。 |

## 11. 测试计划与验收

- Unit：解析并校验 manager HaaS backend config，包括 local/remote mode、token ref、loopback autostart 规则、TLS verification 和 endpoint fingerprint。
- Unit：`HaasClient` 对 local 与 remote base URL 使用完全相同的 request/response 处理。
- Unit：local managed supervisor 不在 config 或 process env 中生成 plaintext secret，并报告安全状态值。
- Unit：sidecar capability discovery 经过 schema validation，并拒绝未知的不安全 workspace mode。
- Unit：chat routing policy 按 existing binding、backend mode、persona allowlist、workspace trust、sidecar capabilities、content shape、trigger keywords 和 explicit user override 的顺序决策。
- Unit：streaming bridge 将 ADK text、terminal、approval 和 error events 映射为带稳定 dedupe key 的 manager WebSocket events。
- Unit：SSE heartbeat/comment frames 不创建 GUI transcript item。
- Integration：local managed sidecar 启动并通过 `/v1/haas/health`、`/v1/haas/ready?scope=control` 和 `/v1/haas/ready?scope=execution`。
- Integration：remote fake sidecar 与 local managed sidecar 都通过同一 manager backend contract tests。
- Integration：首次 delegated turn 创建 HaaS binding；后续 turn 即使 backend settings 改变也复用该 binding。
- Integration：manager-effective MCP config 提交到 HaaS，并通过 HaaS MCP runtime materialize，不直接写给 Codex。
- Integration：manager-effective skills 通过 HaaS skill materialization snapshot，包括 bundled resources。
- Integration：provider config 通过 HaaS model proxy 路由；raw provider key 不出现在 Codex、sidecar logs、manager logs、events 或 bindings。
- Integration：local managed 与 remote fake sidecar replay 同一组 canned HaaS SSE stream，经 normalization 后产出字节等价的 manager WebSocket event sequence。
- E2E：GUI 可在 local managed 与 remote sidecar mode 间切换；两者都使用相同 HaaS `/run_sse` 路径。
- E2E：查看 HaaS-bound chat 时切换 backend mode，会保留已有 binding，并提示新设置只影响新 session。
- E2E：first-turn filter miss 本地执行且不创建 HaaS binding；first-turn filter hit 创建 HaaS binding；follow-up turn 无论 trigger keyword 是否匹配都复用 binding。
- E2E：不支持 `bind_mount` 的 remote sidecar 对本地项目 delegation 返回安全原因。
- Security：secret scan、log redaction assertions、persisted bindings 不含 raw Authorization/provider/MCP values。
- UI：HaaS streamed text 使用与 local manager execution 相同的 transcript streaming component 和 stream gate。
- Compatibility：ADK `/run` 与 `/run_sse` parity 不变；不引入 `/v1/codex-worker/*` shim。
