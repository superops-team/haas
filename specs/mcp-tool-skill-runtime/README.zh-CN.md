# MCP / Tool / Skill Runtime 组件规格

[English](README.md) | **简体中文**

Status: Draft
Last reviewed: 2026-09-10
Related specs: [Harness Registry](../harness-registry/README.zh-CN.md), [Harness Profile](../harness-profile/README.zh-CN.md), [Harness Adapter](../harness-adapter/README.zh-CN.md), [Security Boundary](../security-boundary/README.zh-CN.md)

## 1. 组件定位

MCP / Tool / Skill Runtime 负责把 active `HarnessProfile` 中声明的 MCP servers、
skills、AGENTS.md、disabled tools 和 tool approval 策略转换为各 harness 可执行的
运行时配置，并维护跨 harness 的能力声明。

## 2. 来源与依据

| 来源 | 采用内容 |
|------|----------|
| Harness Profile | `mcpServers`、`skills`、`agentsMd`、`disabledTools` 的配置 shape、版本和校验 |
| `mpa-codex-worker` MCP/skill specs | source freeze、runtime headers、Codex native MCP config、skill folder materialization |
| ADK 2.0 | `actions.artifactDelta`/skill 物化与 harness 能力声明 |
| OpenSandbox | sandbox 内 shell/file/MCP 能力、egress policy 和 credential vault |

## 3. 上游与下游关系

| 方向 | 对象 | 关系 |
|------|------|------|
| 上游 | Harness Profile / Harness Registry | 配置校验、版本和有效配置展开 |
| 上游 | Session Runtime | session 创建或显式 rebind 时冻结 tools/MCP/skills/AGENTS.md |
| 上游 | Harness Adapter | 获取 adapter-specific config materialization |
| 下游 | MCP servers | P0 为 brokered Streamable HTTP/SSE；stdio 等进程合同完成后再支持 |
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
- 物化 profile snapshot 中的 AGENTS.md sources，并把其 fingerprint 计入
  `agentsMdVersion`。
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
async def materialize_agents_md(session: SessionRecord, agents_md: AgentsMdConfig) -> AgentsMdMaterialization: ...
async def render_adapter_tool_config(adapter: str, profile: EffectiveHarnessProfile) -> AdapterToolConfig: ...
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

### 6.1.1 内置 Manager Cowork Recall Source

本地 Manager-backed Codex session 会包含一个保留的内置 source：

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

`manager-cowork-recall` 不是 caller 传入的外部 MCP server，而是 supervising
Manager 拥有的 loopback source，通过 binding-local 随机 recall token 限定在当前
HaaS session 范围内。它只暴露
`recall(query?: string, limit?: int)` 一个工具，返回 Cowork memory 数据库和保留
session transcript 中的有界结构化结果。它是 HaaS/Codex 中断或重启后的模型侧恢复
路径：Codex 可按任务需要主动查询相关历史，而不是由 Manager 改写用户 prompt 或依赖
特定语言关键词。

该 source 是 optional。URL 不视为 secret，但 recall token 是 scoped bearer capability，
不得写入日志、投影或 Manager binding 与 active HaaS profile 之外的持久位置。返回值不得
包含 raw tool arguments、host path、credential、完整 command output 或隐藏 prompt。Local Codex 可在通用外部 MCP
materialization 完成前先 materialize 这一内置 source；其它 caller-provided MCP source
仍遵循既有验证与支持门禁。
失败、中断或进程丢失后的 retry/recovery 场景中，返回的 transcript 必须包含尚未作为
普通 assistant transcript message 提交的最新 HaaS bridge checkpoint。这类恢复行只暴露
安全投影字段，包括有界 command/output preview、status、exit code、safe reason、
task outcome 与 model-stage summary。

### 6.1.2 内置 Browser Harness

OpenHarness 包含一个保留的内置浏览器能力。它不是用户传入的 connector、MCP server
或个人 Chrome 附着。默认 runtime 是 app-owned managed browser harness，要求：

