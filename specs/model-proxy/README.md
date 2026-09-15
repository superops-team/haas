# Model Proxy Component Specification

**English** | [简体中文](README.zh-CN.md)

Status: Draft
Last reviewed: 2026-09-12
Related specs: [Security Boundary](../security-boundary/README.md), [Harness Adapter](../harness-adapter/README.md), [Manager Delegation](../manager-delegation/README.md), [Observability](../observability/README.md)

## 1. Component Role

The Model Proxy is the HaaS model-access boundary. It allows a harness to use OpenAI-compatible or vendor-native endpoints without directly accessing real provider credentials, and performs request/response shape compatibility, SSE relay, usage normalization, and secure observability where required.

### 1.1 Embedded Local Runtime

The HaaS lifespan owns a loopback-only proxy listener and closes upstream connections on shutdown. Each invocation freezes its applied provider route and issues a short-lived model_proxy token restricted to its harness, session, invocation and exact model. Token revocation and route removal happen on every terminal/cancel/failure path. Unknown invocation routes never fall back to mutable registry configuration. Credential resolution additionally checks the Manager grant's exact model/URL tuple; redirects and environment proxy inheritance are disabled.

Responses supports both JSON and incremental SSE with bounded upstream read timeout and cancellation cleanup. Errors never relay raw provider bodies, credentials or raw exception text. For a rejected upstream request, the proxy may retain only a bounded, redacted diagnostic assembled from the structured `error.code`, `error.type`, `error.param`, and `error.message` fields; unknown fields and non-JSON bodies are discarded. JSON and SSE setup failures use the same diagnostic rule. SSE error frames are normalized without dropping valid text/tool frames. Chat Completions remains explicitly unsupported for this Codex path, never silently substituted. Control readiness is independent; execution readiness requires a configured profile and live resolver/proxy in addition to Codex. Submission validates the actual selected profile rather than trusting global readiness.

For the explicitly selected `volcengine-ark` provider, outbound Responses input history fills absent `status` with `completed` on reasoning items and assistant messages, as required by the standard Ark endpoint. The outbound top-level `reasoning.summary` option is omitted because Ark rejects that OpenAI-only field; other reasoning options remain unchanged. Codex namespace tools (`{"type":"namespace","name":"mcp__...","tools":[...]}`) are flattened into ordinary Responses `function` tools before the upstream call, using `<namespace>__<tool>` names, and prior input/output `function_call` items are restored across the same bridge. This preserves Codex MCP semantics while avoiding provider-side `unknown tool type: namespace` failures. Explicit statuses, content, IDs and non-namespace tool payloads remain unchanged, and the caller's input is not mutated. Other provider IDs remain unchanged; neither URL nor display name selects this compatibility rule. JSON and SSE requests use the same conversion. Offline negative tests and real multi-turn provider smoke cover this rule.

## 2. Sources and Rationale

| Source | Adopted Content |
|--------|-----------------|
| `mpa-codex-worker` model proxy / secretless runtime | Provider keys do not enter the harness, short-lived proxy tokens, stream idle timeout, and usage normalization |
| Effective Harness Profile | Frozen applied provider route is the per-invocation source of ModelRoute |
| Security Boundary / Sandbox Runtime | Provider credentials use the credential vault and are not placed in the agent sandbox; caller URL allowlist |
| Component overview | Secretless runtime |

## 3. Upstream and Downstream Relationships

| Direction | Component | Relationship |
|-----------|-----------|--------------|
| Upstream | Harness Adapter | Points the harness model endpoint to the loopback proxy |
| Upstream | Session Runtime | Requests runtime tokens and usage aggregation |
| Upstream | Session Runtime / Effective Harness Profile | Provides the applied frozen provider route |
| Downstream | Security Boundary / Credential Vault | Resolves provider credential refs (`resolve_secret` is the sole entry point) |
| Downstream | Provider clients | OpenAI Responses, Chat Completions, Anthropic Messages, Azure OpenAI, and OpenAI-compatible aggregators |
| Downstream | Observability | Records request status, latency, usage, and security summaries |

