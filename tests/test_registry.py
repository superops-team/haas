"""Harness Registry tests (specs/harness-registry/README.md)."""

import pytest

from haas.identity import Principal
from haas.registry import (
    AppNotFoundError,
    HarnessRegistry,
    HarnessNotFoundError,
    ImmutableFieldError,
    SkillBundleInvalidError,
    UnsupportedBaseError,
    harness_to_dict,
    seed_codex,
    validate_skills,
)
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


# --- get_scoped error paths ---------------------------------------------


def test_get_scoped_unknown_id_is_404(
    registry: HarnessRegistry, principal: Principal
) -> None:
    with pytest.raises(HarnessNotFoundError):
        registry.get_scoped(principal, "chrn_missing")


def test_get_scoped_deleted_record_is_404(
    registry: HarnessRegistry, principal: Principal
) -> None:
    record = registry.create(principal, {"base": "codex", "name": "gone"})
    registry.delete(principal, record.id)
    with pytest.raises(HarnessNotFoundError):
        registry.get_scoped(principal, record.id)


def test_get_scoped_cross_scope_is_indistinguishable_from_missing(
    registry: HarnessRegistry, principal: Principal
) -> None:
    record = registry.create(principal, {"base": "codex", "name": "mine"})
    other = Principal(principalId="p_other", tenantId="t_other")
    with pytest.raises(HarnessNotFoundError):
        registry.get_scoped(other, record.id)


# --- create validation --------------------------------------------------


def test_create_requires_a_base(registry: HarnessRegistry, principal: Principal) -> None:
    with pytest.raises(ValueError, match="base is required"):
        registry.create(principal, {"name": "no-base"})
    with pytest.raises(ValueError, match="base is required"):
        registry.create(principal, {"base": "   "})


def test_create_rejects_unknown_base(
    registry: HarnessRegistry, principal: Principal
) -> None:
    with pytest.raises(UnsupportedBaseError):
        registry.create(principal, {"base": "does-not-exist"})


def test_create_projects_mutable_fields(
    registry: HarnessRegistry, principal: Principal
) -> None:
    record = registry.create(
        principal,
        {
            "base": "codex",
            "name": "full",
            "defaultModel": "gpt-x",
            "systemPrompt": "be terse",
            "mcpServers": [{"name": "fs"}],
            "skills": [{"id": "s1", "files": [{"path": "SKILL.md"}]}],
            "disabledTools": ["web_search"],
            "maxStep": 12,
            "timeoutSeconds": 60,
        },
    )
    assert record.defaultModel == "gpt-x"
    assert record.systemPrompt == "be terse"
    assert record.mcpServers == [{"name": "fs"}]
    assert record.disabledTools == ["web_search"]
    assert record.maxStep == 12
    assert record.timeoutSeconds == 60


# --- update / delete ----------------------------------------------------


def test_update_renames_and_rejects_immutable_change(
    registry: HarnessRegistry, principal: Principal
) -> None:
    record = registry.create(principal, {"base": "codex", "name": "before"})
    updated = registry.update(principal, record.id, {"name": "after"})
    assert updated.name == "after"
    # Re-supplying the same immutable id is idempotent; changing it is rejected.
    with pytest.raises(ImmutableFieldError):
        registry.update(principal, record.id, {"id": "chrn_different"})
    # Matching immutable values are accepted.
    again = registry.update(principal, record.id, {"id": record.id, "name": "again"})
    assert again.name == "again"


def test_update_revalidates_passed_base(
    registry: HarnessRegistry, principal: Principal
) -> None:
    record = registry.create(principal, {"base": "codex"})
    # Re-supplying the same immutable base passes the immutable check and is
    # re-validated against known bases (line 183).
    updated = registry.update(principal, record.id, {"base": "codex"})
    assert updated.base == "codex"
    # Changing base outright is rejected as immutable (it is an immutable field).
    with pytest.raises(ImmutableFieldError):
        registry.update(principal, record.id, {"base": "pi"})


# --- harness_to_dict ----------------------------------------------------


