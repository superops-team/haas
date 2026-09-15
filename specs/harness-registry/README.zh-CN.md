# Harness Registry 组件规格

[English](README.md) | **简体中文**

Status: Draft
Last reviewed: 2026-09-10
Related specs: [HaaS Protocol](../haas-protocol/README.zh-CN.md), [Harness Profile](../harness-profile/README.zh-CN.md), [Harness Adapter](../harness-adapter/README.zh-CN.md), [Security Boundary](../security-boundary/README.zh-CN.md)

## 1. 组件定位

Harness Registry 维护 HaaS 可运行的 configured harness catalog。它回答“这个租户/工作区当前可以选择哪些 harness（ADK app）、每个 harness 当前激活哪个 profile，以及这些能力是否真的可用”。

`appName`（ADK 术语）就是 configured harness 的 `id`（`chrn_...`），`name` 作为可读别名可被 `/run` 解析。`base` 是开放字符串：首期 `codex`，后续注册 `pi`、`opencode`、`amp` 或其他 harness，不需要修改 public task API。Provider、MCP、skills、AGENTS.md、workspace/policy 和 budget 的可变配置归 [Harness Profile](../harness-profile/README.zh-CN.md) 所有；Registry 只保存 harness identity、scope、base 和 active profile pointer。

## 2. 来源与依据

| 来源 | 采用内容 |
|------|----------|
| ADK 2.0 | `appName` = harness `id`，`/list-apps` 列出 app 名 |
| `mpa-codex-worker` profile controller | profile draft/active、session 冻结、runtime policy |
| Harness Profile | provider/MCP/skills/AGENTS.md/workspace/policy/budget 的版本化配置 |
| Model Proxy | provider 路由与 model availability |
| 本组件总览 | configured harness catalog、appName/base/capability/model/provider discovery |
| Manager Delegation | manager 与 HaaS 都通过 provider id、model id 和 `credentialRef` 引用 provider；raw key 不复制进 delegated-session contract |

## 3. 上游与下游关系

| 方向 | 对象 | 关系 |
|------|------|------|
| 上游 | HaaS Protocol | 解析 appName -> configured harness；list/read/create/update/delete |
| 上游 | Session Runtime | 创建 run 时解析 harness |
| 下游 | Harness Adapter Registry | 查询 base 是否已安装、ready、支持能力 |
| 下游 | Model Proxy | 查询模型与 provider 可用性；下发 provider 路由 |
| 下游 | MCP / Tool / Skill Runtime | 校验 MCP、skills 和 disabledTools |
| 下游 | Security Boundary | scope、secret、SSRF 和 policy 校验 |

## 4. 职责边界

负责：

- 保存 configured harness 的稳定 identity 与 active profile pointer。
- 计算 harness、base、active profile、model、provider、MCP、skill 和 tool restriction 的有效视图。
- 解析 `appName`：先按 `id` 精确匹配，再按 `name` 匹配；多命中或未命中返回 404。
- 返回 caller scope 内的 harness；跨 scope 访问返回 not found。
- 为 session 创建解析 active profile，并将 effective profile 交给 Session Runtime 冻结；后续 profile 激活不改变已存在 session。
- 对 `base` 做 adapter availability 检查。
- 委托 Harness Profile 保存并校验 provider route、MCP、skills、AGENTS.md、workspace/policy 和 budget。
- 对 active profile 的 `defaultModel` 与 requested `model` 做可用性校验或显式 fallback。

不负责：

- 不直接执行 harness。
- 不保存 raw credential；credential 只保存引用或交给 secret store/vault。
- 不执行 MCP/tool 调用。
- 不修改已存在 session 的 frozen profile/config。
- 不把某个 harness 的原生工具名强行标准化成所有 harness 的 hard contract。

## 5. 核心接口

### 5.1 Public API

