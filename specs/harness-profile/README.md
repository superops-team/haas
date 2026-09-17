# Harness Profile Component Specification

**English** | [简体中文](README.zh-CN.md)

Status: Draft
Last reviewed: 2026-09-14
Change ID: harness-profile-versioned-config, unified-runtime-approval-policy
Related specs: [HaaS Protocol](../haas-protocol/README.md), [Harness Registry](../harness-registry/README.md), [Session Runtime](../session-runtime/README.md), [MCP / Tool / Skill Runtime](../mcp-tool-skill-runtime/README.md), [Policy Controller](../policy-controller/README.md), [Manager HaaS Sidecar Backend](../manager-haas-sidecar-backend/README.md)

## 1. Component Role

Harness Profile is the versioned runtime-configuration contract shared by all
harnesses. It groups provider/model, MCP servers, skills, AGENTS.md, workspace,
tool restrictions, approval policy, budgets, and metadata into one configuration
unit that can be validated, activated, snapshotted, rebound, and audited.

`Harness` is the ADK app / configured harness identity. `HarnessProfile` is the
current or historical executable configuration for that app. Upstream callers
still select a harness through `appName`; when a new session is created, HaaS
resolves that harness's active profile revision and freezes it as an
`EffectiveHarnessProfile`.

## 2. Sources and Rationale

| Source | Adopted content |
|--------|-----------------|
| Harness Registry | Configured harness identity, active/retired lifecycle, and caller scope |
| Session Runtime | Effective configuration is frozen at session creation; existing sessions do not drift after configuration updates |
| MCP / Tool / Skill Runtime | MCP proxy, skill-folder materialization, and disabled-tool enforcement |
| Policy Controller | Layered workspace/network/tool/approval/model policy merging and unauthorized-widening rejection |
| Manager HaaS Sidecar Backend | Manager settings, session binding, profile drift UI, and explicit rebind |

## 3. Upstream and Downstream Relationships

| Direction | Component | Relationship |
|-----------|-----------|--------------|
| Upstream | HaaS Protocol | Exposes profile CRUD, validation, activation, and session rebind APIs |
| Upstream | Harness Registry | Maintains harness identity and active-profile pointers |
| Upstream | Manager | Submits manager-effective configuration intent and reads profile fingerprints for drift detection |
| Downstream | Session Runtime | Freezes `EffectiveHarnessProfile` at session creation or explicit rebind |
| Downstream | MCP / Tool / Skill Runtime | Validates and materializes MCP, skills, AGENTS.md, and tool restrictions |
| Downstream | Policy Controller | Compiles workspace, network, tool, approval, and model policy |
| Downstream | Model Proxy | Resolves provider routes and credential references |

## 4. Responsibility Boundaries

Responsibilities:

- Define profile as a first-class configuration object for provider, MCP, skills,
  AGENTS.md, workspace/policy, and budget settings.
- Manage forward-only profile revisions; activating old configuration requires copying it into a higher version, never moving the active pointer backward.
- Generate a stable `profileFingerprint` used by capability discovery, session
  snapshots, manager bindings, and drift detection.
- Validate schema, security, policy, MCP, skill, AGENTS.md, and provider routes
  before activation.
- Provide `EffectiveHarnessProfile` for session creation and define the dynamic
  update boundary for existing sessions.
- Record profile lifecycle audit events without leaking secrets, host paths, raw
  prompts, or complete tool payloads.

Non-responsibilities:

- Does not execute harnesses, MCP calls, tools, or model requests.
- Does not store raw credentials; stores only `credentialRef` and fingerprints.
- Does not directly read or write user workspace files. AGENTS.md content is
  supplied through manager-authorized input or a controlled sandbox read flow.
- Does not implicitly mutate existing sessions, delegated sessions, or native
  harness threads after a profile update.

## 5. Core Interfaces

### 5.1 HaaS Native Profile API

| Method | Path | Description |
|--------|------|-------------|
| GET | `/v1/haas/profiles` | Lists profile revisions in caller scope, filterable by `harnessId` and `status` |
| POST | `/v1/haas/profiles` | Creates a new draft profile revision (supports `Idempotency-Key`) |
| GET | `/v1/haas/profiles/{profile_id}` | Reads one profile revision |
| PUT | `/v1/haas/profiles/{profile_id}` | Replaces mutable draft fields; non-draft revisions return `409 haas_profile_conflict` |
| POST | `/v1/haas/profiles/{profile_id}/validate` | Performs side-effect-free validation and returns typed findings |
| POST | `/v1/haas/profiles/{profile_id}/activate` | Sets a validated profile revision as the harness's active profile |
| POST | `/v1/haas/sessions/{session_id}/profile-rebind` | Explicitly rebinds future turns in an existing session to a profile snapshot |

