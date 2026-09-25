"""Failure-injection contracts for pause/cancel/continue control-plane routes."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import replace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from haas.api import build_app
from haas.harnesses.base import HarnessEvent
from haas.identity import Principal
from haas.sessions import (
    AdapterTurnError,
    InvocationNotFoundError,
    InvocationNotResumableError,
    InvocationNotRunningError,
    SessionBusyError,
)
from haas.stores import InvocationRecord, SessionRecord

pytestmark = pytest.mark.adk

TOKEN = "control-failure-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def _client() -> TestClient:
    return TestClient(
        build_app(
            identity_tokens={
                TOKEN: Principal(
                    principalId="p_control",
                    tenantId="t_control",
                    userIds=frozenset({"u_control"}),
                )
            }
        )
    )


def _seed(client: TestClient, *, sid: str, iid: str, status: str = "running") -> None:
    store = client.app.state.runtime.store
    store.put_session(SessionRecord(id=sid, appName="chrn_codex_default", userId="u_control"))
    store.put_invocation(
        InvocationRecord(
            id=iid,
            sessionId=sid,
            appName="chrn_codex_default",
            userId="u_control",
            turnId=f"turn_{iid}",
            status=status,
        )
    )


@pytest.mark.parametrize("with_key", [False, True])
@pytest.mark.parametrize(
    ("method_name", "error", "expected_status", "expected_code"),
    [
        ("cancel_invocation", InvocationNotFoundError("inv"), 404, "haas_invocation_not_found"),
        ("cancel_invocation", SessionBusyError("sid"), 409, "session_busy"),
        ("cancel_invocation", AdapterTurnError("inv"), 502, "haas_adapter_error"),
        ("pause_invocation", InvocationNotFoundError("inv"), 404, "haas_invocation_not_found"),
        ("pause_invocation", InvocationNotRunningError("inv"), 409, "haas_invocation_not_running"),
        ("pause_invocation", AdapterTurnError("inv"), 502, "haas_adapter_error"),
    ],
)
def test_control_route_failure_mapping_and_key_release(
    monkeypatch: pytest.MonkeyPatch,
    with_key: bool,
    method_name: str,
    error: Exception,
    expected_status: int,
    expected_code: str,
) -> None:
    client = _client()
    sid, iid = f"hsess_{method_name}_{with_key}", f"inv_{method_name}_{with_key}"
    _seed(client, sid=sid, iid=iid)

    async def fail(*args: Any, **kwargs: Any):
        raise error

    monkeypatch.setattr(client.app.state.runtime.sessions, method_name, fail)
    headers = dict(AUTH)
    if with_key:
        headers["Idempotency-Key"] = f"key-{method_name}-{type(error).__name__}"
    action = "cancel" if method_name == "cancel_invocation" else "pause"
    response = client.post(
        f"/v1/haas/sessions/{sid}/invocations/{iid}/{action}",
        headers=headers,
    )
    assert response.status_code == expected_status
    assert response.json()["haasError"]["code"] == expected_code

    if with_key:
        # The failed reservation must be released so an identical retry can own it.
        key_hashes = list(client.app.state.runtime.store._idempotency)
        assert all(not client.app.state.runtime.store.is_pending(key) for key in key_hashes)


@pytest.mark.parametrize("with_key", [False, True])
@pytest.mark.parametrize(
    ("error", "expected_status", "expected_code"),
    [
        (InvocationNotFoundError("inv"), 404, "haas_invocation_not_found"),
        (InvocationNotResumableError("inv"), 409, "haas_invocation_not_resumable"),
        (SessionBusyError("sid"), 409, "session_busy"),
    ],
)
def test_continue_failure_mapping_and_key_release(
    monkeypatch: pytest.MonkeyPatch,
    with_key: bool,
    error: Exception,
    expected_status: int,
    expected_code: str,
) -> None:
    client = _client()
    sid, iid = f"hsess_continue_{with_key}", f"inv_continue_{with_key}"
    _seed(client, sid=sid, iid=iid, status="interrupted")
    session = client.app.state.runtime.store.get_session(("chrn_codex_default", "u_control", sid))
    assert session is not None
    client.app.state.runtime.store.put_session(
        replace(session, controlState="paused", supportsResume=True, resumableInvocationId=iid)
    )

    async def failing_stream(*args: Any, **kwargs: Any) -> AsyncIterator[HarnessEvent]:
        raise error
        yield  # pragma: no cover

    monkeypatch.setattr(client.app.state.runtime.sessions, "continue_stream", failing_stream)
    headers = dict(AUTH)
    if with_key:
        headers["Idempotency-Key"] = f"continue-{type(error).__name__}"
    response = client.post(
        f"/v1/haas/sessions/{sid}/invocations/{iid}/continue",
        json={"additionalInstruction": "continue"},
        headers=headers,
    )
    assert response.status_code == expected_status
    assert response.json()["haasError"]["code"] == expected_code


@pytest.mark.parametrize("with_key", [False, True])
def test_continue_empty_stream_maps_adapter_error(
    monkeypatch: pytest.MonkeyPatch, with_key: bool
) -> None:
    client = _client()
    sid, iid = f"hsess_empty_{with_key}", f"inv_empty_{with_key}"
    _seed(client, sid=sid, iid=iid, status="interrupted")
    session = client.app.state.runtime.store.get_session(("chrn_codex_default", "u_control", sid))
    assert session is not None
    client.app.state.runtime.store.put_session(
        replace(session, controlState="paused", supportsResume=True, resumableInvocationId=iid)
    )

    async def empty(*args: Any, **kwargs: Any) -> AsyncIterator[HarnessEvent]:
        return
        yield  # pragma: no cover

    monkeypatch.setattr(client.app.state.runtime.sessions, "continue_stream", empty)
    headers = dict(AUTH)
    if with_key:
        headers["Idempotency-Key"] = "continue-empty"
    response = client.post(
        f"/v1/haas/sessions/{sid}/invocations/{iid}/continue",
        json={},
        headers=headers,
    )
    assert response.status_code == 502
    assert response.json()["haasError"]["code"] == "haas_adapter_error"


def test_cancel_success_replays_cached_envelope() -> None:
    client = _client()
    sid, iid = "hsess_cancel_success", "inv_cancel_success"
    _seed(client, sid=sid, iid=iid, status="completed")
    headers = {**AUTH, "Idempotency-Key": "cancel-success"}
    first = client.post(
        f"/v1/haas/sessions/{sid}/invocations/{iid}/cancel", headers=headers
    )
    second = client.post(
        f"/v1/haas/sessions/{sid}/invocations/{iid}/cancel", headers=headers
    )
    assert first.status_code == second.status_code == 200
    assert first.json()["data"] == second.json()["data"]
