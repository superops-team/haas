# Harness Profile 组件规格

[English](README.md) | **简体中文**

Status: Draft
Last reviewed: 2026-09-14
Change ID: harness-profile-versioned-config, unified-runtime-approval-policy
Related specs: [HaaS Protocol](../haas-protocol/README.zh-CN.md), [Harness Registry](../harness-registry/README.zh-CN.md), [Session Runtime](../session-runtime/README.zh-CN.md), [MCP / Tool / Skill Runtime](../mcp-tool-skill-runtime/README.zh-CN.md), [Policy Controller](../policy-controller/README.zh-CN.md), [Manager HaaS Sidecar Backend](../manager-haas-sidecar-backend/README.zh-CN.md)

## 1. 组件定位

Harness Profile 是跨 harness 的可版本化运行配置合同。它把 provider/model、MCP
servers、skills、AGENTS.md、workspace、tool restrictions、approval policy、budget
和 metadata 组织为一个可以 validate、activate、snapshot、rebind 和审计的配置单位。

`Harness` 表示一个 ADK app / configured harness identity；`HarnessProfile` 表示该
app 当前或历史可执行配置。上游仍通过 `appName` 选择 harness；HaaS 在创建新 session
时解析该 harness 的 active profile revision，并冻结为 `EffectiveHarnessProfile`。

## 2. 来源与依据

| 来源 | 采用内容 |
|------|----------|
| Harness Registry | configured harness identity、active/retired 生命周期和 caller scope |
| Session Runtime | session 创建时冻结 effective config；已有 session 不受后续配置漂移影响 |
| MCP / Tool / Skill Runtime | MCP proxy、skill folder materialization、disabled tool enforcement |
| Policy Controller | workspace/network/tool/approval/model policy 分层合并与禁止未授权放宽 |
| Manager HaaS Sidecar Backend | Manager settings、session binding、profile drift UI 和显式 rebind |

## 3. 上游与下游关系

| 方向 | 对象 | 关系 |
|------|------|------|
| 上游 | HaaS Protocol | 暴露 profile CRUD、validate、activate、session rebind API |
| 上游 | Harness Registry | 维护 harness identity 与 active profile pointer |
| 上游 | Manager | 提交 manager-effective 配置意图，读取 profile fingerprint 与 drift 状态 |
| 下游 | Session Runtime | 在 session 创建或显式 rebind 时冻结 `EffectiveHarnessProfile` |
| 下游 | MCP / Tool / Skill Runtime | 校验并物化 MCP、skills、AGENTS.md 与 tool restriction |
| 下游 | Policy Controller | 编译 workspace/network/tool/approval/model policy |
| 下游 | Model Proxy | 解析 provider route 与 credential reference |

## 4. 职责边界

负责：

- 定义 profile 作为 provider、MCP、skills、AGENTS.md、workspace/policy 和 budget 的一等配置对象。
- 对 profile revision 做只增不改的版本管理；`active` 指针只前移到新建版本，`version` 只递增、不回滚。
- 生成稳定 `profileFingerprint`，供 capability discovery、session snapshot、manager binding 和 drift 检测使用。
- 在 activation 前执行 schema、security、policy、MCP、skill、AGENTS.md 与 provider route validation。
- 为 session 创建提供 `EffectiveHarnessProfile`，并明确已有 session 的动态更新边界。
- 记录 profile lifecycle audit event，且不泄漏 secret、host path、raw prompt 或完整 tool payload。

不负责：

- 不执行 harness、MCP、tool 或 model request。
- 不保存 raw credential；只保存 `credentialRef` 与 fingerprint。
- 不直接读写用户 workspace 文件；AGENTS.md 内容由 manager 授权输入或 sandbox 内受控读取流程提供。
- 不让 profile update 隐式修改已有 session、delegated session 或 native harness thread。

## 5. 核心接口

### 5.1 HaaS Native Profile API

