# Security Boundary 组件规格

[English](README.md) | **简体中文**

Status: Draft
Last reviewed: 2026-09-14
Change ID: unified-runtime-approval-policy
Related specs: [HaaS Protocol](../haas-protocol/README.zh-CN.md), [Harness Profile](../harness-profile/README.zh-CN.md), [Model Proxy](../model-proxy/README.zh-CN.md), [MCP / Tool / Skill Runtime](../mcp-tool-skill-runtime/README.zh-CN.md), [Manager Delegation](../manager-delegation/README.zh-CN.md), [Container Runtime](../container-runtime/README.zh-CN.md)

## 1. 组件定位

Security Boundary 定义 HaaS 所有公开入口、内部 adapter、模型代理、MCP/tool/skill、artifact 和容器运行时的安全不变量。它不是单独的运行进程，而是一组必须由各组件共同执行的合同。

## 2. 来源与依据

| 来源 | 采用内容 |
|------|----------|
| ADK 2.0 / HaaS Protocol | bearer auth、object scope（含 userId/sessionId）、error hygiene |
| `mpa-codex-worker` AGENTS/specs | secretless runtime、redaction、runtime token、public surface 不泄密 |
| OpenSandbox docs | credential vault、egress allowlist、sandbox isolation、API key auth |
| 总览要求 | secretless 和 artifact path 安全要求 |

## 3. 上游与下游关系

| 方向 | 对象 | 关系 |
|------|------|------|
| 上游 | HaaS Protocol | auth、scope、schema、error hygiene |
| 上游 | Harness Registry / Harness Profile | credential ref、MCP URL、skill bundle、AGENTS.md validation |
| 上游 | Session Runtime | object ownership、idempotency、workspace scope |
| 上游 | Harness Adapter | env/config redaction、permission policy |
| 上游 | Model Proxy | provider credential handling |
| 上游 | Container Runtime | filesystem/network/process isolation |

## 4. 职责边界

负责：

- 定义 secret 分类和禁止出现的输出面。
- 定义 caller principal、tenant、workspace、harness、session、invocation、file 的 scope 规则。
- 定义 public error message、log、metrics、event、artifact metadata 的脱敏规则。
- 定义 URL allowlist、private-network、loopback、Unix socket 和 SSRF 防护规则。
- 定义 artifact path 防穿越、content serving headers 和 size/count limit。
- 定义 credential reference 与短期 runtime token 的生命周期。
- 定义不可审批 hard deny，以及 approval grant 的最大 scope/lifetime。

不负责：

- 不实现具体 auth provider。
- 不直接执行 redaction；具体组件必须调用统一 redaction API。
- 不决定业务层 entitlement 或计费策略。
- 不替代 OpenSandbox/Kubernetes/Docker 自身隔离能力。

Approval 绝不是 root 或 host capability。Platform hard deny 包括 credential/secret store
访问、raw host/private/link-local/metadata/control-plane 网络、Docker/Unix control socket、
授权 manifest 外 mount root、跨 principal/session 对象，以及所选 runtime 无法 enforce 的能力。
这些限制在 `always`、`on-request`、`never` 下都保持 deny。Fresh-session 公网 allow 只有在
上述排除之后才生效。Approval request/event 只保留脱敏摘要和 fingerprint，不保留 raw command、
prompt、header、cookie、credential 或 signed authorization URL。

## 5. 核心接口

```python
def redact(value: object, *, context: RedactionContext) -> object: ...
def validate_scope(principal: Principal, obj: ScopedObject, action: str) -> ScopeDecision: ...
def validate_url(url: str, policy: UrlPolicy) -> UrlDecision: ...
def validate_mount_manifest(manifest: MountManifest, policy: EffectivePolicy) -> MountDecision: ...
def issue_runtime_token(scope: RuntimeTokenScope, ttl_seconds: int) -> RuntimeToken: ...
def resolve_secret(ref: str, audience: str) -> SecretValue: ...
def validate_artifact_path(container_root: str, requested: str) -> SafePath: ...
def assert_no_secret_surface(surface: object) -> None: ...
```

## 6. 数据模型

### 6.1 RuntimeToken

```json
{
  "id": "rtok_abc",
  "audience": "model_proxy",
  "sessionId": "hsess_abc",
  "harnessId": "chrn_codex_default",
  "expiresAtMs": 1786400000000,
  "fingerprint": "sha256:abc",
  "revoked": false
}
```

