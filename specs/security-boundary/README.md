# Security Boundary Component Specification

**English** | [简体中文](README.zh-CN.md)

Status: Draft
Last reviewed: 2026-09-14
Change ID: unified-runtime-approval-policy
Related specs: [HaaS Protocol](../haas-protocol/README.md), [Harness Profile](../harness-profile/README.md), [Model Proxy](../model-proxy/README.md), [MCP / Tool / Skill Runtime](../mcp-tool-skill-runtime/README.md), [Manager Delegation](../manager-delegation/README.md), [Container Runtime](../container-runtime/README.md)

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
| Upstream | Harness Registry / Harness Profile | Credential ref, MCP URL, skill bundle, and AGENTS.md validation |
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
- Define non-approvable hard denies and the maximum scope/lifetime of an approval grant.

Non-responsibilities:

- Does not implement a specific auth provider.
- Does not perform redaction directly; concrete components MUST call the shared redaction API.
- Does not determine business-level entitlement or billing policies.
- Does not replace the isolation capabilities of OpenSandbox/Kubernetes/Docker.

An approval is never a root or host capability. Platform hard denies include credential and
secret-store access, raw host/private/link-local/metadata/control-plane networking, Docker/Unix
control sockets, mount roots outside the authorized manifest, cross-principal/session objects, and
capabilities the selected runtime cannot enforce. These remain denied under `always`,
`on-request`, and `never`. Fresh-session public network allow applies only after these exclusions.
Approval requests and events retain redacted summaries and fingerprints, never raw commands,
prompts, headers, cookies, credentials, or signed authorization URLs.

## 5. Core Interfaces

```python
def redact(value: object, *, context: RedactionContext) -> object: ...
def validate_scope(principal: Principal, obj: ScopedObject, action: str) -> ScopeDecision: ...
def validate_url(url: str, policy: UrlPolicy) -> UrlDecision: ...
def validate_mount_manifest(manifest: MountManifest, policy: EffectivePolicy) -> MountDecision: ...
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

### 7.1 Credential Provisioning and Execution Data

`credentialRef` is an address, not a transported credential. In local-managed mode the manager secret store remains the long-lived owner. The control-side resolver uses an owner-only, peer-authenticated local IPC service to request only references authorized for the session; it provisions the trusted external broker's memory over a private authenticated TLS channel or owner-only IPC. This channel is separate from the worker execution transport, rejects replay/out-of-scope requests, and never includes secrets in process arguments, env, logs or persistent HaaS configuration. The broker resolves model/MCP requests but offers no credential-read operation to workers. Restart re-provisions secrets; revocation closes old grants. Remote deployments require operator-provisioned vault references in the remote scope; a local `secret://manager/...` reference is not implicitly resolvable remotely.

For Lite, broker memory replaces the OpenSandbox runtime vault. Security Boundary remains the sole issuer of short-lived tokens. Plaintext upstream credentials are never stored in the worker volume. Private native conversation/checkpoint files required for continuation are access-controlled execution data, not logs or public event payloads; validate/reject secret-bearing input before native execution, apply session retention and exclude those files from artifacts/debug output. Public raw prompt/tool-payload prohibitions remain unchanged.

For the embedded macOS local API, the private channel is an anonymous Unix socketpair inherited only by the owned HaaS child; possession of the inherited descriptor authenticates the peer, without a discoverable listener. `HAAS_CREDENTIAL_FD` carries only its number, is consumed on startup, and is never inherited by Codex. Manager grants bind reference/harness/session/model/exact URL, with monotonic request sequence and bounded frames. EOF, timeout, replay, malformed frames or an unknown grant deny resolution; only Manager SecretStore resolution can return key bytes on this channel. Grants and channel die with the supervisor; no arbitrary-secret lookup or remote forwarding is permitted.

Short-lived execution evidence is a separate, non-durable class of execution data. The
HaaS process MAY retain one bounded evidence record in memory for each active command
activity so the owning user can inspect the actual command, working directory and output.
The redacted output field is bounded to 8 MiB (8,388,608 UTF-8 bytes) per record. The Codex
transport frame bound MUST additionally accommodate the JSON-RPC envelope and worst-case JSON
escaping for a supported evidence payload. Content that fits this evidence bound MUST be
returned completely; silent truncation below the bound is prohibited. Output beyond the
bound remains explicitly truncated and requires the governed Artifact Store for complete
retrieval.
The record is addressed only by an opaque `evidenceRef`, is scoped to the authenticated
principal, full session key, invocation and tool call, and expires no later than the native
authorization URL or 15 minutes after command termination, whichever is earlier. It is
deleted on expiry, session deletion or runtime shutdown and is excluded from event stores,
transcripts, artifacts, diagnostics, metrics and logs.

An evidence record applies value-aware credential redaction before it can be returned. API
keys, bearer/basic authorization values, cookies, passwords, private keys and secret-bearing
environment assignments are replaced while ordinary command text, container working paths
and ordinary HTTPS URLs remain intact. A complete short-lived HTTPS authorization URL MAY
remain intact only when the adapter identifies explicit user-authorization semantics and the
response is served only by the scoped execution-evidence endpoint with an enforced bounded
expiry. If the native URL exposes a trustworthy earlier expiry, the evidence adopts that
earlier deadline; the URL need not expose its expiry in a query parameter. This narrow
exception preserves the link's signature and usability; it does not permit the URL in any
durable or observable surface.

## 8. Security and Permissions

### 8.1 Prohibited Output Surfaces

The following MUST NOT appear in public responses, SSE, logs, metrics, artifact metadata, verification reports, or harness-visible durable configuration:

