"""Versioned Harness Profile lifecycle and validation."""

from __future__ import annotations

import builtins
import hashlib
import json
import posixpath
import threading
import time
import uuid
from copy import deepcopy
from dataclasses import dataclass, replace
from typing import Any
from urllib.parse import urlsplit

from haas.identity import Principal
from haas.registry import (
    HarnessNotFoundError,
    HarnessRegistry,
    SkillBundleInvalidError,
    validate_skills,
)
from haas.stores.memory import MemoryStore, ProfileRecord

_CONTENT_FIELDS = frozenset(
    {
        "harnessId",
        "base",
        "name",
        "provider",
        "mcpServers",
        "skills",
        "agentsMd",
        "workspace",
        "policy",
        "budget",
        "metadata",
    }
)


class ProfileNotFoundError(Exception):
    pass


class ProfileConflictError(Exception):
    pass


def _fingerprint(content: dict[str, Any]) -> str:
    encoded = json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return f"sha256:{hashlib.sha256(encoded.encode()).hexdigest()}"


def execution_intent_fingerprint(content: dict[str, Any]) -> str:
    """Hash execution intent without supervisor-scoped credential handles."""

    def without_credentials(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: without_credentials(item)
                for key, item in value.items()
                if key != "credentialRef"
            }
        if isinstance(value, list):
            return [without_credentials(item) for item in value]
        return value

    return _fingerprint(without_credentials(content))


def profile_to_dict(profile: ProfileRecord) -> dict[str, Any]:
    return {
        "id": profile.id,
        "object": profile.object,
        **profile.content,
        "version": profile.version,
        "status": profile.status,
        "profileFingerprint": profile.profileFingerprint,
        "validation": profile.validation,
        "createdAtMs": profile.createdAtMs,
        "updatedAtMs": profile.updatedAtMs,
        "activatedAtMs": profile.activatedAtMs,
    }


