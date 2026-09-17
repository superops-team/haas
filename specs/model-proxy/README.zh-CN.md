# Model Proxy 组件规格

[English](README.md) | **简体中文**

Status: Draft
Last reviewed: 2026-09-15
Change ID: long-task-model-proxy-stability
Related specs: [Security Boundary](../security-boundary/README.zh-CN.md), [Harness Adapter](../harness-adapter/README.zh-CN.md), [Manager Delegation](../manager-delegation/README.zh-CN.md), [Observability](../observability/README.zh-CN.md)

## 1. 组件定位

Model Proxy 是 HaaS 的模型访问边界。它让 harness 使用 OpenAI-compatible 或 vendor-native endpoint 时不直接接触真实 provider credential，并在必要时做请求/响应形状兼容、SSE relay、usage normalization 和安全观测。

### 1.1 内置本地运行时

HaaS lifespan 管理仅 loopback 的 proxy listener，退出时关闭上游连接。Model Proxy
capability 按 session scope 和 generation 管理：Codex 只能看到一个 `model_proxy`
bearer，该 bearer 只标识已授权的 HaaS session、harness、provider 协议族、base URL
和 credential scope。它不得绑定到单个 invocation id、profile version、model id 或
model-call sequence。每个 invocation 仍冻结实际使用的 provider route，并记录所用
generation；但 terminal/cancel/fail 不得在逻辑 session 仍可继续时撤销 session
capability。未知 session route 不回退到可变 registry 配置。凭证解析还检查 Manager
grant 的精确 provider scope 与 URL，禁止重定向和继承环境代理。

Responses 支持 JSON 和增量 SSE，并对上游 connect、response-header 与 read 使用有界
timeout，取消时清理连接。错误不透传原始 provider body、credential 或原始异常文本。
上游拒绝请求时，proxy 仅可从结构化 `error.code`、`error.type`、`error.param`、
`error.message` 字段组装经过脱敏、限长的诊断摘要；未知字段与非 JSON 正文必须丢弃。
JSON 请求和 SSE 建连失败使用同一规则；规范化 SSE error frame，但不丢合法 text/tool
frame。此 Codex 路径明确不支持 Chat Completions，不能静默替换。Control ready 独立；
execution ready 除 Codex 外要求已配置 profile 和可用 resolver/proxy。每次提交校验实际
选择的 profile，不能仅信全局 ready。

仅对显式选择的 `volcengine-ark` provider，出站 Responses 历史输入中的 reasoning 项和 assistant message 在缺失 `status` 时补 `completed`，满足方舟标准接口要求。出站顶层 `reasoning.summary` 选项会被移除，因为方舟拒绝这个 OpenAI 专有字段；其他 reasoning 选项保持不变。Codex namespace tool（`{"type":"namespace","name":"mcp__...","tools":[...]}`）在上游调用前展平成普通 Responses `function` tool，名称使用 `<namespace>__<tool>`，同一 bridge 中的历史 input/output `function_call` 再恢复为 namespace/name 形态。这样保留 Codex MCP 语义，同时避免 provider 侧 `unknown tool type: namespace` 失败。已有状态、内容、ID 和非 namespace tool payload 不变，且不修改调用方原始输入。其他 provider ID 不变，不按 URL 或展示名称启用该兼容规则。JSON 与 SSE 请求共用转换；离线反向断言与真实多轮 provider smoke 验证此规则。

## 2. 来源与依据

| 来源 | 采用内容 |
|------|----------|
| `mpa-codex-worker` model proxy / secretless runtime | provider key 不进 harness、session-scoped proxy token、动态 credential generation 与 grace fallback、stream idle timeout、retry budget、usage normalization |
| Effective Harness Profile | 已应用 frozen provider route 是 per-invocation ModelRoute 来源 |
| Security Boundary / Sandbox Runtime | provider credential 走 credential vault，不放入 agent sandbox；caller URL allowlist |
| 本组件总览 | Secretless runtime |

## 3. 上游与下游关系

| 方向 | 对象 | 关系 |
|------|------|------|
| 上游 | Harness Adapter | adapter 把 harness model endpoint 指向 loopback proxy |
| 上游 | Session Runtime | 请求 runtime token 和 usage 汇总 |
| 上游 | Session Runtime / Effective Harness Profile | 提供已应用 frozen provider route |
| 下游 | Security Boundary / Credential Vault | 解析 provider credential ref（`resolve_secret` 唯一入口） |
| 下游 | Provider clients | OpenAI Responses、Chat Completions、Anthropic Messages、Azure OpenAI、OpenAI-compatible aggregators |
| 下游 | Observability | 记录请求状态、时延、usage、安全摘要 |

