---
name: fastapi-backend
description: FastAPI backend conventions for the HaaS sidecar (haas/ and manager/coworker/server). Use when adding or changing an HTTP/SSE route, a dependency, a router, a response model, or a DB/connection lifecycle in the Python sidecar. Triggers on "FastAPI", "Depends", "Annotated", "router", "response_model", "yield dependency", "async def vs def", "run_sse", "POST /run", or any new northbound or /v1/haas/* endpoint. Covers DI, async/sync choice, sensitive-field filtering, and router layout. Does NOT choose which verification command to run (code-automation) and does NOT drive a bug investigation workflow (haas-debug-workflow).
---

# FastAPI Backend

Conventions for writing HTTP/SSE endpoints in the HaaS sidecar. This is the
Python API layer that sits between clients and the session/adapter runtime.

## Authority

- Root `AGENTS.md` is authoritative: protocol-first, secretless, adapter isolation,
  events-as-facts, and "失败必须可恢复或可解释".
- Northbound contract: `specs/haas-protocol/README.md` +
  `specs/haas-protocol/haas-2026-09-10.openapi.yaml`. Additive changes only;
  any field/event/error/semantic change updates that spec first.
- SSE delivery semantics live in `specs/event-log-sse/README.md` and in
  `references/streaming.md` alongside this skill.
- This skill does not pick test/verification commands for you — see
  `code-automation` for which gate to run. It does not debug a live bug — see
  `haas-debug-workflow`.

## 1. Dependency injection: `Annotated[..., Depends(...)]`

Use `Annotated` aliases, not raw `Depends(...)` repeated in every signature.
This keeps signatures short, makes dependencies self-documenting, and lets you
swap implementations in one place.

```python
from typing import Annotated
from fastapi import Depends, FastAPI, Request

app = FastAPI()

# One type alias per injectable resource. Reuse everywhere.
def get_runtime(request: Request) -> Runtime:
    return request.app.state.runtime

RuntimeDep = Annotated[Runtime, Depends(get_runtime)]
SessionDep = Annotated[SessionService, Depends(get_sessions)]

@app.post("/run")
async def run(runtime: RuntimeDep, sessions: SessionDep, payload: RunRequest):
    ...
```

Rules:

- Define the alias once near the router or in `haas/api.py` and import it. Do
  not inline `Depends(get_runtime)` in route bodies.
- Read app state (runtime, event log, policy controller, stores) from
  `request.app.state` via a `get_*` dependency — never reach for a module-level
  global that hides lifecycle.
- Router-level dependencies (`dependencies=[Depends(...)]`) are for concerns
  that apply to *every* route in the router (auth, idempotency-key parsing,
  request-id), not for values individual handlers need.

### `yield` dependencies for resource cleanup

Use a generator dependency (`yield`) for resources that must be torn down after
the request: DB sessions, connection-patient checkouts, transaction scopes.

```python
from collections.abc import Iterator
from typing import Annotated
from fastapi import Depends

def get_db() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()

DbDep = Annotated[Session, Depends(get_db)]
```

- The code after `yield` ALWAYS runs, even on exception — use it for close/rollback.
- For transactions: commit on the happy path, roll back on exception, then close.
- Do not put business logic in a `yield` dependency; it is lifecycle, not a use case.

## 2. async vs sync

Default to **plain `def`** route handlers. FastAPI runs sync handlers in a
threadpool, which is safe for blocking work. Only use `async def` when you have
an actual async library in the call path.

| Choice | When |
|---|---|
| `def handler()` | Default. The handler or the underlying lib (DB driver, HTTP client, file IO) blocks. Runs in the threadpool. |
| `async def handler()` | Only when the whole call chain is non-blocking async (e.g. streaming SSE generators, async drivers, `await`-ing real async I/O). |

Hard rules:

- **Never run blocking code inside an `async def`.** A `time.sleep()`, a sync DB
  query, a sync HTTP call, or a CPU-heavy loop in an `async` handler blocks the
  whole event loop and stalls every concurrent SSE stream.
- If you must call a blocking library from an async handler, offload it — e.g.
  Asyncer `asyncify()` / `syncify()` — rather than awaiting it inline. When in
  doubt, make the route a plain `def` and let FastAPI move it to the threadpool.
- SSE/streaming endpoints that `async for` over a generator SHOULD be `async def`
  (see `references/streaming.md`); everything non-streaming is usually plain `def`.

## 3. `response_model` and sensitive-field filtering

`response_model` is a security control, not just documentation. It filters the
output so internal fields never leak to the northbound client — this is how HaaS
honors secretless at the API boundary.

```python
class SessionInternal(BaseModel):
    session_id: str
    # internal-only fields the caller must never see:
    db_row_id: int
    raw_provider_ref: str
    admin_note: str

class SessionView(BaseModel):
    session_id: str

@app.get("/sessions/{sid}", response_model=SessionView)
def get_session(...) -> SessionInternal:
    return repo.load(sid)  # extra fields are stripped by response_model
```

Rules:

- Always declare `response_model` on routes that return an internal/ORM-ish
  object. Do not return the internal model directly.
- Never put credentials, provider tokens, raw prompts, full tool arguments,
  presigned URLs, or cookie values in a response model field that is exposed.
  If a debug field is added, it must be gated behind an explicit opt-in and
  redaction test (see `trace_content` style switches in AGENTS.md).
- Error responses use structured schemas (`specs/haas-protocol/ERROR-CODES.md`),
  not human-readable strings the caller must parse.

## 4. Router organization

Group routes into `APIRouter` instances with router-level `prefix`, `tags`, and
shared `dependencies`. Keep `haas/api.py` as the composition root that includes
routers; do not grow one giant route file.

```python
from fastapi import APIRouter, Depends

sessions_router = APIRouter(
    prefix="/apps/{app_name}/users/{user_id}/sessions",
    tags=["sessions"],
    dependencies=[Depends(parse_idempotency_key)],
)

@sessions_router.post("/{session_id}/run", response_model=RunResult)
def run_session(...):
    ...

# In the app composition root:
# app.include_router(sessions_router)
# app.include_router(haas_admin_router, prefix="/v1/haas")
```

Rules:

- ADK-compatible routes live under the ADK paths (`/run`, `/run_sse`,
  `/apps/{app}/users/{user}/sessions/{sid}`). HaaS-native runtime/diagnostic
  routes live under `/v1/haas/*`. Do not invent a third namespace.
- Every mutating route accepts an optional `Idempotency-Key` header; repeated
  keys return the first result (see haas-protocol spec).
- `GET /health` = process liveness; `GET /ready` = can accept new sessions/turns.
  Keep these semantics consistent across both image variants.

## 5. HaaS commands

These are the project's verification entry points. Pick the smallest one that
covers your change; escalate only when cross-component risk is high.

| Command | Use |
|---|---|
| `uv run --extra dev pytest tests/ -q` | Fast, scoped unit/integration tests after an API change. |
| `uv run --extra dev mypy haas` | Type-check the sidecar package after editing routes/models. |
| `make test-integration` | API/SSE/session/adapter integration suite for cross-component changes. |

Run the narrowest relevant test first; do not default to the full suite.
If a command cannot run in the current environment, record `not_run` with the
reason instead of claiming a pass.

## Scope boundaries

- **Not `code-automation`**: this skill tells you the API-layer patterns;
  `code-automation` owns which verification/gate command to select for a change.
- **Not `haas-debug-workflow`**: this skill writes endpoints; that skill drives
  a reproduce-before-fix bug investigation across runtimes.
- **Not `pydantic-modeling`**: request/response *model* design (Field metadata,
  union traps, UTC datetimes) lives in `pydantic-modeling`.
- SSE frame construction, heartbeats, and stream-close semantics: read
  `references/streaming.md` before writing a streaming endpoint.
