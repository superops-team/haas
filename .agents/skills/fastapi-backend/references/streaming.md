# Streaming: SSE and streaming responses

Reference for writing streaming endpoints in the HaaS sidecar. Read this before
touching `POST /run_sse`, replay streams, or any `text/event-stream` route.

## Authority and alignment

- `specs/event-log-sse/README.md` is the component contract: event log is the
  source of truth, SSE is only a delivery channel.
- `specs/haas-protocol/README.md` + `haas-2026-09-10.openapi.yaml` define the
  northbound SSE wire format.
- AGENTS.md: events are facts (stable type, monotonic sequence, terminal event,
  redacted payload); heartbeat uses an SSE comment and produces no event;
  invocation completion closes the stream; `POST /run` and `POST /run_sse` must
  emit parity output.

## Recommended pattern: `EventSourceResponse` + `ServerSentEvent`

`sse-starlette`'s `EventSourceResponse` is the canonical way to emit SSE frames.
Use it for `POST /run_sse` so each canonical event becomes one well-formed frame.

```python
from collections.abc import AsyncIterator
from sse_starlette import ServerSentEvent, EventSourceResponse
from fastapi import APIRouter

router = APIRouter()

async def event_frames(
    runtime: RuntimeDep, *, after_event_id: str | None
) -> AsyncIterator[ServerSentEvent]:
    # Replay missed events first (Last-Event-ID / after_event_id), then live.
    async for ev in runtime.stream_events(session_id, after=after_event_id):
        yield ServerSentEvent(
            event=ev.type,            # stable event type, e.g. "on_event"
            data=ev.model_dump_json(),  # redacted canonical ADK Event payload
            id=str(ev.sequence_number),  # monotonic, lets client resume
            retry=15000,               # client reconnection hint, ms
        )

@router.post("/run_sse")
async def run_sse(...) -> EventSourceResponse:
    return EventSourceResponse(event_frames(runtime, after_event_id=None))
```

### `ServerSentEvent` fields

| Field | Meaning |
|---|---|
| `event` | Event type name. Stable, machine-readable; never a human sentence. |
| `data` | Frame payload as a single string (JSON-encoded). One logical event per frame. |
| `id` | Monotonic event id / sequence number. Lets the client resume via `Last-Event-ID`. |
| `retry` | Millisecond hint for when the client should reconnect after a drop. |
| `comment` | An SSE comment line (`: ...`). Used for heartbeats — it produces NO event and does NOT advance the cursor. |
| `raw_data` | Pre-formatted frame bytes when you must bypass JSON encoding (rare; prefer `data`). |

## Heartbeats

Idle streams must send heartbeats so proxies do not close the connection, but a
heartbeat MUST NOT look like an event and MUST NOT advance the sequence:

```python
async def heartbeat() -> ServerSentEvent:
    return ServerSentEvent(comment="hb")  # emitted as ": hb\n\n"
```

Combine replay-then-live in one generator: when the live queue is empty for the
interval, yield a `comment` heartbeat; otherwise yield the next event frame.

## When to close the stream

- Close the stream as soon as the invocation reaches a terminal state
  (`completed` / `failed` / `cancelled`). The terminal event is the last frame.
- Closing the stream does NOT cancel the underlying task. The event log remains
  the source of truth; a disconnected client replays from `Last-Event-ID`.
- If a subscriber cannot keep up on a bounded queue, disconnect it with the last
  successfully delivered event id rather than dropping events silently.

## `StreamingResponse` for bytes / JSON Lines

When you are not emitting named SSE events (binary artifact downloads,
JSON-lines logs, model-proxy relay), use the lower-level
`fastapi.responses.StreamingResponse`:

```python
from fastapi.responses import StreamingResponse

@router.get("/v1/haas/artifacts/{aid}/download")
def download_artifact(aid: str, runtime: RuntimeDep) -> StreamingResponse:
    def chunker() -> Iterator[bytes]:
        for chunk in runtime.read_artifact_chunks(aid):
            yield chunk
    return StreamingResponse(chunker(), media_type="application/octet-stream")
```

- Set `X-Content-Type-Options: nosniff` on artifact downloads and guard against
  path traversal (AGENTS.md security section).
- The model-proxy loopback relays upstream `text/event-stream` bytes verbatim;
  it must not buffer the whole stream.

## Parity: `POST /run` vs `POST /run_sse`

`POST /run` collects all events and returns them as a JSON array; `POST /run_sse`
streams the same events frame-by-frame. The aggregated `/run` output MUST equal
the `/run_sse` output (same event types, same order, same terminal state). Build
both on the same event source; do not maintain two divergent code paths.

```python
# Shared source; two deliveries.
async def collect(runtime, request) -> list[CanonicalEvent]:
    return [ev async for ev in run_and_collect(runtime, request)]

@app.post("/run")
def run_sync(...) -> list[Event]:
    return collect(...)                      # buffered JSON array

@app.post("/run_sse")
def run_stream(...) -> EventSourceResponse:
    return EventSourceResponse(to_frames(run_and_stream(...)))  # frames
```

## Safety

- All streamed payloads are redacted before they reach the wire: no raw
  prompts, no full tool arguments, no credentials, no provider tokens, no
  presigned URLs. Add a reverse test that asserts a redacted field never appears.
- Code samples above use placeholders (`RuntimeDep`, `CanonicalEvent`,
  `session_id`). Do not copy real tokens, keys, or endpoint URLs into code or
  tests; fixtures must use dummy values and may carry `# haas-secret-ignore`
  only when reviewed.