- Provider API key
- `Authorization` header value
- Cookie
- Presigned URL
- Raw prompt
- Full tool arguments or raw tool result
- Raw AGENTS.md content outside approved profile materialization storage
- Adapter/native event type names or untyped debug payloads on public native events
- Internal-only event fields such as `adapterId`, `userId`, `schemaVersion`, or `redactionApplied`
- Internal hostnames that reveal private infrastructure
- Absolute host paths
- Stack traces

The scoped, `Cache-Control: no-store` execution-evidence response defined in §7.1 is the
only exception for a validated, unexpired user-authorization URL. The exception does not
apply to canonical events, ADK projections, logs, transcripts, artifacts, diagnostics,
crash reports or verification output.

### 8.2 URLs and Networking

- Caller-provided upstream URLs are denied by default unless they match the allowlist.
- Allowed schemes MUST be listed explicitly; only `https` and loopback `http` are allowed by default.
- Private IPs, metadata services, Unix sockets, and Docker sockets are denied by default.
- OpenSandbox egress policy provides runtime network control, while the HaaS URL validator provides protocol-entry control; neither can replace the other.

### 8.3 Object Scope

- Every object carries tenant/workspace/principal scope.
- Harness profiles carry the same tenant/workspace scope as their harness. Cross-scope
  profile reads, activation, deletion, validation, and session rebind return 404.
- ADK `userId`/`sessionId` values are caller-supplied and MUST belong to the authenticated principal; cross-user/session access returns 404.
- Cross-scope reads, cancellations, deletions, and downloads return 404.
- Admin/debug capabilities MUST be authorized independently and MUST NOT be derived from an ordinary bearer token.

### 8.4 Artifact Safety

- An artifact id MUST NOT be interpreted as a caller-controlled path.
- Downloads MUST be confined to the container root.
- Responses MUST include `X-Content-Type-Options: nosniff`.
- Active content such as HTML/JS/SVG SHOULD use a separate origin or attachment.

### 8.5 Delegated Mount Safety

Manager-delegated execution may mount a user-authorized project root into the HaaS
container, but the mount contract is still subject to the Security Boundary:

- primary project mount is `/workspace:rw` only after explicit manager authorization;
- extra mounts default to `ro` and require explicit authorization to become `rw`;
- user HOME, parent directories beyond the project grant, Docker socket, SSH
  directories, credential stores, and unrequested paths are denied;
- restore MUST revalidate path existence, canonical path, type, symlink boundaries,
  and access mode before creating a container;
- failures return structured safe errors and never fall back to local execution.

### 8.6 Redaction Taxonomy

`redact(value, context)` applies the following classifications consistently. Detection uses field-level rules plus a regex table. The regex table and the commit gate `scripts/quality/secret-scan.py` share the same pattern list as a single source of truth, driven by the same fixture in code.

| Classification | Detection | Default Action |
|----------------|-----------|----------------|
| `credential` | Field names (key/token/password, etc.) + secret patterns (AKIA/sk-/ghp_/AIza/JWT…) | Replace with `[REDACTED]` |
| `authorization` | `Authorization`/`Bearer`/`Basic`/`Cookie` header values | Replace with `[REDACTED]` |
| `presigned-url` | `X-Amz-Signature`/`Signature=` query | Replace the entire URL with `[REDACTED_URL]` |
| `ephemeral-authorization-url` | Adapter-classified HTTPS user-authorization URL with explicit authorization semantics and an evidence-enforced expiry | Preserve byte-for-byte only in the scoped no-store evidence response; otherwise treat as `presigned-url` |
| `raw-prompt` | Input prompt when `trace_content=false` (default) | Do not write to logs/events |
| `tool-payload` | Full tool args/result | Summarize by default; full payload requires an approved debug design |
| `native-event` | Adapter/native type names, untyped metadata, internal event fields | Map to stable allowlisted `haas.*` type, validate type-specific `haas` metadata, and strip internal fields before public projection |
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
| AGENTS.md/profile content contains a secret-like value | Reject validation or activation with `haas_agents_md_invalid` / `haas_secret_input_invalid`; do not materialize |
| URL not in allowlist | `403 haas_url_not_allowed` |
| Runtime token expired | Proxy returns 401; the adapter MAY refresh once, and task fails if refresh fails |
| Redaction pipeline fails | Fail closed; do not write an unredacted payload |
| Artifact path traversal | Deny access and record a security event |
| Delegated mount validation fails | Deny with `haas_delegation_mount_invalid`; require reauthorization or policy rebind |

## 11. Test Plan and Acceptance Criteria

- Unit: redaction regex/structured traversal, URL allowlist, scope checks, and artifact path canonicalization.
- Unit: execution-evidence redaction preserves ordinary paths and URLs, masks actual credential values, preserves only validated unexpired authorization URLs byte-for-byte, round-trips a 100,001-byte command output without truncation, enforces the 8 MiB bound, and never copies those URLs into durable surfaces.
- Integration: evidence access returns 404 across principal/session/invocation/tool scope, 410 after expiry, `Cache-Control: no-store`, `Referrer-Policy: no-referrer`, and no authorization URL in logs/events/transcripts/artifacts.
- Integration: cross-access between two principals for harness/profile/session/invocation/file returns 404, and unauthorized session profile rebind also returns 404.
- Proxy: the real provider key does not enter harness env/config; short-lived token expiry and revocation take effect.
- Event/log: construct input containing a key/header/raw prompt/tool args/AGENTS.md
  secret-like content and assert that all public surfaces are redacted or rejected.
  Native event tests reject unknown/untyped public payloads and prove adapter
  `nativeType`, internal ids, raw prompts/reasoning, and full tool payloads do not escape.
- Container: the AIO container contains no publicly exposed secret by default; `/health` and `/ready` do not output sensitive environment data.
- Delegation: HOME, Docker socket, SSH paths, symlink escapes, parent-directory widening, and mount drift are denied during create and restore.
