"""Harness Registry tests (specs/harness-registry/README.md)."""
import pytest

from haas.identity import Principal
from haas.registry import AppNotFoundError, HarnessRegistry, seed_codex
from haas.stores import HarnessRecord, MemoryStore


@pytest.fixture
def registry() -> HarnessRegistry:
    return HarnessRegistry(store=MemoryStore())


@pytest.fixture
def principal() -> Principal:
    return Principal(principalId="p_1", tenantId="t1")


def test_list_apps_and_resolve_by_id_and_name(
    registry: HarnessRegistry, principal: Principal
) -> None:
    seed_codex(registry, principal=principal)
    assert registry.list_apps(principal) == ["chrn_codex_default"]
    assert registry.resolve_app(principal, "chrn_codex_default").name == "codex-default"
    assert registry.resolve_app(principal, "codex-default").id == "chrn_codex_default"


def test_resolve_unknown_or_ambiguous(registry: HarnessRegistry, principal: Principal) -> None:
    seed_codex(registry, principal=principal)
    registry.save(
        HarnessRecord(
            id="chrn_dup_1", name="dup", base="codex", status="active",
            tenantId=principal.tenantId,
        )
    )
    registry.save(
        HarnessRecord(
            id="chrn_dup_2", name="dup", base="codex", status="active",
            tenantId=principal.tenantId,
        )
    )
    with pytest.raises(AppNotFoundError):
        registry.resolve_app(principal, "missing")
    with pytest.raises(AppNotFoundError):
        registry.resolve_app(principal, "dup")


def test_default_app(registry: HarnessRegistry, principal: Principal) -> None:
    with pytest.raises(AppNotFoundError):
        registry.resolve_default_app(principal)
    seed_codex(registry, principal=principal)
    assert registry.resolve_default_app(principal).id == "chrn_codex_default"