### 6.2 SecretRef

```json
{
  "type": "secret_ref",
  "ref": "secret://tenant/workspace/provider/default",
  "fingerprint": "sha256:abc",
  "expiresAtMs": null
}
```

### 6.3 SafeErrorDetail

```json
{
  "safeReason": "provider_unavailable",
  "retryable": true,
  "traceId": "tr_abc",
  "supportCode": "diag_abc"
}
```

## 7. 运行模型与状态机

```text
raw inbound request
  -> schema validation
  -> auth
  -> scope check
  -> secret extraction/rejection
  -> policy validation
  -> safe internal request
  -> adapter execution
  -> redacted event/log/response/artifact metadata
```

Credential lifecycle:

```text
secret_ref configured
  -> runtime token issued for session（唯一签发方：Security Boundary）
  -> adapter receives token/ref only
  -> model/MCP proxy resolves real secret per request
  -> token expires or revoked
```

**统一签发原则**：runtime token 的唯一签发方是 Security Boundary 的
`issue_runtime_token(scope, audience)`。Model Proxy、MCP proxy 只消费带
`audience=model_proxy|mcp_proxy|adapter` 的 token，不得各自实现 token 体系。

**secret 概念分层**：

| 概念 | 定位 | 存储内容 |
|------|------|----------|
| secret store | 长期 credential 引用的持久层 | 只存 `credentialRef` + fingerprint，不存明文 |
| credential vault（OpenSandbox） | 运行时把真实 secret 解析进 sandbox 会话、签发短 token 的运行时面 | 真实 secret，session 级 TTL |

两者不同层，归属都在 Security Boundary 定义的边界内；`resolve_secret(ref)`
是唯一取真实 secret 的入口。

### 7.1 凭证配置与执行数据

`credentialRef` 是地址，不是已经传输的凭证。Local-managed 下 manager secret store 持有长期凭证；控制侧 resolver 通过 owner-only、peer-authenticated 本地 IPC 服务，仅请求 session 已授权引用；再通过私有认证 TLS 或 owner-only IPC 配置可信外部 broker 的内存。该通道与 worker execution transport 分离，拒绝重放/越权请求，不把 secret 放入进程参数、env、logs 或 HaaS 持久配置。Broker 处理 model/MCP 请求，但不给 worker 提供 credential-read 操作。重启重新配置 secret，撤销授权关闭旧 grant。Remote 部署要求 operator 预置远程 scope 的 vault reference，不能假定远程能解析本地 `secret://manager/...`。

Lite 使用 broker memory 替代 OpenSandbox runtime vault；Security Boundary 仍是短 token 的唯一签发方。Worker volume 不保存真实上游 credential。续接必需的私有 native conversation/checkpoint 文件是受访问控制的执行数据，不是日志或 public event payload；native 执行前校验/拒绝 secret-bearing input，按 session retention 保存并排除 artifact/debug 输出。公开 raw prompt/tool-payload 禁止规则不变。

内置 macOS local API 使用仅由自有 HaaS 子进程继承的匿名 Unix socketpair；继承 descriptor 的持有权证明 peer 身份，没有可发现 listener。`HAAS_CREDENTIAL_FD` 只传编号，启动时消费且不传给 Codex。Manager grant 绑定 reference/harness/session/model/精确 URL，请求序号单调、frame 有界。EOF、超时、重放、格式错误或未知 grant 均拒绝解析；只有 Manager SecretStore 解析可在此通道返回 key bytes。Grant 与通道随 supervisor 结束，不支持任意 secret 查询或远程转发。

短期执行证据是独立的非持久执行数据。HaaS process 可以为每个 active command
activity 在内存保留一条有界 evidence record，使所属用户可检查实际命令、工作目录和
输出。每条 record 的脱敏 output 上限为 8 MiB（8,388,608 UTF-8 bytes）；Codex transport
frame 上限还必须容纳受支持 evidence payload 的 JSON-RPC envelope 与最坏 JSON 转义。
未超过 evidence 上限的内容必须完整返回，禁止在更低阈值
静默截断。超过上限的输出继续显式截断，完整内容必须通过受治理的 Artifact Store 获取。
Record 只能通过 opaque `evidenceRef` 定位，绑定认证 principal、完整 session key、
invocation 与 tool call；其过期时间不得晚于 native 授权 URL 的过期时间或命令结束后
15 分钟（取更早者）。它在过期、session 删除或 runtime 退出时删除，且不得进入 event
store、transcript、artifact、diagnostics、metrics 或 logs。

