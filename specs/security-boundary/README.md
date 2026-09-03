# Security Boundary Component Specification

**English** | [简体中文](README.zh-CN.md)

Status: Draft
Last reviewed: 2026-08-30
Related specs: [HaaS Protocol](../haas-protocol/README.md), [Model Proxy](../model-proxy/README.md), [MCP / Tool / Skill Runtime](../mcp-tool-skill-runtime/README.md), [Container Runtime](../container-runtime/README.md)

## 1. Component Role

The Security Boundary defines security invariants for all HaaS public entry points, internal adapters, model proxy, MCP/tool/skill runtime, artifacts, and container runtime. It is not a standalone runtime process; it is a set of contracts that all components MUST enforce collectively.

## 2. Sources and Rationale

| Source | Adopted Content |
|--------|-----------------|
| ADK 2.0 / HaaS Protocol | Bearer auth, object scope (including userId/sessionId), and error hygiene |
| `mpa-codex-worker` AGENTS/specs | Secretless runtime, redaction, runtime tokens, and prevention of secret leakage through public surfaces |
| OpenSandbox docs | Credential vault, egress allowlist, sandbox isolation, and API key auth |
| Overview requirements | Secretless and artifact-path security requirements |

## 3. Upstream and Downstream Relationships

| Direction | Component | Relationship |
|-----------|-----------|--------------|
| Upstream | HaaS Protocol | Auth, scope, schema, and error hygiene |
| Upstream | Harness Registry | Credential ref, MCP URL, and skill bundle validation |
| Upstream | Session Runtime | Object ownership, idempotency, and workspace scope |
| Upstream | Harness Adapter | Env/config redaction and permission policy |
| Upstream | Model Proxy | Provider credential handling |
| Upstream | Container Runtime | Filesystem/network/process isolation |

## 4. Responsibility Boundaries

Responsibilities:

- Define secret classifications and surfaces on which they MUST NOT appear.
- Define scope rules for caller principal, tenant, workspace, harness, session, invocation, and file.
- Define redaction rules for public error messages, logs, metrics, events, and artifact metadata.
- Define URL allowlist, private-network, loopback, Unix socket, and SSRF protection rules.
- Define artifact path-traversal protection, content-serving headers, and size/count limits.
- Define the lifecycle of credential references and short-lived runtime tokens.

Non-responsibilities:

- Does not implement a specific auth provider.
- Does not perform redaction directly; concrete components MUST call the shared redaction API.
- Does not determine business-level entitlement or billing policies.
- Does not replace the isolation capabilities of OpenSandbox/Kubernetes/Docker.

## 5. Core Interfaces

```python
def redact(value: object, *, context: RedactionContext) -> object: ...
def validate_scope(principal: Principal, obj: ScopedObject, action: str) -> ScopeDecision: ...
def validate_url(url: str, policy: UrlPolicy) -> UrlDecision: ...
def issue_runtime_token(scope: RuntimeTokenScope, ttl_seconds: int) -> RuntimeToken: ...
def resolve_secret(ref: str, audience: str) -> SecretValue: ...
def validate_artifact_path(container_root: str, requested: str) -> SafePath: ...
def assert_no_secret_surface(surface: object) -> None: ...
```

## 6. Data Model

### 6.1 RuntimeToken

```json
{
  "id": "rtok_abc",
  "audience": "model_proxy",
  "sessionId": "hsess_abc",
  "harnessId": "chrn_codex_default",
  "expiresAtMs": 1786400000000,
  "fingerprint": "sha256:abc",
  "revoked": false
}
```

### 6.2 SecretRef

```json
{
  "type": "secret_ref",
  "ref": "secret://tenant/workspace/provider/default",
  "fingerprint": "sha256:abc",
  "expiresAtMs": null
}
```

### 6.3 SafeErrorDetail

```json
{
  "safeReason": "provider_unavailable",
  "retryable": true,
  "traceId": "tr_abc",
  "supportCode": "diag_abc"
}
```

## 7. Runtime Model and State Machine

```text
raw inbound request
  -> schema validation
  -> auth
  -> scope check
  -> secret extraction/rejection
  -> policy validation
  -> safe internal request
  -> adapter execution
  -> redacted event/log/response/artifact metadata
```

Credential lifecycle:

```text
secret_ref configured
  -> runtime token issued for session (sole issuer: Security Boundary)
  -> adapter receives token/ref only
  -> model/MCP proxy resolves real secret per request
  -> token expires or revoked
```

**Unified issuance principle:** the sole issuer of runtime tokens is the Security Boundary's `issue_runtime_token(scope, audience)`. The Model Proxy and MCP proxy only consume tokens with `audience=model_proxy|mcp_proxy|adapter` and MUST NOT implement their own token systems.

**Secret concept layering:**

| Concept | Role | Stored Content |
|---------|------|----------------|
| secret store | Persistence layer for long-lived credential references | Stores only `credentialRef` + fingerprint, not plaintext |
| credential vault (OpenSandbox) | Runtime layer that resolves real secrets into sandbox sessions and issues short-lived tokens | Real secrets with session-level TTL |

