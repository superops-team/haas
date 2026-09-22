from __future__ import annotations

import json

import httpx
import pytest
from coworker.haas import (
    AcceptedInvocationHeaders,
    HaasClient,
    HaasEndpoint,
    HaasProtocolError,
    HaasRemoteError,
    HaasTimeouts,
)


def _endpoint() -> HaasEndpoint:
    return HaasEndpoint.create(
        endpoint_id="hep_test",
        mode="remote",
        base_url="https://haas.example.com",
        token_ref="secret://manager/haas/test",
        server_identity="server-test",
    )


@pytest.mark.asyncio
async def test_sync_profile_creates_validates_activates_then_reuses():
    calls = []
    profile = {"id": "hprof_test", "version": 1, "status": "active", "harnessId": "chrn_test"}

    def handler(request):
        calls.append((request.method, request.url.path))
        data = {"valid": True} if request.url.path.endswith("/validate") else profile
        return httpx.Response(200, json={"data": data, "traceId": "tr_test"})

    client = HaasClient(
        _endpoint(), token_resolver=lambda _: "test", transport=httpx.MockTransport(handler)
    )
    assert await client.sync_profile({"harnessId": "chrn_test"}) == profile
    assert await client.sync_profile({"harnessId": "chrn_test"}, profile_id="hprof_test") == profile
    assert calls == [
        ("POST", "/v1/haas/profiles"),
        ("POST", "/v1/haas/profiles/hprof_test/validate"),
        ("POST", "/v1/haas/profiles/hprof_test/activate"),
        ("GET", "/v1/haas/profiles/hprof_test"),
    ]


@pytest.mark.asyncio
async def test_client_reads_recovery_safe_session_profile() -> None:
    seen: list[httpx.Request] = []
    recovery = {
        "profileId": "hprof_test",
        "profileVersion": 3,
        "profileFingerprint": "sha256:profile",
        "harnessId": "chrn_test",
        "base": "codex",
        "executionIntentFingerprint": "sha256:intent",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"data": recovery, "traceId": "tr_profile"})

    client = HaasClient(
        _endpoint(), token_resolver=lambda _: "token", transport=httpx.MockTransport(handler)
    )

    result = await client.get_session_profile("hsess_test")

    assert result.data == recovery
    assert seen[0].method == "GET"
    assert seen[0].url.path == "/v1/haas/sessions/hsess_test/profile"


@pytest.mark.asyncio
async def test_client_reads_typed_health_envelope_and_resolves_token_at_request_time() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"data": {"status": "ok"}, "traceId": "tr_1"})

    client = HaasClient(
        _endpoint(),
        token_resolver=lambda ref: "resolved-token",
        transport=httpx.MockTransport(handler),
    )

    result = await client.health()

    assert result.data == {"status": "ok"}
    assert result.trace_id == "tr_1"
    assert seen[0].headers["authorization"] == "Bearer resolved-token"


@pytest.mark.asyncio
async def test_client_preserves_structured_haas_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            503,
            json={
                "detail": "safe public detail",
                "haasError": {
                    "type": "invalid_request_error",
                    "code": "haas_adapter_unavailable",
                    "safeReason": "adapter_not_ready",
                    "retryable": True,
                    "traceId": "tr_err",
                },
            },
        )

    client = HaasClient(
        _endpoint(), token_resolver=lambda _: "token", transport=httpx.MockTransport(handler)
    )

    with pytest.raises(HaasRemoteError) as caught:
        await client.ready(scope="execution")

    assert caught.value.status_code == 503
    assert caught.value.code == "haas_adapter_unavailable"
    assert caught.value.safe_reason == "adapter_not_ready"
    assert caught.value.retryable is True
    assert caught.value.trace_id == "tr_err"


