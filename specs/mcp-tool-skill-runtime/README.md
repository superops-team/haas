# MCP / Tool / Skill Runtime Component Specification

**English** | [简体中文](README.zh-CN.md)

Status: Draft
Last reviewed: 2026-09-10
Related specs: [Harness Registry](../harness-registry/README.md), [Harness Profile](../harness-profile/README.md), [Harness Adapter](../harness-adapter/README.md), [Security Boundary](../security-boundary/README.md)

## 1. Component Role

The MCP / Tool / Skill Runtime converts the MCP servers, skills, AGENTS.md,
disabled tools, and tool approval policies declared in the active
`HarnessProfile` into executable runtime configuration for each harness, and
maintains cross-harness capability declarations.

## 2. Sources and Rationale

| Source | Adopted Content |
|--------|-----------------|
| Harness Profile | Configuration shape, versioning, and validation for `mcpServers`, `skills`, `agentsMd`, and `disabledTools` |
| `mpa-codex-worker` MCP/skill specs | Source freezing, runtime headers, Codex native MCP configuration, and skill-folder materialization |
| ADK 2.0 | `actions.artifactDelta`/skill materialization and harness capability declarations |
| OpenSandbox | Shell/file/MCP capabilities in the sandbox, egress policy, and credential vault |

## 3. Upstream and Downstream Relationships

| Direction | Component | Relationship |
|-----------|-----------|--------------|
| Upstream | Harness Profile / Harness Registry | Configuration validation, versioning, and effective configuration expansion |
| Upstream | Session Runtime | Freezes tools/MCP/skills/AGENTS.md when creating a session or explicitly rebinding |
| Upstream | Harness Adapter | Obtains adapter-specific configuration materialization |
| Downstream | MCP servers | Brokered Streamable HTTP or SSE in P0; stdio is unsupported until its process contract exists |
| Downstream | Skill Store | Stores and materializes skill bundles |
| Downstream | Security Boundary | URL/header/secret/path validation |
| Downstream | Model Proxy | Provider-specific tool schema transforms |

## 4. Responsibility Boundaries

Responsibilities:

- Validate MCP server URLs, transports, headers, auth refs, timeouts, and enabled flags.
- Run the loopback MCP proxy (port `18081`): the harness connects to the proxy rather than directly to an MCP server.
- Have the proxy inject the real MCP secret (resolved through Security Boundary `resolve_secret`), so the harness does not access plaintext.
- Convert enabled MCP servers into adapter-specific configuration that points to the loopback proxy.
- Ensure that disabled MCP servers are not contacted.
- Materialize the complete skill folder rather than writing only `SKILL.md`.
- Materialize AGENTS.md sources from the profile snapshot and include their
  fingerprint in `agentsMdVersion`.
- Validate that skill paths do not escape their boundary and preserve binary content byte-for-byte.
- Maintain disabledTools semantics: hard, advisory, or unsupported.
- Record tool/MCP/skill capabilities and degradation events.

Non-responsibilities:

- Does not store real MCP credentials in plaintext.
- Does not call models directly.
- Does not expose harness-specific configuration file paths upstream.
- Does not falsely claim enforced disablement on a harness that does not support hard-disable.

## 5. Core Interfaces

