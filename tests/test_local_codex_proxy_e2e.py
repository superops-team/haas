import asyncio
import contextlib
import json
import os
import socket
from typing import Any

import httpx
import pytest

from haas.api import build_app
from haas.harnesses.codex_app_server.adapter import CodexAdapter
from haas.harnesses.codex_app_server.transport import CodexEndpoint
from haas.model_proxy.runtime import RuntimeModelProxy
from haas.model_proxy.secret import LocalCredentialResolver


async def _asgi_stream_request(
    app: Any,
    path: str,
    body: dict[str, Any],
    *,
    headers: tuple[tuple[bytes, bytes], ...],
    response_started: asyncio.Event,
    response_headers: dict[str, str],
) -> bytes:
    request_sent = False
    chunks: list[bytes] = []

    async def receive() -> dict[str, Any]:
        nonlocal request_sent
        if not request_sent:
            request_sent = True
            return {
                "type": "http.request",
                "body": json.dumps(body).encode(),
                "more_body": False,
            }
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    async def send(message: dict[str, Any]) -> None:
        if message["type"] == "http.response.start":
            response_headers.update(
                {key.decode().lower(): value.decode() for key, value in message.get("headers", [])}
            )
            response_started.set()
        elif message["type"] == "http.response.body":
            chunks.append(message.get("body", b""))

    await app(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "headers": list(headers),
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 80),
            "root_path": "",
        },
        receive,
        send,
    )
    return b"".join(chunks)


def _response_frames(response_id: str, item: dict[str, Any]) -> str:
    response = {
        "id": response_id,
        "object": "response",
        "status": "completed",
        "output": [item],
        "usage": {
            "input_tokens": 1,
            "output_tokens": 1,
            "total_tokens": 2,
            "input_tokens_details": {"cached_tokens": 0},
            "output_tokens_details": {"reasoning_tokens": 0},
        },
    }
    frames = [
        {"type": "response.created", "response": {**response, "output": []}},
        {"type": "response.output_item.done", "output_index": 0, "item": item},
        {"type": "response.completed", "response": response},
    ]
    return "".join("data: " + json.dumps(frame) + "\n\n" for frame in frames)


def _function_call_item(call_id: str, command: str) -> dict[str, Any]:
    return {
        "id": f"item_{call_id}",
        "type": "function_call",
        "status": "completed",
        "call_id": call_id,
        "name": "exec_command",
        "arguments": json.dumps(
            {"cmd": command, "login": False, "yield_time_ms": 1000},
            separators=(",", ":"),
        ),
    }