def test_accepted_headers_require_all_execution_identity_fields() -> None:
    accepted = AcceptedInvocationHeaders.parse(
        {
            "X-HaaS-Invocation-ID": "inv_123",
            "X-HaaS-Session-ID": "hsess_123",
            "Idempotency-Expires-At": "1786400000000",
        }
    )
    assert accepted.invocation_id == "inv_123"
    assert accepted.session_id == "hsess_123"
    assert accepted.idempotency_expires_at_ms == 1786400000000

    with pytest.raises(HaasProtocolError):
        AcceptedInvocationHeaders.parse({"X-HaaS-Invocation-ID": "inv_123"})


@pytest.mark.asyncio
async def test_client_exposes_recovery_and_configuration_surfaces() -> None:
    seen: list[tuple[str, str, dict[str, str], object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(
            (
                request.method,
                str(request.url),
                dict(request.headers),
                request.read().decode() if request.content else None,
            )
        )
        path = request.url.path
        if path.endswith("/events-page"):
            return httpx.Response(
                200,
                json={
                    "data": [{"eventId": "evt_2"}],
                    "nextCursor": "evt_2",
                    "traceId": "tr_events",
                },
            )
        if path.endswith("/policy"):
            return httpx.Response(
                202, json={"data": {"id": "dgsess_1", "desiredRevision": 2}, "traceId": "tr_policy"}
            )
        if "/invocations/" in path:
            return httpx.Response(
                200, json={"data": {"id": "inv_1", "status": "running"}, "traceId": "tr_inv"}
            )
        if path.endswith("/capabilities"):
            return httpx.Response(
                200, json={"data": {"object": "haas_capabilities"}, "traceId": "tr_caps"}
            )
        return httpx.Response(200, json={"data": {"id": "dgsess_1"}, "traceId": "tr_delegated"})

    client = HaasClient(
        _endpoint(), token_resolver=lambda _: "token", transport=httpx.MockTransport(handler)
    )

    assert (await client.capabilities()).data["object"] == "haas_capabilities"
    assert (await client.get_delegated_session("dgsess_1")).data["id"] == "dgsess_1"
    policy = await client.update_delegated_policy(
        "dgsess_1",
        {
            "profileRef": {
                "profileId": "hprof_1",
                "profileVersion": 2,
                "profileFingerprint": "sha256:p",
            }
        },
        idempotency_key="mgr-delegated-policy:dgsess_1:change-1",
    )
    assert policy.data["desiredRevision"] == 2
    local_policy = await client.update_session_policy(
        "hsess_1",
        {"network": {"defaultAction": "deny", "allow": []}},
        expected_revision=1,
        idempotency_key="mgr-local-policy:hsess_1:2",
    )
    assert local_policy.data["desiredRevision"] == 2
    assert (await client.get_invocation("hsess_1", "inv_1")).data["status"] == "running"
    page = await client.events_page("hsess_1", after_event_id="evt_1", limit=50)
    assert page.next_cursor == "evt_2"
    assert page.data == [{"eventId": "evt_2"}]

    policy_request = next(item for item in seen if item[0] == "POST")
    assert policy_request[2]["idempotency-key"] == "mgr-delegated-policy:dgsess_1:change-1"
    assert "after_event_id=evt_1" in seen[-1][1]
    assert "limit=50" in seen[-1][1]


@pytest.mark.asyncio
async def test_client_lists_and_downloads_artifacts() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/artifacts"):
            return httpx.Response(
                200,
                json={
                    "data": {
                        "artifacts": [
                            {
                                "id": "file_report",
                                "object": "file",
                                "sessionId": "hsess_1",
                                "filename": "report.md",
                                "relativePath": "output/report.md",
                                "bytes": 7,
                                "mediaType": "text/markdown",
                                "previewStatus": "available",
                                "downloadStatus": "available",
                            }
                        ]
                    },
                    "traceId": "tr_artifacts",
                },
            )
        if request.url.path.endswith("/files/file_report/content"):
            return httpx.Response(
                200,
                content=b"# Report",
                headers={"content-type": "text/markdown"},
            )
        if request.url.path.endswith("/artifacts/archive"):
            return httpx.Response(200, content=b"zip", headers={"content-type": "application/zip"})
        raise AssertionError(request.url.path)

    client = HaasClient(
        _endpoint(), token_resolver=lambda _: "token", transport=httpx.MockTransport(handler)
    )

    listed = await client.list_artifacts("hsess_1")
    assert listed.data == [
        {
            "id": "file_report",
            "object": "file",
            "sessionId": "hsess_1",
            "filename": "report.md",
            "relativePath": "output/report.md",
            "bytes": 7,
            "mediaType": "text/markdown",
            "previewStatus": "available",
            "downloadStatus": "available",
        }
    ]
    content, media_type = await client.download_file("file_report")
    assert content == b"# Report"
    assert media_type == "text/markdown"
    archive, archive_media_type = await client.download_artifact_archive("hsess_1")
    assert archive == b"zip"
    assert archive_media_type == "application/zip"
    assert [item.url.path for item in seen] == [
        "/v1/haas/sessions/hsess_1/artifacts",
        "/v1/haas/files/file_report/content",
        "/v1/haas/sessions/hsess_1/artifacts/archive",
    ]


@pytest.mark.asyncio
async def test_client_resolves_human_bridge_requests_with_stable_ids() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={"data": {"status": "resolved"}, "traceId": "tr_interaction"},
        )

    client = HaasClient(
        _endpoint(), token_resolver=lambda _: "token", transport=httpx.MockTransport(handler)
    )
    await client.resolve_approval("hsess_1", "appr_1", approved=True)
    await client.answer_input_request("hsess_1", "inreq_1", answers={"q1": {"values": ["yes"]}})

    assert seen[0].url.path.endswith("/approvals/appr_1")
    assert seen[0].headers["idempotency-key"] == "manager-approval:appr_1:approved"
    assert json.loads(seen[0].content) == {"decision": "approved", "scope": "action"}
    assert seen[1].url.path.endswith("/input-requests/inreq_1")
    assert seen[1].headers["idempotency-key"] == "manager-input:hsess_1:inreq_1"


@pytest.mark.asyncio
async def test_client_cancels_invocation_with_stable_idempotency_key() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "data": {"id": "inv_1", "sessionId": "hsess_1", "status": "running"},
                "traceId": "tr_cancel",
            },
        )

    client = HaasClient(
        _endpoint(), token_resolver=lambda _: "token", transport=httpx.MockTransport(handler)
    )

    result = await client.cancel_invocation("hsess_1", "inv_1")

    assert result.data["status"] == "running"
    assert seen[0].method == "POST"
    assert seen[0].url.path == "/v1/haas/sessions/hsess_1/invocations/inv_1/cancel"
    assert seen[0].headers["idempotency-key"] == "mgr-cancel:inv_1"


