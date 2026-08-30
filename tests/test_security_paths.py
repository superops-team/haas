"""Security-path coverage: policy narrowing, SSRF guard, secret surfaces.

AGENTS.md requires >=95% on policy / redaction / proxy-token paths. These
tests target the widening-rejection and URL-classification branches that
enforce those guarantees.
"""
from __future__ import annotations

import pytest

from haas.policy.controller import (
    PolicyController,
    PolicyInvalid,
    PolicyWideningRejected,
)
from haas.policy.models import (
    ModelPolicy,
    NetworkPolicy,
    PolicyCompileInput,
    PolicyLayer,
    PolicyScope,
    ToolsPolicy,
    WorkspacePolicy,
)
from haas.security.redact import (
    SecretSurfaceError,
    UrlPolicy,
    assert_no_secret_surface,
    validate_url,
)


_COUNTER = iter(range(1000))


def _resolve(*layers: PolicyLayer):
    return PolicyController().compile(
        PolicyCompileInput(scope=PolicyScope(), layers=list(layers))
    )


def _base_layer(**kw) -> PolicyLayer:
    return PolicyLayer(name=f"layer-{next(_COUNTER)}", **kw)


# --- workspace narrowing ----------------------------------------------------


def test_workspace_mode_narrowing_is_allowed() -> None:
    result = _resolve(
        _base_layer(workspace=WorkspacePolicy(mode="workspace-write",
                                              writableRoots=["/w"])),
        _base_layer(workspace=WorkspacePolicy(mode="read-only",
                                              writableRoots=["/w"])),
    )
    assert result.workspace.mode == "read-only"


def test_workspace_mode_widening_is_rejected() -> None:
    with pytest.raises(PolicyWideningRejected, match="workspace mode widened"):
        _resolve(
            _base_layer(workspace=WorkspacePolicy(mode="read-only",
                                                  writableRoots=["/w"])),
            _base_layer(workspace=WorkspacePolicy(mode="danger-full-access",
                                                  writableRoots=["/w"])),
        )


def test_workspace_widening_allowed_after_delegation() -> None:
    result = _resolve(
        _base_layer(
            workspace=WorkspacePolicy(mode="read-only", writableRoots=["/w"]),
            delegation=True,
        ),
        _base_layer(workspace=WorkspacePolicy(mode="workspace-write",
                                              writableRoots=["/w", "/extra"])),
    )
    assert result.workspace.mode == "workspace-write"
    assert "/extra" in result.workspace.writableRoots


def test_writable_root_widening_is_rejected() -> None:
    with pytest.raises(PolicyWideningRejected, match="writable root widened"):
        _resolve(
            _base_layer(workspace=WorkspacePolicy(mode="read-only",
                                                  writableRoots=["/w"])),
            _base_layer(workspace=WorkspacePolicy(mode="read-only",
                                                  writableRoots=["/w", "/etc"])),
        )


def test_writable_root_subset_narrows() -> None:
    result = _resolve(
        _base_layer(workspace=WorkspacePolicy(mode="read-only",
                                              writableRoots=["/w", "/x"])),
        _base_layer(workspace=WorkspacePolicy(mode="read-only",
                                              writableRoots=["/w"])),
    )
    assert result.workspace.writableRoots == ["/w"]


def test_unknown_workspace_mode_is_invalid() -> None:
    with pytest.raises(PolicyInvalid, match="unknown workspace mode"):
        _resolve(
            _base_layer(workspace=WorkspacePolicy(mode="read-only",
                                                  writableRoots=["/w"])),
            _base_layer(workspace=WorkspacePolicy(mode="bogus",
                                                  writableRoots=["/w"])),
        )


# --- network narrowing ------------------------------------------------------


def test_network_default_action_widening_is_rejected() -> None:
    with pytest.raises(PolicyWideningRejected, match="defaultAction widened"):
        _resolve(
            _base_layer(workspace=WorkspacePolicy(mode="read-only",
                                                  writableRoots=["/w"]),
                        network=NetworkPolicy(defaultAction="deny", allow=[])),
            _base_layer(network=NetworkPolicy(defaultAction="allow", allow=[])),
        )


def test_network_allow_widening_is_rejected() -> None:
    with pytest.raises(PolicyWideningRejected, match="network allow widened"):
        _resolve(
            _base_layer(workspace=WorkspacePolicy(mode="read-only",
                                                  writableRoots=["/w"]),
                        network=NetworkPolicy(defaultAction="deny", allow=["a.com"])),
            _base_layer(network=NetworkPolicy(defaultAction="deny",
                                              allow=["a.com", "evil.com"])),
        )


def test_network_allow_subset_narrows() -> None:
    result = _resolve(
        _base_layer(workspace=WorkspacePolicy(mode="read-only",
                                              writableRoots=["/w"]),
                    network=NetworkPolicy(defaultAction="deny",
                                          allow=["a.com", "b.com"])),
        _base_layer(network=NetworkPolicy(defaultAction="deny", allow=["a.com"])),
    )
    assert result.network.allow == ["a.com"]


def test_unknown_network_default_action_is_invalid() -> None:
    with pytest.raises(PolicyInvalid, match="unknown network defaultAction"):
        _resolve(
            _base_layer(workspace=WorkspacePolicy(mode="read-only",
                                                  writableRoots=["/w"]),
                        network=NetworkPolicy(defaultAction="maybe", allow=[])),
        )


# --- tools / model narrowing ------------------------------------------------


def test_tool_disable_accumulates_and_approval_widening_rejected() -> None:
    with pytest.raises(PolicyWideningRejected, match="approvalMode widened"):
        _resolve(
            _base_layer(workspace=WorkspacePolicy(mode="read-only",
                                                  writableRoots=["/w"]),
                        tools=ToolsPolicy(disabled=["shell"], approvalMode="always")),
            _base_layer(tools=ToolsPolicy(disabled=["net"], approvalMode="never")),
        )