| Method | Path | 说明 |
|--------|------|------|
| GET | `/list-apps` | 列出 caller scope 内 harness 的 app 名（id 字符串数组） |
| GET | `/v1/haas/capabilities` | 为 protocol-owned capability response 提供 caller-visible configured-harness snapshot |
| GET | `/v1/haas/harnesses` | 列出 caller scope 内 configured harness 详情 |
| GET | `/v1/haas/harnesses/{harness_id}` | 读取一个 configured harness |
| POST | `/v1/haas/harnesses` | 创建 configured harness |
| PUT | `/v1/haas/harnesses/{harness_id}` | 替换 mutable config，`id`、`base`、`createdAtMs` 不可变 |
| DELETE | `/v1/haas/harnesses/{harness_id}` | 标记删除，不删除历史 session |
| GET/POST | `/v1/haas/profiles` | Harness Profile 组件拥有；Registry 为 harness lookup 与 active pointer 提供一致性 |
| GET/PUT/POST | `/v1/haas/profiles/{profile_id}`、`/validate`、`/activate` | Harness Profile 组件拥有；activation 原子更新 harness active pointer |
| GET | `/v1/haas/models` | 全局 backend/model catalog |
| GET | `/v1/haas/harnesses/{harness_id}/skills/{skill_id}/files` | 读取完整 skill folder bundle |

### 5.1.1 PUT 不变字段语义（S6）

`PUT` body 使用 `HarnessCreate` schema，其中不含 `id`/`createdAtMs`，但 schema 为
`additionalProperties: true`，调用方仍可能带上这些字段。处理规则：

| 情况 | 行为 |
|------|------|
| body 未带 `id`/`base`/`createdAtMs` | 正常更新 mutable field |
| body 带的值与现存记录**一致** | 幂等接受，不报错（便于 read-modify-write 回写） |
| body 带的值与现存记录**冲突** | `400 invalid_input`，不做部分更新 |

不为此新增专用错误码：不变字段冲突属于请求体校验失败，复用既有
`invalid_input`（见 [ERROR-CODES](../haas-protocol/ERROR-CODES.zh-CN.md) §2）。
`updatedAtMs` 由服务端重写，调用方传入值一律忽略。

### 5.1.2 Base 可用性校验依据（S6）

`base` 是开放字符串，但创建/更新时必须校验其**已注册**，否则返回
`422 haas_unsupported_base`。S6 的判定来源是本进程已装配的 adapter 集合
（当前为 `codex` 与测试用 `fake`），不是硬编码白名单：新增 adapter 即自动
可用。`base` 已注册但 adapter 探测未 ready 时，创建仍可成功，readiness 由
`/v1/haas/ready?scope=execution` 与 `/v1/haas/status` 反映——registry 不把
运行时可用性混入配置校验。

### 5.1.3 Harness Scope（S6）

`HarnessRecord` 携带 `tenantId` / `workspaceId`，与 Security Boundary §8.3
一致：

- 创建时记录调用方 principal 的 tenant/workspace。
- `list` / `get` / `update` / `delete` 与 `appName` 解析只命中 caller scope 内记录。
- 跨 scope 访问 `/v1/haas/harnesses/{id}` 返回 `404 haas_harness_not_found`；
  跨 scope 的 `appName` 解析返回 `404 app_not_found`。
- scope 字段为 `None` 的记录视为未绑定租户，仅对同样未绑定的 principal 可见，
  便于单节点部署与既有 seed 记录继续工作。

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

## 6. 数据模型

### 6.1 Harness（ADK app）

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

`defaultModel`、`systemPrompt`、`mcpServers`、`skills`、`disabledTools`、
`provider`、`maxStep` 和 `timeoutSeconds` 是 legacy-compatible projection
字段。新实现的写入事实源是 Harness Profile。读取 `Harness` 时可以内联
active profile 摘要以兼容旧 manager client，但这些字段不得绕过 profile validation。

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

`files[].path` 必须是相对路径，不能包含 `..`、绝对路径或 symlink escape。

### 6.5 Provider Identity Catalog

Provider identity 是稳定配置身份。endpoint、credential source、billing region 或 API shape 不同时，不得混用：

| Provider id | Endpoint | Wire/API shape | 规则 |
|-------------|----------|----------------|------|
| `ark` | `https://ark.ap-southeast.bytepluses.com/api/v3` | OpenAI-compatible data plane | BytePlus Ark global provider identity。 |
| `volcengine-ark` | `https://ark.cn-beijing.volces.com/api/v3` | OpenAI-compatible data plane | 火山方舟中国区标准数据面 identity。 |
| `ark-agent-plan-cn` | `https://ark.cn-beijing.volces.com/api/plan/v3` | Agent Plan API | 火山方舟 Agent Plan identity；不得与标准数据面互换。 |