@pytest.mark.e2e
@pytest.mark.skipif(
    os.environ.get("HAAS_E2E_CODEX") != "1", reason="explicit real Codex opt-in required"
)
async def test_real_codex_local_proxy_two_turns_and_model_update(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("CODEX_HOME", raising=False)
    binary = os.environ.get("COWORKER_CODEX_BIN", "codex")
    adapter = CodexAdapter(
        CodexEndpoint(transport="stdio", listen_url="stdio://"), codex_bin=binary
    )
    native_errors = []
    original_notifications = adapter._rpc.notifications

    async def notifications(*, after=0):
        async for notification in original_notifications(after=after):
            if notification.get("method") in {"error", "turn/completed"}:
                native_errors.append(notification)
            yield notification

    monkeypatch.setattr(adapter._rpc, "notifications", notifications)
    app = build_app(adapter=adapter)
    parent, child = socket.socketpair()
    parent.setblocking(False)
    resolver = LocalCredentialResolver(child.detach())
    proxy = RuntimeModelProxy(app.state.runtime.registry, resolver, "127.0.0.1:0")
    app.state.runtime.sessions.model_proxy = proxy
    seen = []
    key = "local-fixture-credential"

    async def credentials():
        loop = asyncio.get_running_loop()
        buffer = b""
        while True:
            data = await loop.sock_recv(parent, 16384)
            if not data:
                return
            buffer += data
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                request = json.loads(line)
                assert request["sessionId"] == "hsess_e2e"
                await loop.sock_sendall(
                    parent,
                    json.dumps({"sequence": request["sequence"], "value": key}).encode() + b"\n",
                )

    def upstream(request):
        assert request.headers["authorization"] == f"Bearer {key}"
        body = json.loads(request.content)
        seen.append(body["model"])
        assert body["tools"]
        assert all(tool["type"] == "function" for tool in body["tools"])
        assert body.get("reasoning", {}).get("summary") == "auto"
        item = {
            "id": "msg_test",
            "type": "message",
            "role": "assistant",
            "status": "completed",
            "content": [{"type": "output_text", "text": "LOCAL-PROXY-OK", "annotations": []}],
        }
        response = {
            "id": "resp_test",
            "object": "response",
            "status": "completed",
            "output": [item],
            "usage": {
                "input_tokens": 1,
                "output_tokens": 1,
                "total_tokens": 2,
                "input_tokens_details": {"cached_tokens": 0},
                "output_tokens_details": {"reasoning_tokens": 0},
            },
        }
        frames = [
            {
                "type": "response.created",
                "response": {**response, "status": "in_progress", "output": []},
            },
            {
                "type": "response.output_item.added",
                "output_index": 0,
                "item": {**item, "status": "in_progress", "content": []},
            },
            {
                "type": "response.output_text.delta",
                "item_id": item["id"],
                "output_index": 0,
                "content_index": 0,
                "delta": "LOCAL-PROXY-OK",
            },
            {"type": "response.output_item.done", "output_index": 0, "item": item},
            {"type": "response.completed", "response": response},
        ]
        return httpx.Response(
            200,
            text="".join("data: " + json.dumps(frame) + "\n\n" for frame in frames),
            headers={"content-type": "text/event-stream"},
        )

    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
    proxy.proxy._client = upstream_client
    credential_task = asyncio.create_task(credentials())
    auth = {"Authorization": "Bearer dev-token"}
    try:
        async with (
            proxy.lifespan(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://haas", headers=auth
            ) as client,
        ):
            thread_id = None
            for index, model in enumerate(["gpt-5", "gpt-5.1"]):
                body = {
                    "harnessId": "chrn_codex_default",
                    "base": "codex",
                    "provider": {
                        "providerId": "test",
                        "name": "test",
                        "model": model,
                        "baseUrl": "https://provider.example/v1",
                        "credentialRef": "secret://test/model",
                        "wireApi": "responses",
                        "apiType": "responses",
                    },
                }
                profile = (await client.post("/v1/haas/profiles", json=body)).json()["data"]
                assert (await client.post(f"/v1/haas/profiles/{profile['id']}/validate")).json()[
                    "data"
                ]["valid"]
                assert (
                    await client.post(f"/v1/haas/profiles/{profile['id']}/activate")
                ).status_code == 200
                if index:
                    assert (
                        await client.post(
                            "/v1/haas/sessions/hsess_e2e/profile-rebind",
                            json={"profileId": profile["id"]},
                        )
                    ).status_code == 200
                async with asyncio.timeout(45):
                    result = await client.post(
                        "/run",
                        json={
                            "appName": "chrn_codex_default",
                            "userId": "u_test",
                            "sessionId": "hsess_e2e",
                            "newMessage": {"role": "user", "parts": [{"text": "Reply OK"}]},
                            "sandbox": {"mode": "read-only", "workspaceRoot": str(tmp_path)},
                            "haas": {
                                "profileId": profile["id"],
                                "profileVersion": profile["version"],
                            },
                        },
                    )
                assert result.status_code == 200, result.text
                invocations = list(app.state.runtime.store._invocations.values())
                assert invocations[-1].status == "completed", json.dumps(native_errors)
                assert "LOCAL-PROXY-OK" in result.text
                current_thread = adapter._session_threads["hsess_e2e"]
                assert thread_id is None or thread_id == current_thread
                thread_id = current_thread
                assert not proxy.routes
                assert not proxy.tokens._tokens
            assert seen == ["gpt-5", "gpt-5.1"]
        assert not resolver.available
        assert not proxy.base_url
    finally:
        await adapter.close()
        await upstream_client.aclose()
        parent.close()
        credential_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await credential_task


@pytest.mark.e2e
@pytest.mark.skipif(
    os.environ.get("HAAS_E2E_CODEX") != "1", reason="explicit real Codex opt-in required"
)
async def test_real_codex_cancel_and_network_policy(tmp_path, monkeypatch):
    """Exercise real shell cancellation and sandbox network enforcement."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("CODEX_HOME", raising=False)
    adapter = CodexAdapter(
        CodexEndpoint(transport="stdio", listen_url="stdio://"),
        codex_bin=os.environ.get("COWORKER_CODEX_BIN", "codex"),
    )
    app = build_app(adapter=adapter)
    parent, child = socket.socketpair()
    parent.setblocking(False)
    resolver = LocalCredentialResolver(child.detach())
    proxy = RuntimeModelProxy(app.state.runtime.registry, resolver, "127.0.0.1:0")
    app.state.runtime.sessions.model_proxy = proxy
    key = "local-fixture-credential"
    started = tmp_path / "cancel-probe.txt"
    pause_started = tmp_path / "pause-probe.txt"
    deny_result = tmp_path / "network-deny.txt"
    allow_result = tmp_path / "network-allow.txt"

    async def credentials():
        loop = asyncio.get_running_loop()
        buffer = b""
        while True:
            data = await loop.sock_recv(parent, 16384)
            if not data:
                return
            buffer += data
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                request = json.loads(line)
                await loop.sock_sendall(
                    parent,
                    json.dumps({"sequence": request["sequence"], "value": key}).encode() + b"\n",
                )

    async def http_probe(reader, writer):
        await reader.read(4096)
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nOK")
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(http_probe, "127.0.0.1", 0)
    probe_port = server.sockets[0].getsockname()[1]

    commands = {
        "CANCEL_PROBE": f"printf started > '{started}'; sleep 30; printf completed > '{started}'",
        "PAUSE_PROBE": (
            f"printf started > '{pause_started}'; sleep 30; "
            f"printf completed > '{pause_started}'"
        ),
        "NETWORK_DENY_PROBE": (
            f"if curl -fsS --max-time 3 http://127.0.0.1:{probe_port}/health >/dev/null; "
            f"then printf allowed > '{deny_result}'; else printf denied > '{deny_result}'; fi"
        ),
        "NETWORK_ALLOW_PROBE": (
            f"if curl -fsS --max-time 3 http://127.0.0.1:{probe_port}/health >/dev/null; "
            f"then printf allowed > '{allow_result}'; else printf denied > '{allow_result}'; fi"
        ),
    }

    def upstream(request):
        assert request.headers["authorization"] == f"Bearer {key}"
        body = json.loads(request.content)
        inputs = body.get("input", [])
        encoded = json.dumps(inputs)
        if "CONTINUE_AFTER_PAUSE" in encoded:
            item = {
                "id": "msg_pause_continued",
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [
                    {"type": "output_text", "text": "PAUSE-CONTINUE-OK", "annotations": []}
                ],
            }
            payload = _response_frames("resp_pause_continued", item)
        elif any(
            isinstance(item, dict) and item.get("type") == "function_call_output" for item in inputs
        ):
            item = {
                "id": "msg_probe_done",
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": "PROBE-DONE", "annotations": []}],
            }
            payload = _response_frames("resp_probe_done", item)
        else:
            probe = next(name for name in commands if name in encoded)
            payload = _response_frames(
                f"resp_{probe.lower()}",
                _function_call_item(f"call_{probe.lower()}", commands[probe]),
            )
        return httpx.Response(200, text=payload, headers={"content-type": "text/event-stream"})

    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
    proxy.proxy._client = upstream_client
    credential_task = asyncio.create_task(credentials())
    auth = {"Authorization": "Bearer dev-token"}
    raw_auth = (
        (b"authorization", b"Bearer dev-token"),
        (b"content-type", b"application/json"),
        (b"idempotency-key", b"real-cancel-probe"),
    )
    try:
        async with (
            proxy.lifespan(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://haas", headers=auth
            ) as client,
        ):
            profile_body = {
                "harnessId": "chrn_codex_default",
                "base": "codex",
                "provider": {
                    "providerId": "test",
                    "name": "test",
                    "model": "gpt-5",
                    "baseUrl": "https://provider.example/v1",
                    "credentialRef": "secret://test/model",
                    "wireApi": "responses",
                    "apiType": "responses",
                },
            }
            profile = (await client.post("/v1/haas/profiles", json=profile_body)).json()["data"]
            assert (
                await client.post(f"/v1/haas/profiles/{profile['id']}/validate")
            ).status_code == 200
            assert (
                await client.post(f"/v1/haas/profiles/{profile['id']}/activate")
            ).status_code == 200

            response_started = asyncio.Event()
            response_headers: dict[str, str] = {}
            cancel_body = {
                "appName": "chrn_codex_default",
                "userId": "u_test",
                "sessionId": "hsess_real_cancel",
                "newMessage": {
                    "role": "user",
                    "parts": [{"text": "Run the command for CANCEL_PROBE exactly."}],
                },
                "sandbox": {
                    "mode": "workspace-write",
                    "workspaceRoot": str(tmp_path),
                    "writableRoots": [str(tmp_path)],
                },
                "policy": {
                    "approvalPolicy": "never",
                    "network": {"defaultAction": "deny", "allow": []},
                },
                "haas": {"profileId": profile["id"], "profileVersion": profile["version"]},
            }
            run_task = asyncio.create_task(
                _asgi_stream_request(
                    app,
                    "/run_sse",
                    cancel_body,
                    headers=raw_auth,
                    response_started=response_started,
                    response_headers=response_headers,
                )
            )
            async with asyncio.timeout(15):
                while not started.exists():
                    await asyncio.sleep(0.05)
                await response_started.wait()
            invocation_id = response_headers["x-haas-invocation-id"]
            cancel = await client.post(
                f"/v1/haas/sessions/hsess_real_cancel/invocations/{invocation_id}/cancel",
                headers={"Idempotency-Key": f"mgr-cancel:{invocation_id}"},
            )
            assert cancel.status_code == 200, cancel.text
            stream_body = await asyncio.wait_for(run_task, timeout=15)
            frames = [
                json.loads(line.removeprefix("data: "))
                for line in stream_body.decode().splitlines()
                if line.startswith("data: ")
            ]
            assert frames[-1]["actions"]["stateDelta"]["status"] == "cancelled"
            assert (
                await client.get(f"/v1/haas/sessions/hsess_real_cancel/invocations/{invocation_id}")
            ).json()["data"]["status"] == "cancelled"
            await asyncio.sleep(1)
            assert started.read_text() == "started"

            pause_started_event = asyncio.Event()
            pause_headers: dict[str, str] = {}
            pause_body = {
                **cancel_body,
                "sessionId": "hsess_real_pause",
                "newMessage": {
                    "role": "user",
                    "parts": [{"text": "Run the command for PAUSE_PROBE exactly."}],
                },
            }
            pause_run_task = asyncio.create_task(
                _asgi_stream_request(
                    app,
                    "/run_sse",
                    pause_body,
                    headers=(
                        (b"authorization", b"Bearer dev-token"),
                        (b"content-type", b"application/json"),
                        (b"idempotency-key", b"real-pause-probe"),
                    ),
                    response_started=pause_started_event,
                    response_headers=pause_headers,
                )
            )
            async with asyncio.timeout(15):
                while not pause_started.exists():
                    await asyncio.sleep(0.05)
                await pause_started_event.wait()
            source_invocation_id = pause_headers["x-haas-invocation-id"]
            source_thread_id = adapter._session_threads["hsess_real_pause"]
            paused = await client.post(
                f"/v1/haas/sessions/hsess_real_pause/invocations/{source_invocation_id}/pause",
                headers={"Idempotency-Key": f"mgr-pause:{source_invocation_id}"},
            )
            assert paused.status_code == 200, paused.text
            assert paused.json()["data"]["status"] == "interrupted"
            assert paused.json()["data"]["sessionControl"] == {
                "controlState": "paused",
                "supportsResume": True,
                "resumableInvocationId": source_invocation_id,
            }
            pause_stream_body = await asyncio.wait_for(pause_run_task, timeout=15)
            pause_frames = [
                json.loads(line.removeprefix("data: "))
                for line in pause_stream_body.decode().splitlines()
                if line.startswith("data: ")
            ]
            assert pause_frames[-1]["actions"]["stateDelta"]["status"] == "interrupted"
            assert pause_started.read_text() == "started"

            continued = await client.post(
                f"/v1/haas/sessions/hsess_real_pause/invocations/{source_invocation_id}/continue",
                json={"additionalInstruction": "CONTINUE_AFTER_PAUSE"},
                headers={"Idempotency-Key": f"mgr-continue:{source_invocation_id}"},
            )
            assert continued.status_code == 200, continued.text
            continued_invocation_id = continued.headers["x-haas-invocation-id"]
            assert continued_invocation_id != source_invocation_id
            assert adapter._session_threads["hsess_real_pause"] == source_thread_id
            continued_frames = [
                json.loads(line.removeprefix("data: "))
                for line in continued.text.splitlines()
                if line.startswith("data: ")
            ]
            assert continued_frames[-1]["actions"]["stateDelta"]["status"] == "completed"
            source_record = app.state.runtime.store.get_invocation(source_invocation_id)
            continued_record = app.state.runtime.store.get_invocation(continued_invocation_id)
            assert source_record is not None and source_record.status == "interrupted"
            assert continued_record is not None
            assert continued_record.continuedFromInvocationId == source_invocation_id
            assert continued_record.executionContext == source_record.executionContext

            async def run_network_probe(session_id: str, marker: str, allow: bool):
                result = await client.post(
                    "/run",
                    json={
                        "appName": "chrn_codex_default",
                        "userId": "u_test",
                        "sessionId": session_id,
                        "newMessage": {"role": "user", "parts": [{"text": marker}]},
                        "sandbox": {
                            "mode": "workspace-write",
                            "workspaceRoot": str(tmp_path),
                            "writableRoots": [str(tmp_path)],
                        },
                        "policy": {
                            "approvalPolicy": "never",
                            "network": {
                                "defaultAction": "allow" if allow else "deny",
                                "allow": [],
                            },
                        },
                        "haas": {
                            "profileId": profile["id"],
                            "profileVersion": profile["version"],
                        },
                    },
                )
                assert result.status_code == 200, result.text

            await run_network_probe("hsess_network_deny", "NETWORK_DENY_PROBE", False)
            await run_network_probe("hsess_network_allow", "NETWORK_ALLOW_PROBE", True)
            assert deny_result.read_text() == "denied"
            assert allow_result.read_text() == "allowed"
    finally:
        server.close()
        await server.wait_closed()
        await adapter.close()
        await upstream_client.aclose()
        parent.close()
        credential_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await credential_task