## 4. Responsibility Boundaries

Responsibilities:

- Receive model-provider requests from the harness.
- Validate the session, audience, expiry, and revocation of a short-lived runtime token.
- Resolve the provider endpoint from the configured harness and requested model.
- For manager-delegated sessions, resolve only manager-supplied `credentialRef`
  values that are present in the delegated-session contract and allowed by the frozen
  policy snapshot.
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

`ModelRoute` is resolved only from the invocation's applied `EffectiveHarnessProfile`, after authorized management overrides and hard policy checks; the live Registry projection is not a running session's route source. It is derived from the same `ProviderRoute` contract used by Harness Profile: the source keeps both `providerId` (stable identity) and `name` (alias). `wireApi` declares the protocol: `openai-compatible` is the OpenAI-compatible protocol family, `responses` requires the OpenAI Responses protocol type within that family, and `agent-plan` is the Ark Agent Plan protocol.

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

The capability lifetime covers the whole accepted invocation, including model calls after tools and a bounded wait for human response. The initial expiry MUST be later than the invocation deadline plus a bounded cleanup margin, or the runtime MUST refresh before expiry. A blocking interaction has an explicit `expiresAtMs` no later than the invocation deadline; expiry produces a typed terminal rather than an indefinite wait. Refresh keeps the same audience, harness, session, invocation, exact model and exact URL; it never widens scope and never exposes the upstream credential. Only one refresh is attempted for a rejected active capability. The previous token remains valid until the replacement has been applied to the native thread, then is revoked. All tokens and routes are revoked at the authoritative terminal, cancellation, or runtime shutdown.

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
- A delegated HaaS container MUST NOT receive the real provider key in env, config,
  startup command, mount, event, log, or artifact metadata.
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
| Runtime token missing/invalid during an active invocation | Refresh once only if the exact frozen scope is still active; otherwise fail with `model_proxy_token_invalid` and retain partial progress |
| Runtime token expired during an active invocation | Refresh once before retrying the model request; if refresh fails, emit `model_proxy_token_expired`; do not convert the turn to a generic `incomplete` |
| Delegated session credentialRef missing or not allowed | fail closed with `invalid_credential` or `haas_provider_source_invalid`; do not ask the harness for a key |
| Provider unreachable | Before invocation acceptance, return `502 haas_provider_error`; after acceptance, persist `haas.turn.failed` with `haas.code=haas_provider_error`, retain partial events, and keep `/run`/`/run_sse` HTTP 200 |
| Stream idle timeout | Retry according to provider policy; after acceptance, exhaustion persists `haas.turn.failed` or `haas.turn.incomplete` with a stable timeout code/reason and HTTP 200 |
| Unsupported tool schema | Fail with safe `haas_tool_schema_unsupported`; do not silently drop the tool |
| Usage missing | Return usage `null` and log a safe diagnostic |
| Transform fails | Fail closed before the upstream call if semantics are uncertain |

## 11. Test Plan and Acceptance Criteria

- Unit: token validation, route resolution, URL allowlist, usage normalization, and tool transforms.
- Integration: the loopback proxy receives a harness request and injects the provider credential only outbound.
- Integration: a delegated Codex container uses only the loopback model proxy and never observes the raw manager/provider key.
- Streaming: SSE/chunked upstream relay is progressive and handles idle timeout.
- Security: the provider key never appears in harness env/config/log/event/artifact/report.
- Negative: unsupported provider, missing key, expired token, disallowed URL, and malformed upstream response. Tests distinguish pre-acceptance HTTP errors from accepted HTTP-200 terminal failures and retain partial output.
- Lifecycle: a real or clock-controlled task crosses the configured stream-idle interval and at least one tool round-trip without losing its token; refresh preserves exact scope; stale, cross-session and post-terminal tokens fail.
- Observability: token issue/refresh/revoke records include only safe token fingerprint, invocation id, age and reason. The original token and provider key never appear.
