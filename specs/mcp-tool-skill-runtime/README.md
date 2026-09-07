# MCP / Tool / Skill Runtime Component Specification

**English** | [简体中文](README.zh-CN.md)

Status: Draft
Last reviewed: 2026-08-30
Related specs: [Harness Registry](../harness-registry/README.md), [Harness Adapter](../harness-adapter/README.md), [Security Boundary](../security-boundary/README.md)

## 1. Component Role

The MCP / Tool / Skill Runtime converts the MCP servers, skills, disabled tools, and tool approval policies declared in a configured harness into executable runtime configuration for each harness, and maintains cross-harness capability declarations.

## 2. Sources and Rationale

| Source | Adopted Content |
|--------|-----------------|
| Harness Registry | Configuration shape and validation for `mcpServers`, `skills`, and `disabledTools` |
| `mpa-codex-worker` MCP/skill specs | Source freezing, runtime headers, Codex native MCP configuration, and skill-folder materialization |
| ADK 2.0 | `actions.artifactDelta`/skill materialization and harness capability declarations |
| OpenSandbox | Shell/file/MCP capabilities in the sandbox, egress policy, and credential vault |

## 3. Upstream and Downstream Relationships

| Direction | Component | Relationship |
|-----------|-----------|--------------|
| Upstream | Harness Registry | Configuration validation and effective configuration expansion |
| Upstream | Session Runtime | Freezes tools/MCP/skills when creating a session |
| Upstream | Harness Adapter | Obtains adapter-specific configuration materialization |
| Downstream | MCP servers | Streamable HTTP, SSE, or stdio |
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
async def render_adapter_tool_config(adapter: str, config: EffectiveHarnessConfig) -> AdapterToolConfig: ...
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
  -> validate MCP/skills/tools
  -> active harness saved
  -> session created
  -> effective MCP/skills/tools frozen
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
| Disabled tool unsupported | Mark `advisory` or `unsupported`; do not claim hard enforcement |
| Header template value missing | Reject the turn before contacting MCP |

## 11. Test Plan and Acceptance Criteria

- Unit: MCP configuration validation, header resolution, skill path validation, and disabled-tool mapping.
- Integration: adapter-specific configuration rendering for Codex, Pi, and OpenCode fixtures.
- Security: path traversal, secret-header redaction, and verification that a disabled source is not contacted.
- Compatibility: skill-folder round-trip test; MCP-unavailable degradation test.
- E2E: a Codex session with one mock MCP server proves tool discovery and safe event projection.
