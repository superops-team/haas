# Harness Registry 组件规格

Status: Draft
Last reviewed: 2026-08-26
Related specs: [HaaS Protocol](../haas-protocol/README.md), [Harness Adapter](../harness-adapter/README.md), [Security Boundary](../security-boundary/README.md)

## 1. 组件定位

Harness Registry 维护 HaaS 可运行的 configured harness catalog。它回答“这个租户/工作区当前可以选择哪些 harness（ADK app）、每个 harness 能用哪些模型、工具、MCP 和 skill，以及这些能力是否真的可用”。

`appName`（ADK 术语）就是 configured harness 的 `id`（`chrn_...`），`name` 作为可读别名可被 `/run` 解析。`base` 是开放字符串：首期 `codex`，后续注册 `pi`、`opencode`、`amp` 或其他 harness，不需要修改 public task API。

## 2. 来源与依据

| 来源 | 采用内容 |
|------|----------|
| ADK 2.0 | `appName` = harness `id`，`/list-apps` 列出 app 名 |
| `mpa-codex-worker` profile controller | profile draft/active、session 冻结、runtime policy |
| Model Proxy | provider 路由与 model availability |
| 本组件总览 | configured harness catalog、appName/base/capability/model/provider discovery |

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

- 保存 configured harness 的稳定配置。
- 计算 harness、base、model、provider、MCP、skill 和 tool restriction 的有效视图。
- 解析 `appName`：先按 `id` 精确匹配，再按 `name` 匹配；多命中或未命中返回 404。
- 返回 caller scope 内的 harness；跨 scope 访问返回 not found。
- 冻结 session 创建时的 effective harness config，后续 harness 更新不改变已存在 session。
- 对 `base` 做 adapter availability 检查。
- 保存并校验 provider 路由配置（`baseUrl`/`wireApi`/`credentialRef`），URL 必须过 allowlist。
- 对 `defaultModel` 和 requested `model` 做可用性校验或显式 fallback。
- 保存 skill folder bundle，保证 round-trip 不丢文件。

不负责：

- 不直接执行 harness。
- 不保存 raw credential；credential 只保存引用或交给 secret store/vault。
- 不执行 MCP/tool 调用。
- 不修改已存在 session 的 frozen config。
- 不把某个 harness 的原生工具名强行标准化成所有 harness 的 hard contract。

## 5. 核心接口

### 5.1 Public API

| Method | Path | 说明 |
|--------|------|------|
| GET | `/list-apps` | 列出 caller scope 内 harness 的 app 名（id 字符串数组） |
| GET | `/v1/haas/harnesses` | 列出 caller scope 内 configured harness 详情 |
| GET | `/v1/haas/harnesses/{harness_id}` | 读取一个 configured harness |
| POST | `/v1/haas/harnesses` | 创建 configured harness |
| PUT | `/v1/haas/harnesses/{harness_id}` | 替换 mutable config，`id`、`base`、`createdAtMs` 不可变 |
| DELETE | `/v1/haas/harnesses/{harness_id}` | 标记删除，不删除历史 session |
| GET | `/v1/haas/models` | 全局 backend/model catalog |
| GET | `/v1/haas/harnesses/{harness_id}/skills/{skill_id}/files` | 读取完整 skill folder bundle |

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

## 6. 数据模型

### 6.1 Harness（ADK app）

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

`files[].path` 必须是相对路径，不能包含 `..`、绝对路径或 symlink escape。

## 7. 运行模型与状态机

```text
draft -> validated -> active -> superseded -> deleted
           |             |
           |             +-> snapshotted into session
           v
        rejected
```

规则：

- `draft` 可以修改任意 mutable field。
- `validated` 表示 schema、adapter base、provider URL、MCP URL、skill bundle 和 policy 通过校验。
- `active` 可被 session 使用；`appName` 解析只命中 active harness。
- `superseded` 保留历史，不再被新 session 默认选择。
- `deleted` 不可被新任务选择，但历史 session 仍可审计。

Session 使用 `EffectiveHarnessConfig` frozen snapshot，不读取 live harness 对象继续执行旧任务。

## 8. 安全与权限

- Registry 只保存 credential ref、fingerprint 和 safe metadata，不保存 raw secret。
- provider URL 与 MCP URL 必须通过 allowlist 和 SSRF 校验后才可进入 active harness。
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
| app 不存在或越权 | `404 app_not_found` |
| model 不可用 | `422 haas_model_unavailable` 或显式 fallback 并写入 metadata |
| provider URL 未通过 allowlist | `haas_provider_source_invalid` |
| skill bundle 无 `SKILL.md` | config validation failed，拒绝 active |
| MCP URL 未通过 allowlist | `haas_mcp_source_invalid` |
| registry store 不可用 | 创建/更新 fail closed；已冻结 session 继续执行 |
| harness 被删除 | 新任务失败；历史 session 可读 |

## 11. 测试计划与验收

- Unit：appName 解析（id 优先 / name fallback / 多命中断言）、scope filtering、model fallback、provider URL allowlist、skill path validation。
- Integration：`GET /list-apps`、`GET/POST/PUT/DELETE /v1/haas/harnesses`、`GET /v1/haas/models`。
- Compatibility：ADK client `list-apps` 返回数组；`/run` 用 harness id 和 name 均能解析。
- Security：两 principal 互相读取 harness 返回 404；secret/credential ref 不在 response 中出现。
- Regression：更新 harness 名称不能丢 skill files；更新 active harness 不影响既有 session snapshot。
