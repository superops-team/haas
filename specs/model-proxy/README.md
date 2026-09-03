# Model Proxy Component Specification

**English** | [简体中文](README.zh-CN.md)

Status: Draft
Last reviewed: 2026-08-26
Related specs: [Security Boundary](../security-boundary/README.md), [Harness Adapter](../harness-adapter/README.md), [Observability](../observability/README.md)

## 1. Component Role

The Model Proxy is the HaaS model-access boundary. It allows a harness to use OpenAI-compatible or vendor-native endpoints without directly accessing real provider credentials, and performs request/response shape compatibility, SSE relay, usage normalization, and secure observability where required.

## 2. Sources and Rationale

| Source | Adopted Content |
|--------|-----------------|
| `mpa-codex-worker` model proxy / secretless runtime | Provider keys do not enter the harness, short-lived proxy tokens, stream idle timeout, and usage normalization |
| Harness Registry | The `harness.provider` configuration is the source of ModelRoute (baseUrl/wireApi/credentialRef) |
| Security Boundary / Sandbox Runtime | Provider credentials use the credential vault and are not placed in the agent sandbox; caller URL allowlist |
| Component overview | Secretless runtime |

## 3. Upstream and Downstream Relationships

| Direction | Component | Relationship |
|-----------|-----------|--------------|
| Upstream | Harness Adapter | Points the harness model endpoint to the loopback proxy |
| Upstream | Session Runtime | Requests runtime tokens and usage aggregation |
| Upstream | Harness Registry | Provides provider route configuration (`harness.provider`) |
| Downstream | Security Boundary / Credential Vault | Resolves provider credential refs (`resolve_secret` is the sole entry point) |
| Downstream | Provider clients | OpenAI Responses, Chat Completions, Anthropic Messages, Azure OpenAI, and OpenAI-compatible aggregators |
| Downstream | Observability | Records request status, latency, usage, and security summaries |

## 4. Responsibility Boundaries

Responsibilities:

- Receive model-provider requests from the harness.
- Validate the session, audience, expiry, and revocation of a short-lived runtime token.
- Resolve the provider endpoint from the configured harness and requested model.
- Inject real provider credentials into outbound requests.
- Apply allowlist and SSRF protection to provider URLs.
- Forward streaming responses without buffering them to completion.
- Safely normalize provider responses and errors.
- Aggregate token usage and normalize fresh input, cache read, cache write, and output semantics.
- Support adapter-specific compatibility transforms, such as flattening/unflattening namespaced tools.

Non-responsibilities:

- Does not select the harness.
- Does not decide whether a user is authorized to use a model; it only enforces the frozen policy.
- Does not store full prompts or completions.
- Does not expose provider-specific schemas to upstream ADK clients.
- Does not silently drop tools when the provider does not support them; it MUST fail or record an explicit degradation.

## 5. Core Interfaces

### 5.1 Harness-Facing Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/v1/responses` | OpenAI-compatible Responses proxy |
| POST | `/v1/chat/completions` | Chat Completions-compatible proxy |
| GET | `/v1/models` | Summary of models available to the current token |
| GET | `/health` | Loopback proxy liveness |
| GET | `/ready` | Provider routing and secret resolver readiness |

These endpoints listen only on loopback by default, for example `127.0.0.1:18080`.

### 5.2 Internal API

```python
async def resolve_model_proxy_token(scope: ModelProxyScope) -> RuntimeToken: ...  # Delegate to Security Boundary issue_runtime_token(audience="model_proxy")
async def resolve_model_route(session_id: str, model: str) -> ModelRoute: ...
async def proxy_openai_responses(request: ProxyRequest) -> ProxyResponse: ...
async def proxy_chat_completions(request: ProxyRequest) -> ProxyResponse: ...
async def normalize_usage(provider: str, body: object) -> Usage: ...
async def transform_tools(provider: str, request: object) -> ProviderRequestTransform: ...
```

## 6. Data Model

### 6.1 ModelRoute

`ModelRoute` is resolved from the Harness Registry `harness.provider` configuration (see [harness-registry](../harness-registry/README.md) 6.1); the Model Proxy does not maintain provider configuration itself:

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

## 7. Runtime Model and State Machine

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
| OpenAI Responses | Preserve the Responses request/stream where supported |
| Chat Completions | Transform only when the adapter declares compatibility and tests cover it |
| Anthropic Messages | Use a provider-specific bridge only behind the proxy |
| OpenAI-compatible aggregator | Require route configuration to declare `wireApi`; do not infer it solely from the host |

## 8. Security and Permissions

- The real provider key is read only by the Model Proxy or secret resolver.
- The harness sees only the loopback base URL and a short-TTL token.
- A caller-provided provider base URL is accepted only through the registry allowlist.
- Request/response logging redacts Authorization, API keys, cookies, raw messages, and tool arguments.
- A proxy token MUST be session-scoped, audience-restricted, and revocable.
- The proxy MUST NOT expose arbitrary URL forwarding.

## 9. Observability

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

Log fields MUST use safe route ids, fingerprints, and status codes, and MUST NOT contain prompt bodies or credentials.

## 10. Failure and Recovery

| Scenario | Behavior |
|----------|----------|
| Runtime token missing/invalid | 401 `invalid_credential` |
| Runtime token expired | 401; the adapter MAY refresh once |
| Provider unreachable | Task fails with `haas_provider_error`, or the request returns 502 before task acceptance |
| Stream idle timeout | Retry according to provider policy; when exhausted -> `timeout` |
| Unsupported tool schema | Fail with safe `haas_tool_schema_unsupported`; do not silently drop the tool |
| Usage missing | Return usage `null` and log a safe diagnostic |
| Transform fails | Fail closed before the upstream call if semantics are uncertain |

## 11. Test Plan and Acceptance Criteria

- Unit: token validation, route resolution, URL allowlist, usage normalization, and tool transforms.
- Integration: the loopback proxy receives a harness request and injects the provider credential only outbound.
- Streaming: SSE/chunked upstream relay is progressive and handles idle timeout.
- Security: the provider key never appears in harness env/config/log/event/artifact/report.
- Negative: unsupported provider, missing key, expired token, disallowed URL, and malformed upstream response.
