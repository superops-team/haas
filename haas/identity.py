"""Identity boundary: bearer -> Principal and scope checks (specs/identity/)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class MissingCredentialError(Exception):
    """No/bad Authorization header -> 401 missing_credential."""


class InvalidCredentialError(Exception):
    """Unrecognized bearer -> 401 invalid_credential."""


@dataclass(frozen=True)
class Principal:
    principalId: str
    tenantId: str | None = None
    workspaceId: str | None = None
    roles: frozenset[str] = frozenset({"user"})
    userIds: frozenset[str] | None = None


class IdentityProvider(Protocol):
    async def authenticate(self, authorization: str | None) -> Principal: ...
    def owns(
        self,
        principal: Principal,
        *,
        tenant_id: str | None = None,
        workspace_id: str | None = None,
        user_id: str | None = None,
    ) -> bool: ...
    def is_admin(self, principal: Principal) -> bool: ...


class StaticTokenIdentityProvider:
    """Static token -> Principal mapping for tests and single-node deployments."""

    def __init__(self, tokens: dict[str, Principal] | None = None) -> None:
        self._tokens = dict(tokens or {})

    def principals(self) -> list[Principal]:
        """Configured principals, used to materialize per-scope seed data."""
        return list(self._tokens.values())

    async def authenticate(self, authorization: str | None) -> Principal:
        if not authorization:
            raise MissingCredentialError("missing credential")
        if not authorization.startswith("Bearer "):
            raise InvalidCredentialError("invalid credential")
        token = authorization[len("Bearer ") :].strip()
        principal = self._tokens.get(token)
        if principal is None:
            raise InvalidCredentialError("invalid credential")
        return principal

    def owns(
        self,
        principal: Principal,
        *,
        tenant_id: str | None = None,
        workspace_id: str | None = None,
        user_id: str | None = None,
    ) -> bool:
        if tenant_id is not None and principal.tenantId != tenant_id:
            return False
        if workspace_id is not None and principal.workspaceId != workspace_id:
            return False
        if user_id is not None and principal.userIds is not None:
            return user_id in principal.userIds
        return True

    def is_admin(self, principal: Principal) -> bool:
        return "admin" in principal.roles