All endpoints are HaaS native extensions. ADK-compatible `/run` does not add
top-level fields; optional `haas.profileId` / `haas.profileVersion` may pin a
profile only for a **new session**. If an existing session's frozen profile differs
from the requested profile, HaaS returns `409 haas_profile_rebind_required`; the
caller must use native rebind or create a new session.

### 5.2 Internal API

```python
async def create_profile(principal, input: HarnessProfileCreate) -> HarnessProfile: ...
async def validate_profile(profile_id: str) -> ProfileValidation: ...
async def activate_profile(harness_id: str, profile_id: str) -> HarnessRecord: ...
async def resolve_active_profile(principal, harness_id: str) -> HarnessProfile: ...
async def snapshot_effective_profile(profile: HarnessProfile, ctx: SessionContext) -> EffectiveHarnessProfile: ...
async def rebind_session_profile(session_key: SessionKey, request: ProfileRebindRequest) -> EffectiveHarnessProfile: ...
```

## 6. Data Model

### 6.1 HarnessProfile

```json
{
  "id": "hprof_abc",
  "object": "harness_profile",
  "harnessId": "chrn_codex_default",
  "base": "codex",
  "version": 12,
  "status": "draft",
  "name": "Codex default profile",
  "provider": {
    "providerId": "volcengine-ark",
    "name": "volcengine-ark",
    "model": "doubao-seed-1-6",
    "credentialRef": "secret://tenant/workspace/provider/default",
    "credentialFingerprint": "sha256:abc",
    "baseUrl": "https://ark.cn-beijing.volces.com/api/v3",
    "wireApi": "openai-compatible",
    "apiType": "responses"
  },
  "mcpServers": [],
  "skills": [],
  "agentsMd": {
    "mode": "snapshot",
    "sources": [
      {
        "scope": "workspace",
        "path": "AGENTS.md",
        "contentRef": "file_agents_md",
        "fingerprint": "sha256:def"
      }
    ],
    "maxBytes": 262144
  },
  "workspace": {
    "mode": "bind_mount",
    "roots": [{ "containerPath": "/workspace", "access": "rw" }]
  },
  "policy": {
    "network": { "defaultAction": "allow", "allow": [] },
    "tools": { "disabled": [], "approvalMode": "on-request" },
    "model": { "allowedModels": ["doubao-seed-1-6"], "fallbackModel": null }
  },
  "budget": {
    "maxStep": 40,
    "timeoutSeconds": 86400,
    "maxOutputTokens": 4096
  },
  "profileFingerprint": "sha256:profile",
  "createdAtMs": 1786400000000,
  "updatedAtMs": 1786400000000,
  "activatedAtMs": null
}
```

`provider` follows the `ProviderRoute` contract (see
[Harness Registry](../harness-registry/README.md) 6.5 and
[Model Proxy](../model-proxy/README.md) 6.1). `providerId` is the stable provider
identity; `name` is a human-readable alias and both are retained. `wireApi`
declares the request/stream protocol: `openai-compatible` means the provider
speaks the OpenAI-compatible protocol family, `responses` requires the OpenAI
Responses protocol type within that family, and `agent-plan` targets the Ark Agent
Plan protocol. Raw credentials never appear; only `credentialRef` and
`credentialFingerprint` are stored.

### 6.2 EffectiveHarnessProfile

`EffectiveHarnessProfile` is the frozen snapshot used for session/turn execution:

```json
{
  "profileId": "hprof_abc",
  "profileVersion": 12,
  "profileFingerprint": "sha256:profile",
  "harnessId": "chrn_codex_default",
  "base": "codex",
  "providerFingerprint": "sha256:provider",
  "mcpVersion": "sha256:mcp",
  "skillsVersion": "sha256:skills",
  "agentsMdVersion": "sha256:agents-md",
  "workspacePolicyVersion": "sha256:workspace-policy",
  "resolvedAtMs": 1786400000000
}
```

The complete provider route, MCP headers, skill bytes, AGENTS.md content, and
policy enter only internal snapshots/materialization. They MUST NOT enter ADK
Session output, capability responses, or manager-visible bindings. Public surfaces
return only fingerprints, versions, safe names, and degraded reasons.

### 6.3 AGENTS.md Source

`agentsMd.sources[].scope` allows only:

- `global`: manager/user global rules;
- `workspace`: the project-root `AGENTS.md`;
- `directory`: future closer-directory rules; P0 may advertise this as unsupported.

Rules:

- `path` MUST be relative and MUST NOT contain `..`, absolute paths, control
  characters, or symlink escapes.
- `contentRef` points to a HaaS artifact/profile object, not a host absolute path
  or presigned URL.
- `mode=snapshot` freezes AGENTS.md content when a session is created. Later file
  changes affect only new profile revisions.
- `mode=dynamic` is P1/spec-only and MUST NOT be enabled until reload, conflict,
  audit, and prompt-injection controls are defined.

## 7. Runtime Model and State Machine

A profile is simply a versioned configuration record. The lifecycle is deliberately
minimal:

```text
draft -> active -> retired
```

Rules:

- Status is one of `draft` (editable), `active` (used by new sessions), and
  `retired` (a previous version kept for audit only).
- `version` is a per-harness integer that only increments. It is never rolled
  back. To reuse an older configuration, create a new version by copying that
  configuration; the new revision gets the next higher version number.
- Once a revision is activated it is immutable; activating a new revision marks the
  previous active revision `retired`. `retired` revisions are never re-activated.
- `validate` and `activate` are operations, not persisted statuses. `validate` is
  side-effect-free (no harness start, no external MCP contact, no credential read)
  and may perform static URL, policy, schema, skill-bundle, AGENTS.md, and
  adapter-capability compatibility checks. `activate` requires a revision that has
  passed validation.
- New sessions use the harness's active profile revision by default. Existing
  sessions continue using their frozen `EffectiveHarnessProfile`.
- A newly created OpenHarness interactive profile defaults to `workspace-write`, public-network
  `defaultAction=allow`, and `approvalMode=on-request`. Installations whose persisted profile
  predates these explicit fields perform a one-time schema migration that materializes these
  values and records the migration revision; later reads MUST NOT reinterpret omission as a
  changing default. Explicit user or administrator values are never overwritten.
- Existing session profile changes require explicit `profile-rebind` and affect
  only new invocations after the rebind. Running invocations are not modified.
- `GET /v1/haas/sessions/{sessionId}/profile` is the authoritative non-delegated
  recovery read. It returns only `profileId`, `profileVersion`,
  `profileFingerprint`, `harnessId`, `base`, and a credential-free execution-intent
  fingerprint. It never returns provider URLs, `credentialRef`, MCP headers, skill
  bytes, policy bodies, or the internal effective snapshot. ADK Session output is
  unchanged. A missing, ambiguous, or out-of-scope session returns `404
  session_not_found`; a delegated session returns `409
  haas_profile_rebind_unsupported` and uses delegated policy state instead.
- `profile-rebind` applies only to non-delegated sessions. A manager-delegated
  session (one that carries `delegatedSessionRef`) MUST reject `profile-rebind`
  with `409 haas_profile_rebind_unsupported`; its runtime configuration is owned
  by the manager-authorized mount manifest, delegation policy snapshot, and
  `profileRef`, and is changed only through
  `POST /v1/haas/delegated-sessions/{id}/policy`. That endpoint accepts a new
  complete `profileRef` and applies it in-place to the same delegated
  session and the same manager chat binding; the manager MUST NOT create a new
  session to change configuration.
- Rebind MUST NOT change session id, history, artifact visibility, or native
  session ownership. If the adapter cannot safely continue, it marks future
  invocation as `non_resumable` or requires a new session.

Activation revalidates the current content fingerprint under the per-harness transaction. Draft edits invalidate previous validation; validation itself does not make a draft immutable. The active version must advance; identical active activation retries are no-ops. Store complete non-secret resolved snapshots and pin content bytes for applied/pending sessions independently of profile-history retention.

`ProviderRoute` keeps required providerId/name/wireApi. `wireApi=responses` implies apiType=responses; openai-compatible and agent-plan require explicit apiType (responses or chat_completions). Never guess API type from hostname or silently switch protocols. Delegated configuration uses the application barrier, not profile-rebind.

### 7.1 Minimal Deployment and Management Override

- Multi-version retention is optional. A deployment that cannot retain history MAY
  keep only the single latest profile per harness; the version number still only
  increments across replacements.
- When a profile field conflicts with the value supplied by the management entry
  (for a delegated session, the manager-authorized mount manifest and delegation
  policy snapshot), the management value wins. The profile does not widen or
  override management-controlled configuration.

### 7.2 Local API Application