These are distinct layers, both within the boundary defined by the Security Boundary; `resolve_secret(ref)` is the sole entry point for retrieving real secrets.

## 8. Security and Permissions

### 8.1 Prohibited Output Surfaces

The following MUST NOT appear in public responses, SSE, logs, metrics, artifact metadata, verification reports, or harness-visible durable configuration:

- Provider API key
- `Authorization` header value
- Cookie
- Presigned URL
- Raw prompt
- Full tool arguments or raw tool result
- Internal hostnames that reveal private infrastructure
- Absolute host paths
- Stack traces

### 8.2 URLs and Networking

- Caller-provided upstream URLs are denied by default unless they match the allowlist.
- Allowed schemes MUST be listed explicitly; only `https` and loopback `http` are allowed by default.
- Private IPs, metadata services, Unix sockets, and Docker sockets are denied by default.
- OpenSandbox egress policy provides runtime network control, while the HaaS URL validator provides protocol-entry control; neither can replace the other.

### 8.3 Object Scope

- Every object carries tenant/workspace/principal scope.
- ADK `userId`/`sessionId` values are caller-supplied and MUST belong to the authenticated principal; cross-user/session access returns 404.
- Cross-scope reads, cancellations, deletions, and downloads return 404.
- Admin/debug capabilities MUST be authorized independently and MUST NOT be derived from an ordinary bearer token.

### 8.4 Artifact Safety

- An artifact id MUST NOT be interpreted as a caller-controlled path.
- Downloads MUST be confined to the container root.
- Responses MUST include `X-Content-Type-Options: nosniff`.
- Active content such as HTML/JS/SVG SHOULD use a separate origin or attachment.

### 8.5 Redaction Taxonomy

`redact(value, context)` applies the following classifications consistently. Detection uses field-level rules plus a regex table. The regex table and the commit gate `scripts/quality/secret-scan.py` share the same pattern list as a single source of truth, driven by the same fixture in code.

| Classification | Detection | Default Action |
|----------------|-----------|----------------|
| `credential` | Field names (key/token/password, etc.) + secret patterns (AKIA/sk-/ghp_/AIza/JWT…) | Replace with `[REDACTED]` |
| `authorization` | `Authorization`/`Bearer`/`Basic`/`Cookie` header values | Replace with `[REDACTED]` |
| `presigned-url` | `X-Amz-Signature`/`Signature=` query | Replace the entire URL with `[REDACTED_URL]` |
| `raw-prompt` | Input prompt when `trace_content=false` (default) | Do not write to logs/events |
| `tool-payload` | Full tool args/result | Summarize by default; full payload requires an approved debug design |
| `host-path` | Internal hosts and absolute paths | `[REDACTED_PATH]` |
| `stack-trace` | Exception stack | Exclude from public surfaces; record only a safe reason internally |

If detection fails or context is insufficient, **fail closed**: omit the content rather than writing unredacted content. `redact()` is the sole entry point; components MUST NOT implement private redaction logic.

**Upstream response bodies are also untrusted sources:** a provider / harness / MCP response body may echo an injected `Authorization` value or other credential. Therefore, before it is included in an error message, log, or event, it MUST be redacted and truncated by `safe_upstream_body()` (512 characters by default). Error information MUST still retain diagnosable details such as status codes; redaction MUST NOT remove actionability. This function is the sole implementation shared by the model proxy and OpenSandbox client; each component MUST NOT duplicate it.

## 9. Observability

Security logs record only:

- `traceId`
- `principalHash`
- `tenantId` / `workspaceId` hash or safe id
- `objectType`
- `objectId`
- `action`
- `decision`
- `safeReason`
- `credentialFingerprint`
- `policyVersion`

They MUST NOT record secret values, raw request bodies, raw prompts, or full tool payloads.

## 10. Failure and Recovery

| Scenario | Behavior |
|----------|----------|
| Auth missing or invalid | `401 missing_credential` / `invalid_credential` |
| Scope mismatch | 404, without exposing object existence |
| Secret appears in a prohibited field | `400 haas_secret_input_invalid` or redaction + security finding |
| URL not in allowlist | `403 haas_url_not_allowed` |
| Runtime token expired | Proxy returns 401; the adapter MAY refresh once, and task fails if refresh fails |
| Redaction pipeline fails | Fail closed; do not write an unredacted payload |
| Artifact path traversal | Deny access and record a security event |

## 11. Test Plan and Acceptance Criteria

- Unit: redaction regex/structured traversal, URL allowlist, scope checks, and artifact path canonicalization.
- Integration: cross-access between two principals for harness/session/invocation/file returns 404.
- Proxy: the real provider key does not enter harness env/config; short-lived token expiry and revocation take effect.
- Event/log: construct input containing a key/header/raw prompt/tool args and assert that all public surfaces are redacted.
- Container: the AIO container contains no publicly exposed secret by default; `/health` and `/ready` do not output sensitive environment data.