Evidence record 返回前执行 value-aware credential redaction。API key、Bearer/Basic
authorization value、cookie、password、private key 与携带 secret 的环境变量赋值必须
替换；普通命令文本、容器内工作路径和普通 HTTPS URL 保持原样。只有 adapter 根据明确
的用户授权语义将 URL 识别为授权链接，且 scoped execution-evidence endpoint 对响应
强制执行有界 expiry 时，完整短期 HTTPS 授权 URL 才可原样保留。如果 native URL 提供
可信且更早的 expiry，evidence 采用该更早期限；URL 本身不要求在 query 中显式携带
expiry。该窄例外用于保持签名与可用性，不允许该 URL 进入任何持久或可观测 surface。

## 8. 安全与权限

### 8.1 禁止输出面

以下内容不得出现在 public response、SSE、logs、metrics、artifact metadata、verification report、harness-visible durable config：

- Provider API key
- `Authorization` header value
- Cookie
- Presigned URL
- Raw prompt
- Full tool arguments or raw tool result
- Raw AGENTS.md content outside approved profile materialization storage
- Public native event 中的 adapter/native event type name 或 untyped debug payload
- `adapterId`、`userId`、`schemaVersion`、`redactionApplied` 等 internal-only event 字段
- Internal hostnames that reveal private infrastructure
- Absolute host paths
- Stack traces

§7.1 定义的 scoped、`Cache-Control: no-store` execution-evidence response 是已验证、
未过期用户授权 URL 的唯一例外。Canonical event、ADK projection、log、transcript、
artifact、diagnostics、crash report 和 verification output 均不适用该例外。

### 8.2 URL 和网络

- Caller-provided upstream URL 默认拒绝，除非命中 allowlist。
- 允许的 scheme 必须显式列出，默认只允许 `https` 和 loopback `http`。
- 私网 IP、metadata service、Unix socket、Docker socket 默认拒绝。
- OpenSandbox egress policy 是运行时网络控制，HaaS URL validator 是协议入口控制，两者不能互相替代。

### 8.3 Object Scope

- 所有对象带 tenant/workspace/principal scope。
- Harness profile 与所属 harness 带相同 tenant/workspace scope；跨 scope 的 profile
  read、activation、delete、validation 和 session rebind 返回 404。
- ADK 的 `userId`/`sessionId` 是 caller-supplied，必须归属认证 principal；跨 user/session 访问返回 404。
- 跨 scope 读取、取消、删除、下载返回 404。
- Admin/debug 能力必须独立授权，不由普通 bearer token 推导。

### 8.4 Artifact Safety

- Artifact id 不得被解析成 caller-controlled path。
- 下载必须限定在 container root。
- 响应必须带 `X-Content-Type-Options: nosniff`。
- HTML/JS/SVG 等主动内容应使用独立 origin 或 attachment。

### 8.5 Delegated Mount Safety

Manager-delegated execution 可以把用户授权的项目根目录挂进 HaaS 容器，但 mount contract 仍受 Security Boundary 约束：

- primary project mount 只有在 manager 显式授权后才允许为 `/workspace:rw`；
- extra mount 默认 `ro`，升级为 `rw` 需要显式授权；
- 用户 HOME、超出项目授权的父目录、Docker socket、SSH 目录、credential store 和未请求路径均拒绝；
- restore 创建容器前必须重新校验路径存在性、canonical path、类型、symlink 边界和 access mode；
- 失败返回结构化安全错误，不回退本地执行。

### 8.6 Redaction Taxonomy

`redact(value, context)` 按以下分类统一处理；识别方式为「字段级规则 + 正则
table」。正则 table 与提交门禁 `scripts/quality/secret-scan.py` 共用同一份
pattern 清单（单一事实源，代码侧由同一 fixture 驱动）。