| Method | Path | 说明 |
|--------|------|------|
| GET | `/v1/haas/profiles` | 分页列出 caller scope 内 profile revision，可按 `harnessId`/`status` 过滤 |
| POST | `/v1/haas/profiles` | 创建新的 draft profile revision（支持 `Idempotency-Key`） |
| GET | `/v1/haas/profiles/{profile_id}` | 读取单个 profile revision |
| PUT | `/v1/haas/profiles/{profile_id}` | 替换 draft profile mutable fields；非 draft 返回 `409 haas_profile_conflict` |
| POST | `/v1/haas/profiles/{profile_id}/validate` | 执行无副作用 validation，返回 typed finding |
| POST | `/v1/haas/profiles/{profile_id}/activate` | 将 validated profile revision 设为 harness active profile |
| POST | `/v1/haas/sessions/{session_id}/profile-rebind` | 对已有 session 显式 rebind 后续 turn 使用的 profile snapshot |

所有 endpoint 都是 HaaS native extension。ADK-compatible `/run` 不新增顶层字段；
可选 `haas.profileId` / `haas.profileVersion` 只允许在**新 session** 上 pin
profile。若已有 session 的 frozen profile 与请求 profile 不一致，返回
`409 haas_profile_rebind_required`，调用方必须走 native rebind 或新建 session。

### 5.2 Internal API

```python
async def create_profile(principal, input: HarnessProfileCreate) -> HarnessProfile: ...
async def validate_profile(profile_id: str) -> ProfileValidation: ...
async def activate_profile(harness_id: str, profile_id: str) -> HarnessRecord: ...
async def resolve_active_profile(principal, harness_id: str) -> HarnessProfile: ...
async def snapshot_effective_profile(profile: HarnessProfile, ctx: SessionContext) -> EffectiveHarnessProfile: ...
async def rebind_session_profile(session_key: SessionKey, request: ProfileRebindRequest) -> EffectiveHarnessProfile: ...
```

