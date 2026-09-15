# Identity 组件规格

[English](README.md) | **简体中文**

Status: Draft
Last reviewed: 2026-09-10
Related specs: [Security Boundary](../security-boundary/README.zh-CN.md), [HaaS Protocol](../haas-protocol/README.zh-CN.md), [Session Runtime](../session-runtime/README.zh-CN.md)

## 1. 组件定位

Identity 定义 HaaS 的认证与身份边界。HaaS 不实现具体 auth provider，但必须定义一个稳定的 `IdentityProvider` 接口：把 `Authorization: Bearer` 转换成 `Principal`，并把 caller-supplied 的 `tenantId`/`workspaceId`/`userId` 归属到 principal scope。

首期提供 `StaticTokenIdentityProvider`（测试/单机部署）；生产通过 `ExternalJwtIdentityProvider` 委托外部 OIDC/JWT 校验（预留接口，不绑定具体实现）。

## 2. 来源与依据

| 来源 | 采用内容 |
|------|----------|
| Security Boundary | 「不实现具体 auth provider」「跨 scope 返回 404 不返回 403」 |
| HaaS Protocol | `Authorization: Bearer` 必填（除 health/ready） |
| Session Runtime | `userId` 必须属于认证 principal |

## 3. 上游与下游关系

| 方向 | 对象 | 关系 |
|------|------|------|
| 上游 | HaaS Protocol | 每个请求进入组件管线前完成鉴权 |
| 上游 | Security Boundary | 提供 scope 判定的 principal 事实 |
| 下游 | Session Runtime / Registry / Artifact / Admission | 提供 `Principal` 作为 scope 依据 |
| 下游 | Observability | 提供 `principalHash`（不提供明文） |

## 4. 职责边界

负责：

- 定义 `IdentityProvider.authenticate(authorization) -> Principal`。
- 定义 principal 与 tenant/workspace/userId 的归属判定 `owns()`。
- 定义 401（缺失/无效）与 404（存在但无权限）的边界。
- 定义 admin/debug 独立授权通道（不由普通 bearer 推导）。

不负责：

- 不实现 OIDC/OAuth/token 签发服务本身。
- 不保存 caller token 明文或完整身份到日志。
- 不做业务计费或 entitlement。

## 5. 核心接口

```python
class Principal(TypedDict):
    principalId: str
    tenantId: str | None
    workspaceId: str | None
    defaultUserId: str
    allowedUserIds: list[str]
    roles: list[str]

class IdentityProvider(Protocol):
    async def authenticate(self, authorization: str | None) -> Principal:
        """Return Principal or raise MissingCredential / InvalidCredential."""
    def owns(self, principal: Principal, *, tenant_id=None, workspace_id=None, user_id=None) -> bool:
        """Return True when the caller-supplied scope belongs to the principal."""
    def is_admin(self, principal: Principal) -> bool:
        """True only for an independently granted admin/debug role."""
```

实现登记：

| 实现 | 用途 |
|------|------|
| `StaticTokenIdentityProvider` | 静态 token -> principal 映射，单机/测试 |
| `ExternalJwtIdentityProvider` | 委托外部 JWKS/OIDC 校验（预留） |

## 6. 数据模型

```json
{
  "principalId": "p_abc",
  "tenantId": "tenant_1",
  "workspaceId": "workspace_1",
  "defaultUserId": "u_123",
  "allowedUserIds": ["u_123"],
  "roles": ["user"]
}
```

ADK `userId` 是受控业务子身份，不是 authentication override。认证后必须得到
`defaultUserId`；`allowedUserIds` 是普通 principal 可访问的精确集合，并且必须包含
`defaultUserId`。Request 省略 `userId` 时由 Protocol Mapper 使用 `defaultUserId`；显式
提供时，只有命中 `allowedUserIds`，或独立授予的 `delegate_user`/admin scope 明确允许
目标时，`owns()` 才能通过。显式值绝不能替换 `principalId`、tenant、workspace、role
或 token identity。

`StaticTokenIdentityProvider` 必须把每个 token 映射到固定 `defaultUserId` 与显式
allowlist。`ExternalJwtIdentityProvider` 只映射已配置并验证的 claim，不得从任意 request
header 推导 allowlist。非法或未授权 user sub-scope 返回 404，不暴露存在性。

Managed launch 的 `HAAS_STATIC_PRINCIPAL_JSON` 仅从可信进程配置解析（base64 JSON），要求上述 Principal 字段且 allowedUserIds 包含 defaultUserId，只映射配置的 token file。Bootstrap 默认 user `manager`，不是 admin。映射缺失/非法启动失败，request/project 字段不能授予 role。Token 创建/轮换由 supervisor 在认证前管理，不进入 worker container。

## 7. 运行模型与状态机

```text
request -> identity.authenticate(bearer)
  -> missing/invalid -> 401 missing_credential / invalid_credential
  -> principal resolved
  -> caller scope (tenant/workspace/user) checked via owns()
  -> mismatch -> 404 (not 403)
  -> proceed with principal bound to request context
```

## 8. 安全与权限

- 鉴权失败分两类：无/坏凭证 `401`；有凭证但对象不在其 scope `404`。
- admin/debug 需独立授权（`is_admin`），普通 bearer 不得推导。
- 日志只记录 `principalHash`、tenant/workspace hash 或安全 id。

## 9. 可观测性

- `haas.identity.authenticated`
- `haas.identity.missing_credential`
- `haas.identity.invalid_credential`
- `haas.identity.scope_denied`

Metrics：`haas_identity_auth_total{outcome}`，label 低基数，不含明文身份。

## 10. 失败与恢复

| 场景 | 行为 |
|------|------|
| bearer 缺失 | `401 missing_credential` |
| bearer 无效/过期 | `401 invalid_credential` |
| caller scope 不属于 principal | `404`，不暴露存在性 |
| identity provider 不可用 | fail closed `503 haas_identity_unavailable` |
| admin 通道失败 | 普通路径继续；admin/debug 路径失败关闭 |

## 11. 测试计划与验收

- Unit：`authenticate` 三类输出、defaultUserId 派生、allowedUserIds 精确判断、独立 scope user delegation、拒绝显式 identity override、`is_admin`。
- Integration：两 principal 互相访问 harness/session/invocation/file 全 404。
- Security：token 明文、完整 principal 不进入日志/metrics。
