# Model Proxy 组件规格

[English](README.md) | **简体中文**

Status: Draft
Last reviewed: 2026-09-12
Related specs: [Security Boundary](../security-boundary/README.zh-CN.md), [Harness Adapter](../harness-adapter/README.zh-CN.md), [Manager Delegation](../manager-delegation/README.zh-CN.md), [Observability](../observability/README.zh-CN.md)

## 1. 组件定位

Model Proxy 是 HaaS 的模型访问边界。它让 harness 使用 OpenAI-compatible 或 vendor-native endpoint 时不直接接触真实 provider credential，并在必要时做请求/响应形状兼容、SSE relay、usage normalization 和安全观测。

### 1.1 内置本地运行时

HaaS lifespan 管理仅 loopback 的 proxy listener，退出时关闭上游连接。每个 invocation 冻结已应用 provider route，签发仅限其 harness、session、invocation 和精确 model 的短期 model_proxy token；所有终态/取消/失败路径均撤销 token 并删除 route。未知 invocation route 不回退可变 registry 配置。凭证解析还检查 Manager grant 的精确 model/URL，禁止重定向和继承环境代理。

Responses 支持 JSON 和增量 SSE，有界上游读取超时且取消时清理连接。错误不透传原始 provider body、credential 或原始异常文本。上游拒绝请求时，proxy 仅可从结构化 `error.code`、`error.type`、`error.param`、`error.message` 字段组装经过脱敏、限长的诊断摘要；未知字段与非 JSON 正文必须丢弃。JSON 请求和 SSE 建连失败使用同一规则；规范化 SSE error frame，但不丢合法 text/tool frame。此 Codex 路径明确不支持 Chat Completions，不能静默替换。Control ready 独立；execution ready 除 Codex 外要求已配置 profile 和可用 resolver/proxy。每次提交校验实际选择的 profile，不能仅信全局 ready。

仅对显式选择的 `volcengine-ark` provider，出站 Responses 历史输入中的 reasoning 项和 assistant message 在缺失 `status` 时补 `completed`，满足方舟标准接口要求。出站顶层 `reasoning.summary` 选项会被移除，因为方舟拒绝这个 OpenAI 专有字段；其他 reasoning 选项保持不变。已有状态、内容、ID 和工具 payload 不变，且不修改调用方原始输入。其他 provider ID 不变，不按 URL 或展示名称启用该兼容规则。JSON 与 SSE 请求共用转换；离线反向断言与真实多轮 provider smoke 验证此规则。

## 2. 来源与依据

| 来源 | 采用内容 |
|------|----------|
| `mpa-codex-worker` model proxy / secretless runtime | provider key 不进 harness、短期 proxy token、stream idle timeout、usage normalization |
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
- 验证短期 runtime token 的 session、audience、expiry 和 revocation。
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
  "invocationId": "inv_abc",
  "harnessId": "chrn_codex_default",
  "allowedModels": ["gpt-5.6-terra"],
  "expiresAtMs": 1786400000000
}
```

### 6.3 Usage

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
runtime token issued
  -> harness sends model request to loopback proxy
  -> token validated
  -> route resolved
  -> request transformed if required
  -> upstream called
  -> streaming bytes relayed or JSON returned
  -> response transformed if required
  -> usage emitted
```

Capability 生命周期必须覆盖完整 accepted invocation，包括 tool 之后的模型调用和有界的人工响应等待。初始 expiry 必须晚于 invocation deadline 加有界 cleanup margin，或 runtime 必须在到期前刷新。Blocking interaction 带显式 `expiresAtMs`，且不得晚于 invocation deadline；过期生成 typed terminal，不能无限等待。刷新保持相同 audience、harness、session、invocation、精确 model 与精确 URL，不扩大 scope，也不暴露上游 credential。Active capability 被拒绝时最多刷新一次。旧 token 在 replacement 应用到 native thread 前保持有效，应用后再撤销。权威 terminal、取消或 runtime shutdown 时撤销全部对应 token 和 route。

Provider compatibility:

| Provider shape | Behavior |
|----------------|----------|
| OpenAI Responses | Preserve Responses request/stream where supported |
| Chat Completions | Transform only when adapter declares compatibility and tests cover it |
| Anthropic Messages | Use provider-specific bridge only behind proxy |
| OpenAI-compatible aggregator | Require route config to declare `wireApi`; do not infer solely from host |

## 8. 安全与权限

- Real provider key is read only by Model Proxy or secret resolver.
- Harness sees only loopback base URL and short TTL token.
- Delegated HaaS container 不得在 env、config、startup command、mount、event、log
  或 artifact metadata 中看到真实 provider key。
- Caller-provided provider base URL is accepted only through registry allowlist.
- Request/response logging redacts Authorization, API keys, cookies, raw messages and tool args.
- Proxy token must be session scoped, audience restricted and revocable.
- Proxy must not expose arbitrary URL forwarding.

## 9. 可观测性

Metrics:

- `haas_model_proxy_request_total{provider,wireApi,status}`
- `haas_model_proxy_request_duration_ms{provider,wireApi,status}`
- `haas_model_proxy_stream_idle_timeout_total{provider,wireApi}`
- `haas_model_proxy_token_refresh_total{status}`
- `haas_model_proxy_usage_tokens{kind,provider,model}`

Logs:

- `haas.model_proxy.request_started`
- `haas.model_proxy.request_completed`
- `haas.model_proxy.request_failed`
- `haas.model_proxy.token_refreshed`
- `haas.model_proxy.transform_applied`

Log fields must use safe route ids, fingerprints and status codes, never prompt bodies or credentials.

## 10. 失败与恢复

| 场景 | 行为 |
|------|------|
| Active invocation 中 runtime token missing/invalid | 仅在相同 frozen scope 仍 active 时刷新一次；否则以 `model_proxy_token_invalid` 失败并保留阶段性进展 |
| Active invocation 中 runtime token expired | 重试模型请求前刷新一次；刷新失败发出 `model_proxy_token_expired`，不得退化为泛化 `incomplete` |
| delegated session credentialRef 缺失或未授权 | fail closed，返回 `invalid_credential` 或 `haas_provider_source_invalid`；不得要求 harness 提供 key |
| provider unreachable | invocation 接受前返回 `502 haas_provider_error`；接受后持久化 `haas.turn.failed` 且 `haas.code=haas_provider_error`，保留 partial events，并保持 `/run`/`/run_sse` HTTP 200 |
| stream idle timeout | 按 provider policy retry；接受后耗尽时持久化带稳定 timeout code/reason 的 `haas.turn.failed` 或 `haas.turn.incomplete`，HTTP 保持 200 |
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
- Lifecycle：真实或受控时钟任务跨过配置的 stream-idle 区间并完成至少一轮 tool round-trip，token 仍有效；refresh 保持精确 scope；stale、跨 session 和 terminal 后 token 均失败。
- Observability：token issue/refresh/revoke 只记录安全 token fingerprint、invocation id、age 与 reason；不得出现原始 token 或 provider key。