## 6. 数据模型

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
    "roots": [{"containerPath": "/workspace", "access": "rw"}]
  },
  "policy": {
    "network": {"defaultAction": "allow", "allow": []},
    "tools": {"disabled": [], "approvalMode": "on-request"},
    "model": {"allowedModels": ["doubao-seed-1-6"], "fallbackModel": null}
  },
  "budget": {
    "maxStep": 40,
    "timeoutSeconds": 900,
    "maxOutputTokens": 4096
  },
  "profileFingerprint": "sha256:profile",
  "createdAtMs": 1786400000000,
  "updatedAtMs": 1786400000000,
  "activatedAtMs": null
}
```

`provider` 遵循 `ProviderRoute` 合同（见
[Harness Registry](../harness-registry/README.zh-CN.md) 6.5 与
[Model Proxy](../model-proxy/README.zh-CN.md) 6.1）。`providerId` 是稳定 provider
身份，`name` 是可读别名，两者都保留。`wireApi` 声明请求/流式协议：
`openai-compatible` 表示该 provider 说 OpenAI 兼容协议族，`responses` 要求使用该族里的
OpenAI Responses 协议类型，`agent-plan` 指向 Ark Agent Plan 协议。原始 credential 绝不出现，
只存 `credentialRef` 与 `credentialFingerprint`。

### 6.2 EffectiveHarnessProfile

`EffectiveHarnessProfile` 是 session/turn 执行使用的 frozen snapshot：

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

完整 provider route、MCP headers、skill bytes、AGENTS.md content 和 policy 只进入内部
snapshot/materialization，不进入 ADK Session 输出、capability response 或 manager-visible
binding；公开面只返回 fingerprint、版本、安全名称和 degraded reason。

### 6.3 AGENTS.md Source

`agentsMd.sources[]` 的 `scope` 只允许：

- `global`：manager/user 全局规则；
- `workspace`：项目根目录中的 `AGENTS.md`；
- `directory`：未来支持的更近目录规则，P0 可声明 unsupported。

规则：

- `path` 必须为相对路径，不能包含 `..`、绝对路径、控制字符或 symlink escape。
- `contentRef` 指向 HaaS artifact/profile object，不得是 host 绝对路径或 presigned URL。
- `mode=snapshot` 表示 session 创建时固定 AGENTS.md 内容；后续文件变化只影响新 profile revision。
- `mode=dynamic` 为 P1/spec-only，必须先定义 reload、conflict、audit 与 prompt-injection 防护后才能启用。

## 7. 运行模型与状态机

profile 就是一个版本化的地址/配置记录，生命周期刻意保持最小：

```text
draft -> active -> retired
```

规则：

- 状态只有 `draft`（可编辑）、`active`（新 session 使用）、`retired`（仅留档审计的旧版本）。
- `version` 是 per-harness 的整数，只递增、不回滚。要复用旧配置时，复制该配置创建新版本，版本号取下一个更大的值。
- 已激活的 revision 不可原地修改；激活新 revision 会把旧 active 标为 `retired`，`retired` 不再被重新激活。
- `validate` 与 `activate` 是操作而非持久状态。`validate` 无副作用（不启动 harness、不连外部 MCP、不读 provider credential），可做 URL/policy/schema/skill bundle/AGENTS.md 静态校验和 adapter capability compatibility 检查；`activate` 要求 revision 已通过校验。
- 新 session 默认使用 harness 的 active profile revision。已有 session 继续使用 frozen `EffectiveHarnessProfile`。
- 新建 OpenHarness interactive profile 默认使用 `workspace-write`、公网
  `defaultAction=allow` 与 `approvalMode=on-request`。持久 profile 早于这些显式字段的
  旧安装执行一次 schema migration，物化上述值并记录 migration revision；后续读取不得
  把字段缺失动态解释为会变化的默认值。用户或管理员已显式设置的值绝不覆盖。
- 已有 session 的 profile 变更必须显式 `profile-rebind`，且只影响 rebind 后的新 invocation；运行中的 invocation 不被修改。
- `GET /v1/haas/sessions/{sessionId}/profile` 是非 delegated session 的权威恢复读接口，只返回 `profileId`、`profileVersion`、`profileFingerprint`、`harnessId`、`base` 与不含 credential 的 execution-intent fingerprint。不得返回 provider URL、`credentialRef`、MCP header、skill bytes、policy body 或内部 effective snapshot；ADK Session 输出保持不变。session 不存在、歧义或越权统一返回 `404 session_not_found`；delegated session 返回 `409 haas_profile_rebind_unsupported`，其恢复依据 delegated policy 状态。
- `profile-rebind` 只适用于非 delegated session。带 `delegatedSessionRef` 的 manager-delegated session 必须拒绝 `profile-rebind`，返回 `409 haas_profile_rebind_unsupported`；其运行配置由 manager 授权的 mount manifest、delegation policy snapshot 与 `profileRef` 拥有，只能通过 `POST /v1/haas/delegated-sessions/{id}/policy` 变更。该端点接受完整新 `profileRef`，通过 desired/applied revision 屏障对同一 delegated session 与 manager chat binding 就地应用；manager 不得为改配置新建 session。
- Rebind 不得改变 session id、history、artifact 可见性或 native session ownership；如果 adapter 不能安全延续，则把后续 invocation 标记为 `non_resumable` 或要求新 session。

### 7.1 最小部署与管控覆盖

- 多版本留存是可选的。无法留档历史的部署可以每个 harness 只保留最新一份 profile；替换时版本号仍然只递增。
- 当 profile 字段与管控入口下发的值冲突时（delegated session 即 manager 授权的 mount manifest 与 delegation policy snapshot），以管控值为准。profile 不得放宽或覆盖受管控的配置。

激活在同一事务内分配/检查单调版本并验证当前确切内容 fingerprint；修改 draft 使旧 validation 失效，validate 本身不使 draft 不可修改。重复激活当前相同 revision 无副作用，拒绝低版本/retired 激活。并发请求由 per-harness store 事务串行化。

Active profile 是默认值，不是已绑定 session 的 live 依赖。Session 复制完整解析后的非 secret 配置并 pin 内容引用；仅保留最新 profile 时，不得删除旧 applied/pending session 所需字节。授权管控值先替换 profile 默认，再由 Policy Controller 执行 platform/tenant hard limit，不能覆盖硬边界。Delegated 更新使用 Manager Delegation §5.1.1 revision 屏障。

`ProviderRoute` 必填 providerId/name/wireApi。`wireApi=responses` 隐含 `apiType=responses`；`openai-compatible` 与 `agent-plan` 必须明确 `apiType`（responses 或 chat_completions）。不得按 hostname 猜协议或静默切换。

### 7.2 Local API 应用

Manager local API 首次使用以 haas.profileId/profileVersion pin 已验证 profile。完整非 secret effective snapshot 深拷贝后私有持久化，不写入 ADK state。Native rebind 响应仅暴露 profileId、profileVersion、profileFingerprint、harnessId、base、resolvedAtMs；ADK Session 输出不变。非委托 profile-rebind 接受 profileId 与可选 expectedProfileVersion（对比当前 applied version），等待当前 lease 后在 lease 内原子替换 snapshot。新 invocation 冻结 applied route，更新不能改变 running invocation。同目标重试为空操作，旧版本不能覆盖新版本，保留 session identity 与 native history。Idempotency-Key 按 principal、完整 session identity 与 operation 隔离：相同请求即使在后续更新后也重放原响应，不同请求体返回 haas_idempotency_conflict。替换 snapshot 前校验请求类型及物化支持情况。尚未实现的安全相关物化必须拒绝，不能宣称已应用。Delegated rebind 仍禁止。

## 8. 安全与权限

- Profile 与 harness 一样带 tenant/workspace scope；跨 scope 返回 404。
- `credentialRef` 必须指向 caller scope 内可解析 secret；response 中只暴露 fingerprint。
- opaque credential handle 是有 scope 的运行时材料，不参与崩溃恢复使用的 execution-intent fingerprint。复用范围只限同一逻辑 session 与当前 supervisor grant；不得让 credential handle 跨 session 或 supervisor 携带。
- MCP URL/provider URL 必须通过 allowlist 和 SSRF 校验。
- AGENTS.md 与 skill 内容不得包含 secret-like 字符串；命中时 activation fail closed。
- Profile fingerprint 不能由 raw secret 参与计算；使用 credential fingerprint 代替。
- Unknown security-affecting fields fail closed；非安全 metadata 必须放入 `metadata`。

## 9. 可观测性

Events/logs：

- `haas.profile.created`
- `haas.profile.validation_failed`
- `haas.profile.activated`
- `haas.profile.retired`
- `haas.profile.rebind_requested`
- `haas.profile.rebind_applied`

日志字段只允许 profile id/version、harness id/base、fingerprint、安全 finding 和 safe reason。

## 10. 失败与恢复

| 场景 | 行为 |
|------|------|
| profile 不存在或越权 | `404 haas_profile_not_found` |
| 更新非 draft revision | `409 haas_profile_conflict` |
| 激活未验证或已删除 profile | `409 haas_profile_conflict` |
| existing session 请求不同 profile | `409 haas_profile_rebind_required`，不启动 invocation |
| AGENTS.md 或 skill source 非法 | `422 haas_skill_source_invalid` 或 `422 haas_agents_md_invalid` |
| profile store 不可用 | `503 haas_store_unavailable`；已有 frozen session 继续 |
| rebind 时 policy/workspace/provider 放宽未授权 | 对应 policy/security 错误；保持旧 snapshot |

## 11. 测试计划与验收

- Unit：profile fingerprint 稳定性、revision append-only、activate/retire、draft-only update、scope 404。
- Unit：provider/MCP/skill/AGENTS.md/workspace/policy schema validation 和 secret/path/URL negative cases。
- Integration：新 session 冻结 active profile；profile update 后新 session 使用新 revision，旧 session 不漂移。
- Integration：已有 session 显式 rebind 后，下一 invocation 使用新 `EffectiveHarnessProfile`；运行中 invocation 不受影响。
- Compatibility：`/list-apps` 与 `/run` 仍以 harness id/name 工作，不要求 ADK client 理解 profile。
- Client：capabilities/harness/profile response 带 `profileVersion`/`profileFingerprint`，Manager 能检测 drift 并展示 rebind/new-session 行为。
- Security：公开 response、event、binding、logs 不包含 raw credential、host path、AGENTS.md 原文、MCP header 或完整 tool payload。