@pytest.mark.asyncio
async def test_client_pauses_and_continues_with_distinct_lifecycle_routes() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/pause"):
            return httpx.Response(
                200,
                json={
                    "data": {
                        "id": "inv_1",
                        "status": "interrupted",
                        "sessionControl": {
                            "controlState": "paused",
                            "supportsResume": True,
                            "resumableInvocationId": "inv_1",
                        },
                    },
                    "traceId": "tr_pause",
                },
            )
        return httpx.Response(
            200,
            headers={
                "content-type": "text/event-stream",
                "X-HaaS-Invocation-ID": "inv_2",
                "X-HaaS-Session-ID": "hsess_1",
                "Idempotency-Expires-At": "1786400000000",
            },
            content=b'data: {"id":"evt_2","invocationId":"inv_2"}\n\n',
        )

    client = HaasClient(
        _endpoint(), token_resolver=lambda _: "token", transport=httpx.MockTransport(handler)
    )

    paused = await client.pause_invocation("hsess_1", "inv_1")
    async with client.continue_invocation(
        "hsess_1", "inv_1", additional_instruction="Continue safely"
    ) as stream:
        events = [event async for event in stream.events()]

    assert paused.data["sessionControl"]["controlState"] == "paused"
    assert stream.accepted.invocation_id == "inv_2"
    assert events == [{"id": "evt_2", "invocationId": "inv_2"}]
    assert seen[0].url.path.endswith("/invocations/inv_1/pause")
    assert seen[0].headers["idempotency-key"] == "mgr-pause:inv_1"
    assert seen[1].url.path.endswith("/invocations/inv_1/continue")
    assert seen[1].headers["idempotency-key"] == "mgr-continue:inv_1"
    assert seen[1].read().decode() == '{"additionalInstruction":"Continue safely"}'