### 5.1 MCP Proxy (Loopback, Port `18081`)

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/mcp` | Streamable HTTP MCP relay (routes to the target server by `Mcp-Proxy-Key` or session token) |
| GET | `/sse` | SSE transport relay |
| GET | `/health` | Proxy liveness |
| GET | `/ready` | Secret resolver and allowlist readiness |

The proxy listens only on `127.0.0.1:18081` and is accessed by the harness through loopback from within the sandbox. The proxy injects real MCP secrets; they do not enter harness config/env.

### 5.2 Internal API

```python
async def validate_mcp_server(server: McpServerConfig, policy: Policy) -> ValidationResult: ...
async def resolve_mcp_headers(server: McpServerConfig, ctx: RequestContext) -> ResolvedHeaders: ...
async def materialize_skills(session: SessionRecord, skills: list[SkillBundle]) -> SkillMaterialization: ...
async def materialize_agents_md(session: SessionRecord, agents_md: AgentsMdConfig) -> AgentsMdMaterialization: ...
async def render_adapter_tool_config(adapter: str, profile: EffectiveHarnessProfile) -> AdapterToolConfig: ...
async def enforce_disabled_tools(adapter: str, disabled: list[str]) -> ToolRestrictionResult: ...
async def probe_mcp_server(server: McpServerConfig) -> McpProbeResult: ...
async def relay_mcp_request(route: McpRoute, request: McpWireRequest) -> McpWireResponse: ...
```

## 6. Data Model

### 6.1 McpServerConfig

```json
{
  "name": "repo",
  "url": "https://mcp.example.com/mcp",
  "transport": "http",
  "enabled": true,
  "headers": {
    "X-Request-ID": "{request.traceId}"
  },
  "auth": {
    "type": "secret_ref",
    "ref": "secret://tenant/workspace/mcp/repo"
  },
  "timeoutSeconds": 60,
  "required": false
}
```

### 6.1.1 Built-In Manager Cowork Recall Source

Local Manager-backed Codex sessions include one reserved built-in source:

```json
{
  "name": "manager-cowork-recall",
  "url": "http://127.0.0.1:<manager-port>/mcp/cowork-recall",
  "transport": "http",
  "enabled": true,
  "required": false,
  "haas_builtin": true,
  "headers": {
    "X-HaaS-Session-ID": "hsess_abc",
    "X-HaaS-Recall-Token": "<session-scoped-random-token>"
  },
  "tools": {
    "recall": {
      "description": "Recall scoped Cowork memories and recent session history."
    }
  }
}
```

`manager-cowork-recall` is not a caller-supplied external MCP server. It is a
loopback source owned by the supervising Manager and scoped to the active HaaS session
with a random binding-local recall token.
The only exposed tool is `recall(query?: string, limit?: int)`, which returns bounded
structured results from Cowork's memory database and retained session transcript. It is
the model-facing recovery path for interrupted or restarted HaaS/Codex work: Codex can
ask for relevant prior context instead of Manager rewriting user prompts or relying on
language-specific trigger words.

The source is optional. Its URL is non-secret, but the recall token is a scoped bearer
capability and MUST NOT be logged, projected, or persisted outside the Manager binding and
active HaaS profile. It must not return raw tool arguments, host paths,
credentials, full command output, or hidden prompts. For local Codex, HaaS may materialize
this one built-in source before the general external MCP materialization work is complete;
all other caller-provided MCP sources continue to follow the existing validation and
support gates.
For retry/recovery after a failed, interrupted, or process-lost turn, the returned
transcript MUST include any latest HaaS bridge checkpoint that has not yet been committed
as a normal assistant transcript message. Such recovered rows expose only safe projection
fields, including bounded command and output previews, status, exit code, safe reason,
task outcome and model-stage summaries.

### 6.1.2 Built-In Browser Harness

OpenHarness includes one reserved built-in browser capability. It is not a
user-supplied connector, MCP server, or personal Chrome attachment. The default
runtime is an app-owned managed browser harness with:

- an isolated user data directory under the OpenHarness state directory,
- a controlled Chromium/Chrome-for-Testing executable selected by the runtime,
- no import of the user's personal Chrome profile, cookies, passwords, or
  extensions,
- bounded CDP/browser health checks before every tool action,
- one automatic runtime rebuild when the browser context, page, or CDP session
  has crashed or closed,
- structured state visible through `/v1/browser/state`, including `status`,
  `last_action`, `last_result`, and a safe `last_error`.
- packaged desktop builds include the Playwright driver and controlled browser
  runtime under the bundled sidecar resources.

The Manager MUST NOT default to launching `/Applications/Google Chrome.app` or
any other user browser profile for agent browser tools. A developer override may
name a browser executable explicitly, but it still uses the app-owned profile and
must be reflected in local diagnostics. Browser warmup is optional and MUST NOT
block overall service readiness.

Browser tool names remain stable for the Manager connector surface:
`browser_open_url`, `browser_read_page`, `browser_click`, `browser_type`,
`browser_select`, `browser_upload_file`, `browser_wait`,
`browser_screenshot`, and `browser_close`. The implementation may route these
calls through Browser Harness helper/CLI logic internally, but raw CDP endpoints,
profile paths, cookies, Authorization headers, and full screenshots are never
written to model-visible logs.

Acceptance requires a source-tree smoke and a packaged-app smoke that call the
browser screenshot endpoint from an isolated state directory and observe
`browser=managed_chromium`, `managed=true`, an app-owned profile, and a PNG data
URL. Missing bundled Chromium, missing Playwright driver resources, or fallback
to personal Chrome are release blockers.


### 6.2 SkillBundle

```json
{
  "id": "skill_repo_rules",
  "name": "repo-rules",
  "enabled": true,
  "files": [
    {
      "path": "SKILL.md",
      "content": "Skill instructions..."
    },
    {
      "path": "references/policy.md",
      "content": "..."
    }
  ],
  "fingerprint": "sha256:abc"
}
```

### 6.2.1 AgentsMdConfig

```json
{
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
}
```

AGENTS.md uses the same path-safety, secret-scan, and fingerprint rules as skill
bundles, but it is not a skill. P0 supports only `mode=snapshot`; dynamic reload is
P1/spec-only.

### 6.3 ToolRestrictionResult

```json
{
  "tool": "bash",
  "requested": "deny",
  "enforcement": "hard",
  "adapterId": "opencode",
  "safeReason": "native_permission_config"
}
```

## 7. Runtime Model and State Machine

```text
harness config submitted
  -> validate profile MCP/skills/AGENTS.md/tools
  -> active profile saved
  -> session created
  -> effective MCP/skills/AGENTS.md/tools frozen
  -> adapter-specific materialization
  -> optional MCP capability probe
  -> turn executes
