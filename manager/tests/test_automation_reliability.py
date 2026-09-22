"""Offline automation regressions: real stores, injected execution boundary."""

import asyncio
import time
from types import SimpleNamespace

import pytest
from coworker.automation import Schedule, ScheduledTask, Scheduler, TaskRun, TaskStore
from coworker.events import Event, EventType
from coworker.server.manager import SessionManager


@pytest.mark.parametrize(
    "events,status",
    [
        ([Event(EventType.ERROR, {"error": "synthetic failure"})], "error"),
        ([], "error"),
        ([Event(EventType.TURN_END, {"status": "completed"})], "ok"),
        ([Event(EventType.TURN_END, {"status": "failed"})], "error"),
    ],
)
async def test_scheduled_execution_routes_and_requires_terminal(
    tmp_path, monkeypatch, events, status
):
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    manager = SessionManager(data_dir=tmp_path / "data")
    engine = SimpleNamespace(messages=[], model="test", request_interrupt=lambda: None)
    task = ScheduledTask(
        title="Fixture",
        instructions="fixture",
        workspace=str(tmp_path),
        schedule=Schedule(kind="cron", cron="* * * * *"),
        agent="cowork",
    )
    manager.task_store.save(task)
    monkeypatch.setattr(manager, "_build_task_engine", lambda *a, **k: engine)
    monkeypatch.setattr(manager, "save", lambda *a, **k: None)
    seen = []

    async def routed(sid, eng, content, **kwargs):
        seen.append(kwargs)
        for event in events:
            yield event

    monkeypatch.setattr(manager, "run_turn_events", routed)
    run = await manager._run_scheduled_task(task, "schedule")
    assert seen and seen[0]["agent"] == "cowork"
    assert run.status == status
    assert manager.task_store.find_run(run.run_id).status == status
    assert manager.inbox.pending(run.session_id)
    assert not manager.is_running(run.session_id)


async def test_scheduler_bounds_active_runs(tmp_path):
    store = TaskStore(tmp_path / "tasks.db")
    for n in range(7):
        task = ScheduledTask(
            title=str(n),
            instructions="fixture",
            workspace=str(tmp_path),
            schedule=Schedule(kind="cron", cron="* * * * *"),
        )
        store.save(task)
    tasks = store.list()
    store.due = lambda: tasks
    gate = asyncio.Event()
    active = []

    async def runner(task, trigger):
        active.append(task.id)
        await gate.wait()
        return TaskRun(task_id=task.id, status="ok")

    scheduler = Scheduler(store, runner, max_concurrent_runs=2)
    await scheduler._tick(trigger="schedule")
    await asyncio.sleep(0)
    assert len(active) == 2
    gate.set()
    await asyncio.gather(*scheduler._spawned)
    store.close()


def test_startup_recovers_unknown_runs_without_replay(tmp_path):
    store = TaskStore(tmp_path / "tasks.db")
    task = ScheduledTask(
        title="Fixture",
        instructions="fixture",
        workspace=str(tmp_path),
        schedule=Schedule(kind="cron", cron="* * * * *"),
    )
    store.save(task)
    run = TaskRun(task_id=task.id)
    store.add_run(run)
    recovered = store.recover_unfinished_runs()
    assert recovered[0].session_id == run.session_id
    assert store.find_run(run.run_id).status == "error"
    assert not store.get(task.id).enabled
    assert store.due(now=time.time() + 3600) == []
    assert store.recover_unfinished_runs() == []
    store.close()


async def test_scheduler_consumes_occurrence_before_execution(tmp_path):
    store = TaskStore(tmp_path / "tasks.db")
    task = ScheduledTask(
        title="Fixture",
        instructions="fixture",
        workspace=str(tmp_path),
        schedule=Schedule(kind="cron", cron="* * * * *"),
    )
    store.save(task)

    async def runner(task, trigger):
        assert store.get(task.id).run_count == 1
        return TaskRun(task_id=task.id, status="ok")

    scheduler = Scheduler(store, runner)
    await scheduler.run_task(task, trigger="schedule")
    assert store.get(task.id).run_count == 1
    store.close()


async def test_notification_failure_does_not_reclassify_success(tmp_path, monkeypatch):
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    manager = SessionManager(data_dir=tmp_path / "data")
    engine = SimpleNamespace(messages=[], model="test", request_interrupt=lambda: None)
    task = ScheduledTask(
        title="Fixture",
        instructions="fixture",
        workspace=str(tmp_path),
        schedule=Schedule(kind="cron", cron="* * * * *"),
    )
    manager.task_store.save(task)
    monkeypatch.setattr(manager, "_build_task_engine", lambda *a, **k: engine)
    monkeypatch.setattr(manager, "save", lambda *a, **k: None)

    async def routed(*args, **kwargs):
        yield Event(EventType.TASK_STATE, {"phase": "verifying"})
        yield Event(EventType.TURN_END, {"status": "completed", "taskPhase": "completed"})

    async def failed_notification(*args):
        raise OSError("offline")

    monkeypatch.setattr(manager, "run_turn_events", routed)
    monkeypatch.setattr(manager, "_notify_task_done", failed_notification)
    run = await manager._run_scheduled_task(task, "schedule")
    assert run.status == "ok"
    assert manager.task_store.find_run(run.run_id).status == "ok"


