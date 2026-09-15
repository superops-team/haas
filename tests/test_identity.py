"""Identity boundary tests (specs/identity/README.md)."""

import pytest

from haas.identity import (
    InvalidCredentialError,
    MissingCredentialError,
    Principal,
    StaticTokenIdentityProvider,
)


@pytest.fixture
def provider() -> StaticTokenIdentityProvider:
    return StaticTokenIdentityProvider(
        {
            "tok_user": Principal(principalId="p_user", userIds=frozenset({"u_1", "u_2"})),
            "tok_admin": Principal(principalId="p_admin", roles=frozenset({"admin", "user"})),
        }
    )


async def test_authenticate_ok_missing_and_invalid(provider: StaticTokenIdentityProvider) -> None:
    principal = await provider.authenticate("Bearer tok_user")
    assert principal.principalId == "p_user"

    with pytest.raises(MissingCredentialError):
        await provider.authenticate(None)
    with pytest.raises(InvalidCredentialError):
        await provider.authenticate("Bearer unknown")
    with pytest.raises(InvalidCredentialError):
        await provider.authenticate("Basic abc")


def test_owns_scope(provider: StaticTokenIdentityProvider) -> None:
    principal = Principal(principalId="p_user", tenantId="t1", userIds=frozenset({"u_1"}))
    assert provider.owns(principal, tenant_id="t1", user_id="u_1") is True
    assert provider.owns(principal, tenant_id="t2") is False
    assert provider.owns(principal, user_id="u_other") is False
    # no userId restriction configured -> any user is allowed
    open_principal = Principal(principalId="p_open", tenantId="t1")
    assert provider.owns(open_principal, tenant_id="t1", user_id="anyone") is True


def test_is_admin(provider: StaticTokenIdentityProvider) -> None:
    user = Principal(principalId="p_user")
    admin = Principal(principalId="p_admin", roles=frozenset({"admin"}))
    assert provider.is_admin(admin) is True
    assert provider.is_admin(user) is False
