# Model Proxy Component Specification

**English** | [简体中文](README.zh-CN.md)

Status: Draft
Last reviewed: 2026-09-15
Change ID: long-task-model-proxy-stability
Related specs: [Security Boundary](../security-boundary/README.md), [Harness Adapter](../harness-adapter/README.md), [Manager Delegation](../manager-delegation/README.md), [Observability](../observability/README.md)

## 1. Component Role

The Model Proxy is the HaaS model-access boundary. It allows a harness to use OpenAI-compatible or vendor-native endpoints without directly accessing real provider credentials, and performs request/response shape compatibility, SSE relay, usage normalization, and secure observability where required.

### 1.1 Embedded Local Runtime

The HaaS lifespan owns a loopback-only proxy listener and closes upstream
connections on shutdown. Model Proxy capabilities are session-scoped and
generationed: Codex sees a `model_proxy` bearer that identifies only the
authorized HaaS session, harness, provider family, base URL and credential
scope. It MUST NOT bind to one invocation id, one profile version, one model id,
or one model-call sequence. Each invocation still freezes its applied provider
route for execution and records the generation it used, but terminal/cancel/fail
does not revoke the session capability while the logical session remains usable.
Unknown session routes never fall back to mutable registry configuration.
Credential resolution additionally checks the Manager grant's exact provider
scope and URL; redirects and environment proxy inheritance are disabled.

Responses supports both JSON and incremental SSE with bounded upstream read,
connect and response-header timeouts plus cancellation cleanup. Errors never
relay raw provider bodies, credentials or raw exception text. For a rejected
upstream request, the proxy may retain only a bounded, redacted diagnostic
assembled from the structured `error.code`, `error.type`, `error.param`, and
`error.message` fields; unknown fields and non-JSON bodies are discarded. JSON
and SSE setup failures use the same diagnostic rule. SSE error frames are
normalized without dropping valid text/tool frames. Chat Completions remains
explicitly unsupported for this Codex path, never silently substituted. Control
readiness is independent; execution readiness requires a configured profile and
live resolver/proxy in addition to Codex. Submission validates the actual
selected profile rather than trusting global readiness.

For the explicitly selected `volcengine-ark` provider, outbound Responses input history fills absent `status` with `completed` on reasoning items and assistant messages, as required by the standard Ark endpoint. The outbound top-level `reasoning.summary` option is omitted because Ark rejects that OpenAI-only field; other reasoning options remain unchanged. Codex namespace tools (`{"type":"namespace","name":"mcp__...","tools":[...]}`) are flattened into ordinary Responses `function` tools before the upstream call, using `<namespace>__<tool>` names, and prior input/output `function_call` items are restored across the same bridge. This preserves Codex MCP semantics while avoiding provider-side `unknown tool type: namespace` failures. Explicit statuses, content, IDs and non-namespace tool payloads remain unchanged, and the caller's input is not mutated. Other provider IDs remain unchanged; neither URL nor display name selects this compatibility rule. JSON and SSE requests use the same conversion. Offline negative tests and real multi-turn provider smoke cover this rule.

## 2. Sources and Rationale

| Source | Adopted Content |
|--------|-----------------|
| `mpa-codex-worker` model proxy / secretless runtime | Provider keys do not enter the harness, session-scoped proxy tokens, dynamic credential generations with grace fallback, stream idle timeout, retry budgets, and usage normalization |
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
- Validate the session, audience, generation, expiry, and revocation of a
  session-scoped runtime token.
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
async def register_model_proxy_route(session_id: str, route: ModelRoute, credential_ref: str) -> ModelProxyCapability: ...
async def refresh_model_proxy_capability(session_id: str, token_generation: int, reason: str) -> ModelProxyCapability: ...
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
  "harnessId": "chrn_codex_default",
  "providerScopeKey": "sha256:provider-base-wire-token-field",
  "expiresAtMs": 1786400000000
}
```

`RuntimeTokenScope` is intentionally session-scoped. The token may also carry a
non-secret `generation` and `issuedAtMs`, but it MUST NOT carry provider
credentials, profile versions, model ids as hard auth boundaries, invocation ids,
host paths, or raw URLs with query strings. Model authorization is enforced by
the current session capability and route registry at request time.

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

The capability is process memory by default and may be reconstructed from the
Manager credential channel plus the session's frozen profile. Durable stores keep
only fingerprints, generation counters and route summaries; they never persist
raw model proxy bearer tokens or provider credentials.

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

## 7. Runtime Model and State Machine

```text
session capability registered or refreshed
  -> harness sends model request to loopback proxy
  -> token validated to session/generation/provider scope
  -> route resolved from the session capability and frozen profile
  -> request transformed if required
  -> upstream called with bounded retry policy
  -> streaming bytes relayed or JSON returned
  -> response transformed if required
  -> usage emitted
