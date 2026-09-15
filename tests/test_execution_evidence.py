from __future__ import annotations

from haas.execution_evidence import MAX_EVIDENCE_BYTES, ExecutionEvidenceStore


def test_evidence_masks_credentials_but_preserves_live_authorization_url() -> None:
    now = 1_789_263_000_000
    signed = "https://auth.example.com/oauth/authorize?code=abc&state=xyz&exp=1789263600"
    bearer = "Authorization: Bearer abcdefghijklmnopqrstuvwxyz"  # haas-secret-ignore
    store = ExecutionEvidenceStore(clock_ms=lambda: now)
    record = store.put(
        principal_id="p_1", app_name="chrn_1", user_id="u_1",
        session_id="hsess_1", invocation_id="inv_1", tool_call_id="call_1",
        command=f"API_KEY=top-secret tool --login '{signed}'",
        working_directory="/workspace",
        output=f"Authorize at {signed}\n{bearer}",
    )

    body = record.public()
    assert "top-secret" not in str(body)
    assert "abcdefghijklmnopqrstuvwxyz" not in str(body)
    assert signed in body["command"]
    assert signed in body["output"]
    assert body["links"] == [
        {"url": signed, "kind": "authorization", "expiresAtMs": 1789263600000},
        {"url": signed, "kind": "authorization", "expiresAtMs": 1789263600000},
    ]
    assert body["expiresAtMs"] == 1789263600000


def test_ordinary_url_stays_clickable_but_secret_query_value_is_masked() -> None:
    store = ExecutionEvidenceStore(clock_ms=lambda: 1_789_263_000_000)
    record = store.put(
        principal_id="p_1", app_name="chrn_1", user_id="u_1",
        session_id="hsess_1", invocation_id="inv_1", tool_call_id="call_1",
        command="curl https://docs.example.com/guide?q=haas&api_key=secret",
        working_directory="/workspace",
    )
    assert "https://docs.example.com/guide?q=haas&api_key=%5BREDACTED%5D" in record.command


def test_short_lived_authorization_url_without_expiry_query_is_byte_preserved() -> None:
    now = 1_789_263_000_000
    signed = (
        "https://login.example.com/oauth/authorize?client_id=abc&redirect_uri="
        "https%3A%2F%2Flocalhost%2Fcallback&state=signed-state&sig=abc123"
    )
    store = ExecutionEvidenceStore(clock_ms=lambda: now)
    record = store.put(
        principal_id="p_1", app_name="chrn_1", user_id="u_1",
        session_id="hsess_1", invocation_id="inv_1", tool_call_id="call_1",
        command=f"open '{signed}'", working_directory="/workspace",
    )

    assert signed in record.command
    assert record.links == ({
        "url": signed, "kind": "authorization", "expiresAtMs": now + 15 * 60 * 1000,
    },)


def test_working_directory_is_value_redacted_defensively() -> None:
    store = ExecutionEvidenceStore(clock_ms=lambda: 1_789_263_000_000)
    record = store.put(
        principal_id="p_1", app_name="chrn_1", user_id="u_1",
        session_id="hsess_1", invocation_id="inv_1", tool_call_id="call_1",
        command="pwd", working_directory="/tmp/API_KEY=top-secret",
    )
    assert "top-secret" not in record.workingDirectory
    assert record.workingDirectory.startswith("/tmp/")


def test_authorization_header_does_not_swallow_a_following_url() -> None:
    now = 1_789_263_000_000
    signed = "https://auth.example.com/oauth/authorize?state=s&client_id=c"
    value = f"curl -H 'Authorization: Bearer top-secret' '{signed}'"
    safe = ExecutionEvidenceStore(clock_ms=lambda: now).put(
        principal_id="p_1", app_name="chrn_1", user_id="u_1",
        session_id="hsess_1", invocation_id="inv_1", tool_call_id="call_1",
        command=value, working_directory="/workspace/project",
    )
    assert "top-secret" not in safe.command
    assert signed in safe.command


