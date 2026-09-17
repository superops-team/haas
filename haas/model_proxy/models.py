"""Model Proxy data models (specs/model-proxy/README.md §6)."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ModelRoute:
    provider: str
    baseUrl: str
    model: str
    providerId: str = ""
    name: str = ""
    wireApi: str = "responses"
    apiType: str = "responses"
    credentialRef: str = ""
    credentialFingerprint: str = ""
    allowlistRuleId: str = ""
    timeoutMs: int = 300000
    streamIdleTimeoutMs: int = 300000


@dataclass
class RuntimeTokenScope:
    audience: str = "model_proxy"
    sessionId: str = ""
    # Backward-compatible read field. New model-proxy tokens are session-scoped
    # and leave invocationId empty.
    invocationId: str = ""
    harnessId: str = ""
    providerScopeKey: str = ""
    generation: int = 1
    issuedAtMs: int = 0
    allowedModels: list[str] = field(default_factory=list)
    expiresAtMs: int = 0


@dataclass
class Usage:
    inputTokens: int = 0
    outputTokens: int = 0
    totalTokens: int = 0
    cacheReadTokens: int = 0
    cacheWriteTokens: int = 0


def provider_scope_key(route: Mapping[str, Any]) -> str:
    scope = {
        "providerId": str(route.get("providerId") or ""),
        "name": str(route.get("name") or ""),
        "baseUrl": str(route.get("baseUrl") or ""),
        "wireApi": str(route.get("wireApi") or ""),
        "apiType": str(route.get("apiType") or ""),
        "credentialRef": str(route.get("credentialRef") or ""),
    }
    encoded = json.dumps(scope, separators=(",", ":"), sort_keys=True)
    return "sha256:" + hashlib.sha256(encoded.encode()).hexdigest()