- user data dir 位于 OpenHarness state directory 下的隔离目录；
- runtime 选择受控 Chromium / Chrome-for-Testing executable；
- 不导入用户个人 Chrome profile、cookie、密码或扩展；
- 每次 tool action 前执行有界 CDP/browser 健康检查；
- browser context、page 或 CDP session 崩溃/关闭后自动重建一次；
- `/v1/browser/state` 暴露结构化 `status`、`last_action`、`last_result`
  和安全 `last_error`。
- 桌面打包产物必须在 sidecar resources 下内置 Playwright driver 与受控 browser
  runtime。

Manager 默认不得为 agent browser tools 启动 `/Applications/Google Chrome.app`
或其它用户浏览器 profile。开发者可以显式指定 browser executable，但仍必须使用
app-owned profile，并在本地诊断中体现。Browser warmup 是 optional，不得阻塞整体
service readiness。

Manager connector 面的 browser tool 名称保持稳定：`browser_open_url`、
`browser_read_page`、`browser_click`、`browser_type`、`browser_select`、
`browser_upload_file`、`browser_wait`、`browser_screenshot` 和 `browser_close`。
实现内部可路由到 Browser Harness helper/CLI 逻辑，但 raw CDP endpoint、profile path、
cookie、Authorization header 和完整 screenshot 不得写入 model-visible 日志。

验收必须包含源码环境 smoke 与 packaged app smoke：在隔离 state directory 下调用
browser screenshot endpoint，并观察 `browser=managed_chromium`、`managed=true`、
app-owned profile 与 PNG data URL。缺少内置 Chromium、缺少 Playwright driver
resources 或回退到个人 Chrome 均为 release blocker。

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

AGENTS.md 使用与 skill bundle 相同的 path safety、secret scan 和 fingerprint
规则，但它不是 skill。P0 只支持 `mode=snapshot`，动态 reload 是 P1/spec-only。

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
  -> validate profile MCP/skills/AGENTS.md/tools
  -> active profile saved
  -> session created
  -> effective MCP/skills/AGENTS.md/tools frozen
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

### 7.1 Snapshot 内容与应用

P0 MCP 仅支持 brokered HTTP/SSE；command/args/isolated-env/process-lifecycle 合同完成前不接受 stdio。Required/optional 针对真实 upstream discovery，不是假造的 loopback relay URL。可信外部 broker 注入真实 credential，worker relay 只持 scoped runtime token。

Skill file 必须且只能有 content/contentB64/contentRef 之一；skill 和 AGENTS.md 的 contentRef 均为既有上传端点返回、caller-owned immutable `file_...` id。接受 profile 前验证 ownership、bytes、size、encoding、digest。Applied/pending session pin 内容，profile history 清理不能删除引用字节。不接受 Manager-local artifact URI 或未验证 remote URL。

内容写入隔离 revision directory，验证后且 native readback 成功才切 active generation。不覆盖 bind-mounted 项目的 AGENTS.md。Adapter 必须关闭隐式可变 instruction/skill/config discovery，或在相同逻辑 workspace path 提供隔离 snapshot view。明确 global 在 workspace 前；P0 不支持 directory rule 和不受控 nested discovery。Pin 的 harness 无法执行该来源边界时 validation 失败，不能虚称 snapshot isolation。文件变更创建更高 profile revision，delegated `/policy` 应用，不开启 turn 内自动 file watcher。

应用 profile 一起刷新 model route、MCP connection、skill、instruction；断开撤销来源、撤销旧 generation token，reload 不可验证时 restart/resume native runtime，session volume 保留 conversation。应用失败保持旧 applied revision 并阻止排队 turn，不混用旧 MCP 和新 instruction。

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
| AGENTS.md materialization partial write | remove partial directory and fail closed |
| Disabled tool unsupported | mark `advisory` or `unsupported`; do not claim hard enforcement |
| Header template missing value | reject turn before contacting MCP |

## 11. 测试计划与验收

- Unit：MCP config validation、header resolution、skill path validation、disabled tool mapping。
- Unit：AGENTS.md source validation、snapshot fingerprint、secret/path negative cases。
- Integration：adapter-specific config rendering for Codex, Pi and OpenCode fixtures。
- Security：path traversal, secret header redaction, AGENTS.md content redaction, disabled source not contacted。
- Compatibility：skill folder round-trip test；MCP unavailable degradation test。
- E2E：Codex session with one mock MCP server proves tool discovery and safe event projection。
