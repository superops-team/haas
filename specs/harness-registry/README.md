# Harness Registry Component Specification

**English** | [简体中文](README.zh-CN.md)

Status: Draft
Last reviewed: 2026-08-30
Related specs: [HaaS Protocol](../haas-protocol/README.md), [Harness Adapter](../harness-adapter/README.md), [Security Boundary](../security-boundary/README.md)

## 1. Component Role

The Harness Registry maintains the catalog of configured harnesses that HaaS can run. It answers which harnesses (ADK apps) a tenant or workspace may currently select, which models, tools, MCP servers, and skills each harness may use, and whether those capabilities are actually available.

In ADK terminology, `appName` is the configured harness `id` (`chrn_...`); `/run` MAY also resolve the human-readable `name` as an alias. `base` is an open string: the initial release uses `codex`, and future registration of `pi`, `opencode`, `amp`, or another harness requires no change to the public task API.

## 2. Sources and Rationale

| Source | Adopted elements |
|--------|------------------|
| ADK 2.0 | `appName` = harness `id`; `/list-apps` lists app names |
| `mpa-codex-worker` profile controller | Profile draft/active lifecycle, session freezing, runtime policy |
| Model Proxy | Provider routing and model availability |
| Component overview | Configured harness catalog and appName/base/capability/model/provider discovery |

## 3. Upstream and Downstream Relationships

| Direction | Component | Relationship |
|-----------|-----------|--------------|
| Upstream | HaaS Protocol | Resolves appName -> configured harness; list/read/create/update/delete |
| Upstream | Session Runtime | Resolves a harness when creating a run |
| Downstream | Harness Adapter Registry | Checks whether a base is installed and ready, and which capabilities it supports |
| Downstream | Model Proxy | Queries model and provider availability; supplies provider routes |
| Downstream | MCP / Tool / Skill Runtime | Validates MCP, skills, and disabledTools |
| Downstream | Security Boundary | Validates scope, secrets, SSRF controls, and policy |

## 4. Responsibility Boundaries

Responsibilities:

- Store stable configured harness configuration.
- Compute the effective view of harness, base, model, provider, MCP, skill, and tool restrictions.
- Resolve `appName`: first by exact `id`, then by `name`; return 404 for multiple matches or no match.
- Return harnesses within the caller's scope; return not found for cross-scope access.
- Freeze the effective harness configuration when a session is created; later harness updates MUST NOT change existing sessions.
- Check adapter availability for `base`.
- Store and validate provider routing configuration (`baseUrl`/`wireApi`/`credentialRef`); URLs MUST pass the allowlist.
- Validate availability of `defaultModel` and the requested `model`, or apply an explicit fallback.
- Store skill folder bundles and preserve every file across a round trip.

Non-responsibilities:

- Does not execute a harness directly.
- Does not store raw credentials; it stores only references or delegates them to a secret store/vault.
- Does not execute MCP/tool calls.
- Does not modify the frozen configuration of an existing session.
- Does not force a harness's native tool names into a hard contract shared by all harnesses.

## 5. Core Interfaces

### 5.1 Public API

| Method | Path | Description |
|--------|------|------|
| GET | `/list-apps` | Lists harness app names within the caller's scope (an array of id strings) |
| GET | `/v1/haas/harnesses` | Lists configured harness details within the caller's scope |
| GET | `/v1/haas/harnesses/{harness_id}` | Reads one configured harness |
| POST | `/v1/haas/harnesses` | Creates a configured harness |
| PUT | `/v1/haas/harnesses/{harness_id}` | Replaces mutable configuration; `id`, `base`, and `createdAtMs` are immutable |
| DELETE | `/v1/haas/harnesses/{harness_id}` | Marks a harness as deleted without deleting historical sessions |
| GET | `/v1/haas/models` | Returns the global backend/model catalog |
| GET | `/v1/haas/harnesses/{harness_id}/skills/{skill_id}/files` | Reads the complete skill folder bundle |

### 5.1.1 PUT Immutable-Field Semantics (S6)

