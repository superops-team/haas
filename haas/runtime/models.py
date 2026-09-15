"""Sandbox Runtime data models (specs/sandbox-runtime/README.md §6)."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SandboxNetwork:
    defaultAction: str = "allow"
    allow: list[str] = field(default_factory=list)


@dataclass
class SandboxResources:
    cpu: int = 2
    memoryMb: int = 4096
    timeoutSeconds: int = 1800


@dataclass
class CredentialVault:
    providerKeyRef: str = ""


@dataclass
class SandboxSpec:
    sessionId: str = ""
    workspaceRoot: str = "/workspace"
    writableRoots: list[str] = field(default_factory=list)
    readOnlyRoots: list[str] = field(default_factory=list)
    network: SandboxNetwork = field(default_factory=SandboxNetwork)
    resources: SandboxResources = field(default_factory=SandboxResources)
    credentialVault: CredentialVault = field(default_factory=CredentialVault)


@dataclass
class EgressPolicy:
    defaultAction: str = "deny"
    allow: list[str] = field(default_factory=list)


@dataclass
class SandboxHandle:
    sandboxId: str
    sessionId: str
    status: str = "running"
    generation: int = 1
    createdAtMs: int = 0


@dataclass
class SandboxInspection:
    sandboxId: str
    sessionId: str = ""
    status: str = "running"
    generation: int = 1
    createdAtMs: int = 0


@dataclass
class ExecResult:
    sandboxId: str
    exitCode: int = 0
    stdout: str = ""
    stderr: str = ""
