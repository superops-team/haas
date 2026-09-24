"""Security Boundary primitive tests (specs/security-boundary/README.md)."""

import pytest

from haas.security import (
    ArtifactPathTraversalError,
    RedactionContext,
    SecretSurfaceError,
    UrlPolicy,
    assert_no_secret_surface,
    redact,
    validate_artifact_path,
    validate_url,
)
from haas.security.redact import bounded_redacted_preview
from haas.runtime.delegation import DelegatedContainerUnavailable


def test_redact_credential_field_and_token() -> None:
    value = {
        "api_key": "sk-proj-abcdefghijklmnopqrstuvwxyz123456",  # haas-secret-ignore
        "text": "token sk-ant-abcdefghijklmnopqrstuvwxyz123456 here",  # haas-secret-ignore
        "keep": "plain value",
    }
    out = redact(value)
    assert out["api_key"] == "[REDACTED]"
    assert "sk-ant-" not in out["text"]
    assert "[REDACTED]" in out["text"]
    assert out["keep"] == "plain value"


def test_redact_haas_mp_runtime_token() -> None:
    """haas_mp_ loopback bearer tokens are HaaS-internal credentials (S4-001).

    They must be redacted the same way as provider keys, wherever they surface:
    plain strings, dict values, and nested structures.
    """
    token = "haas_mp_aGVsbG8uMS.1.abcdefghijklmnopqrstuvwx"  # haas-secret-ignore - synthetic
    assert redact({"token": token}) == {"token": "[REDACTED]"}
    # bearer header carrying the token is scrubbed (token itself never survives)
    bearer_out = redact(f"Authorization: Bearer {token}")
    assert token not in bearer_out
    assert "[REDACTED]" in bearer_out
    # nested structures
    nested = redact({"outer": [{"inner": token}, "tail"]})
    assert nested == {"outer": [{"inner": "[REDACTED]"}, "tail"]}
    # token embedded in surrounding text is also scrubbed
    out = redact(f"issued {token} to session")
    assert token not in out
    assert "[REDACTED]" in out


def test_preview_redacts_inline_generic_credentials() -> None:
    preview, omitted = bounded_redacted_preview(
        "password=hunter2\napi_key=abcdefghijklmnop\nstatus=ok"
    )
    assert omitted == 0
    assert "hunter2" not in preview
    assert "abcdefghijklmnop" not in preview
    assert "password=[REDACTED]" in preview  # haas-secret-ignore - expected redaction
    assert "api_key=[REDACTED]" in preview  # haas-secret-ignore - expected redaction


def test_redact_does_not_falsely_redact_normal_url_paths() -> None:
    """P2-15: ordinary URL paths must not be mistaken for host filesystem paths.

    ``https://host/v1/responses`` is application surface, not a bind-mount; the
    absolute-path heuristic must not rewrite it. Host paths like
    ``/workspace/tmp/out.json`` must still be redacted (regression).
    """
    url = "https://api.example.com/v1/responses"
    assert redact(url) == url
    # scheme-relative double slash must not be redacted either
    assert redact("see https://cdn.example.com/assets/app.js now") == (
        "see https://cdn.example.com/assets/app.js now"
    )
    # host filesystem paths remain redacted
    assert "[REDACTED_PATH]" in redact("wrote /workspace/tmp/out.json")
    assert redact("cat specs/event-log-sse/README.md") == (
        "cat specs/event-log-sse/README.md"
    )


def test_redact_presigned_url_and_absolute_path() -> None:
    url = "https://example.com/x?X-Amz-Signature=deadbeef"  # haas-secret-ignore
    assert "[REDACTED_URL]" in redact(url)

    path_text = "wrote /workspace/tmp/out.json"
    assert "[REDACTED_PATH]" in redact(path_text)
    assert redact("cat specs/event-log-sse/README.md") == (
        "cat specs/event-log-sse/README.md"
    )

    # host-path redaction can be disabled via context
    kept = redact(path_text, RedactionContext(redact_host_path=False))
    assert "/workspace/tmp/out.json" in kept


def test_validate_url_scheme_host_and_private_network() -> None:
    assert validate_url("https://api.example.com").allowed is False  # not allowlisted

    policy = UrlPolicy(allowed_hosts=frozenset({"api.example.com"}))
    assert validate_url("https://api.example.com/v1", policy).allowed is True

    default = UrlPolicy()
    assert validate_url("http://127.0.0.1:8080/x", default).allowed is True  # loopback http
    assert validate_url("http://10.0.0.1/x", default).safeReason == "private_network_not_allowed"
    assert validate_url("ftp://example.com/x", default).safeReason == "scheme_not_allowed"


def test_validate_artifact_path_blocks_traversal() -> None:
    root = "/workspace/session"
    assert validate_artifact_path(root, "out/file.txt") == __import__("pathlib").Path(
        "/workspace/session/out/file.txt"
    )
    with pytest.raises(ArtifactPathTraversalError):
        validate_artifact_path(root, "../etc/passwd")


async def test_docker_subprocess_stderr_does_not_leak_host_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    """Raw docker stderr may echo host bind-mount paths (S3-007).

    The subprocess runner must pass stderr through the redaction boundary before
    it becomes an exception message; host filesystem paths must not escape.
    """
    import haas.runtime.delegation as delegation

    class _FakeProc:
        returncode = 1

        async def communicate(self) -> tuple[bytes, bytes]:
            return b"", b"docker: error mounting source /etc/secret/path/key"

    async def _fake_exec(*_args: object, **_kwargs: object) -> object:
        return _FakeProc()

    monkeypatch.setattr(delegation.asyncio, "create_subprocess_exec", _fake_exec)
    with pytest.raises(DelegatedContainerUnavailable) as excinfo:
        await delegation._subprocess_runner(["docker", "run"])
    message = str(excinfo.value)
    assert "/etc/secret/path/key" not in message
    assert "[REDACTED_PATH]" in message


def test_assert_no_secret_surface() -> None:
    assert_no_secret_surface({"ok": "value"})
    with pytest.raises(SecretSurfaceError):
        assert_no_secret_surface({"Authorization": "Bearer abc"})
    with pytest.raises(SecretSurfaceError):
        assert_no_secret_surface({"text": "AKIA1234567890ABCDEF"})  # haas-secret-ignore