## 4. 职责边界

负责：

- 接收 harness 发往模型 provider 的请求。
- 验证 session-scoped runtime token 的 session、audience、generation、expiry 和 revocation。
- 根据 configured harness 和 request model 解析 provider endpoint。
- 对 manager-delegated session，只解析 delegated-session contract 中存在且被
  frozen policy snapshot 允许的 manager-supplied `credentialRef`。
- 注入真实 provider credential 到 outbound request。
- 对 provider URL 做 allowlist 和 SSRF 防护。
- 转发 streaming response，不缓冲到完成。
- 对 provider response 和 error 做安全规范化。
- 汇总 token usage，并统一 fresh input、cache read、cache write、output 语义。
- 支持 adapter-specific compatibility transform，例如 namespace tool flatten/unflatten。

不负责：

- 不选择 harness。
- 不决定用户是否有权使用模型；只执行已冻结 policy。
- 不保存完整 prompt 或 completion。
- 不把 provider-specific schema 暴露给上游 ADK client。
- 不在 provider 不支持工具时静默丢工具；必须失败或记录显式降级。

## 5. 核心接口

### 5.1 Harness-Facing Endpoints

| Method | Path | 用途 |
|--------|------|------|
| POST | `/v1/responses` | OpenAI-compatible Responses proxy |
| POST | `/v1/chat/completions` | Chat Completions compatible proxy |
| GET | `/v1/models` | 当前 token 可用模型摘要 |
| GET | `/health` | loopback proxy liveness |
| GET | `/ready` | provider routing 和 secret resolver readiness |

这些 endpoint 默认只监听 loopback，例如 `127.0.0.1:18080`。

### 5.2 Internal API

```python
async def resolve_model_proxy_token(scope: ModelProxyScope) -> RuntimeToken: ...  # 委托 Security Boundary issue_runtime_token(audience="model_proxy")
async def resolve_model_route(session_id: str, model: str) -> ModelRoute: ...
async def register_model_proxy_route(session_id: str, route: ModelRoute, credential_ref: str) -> ModelProxyCapability: ...
async def refresh_model_proxy_capability(session_id: str, token_generation: int, reason: str) -> ModelProxyCapability: ...
async def proxy_openai_responses(request: ProxyRequest) -> ProxyResponse: ...
async def proxy_chat_completions(request: ProxyRequest) -> ProxyResponse: ...
async def normalize_usage(provider: str, body: object) -> Usage: ...
async def transform_tools(provider: str, request: object) -> ProviderRequestTransform: ...
```

## 6. 数据模型

### 6.1 ModelRoute

`ModelRoute` 只从 invocation 已应用的 `EffectiveHarnessProfile` 解析，先做授权管控覆盖和 hard policy 校验；不能用 live Registry 投影作为运行中 session 的路由来源。它与 Harness Profile 共用同一个 `ProviderRoute` 合同：源配置同时保留 `providerId`（稳定身份）与 `name`（别名）。`wireApi` 声明协议：`openai-compatible` 是 OpenAI 兼容协议族，`responses` 要求使用该族里的 OpenAI Responses 协议类型，`agent-plan` 是 Ark Agent Plan 协议。

```json
{
  "provider": "openai-compatible",
  "providerId": "openai",
  "baseUrl": "https://provider.example.com/v1",
  "model": "gpt-5.6-terra",
  "wireApi": "responses",
  "credentialRef": "secret://tenant/workspace/provider/default",
  "credentialFingerprint": "sha256:abc",
  "allowlistRuleId": "allow_provider_default",
  "timeoutMs": 300000,
  "streamIdleTimeoutMs": 300000
}
```

### 6.2 RuntimeTokenScope

```json
{
  "audience": "model_proxy",
  "sessionId": "s_123",
  "harnessId": "chrn_codex_default",
  "providerScopeKey": "sha256:provider-base-wire-token-field",
  "expiresAtMs": 1786400000000
}
```

