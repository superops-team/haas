# Security Boundary 组件规格

Status: Draft
Last reviewed: 2026-08-26
Related specs: [HaaS Protocol](../haas-protocol/README.md), [Model Proxy](../model-proxy/README.md), [MCP / Tool / Skill Runtime](../mcp-tool-skill-runtime/README.md), [Container Runtime](../container-runtime/README.md)

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
| 上游 | Harness Registry | credential ref、MCP URL、skill bundle validation |
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

不负责：

- 不实现具体 auth provider。
- 不直接执行 redaction；具体组件必须调用统一 redaction API。
- 不决定业务层 entitlement 或计费策略。
- 不替代 OpenSandbox/Kubernetes/Docker 自身隔离能力。

## 5. 核心接口

```python
def redact(value: object, *, context: RedactionContext) -> object: ...
def validate_scope(principal: Principal, obj: ScopedObject, action: str) -> ScopeDecision: ...
def validate_url(url: str, policy: UrlPolicy) -> UrlDecision: ...
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

## 8. 安全与权限

### 8.1 禁止输出面

以下内容不得出现在 public response、SSE、logs、metrics、artifact metadata、verification report、harness-visible durable config：

- Provider API key
- `Authorization` header value
- Cookie
- Presigned URL
- Raw prompt
- Full tool arguments or raw tool result
- Internal hostnames that reveal private infrastructure
- Absolute host paths
- Stack traces

### 8.2 URL 和网络

- Caller-provided upstream URL 默认拒绝，除非命中 allowlist。
- 允许的 scheme 必须显式列出，默认只允许 `https` 和 loopback `http`。
- 私网 IP、metadata service、Unix socket、Docker socket 默认拒绝。
- OpenSandbox egress policy 是运行时网络控制，HaaS URL validator 是协议入口控制，两者不能互相替代。

### 8.3 Object Scope

- 所有对象带 tenant/workspace/principal scope。
- ADK 的 `userId`/`sessionId` 是 caller-supplied，必须归属认证 principal；跨 user/session 访问返回 404。
- 跨 scope 读取、取消、删除、下载返回 404。
- Admin/debug 能力必须独立授权，不由普通 bearer token 推导。

### 8.4 Artifact Safety

- Artifact id 不得被解析成 caller-controlled path。
- 下载必须限定在 container root。
- 响应必须带 `X-Content-Type-Options: nosniff`。
- HTML/JS/SVG 等主动内容应使用独立 origin 或 attachment。

### 8.5 Redaction Taxonomy

`redact(value, context)` 按以下分类统一处理；识别方式为「字段级规则 + 正则
table」。正则 table 与提交门禁 `scripts/quality/secret-scan.py` 共用同一份
pattern 清单（单一事实源，代码侧由同一 fixture 驱动）。

| 分类 | 识别方式 | 默认动作 |
|------|----------|----------|
| `credential` | 字段名（key/token/password 等）+ secret pattern（AKIA/sk-/ghp_/AIza/JWT…） | 替换为 `[REDACTED]` |
| `authorization` | `Authorization`/`Bearer`/`Basic`/`Cookie` 头值 | 替换为 `[REDACTED]` |
| `presigned-url` | `X-Amz-Signature`/`Signature=` query | 整 URL 替换为 `[REDACTED_URL]` |
| `raw-prompt` | `trace_content=false`（默认）下的输入 prompt | 不入日志/事件 |
| `tool-payload` | 完整 tool args/result | 默认摘要，full payload 需已批准 debug 设计 |
| `host-path` | 内部 host、绝对路径 | `[REDACTED_PATH]` |
| `stack-trace` | 异常栈 | 不出公开面，内部只记 safe reason |

识别失败或上下文不足时 **fail closed**：宁可不写，不写未脱敏内容。`redact()`
是唯一入口，各组件不得实现私有脱敏逻辑。

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
| URL 不在 allowlist | `403 haas_url_not_allowed` |
| runtime token 过期 | proxy 返回 401；adapter 可刷新一次，失败则 task failed |
| redaction pipeline 失败 | fail closed，不写未脱敏 payload |
| artifact path traversal | 拒绝访问并记录安全事件 |

## 11. 测试计划与验收

- Unit：redaction regex/structured traversal、URL allowlist、scope check、artifact path canonicalization。
- Integration：两 principal 对 harness/session/invocation/file 的互访返回 404。
- Proxy：真实 provider key 不进入 harness env/config；短期 token 过期和撤销生效。
- Event/log：构造含 key/header/raw prompt/tool args 的输入，断言公开面全部脱敏。
- Container：AIO 容器内无默认公开 secret；`/health`、`/ready` 不输出敏感环境。
