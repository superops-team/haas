# MCP / Tool / Skill Runtime 组件规格

[English](README.md) | **简体中文**

Status: Draft
Last reviewed: 2026-08-30
Related specs: [Harness Registry](../harness-registry/README.zh-CN.md), [Harness Adapter](../harness-adapter/README.zh-CN.md), [Security Boundary](../security-boundary/README.zh-CN.md)

## 1. 组件定位

MCP / Tool / Skill Runtime 负责把 configured harness 中声明的 MCP servers、skills、disabled tools 和 tool approval 策略转换为各 harness 可执行的运行时配置，并维护跨 harness 的能力声明。

## 2. 来源与依据

| 来源 | 采用内容 |
|------|----------|
| Harness Registry | `mcpServers`、`skills`、`disabledTools` 的配置 shape 与校验 |
| `mpa-codex-worker` MCP/skill specs | source freeze、runtime headers、Codex native MCP config、skill folder materialization |
| ADK 2.0 | `actions.artifactDelta`/skill 物化与 harness 能力声明 |
| OpenSandbox | sandbox 内 shell/file/MCP 能力、egress policy 和 credential vault |

## 3. 上游与下游关系

| 方向 | 对象 | 关系 |
|------|------|------|
| 上游 | Harness Registry | 配置校验和有效配置展开 |
| 上游 | Session Runtime | session 创建时冻结 tools/MCP/skills |
| 上游 | Harness Adapter | 获取 adapter-specific config materialization |
| 下游 | MCP servers | Streamable HTTP、SSE 或 stdio |
| 下游 | Skill Store | skill bundle 保存和 materialization |
| 下游 | Security Boundary | URL/header/secret/path validation |
| 下游 | Model Proxy | provider-specific tool schema transform |

## 4. 职责边界

负责：

- 校验 MCP server URL、transport、headers、auth ref、timeout、enabled flag。
- 运行 loopback MCP proxy（端口 `18081`）：harness 连 proxy 而不是直连 MCP server。
- 由 proxy 注入真实 MCP secret（经 Security Boundary 的 `resolve_secret` 解析），harness 不接触明文。
- 将 enabled MCP servers 转换为 adapter-specific config（指向 loopback proxy）。
- 保证 disabled MCP server 不被连接。
- 物化完整 skill folder，而不是只写 `SKILL.md`。
- 校验 skill path 不越界，二进制内容 byte-for-byte 保留。
- 维护 disabledTools 的语义：hard、advisory、unsupported。
- 记录 tool/MCP/skill capability 和降级事件。

不负责：

- 不保存真实 MCP credential 明文。
- 不直接调用模型。
- 不向上游暴露 harness-specific config file path。
- 不在不支持 hard-disable 的 harness 上谎称已强制禁用。

## 5. 核心接口

### 5.1 MCP Proxy（loopback，端口 `18081`）

| Method | Path | 用途 |
|--------|------|------|
| POST | `/mcp` | Streamable HTTP MCP relay（按 `Mcp-Proxy-Key` 或 session token 路由到目标 server） |
| GET | `/sse` | SSE transport relay |
| GET | `/health` | proxy liveness |
| GET | `/ready` | secret resolver 与 allowlist readiness |

proxy 只监听 `127.0.0.1:18081`，由 harness 在 sandbox 内经 loopback 访问；真实 MCP secret 由 proxy 注入，不进入 harness config/env。

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

## 6. 数据模型

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

## 7. 运行模型与状态机

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

- `required=false`：MCP unavailable does not fail the turn; event records degraded capability.
- `required=true`：MCP unavailable fails session preparation or turn start with `haas_mcp_unavailable`.

Skill requiredness:

- Enabled skill missing `SKILL.md` fails config validation with
  `422 haas_skill_source_invalid`；skill path 越界（`..`、绝对路径、控制字符）
  同样返回 `422 haas_skill_source_invalid`，整个 harness 创建/更新不生效。
- Skill file 内容支持 `content`（文本）或 `contentB64`（二进制），二进制必须
  byte-for-byte round-trip；读取端点为
  `GET /v1/haas/harnesses/{harness_id}/skills/{skill_id}/files`，越权与不存在
  统一返回 404。
- Runtime materialization failure fails session preparation unless adapter declares skills as advisory-only and the harness config accepts that degradation.

## 8. 安全与权限

- `headers` values may contain templated request values or secret refs; resolved values are never returned.
- MCP proxy 只监听 loopback；真实 MCP secret 只由 proxy 注入 outbound，harness 只见 loopback 地址和短 token。
- URL validation rejects unsupported schemes, private networks and disallowed hosts unless policy explicitly allows them.
- Skill paths cannot escape their skill root.
- Skill execution scripts are treated as executable code and must remain within declared skill bundle paths.
- Disabled tools are enforced through native config when available; instruction-only fallback must be visible in capability metadata.
- MCP calls must not log full arguments or raw results by default.

## 9. 可观测性

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

## 10. 失败与恢复

| 场景 | 行为 |
|------|------|
| MCP URL invalid | reject config with `haas_mcp_source_invalid` |
| MCP required unavailable | fail session/turn preparation |
| MCP optional unavailable | run without it and emit degraded event |
| MCP proxy secret 解析失败 | proxy 返回 401；harness 侧回合按 adapter 语义失败 |
| Skill missing `SKILL.md` | reject config |
| Skill materialization partial write | remove partial directory and fail closed |
| Disabled tool unsupported | mark `advisory` or `unsupported`; do not claim hard enforcement |
| Header template missing value | reject turn before contacting MCP |

## 11. 测试计划与验收

- Unit：MCP config validation、header resolution、skill path validation、disabled tool mapping。
- Integration：adapter-specific config rendering for Codex, Pi and OpenCode fixtures。
- Security：path traversal, secret header redaction, disabled source not contacted。
- Compatibility：skill folder round-trip test；MCP unavailable degradation test。
- E2E：Codex session with one mock MCP server proves tool discovery and safe event projection。
