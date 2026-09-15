"""Policy compilation and authorization (specs/policy-controller/README.md).

Compiles layered policy inputs into an :class:`EffectivePolicy` with
narrow-only enforcement, and authorizes network and workspace-path access
against the frozen policy.
"""

from __future__ import annotations

import ipaddress
import uuid
from pathlib import Path
from urllib.parse import ParseResult, urlparse

from haas.policy.models import (
    EffectivePolicy,
    ModelPolicy,
    NetworkPolicy,
    PolicyCompileInput,
    PolicyDecision,
    ToolsPolicy,
    WorkspacePolicy,
)

# Narrower == smaller rank. Higher ranks are wider (more permissive).
_MODE_RANK = {
    "read-only": 0,
    "workspace-write": 1,
    "danger-full-access": 2,
}
_APPROVAL_MODES = frozenset({"always", "on-request", "never"})


class PolicyError(Exception):
    """Base error for policy compilation/authorization failures."""


class PolicyWideningRejected(PolicyError):
    """A lower layer widened a higher layer's constraint without delegation."""


class PolicyInvalid(PolicyError):
    """A policy layer is malformed or carries an unknown security-affecting field."""


class PolicyController:
    """Owns policy precedence merging and runtime authorization decisions."""

    def compile(self, input: PolicyCompileInput) -> EffectivePolicy:
        workspace = WorkspacePolicy()
        network = NetworkPolicy()
        tools = ToolsPolicy()
        model = ModelPolicy()

        # A higher layer may explicitly grant lower layers the right to widen
        # its constraints (spec §7). Once granted, all subsequent layers may
        # widen; until then, any widening is rejected.
        allow_widening = False
        for layer in input.layers:
            if layer.workspace is not None:
                workspace = self._merge_workspace(workspace, layer.workspace, allow_widening)
            if layer.network is not None:
                network = self._merge_network(network, layer.network, allow_widening)
            if layer.tools is not None:
                tools = self._merge_tools(tools, layer.tools, allow_widening)
            if layer.model is not None:
                model = self._merge_model(model, layer.model, allow_widening)
            if layer.delegation:
                allow_widening = True

        if not workspace.writableRoots:
            workspace.writableRoots = [workspace.root]

        return EffectivePolicy(
            policyId=f"pol_{uuid.uuid4().hex[:16]}",
            version=1,
            scope=input.scope,
            workspace=workspace,
            network=network,
            tools=tools,
            model=model,
        )

    # --- layered merge (narrow-only) ----------------------------------------

    def _merge_workspace(
        self, current: WorkspacePolicy, nxt: WorkspacePolicy, allow_widening: bool
    ) -> WorkspacePolicy:
        if nxt.mode not in _MODE_RANK:
            raise PolicyInvalid(f"unknown workspace mode: {nxt.mode}")

        new_mode = current.mode
        if _MODE_RANK[nxt.mode] < _MODE_RANK[current.mode]:
            new_mode = nxt.mode
        elif _MODE_RANK[nxt.mode] > _MODE_RANK[current.mode]:
            if not allow_widening:
                raise PolicyWideningRejected(
                    f"workspace mode widened from {current.mode} to {nxt.mode}"
                )
            new_mode = nxt.mode

        new_roots = current.writableRoots
        if nxt.writableRoots:
            if not new_roots:
                new_roots = list(nxt.writableRoots)
            else:
                for root in nxt.writableRoots:
                    if root not in new_roots:
                        if not allow_widening:
                            raise PolicyWideningRejected(f"writable root widened: {root!r}")
                        new_roots.append(root)
                if not allow_widening and set(nxt.writableRoots) < set(current.writableRoots):
                    new_roots = list(nxt.writableRoots)

        new_root = current.root
        if nxt.root:
            if not _is_same_or_within(nxt.root, current.root) and not allow_widening:
                raise PolicyWideningRejected(
                    f"workspace root widened from {current.root!r} to {nxt.root!r}"
                )
            new_root = nxt.root

        return WorkspacePolicy(
            mode=new_mode,
            root=new_root,
            writableRoots=new_roots,
        )

    def _merge_network(
        self, current: NetworkPolicy, nxt: NetworkPolicy, allow_widening: bool
    ) -> NetworkPolicy:
        if nxt.defaultAction not in {"deny", "allow"}:
            raise PolicyInvalid(f"unknown network defaultAction: {nxt.defaultAction}")
        if nxt.defaultAction == "allow" and current.defaultAction == "deny" and not allow_widening:
            raise PolicyWideningRejected("network defaultAction widened from deny to allow")

        new_allow = current.allow
        if nxt.allow:
            if not new_allow:
                new_allow = list(nxt.allow)
            else:
                for entry in nxt.allow:
                    if entry not in new_allow:
                        if not allow_widening:
                            raise PolicyWideningRejected(f"network allow widened: {entry!r}")
                        new_allow.append(entry)
                if not allow_widening and set(nxt.allow) < set(current.allow):
                    new_allow = list(nxt.allow)

        return NetworkPolicy(defaultAction=nxt.defaultAction, allow=new_allow)

    def _merge_tools(
        self, current: ToolsPolicy, nxt: ToolsPolicy, allow_widening: bool
    ) -> ToolsPolicy:
        if nxt.approvalMode not in _APPROVAL_MODES:
            raise PolicyInvalid(f"unknown approvalMode: {nxt.approvalMode}")

        new_disabled = list(current.disabled)
        for tool in nxt.disabled:
            if tool not in new_disabled:
                new_disabled.append(tool)

        if current.approvalMode not in _APPROVAL_MODES:
            raise PolicyInvalid(f"unknown approvalMode: {current.approvalMode}")

        new_approval = current.approvalMode
        if nxt.approvalMode != current.approvalMode:
            # Approval modes are not linearly ordered. `never` closes the grant
            # channel (it is not unrestricted), while `always` adds mandatory
            # prompts. Only the transitions below are unambiguously narrowing.
            narrowing = nxt.approvalMode == "never" or (
                current.approvalMode == "on-request" and nxt.approvalMode == "always"
            )
            if not narrowing and not allow_widening:
                raise PolicyWideningRejected(
                    f"approvalMode widened from {current.approvalMode} to {nxt.approvalMode}"
                )
            new_approval = nxt.approvalMode

        return ToolsPolicy(disabled=new_disabled, approvalMode=new_approval)

    def _merge_model(
        self, current: ModelPolicy, nxt: ModelPolicy, allow_widening: bool
    ) -> ModelPolicy:
        new_models = current.allowedModels
        if nxt.allowedModels:
            if not new_models:
                new_models = list(nxt.allowedModels)
            else:
                for m in nxt.allowedModels:
                    if m not in new_models:
                        if not allow_widening:
                            raise PolicyWideningRejected(f"model allow widened: {m!r}")
                        new_models.append(m)
                if not allow_widening and set(nxt.allowedModels) < set(current.allowedModels):
                    new_models = list(nxt.allowedModels)
        return ModelPolicy(
            allowedModels=new_models,
            fallbackModel=nxt.fallbackModel or current.fallbackModel,
        )

    # --- authorization ------------------------------------------------------

    def authorize_network(self, policy: EffectivePolicy, url: str) -> PolicyDecision:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            return self._deny("url_scheme_not_allowed")
        host = parsed.hostname or ""
        if not host:
            return self._deny("url_host_missing")
        if _effective_port(parsed) is None:
            return self._deny("url_port_invalid")

        allowed = any(self._allowlist_matches(entry, parsed) for entry in policy.network.allow)
        # Loopback/private hosts are only allowed when explicitly allowlisted.
        if self._is_private_or_special(host) and not allowed:
            return self._deny("private_network_blocked")

        if policy.network.defaultAction == "deny" and not allowed:
            return self._deny("network_host_not_allowed")
        return PolicyDecision(allowed=True)

    def authorize_workspace_path(
        self,
        policy: EffectivePolicy,
        path: str,
        access: str,
    ) -> PolicyDecision:
        if access not in {"read", "write"}:
            return self._deny("invalid_access")
        canonical = _canonicalize(path)
        if access == "write":
            roots = policy.workspace.writableRoots or [policy.workspace.root]
        else:
            roots = [policy.workspace.root, *(policy.workspace.writableRoots or [])]
        if _is_within(canonical, roots):
            return PolicyDecision(allowed=True)
        return self._deny("workspace_path_outside_roots")

    # --- helpers ------------------------------------------------------------

    def _deny(self, reason: str) -> PolicyDecision:
        return PolicyDecision(
            allowed=False, code="haas_policy_denied", safeReason=reason, retryable=False
        )

    def _allowlist_matches(self, entry: str, parsed: ParseResult) -> bool:
        e = urlparse(entry)
        if e.hostname is None:
            host = entry
            explicit_port: int | None = None
            if ":" in entry and entry.rsplit(":", 1)[1].isdigit():
                host, port_text = entry.rsplit(":", 1)
                explicit_port = int(port_text)
            if parsed.hostname != host:
                return False
            if explicit_port is not None:
                return _effective_port(parsed) == explicit_port
            return _effective_port(parsed) == _default_port_for_scheme(parsed.scheme)
        if parsed.hostname != e.hostname:
            return False
        if e.scheme and parsed.scheme != e.scheme:
            return False
        entry_port = _effective_port(e)
        request_port = _effective_port(parsed)
        if entry_port is None or request_port is None:
            return False
        return entry_port == request_port

    def _is_private_or_special(self, host: str) -> bool:
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            ip = None
        if ip is not None:
            return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
        return (
            host == "localhost"
            or host.endswith(".localhost")
            or host == "metadata.google.internal"
            or host.endswith(".internal")
        )


def _canonicalize(path: str) -> str:
    return str(Path(path).expanduser().resolve())


def _is_same_or_within(path: str, root: str) -> bool:
    canonical_path = Path(path).expanduser().resolve()
    canonical_root = Path(root).expanduser().resolve()
    try:
        canonical_path.relative_to(canonical_root)
    except ValueError:
        return False
    return True


def _is_within(path: str, roots: list[str]) -> bool:
    for root in roots:
        r = _canonicalize(root).rstrip("/")
        if path == r or path.startswith(r + "/"):
            return True
    return False


def _effective_port(parsed: ParseResult) -> int | None:
    try:
        port = parsed.port
    except ValueError:
        return None
    if port is not None:
        return port
    return _default_port_for_scheme(parsed.scheme)


def _default_port_for_scheme(scheme: str) -> int | None:
    if scheme == "http":
        return 80
    if scheme == "https":
        return 443
    return None