```

MCP requiredness:

- `required=false`: MCP unavailability does not fail the turn; an event records the degraded capability.
- `required=true`: MCP unavailability fails session preparation or turn start with `haas_mcp_unavailable`.

Skill requiredness:

- If an enabled skill is missing `SKILL.md`, configuration validation fails with `422 haas_skill_source_invalid`. A skill path that escapes its boundary (`..`, an absolute path, or control characters) also returns `422 haas_skill_source_invalid`, and the entire harness create/update operation does not take effect.
- Skill file content supports `content` (text) or `contentB64` (binary). Binary content MUST round-trip byte-for-byte. The read endpoint is `GET /v1/haas/harnesses/{harness_id}/skills/{skill_id}/files`; unauthorized and nonexistent resources both return 404.
- Runtime materialization failure fails session preparation unless the adapter declares skills advisory-only and the harness configuration accepts that degradation.

### 7.1 Snapshot Content and Application

P0 MCP supports brokered HTTP and SSE only. stdio is not accepted until a command/args/isolated-env/process-lifecycle contract exists. Required/optional applies to actual upstream discovery, not a fabricated local relay URL. The trusted external broker injects real credentials; worker loopback relays see only scoped runtime tokens.

Each skill file contains exactly one of content, contentB64, or contentRef. contentRef and AGENTS.md source contentRef are immutable caller-owned `file_...` ids from the existing upload endpoint. Resolve ownership, bytes, size, encoding and digest before accepting the profile. Pin content for every applied/pending session; profile history cleanup cannot remove it. No Manager-local artifact URI or unvalidated remote URL is accepted.

Materialize into isolated revision directories, verify bytes, then switch the active generation only after native readback. Never overwrite the bind-mounted project's AGENTS.md. The adapter must disable implicit mutable instruction/skill/config discovery or provide an isolated snapshot view at the same logical workspace paths. Global/workspace rule ordering is explicit (global before workspace); directory-scoped rules and uncontrolled nested discovery are unsupported in P0. If the pinned harness cannot enforce this source boundary, configuration validation fails rather than claiming snapshot isolation. File changes create a higher profile revision and delegated `/policy` applies it; automatic in-turn file watching is not enabled.

Applying a profile refreshes model routes, MCP connections, skills and instructions together. Disconnect revoked MCP sources, revoke old generation tokens, and restart/resume the native runtime when reload cannot be verified. Preserve native conversation on the session volume. Failed application leaves the old applied revision and gates queued turns; do not silently mix old MCP with new instructions.

## 8. Security and Permissions

- `headers` values MAY contain templated request values or secret refs; resolved values are never returned.
- The MCP proxy listens only on loopback. Only the proxy injects real MCP secrets outbound; the harness sees only the loopback address and a short-lived token.
- URL validation reuses Policy Controller network authorization rather than
  duplicating SSRF logic. Unsupported schemes, private, loopback, link-local,
  reserved, metadata, and otherwise disallowed hosts are rejected by default
  unless the frozen network policy explicitly allowlists the normalized target.
- Skill paths MUST NOT escape their skill root.
- Skill execution scripts are treated as executable code and MUST remain within declared skill bundle paths.
- Disabled tools are enforced through native configuration where available; instruction-only fallback MUST be visible in capability metadata.
- MCP calls MUST NOT log full arguments or raw results by default.

## 9. Observability

Events/logs:

- `haas.mcp.config_validated`
- `haas.mcp.source_degraded`
- `haas.mcp.source_required_failed`
- `haas.skill.materialized`
- `haas.skill.materialization_failed`
- `haas.tool.restriction_applied`
- `haas.tool.restriction_advisory`

Metrics:

- `haas_mcp_sources_total{status,transport}`
- `haas_mcp_probe_duration_ms{status}`
- `haas_mcp_proxy_request_total{status}`
- `haas_skill_materialization_total{status}`
- `haas_tool_restriction_total{enforcement,status}`

## 10. Failure and Recovery

| Scenario | Behavior |
|----------|----------|
| MCP URL invalid | Reject configuration with `haas_mcp_source_invalid` |
| Required MCP unavailable | Fail session/turn preparation |
| Optional MCP unavailable | Run without it and emit a degraded event |
| MCP proxy secret resolution fails | Proxy returns 401; the harness-side turn fails according to adapter semantics |
| Skill missing `SKILL.md` | Reject configuration |
| Partial write during skill materialization | Remove the partial directory and fail closed |
| Partial write during AGENTS.md materialization | Remove the partial directory and fail closed |
| Disabled tool unsupported | Mark `advisory` or `unsupported`; do not claim hard enforcement |
| Header template value missing | Reject the turn before contacting MCP |

## 11. Test Plan and Acceptance Criteria

- Unit: MCP configuration validation, header resolution, skill path validation, and disabled-tool mapping.
- Unit: AGENTS.md source validation, snapshot fingerprints, and negative secret/path cases.
- Integration: adapter-specific configuration rendering for Codex, Pi, and OpenCode fixtures.
- Security: path traversal, secret-header redaction, AGENTS.md content redaction, and verification that a disabled source is not contacted.
- Compatibility: skill-folder round-trip test; MCP-unavailable degradation test.
- E2E: a Codex session with one mock MCP server proves tool discovery and safe event projection.