`RuntimeTokenScope` 刻意按 session 定界。Token 还可以携带非 secret 的
`generation` 和 `issuedAtMs`，但不得携带 provider credential、profile version、
作为硬鉴权边界的 model id、invocation id、host path 或带 query 的原始 URL。模型授权
在请求时通过当前 session capability 与 route registry 执行。

### 6.3 ModelProxyCapability

```json
{
  "sessionId": "s_123",
  "harnessId": "chrn_codex_default",
  "providerScopeKey": "sha256:provider-base-wire-token-field",
  "activeGeneration": 3,
  "activeTokenFingerprint": "sha256:token",
  "route": {
    "providerId": "volcengine-ark",
    "baseUrl": "https://provider.example.com/v1",
    "wireApi": "responses",
    "apiType": "responses"
  },
  "allowedModels": ["gpt-5.6-terra", "gpt-5.6-sol"],
  "credentialGeneration": 7,
  "status": "active",
  "graceGenerations": [
    {
      "generation": 2,
      "tokenFingerprint": "sha256:previous-token",
      "expiresAtMs": 1786400300000,
      "reason": "supervisor_restart"
    }
  ],
  "lastUsedAtMs": 1786400000000,
  "expiresAtMs": 1786486400000
}
```

Capability 默认保存在进程内存中，也可以由 Manager credential channel 与 session
frozen profile 重建。持久 store 只保存 fingerprint、generation counter 与 route
摘要；不得持久化原始 model proxy bearer token 或 provider credential。

### 6.4 Usage

```json
{
  "inputTokens": 1000,
  "outputTokens": 200,
  "totalTokens": 1200,
  "cacheReadTokens": 100,
  "cacheWriteTokens": 50
}
```

If usage is unknown, return `null`; never fabricate zero.

## 7. 运行模型与状态机

```text
session capability registered or refreshed
  -> harness sends model request to loopback proxy
  -> token validated to session/generation/provider scope
  -> route resolved from session capability and frozen profile
  -> request transformed if required
  -> upstream called with bounded retry policy
  -> streaming bytes relayed or JSON returned
  -> response transformed if required
  -> usage emitted
```

Capability 生命周期覆盖整个可继续接收工作的逻辑 session。它必须长于任意 accepted
invocation deadline，包括 24 小时长任务默认值，并且必须跨 tool call、model call、
GUI 重连、以及保持同一 owned credential channel 的 Manager/HaaS 热重启继续有效。
Blocking interaction 带显式 `expiresAtMs`，且不得晚于 invocation deadline；capability
刷新必须让 pending turn 在该 deadline 或权威 terminal 前保持可恢复。

刷新保持相同 audience、harness、session、provider scope 与 base URL，不扩大 scope，也不
暴露上游 credential。只有 session applied profile 或显式 profile rebind 授权时，refresh
才可增删 allowed model。Previous generation 在 replacement 应用到 native thread 前、
短 grace TTL 到期前、且未跨安全边界时保持有效。上游认证失败若尚未输出任何 byte，可使用
最近 grace credential 重试一次；已输出 byte 后的失败是 terminal stream failure，不做隐藏重试。

Codex app-server 在 HaaS/Manager 重连后可能继续在 native thread 中持有旧 loopback base URL
和 bearer token。Model Proxy 在向 harness 报告 `model_proxy_token_invalid` 前，必须先尝试
一次 same-session 恢复：解析或查找 token 的 session/generation fingerprint，确认当前
Manager-owned credential channel 有效，确认请求 model 被当前 session capability 允许，并把
route 重新绑定到当前 loopback listener。全部检查通过后，用刷新后的 capability 重试该请求一次；
失败时返回稳定 token error 并保留 partial progress。该恢复不得允许跨 session 复用、未经
profile 授权的 provider URL 变化，或访问 grace window 外的已退休 credential。

Provider compatibility:

| Provider shape | Behavior |
|----------------|----------|
| OpenAI Responses | Preserve Responses request/stream where supported |
| Chat Completions | Transform only when adapter declares compatibility and tests cover it |
| Anthropic Messages | Use provider-specific bridge only behind proxy |
| OpenAI-compatible aggregator | Require route config to declare `wireApi`; do not infer solely from host |

## 8. 安全与权限

- Real provider key is read only by Model Proxy or secret resolver.
- Harness 只能看到 loopback base URL 和 session-scoped bearer token。
- Delegated HaaS container 不得在 env、config、startup command、mount、event、log
  或 artifact metadata 中看到真实 provider key。
