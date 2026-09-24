"""Unit-level coverage for HarnessProfileService internals: fingerprinting,
activate conflicts, resolve_ref, execution_snapshot and static findings."""

from __future__ import annotations

import pytest

from haas.identity import Principal
from haas.profiles import (
    HarnessProfileService,
    ProfileConflictError,
    ProfileNotFoundError,
    execution_intent_fingerprint,
)
from haas.registry import HarnessRegistry
from haas.stores import HarnessRecord, MemoryStore, ProfileRecord

PRINCIPAL = Principal(principalId="p", tenantId="t", workspaceId="w")


def _provider(**over: object) -> dict:
    base = {
        "providerId": "openai",
        "name": "openai",
        "model": "gpt-test",
        "credentialRef": "secret://tenant/provider/default",
        "wireApi": "responses",
        "apiType": "responses",
    }
    base.update(over)  # type: ignore[arg-type]
    return base


def _service() -> HarnessProfileService:
    store = MemoryStore()
    registry = HarnessRegistry(store=store)
    registry.save(HarnessRecord(id="chrn_1", name="codex", base="codex"))
    return HarnessProfileService(store=store, registry=registry)


def _make_profile(
    service: HarnessProfileService,
    *,
    version: int = 1,
    status: str = "draft",
    fingerprint: str = "sha256:real",
    content: dict | None = None,
) -> ProfileRecord:
    body = {
        "harnessId": "chrn_1",
        "base": "codex",
        "provider": _provider(),
    }
    if content is not None:
        body = content
    return service.store.save_profile(
        ProfileRecord(
            id=f"hprof_{version}",
            harnessId="chrn_1",
            base="codex",
            version=version,
            content=body,
            profileFingerprint=fingerprint,
            status=status,
            tenantId="t",
            workspaceId="w",
            createdAtMs=1,
        )
    )


# --- fingerprinting ---------------------------------------------------------


def test_execution_intent_fingerprint_strips_credentials() -> None:
    a = execution_intent_fingerprint(
        {"provider": {"model": "gpt", "credentialRef": "secret://x"}, "list": [{"credentialRef": "y"}]}
    )
    b = execution_intent_fingerprint(
        {"provider": {"model": "gpt"}, "list": [{}]}
    )
    assert a == b


# --- activate conflict paths ------------------------------------------------


def test_activate_rejects_fingerprint_drift() -> None:
    service = _service()
    _make_profile(service, fingerprint="sha256:stale")
    with pytest.raises(ProfileConflictError):
        service.activate(PRINCIPAL, "hprof_1")


def test_activate_rejects_lower_than_existing_active_version() -> None:
    service = _service()
    from haas.profiles import _fingerprint

    current_content = {"harnessId": "chrn_1", "base": "codex", "provider": _provider()}
    _make_profile(
        service,
        version=2,
        status="active",
        fingerprint=_fingerprint(current_content),
        content=current_content,
    )
    draft = _make_profile(
        service,
        version=1,
        status="draft",
        fingerprint=_fingerprint(current_content),
        content=current_content,
    )
    # Mark draft as validated.
    service.store.save_profile(
        type(draft)(**{**draft.__dict__, "validation": {"valid": True, "findings": []}})
    )
    with pytest.raises(ProfileConflictError):
        service.activate(PRINCIPAL, draft.id)


# --- resolve_ref / execution_snapshot ---------------------------------------


def test_resolve_ref_rejects_fingerprint_mismatch() -> None:
    service = _service()
    _make_profile(service, fingerprint="sha256:a")
    with pytest.raises(ProfileNotFoundError):
        service.resolve_ref(
            PRINCIPAL,
            "chrn_1",
            {"profileId": "hprof_1", "profileVersion": 1, "profileFingerprint": "sha256:different"},
        )


def test_execution_snapshot_rejects_unvalidated_profile() -> None:
    service = _service()
    profile = _make_profile(service, status="draft")
    with pytest.raises(ProfileConflictError):
        service.execution_snapshot(profile)


# --- static findings ----------------------------------------------------------


def test_findings_flag_openai_compatible_without_api_type() -> None:
    service = _service()
    findings = service._findings(
        {"harnessId": "chrn_1", "base": "codex", "provider": _provider(apiType="", wireApi="openai-compatible")}
    )
    assert any(f["code"] == "haas_provider_invalid" and f["field"] == "provider.apiType" for f in findings)


def test_findings_flag_unsupported_wire_api() -> None:
    service = _service()
    findings = service._findings(
        {"harnessId": "chrn_1", "base": "codex", "provider": _provider(wireApi="gpt-json")}
    )
    assert any(f["field"] == "provider.wireApi" for f in findings)


def test_findings_flag_non_https_base_url() -> None:
    service = _service()
    findings = service._findings(
        {"harnessId": "chrn_1", "base": "codex", "provider": _provider(baseUrl="http://insecure.example/v1")}
    )
    assert any(f["field"] == "provider.baseUrl" for f in findings)


def test_findings_flag_invalid_agents_md_mode() -> None:
    service = _service()
    findings = service._findings(
        {
            "harnessId": "chrn_1",
            "base": "codex",
            "provider": _provider(),
            "agentsMd": {"mode": "live"},
        }
    )
    assert any(f["code"] == "haas_agents_md_invalid" for f in findings)


def test_findings_flag_unsafe_agents_md_path() -> None:
    service = _service()
    findings = service._findings(
        {
            "harnessId": "chrn_1",
            "base": "codex",
            "provider": _provider(),
            "agentsMd": {"mode": "snapshot", "sources": [{"path": "../AGENTS.md"}]},
        }
    )
    assert any(f["field"] == "agentsMd.sources" for f in findings)
