import json
import socket
import threading

import pytest
from coworker.haas.credentials import CredentialChannel


def exchange(sock, payload):
    sock.sendall(json.dumps(payload).encode() + b"\n")
    return json.loads(sock.makefile("rb").readline())


def test_channel_scopes_replay_and_revocation():
    resolved = []
    channel = CredentialChannel(lambda provider: resolved.append(provider) or "fixture-value")
    scope = {
        "harnessId": "chrn_test",
        "sessionId": "hsess_test",
        "model": "test",
        "baseUrl": "https://provider.example/v1",
    }
    ref = channel.grant("openai", scope)
    parent, child = socket.socketpair()
    channel.start(parent)
    try:
        payload = {"sequence": 1, "credentialRef": ref, **scope}
        assert exchange(child, {**payload, "sessionId": "hsess_other"}) == {
            "sequence": 1,
            "error": "credential_unavailable",
        }
        assert not resolved
        assert exchange(child, {**payload, "sequence": 2}) == {
            "sequence": 2,
            "value": "fixture-value",
        }
        assert resolved == ["openai"]
        assert "error" in exchange(child, {**payload, "sequence": 2})
        assert len(resolved) == 1
        channel.revoke(ref)
        assert "error" in exchange(child, {**payload, "sequence": 3})
        assert len(resolved) == 1
    finally:
        child.close()
        channel.close()


@pytest.mark.parametrize(
    "data", [b"[]\n", b"invalid\n", b"x" * 16385], ids=["array", "invalid", "oversize"]
)
def test_malformed_request_closes_channel(data):
    channel = CredentialChannel(lambda _: "fixture-value")
    parent, child = socket.socketpair()
    child.settimeout(1)
    channel.start(parent)
    try:
        child.sendall(data)
        assert child.recv(1) == b""
    finally:
        child.close()
        channel.close()


def test_revocation_during_resolve_does_not_release_key():
    started, resume = threading.Event(), threading.Event()

    def resolve(_):
        started.set()
        assert resume.wait(2)
        return "fixture-value"

    channel = CredentialChannel(resolve)
    scope = {
        "harnessId": "chrn_a",
        "sessionId": "hsess_a",
        "model": "m",
        "baseUrl": "https://provider.example",
    }
    ref = channel.grant("test", scope)
    assert channel.grant("test", scope) == ref
    parent, child = socket.socketpair()
    child.settimeout(2)
    channel.start(parent)
    try:
        child.sendall(json.dumps({"sequence": 1, "credentialRef": ref, **scope}).encode() + b"\n")
        assert started.wait(2)
        channel.revoke(ref)
        resume.set()
        assert "value" not in json.loads(child.makefile("rb").readline())
    finally:
        resume.set()
        child.close()
        channel.close()


@pytest.mark.parametrize(
    "value", [None, "", "x" * 8193, RuntimeError()], ids=["missing", "empty", "large", "error"]
)
def test_resolver_failure_never_releases_key(value):
    def resolve(_):
        if isinstance(value, Exception):
            raise value
        return value

    channel = CredentialChannel(resolve)
    scope = {
        "harnessId": "chrn_a",
        "sessionId": "hsess_a",
        "model": "m",
        "baseUrl": "https://provider.example",
    }
    ref = channel.grant("test", scope)
    parent, child = socket.socketpair()
    channel.start(parent)
    try:
        assert "value" not in exchange(child, {"sequence": 1, "credentialRef": ref, **scope})
    finally:
        child.close()
        channel.close()


def test_grant_requires_exact_safe_url_and_scope():
    channel = CredentialChannel(lambda _: "fixture-value")
    channel.close()
    channel._serve()
    scope = {
        "harnessId": "chrn_a",
        "sessionId": "hsess_a",
        "model": "m",
        "baseUrl": "https://provider.example:0",
    }
    with pytest.raises(ValueError):
        channel.grant("test", scope)
    scope["baseUrl"] = "https://provider.example"
    first = channel.grant("test", scope)
    assert channel.grant("another", scope) != first
    with pytest.raises(ValueError):
        channel.grant("openai", {"baseUrl": "https://user:password@provider.example"})
    with pytest.raises(ValueError):
        channel.grant(
            "openai",
            {
                "harnessId": "chrn_a",
                "sessionId": "hsess_a",
                "model": "m",
                "baseUrl": "http://169.254.169.254",
            },
        )
