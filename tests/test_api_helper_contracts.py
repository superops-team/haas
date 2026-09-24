"""Behavioral contracts for API helper boundaries and cached response parity."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from haas.api import (
    HaasError,
    MountManifestInvalid,
    _accepted_headers,
    _adk_event_frame,
    _deep_merge,
    _delegated_session_matches_body,
    _idempotency_headers,
    _render_cached,
    _sse_backpressure_frame,
    _sse_heartbeat_frame,
    _timeout_seconds_from_haas,
    _validate_delegation_policy,
    _validate_mount_entry,
    _validate_mount_manifest,
    _validate_profile_ref,
)
from haas.stores import DelegatedSessionRecord


def _policy(**overrides: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "idleTtlSeconds": 60,
        "maxContainerLifetimeSeconds": 3600,
        "rwWorkspaceConcurrency": "single_writer",
        "queuePolicy": "fifo",
        "restorePolicy": "fail_closed",
        "mountPolicy": "manager_approved",
        "network": {"defaultAction": "deny", "allow": []},
        "tools": {"disabled": [], "approvalMode": "on-request"},
    }
    value.update(overrides)
    return value


def _mount(**overrides: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "hostPathCanonical": "/tmp/haas-helper-contract",
        "containerPath": "/workspace",
        "access": "rw",
    }
    value.update(overrides)
    return value


def _delegated() -> DelegatedSessionRecord:
    return DelegatedSessionRecord(
        id="dgsess_contract",
        managerSessionId="mgr_contract",
        haasSessionId="hsess_contract",
        haasUserId="u_contract",
        harnessId="chrn_codex_default",
        harnessBase="codex",
        image={"reference": "r", "digest": "sha256:" + "a" * 64},
        profileRef={"profileId": "p", "profileVersion": 1, "profileFingerprint": "f"},
        provider={"providerId": "x", "model": "m", "credentialRef": "secret://x"},
        workspaceMode="bind_mount",
        mountManifest={"version": 1, "primaryWorkspace": _mount(), "extraMounts": []},
        delegationPolicySnapshot=_policy(),
    )


def test_sse_frame_helpers_preserve_legacy_and_structured_compatibility() -> None:
    event = {"id": "evt_1", "content": {"role": "model", "parts": []}}
    legacy = _adk_event_frame(event, structured=False).decode()
    assert legacy.startswith("data: ")
    assert "id:" not in legacy and "retry:" not in legacy

    structured = _adk_event_frame(event, structured=True).decode()
    assert "id: evt_1" in structured
    assert "retry: 15000" in structured
    assert "data: " in structured

    assert _sse_heartbeat_frame(structured=False) == b": keep-alive\n\n"
    assert _sse_heartbeat_frame(structured=True).startswith(b": keep-alive")
    backpressure = _sse_backpressure_frame(None).decode()
    assert "event: error" in backpressure
    assert "haas_sse_backpressure" in backpressure


def test_deep_merge_recurses_but_replaces_scalar_values() -> None:
    base = {"nested": {"keep": 1, "replace": 2}, "scalar": 1}
    merged = _deep_merge(base, {"nested": {"replace": 3}, "scalar": {"now": "dict"}})
    assert merged == {"nested": {"keep": 1, "replace": 3}, "scalar": {"now": "dict"}}
    assert base["nested"]["replace"] == 2


def test_timeout_helper_covers_absent_valid_and_capped_values() -> None:
    assert _timeout_seconds_from_haas({}) is None
    assert _timeout_seconds_from_haas({"timeoutSeconds": 2.5}) == 2.5
    assert _timeout_seconds_from_haas({"timeoutSeconds": 100_000}) == 86_400
    with pytest.raises(TypeError):
        _timeout_seconds_from_haas({"timeoutSeconds": object()})


@pytest.mark.parametrize(
    "policy",
    [
        _policy(idleTtlSeconds=0),
        _policy(maxContainerLifetimeSeconds=0),
        _policy(rwWorkspaceConcurrency="parallel"),
        _policy(queuePolicy="lifo"),
        _policy(restorePolicy="best_effort"),
        _policy(network="not-a-dict"),
        _policy(network={"defaultAction": "bogus", "allow": []}),
        _policy(network={"defaultAction": "deny", "allow": "example.com"}),
        _policy(network={"defaultAction": "deny", "allow": [1]}),
        _policy(tools="not-a-dict"),
        _policy(tools={"approvalMode": "bogus", "disabled": []}),
        _policy(tools={"approvalMode": "never", "disabled": "shell"}),
        _policy(tools={"approvalMode": "never", "disabled": [1]}),
    ],
)
def test_delegation_policy_validator_fails_closed(policy: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        _validate_delegation_policy(policy)


def test_delegation_policy_defaults_optional_network_and_tools() -> None:
    policy = _policy()
    policy.pop("network")
    policy.pop("tools")
    _validate_delegation_policy(policy)


@pytest.mark.parametrize(
    ("entry", "primary"),
    [
        (None, True),
        ({"hostPathCanonical": "", "containerPath": "/workspace", "access": "rw"}, True),
        (_mount(hostPathCanonical="relative"), True),
        (_mount(hostPathCanonical="/"), True),
        (_mount(hostPathCanonical="/var/run/docker.sock"), True),
        (_mount(hostPathCanonical="/run/docker.sock"), True),
        (_mount(hostPathCanonical="/tmp/.ssh/key"), True),
        (_mount(access="execute"), True),
        (_mount(containerPath="/wrong"), True),
        (_mount(access="ro"), True),
        (_mount(containerPath="/extra", access="rw"), False),
    ],
)
def test_mount_entry_validator_rejects_unsafe_contract(entry: Any, primary: bool) -> None:
    with pytest.raises(MountManifestInvalid):
        _validate_mount_entry(entry, primary=primary)


def test_mount_entry_validator_accepts_safe_primary_and_readonly_extra() -> None:
    _validate_mount_entry(_mount(), primary=True)
    _validate_mount_entry(_mount(containerPath="/extra", access="ro"), primary=False)


@pytest.mark.parametrize(
    "manifest",
    [
        None,
        {},
        {"version": 0, "primaryWorkspace": _mount()},
        {"version": 1, "primaryWorkspace": _mount(), "extraMounts": [None]},
    ],
)
def test_mount_manifest_validator_rejects_invalid_shape(manifest: Any) -> None:
    with pytest.raises(MountManifestInvalid):
        _validate_mount_manifest(manifest)


@pytest.mark.parametrize(
    "profile_ref",
    [
        None,
        {},
        {"profileId": "", "profileVersion": 1, "profileFingerprint": "f"},
        {"profileId": "p", "profileVersion": True, "profileFingerprint": "f"},
        {"profileId": "p", "profileVersion": 0, "profileFingerprint": "f"},
        {"profileId": "p", "profileVersion": 1, "profileFingerprint": ""},
    ],
)
def test_profile_ref_validator_rejects_incomplete_identity(profile_ref: Any) -> None:
    with pytest.raises(ValueError):
        _validate_profile_ref(profile_ref)


def test_delegated_session_body_match_checks_every_contract_domain() -> None:
    record = _delegated()
    body = {
        "haasSessionId": record.haasSessionId,
        "haasUserId": record.haasUserId,
        "harnessId": record.harnessId,
        "harnessBase": record.harnessBase,
        "image": record.image,
        "profileRef": record.profileRef,
        "provider": record.provider,
        "workspaceMode": record.workspaceMode,
        "mountManifest": record.mountManifest,
        "delegationPolicySnapshot": record.delegationPolicySnapshot,
    }
    assert _delegated_session_matches_body(record, body)
    for field in (
        "haasSessionId",
        "haasUserId",
        "harnessId",
        "harnessBase",
        "image",
        "profileRef",
        "provider",
        "workspaceMode",
        "mountManifest",
        "delegationPolicySnapshot",
    ):
        changed = dict(body)
        changed[field] = "different" if field not in {"image", "profileRef", "provider", "mountManifest", "delegationPolicySnapshot"} else {}
        assert not _delegated_session_matches_body(record, changed), field


def test_cached_response_rendering_preserves_status_headers_and_stream_shape() -> None:
    assert _idempotency_headers(None) is None
    assert _idempotency_headers(123) == {"Idempotency-Expires-At": "123"}
    assert _accepted_headers(invocation_id=None, session_id=None, expires_at_ms=None) == {}
    headers = _accepted_headers(invocation_id="inv_1", session_id="hsess_1", expires_at_ms=123)
    assert headers == {
        "Idempotency-Expires-At": "123",
        "X-HaaS-Invocation-ID": "inv_1",
        "X-HaaS-Session-ID": "hsess_1",
    }

    error = _render_cached({"status_code": 503, "body": "invalid"}, streaming=False)
    assert error.status_code == 503
    assert error.body == b'{"detail":"request_failed"}'

    success = _render_cached({"events": [{"id": "evt_1"}]}, streaming=False)
    assert success.status_code == 200
    assert b"evt_1" in success.body

    stream = _render_cached({"events": [{"id": "evt_1"}]}, streaming=True)
    assert stream.media_type == "text/event-stream"

    empty = _render_cached(None, streaming=False)
    assert empty.status_code == 200
    assert empty.body == b"[]"
