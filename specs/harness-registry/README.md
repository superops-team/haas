# Harness Registry Component Specification

**English** | [简体中文](README.zh-CN.md)

Status: Draft
Last reviewed: 2026-09-10
Related specs: [HaaS Protocol](../haas-protocol/README.md), [Harness Profile](../harness-profile/README.md), [Harness Adapter](../harness-adapter/README.md), [Security Boundary](../security-boundary/README.md)

## 1. Component Role

The Harness Registry maintains the catalog of configured harnesses that HaaS can
run. It answers which harnesses (ADK apps) a tenant or workspace may currently
select, which profile each harness has active, and whether those capabilities are
actually available.

In ADK terminology, `appName` is the configured harness `id` (`chrn_...`);
`/run` MAY also resolve the human-readable `name` as an alias. `base` is an open
string: the initial release uses `codex`, and future registration of `pi`,
`opencode`, `amp`, or another harness requires no change to the public task API.
Mutable provider, MCP, skills, AGENTS.md, workspace/policy, and budget
configuration belongs to [Harness Profile](../harness-profile/README.md). The
registry stores only harness identity, scope, base, and the active-profile
pointer.

## 2. Sources and Rationale

| Source | Adopted elements |
|--------|------------------|
| ADK 2.0 | `appName` = harness `id`; `/list-apps` lists app names |
| `mpa-codex-worker` profile controller | Profile draft/active lifecycle, session freezing, runtime policy |
| Harness Profile | Versioned provider/MCP/skills/AGENTS.md/workspace/policy/budget configuration |
| Model Proxy | Provider routing and model availability |
| Component overview | Configured harness catalog and appName/base/capability/model/provider discovery |
| Manager Delegation | Manager and HaaS both reference providers by provider id, model id, and `credentialRef`; raw keys are never copied into delegated-session contracts |

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

- Store stable configured harness identity and the active-profile pointer.
- Compute the effective view of harness, base, active profile, model, provider, MCP, skill, and tool restrictions.
- Resolve `appName`: first by exact `id`, then by `name`; return 404 for multiple matches or no match.
- Return harnesses within the caller's scope; return not found for cross-scope access.
- Resolve the active profile for session creation and pass the effective profile to Session Runtime for freezing; later profile activations MUST NOT change existing sessions.
- Check adapter availability for `base`.
- Delegate storage and validation of provider routes, MCP, skills, AGENTS.md, workspace/policy, and budgets to Harness Profile.
- Validate availability of the active profile's `defaultModel` and the requested `model`, or apply an explicit fallback.

Non-responsibilities:

- Does not execute a harness directly.
- Does not store raw credentials; it stores only references or delegates them to a secret store/vault.
- Does not execute MCP/tool calls.
- Does not modify the frozen profile/configuration of an existing session.
- Does not force a harness's native tool names into a hard contract shared by all harnesses.

## 5. Core Interfaces

### 5.1 Public API