The `PUT` body uses the `HarnessCreate` schema, which does not contain `id`/`createdAtMs`. However, because the schema specifies `additionalProperties: true`, callers may still include these fields. The handling rules are:

| Condition | Behavior |
|------|------|
| Body omits `id`/`base`/`createdAtMs` | Update mutable fields normally |
| Values in the body **match** the existing record | Accept idempotently without error, enabling read-modify-write round trips |
| Values in the body **conflict** with the existing record | Return `400 invalid_input`; do not apply a partial update |

No dedicated error code is introduced. An immutable-field conflict is a request-body validation failure and reuses the existing `invalid_input` code (see [ERROR-CODES](../haas-protocol/ERROR-CODES.md) §2). The server rewrites `updatedAtMs` and ignores any caller-supplied value.

### 5.1.2 Basis for Base Availability Validation (S6)

`base` is an open string, but create/update operations MUST validate that it is **registered** and otherwise return `422 haas_unsupported_base`. In S6, registration is determined by the adapters assembled into the current process (currently `codex` and the test-only `fake`), not by a hard-coded allowlist: adding an adapter makes its base available automatically. If `base` is registered but the adapter probe is not ready, creation may still succeed. `/v1/haas/ready?scope=execution` and `/v1/haas/status` report readiness; the registry does not mix runtime availability into configuration validation.

### 5.1.3 Harness Scope (S6)

`HarnessRecord` carries `tenantId` / `workspaceId`, consistent with Security Boundary §8.3:

- On creation, record the calling principal's tenant/workspace.
- `list` / `get` / `update` / `delete` and `appName` resolution only match records within the caller's scope.
- Cross-scope access to `/v1/haas/harnesses/{id}` returns `404 haas_harness_not_found`; cross-scope `appName` resolution returns `404 app_not_found`.
- A record whose scope fields are `None` is considered tenant-unbound and is visible only to an equally unbound principal. This preserves single-node deployments and existing seed records.

### 5.2 Internal API

```python
async def resolve_app(principal, app_name: str) -> HarnessConfig: ...
async def resolve_default_app(principal) -> HarnessConfig: ...
async def resolve_model(harness: HarnessConfig, requested_model: str | None) -> ModelResolution: ...
async def snapshot_for_session(harness: HarnessConfig) -> EffectiveHarnessConfig: ...
async def validate_harness_config(input: HarnessCreate) -> ValidationResult: ...
async def list_bases(principal) -> list[HarnessBase]: ...
async def resolve_provider_route(harness: HarnessConfig, model: str) -> ModelRoute: ...
```

## 6. Data Model

### 6.1 Harness (ADK app)

```json
{
  "id": "chrn_codex_default",
  "object": "harness",
  "name": "Codex default",
  "base": "codex",
  "baseLabel": "Codex",
  "defaultModel": "gpt-5.6-terra",
  "systemPrompt": "",
  "mcpServers": [],
  "skills": [],
  "disabledTools": [],
  "provider": {
    "name": "openai-compatible",
    "baseUrl": "https://provider.example.com/v1",
    "wireApi": "responses",
    "credentialRef": "secret://tenant/workspace/provider/default",
    "credentialFingerprint": "sha256:abc",
    "allowlistRuleId": "allow_provider_default"
  },
  "maxStep": 40,
  "timeoutSeconds": 900,
  "adapterCapabilities": {
    "streaming": true,
    "sessionContinuation": true,
    "cancellation": true,
    "hardToolDisable": false,
    "mcp": true,
    "skills": true,
    "files": true
  },
  "createdAtMs": 1786400000000,
  "updatedAtMs": 1786400000000
}
```

### 6.2 HarnessBase

```json
{
  "id": "codex",
  "object": "harness.base",
  "label": "Codex",
  "status": "ready",
  "adapter": "codex-app-server",
  "defaultModel": "gpt-5.6-terra",
  "capabilities": {
    "streaming": true,
    "sessionContinuation": true,
    "cancellation": true,
    "mcp": true,
    "skills": true,
    "toolRestriction": "advisory"
  }
}
```