async def test_notification_is_durable_and_deduplicated(tmp_path, monkeypatch):
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    manager = SessionManager(data_dir=tmp_path / "data")
    task = ScheduledTask(
        title="Fixture",
        instructions="fixture",
        workspace=str(tmp_path),
        schedule=Schedule(kind="cron", cron="* * * * *"),
    )
    run = TaskRun(task_id=task.id, status="error")
    await manager._notify_task_done(task, run)
    await manager._notify_task_done(task, run)
    assert len(manager.inbox.pending(run.session_id)) == 1
    from coworker.inbox import InboxStore

    reloaded = InboxStore(manager.inbox.path)
    assert len(reloaded.pending(run.session_id)) == 1


async def test_eof_freezes_schedule_and_notification_input_does_not_hide_result(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    manager = SessionManager(data_dir=tmp_path / "data")
    engine = SimpleNamespace(messages=[], model="test", request_interrupt=lambda: None)
    task = ScheduledTask(
        title="Fixture",
        instructions="fixture",
        workspace=str(tmp_path),
        schedule=Schedule(kind="cron", cron="* * * * *"),
    )
    manager.task_store.save(task)
    monkeypatch.setattr(manager, "_build_task_engine", lambda *a, **k: engine)
    monkeypatch.setattr(manager, "save", lambda *a, **k: None)

    async def routed(*args, **kwargs):
        yield Event(EventType.PERMISSION_REQUIRED, {"approvalId": "approval-fixture"})

    monkeypatch.setattr(manager, "run_turn_events", routed)
    run = await manager._run_scheduled_task(task, "schedule")
    assert run.status == "error"
    assert not manager.task_store.get(task.id).enabled
    notices = manager.inbox.pending(run.session_id)
    assert len(notices) == 2
    assert any(item.tool_call_id == f"automation-result:{run.run_id}" for item in notices)


def test_manual_ws_run_finishes_without_browser_finalize(tmp_path, monkeypatch):
    from coworker.providers import AssistantTurn, ModelCapabilities, ProviderClient
    from coworker.server import create_app
    from fastapi.testclient import TestClient

    class Provider(ProviderClient):
        def complete(self, **kwargs):
            return AssistantTurn(text="Fixture result", finish_reason="stop")

        def capabilities(self, model):
            return ModelCapabilities()

    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    manager = SessionManager(data_dir=tmp_path / "data", provider=Provider())
    task = ScheduledTask(
        title="Fixture",
        instructions="fixture",
        workspace=str(tmp_path),
        schedule=Schedule(kind="cron", cron="* * * * *"),
        agent="cowork",
    )
    manager.task_store.save(task)
    prepared = manager.prepare_manual_run(task.id)
    client = TestClient(create_app(manager))
    with client.websocket_connect(
        f"/ws/session/{prepared['session_id']}?workspace={tmp_path}&agent=cowork"
    ) as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"type": "user_message", "text": prepared["prompt"]})
        while ws.receive_json()["type"] != "turn_done":
            pass
    assert manager.task_store.find_run(prepared["run_id"]).status == "ok"
    assert manager.task_store.get(task.id).run_count == 1
    manager.finalize_manual_run(task.id, prepared["run_id"])
    assert manager.task_store.get(task.id).run_count == 1


def test_inbox_save_failure_preserves_previous_file(tmp_path, monkeypatch):
    from coworker.inbox import InboxStore
    import coworker.inbox as inbox_module

    path = tmp_path / "inbox.json"
    store = InboxStore(path)
    store.add_notification("s1", "First")
    before = path.read_bytes()

    def fail_replace(*args):
        raise OSError("synthetic disk failure")

    monkeypatch.setattr(inbox_module.os, "replace", fail_replace)
    with pytest.raises(OSError):
        store.add_notification("s2", "Second")
    assert len(store.list()) == 1
    assert path.read_bytes() == before
    assert not list(tmp_path.glob(".inbox-*"))


def test_manual_run_overlap_and_early_finalize_keep_original_attempt(tmp_path, monkeypatch):
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    manager = SessionManager(data_dir=tmp_path / "data")
    task = ScheduledTask(
        title="Fixture",
        instructions="fixture",
        workspace=str(tmp_path),
        schedule=Schedule(kind="cron", cron="* * * * *"),
    )
    manager.task_store.save(task)
    first = manager.prepare_manual_run(task.id)
    second = manager.prepare_manual_run(task.id)
    assert first["ok"] and not second["ok"]
    assert not manager.scheduler._claim(task.id)
    manager.try_mark_running(first["session_id"])
    result = manager.finalize_manual_run(task.id, first["run_id"])
    assert result["run"]["status"] == "running"
    assert manager.task_store.get(task.id).run_count == 0


async def test_timeout_freezes_schedule_and_releases_occupancy(tmp_path, monkeypatch):
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("COWORKER_AUTOMATION_TIMEOUT_SECONDS", "0.02")
    manager = SessionManager(data_dir=tmp_path / "data")
    engine = SimpleNamespace(messages=[], model="test", request_interrupt=lambda: None)
    task = ScheduledTask(
        title="Fixture",
        instructions="fixture",
        workspace=str(tmp_path),
        schedule=Schedule(kind="cron", cron="* * * * *"),
    )
    manager.task_store.save(task)
    monkeypatch.setattr(manager, "_build_task_engine", lambda *a, **k: engine)
    monkeypatch.setattr(manager, "save", lambda *a, **k: None)

    async def routed(*args, **kwargs):
        yield Event(EventType.TURN_START, {})
        await asyncio.Event().wait()

    monkeypatch.setattr(manager, "run_turn_events", routed)
    run = await manager._run_scheduled_task(task, "schedule")
    assert run.status == "error"
    assert not manager.is_running(run.session_id)
    assert not manager.task_store.get(task.id).enabled
    assert manager.inbox.pending(run.session_id)