@pytest.mark.asyncio
async def test_client_rejects_malformed_envelopes_and_path_identifiers() -> None:
    client = HaasClient(
        _endpoint(),
        token_resolver=lambda _: "token",
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"data": {}})),
    )
    with pytest.raises(HaasProtocolError):
        await client.capabilities()
    with pytest.raises(ValueError):
        await client.get_delegated_session("../secret")


@pytest.mark.asyncio
async def test_run_sse_validates_accepted_headers_before_exposing_events() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["idempotency-key"] == "mgr-turn:s1:1:a1"
        return httpx.Response(
            200,
            headers={
                "content-type": "text/event-stream",
                "X-HaaS-Invocation-ID": "inv_1",
                "X-HaaS-Session-ID": "hsess_1",
                "Idempotency-Expires-At": "1786400000000",
            },
            content=b': heartbeat\n\ndata: {"id":"evt_1"}\n\n',
        )

    client = HaasClient(
        _endpoint(), token_resolver=lambda _: "token", transport=httpx.MockTransport(handler)
    )
    async with client.run_sse(
        {
            "appName": "chrn_codex",
            "userId": "manager",
            "sessionId": "hsess_1",
            "newMessage": {"role": "user", "parts": [{"text": "hi"}]},
            "streaming": True,
        },
        idempotency_key="mgr-turn:s1:1:a1",
    ) as stream:
        assert stream.accepted.invocation_id == "inv_1"
        assert [event async for event in stream.events()] == [{"id": "evt_1"}]


@pytest.mark.asyncio
async def test_run_sse_retries_pre_acceptance_transport_failure_with_same_key() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.ConnectError("refused", request=request)
        return httpx.Response(
            200,
            headers={
                "content-type": "text/event-stream",
                "X-HaaS-Invocation-ID": "inv_2",
                "X-HaaS-Session-ID": "hsess_1",
                "Idempotency-Expires-At": "1786400000000",
            },
            content=b'data: {"id":"evt_2"}\n\n',
        )

    client = HaasClient(
        _endpoint(),
        token_resolver=lambda _: "token",
        transport=httpx.MockTransport(handler),
        timeouts=HaasTimeouts(connect=1, response_header=2, stream_idle=3, turn=4),
        reconnect_max_attempts=1,
        reconnect_backoff_initial=0,
    )
    async with client.run_sse(
        {"sessionId": "hsess_1"}, idempotency_key="mgr-turn:s1:1:a1"
    ) as stream:
        assert [event async for event in stream.events()] == [{"id": "evt_2"}]
    assert attempts == 2


@pytest.mark.asyncio
async def test_run_sse_rejects_success_without_accepted_headers() -> None:
    client = HaasClient(
        _endpoint(),
        token_resolver=lambda _: "token",
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, headers={"content-type": "text/event-stream"})
        ),
    )
    with pytest.raises(HaasProtocolError):
        async with client.run_sse({"sessionId": "hsess_1"}, idempotency_key="mgr-turn:s1:1:a1"):
            pass


@pytest.mark.asyncio
async def test_invocation_events_streams_native_frames_with_cursor() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/haas/sessions/hsess_1/invocations/inv_1/events"
        assert request.url.params["after_event_id"] == "evt_1"
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=(b': keep-alive\n\ndata: {"eventId":"evt_2","type":"haas.tool.started"}\n\n'),
        )

    client = HaasClient(
        _endpoint(), token_resolver=lambda _: "token", transport=httpx.MockTransport(handler)
    )

    events = [
        event
        async for event in client.invocation_events("hsess_1", "inv_1", after_event_id="evt_1")
    ]
    assert events == [{"eventId": "evt_2", "type": "haas.tool.started"}]