def test_harness_to_dict_without_provider(
    registry: HarnessRegistry, principal: Principal
) -> None:
    record = registry.create(principal, {"base": "codex", "name": "noprovider"})
    dumped = harness_to_dict(record)
    assert dumped["provider"] is None
    assert dumped["object"] == "harness"
    assert dumped["baseLabel"] == "Codex"


# --- validate_skills -----------------------------------------------------


def test_validate_skills_rejects_non_object_and_bad_files() -> None:
    with pytest.raises(SkillBundleInvalidError, match="object"):
        validate_skills(["not-a-dict"])  # type: ignore[list-item]
    with pytest.raises(SkillBundleInvalidError, match="files"):
        validate_skills([{"id": "x"}])
    with pytest.raises(SkillBundleInvalidError, match="skill file"):
        validate_skills([{"id": "x", "files": ["nope"]}])  # type: ignore[list-item]
    with pytest.raises(SkillBundleInvalidError, match="path"):
        validate_skills([{"id": "x", "files": [{"path": 5}]}])


def test_validate_skills_requires_skill_md_for_enabled_bundle() -> None:
    with pytest.raises(SkillBundleInvalidError, match="SKILL.md"):
        validate_skills([{"id": "x", "files": [{"path": "README.md"}]}])
    # Disabled bundles need no SKILL.md.
    out = validate_skills(
        [{"id": "x", "enabled": False, "files": [{"path": "README.md"}]}]
    )
    assert out[0]["id"] == "x"


def test_validate_skill_path_rejects_traversal_and_absolute() -> None:
    with pytest.raises(SkillBundleInvalidError, match="absolute"):
        validate_skills([{"id": "x", "files": [{"path": "/etc/passwd"}]}])
    with pytest.raises(SkillBundleInvalidError, match="escapes bundle root"):
        validate_skills([{"id": "x", "files": [{"path": "../escape"}]}])
    # An embedded parent segment that normpath does not collapse out still trips
    # the explicit parent-segment guard.
    with pytest.raises(SkillBundleInvalidError, match="parent"):
        validate_skills([{"id": "x", "files": [{"path": "a/../b"}]}])
    with pytest.raises(SkillBundleInvalidError, match="backslash"):
        validate_skills([{"id": "x", "files": [{"path": "a\\b"}]}])
    with pytest.raises(SkillBundleInvalidError, match="control character"):
        validate_skills([{"id": "x", "files": [{"path": "a\x01b"}]}])


# --- provider validation ------------------------------------------------


def test_provider_validation_cases(registry: HarnessRegistry) -> None:
    from haas.registry import validate_provider_config
    from haas.stores.memory import ProviderConfig

    with pytest.raises(ValueError, match="providerId"):
        validate_provider_config(
            ProviderConfig(
                providerId="", name="n", wireApi="responses", apiType="responses"
            )
        )
    with pytest.raises(ValueError, match="name"):
        validate_provider_config(
            ProviderConfig(
                providerId="p", name="", wireApi="responses", apiType="responses"
            )
        )
    with pytest.raises(ValueError, match="wireApi"):
        validate_provider_config(
            ProviderConfig(
                providerId="p", name="n", wireApi="bogus", apiType="responses"
            )
        )
    # responses wireApi requires apiType=responses.
    with pytest.raises(ValueError, match="responses"):
        validate_provider_config(
            ProviderConfig(
                providerId="p", name="n", wireApi="responses", apiType="chat_completions"
            )
        )


# --- seed_codex fan-out across scopes -----------------------------------


def test_seed_codex_fans_out_to_multiple_scopes() -> None:
    reg = HarnessRegistry(store=MemoryStore())
    a = Principal(principalId="p_a", tenantId="ta")
    b = Principal(principalId="p_b", tenantId="tb")
    first = seed_codex(reg, scopes=[(a.tenantId, a.workspaceId), (b.tenantId, b.workspaceId)])
    assert first.id == "chrn_codex_default"
    assert reg.list_apps(a) == ["chrn_codex_default"]
    assert reg.list_apps(b) == ["chrn_codex_default_1"]
