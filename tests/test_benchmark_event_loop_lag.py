"""M-01 benchmark: measure whether synchronous SQLite writes on the async
event loop produce measurable / blocking latency during /run_sse streaming.

NOT COMMITTED. Temporary Phase-0 measurement artifact.

Method:
- Build the real FastAPI app backed by a real on-disk SQLiteStore (WAL).
- Subclass FakeAdapter to emit N_EVENTS text-delta events per stream (each
  carries a stateDelta so the runtime exercises put_session + event_log.append
  per event, exactly the hot path called inline from _drive on the loop thread).
- Wrap store.put_session / put_turn / put_invocation / append / assert_lease
  bound methods; record loop.time() delta around each original call. Because
  these run synchronously on the single asyncio event-loop thread, the delta
  IS the amount of time the loop was blocked inside the store write.
- Fire N_CONCURRENCY concurrent POST /run_sse streams via httpx ASGITransport
  (same event loop, like a real sidecar) and drain them.

Decision threshold (from M-01): P99 of per-call block time > 50 ms => go
(offload to thread pool); <= 50 ms => no-go (record as not_issue).
"""

from __future__ import annotations

import asyncio
import statistics
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import httpx
import pytest

from haas.api import build_app
from haas.harnesses.base import HarnessEvent, TurnHandle
from haas.harnesses.fake import FakeAdapter
from haas.identity import Principal
from haas.stores.sqlite import SQLiteStore

TOKEN = "bench-token"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}

N_EVENTS = 1_200  # per stream; >= 1000 required by M-01
N_CONCURRENCY = 5
WRITE_METHODS = ("put_session", "put_turn", "put_invocation", "append", "assert_lease")


class HighVolumeAdapter(FakeAdapter):
    """Emits N_EVENTS text deltas (each with a stateDelta) then completes."""

    async def stream_events(self, handle: TurnHandle) -> AsyncIterator[HarnessEvent]:
        for i in range(N_EVENTS):
            yield HarnessEvent(
                type="harness.text.delta",
                nativeType="fake/text",
                invocationId=handle.invocationId,
                sessionId=handle.sessionId,
                turnId=handle.turnId,
                author=self.base,
                content={"role": "model", "parts": [{"text": f"tok{i}"}]},
                # stateDelta forces _merge_actions -> store.put_session every event.
                actions={"stateDelta": {"last_text": f"tok{i}", "seq": i}},
            )
            # Yield so the 5 concurrent streams interleave on the shared loop,
            # mirroring real concurrent SSE consumption.
            await asyncio.sleep(0)


@dataclass
class BlockRecorder:
    samples: dict[str, list[float]] = field(default_factory=dict)

    def record(self, name: str, dt_s: float) -> None:
        self.samples.setdefault(name, []).append(dt_s * 1000.0)  # ms


def _wrap(store: SQLiteStore, name: str, rec: BlockRecorder) -> None:
    original = getattr(store, name)

    def wrapped(*args: object, **kwargs: object) -> object:
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        try:
            return original(*args, **kwargs)
        finally:
            rec.record(name, loop.time() - t0)

    setattr(store, name, wrapped)


def _pct(sorted_values: list[float], q: float) -> float:
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return sorted_values[0]
    k = (len(sorted_values) - 1) * q
    lo = int(k)
    hi = min(lo + 1, len(sorted_values) - 1)
    frac = k - lo
    return sorted_values[lo] * (1 - frac) + sorted_values[hi] * frac


@pytest.mark.asyncio
async def test_m01_event_loop_sqlite_write_lag(tmp_path, monkeypatch) -> None:
    # The SSE bounded queue (256) disconnects slow consumers under 5x1200
    # concurrent load; this benchmark measures store-write latency, not SSE
    # delivery, so raise the queue cap to avoid backpressure truncation.
    # SSE_QUEUE_MAXSIZE is evaluated at import time, so patch the constant
    # directly rather than the env var.
    import haas.api as _haas_api
    monkeypatch.setattr(_haas_api, "SSE_QUEUE_MAXSIZE", 100_000)
    db_path = tmp_path / "m01_bench.db"
    store = SQLiteStore(db_path)
    app = build_app(
        adapter=HighVolumeAdapter(),
        identity_tokens={
            TOKEN: Principal(
                principalId="p_1", tenantId="t1", userIds=frozenset({"u_1"})
            )
        },
        store=store,
        run_quota=100,
        rate_limit=1000,
    )

    rec = BlockRecorder()
    for method in WRITE_METHODS:
        _wrap(store, method, rec)

    async def one_stream(index: int) -> int:
        body = {
            "appName": "chrn_codex_default",
            "userId": "u_1",
            "sessionId": f"hsess_m01_{index}",
            "newMessage": {"role": "user", "parts": [{"text": "go"}]},
        }
        n_lines = 0
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://bench"
        ) as client:
            async with client.stream("POST", "/run_sse", json=body, headers=HEADERS) as resp:
                assert resp.status_code == 200, resp.text
                async for line in resp.aiter_lines():
                    if line.startswith("data: "):
                        n_lines += 1
        return n_lines

    wall0 = asyncio.get_running_loop().time()
    per_stream = await asyncio.gather(*(one_stream(i) for i in range(N_CONCURRENCY)))
    wall = asyncio.get_running_loop().time() - wall0
    store.close()

    total_events = sum(per_stream)
    print(f"\n=== M-01 event-loop SQLite write block measurement ===")
    print(f"streams={N_CONCURRENCY} events_per_stream={N_EVENTS} "
          f"data_frames={total_events} wall_s={wall:.2f}")

    all_writes: list[float] = []
    for method in WRITE_METHODS:
        vals = sorted(rec.samples.get(method, []))
        if not vals:
            print(f"  {method:16s} n=0")
            continue
        all_writes.extend(vals)
        print(
            f"  {method:16s} n={len(vals):6d} "
            f"P50={_pct(vals,0.50):7.3f}ms "
            f"P95={_pct(vals,0.95):7.3f}ms "
            f"P99={_pct(vals,0.99):8.3f}ms "
            f"max={vals[-1]:8.3f}ms mean={statistics.mean(vals):.3f}ms"
        )

    all_sorted = sorted(all_writes)
    p50 = _pct(all_sorted, 0.50)
    p95 = _pct(all_sorted, 0.95)
    p99 = _pct(all_sorted, 0.99)
    print(f"  {'ALL WRITES':16s} n={len(all_sorted):6d} "
          f"P50={p50:7.3f}ms P95={p95:7.3f}ms P99={p99:8.3f}ms "
          f"max={all_sorted[-1]:.3f}ms")
    verdict = "GO (offload to thread pool)" if p99 > 50.0 else "NO-GO (not_issue)"
    print(f"  THRESHOLD: P99 > 50ms -> go ; P99 = {p99:.3f}ms => {verdict}")

    # Sanity: we actually exercised the write hot path.
    assert total_events >= N_CONCURRENCY * N_EVENTS, per_stream
    assert len(rec.samples.get("append", [])) > N_CONCURRENCY * N_EVENTS
    assert len(rec.samples.get("put_session", [])) > N_CONCURRENCY * N_EVENTS
