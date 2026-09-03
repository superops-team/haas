# Identity Component Specification

**English** | [简体中文](README.zh-CN.md)

Status: Draft
Last reviewed: 2026-08-26
Related specs: [Security Boundary](../security-boundary/README.md), [HaaS Protocol](../haas-protocol/README.md), [Session Runtime](../session-runtime/README.md)

## 1. Component Role

Identity defines the authentication and identity boundary for HaaS. HaaS does not implement a specific auth provider, but it MUST define a stable `IdentityProvider` interface that converts `Authorization: Bearer` into a `Principal` and associates caller-supplied `tenantId`/`workspaceId`/`userId` values with the principal scope.

The initial release provides `StaticTokenIdentityProvider` for testing and single-node deployments. Production deployments delegate external OIDC/JWT validation through `ExternalJwtIdentityProvider`, which is a reserved interface not bound to a specific implementation.

## 2. Sources and Rationale

| Source | Adopted content |
|------|----------|
| Security Boundary | No specific auth provider implementation; cross-scope access returns 404 rather than 403 |
| HaaS Protocol | `Authorization: Bearer` is required except for health/ready |
| Session Runtime | `userId` MUST belong to the authenticated principal |

## 3. Upstream and Downstream Relationships

| Direction | Component | Relationship |
|------|------|------|
| Upstream | HaaS Protocol | Authenticates each request before it enters the component pipeline |
| Upstream | Security Boundary | Provides the principal facts used for scope decisions |
| Downstream | Session Runtime / Registry / Artifact / Admission | Provides `Principal` as the basis for scope decisions |
| Downstream | Observability | Provides `principalHash`, never plaintext identity |

## 4. Responsibility Boundaries

Responsibilities:

- Define `IdentityProvider.authenticate(authorization) -> Principal`.
- Define `owns()` to determine whether a tenant/workspace/userId belongs to a principal.
- Define the boundary between 401 (missing/invalid credentials) and 404 (the object exists but is unauthorized).
- Define an independent authorization channel for admin/debug access; it MUST NOT be derived from an ordinary bearer token.

Non-responsibilities:

- Does not implement the OIDC/OAuth/token issuance service itself.
- MUST NOT store plaintext caller tokens or complete identities in logs.
- Does not perform business billing or entitlement checks.

## 5. Core Interfaces

```python
class Principal(TypedDict):
    principalId: str
    tenantId: str | None
    workspaceId: str | None
    roles: list[str]

class IdentityProvider(Protocol):
    async def authenticate(self, authorization: str | None) -> Principal:
        """Return Principal or raise MissingCredential / InvalidCredential."""
    def owns(self, principal: Principal, *, tenant_id=None, workspace_id=None, user_id=None) -> bool:
        """Return True when the caller-supplied scope belongs to the principal."""
    def is_admin(self, principal: Principal) -> bool:
        """True only for an independently granted admin/debug role."""
```

Implementation registry:

| Implementation | Purpose |
|------|------|
| `StaticTokenIdentityProvider` | Static token -> principal mapping for single-node deployments and tests |
| `ExternalJwtIdentityProvider` | Delegates external JWKS/OIDC validation (reserved) |

## 6. Data Model

```json
{
  "principalId": "p_abc",
  "tenantId": "tenant_1",
  "workspaceId": "workspace_1",
  "roles": ["user"]
}
```

`userId` is not a fixed field of the principal. It is a sub-scope within the principal scope and is evaluated by `owns(principal, user_id=...)`. By default, `userId` MAY be derived from the principal (`defaultUserId`), or the caller MAY declare it explicitly for validation by `owns`.

## 7. Runtime Model and State Machine

```text
request -> identity.authenticate(bearer)
  -> missing/invalid -> 401 missing_credential / invalid_credential
  -> principal resolved
  -> caller scope (tenant/workspace/user) checked via owns()
  -> mismatch -> 404 (not 403)
  -> proceed with principal bound to request context
```

## 8. Security and Authorization

- Authentication failures fall into two classes: missing/invalid credentials return `401`; valid credentials for an object outside their scope return `404`.
- Admin/debug access requires independent authorization (`is_admin`) and MUST NOT be derived from an ordinary bearer token.
- Logs MUST contain only `principalHash`, tenant/workspace hashes, or safe IDs.

## 9. Observability

- `haas.identity.authenticated`
- `haas.identity.missing_credential`
- `haas.identity.invalid_credential`
- `haas.identity.scope_denied`

Metric: `haas_identity_auth_total{outcome}`. Labels MUST be low-cardinality and MUST NOT contain plaintext identities.

## 10. Failure and Recovery

| Scenario | Behavior |
|------|------|
| Bearer token missing | `401 missing_credential` |
| Bearer token invalid/expired | `401 invalid_credential` |
| Caller scope does not belong to the principal | `404`, without revealing existence |
| Identity provider unavailable | Fail closed with `503 haas_identity_unavailable` |
| Admin channel failure | Ordinary paths continue; admin/debug paths fail closed |

## 11. Test Plan and Acceptance Criteria

- Unit: the three `authenticate` outcomes, `owns` decisions, and `is_admin`.
- Integration: cross-access by two principals to each other's harness/session/invocation/file returns 404 in all cases.
- Security: plaintext tokens and complete principals never enter logs or metrics.