| Method | Path | Description |
|--------|------|------|
| GET | `/list-apps` | Lists harness app names within the caller's scope (an array of id strings) |
| GET | `/v1/haas/capabilities` | Contributes caller-visible configured-harness snapshots to the protocol-owned capability response |
| GET | `/v1/haas/harnesses` | Lists configured harness details within the caller's scope |
| GET | `/v1/haas/harnesses/{harness_id}` | Reads one configured harness |
| POST | `/v1/haas/harnesses` | Creates a configured harness |
| PUT | `/v1/haas/harnesses/{harness_id}` | Replaces mutable configuration; `id`, `base`, and `createdAtMs` are immutable |
| DELETE | `/v1/haas/harnesses/{harness_id}` | Marks a harness as deleted without deleting historical sessions |
| GET/POST | `/v1/haas/profiles` | Owned by Harness Profile; Registry maintains harness lookup and active-pointer consistency |
| GET/PUT/POST | `/v1/haas/profiles/{profile_id}`, `/validate`, `/activate` | Owned by Harness Profile; activation atomically updates the harness active pointer |
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
async def resolve_active_profile(principal, harness: HarnessConfig) -> HarnessProfile: ...
async def snapshot_for_session(harness: HarnessConfig, profile: HarnessProfile) -> EffectiveHarnessProfile: ...
async def validate_harness_config(input: HarnessCreate) -> ValidationResult: ...
async def list_bases(principal) -> list[HarnessBase]: ...
async def resolve_provider_route(harness: HarnessConfig, model: str) -> ModelRoute: ...
async def capability_snapshot(principal, probes: dict[str, AdapterProbe]) -> list[HarnessCapabilitySnapshot]: ...
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
  "activeProfileId": "hprof_abc",
  "activeProfileVersion": 12,
  "activeProfileFingerprint": "sha256:profile",
  "defaultModel": "gpt-5.6-terra",
  "systemPrompt": "",
  "mcpServers": [],
  "skills": [],
  "disabledTools": [],
  "provider": {
    "providerId": "openai",
    "name": "openai",
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

`defaultModel`, `systemPrompt`, `mcpServers`, `skills`, `disabledTools`,
`provider`, `maxStep`, and `timeoutSeconds` are legacy-compatible projection
fields. The write source of truth for new implementations is Harness Profile.
Reads MAY inline an active profile summary for older manager clients, but those
fields MUST NOT bypass profile validation.

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

### 6.5 Provider Identity Catalog

Provider identities are stable configuration identities. They MUST NOT be conflated
when endpoints, credential source, billing region, or API shape differ:

| Provider id | Endpoint | Wire/API shape | Rule |
|-------------|----------|----------------|------|
| `ark` | `https://ark.ap-southeast.bytepluses.com/api/v3` | OpenAI-compatible data plane | BytePlus Ark global provider identity. |
| `volcengine-ark` | `https://ark.cn-beijing.volces.com/api/v3` | OpenAI-compatible data plane | Volcengine Ark China standard data-plane identity. |
| `ark-agent-plan-cn` | `https://ark.cn-beijing.volces.com/api/plan/v3` | Agent Plan API | Volcengine Ark Agent Plan identity; not interchangeable with the standard data plane. |

Manager-local execution and HaaS delegated execution both pass provider selection as
`providerId + model + credentialRef`. The provider route keeps both `providerId`
(stable identity) and `name` (human-readable alias). `wireApi` declares the protocol:
`openai-compatible` is the OpenAI-compatible protocol family, `responses` requires the
OpenAI Responses protocol type within that family, and `agent-plan` is the Ark Agent
Plan protocol. The registry stores the provider route and credential
reference/fingerprint only; the Model Proxy resolves real credentials at request time.

## 7. Runtime Model and State Machine

```text
draft -> active -> retired
```

Rules:

- `draft` may change mutable harness identity metadata; executable configuration
  fields use the versioned lifecycle owned by Harness Profile.
- `active` means harness identity, adapter base, and active-profile pointer have
  passed validation and may be used by sessions; `appName` resolution matches only
  active harnesses, and an active profile MUST exist.
- `retired` preserves history for audit and is no longer selected for new sessions.
  A retired harness is never re-activated; historical sessions remain readable.

Sessions use a frozen `EffectiveHarnessProfile` snapshot and do not read the live
harness or active-profile object while continuing an existing task. Capability
discovery is a live caller-scoped snapshot and MUST include the current active
profile version/fingerprint, but MUST NOT mutate or replace frozen session
configuration. Registry supplies only visible harness identity, active-profile
pointer, and configuration facts; HaaS Protocol owns the public schema, and
adapter/runtime components supply availability/mechanism/enforcement facts.

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
| Provider id does not match its endpoint/API shape | `haas_provider_source_invalid` |
| Skill bundle lacks `SKILL.md` | Configuration validation fails; activation is rejected |
| MCP URL fails the allowlist | `haas_mcp_source_invalid` |
| Active profile is missing or unvalidated | `409 haas_profile_conflict`; the harness cannot start new sessions |
| Session request asks for a profile that differs from the frozen snapshot | `409 haas_profile_rebind_required`; no invocation starts |
| Registry store unavailable | Create/update fails closed; frozen sessions continue executing |
| Harness deleted | New tasks fail; historical sessions remain readable |

## 11. Test Plan and Acceptance Criteria

- Unit: appName resolution (id precedence / name fallback / multiple-match assertion), scope filtering, active-profile pointers, model fallback, provider URL allowlist, and skill path validation.
- Integration: `GET /list-apps`, `GET /v1/haas/capabilities`, `GET/POST/PUT/DELETE /v1/haas/harnesses`, and `GET /v1/haas/models`; capability harnesses exactly match caller-visible active configured harnesses and include active profile version/fingerprint.
- Compatibility: ADK client `list-apps` returns an array; `/run` resolves both harness id and name.
- Security: two principals receive 404 when reading each other's harnesses; secret/credential references do not appear in responses.
- Regression: updating a harness name does not lose the active-profile pointer; activating a new profile does not affect existing session snapshots unless explicit rebind is used.
