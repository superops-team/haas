"""Harness Registry: configured harness catalog and appName resolution."""
from __future__ import annotations

from dataclasses import dataclass

from haas.identity import Principal
from haas.stores import HarnessRecord, MemoryStore


class AppNotFoundError(Exception):
    """appName resolution failed (maps to 404 app_not_found)."""


@dataclass
class HarnessRegistry:
    store: MemoryStore

    def save(self, harness: HarnessRecord) -> HarnessRecord:
        return self.store.save_harness(harness)

    def get(self, harness_id: str) -> HarnessRecord | None:
        return self.store.get_harness(harness_id)

    def list_active(self, principal: Principal) -> list[HarnessRecord]:
        # S2 keeps a single-scope catalog; tenant/workspace scoping is added
        # with multi-tenant identity (specs/harness-registry §8).
        return [h for h in self.store.list_harnesses() if h.status == "active"]

    def list_apps(self, principal: Principal) -> list[str]:
        return [h.id for h in self.list_active(principal)]

    def resolve_app(self, principal: Principal, app_name: str) -> HarnessRecord:
        """Resolve by id first, then by name; multiple/zero matches -> 404."""
        by_id = self.store.get_harness(app_name)
        if by_id is not None and by_id.status == "active":
            return by_id

        matches = [
            h
            for h in self.store.list_harnesses()
            if h.name == app_name and h.status == "active"
        ]
        if len(matches) == 1:
            return matches[0]
        raise AppNotFoundError(app_name)

    def resolve_default_app(self, principal: Principal) -> HarnessRecord:
        apps = self.list_active(principal)
        if not apps:
            raise AppNotFoundError("<default>")
        return apps[0]


def seed_codex(registry: HarnessRegistry) -> HarnessRecord:
    """Seed the P0 Codex configured harness used by S2 protocol tests."""
    return registry.save(
        HarnessRecord(
            id="chrn_codex_default",
            name="codex-default",
            base="codex",
            status="active",
            defaultModel="gpt-5.6-terra",
        )
    )