| 分类 | 识别方式 | 默认动作 |
|------|----------|----------|
| `credential` | 字段名（key/token/password 等）+ secret pattern（AKIA/sk-/ghp_/AIza/JWT…） | 替换为 `[REDACTED]` |
| `authorization` | `Authorization`/`Bearer`/`Basic`/`Cookie` 头值 | 替换为 `[REDACTED]` |
| `presigned-url` | `X-Amz-Signature`/`Signature=` query | 整 URL 替换为 `[REDACTED_URL]` |
| `ephemeral-authorization-url` | Adapter 根据明确授权语义分类、由 evidence 强制 expiry 的 HTTPS 用户授权 URL | 仅在 scoped no-store evidence response 中逐字节保留；其他 surface 按 `presigned-url` 处理 |
| `raw-prompt` | `trace_content=false`（默认）下的输入 prompt | 不入日志/事件 |
| `tool-payload` | 完整 tool args/result | 默认摘要，full payload 需已批准 debug 设计 |
| `native-event` | Adapter/native type name、untyped metadata、internal event field | 投影到稳定 allowlisted `haas.*` type，校验 type-specific `haas` metadata，并在 public projection 前剥离内部字段 |
| `host-path` | 内部 host、绝对路径 | `[REDACTED_PATH]` |
| `stack-trace` | 异常栈 | 不出公开面，内部只记 safe reason |

识别失败或上下文不足时 **fail closed**：宁可不写，不写未脱敏内容。`redact()`
是唯一入口，各组件不得实现私有脱敏逻辑。

**上游响应体同样是不可信来源**：provider / harness / MCP 返回的 body 可能回显
我们注入的 `Authorization` 或其他凭据，因此在拼进 error message、日志或事件前
必须经 `safe_upstream_body()` 脱敏并截断（默认 512 字符）。错误信息仍须保留
状态码等可诊断信息，不得为脱敏而丢失可操作性。该函数是唯一实现，
model proxy 与 OpenSandbox client 共用，禁止各自复制一份。

## 9. 可观测性

安全日志只记录：

- `traceId`
- `principalHash`
- `tenantId` / `workspaceId` hash or safe id
- `objectType`
- `objectId`
- `action`
- `decision`
- `safeReason`
- `credentialFingerprint`
- `policyVersion`

不得记录 secret value、raw request body、raw prompt 或 full tool payload。

## 10. 失败与恢复

| 场景 | 行为 |
|------|------|
| auth 缺失或无效 | `401 missing_credential` / `invalid_credential` |
| scope 不匹配 | 404，不暴露对象存在性 |
| secret 出现在禁止字段 | `400 haas_secret_input_invalid` 或 redaction + security finding |
| AGENTS.md/profile content 包含 secret-like 值 | 用 `haas_agents_md_invalid` / `haas_secret_input_invalid` 拒绝 validation 或 activation；不得 materialize |
| URL 不在 allowlist | `403 haas_url_not_allowed` |
| runtime token 过期 | proxy 返回 401；adapter 可刷新一次，失败则 task failed |
| redaction pipeline 失败 | fail closed，不写未脱敏 payload |
| artifact path traversal | 拒绝访问并记录安全事件 |
| delegated mount validation 失败 | 用 `haas_delegation_mount_invalid` 拒绝；要求重新授权或 policy rebind |

## 11. 测试计划与验收

- Unit：redaction regex/structured traversal、URL allowlist、scope check、artifact path canonicalization。
- Unit：execution-evidence redaction 保留普通路径与 URL，遮蔽真实 credential value，只逐字节保留已验证且未过期的授权 URL，100,001-byte command output 完整往返，强制 8 MiB 上限，并证明授权 URL 不进入持久 surface。
- Integration：跨 principal/session/invocation/tool scope 读取 evidence 返回 404，过期返回 410；响应带 `Cache-Control: no-store`、`Referrer-Policy: no-referrer`，log/event/transcript/artifact 中均无授权 URL。
- Integration：两 principal 对 harness/profile/session/invocation/file 的互访返回 404；session profile rebind 越权也返回 404。
- Proxy：真实 provider key 不进入 harness env/config；短期 token 过期和撤销生效。
- Event/log：构造含 key/header/raw prompt/tool args/AGENTS.md secret-like content 的输入，断言公开面全部脱敏或拒绝。Native event 测试拒绝 unknown/untyped public payload，并证明 adapter `nativeType`、internal id、raw prompt/reasoning、完整 tool payload 不外泄。
- Container：AIO 容器内无默认公开 secret；`/health`、`/ready` 不输出敏感环境。
- Delegation：HOME、Docker socket、SSH path、symlink escape、父目录扩大和 mount drift 在 create 与 restore 时均被拒绝。
