# Model Proxy 组件规格

Status: Draft
Last reviewed: 2026-08-26
Related specs: [Security Boundary](../security-boundary/README.md), [Harness Adapter](../harness-adapter/README.md), [Observability](../observability/README.md)

## 1. 组件定位

Model Proxy 是 HaaS 的模型访问边界。它让 harness 使用 OpenAI-compatible 或 vendor-native endpoint 时不直接接触真实 provider credential，并在必要时做请求/响应形状兼容、SSE relay、usage normalization 和安全观测。

## 2. 来源与依据

| 来源 | 采用内容 |
|------|----------|
| `mpa-codex-worker` model proxy / secretless runtime | provider key 不进 harness、短期 proxy token、stream idle timeout、usage normalization |
| Harness Registry | `harness.provider` 配置是 ModelRoute 的来源（baseUrl/wireApi/credentialRef） |
| Security Boundary / Sandbox Runtime | provider credential 走 credential vault，不放入 agent sandbox；caller URL allowlist |
| 本组件总览 | Secretless runtime |

## 3. 上游与下游关系

| 方向 | 对象 | 关系 |
|------|------|------|
| 上游 | Harness Adapter | adapter 把 harness model endpoint 指向 loopback proxy |
| 上游 | Session Runtime | 请求 runtime token 和 usage 汇总 |
| 上游 | Harness Registry | 提供 provider 路由配置（`harness.provider`） |
| 下游 | Secret Store / Credential Vault | 解析 provider credential ref |
| 下游 | Provider clients | OpenAI Responses、Chat Completions、Anthropic Messages、Azure OpenAI、OpenAI-compatible aggregators |
| 下游 | Observability | 记录请求状态、时延、usage、安全摘要 |

## 4. 职责边界

负责：

- 接收 harness 发往模型 provider 的请求。
- 验证短期 runtime token 的 session、audience、expiry 和 revocation。
- 根据 configured harness 和 request model 解析 provider endpoint。
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
async def issue_model_proxy_token(scope: ModelProxyScope) -> RuntimeToken: ...
async def resolve_model_route(session_id: str, model: str) -> ModelRoute: ...
async def proxy_openai_responses(request: ProxyRequest) -> ProxyResponse: ...
async def proxy_chat_completions(request: ProxyRequest) -> ProxyResponse: ...
async def normalize_usage(provider: str, body: object) -> Usage: ...
async def transform_tools(provider: str, request: object) -> ProviderRequestTransform: ...
```

## 6. 数据模型

### 6.1 ModelRoute

`ModelRoute` 由 Harness Registry 的 `harness.provider` 配置解析而来（见 [harness-registry](../harness-registry/README.md) 6.1），Model Proxy 不自行持有 provider 配置：

```json
{
  "provider": "openai-compatible",
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
  "expiresAt": "2026-08-26T12:05:00+08:00"
}
```

### 6.3 Usage

```json
{
  "input_tokens": 1000,
  "output_tokens": 200,
  "total_tokens": 1200,
  "cache_read_tokens": 100,
  "cache_write_tokens": 50
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
| runtime token missing/invalid | 401 `invalid_credential` |
| runtime token expired | 401, adapter may refresh once |
| provider unreachable | task failed with `provider_error` or request 502 before task accepted |
| stream idle timeout | retry according to provider policy; exhaust -> `timeout` |
| unsupported tool schema | fail with safe `haas_tool_schema_unsupported`, do not drop tool silently |
| usage missing | return usage `null` and log safe diagnostic |
| transform fails | fail closed before upstream call if semantics are uncertain |

## 11. 测试计划与验收

- Unit：token validation、route resolution、URL allowlist、usage normalization、tool transform。
- Integration：loopback proxy receives harness request and injects provider credential only outbound。
- Streaming：SSE/chunked upstream relay is progressive and handles idle timeout。
- Security：provider key never appears in harness env/config/log/event/artifact/report。
- Negative：unsupported provider, missing key, expired token, disallowed URL and malformed upstream response。
