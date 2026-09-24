"""P0 API hardening: safe_reason leak (P0-1) + SSE structured frames (P0-2)."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from haas.api import build_app
from haas.harnesses.base import (
    AdapterProbe,
    AdapterTurnResult,
    ArtifactRef,
    HarnessEvent,
    ListArtifactsRequest,
    PreparedSession,
    PrepareSessionRequest,
    StartTurnRequest,
    TurnHandle,
)
from haas.harnesses.fake import FakeAdapter
from haas.identity import Principal

pytestmark = pytest.mark.adk

TOKEN = "hardening-token"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}


class _ExplodingProbeAdapter(FakeAdapter):
    """probe() raises an exception that would leak internal connection details."""

    base = "fake-exploding-probe"
    adapter_id = "fake-exploding-probe"

    async def probe(self) -> AdapterProbe:  # type: ignore[override]
        raise RuntimeError(
            "connect to https://secret-provider.internal:8443/v1/chat/completions failed"
        )


def make_client(**kwargs: object) -> TestClient:
    tokens = {TOKEN: Principal(principalId="p_1", tenantId="t1", userIds=frozenset({"u_1"}))}
    app = build_app(
        adapter=kwargs.get("adapter", FakeAdapter()),
        identity_tokens=tokens,
    )
    return TestClient(app)


def test_execution_ready_probe_failure_does_not_leak_upstream_url() -> None:
    client = make_client(adapter=_ExplodingProbeAdapter())
    resp = client.get("/v1/haas/ready?scope=execution")
    assert resp.status_code == 503
    body = resp.text
    # The upstream URL / host must never reach the northbound client.
    assert "secret-provider.internal" not in body
    assert "8443" not in body
    haas_error = resp.json()["haasError"]
    assert haas_error["code"] == "haas_adapter_unavailable"
    assert haas_error["safeReason"] == "adapter_probe_failed"


def _run_body(session_id: str) -> dict[str, object]:
    return {
        "appName": "chrn_codex_default",
        "userId": "u_1",
        "sessionId": session_id,
        "newMessage": {"role": "user", "parts": [{"text": "hi"}]},
    }


class _FloodAdapter(FakeAdapter):
    """Yields N text deltas synchronously (no awaits) to flood the queue."""

    base = "fake-flood"
    adapter_id = "fake-flood"

    def __init__(self, count: int = 300) -> None:
        self.count = count

    async def stream_events(self, handle: TurnHandle):  # type: ignore[override]
        for i in range(self.count):
            yield HarnessEvent(
                type="harness.text.delta",
                nativeType="fake/text",
                invocationId=handle.invocationId,
                sessionId=handle.sessionId,
                turnId=handle.turnId,
                author=self.base,
                content={"role": "model", "parts": [{"text": f"msg-{i}"}]},
                actions={"stateDelta": {"seq": i}},
            )


def _id_lines(text: str) -> list[str]:
    return [line[len("id: ") :] for line in text.splitlines() if line.startswith("id: ")]


def test_run_sse_emits_structured_id_and_retry_frames() -> None:
    client = make_client()
    with client.stream(
        "POST", "/run_sse", json=_run_body("hsess_struct"), headers=HEADERS
    ) as resp:
        assert resp.status_code == 200
        raw = resp.read()
    text = raw.decode()
    # Every event carries its eventId as an `id:` line and a retry hint.
    assert "retry: 15000" in text
    ids = _id_lines(text)
    assert len(ids) >= 3
    assert all(e.startswith("evt_") for e in ids)
    # The data payload is still the ADK event.
    assert "data: " in text
    assert "hello" in text


def test_run_sse_last_event_id_replay_skips_delivered() -> None:
    client = make_client()
    # First live run delivers the session's events.
    with client.stream(
        "POST", "/run_sse", json=_run_body("hsess_replay"), headers=HEADERS
    ) as resp:
        first_text = resp.read().decode()
    delivered = _id_lines(first_text)
    assert len(delivered) >= 2
    # Resume after the second delivered event: events at/before the cursor must
    # not reappear, and the tail event(s) of the same invocation are replayed.
    cursor = delivered[1]
    with client.stream(
        "POST",
        "/run_sse",
        json=_run_body("hsess_replay"),
        headers={**HEADERS, "Last-Event-ID": cursor},
    ) as resp:
        assert resp.status_code == 200
        text = resp.read().decode()
    replayed = _id_lines(text)
    assert delivered[0] not in replayed
    assert cursor not in replayed
    # The tail event(s) from the same invocation are replayed.
    assert delivered[-1] in replayed


def test_run_sse_backpressure_disconnects(monkeypatch: pytest.MonkeyPatch) -> None:
    # Shrink the bounded queue so a synchronous flood overflows it fast.
    import haas.api as api_mod

    monkeypatch.setattr(api_mod, "SSE_QUEUE_MAXSIZE", 4)
    client = make_client(adapter=_FloodAdapter(300))
    with client.stream(
        "POST", "/run_sse", json=_run_body("hsess_flood"), headers=HEADERS
    ) as resp:
        text = resp.read().decode()
    assert "haas_sse_backpressure" in text


def test_trace_id_middleware_assigns_per_request_id() -> None:
    client = make_client()
    r1 = client.get("/v1/haas/health")
    body_tid = r1.json()["traceId"]
    header_tid = r1.headers.get("X-Trace-Id")
    assert body_tid == header_tid
    assert body_tid.startswith("tr_")
    assert body_tid != "tr_local"
    # A second request gets a fresh, different trace id.
    r2 = client.get("/v1/haas/health")
    assert r2.json()["traceId"] != body_tid
    assert r2.headers["X-Trace-Id"] == r2.json()["traceId"]


def test_trace_id_on_ready_and_error_responses() -> None:
    client = make_client()
    ready = client.get("/v1/haas/ready?scope=control")
    assert ready.json()["traceId"] == ready.headers["X-Trace-Id"]
    assert ready.json()["traceId"] != "tr_local"

    # Error response (unauthenticated) shares the request trace id.
    err = client.get("/list-apps")
    assert err.status_code == 401
    assert err.json()["haasError"]["traceId"] == err.headers["X-Trace-Id"]
    assert err.json()["haasError"]["traceId"].startswith("tr_")


def test_session_busy_reason_maps_pending_policy_sentinel() -> None:
    from haas.api import _session_busy_reason
    from haas.sessions import SessionBusyError

    # The two production raise-sites in sessions.py.
    assert _session_busy_reason(SessionBusyError("configuration_update_pending")) == (
        "configuration_update_pending"
    )
    assert _session_busy_reason(SessionBusyError("hsess_abc123")) == "session_busy"
    # A structured `.reason` attribute (future sessions.py contract) wins.
    pending = SessionBusyError("hsess_abc123")
    pending.reason = "configuration_update_pending"  # type: ignore[attr-defined]
    assert _session_busy_reason(pending) == "configuration_update_pending"


def test_all_protected_routes_inject_principal_dependency() -> None:
    # P1-1 Stage 1 safety: no route may silently run unauthenticated. Every
    # route except liveness/readiness must carry the PrincipalDep dependency.
    client = make_client()
    app = client.app
    # /run and /run_sse are thin wrappers over the module-level `_run` helper,
    # which authenticates internally (it needs the raw request body first).
    public = {
        "/v1/haas/health",
        "/v1/haas/ready",
        "/health",
        "/ready",
        "/run",
        "/run_sse",
    }

    def calls_auth(dependant) -> bool:
        stack = [dependant]
        while stack:
            dep = stack.pop()
            call = getattr(dep, "call", None)
            if call is not None and getattr(call, "__name__", "") == "get_principal":
                return True
            stack.extend(getattr(dep, "dependencies", []))
        return False

    missing: list[str] = []
    for route in app.routes:
        dependant = getattr(route, "dependant", None)
        if dependant is None:
            continue
        path = getattr(route, "path", "")
        if path in public:
            continue
        if not calls_auth(dependant):
            missing.append(path)
    assert missing == [], f"routes missing PrincipalDep auth: {missing}"


def test_protected_route_rejects_missing_credential() -> None:
    client = make_client()
    r = client.get("/v1/haas/harnesses")  # no Authorization header
    assert r.status_code == 401
    assert r.json()["haasError"]["code"] == "missing_credential"
