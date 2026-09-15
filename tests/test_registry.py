"""Harness Registry tests (specs/harness-registry/README.md)."""

import pytest

from haas.identity import Principal
from haas.registry import AppNotFoundError, HarnessRegistry, harness_to_dict, seed_codex
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
            id="chrn_dup_1",
            name="dup",
            base="codex",
            status="active",
            tenantId=principal.tenantId,
        )
    )
    registry.save(
        HarnessRecord(
            id="chrn_dup_2",
            name="dup",
            base="codex",
            status="active",
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


def test_provider_route_round_trip_preserves_contract_fields(
    registry: HarnessRegistry, principal: Principal
) -> None:
    record = registry.create(
        principal,
        {
            "base": "codex",
            "provider": {
                "providerId": "volcengine-ark",
                "name": "ark-cn",
                "baseUrl": "https://ark.example.com/api/v3",
                "wireApi": "agent-plan",
                "apiType": "responses",
                "credentialRef": "secret://ark",
            },
        },
    )
    assert harness_to_dict(record)["provider"] == {
        "providerId": "volcengine-ark",
        "name": "ark-cn",
        "baseUrl": "https://ark.example.com/api/v3",
        "wireApi": "agent-plan",
        "apiType": "responses",
        "credentialRef": "secret://ark",
        "credentialFingerprint": "",
        "allowlistRuleId": "",
    }


def test_registry_rejects_invalid_provider_route(
    registry: HarnessRegistry, principal: Principal
) -> None:
    with pytest.raises(ValueError, match="apiType"):
        registry.create(
            principal,
            {
                "base": "codex",
                "provider": {
                    "providerId": "openai",
                    "name": "openai",
                    "baseUrl": "https://example.com/v1",
                    "wireApi": "openai-compatible",
                    "credentialRef": "secret://openai",
                },
            },
        )