```

The capability lifetime covers the whole logical session while it is eligible to
accept work. It MUST outlive any accepted invocation deadline, including the
24-hour long-task default, and MUST remain valid across tool calls, model calls,
GUI reconnects, and Manager/HaaS hot restarts that keep the same owned
credential channel. A blocking interaction has an explicit `expiresAtMs` no
later than the invocation deadline; capability refresh MUST keep the pending
turn resumable until that deadline or an authoritative terminal.

Refresh keeps the same audience, harness, session, provider scope and base URL;
it never widens scope and never exposes the upstream credential. A refresh MAY
add or remove allowed models only when the session's applied profile or explicit
profile rebind authorizes that change. The previous generation remains valid as
grace until the replacement has been applied to the native thread, a bounded
grace TTL expires, or a security boundary is crossed. Auth failures before any
response bytes MAY use the latest grace credential once; failures after bytes are
emitted are terminal stream failures, not hidden retries.

Codex app-server may keep an old loopback base URL and bearer token inside a
native thread after HaaS/Manager reconnect. Before reporting
`model_proxy_token_invalid` to the harness, Model Proxy MUST attempt one
same-session recovery path: parse or look up the token's session/generation
fingerprint, verify the active Manager-owned credential channel, verify the
request model is allowed by the current session capability, and rebind the route
to the current loopback listener. If these checks pass, retry the request once
with the refreshed capability. If they fail, return the stable token error and
retain partial progress. This recovery MUST NOT allow cross-session reuse,
provider URL changes without profile authorization, or access to retired
credentials outside their grace window.

Provider compatibility:

| Provider shape | Behavior |
|----------------|----------|
| OpenAI Responses | Preserve the Responses request/stream where supported |
| Chat Completions | Transform only when the adapter declares compatibility and tests cover it |
| Anthropic Messages | Use a provider-specific bridge only behind the proxy |
| OpenAI-compatible aggregator | Require route configuration to declare `wireApi`; do not infer it solely from the host |

## 8. Security and Permissions

- The real provider key is read only by the Model Proxy or secret resolver.
- The harness sees only the loopback base URL and a session-scoped bearer token.
- A delegated HaaS container MUST NOT receive the real provider key in env, config,
  startup command, mount, event, log, or artifact metadata.
- A caller-provided provider base URL is accepted only through the registry allowlist.
- Request/response logging redacts Authorization, API keys, cookies, raw messages, and tool arguments.
- A proxy token MUST be session-scoped, audience-restricted, generationed, and revocable.
- The proxy MUST NOT expose arbitrary URL forwarding.

## 9. Observability

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

Log fields MUST use safe route ids, fingerprints, and status codes, and MUST NOT contain prompt bodies or credentials.

## 10. Failure and Recovery

| Scenario | Behavior |
|----------|----------|
| Runtime token missing/invalid during an active or resumable session | Rebind or refresh once only if the same session, harness, provider scope and active credential channel are proven; otherwise fail with `model_proxy_token_invalid` and retain partial progress |
| Runtime token expired during an active or resumable session | Refresh before retrying the model request; if refresh fails, emit `model_proxy_token_expired`; do not convert the turn to a generic `incomplete` |
| Delegated session credentialRef missing or not allowed | fail closed with `invalid_credential` or `haas_provider_source_invalid`; do not ask the harness for a key |
| Harness has no provider bound to this session (no frozen/effective profile, e.g. a turn started without a manager-synced delegated profile) | Persist `haas.turn.failed` with `haas.code=haas_provider_not_configured`, non-retryable. Deterministic operator/config condition (GUI/manager must configure and delegate a provider), distinct from generic `haas_provider_error`/`SecretResolutionError` (present-but-unresolvable credential). A present-but-invalid provider (bad wire api) keeps `haas_provider_error`. |
| Provider unreachable | Before invocation acceptance, return `502 haas_provider_error`; after acceptance, persist `haas.turn.failed` with `haas.code=haas_provider_error`, retain partial events, and keep `/run`/`/run_sse` HTTP 200 |
| Upstream 429/502/503/504 or connect/read timeout before output | Retry with bounded exponential backoff and jitter while the invocation deadline allows it; default retry count is 2 and total retry sleep is bounded |
| Stream idle timeout | Retry according to provider policy before output; after acceptance or after bytes have been emitted, exhaustion persists `haas.turn.failed` or `haas.turn.incomplete` with a stable timeout code/reason and HTTP 200 |
| Loopback listener port changes after Manager/HaaS restart | Reconstruct session capability through the owned credential channel and rebind the native profile before the next model request; never attach to an unowned listener |
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
- Lifecycle: a real or clock-controlled task crosses the configured stream-idle interval, the former 900-second boundary, and at least one tool round-trip without losing its token; refresh/rebind preserves exact session/provider scope; stale, cross-session and post-session-revocation/deletion tokens fail.
- Concurrency: two sessions using the same provider concurrently keep distinct capability generations and routes; model/key/profile changes in one session cannot invalidate or hijack the other's loopback token.
- Observability: token issue/refresh/revoke records include only safe token fingerprint, session id, generation, age and reason. The original token and provider key never appear.
