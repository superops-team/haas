"""Model Proxy data models (specs/model-proxy/README.md §6)."""

from __future__ import annotations

from dataclasses import dataclass, field


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
    invocationId: str = ""
    harnessId: str = ""
    allowedModels: list[str] = field(default_factory=list)
    expiresAtMs: int = 0


@dataclass
class Usage:
    inputTokens: int = 0
    outputTokens: int = 0
    totalTokens: int = 0
    cacheReadTokens: int = 0
    cacheWriteTokens: int = 0