Manager local API pins a validated profile with haas.profileId/profileVersion at first use. Persist a deep copy of the complete non-secret effective snapshot privately, not in ADK state. The native rebind response exposes only profileId, profileVersion, profileFingerprint, harnessId, base, and resolvedAtMs; ADK Session output remains unchanged. Non-delegated profile-rebind accepts profileId and optional expectedProfileVersion (compare against the current applied version), waits for the current lease, and atomically replaces the snapshot under that lease. New invocations freeze the applied route; an update cannot modify an already running invocation. Retrying the same target is a no-op; older versions cannot replace newer ones. Idempotency-Key is scoped to principal, full session identity, and operation: matching retries replay the original response even after later updates, while changed bodies return haas_idempotency_conflict. Validate request types and supported materialization before replacing the snapshot. Rebind preserves session identity and native history. Unimplemented security-affecting materialization must reject rather than claim successful application. Delegated rebind remains prohibited.

## 8. Security and Permissions

- Profiles carry tenant/workspace scope like harnesses; cross-scope access returns 404.
- `credentialRef` MUST point to a resolvable secret in the caller scope; responses
  expose only fingerprints.
- An opaque credential handle is scoped runtime material and is excluded from the
  execution-intent fingerprint used for crash recovery. Reuse is limited to the
  same logical session and its current supervisor grant; it MUST NOT make a
  credential handle portable across sessions or supervisors.
- MCP and provider URLs MUST pass allowlist and SSRF validation.
- AGENTS.md and skill content MUST NOT contain secret-like strings; activation
  fails closed if they do.
- Profile fingerprints MUST NOT be computed from raw secrets; use credential
  fingerprints instead.
- Unknown security-affecting fields fail closed; non-security metadata belongs
  under `metadata`.

## 9. Observability

Events/logs:

- `haas.profile.created`
- `haas.profile.validation_failed`
- `haas.profile.activated`
- `haas.profile.retired`
- `haas.profile.rebind_requested`
- `haas.profile.rebind_applied`

Log fields include only profile id/version, harness id/base, fingerprints, safe
findings, and safe reasons.

## 10. Failure and Recovery

| Scenario | Behavior |
|----------|----------|
| Profile does not exist or is unauthorized | `404 haas_profile_not_found` |
| Updating a non-draft revision | `409 haas_profile_conflict` |
| Activating an unvalidated or deleted profile | `409 haas_profile_conflict` |
| Existing session requests a different profile | `409 haas_profile_rebind_required`; no invocation starts |
| Invalid AGENTS.md or skill source | `422 haas_skill_source_invalid` or `422 haas_agents_md_invalid` |
| Profile store unavailable | `503 haas_store_unavailable`; existing frozen sessions continue |
| Rebind attempts unauthorized policy/workspace/provider widening | Return the matching policy/security error and keep the old snapshot |

## 11. Test Plan and Acceptance Criteria

- Unit: stable profile fingerprints, append-only revisions, activate/retire,
  draft-only update, and scope 404.
- Unit: provider/MCP/skill/AGENTS.md/workspace/policy schema validation and
  negative secret/path/URL cases.
- Integration: a new session freezes the active profile; after profile update,
  new sessions use the new revision and existing sessions do not drift.
- Integration: explicit rebind on an existing session makes the next invocation
  use the new `EffectiveHarnessProfile`; running invocations are unaffected.
- Compatibility: `/list-apps` and `/run` still work by harness id/name and do not
  require ADK clients to understand profiles.
- Client: capabilities/harness/profile responses include `profileVersion` and
  `profileFingerprint`; Manager detects drift and presents rebind/new-session
  behavior.
- Security: public responses, events, bindings, and logs contain no raw
  credentials, host paths, AGENTS.md content, MCP headers, or complete tool
  payloads.
sions, activate/retire,
  draft-only update, and scope 404.
- Unit: provider/MCP/skill/AGENTS.md/workspace/policy schema validation and
  negative secret/path/URL cases.
- Integration: a new session freezes the active profile; after profile update,
  new sessions use the new revision and existing sessions do not drift.
- Integration: explicit rebind on an existing session makes the next invocation
  use the new `EffectiveHarnessProfile`; running invocations are unaffected.
- Compatibility: `/list-apps` and `/run` still work by harness id/name and do not
  require ADK clients to understand profiles.
- Client: capabilities/harness/profile responses include `profileVersion` and
  `profileFingerprint`; Manager detects drift and presents rebind/new-session
  behavior.
- Security: public responses, events, bindings, and logs contain no raw
  credentials, host paths, AGENTS.md content, MCP headers, or complete tool
  payloads.
