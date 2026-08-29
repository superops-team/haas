"""Sandbox Runtime projection (specs/sandbox-runtime/README.md §5).

Compiles a frozen :class:`EffectivePolicy` plus a harness
:class:`HarnessSandboxDecl` into an OpenSandbox-bound :class:`SandboxSpec`
with narrow-only enforcement, and projects network policy to egress.
"""
from __future__ import annotations

from pathlib import Path

from haas.harnesses.base import HarnessSandboxDecl
from haas.policy.models import EffectivePolicy
from haas.runtime.models import (
    CredentialVault,
    EgressPolicy,
    SandboxNetwork,
    SandboxResources,
    SandboxSpec,
)


class SandboxWideningRejected(ValueError):
    """A harness sandbox declaration widened the effective policy."""


class SandboxRuntime:
    """Compiles policy + harness declaration into sandbox/egress configuration."""

    def compile_sandbox_spec(
        self,
        policy: EffectivePolicy,
        decl: HarnessSandboxDecl,
        *,
        session_id: str,
    ) -> SandboxSpec:
        root = _canonicalize(policy.workspace.root)
        cwd = _canonicalize(decl.cwd)
        if not _is_within(cwd, [root]):
            raise SandboxWideningRejected(f"decl cwd {decl.cwd!r} outside workspace root")

        policy_roots = [_canonicalize(r) for r in (policy.workspace.writableRoots or [root])]
        writable: list[str] = []
        for raw in decl.writableRoots:
            canonical = _canonicalize(raw)
            if not _is_within(canonical, policy_roots):
                raise SandboxWideningRejected(f"decl writable root {raw!r} widens policy")
            writable.append(canonical)

        return SandboxSpec(
            sessionId=session_id,
            workspaceRoot=root,
            writableRoots=writable,
            readOnlyRoots=[],
            network=SandboxNetwork(
                defaultAction=policy.network.defaultAction,
                allow=list(policy.network.allow),
            ),
            resources=SandboxResources(),
            credentialVault=CredentialVault(),
        )

    def project_egress(self, spec: SandboxSpec) -> EgressPolicy:
        return EgressPolicy(
            defaultAction=spec.network.defaultAction,
            allow=list(spec.network.allow),
        )


def _canonicalize(path: str) -> str:
    return str(Path(path).expanduser().resolve())


def _is_within(path: str, roots: list[str]) -> bool:
    for root in roots:
        r = root.rstrip("/")
        if path == r or path.startswith(r + "/"):
            return True
    return False