- Caller-provided provider base URL is accepted only through registry allowlist.
- Request/response logging redacts Authorization, API keys, cookies, raw messages and tool args.
- Proxy token 必须是 session-scoped、audience-restricted、带 generation 且可撤销。
- Proxy must not expose arbitrary URL forwarding.

## 9. 可观测性

Metrics:

- `haas_model_proxy_request_total{provider,wireApi,status}`
- `haas_model_proxy_request_duration_ms{provider,wireApi,status}`
- `haas_model_proxy_stream_idle_timeout_total{provider,wireApi}`
- `haas_model_proxy_token_refresh_total{status}`
- `haas_model_proxy_capability_rebind_total{status,reason}`
- `haas_model_proxy_retry_total{provider,wireApi,status,safeReason}`
- `haas_model_proxy_usage_tokens{kind,provider,model}`

Logs:

- `haas.model_proxy.request_started`
- `haas.model_proxy.request_completed`
- `haas.model_proxy.request_failed`
- `haas.model_proxy.token_refreshed`
- `haas.model_proxy.capability_rebound`
- `haas.model_proxy.retry_attempted`
- `haas.model_proxy.transform_applied`

Log fields must use safe route ids, fingerprints and status codes, never prompt bodies or credentials.

## 10. 失败与恢复

| 场景 | 行为 |
|------|------|
| active 或可恢复 session 中 runtime token missing/invalid | 仅在同一 session、harness、provider scope 与 active credential channel 均被证明时 rebind/refresh 一次；否则以 `model_proxy_token_invalid` 失败并保留阶段性进展 |
| active 或可恢复 session 中 runtime token expired | 重试模型请求前 refresh；刷新失败发出 `model_proxy_token_expired`，不得退化为泛化 `incomplete` |
| delegated session credentialRef 缺失或未授权 | fail closed，返回 `invalid_credential` 或 `haas_provider_source_invalid`；不得要求 harness 提供 key |
| provider unreachable | invocation 接受前返回 `502 haas_provider_error`；接受后持久化 `haas.turn.failed` 且 `haas.code=haas_provider_error`，保留 partial events，并保持 `/run`/`/run_sse` HTTP 200 |
| upstream 429/502/503/504 或 connect/read timeout，且尚未输出 byte | 在 invocation deadline 允许范围内执行带 jitter 的有界指数退避重试；默认最多 2 次，总 sleep 有上限 |
| stream idle timeout | 输出前按 provider policy retry；接受后或已输出 byte 后耗尽时，持久化带稳定 timeout code/reason 的 `haas.turn.failed` 或 `haas.turn.incomplete`，HTTP 保持 200 |
| Manager/HaaS 重启后 loopback listener 端口变化 | 通过 owned credential channel 重建 session capability，并在下一次模型请求前 rebind native profile；不得附着到不归当前 Manager 拥有的 listener |
| unsupported tool schema | fail with safe `haas_tool_schema_unsupported`, do not drop tool silently |
| usage missing | return usage `null` and log safe diagnostic |
| transform fails | fail closed before upstream call if semantics are uncertain |

## 11. 测试计划与验收

- Unit：token validation、route resolution、URL allowlist、usage normalization、tool transform。
- Integration：loopback proxy receives harness request and injects provider credential only outbound。
- Integration：delegated Codex container 只使用 loopback model proxy，无法观察 manager/provider raw key。
- Streaming：SSE/chunked upstream relay is progressive and handles idle timeout。
- Security：provider key never appears in harness env/config/log/event/artifact/report。
- Negative：unsupported provider, missing key, expired token, disallowed URL and malformed upstream response；测试区分 pre-acceptance HTTP error 与 accepted HTTP-200 terminal failure，并保留 partial output。
- Lifecycle：真实或受控时钟任务跨过配置的 stream-idle 区间、过去的 900 秒边界，并完成至少一轮 tool round-trip，token 仍有效；refresh/rebind 保持精确 session/provider scope；stale、跨 session 和 session 撤销/删除后的 token 均失败。
- Concurrency：两个 session 并发使用同一 provider 时，各自保持独立 capability generation 与 route；一个 session 的 model/key/profile 变化不得使另一个 session 的 loopback token 失效或被接管。
- Observability：token issue/refresh/revoke 只记录安全 token fingerprint、session id、generation、age 与 reason；不得出现原始 token 或 provider key。
