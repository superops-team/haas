"""Branch-fill tests (suite B) for SessionRuntime.

Complements ``test_sessions_branch_fill.py`` by exercising the durable
readback / recovery, control-plane rejection, and artifact-publication
resilience branches that neither the happy-path suite nor the first branch
fill touches:

* ``reconcile_invocation_readback`` double-check fence (unknown / wrong-session,
  already-terminal, active, sidecar-restart ``incomplete``, lease conflict, and
  the post-lease second-read ``not found`` path that releases the lease).
* ``_drive`` refusing a new run while a policy configuration update is pending.
* An adapter that returns an empty harness event stream (finalize-derived
  terminal must still close the turn).
* ``update_policy`` on a ``pausing`` session staying ``pending``.
* Artifact publication best-effort resilience: non-bytes payloads, traversal
  paths, duplicate registrations, and a failing listing must never rewrite a
  successful harness turn.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest

from haas.artifacts import ArtifactStore
from haas.events import EventLog
from haas.harnesses import FakeAdapter, HarnessEvent, ListArtifactsRequest
from haas.harnesses.base import ArtifactRef, TurnHandle
from haas.identity import Principal
from haas.registry import HarnessRegistry, seed_codex
from haas.sessions import (
    InvocationNotFoundError,
    RunRequest,
    SessionBusyError,
    SessionRuntime,
    _ActiveTurn,
)
from haas.stores import (
    InvocationRecord,
    MemoryStore,
    SessionRecord,
    TurnRecord,
)

APP = "chrn_codex_default"


@pytest.fixture
def runtime() -> SessionRuntime:
    store = MemoryStore()
    registry = HarnessRegistry(store=store)
    seed_codex(registry)
    return SessionRuntime(
        store=store,
        registry=registry,
        adapter=FakeAdapter(),
        event_log=EventLog(store=store),
    )


def _seed_durable(
    runtime: SessionRuntime,
    sid: str,
    iid: str,
    tid: str,
    *,
    status: str = "running",
) -> tuple[SessionRecord, InvocationRecord, TurnRecord]:
    session = SessionRecord(id=sid, appName=APP, userId="u_1")
    runtime.store.put_session(session)
    invocation = runtime.store.put_invocation(
        InvocationRecord(
            id=iid,
            sessionId=sid,
            appName=APP,
            userId="u_1",
            turnId=tid,
            status=status,
        )
    )
    turn = runtime.store.put_turn(
        TurnRecord(id=tid, invocationId=invocation.id, sessionId=sid)
    )
    return session, invocation, turn


def _make_runtime(
    adapter: FakeAdapter, *, with_artifacts: bool = False
) -> tuple[SessionRuntime, Any]:
    store = MemoryStore()
    registry = HarnessRegistry(store=store)
    app = seed_codex(registry)
    runtime = SessionRuntime(
        store=store,
        registry=registry,
        adapter=adapter,
        event_log=EventLog(store=store),
        artifacts=ArtifactStore() if with_artifacts else None,
    )
    return runtime, app


# --- reconcile_invocation_readback ---------------------------------------


def test_reconcile_unknown_invocation_raises(runtime: SessionRuntime) -> None:
    with pytest.raises(InvocationNotFoundError):
        runtime.reconcile_invocation_readback("hs_x", "inv_missing")


def test_reconcile_wrong_session_raises_not_found(runtime: SessionRuntime) -> None:
    _seed_durable(runtime, "hs_real", "inv_real", "turn_real")
    with pytest.raises(InvocationNotFoundError):
        runtime.reconcile_invocation_readback("hs_wrong", "inv_real")


def test_reconcile_terminal_invocation_is_returned_untouched(
    runtime: SessionRuntime,
) -> None:
    session, invocation, _ = _seed_durable(
        runtime, "hs_term", "inv_term", "turn_term", status="completed"
    )
    out = runtime.reconcile_invocation_readback(session.id, invocation.id)
    assert out.status == "completed"
    assert runtime.store.get_invocation(invocation.id).status == "completed"


async def test_reconcile_active_invocation_is_untouched(
    runtime: SessionRuntime,
) -> None:
    session, invocation, turn = _seed_durable(runtime, "hs_act", "inv_act", "turn_act")
    loop = asyncio.get_running_loop()
    runtime._active[invocation.id] = _ActiveTurn(
        turn_id=turn.id,
        session_id=session.id,
        terminal_status=loop.create_future(),
    )
    try:
        out = runtime.reconcile_invocation_readback(session.id, invocation.id)
        assert out.status == "running"
    finally:
        runtime._active.pop(invocation.id, None)


def test_reconcile_sidecar_restart_marks_incomplete(runtime: SessionRuntime) -> None:
    session, invocation, _ = _seed_durable(runtime, "hs_restart", "inv_restart", "turn_restart")
    out = runtime.reconcile_invocation_readback(session.id, invocation.id)
    assert out.status == "incomplete"
    refreshed = runtime.get_session(APP, "u_1", session.id)
    assert refreshed.controlState == "idle"
    assert refreshed.supportsResume is False


def test_reconcile_lease_conflict_returns_still_running(
    runtime: SessionRuntime,
) -> None:
    session, invocation, _ = _seed_durable(runtime, "hs_conf", "inv_conf", "turn_conf")
    runtime.store.acquire_lease((APP, "u_1", session.id), holder="other-sidecar")
    out = runtime.reconcile_invocation_readback(session.id, invocation.id)
    assert out.status == "running"


class _SecondReadDropsInvocation(MemoryStore):
    """Fails the post-lease second read, simulating a concurrent delete."""

    def __init__(self) -> None:
        super().__init__()
        self._running_reads = 0

    def get_invocation(self, invocation_id: str) -> InvocationRecord | None:
        record = super().get_invocation(invocation_id)
        if record is not None and record.status == "running":
            self._running_reads += 1
            if self._running_reads == 2:
                return None
        return record


def test_reconcile_second_read_not_found_raises_and_releases_lease() -> None:
    store = _SecondReadDropsInvocation()
    registry = HarnessRegistry(store=store)
    app = seed_codex(registry)
    runtime = SessionRuntime(
        store=store,
        registry=registry,
        adapter=FakeAdapter(),
        event_log=EventLog(store=store),
    )
    store.put_session(SessionRecord(id="hs_2nd", appName=app.id, userId="u_1"))
    store.put_invocation(
        InvocationRecord(
            id="inv_2nd",
            sessionId="hs_2nd",
            appName=app.id,
            userId="u_1",
            turnId="turn_2nd",
            status="running",
        )
    )
    store.put_turn(TurnRecord(id="turn_2nd", invocationId="inv_2nd", sessionId="hs_2nd"))

    with pytest.raises(InvocationNotFoundError):
        runtime.reconcile_invocation_readback("hs_2nd", "inv_2nd")

    # finally-block released the lease; a fresh acquire must succeed.
    lease = store.acquire_lease((app.id, "u_1", "hs_2nd"), holder="aftermath")
    assert lease.token >= 1


def test_reconcile_missing_turn_raises_not_found(runtime: SessionRuntime) -> None:
    """Post-lease fence: a running invocation whose turn row vanished -> not found."""
    session = SessionRecord(id="hs_noturn", appName=APP, userId="u_1")
    runtime.store.put_session(session)
    runtime.store.put_invocation(
        InvocationRecord(
            id="inv_noturn",
            sessionId="hs_noturn",
            appName=APP,
            userId="u_1",
            turnId="turn_gone",
            status="running",
        )
    )
    with pytest.raises(InvocationNotFoundError):
        runtime.reconcile_invocation_readback("hs_noturn", "inv_noturn")


# --- _drive rejects a pending configuration update -----------------------


async def test_run_rejects_pending_configuration_update(
    runtime: SessionRuntime,
) -> None:
    session = SessionRecord(id="hs_pending", appName=APP, userId="u_1")
    session.desiredRevision = 2
    session.appliedRevision = 1
    session.pendingPolicyUpdate = {"revision": 2, "fields": ["network"], "requestedAtMs": 1}
    runtime.store.put_session(session)

    req = RunRequest(
        app=runtime.registry.resolve_default_app(Principal("p")),
        user_id="u_1",
        session_id="hs_pending",
        message={"role": "user", "parts": [{"text": "hi"}]},
    )
    with pytest.raises(SessionBusyError, match="configuration_update_pending"):
        await runtime.run(req)


# --- empty harness event stream -----------------------------------------


class _EmptyStreamAdapter(FakeAdapter):
    """Yields no harness events; the terminal must come from finalize_turn."""

    async def stream_events(self, handle: TurnHandle) -> AsyncIterator[HarnessEvent]:
        return
        yield  # pragma: no cover  # makes this an async generator


async def test_empty_event_stream_still_closes_via_finalize_terminal() -> None:
    runtime, app = _make_runtime(_EmptyStreamAdapter())
    result = await runtime.run(
        RunRequest(
            app=app,
            user_id="u_1",
            session_id="hs_empty",
            message={"role": "user", "parts": [{"text": "hi"}]},
        )
    )
    assert result.invocation.status == "completed"
    types = [event.type for event in result.events]
    assert "harness.text.delta" not in types
    assert result.events[-1].type == "haas.turn.completed"


# --- update_policy on a pausing session stays pending ------------------


_WS = {"mode": "workspace-write", "root": "/workspace", "writableRoots": ["/workspace"]}


def test_update_policy_pausing_session_marks_pending(runtime: SessionRuntime) -> None:
    session = SessionRecord(id="hs_pol", appName=APP, userId="u_1", controlState="pausing")
    runtime.store.put_session(session)
    out = runtime.update_policy(session, expected_revision=1, delta={"workspace": _WS})
    assert out.policyStatus == "pending"
    assert out.appliedRevision == 1
    assert out.pendingPolicyUpdate is not None


# --- artifact publication resilience ------------------------------------


class _ListingAdapter(FakeAdapter):
    def __init__(self, refs: list[ArtifactRef] | None = None) -> None:
        self._refs = refs or []

    async def list_artifacts(
        self, request: ListArtifactsRequest
    ) -> list[ArtifactRef]:
        return list(self._refs)


class _BoomListingAdapter(FakeAdapter):
    async def list_artifacts(
        self, request: ListArtifactsRequest
    ) -> list[ArtifactRef]:
        raise RuntimeError("artifact listing blew up")


async def test_artifact_non_bytes_content_is_skipped_but_turn_survives() -> None:
    adapter = _ListingAdapter(
        [ArtifactRef(name="note.txt", path="output/note.txt", content="not-bytes")]  # type: ignore[arg-type]
    )
    runtime, app = _make_runtime(adapter, with_artifacts=True)
    result = await runtime.run(
        RunRequest(
            app=app,
            user_id="u_1",
            session_id="hs_art_nonbytes",
            message={"role": "user", "parts": []},
            principal_id="p_1",
        )
    )
    assert result.invocation.status == "completed"
    assert not [e for e in result.events if e.type == "haas.artifact.registered"]
    assert runtime.artifacts is not None
    assert runtime.artifacts.list("hs_art_nonbytes", app_name=app.id, user_id="u_1") == []


async def test_artifact_traversal_path_rejected_but_turn_survives() -> None:
    adapter = _ListingAdapter(
        [ArtifactRef(name="evil", path="../../etc/passwd", content=b"root:x:0:0")]
    )
    runtime, app = _make_runtime(adapter, with_artifacts=True)
    result = await runtime.run(
        RunRequest(
            app=app,
            user_id="u_1",
            session_id="hs_art_traversal",
            message={"role": "user", "parts": []},
            principal_id="p_1",
        )
    )
    assert result.invocation.status == "completed"
    assert not [e for e in result.events if e.type == "haas.artifact.registered"]


async def test_artifact_duplicate_same_content_is_registered_once() -> None:
    ref = ArtifactRef(
        name="report.md", path="output/report.md", content=b"# Report", mediaType="text/markdown"
    )
    adapter = _ListingAdapter([ref, ref])
    runtime, app = _make_runtime(adapter, with_artifacts=True)
    result = await runtime.run(
        RunRequest(
            app=app,
            user_id="u_1",
            session_id="hs_art_dup",
            message={"role": "user", "parts": []},
            principal_id="p_1",
        )
    )
    assert result.invocation.status == "completed"
    registered = [e for e in result.events if e.type == "haas.artifact.registered"]
    assert len(registered) == 1
    assert result.events[-1].type == "haas.turn.completed"


async def test_artifact_list_failure_is_best_effort() -> None:
    runtime, app = _make_runtime(_BoomListingAdapter(), with_artifacts=True)
    result = await runtime.run(
        RunRequest(
            app=app,
            user_id="u_1",
            session_id="hs_art_boom",
            message={"role": "user", "parts": []},
            principal_id="p_1",
        )
    )
    assert result.invocation.status == "completed"
    assert not [e for e in result.events if e.type == "haas.artifact.registered"]
    assert result.events[-1].type == "haas.turn.completed"


async def test_artifact_valid_bytes_is_registered_and_readable() -> None:
    adapter = _ListingAdapter(
        [ArtifactRef(name="report.md", path="output/report.md", content=b"# Report", mediaType="text/markdown")]
    )
    runtime, app = _make_runtime(adapter, with_artifacts=True)
    result = await runtime.run(
        RunRequest(
            app=app,
            user_id="u_1",
            session_id="hs_art_ok",
            message={"role": "user", "parts": []},
            principal_id="p_1",
        )
    )
    assert result.invocation.status == "completed"
    registered = [e for e in result.events if e.type == "haas.artifact.registered"]
    assert len(registered) == 1
    assert runtime.artifacts is not None
    records = runtime.artifacts.list(
        "hs_art_ok", app_name=app.id, user_id="u_1", owner_principal_id="p_1"
    )
    assert len(records) == 1
    assert records[0].relativePath == "output/report.md"
    assert records[0].bytes == len(b"# Report")
    assert (
        runtime.artifacts.read_content(records[0].id, owner_principal_id="p_1")
        == b"# Report"
    )
