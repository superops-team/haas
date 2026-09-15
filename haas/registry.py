"""Harness Registry: configured harness catalog and appName resolution.

Implements specs/harness-registry §4 (resolution + scope), §5.1.1 (immutable
fields), §5.1.2 (base availability) and §5.1.3 (scope binding).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from typing import Any

from haas.identity import Principal
from haas.stores import HarnessRecord, MemoryStore
from haas.stores.memory import AccountKey, ProviderConfig

# Fields the caller may never change through PUT (specs/harness-registry §5.1.1).
IMMUTABLE_FIELDS = ("id", "base", "createdAtMs")
# Server-owned; a caller-supplied value is ignored rather than rejected.
SERVER_OWNED_FIELDS = ("object", "updatedAtMs", "baseLabel", "adapterCapabilities")

BASE_LABELS = {
    "codex": "Codex",
    "pi": "Pi",
    "opencode": "OpenCode",
    "amp": "AMP",
    "fake": "Fake",
}


class AppNotFoundError(Exception):
    """appName resolution failed (maps to 404 app_not_found)."""


class HarnessNotFoundError(Exception):
    """harness id unknown or out of caller scope (404 haas_harness_not_found)."""


class UnsupportedBaseError(Exception):
    """base is not a registered adapter (422 haas_unsupported_base)."""


class ImmutableFieldError(Exception):
    """PUT tried to change an immutable field (400 invalid_input)."""


class SkillBundleInvalidError(Exception):
    """Skill bundle failed validation (422 haas_skill_source_invalid)."""


def validate_skills(skills: list[Any]) -> list[dict[str, Any]]:
    """Validate skill bundles (specs/mcp-tool-skill-runtime §8/§10).

    Rules: paths stay inside the skill root, no absolute paths, no `..`
    segments, no control characters, and an enabled bundle must ship SKILL.md.
    """
    validated: list[dict[str, Any]] = []
    for bundle in skills:
        if not isinstance(bundle, dict):
            raise SkillBundleInvalidError("skill bundle must be an object")
        files = bundle.get("files")
        if not isinstance(files, list):
            raise SkillBundleInvalidError("skill bundle requires files[]")

        paths: list[str] = []
        for entry in files:
            if not isinstance(entry, dict):
                raise SkillBundleInvalidError("skill file must be an object")
            path = entry.get("path")
            if not isinstance(path, str) or not path:
                raise SkillBundleInvalidError("skill file requires a path")
            _validate_skill_path(path)
            paths.append(path)

        # `enabled` defaults to True: an unspecified bundle is still usable.
        if bundle.get("enabled", True) and "SKILL.md" not in paths:
            raise SkillBundleInvalidError("enabled skill bundle requires SKILL.md")
        validated.append(dict(bundle))
    return validated


def _validate_skill_path(path: str) -> None:
    import posixpath

    if path.startswith("/") or (len(path) > 1 and path[1] == ":"):
        raise SkillBundleInvalidError("absolute skill path")
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in path):
        raise SkillBundleInvalidError("control character in skill path")
    if "\\" in path:
        raise SkillBundleInvalidError("backslash in skill path")
    # normpath collapses `a/../b`; anything still escaping is rejected.
    normalized = posixpath.normpath(path)
    if normalized.startswith("../") or normalized in {"..", "."}:
        raise SkillBundleInvalidError("skill path escapes bundle root")
    if any(segment == ".." for segment in path.split("/")):
        raise SkillBundleInvalidError("skill path contains a parent segment")


def account_of(principal: Principal) -> AccountKey:
    return (principal.tenantId, principal.workspaceId)


@dataclass
class HarnessRegistry:
    store: MemoryStore
    # Bases backed by an assembled adapter. Populated by the app factory so
    # that adding an adapter makes its base usable without editing a list.
    known_bases: frozenset[str] = frozenset({"codex", "fake"})

    # --- scope helpers --------------------------------------------------

    def _visible(self, harness: HarnessRecord, principal: Principal) -> bool:
        return harness.account_key() == account_of(principal)

    # --- reads ----------------------------------------------------------

    def save(self, harness: HarnessRecord) -> HarnessRecord:
        return self.store.save_harness(harness)

    def get(self, harness_id: str) -> HarnessRecord | None:
        return self.store.get_harness(harness_id)

    def get_scoped(self, principal: Principal, harness_id: str) -> HarnessRecord:
        """Read one harness inside caller scope, else 404 (never 403)."""
        record = self.store.get_harness(harness_id)
        if record is None or record.status == "deleted":
            raise HarnessNotFoundError(harness_id)
        if not self._visible(record, principal):
            raise HarnessNotFoundError(harness_id)
        return record

    def list_active(self, principal: Principal) -> list[HarnessRecord]:
        return [h for h in self.store.list_harnesses(account_of(principal)) if h.status == "active"]

    def list_apps(self, principal: Principal) -> list[str]:
        return [h.id for h in self.list_active(principal)]

    def resolve_app(self, principal: Principal, app_name: str) -> HarnessRecord:
        """Resolve by id first, then by name; multiple/zero matches -> 404.

        Resolution is scope-bound: another tenant's harness is invisible even
        when its id is guessed correctly.
        """
        by_id = self.store.get_harness(app_name)
        if by_id is not None and by_id.status == "active" and self._visible(by_id, principal):
            return by_id

        matches = [h for h in self.list_active(principal) if h.name == app_name]
        if len(matches) == 1:
            return matches[0]
        raise AppNotFoundError(app_name)

    def resolve_default_app(self, principal: Principal) -> HarnessRecord:
        apps = self.list_active(principal)
        if not apps:
            raise AppNotFoundError("<default>")
        return apps[0]

    # --- writes ---------------------------------------------------------

    def create(self, principal: Principal, body: dict[str, Any]) -> HarnessRecord:
        base = body.get("base")
        if not isinstance(base, str) or not base.strip():
            raise ValueError("base is required")
        self._require_known_base(base)
        harness_id = f"chrn_{uuid.uuid4().hex[:16]}"
        record = HarnessRecord(
            id=harness_id,
            name=str(body.get("name") or harness_id),
            base=base,
            status="active",
            tenantId=principal.tenantId,
            workspaceId=principal.workspaceId,
            **_mutable_fields(body),
        )
        return self.store.save_harness(record)

    def update(self, principal: Principal, harness_id: str, body: dict[str, Any]) -> HarnessRecord:
        current = self.get_scoped(principal, harness_id)
        self._reject_immutable_conflicts(current, body)
        base = body.get("base")
        if isinstance(base, str) and base.strip():
            self._require_known_base(base)
        updated = replace(
            current,
            name=str(body.get("name") or current.name),
            **_mutable_fields(body),
        )
        return self.store.save_harness(updated)

    def delete(self, principal: Principal, harness_id: str) -> None:
        """Soft delete: harness stops resolving, historical sessions remain."""
        current = self.get_scoped(principal, harness_id)
        self.store.save_harness(replace(current, status="deleted"))

    # --- validation -----------------------------------------------------

    def _require_known_base(self, base: str) -> None:
        if base not in self.known_bases:
            raise UnsupportedBaseError(base)

    def _reject_immutable_conflicts(self, current: HarnessRecord, body: dict[str, Any]) -> None:
        """Matching values are accepted so read-modify-write stays idempotent."""
        for name in IMMUTABLE_FIELDS:
            if name not in body:
                continue
            supplied = body[name]
            existing = getattr(current, name)
            if supplied != existing:
                raise ImmutableFieldError(name)


def _mutable_fields(body: dict[str, Any]) -> dict[str, Any]:
    """Project a HarnessCreate body onto mutable HarnessRecord fields."""
    fields: dict[str, Any] = {}
    if "defaultModel" in body:
        value = body["defaultModel"]
        fields["defaultModel"] = str(value) if value is not None else None
    if "systemPrompt" in body:
        fields["systemPrompt"] = str(body.get("systemPrompt") or "")
    for name in ("mcpServers", "skills"):
        if name in body:
            value = body[name]
            items = (
                [dict(item) for item in value if isinstance(item, dict)]
                if isinstance(value, list)
                else []
            )
            # Skill bundles are validated before they can reach a stored
            # harness (specs/mcp-tool-skill-runtime §8/§10).
            fields[name] = validate_skills(items) if name == "skills" else items
    if "disabledTools" in body:
        value = body["disabledTools"]
        fields["disabledTools"] = [str(v) for v in value] if isinstance(value, list) else []
    for name in ("maxStep", "timeoutSeconds"):
        if name in body:
            value = body[name]
            fields[name] = int(value) if isinstance(value, int) else None
    if "provider" in body:
        fields["provider"] = _provider_from(body["provider"])
    return fields


def _provider_from(value: Any) -> ProviderConfig | None:
    if not isinstance(value, dict):
        return None
    provider = ProviderConfig(
        providerId=str(value.get("providerId") or ""),
        name=str(value.get("name") or "openai-compatible"),
        baseUrl=str(value.get("baseUrl") or ""),
        wireApi=str(value.get("wireApi") or "responses"),
        apiType=str(value.get("apiType") or ""),
        # Only the reference is stored; raw secrets never enter the registry
        # (specs/harness-registry §8).
        credentialRef=str(value.get("credentialRef") or ""),
        credentialFingerprint=str(value.get("credentialFingerprint") or ""),
        allowlistRuleId=str(value.get("allowlistRuleId") or ""),
    )
    validate_provider_config(provider)
    return provider


def validate_provider_config(provider: ProviderConfig) -> None:
    """Validate the ProviderRoute discriminated contract without guessing."""
    if not provider.providerId:
        raise ValueError("providerId is required")
    if not provider.name:
        raise ValueError("provider name is required")
    if provider.wireApi not in {"openai-compatible", "responses", "agent-plan"}:
        raise ValueError("unsupported wireApi")
    if provider.wireApi == "responses":
        if provider.apiType != "responses":
            raise ValueError("responses wireApi requires apiType=responses")
    elif provider.apiType not in {"responses", "chat_completions"}:
        raise ValueError(f"{provider.wireApi} wireApi requires apiType")


def harness_to_dict(harness: HarnessRecord) -> dict[str, Any]:
    """Serialize to the OpenAPI `Harness` shape (object is a required const)."""
    provider: dict[str, Any] | None = None
    if harness.provider is not None:
        provider = {
            "providerId": harness.provider.providerId,
            "name": harness.provider.name,
            "baseUrl": harness.provider.baseUrl,
            "wireApi": harness.provider.wireApi,
            "apiType": harness.provider.apiType,
            "credentialRef": harness.provider.credentialRef,
            "credentialFingerprint": harness.provider.credentialFingerprint,
            "allowlistRuleId": harness.provider.allowlistRuleId,
        }
    return {
        "id": harness.id,
        "object": "harness",
        "name": harness.name,
        "base": harness.base,
        "baseLabel": BASE_LABELS.get(harness.base, harness.base),
        "defaultModel": harness.defaultModel or "",
        "systemPrompt": harness.systemPrompt,
        "mcpServers": list(harness.mcpServers),
        "skills": list(harness.skills),
        "disabledTools": list(harness.disabledTools),
        "provider": provider,
        "maxStep": harness.maxStep,
        "timeoutSeconds": harness.timeoutSeconds,
        "createdAtMs": harness.createdAtMs,
        "updatedAtMs": harness.updatedAtMs,
    }


def seed_codex(
    registry: HarnessRegistry,
    *,
    principal: Principal | None = None,
    scopes: list[AccountKey] | None = None,
) -> HarnessRecord:
    """Seed the P0 Codex configured harness used by S2 protocol tests.

    A harness is only visible inside its own account scope
    (specs/harness-registry §5.1.3), so the seed must be materialized once per
    deployment scope; otherwise a tenant-bound principal sees an empty catalog
    and every `/run` 404s. Pass `principal` to bind the seed to one caller, or
    `scopes` to fan it out across several. The first scope keeps the stable
    `chrn_codex_default` id, and that record is returned so existing callers
    relying on a single return value keep working.
    """
    targets: list[AccountKey]
    if scopes:
        targets = scopes
    elif principal is not None:
        targets = [account_of(principal)]
    else:
        targets = [(None, None)]

    seeded: list[HarnessRecord] = []
    for index, (tenant_id, workspace_id) in enumerate(targets):
        harness_id = "chrn_codex_default" if index == 0 else f"chrn_codex_default_{index}"
        seeded.append(
            registry.save(
                HarnessRecord(
                    id=harness_id,
                    name="codex-default",
                    base="codex",
                    status="active",
                    defaultModel="gpt-5.6-terra",
                    tenantId=tenant_id,
                    workspaceId=workspace_id,
                )
            )
        )
    return seeded[0]