manager 本地执行与 HaaS 委派执行都通过 `providerId + model + credentialRef` 传递 provider selection。provider route 同时保留 `providerId`（稳定身份）与 `name`（可读别名）。`wireApi` 声明协议：`openai-compatible` 是 OpenAI 兼容协议族，`responses` 要求使用该族里的 OpenAI Responses 协议类型，`agent-plan` 是 Ark Agent Plan 协议。Registry 只保存 provider route 与 credential reference/fingerprint；真实 credential 由 Model Proxy 在请求时解析。

## 7. 运行模型与状态机

```text
draft -> active -> retired
```

规则：

- `draft` 可以修改 harness identity 的 mutable metadata；执行配置字段的版本化生命周期属于 Harness Profile。
- `active` 表示 harness identity、adapter base 和 active profile pointer 通过校验，可被 session 使用；`appName` 解析只命中 active harness，且必须存在 active profile。
- `retired` 保留历史供审计，不再被新 session 选择；retired harness 不再被重新激活，历史 session 仍可读。

Session 使用 `EffectiveHarnessProfile` frozen snapshot，不读取 live harness 或
active profile 对象继续执行旧任务。Capability discovery 是 caller-scoped live snapshot，
必须包含当前 active profile 的 version/fingerprint，但不得修改或替换 frozen
session config。Registry 只提供可见 harness identity、active profile pointer 与配置事实；
HaaS Protocol 持有 public schema，adapter/runtime 组件提供 availability/mechanism/enforcement
事实。

## 8. 安全与权限

- Registry 只保存 credential ref、fingerprint 和 safe metadata，不保存 raw secret。
- active profile 中的 provider URL 与 MCP URL 必须通过 allowlist 和 SSRF 校验后才可被新 session 使用。
- 不同 tenant/workspace 的 harness 不可互读；越权统一返回 `404 haas_harness_not_found`（app 解析也返回 `404 app_not_found`）。
- Skill 文件拒绝 path traversal、绝对路径、控制字符和过大 bundle。
- `disabledTools` 的 enforcement 必须按 base 如实暴露为 `hard`、`advisory` 或 `unsupported`。

## 9. 可观测性

Registry 必须产出以下安全日志/指标：

- `haas.harness.created`
- `haas.harness.updated`
- `haas.harness.deleted`
- `haas.harness.validation_failed`
- `haas.harness.model_fallback`
- `haas.harness.provider_route_resolved`
- `haas.harness.base_unavailable`

日志字段只包含 id、base、model、capability、fingerprint 和 safe reason，不包含 secret 或完整 tool args。

## 10. 失败与恢复

| 场景 | 行为 |
|------|------|
| base 不支持 | `422 haas_unsupported_base` |
| PUT 试图改 `id`/`base`/`createdAtMs` | `400 invalid_input`，不做部分更新 |
| harness 不存在或越权 | `404 haas_harness_not_found` |
| app 不存在或越权 | `404 app_not_found` |
| model 不可用 | `422 haas_model_unavailable` 或显式 fallback 并写入 metadata |
| provider URL 未通过 allowlist | `haas_provider_source_invalid` |
| provider id 与 endpoint/API shape 不匹配 | `haas_provider_source_invalid` |
| skill bundle 无 `SKILL.md` | config validation failed，拒绝 active |
| MCP URL 未通过 allowlist | `haas_mcp_source_invalid` |
| active profile 缺失或未验证 | `409 haas_profile_conflict`，该 harness 不可用于新 session |
| session 请求的 profile 与 frozen snapshot 不一致 | `409 haas_profile_rebind_required`，不启动 invocation |
| registry store 不可用 | 创建/更新 fail closed；已冻结 session 继续执行 |
| harness 被删除 | 新任务失败；历史 session 可读 |

## 11. 测试计划与验收

- Unit：appName 解析（id 优先 / name fallback / 多命中断言）、scope filtering、active profile pointer、model fallback、provider URL allowlist、skill path validation。
- Integration：`GET /list-apps`、`GET /v1/haas/capabilities`、`GET/POST/PUT/DELETE /v1/haas/harnesses`、`GET /v1/haas/models`；capability harness 列表必须与 caller-visible active configured harness 完全一致，并包含 active profile version/fingerprint。
- Compatibility：ADK client `list-apps` 返回数组；`/run` 用 harness id 和 name 均能解析。
- Security：两 principal 互相读取 harness 返回 404；secret/credential ref 不在 response 中出现。
- Regression：更新 harness 名称不能丢 active profile pointer；激活新 profile 不影响既有 session snapshot，除非显式 rebind。