### 6.3 MCP Server

```json
{
  "name": "repo",
  "url": "https://mcp.example.com/mcp",
  "transport": "http",
  "enabled": true,
  "headers": {
    "X-Trace-ID": "{request.headers.x-haas-trace-id}"
  },
  "auth": {
    "type": "secret_ref",
    "ref": "secret://tenant/provider/repo"
  }
}
```

### 6.4 Skill Bundle

```json
{
  "id": "skill_repo_rules",
  "name": "repo-rules",
  "enabled": true,
  "files": [
    {
      "path": "SKILL.md",
      "content": "Skill instructions..."
    }
  ]
}
```

`files[].path` MUST be a relative path and MUST NOT contain `..`, an absolute path, or a symlink escape.

## 7. Runtime Model and State Machine

```text
draft -> validated -> active -> superseded -> deleted
           |             |
           |             +-> snapshotted into session
           v
        rejected
```

Rules:

- Any mutable field may be changed in `draft`.
- `validated` means that the schema, adapter base, provider URL, MCP URL, skill bundle, and policy have passed validation.
- An `active` harness may be used by sessions; `appName` resolution matches only active harnesses.
- `superseded` preserves history and is no longer selected by default for new sessions.
- `deleted` cannot be selected for a new task, but historical sessions remain auditable.

Sessions use a frozen `EffectiveHarnessConfig` snapshot and do not read the live harness object while continuing an existing task.

## 8. Security and Authorization

- The registry stores only credential references, fingerprints, and safe metadata; it MUST NOT store raw secrets.
- Provider and MCP URLs MUST pass allowlist and SSRF validation before they may enter an active harness.
- Harnesses in different tenants/workspaces MUST NOT be mutually readable. Unauthorized access uniformly returns `404 haas_harness_not_found` (and app resolution returns `404 app_not_found`).
- Skill files reject path traversal, absolute paths, control characters, and oversized bundles.
- Enforcement of `disabledTools` MUST be reported accurately per base as `hard`, `advisory`, or `unsupported`.

## 9. Observability

The registry MUST emit the following safe logs/metrics:

- `haas.harness.created`
- `haas.harness.updated`
- `haas.harness.deleted`
- `haas.harness.validation_failed`
- `haas.harness.model_fallback`
- `haas.harness.provider_route_resolved`
- `haas.harness.base_unavailable`

Log fields contain only id, base, model, capability, fingerprint, and a safe reason; they MUST NOT contain secrets or complete tool arguments.

## 10. Failure and Recovery

| Scenario | Behavior |
|------|------|
| Unsupported base | `422 haas_unsupported_base` |
| PUT attempts to change `id`/`base`/`createdAtMs` | `400 invalid_input`; no partial update |
| Harness does not exist or access is unauthorized | `404 haas_harness_not_found` |
| App does not exist or access is unauthorized | `404 app_not_found` |
| Model unavailable | `422 haas_model_unavailable`, or an explicit fallback recorded in metadata |
| Provider URL fails the allowlist | `haas_provider_source_invalid` |
| Skill bundle lacks `SKILL.md` | Configuration validation fails; activation is rejected |
| MCP URL fails the allowlist | `haas_mcp_source_invalid` |
| Registry store unavailable | Create/update fails closed; frozen sessions continue executing |
| Harness deleted | New tasks fail; historical sessions remain readable |

## 11. Test Plan and Acceptance Criteria

- Unit: appName resolution (id precedence / name fallback / multiple-match assertion), scope filtering, model fallback, provider URL allowlist, and skill path validation.
- Integration: `GET /list-apps`, `GET/POST/PUT/DELETE /v1/haas/harnesses`, and `GET /v1/haas/models`.
- Compatibility: ADK client `list-apps` returns an array; `/run` resolves both harness id and name.
- Security: two principals receive 404 when reading each other's harnesses; secret/credential references do not appear in responses.
- Regression: updating a harness name does not lose skill files; updating an active harness does not affect existing session snapshots.