def test_tool_approval_narrowing_and_disable_union() -> None:
    result = _resolve(
        _base_layer(workspace=WorkspacePolicy(mode="read-only",
                                              writableRoots=["/w"]),
                    tools=ToolsPolicy(disabled=["shell"], approvalMode="never")),
        _base_layer(tools=ToolsPolicy(disabled=["net", "shell"],
                                      approvalMode="always")),
    )
    assert result.tools.approvalMode == "always"
    assert set(result.tools.disabled) == {"shell", "net"}


def test_unknown_approval_mode_is_invalid() -> None:
    with pytest.raises(PolicyInvalid, match="unknown approvalMode"):
        _resolve(
            _base_layer(workspace=WorkspacePolicy(mode="read-only",
                                                  writableRoots=["/w"]),
                        tools=ToolsPolicy(disabled=[], approvalMode="sometimes")),
        )


def test_model_allow_widening_rejected_and_subset_narrows() -> None:
    with pytest.raises(PolicyWideningRejected, match="model allow widened"):
        _resolve(
            _base_layer(workspace=WorkspacePolicy(mode="read-only",
                                                  writableRoots=["/w"]),
                        model=ModelPolicy(allowedModels=["m1"], fallbackModel=None)),
            _base_layer(model=ModelPolicy(allowedModels=["m1", "m2"],
                                          fallbackModel=None)),
        )

    result = _resolve(
        _base_layer(workspace=WorkspacePolicy(mode="read-only",
                                              writableRoots=["/w"]),
                    model=ModelPolicy(allowedModels=["m1", "m2"], fallbackModel="f")),
        _base_layer(model=ModelPolicy(allowedModels=["m1"], fallbackModel=None)),
    )
    assert result.model.allowedModels == ["m1"]
    assert result.model.fallbackModel == "f"


# --- SSRF / URL classification ----------------------------------------------


@pytest.fixture
def url_policy() -> UrlPolicy:
    return UrlPolicy(
        allowed_schemes=frozenset({"https"}),
        allowed_hosts=frozenset({"api.example.com"}),
        allow_loopback_http=False,
        allow_private_network=False,
    )


def test_url_missing_host_is_rejected(url_policy: UrlPolicy) -> None:
    assert validate_url("file:///etc/passwd", url_policy).allowed is False


def test_allowlisted_host_requires_allowed_scheme(url_policy: UrlPolicy) -> None:
    assert validate_url("https://api.example.com/v1", url_policy).allowed is True
    denied = validate_url("http://api.example.com/v1", url_policy)
    assert denied.allowed is False
    assert denied.safeReason == "scheme_not_allowed"


def test_unlisted_hostname_is_rejected(url_policy: UrlPolicy) -> None:
    denied = validate_url("https://evil.example.org/x", url_policy)
    assert denied.allowed is False
    assert denied.safeReason == "host_not_allowed"


def test_unlisted_hostname_with_bad_scheme_reports_scheme(
    url_policy: UrlPolicy,
) -> None:
    denied = validate_url("ftp://evil.example.org/x", url_policy)
    assert denied.safeReason == "scheme_not_allowed"


def test_loopback_http_requires_explicit_opt_in(url_policy: UrlPolicy) -> None:
    assert validate_url("http://127.0.0.1:18080/v1", url_policy).safeReason == (
        "loopback_not_allowed"
    )
    opened = UrlPolicy(
        allowed_schemes=frozenset({"https"}),
        allowed_hosts=frozenset(),
        allow_loopback_http=True,
        allow_private_network=False,
    )
    assert validate_url("http://127.0.0.1:18080/v1", opened).allowed is True


def test_loopback_https_follows_scheme_allowlist(url_policy: UrlPolicy) -> None:
    assert validate_url("https://127.0.0.1/v1", url_policy).allowed is True
    assert validate_url("ftp://127.0.0.1/v1", url_policy).safeReason == "scheme_not_allowed"


@pytest.mark.parametrize(
    "url",
    [
        "https://10.0.0.5/meta",       # private
        "https://169.254.169.254/",    # link-local (cloud metadata)
        "https://224.0.0.1/",          # multicast
        "https://240.0.0.1/",          # reserved
    ],
)
def test_private_and_special_networks_blocked(url: str, url_policy: UrlPolicy) -> None:
    denied = validate_url(url, url_policy)
    assert denied.allowed is False
    assert denied.safeReason == "private_network_not_allowed"


def test_private_network_can_be_explicitly_allowed() -> None:
    policy = UrlPolicy(
        allowed_schemes=frozenset({"https"}),
        allowed_hosts=frozenset(),
        allow_loopback_http=False,
        allow_private_network=True,
    )
    assert validate_url("https://10.0.0.5/meta", policy).allowed is True


def test_public_ip_requires_allowed_scheme(url_policy: UrlPolicy) -> None:
    assert validate_url("https://8.8.8.8/", url_policy).allowed is True
    assert validate_url("ftp://8.8.8.8/", url_policy).safeReason == "scheme_not_allowed"


# --- secret surface guard ---------------------------------------------------


def test_assert_no_secrets_scans_nested_structures() -> None:
    assert_no_secret_surface({"ok": ["fine", {"nested": "value"}]})

    with pytest.raises(SecretSurfaceError, match="secret field"):
        assert_no_secret_surface({"outer": [{"authorization": "Bearer x"}]})


def test_assert_no_secrets_scans_tuples() -> None:
    with pytest.raises(SecretSurfaceError):
        assert_no_secret_surface(("safe", {"api_key": "sk-live-123"}))