@dataclass
class HarnessProfileService:
    store: MemoryStore
    registry: HarnessRegistry

    def __post_init__(self) -> None:
        self._lock = threading.RLock()

    def _scoped(self, principal: Principal, profile_id: str) -> ProfileRecord:
        profile = self.store.get_profile(profile_id)
        if profile is None or profile.account_key() != (principal.tenantId, principal.workspaceId):
            raise ProfileNotFoundError(profile_id)
        return profile

    def create(self, principal: Principal, body: dict[str, Any]) -> ProfileRecord:
        content = self._normalize_body(principal, body)
        harness_id = content["harnessId"]
        with self._lock:
            revisions = self.store.list_profiles(harness_id=harness_id)
            version = max((revision.version for revision in revisions), default=0) + 1
            return self.store.save_profile(
                ProfileRecord(
                    id=f"hprof_{uuid.uuid4().hex[:16]}",
                    harnessId=harness_id,
                    base=content["base"],
                    version=version,
                    content=content,
                    profileFingerprint=_fingerprint(content),
                    tenantId=principal.tenantId,
                    workspaceId=principal.workspaceId,
                )
            )

    def list(
        self, principal: Principal, *, harness_id: str | None = None, status: str | None = None
    ) -> builtins.list[ProfileRecord]:
        if status is not None and status not in {"draft", "active", "retired"}:
            raise ValueError("invalid profile status")
        return self.store.list_profiles(
            (principal.tenantId, principal.workspaceId), harness_id=harness_id, status=status
        )

    def get(self, principal: Principal, profile_id: str) -> ProfileRecord:
        return self._scoped(principal, profile_id)

    def update(self, principal: Principal, profile_id: str, body: dict[str, Any]) -> ProfileRecord:
        current = self._scoped(principal, profile_id)
        if current.status != "draft":
            raise ProfileConflictError(profile_id)
        content = self._normalize_body(principal, body)
        if content["harnessId"] != current.harnessId or content["base"] != current.base:
            raise ProfileConflictError(profile_id)
        return self.store.save_profile(
            replace(
                current,
                content=content,
                profileFingerprint=_fingerprint(content),
                validation=None,
            )
        )

    def validate(self, principal: Principal, profile_id: str) -> dict[str, Any]:
        current = self._scoped(principal, profile_id)
        findings = self._findings(current.content)
        validation = {
            "profileId": current.id,
            "valid": not any(item["severity"] == "error" for item in findings),
            "findings": findings,
        }
        self.store.save_profile(replace(current, validation=validation))
        return validation

    def activate(self, principal: Principal, profile_id: str) -> ProfileRecord:
        with self._lock:
            current = self._scoped(principal, profile_id)
            if current.status == "active":
                return current
            if (
                current.status != "draft"
                or not current.validation
                or not current.validation["valid"]
            ):
                raise ProfileConflictError(profile_id)
            if current.profileFingerprint != _fingerprint(current.content):
                raise ProfileConflictError(profile_id)
            active = self.store.list_profiles(
                (principal.tenantId, principal.workspaceId),
                harness_id=current.harnessId,
                status="active",
            )
            if active and current.version <= max(profile.version for profile in active):
                raise ProfileConflictError(profile_id)
            now = int(time.time() * 1000)
            for previous in active:
                self.store.save_profile(replace(previous, status="retired"))
            return self.store.save_profile(replace(current, status="active", activatedAtMs=now))

    def resolve_ref(
        self, principal: Principal, harness_id: str, profile_ref: dict[str, Any]
    ) -> ProfileRecord:
        profile = self._scoped(principal, str(profile_ref.get("profileId") or ""))
        if (
            profile.harnessId != harness_id
            or profile.version != profile_ref.get("profileVersion")
            or profile.profileFingerprint != profile_ref.get("profileFingerprint")
        ):
            raise ProfileNotFoundError(profile.id)
        return profile

    def execution_snapshot(self, profile: ProfileRecord) -> dict[str, Any]:
        if profile.status not in {"active", "retired"} or not (profile.validation or {}).get(
            "valid"
        ):
            raise ProfileConflictError("profile not validated")
        return {
            **deepcopy(profile.content),
            "profileId": profile.id,
            "profileVersion": profile.version,
            "profileFingerprint": profile.profileFingerprint,
            "resolvedAtMs": int(time.time() * 1000),
        }

    def _normalize_body(self, principal: Principal, body: dict[str, Any]) -> dict[str, Any]:
        if set(body) - _CONTENT_FIELDS:
            raise ValueError("unknown profile field")
        harness_id = body.get("harnessId")
        base = body.get("base")
        provider = body.get("provider")
        if (
            not isinstance(harness_id, str)
            or not isinstance(base, str)
            or not isinstance(provider, dict)
        ):
            raise ValueError("missing profile fields")
        try:
            harness = self.registry.get_scoped(principal, harness_id)
        except HarnessNotFoundError as exc:
            raise ProfileNotFoundError(harness_id) from exc
        if harness.base != base:
            raise ValueError("profile base does not match harness")
        content = {
            "harnessId": harness_id,
            "base": base,
            "provider": dict(provider),
            "mcpServers": list(body.get("mcpServers") or []),
            "skills": list(body.get("skills") or []),
        }
        for field in ("name", "agentsMd", "workspace", "policy", "budget", "metadata"):
            if field in body:
                content[field] = body[field]
        return content

    def _findings(self, content: dict[str, Any]) -> builtins.list[dict[str, Any]]:
        findings: builtins.list[dict[str, Any]] = []
        provider = content["provider"]
        required = ("providerId", "name", "model", "credentialRef", "wireApi")
        if any(not isinstance(provider.get(key), str) or not provider[key] for key in required):
            findings.append(
                self._error("haas_provider_invalid", "provider fields are invalid", "provider")
            )
        if not str(provider.get("credentialRef", "")).startswith("secret://"):
            findings.append(
                self._error(
                    "haas_provider_invalid",
                    "credentialRef must be a secret reference",
                    "provider.credentialRef",
                )
            )
        wire_api = provider.get("wireApi")
        api_type = provider.get("apiType")
        if wire_api == "responses" and api_type not in {None, "responses"}:
            findings.append(
                self._error(
                    "haas_provider_invalid",
                    "responses requires responses apiType",
                    "provider.apiType",
                )
            )
        elif wire_api in {"openai-compatible", "agent-plan"} and api_type not in {
            "responses",
            "chat_completions",
        }:
            findings.append(
                self._error("haas_provider_invalid", "apiType is required", "provider.apiType")
            )
        elif wire_api not in {"responses", "openai-compatible", "agent-plan"}:
            findings.append(
                self._error("haas_provider_invalid", "wireApi is unsupported", "provider.wireApi")
            )
        base_url = provider.get("baseUrl")
        if base_url is not None:
            parsed = urlsplit(str(base_url))
            if parsed.scheme != "https" or not parsed.hostname:
                findings.append(
                    self._error(
                        "haas_provider_invalid",
                        "provider URL must use https",
                        "provider.baseUrl",
                    )
                )
        try:
            validate_skills(content.get("skills", []))
        except SkillBundleInvalidError:
            findings.append(
                self._error("haas_skill_source_invalid", "skill source is invalid", "skills")
            )
        agents_md = content.get("agentsMd")
        if agents_md is not None:
            if not isinstance(agents_md, dict) or agents_md.get("mode") != "snapshot":
                findings.append(
                    self._error("haas_agents_md_invalid", "AGENTS.md mode is invalid", "agentsMd")
                )
            else:
                for source in agents_md.get("sources", []):
                    path = source.get("path") if isinstance(source, dict) else None
                    if not self._safe_relative_path(path):
                        findings.append(
                            self._error(
                                "haas_agents_md_invalid",
                                "AGENTS.md path is invalid",
                                "agentsMd.sources",
                            )
                        )
        return findings

    @staticmethod
    def _safe_relative_path(path: Any) -> bool:
        return (
            isinstance(path, str)
            and bool(path)
            and not path.startswith("/")
            and "\\" not in path
            and all(ord(char) >= 0x20 and ord(char) != 0x7F for char in path)
            and posixpath.normpath(path) not in {".", ".."}
            and not posixpath.normpath(path).startswith("../")
            and ".." not in path.split("/")
        )

    @staticmethod
    def _error(code: str, reason: str, field: str) -> dict[str, Any]:
        return {"severity": "error", "code": code, "safeReason": reason, "field": field}
