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


def test_preview_redacts_inline_generic_credentials() -> None:
    preview, omitted = bounded_redacted_preview(
        "password=hunter2\napi_key=abcdefghijklmnop\nstatus=ok"
    )
    assert omitted == 0
    assert "hunter2" not in preview
    assert "abcdefghijklmnop" not in preview
    assert "password=[REDACTED]" in preview  # haas-secret-ignore - expected redaction
    assert "api_key=[REDACTED]" in preview  # haas-secret-ignore - expected redaction


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


def test_assert_no_secret_surface() -> None:
    assert_no_secret_surface({"ok": "value"})
    with pytest.raises(SecretSurfaceError):
        assert_no_secret_surface({"Authorization": "Bearer abc"})
    with pytest.raises(SecretSurfaceError):
        assert_no_secret_surface({"text": "AKIA1234567890ABCDEF"})  # haas-secret-ignore
