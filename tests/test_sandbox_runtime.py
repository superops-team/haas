"""Sandbox Runtime projection tests: narrow-only spec compile + egress projection."""

from __future__ import annotations

import pytest

from haas.harnesses.base import HarnessSandboxDecl
from haas.policy import (
    PolicyCompileInput,
    PolicyController,
    PolicyLayer,
    PolicyScope,
    WorkspacePolicy,
)
from haas.runtime import SandboxRuntime, SandboxWideningRejected


def _policy(writable_roots: list[str], root: str = "/workspace") -> object:
    return PolicyController().compile(
        PolicyCompileInput(
            scope=PolicyScope(tenantId="t1"),
            layers=[
                PolicyLayer(
                    "tenant",
                    workspace=WorkspacePolicy(
                        mode="workspace-write", root=root, writableRoots=writable_roots
                    ),
                )
            ],
        )
    )


def test_compile_sandbox_spec_narrow_projection() -> None:
    policy = _policy(["/workspace"])
    spec = SandboxRuntime().compile_sandbox_spec(
        policy,
        HarnessSandboxDecl(cwd="/workspace", writableRoots=["/workspace"]),
        session_id="hsess_1",
    )
    assert spec.sessionId == "hsess_1"
    assert spec.workspaceRoot == "/workspace"
    assert spec.writableRoots == ["/workspace"]


def test_compile_rejects_cwd_outside_root() -> None:
    policy = _policy(["/workspace"])
    with pytest.raises(SandboxWideningRejected):
        SandboxRuntime().compile_sandbox_spec(
            policy,
            HarnessSandboxDecl(cwd="/etc", writableRoots=["/workspace"]),
            session_id="hsess_1",
        )


def test_compile_rejects_widened_writable_root() -> None:
    policy = _policy(["/workspace"])
    with pytest.raises(SandboxWideningRejected):
        SandboxRuntime().compile_sandbox_spec(
            policy,
            HarnessSandboxDecl(cwd="/workspace", writableRoots=["/workspace", "/data"]),
            session_id="hsess_1",
        )


def test_compile_allows_subset_writable_root() -> None:
    policy = _policy(["/workspace", "/workspace/tmp"])
    spec = SandboxRuntime().compile_sandbox_spec(
        policy,
        HarnessSandboxDecl(cwd="/workspace", writableRoots=["/workspace/tmp"]),
        session_id="hsess_1",
    )
    assert spec.writableRoots == ["/workspace/tmp"]


def test_compile_canonicalizes_traversal() -> None:
    policy = _policy(["/workspace"])
    spec = SandboxRuntime().compile_sandbox_spec(
        policy,
        HarnessSandboxDecl(cwd="/workspace", writableRoots=["/workspace/sub/../"]),
        session_id="hsess_1",
    )
    assert spec.writableRoots == ["/workspace"]


def test_compile_blocks_traversal_escape() -> None:
    policy = _policy(["/workspace"])
    with pytest.raises(SandboxWideningRejected):
        SandboxRuntime().compile_sandbox_spec(
            policy,
            HarnessSandboxDecl(cwd="/workspace", writableRoots=["/workspace/../../data"]),
            session_id="hsess_1",
        )


def test_project_egress_carries_network_policy() -> None:
    from haas.policy import NetworkPolicy

    policy = PolicyController().compile(
        PolicyCompileInput(
            scope=PolicyScope(tenantId="t1"),
            layers=[
                PolicyLayer(
                    "tenant",
                    network=NetworkPolicy(defaultAction="deny", allow=["https://api.openai.com"]),
                )
            ],
        )
    )
    spec = SandboxRuntime().compile_sandbox_spec(
        policy,
        HarnessSandboxDecl(cwd="/workspace", writableRoots=["/workspace"]),
        session_id="hsess_1",
    )
    egress = SandboxRuntime().project_egress(spec)
    assert egress.defaultAction == "deny"
    assert egress.allow == ["https://api.openai.com"]