def test_json_cookie_value_is_masked_without_losing_following_evidence() -> None:
    safe = ExecutionEvidenceStore(clock_ms=lambda: 1_789_263_000_000).put(
        principal_id="p_1", app_name="chrn_1", user_id="u_1",
        session_id="hsess_1", invocation_id="inv_1", tool_call_id="call_1",
        command="tool --headers '{\"Cookie\": \"session=top-secret\"}' /workspace/file",
        working_directory="/workspace/project",
    )
    assert "top-secret" not in safe.command
    assert "/workspace/file" in safe.command


def test_presigned_storage_url_is_not_misclassified_as_authorization() -> None:
    signed = "https://storage.example.com/object?X-Amz-Signature=abc&X-Amz-Credential=user"
    record = ExecutionEvidenceStore(clock_ms=lambda: 1_789_263_000_000).put(
        principal_id="p_1", app_name="chrn_1", user_id="u_1",
        session_id="hsess_1", invocation_id="inv_1", tool_call_id="call_1",
        command=f"curl '{signed}'", working_directory="/workspace",
    )
    assert record.links[0]["kind"] == "ordinary"
    assert "abc" not in str(record.links[0]["url"])
    assert "user" not in str(record.links[0]["url"])


def test_expiry_erases_sensitive_content_but_retains_scope_tombstone() -> None:
    now = 1_789_263_000_000
    clock = [now]
    signed = "https://auth.example.com/oauth/authorize?state=s&client_id=c"
    store = ExecutionEvidenceStore(clock_ms=lambda: clock[0])
    record = store.put(
        principal_id="p_1", app_name="chrn_1", user_id="u_1",
        session_id="hsess_1", invocation_id="inv_1", tool_call_id="call_1",
        command=f"open '{signed}'", working_directory="/workspace", output=signed,
    )
    clock[0] = record.expiresAtMs + 1

    expired = store.get(record.evidenceRef)
    assert expired is not None and store.expired(expired)
    assert expired.command == expired.workingDirectory == expired.output == ""
    assert expired.links == ()


def test_output_update_discovers_authorization_url_while_command_is_running() -> None:
    now = 1_789_263_000_000
    signed = "https://auth.example.com/oauth/authorize?state=s&client_id=c"
    store = ExecutionEvidenceStore(clock_ms=lambda: now)
    record = store.put(
        principal_id="p_1", app_name="chrn_1", user_id="u_1",
        session_id="hsess_1", invocation_id="inv_1", tool_call_id="call_1",
        command="acme auth login", working_directory="/workspace",
    )
    updated = store.update_output(record.evidenceRef, f"Authorize at {signed}")

    assert updated is not None
    assert updated.links == ({
        "url": signed, "kind": "authorization", "expiresAtMs": record.expiresAtMs,
    },)
    assert signed in updated.output


def test_command_output_round_trips_100001_utf8_bytes_without_truncation() -> None:
    output = "x" * 100_000 + "\n"
    store = ExecutionEvidenceStore(clock_ms=lambda: 1_789_263_000_000)

    record = store.put(
        principal_id="p_1", app_name="chrn_1", user_id="u_1",
        session_id="hsess_1", invocation_id="inv_1", tool_call_id="call_1",
        command="python3 -c 'print(\"x\" * 100000)'",
        working_directory="/workspace", output=output,
    )

    assert MAX_EVIDENCE_BYTES == 8 << 20
    assert record.output == output
    assert len(record.output.encode("utf-8")) == 100_001


def test_command_output_beyond_8_mib_is_explicitly_bounded() -> None:
    output = "x" * (8 << 20) + "y"
    store = ExecutionEvidenceStore(clock_ms=lambda: 1_789_263_000_000)

    record = store.put(
        principal_id="p_1", app_name="chrn_1", user_id="u_1",
        session_id="hsess_1", invocation_id="inv_1", tool_call_id="call_1",
        command="produce-large-output", working_directory="/workspace", output=output,
    )

    assert len(record.output.encode("utf-8")) <= 8 << 20
    assert "...<truncated>" in record.output
    assert record.output.endswith("y")
