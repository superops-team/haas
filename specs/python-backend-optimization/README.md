# Python Backend Optimization Specification

**English**

Status: Approved; ready for implementation
Last reviewed: 2026-09-24 (revision 2)
Change ID: python-backend-optimization-2026q3
Related specs: [haas-protocol](../haas-protocol/), [event-log-sse](../event-log-sse/), [session-runtime](../session-runtime/), [harness-adapter](../harness-adapter/), [codex-app-server-adapter](../codex-app-server-adapter/), [model-proxy](../model-proxy/), [security-boundary](../security-boundary/), [observability](../observability/), [stores](../stores/), [artifact-store](../artifact-store/), [policy-controller](../policy-controller/)

## 1. Background and Evidence Baseline

This spec consolidates findings from an end-to-end read-only review of the HaaS Python sidecar (`haas/**`), manager coworker (`manager/coworker/**`), and tests (`tests/**`, `manager/tests/**`), conducted 2026-09-24 against HEAD `8fdd415`. The review applied four repository skills: `fastapi-backend`, `pydantic-modeling`, `adapter-extension`, `parallel-code-review`.

### 1.1 Static tool evidence (re-run 2026-09-24, revision 2)

- `uv run --extra dev ruff check haas/`: **All checks passed** (0 issues)
- `uv run --extra dev mypy haas`: **Success: no issues found in 55 source files**
- `git diff --check`: **passed** (no whitespace errors)
- `make pre-commit`: **passed** (ruff whitespace + secret-scan, no findings)

These are documentation/spec-only changes; the runtime code has not been modified since the baseline, so static tool results are inherited evidence and remain valid.

### 1.2 Review coverage

~65 Python files in `haas/` (~14,700 lines) plus key manager/coworker files (`server/`, `haas/` bridge, `sessions.py`, `memory/`), across four balanced shards.

### 1.3 Classification taxonomy

- **Confirmed defect**: code path produces incorrect behavior, data loss, or security exposure; root cause and file:line confirmed by static reading.
- **Confirmed structural risk**: code is correct today but the architecture creates a known failure mode under measurable conditions (concurrency, large inputs, long uptime); root cause confirmed, impact conditional.
- **Hypothesis (Phase 0)**: a plausible issue that cannot be confirmed from static reading alone; requires benchmark, telemetry, or reproduction before any refactor is committed. These enter only measurement tasks, not implementation.
- **False positive**: a reported finding that, upon independent re-verification against existing specs or code, is not an actual issue. Recorded for auditability but excluded from actionable counts.

### 1.4 Severity and priority

- **P0**: Affects correctness, SSE/async concurrency, secretless, recovery semantics, or high-frequency performance paths. Must be fixed before next release.
- **P1**: Structural maintainability, medium-risk robustness, or observability gaps. Should be fixed in the next 1–2 iterations.
- **P2**: Minor improvements, nits, or hygiene. Backlog; fix opportunistically.

---

## 2. Findings Summary and Deduplication

### 2.1 Raw findings by shard

| Shard | Scope | Findings | Major | Minor | Nit |
|---|---|---:|---:|---:|---:|
| S1 | API/SSE + Manager sidecar | 12 | 4 | 5 | 3 |
| S2 | sessions/events/stores/artifacts | 14 | 5 | 5 | 4 |
| S3 | harness adapter/runtime/MCP/policy | 13 | 6 | 6 | 1 |
| S4 | model proxy/security/observability/config | 10 | 1 | 5 | 4 |
| **Raw total** | | **49** | **16** | **21** | **12** |

### 2.2 Deduplicated cross-shard overlaps

| Merged ID | Raw IDs | Reason |
|---|---|---|
| O-ASYNC-1 | S1-010 (minor), S2-007 (major) | Both identify blocking SQLite I/O in async event loop |
| O-SSRF-1 | S3-009 (minor), S4-003 (minor) | Both identify no DNS resolution in network policy check |

After dedup: **47 distinct finding entries**, including the later-confirmed false positive S2-009 and the positive/closed item S4-010.

### 2.3 False positives (excluded from actionable counts)

| ID | Original claim | Resolution |
|---|---|---|
| S2-009 | "sequenceNumber not globally monotonic; spec must clarify scope" | **False positive.** `specs/event-log-sse/README.md:38` already defines: "Maintain a gapless, zero-based `sequenceNumber` for each invocation and a separate gapless session-lifecycle sequence for events outside a turn." Lines 131–136 further clarify invocation-scoped vs session-lifecycle-scoped `sequenceNumber`, with session-scoped `eventId` as the replay cursor across both namespaces. The implementation (`haas/events.py:131`, `sequenceNumber = len(existing)`) correctly follows this per-invocation contract. No spec delta or code change needed. |

After false-positive removal: **46 distinct entries**, of which **45 are actionable** (15 major, 18 minor, 12 nit) and 1 is positive/closed (S4-010).

### 2.4 Actionable findings by priority

| Priority | Count | Major | Minor | Nit |
|---|---:|---:|---:|---:|
| P0 (confirmed defects) | 12 | 7 | 5 | 0 |
| P0 (Phase 0 gated) | 1 | 1 | 0 | 0 |
| P1 (structural risks) | 15 | 8 | 5 | 2 |
| P2 (backlog) | 17 | 0 | 6 | 11 |
| **Actionable total** | **45** | **16** | **16** | **13** |

### 2.5 Positive findings (no action needed)

- `haas/security/redact.py` + `patterns.json`: multi-layer redaction with regex table, context-aware path redaction.
- `haas/model_proxy/secret.py`: `HAAS_CREDENTIAL_FD` env pop + `set_inheritable(fd, False)` (S4-010, closed).
- `haas/harnesses/codex_app_server/transport.py`: env allowlist prevents provider credential inheritance to Codex subprocess; loopback-only binding.
- `haas/runtime/delegation.py`: image digest pinning + `--cap-drop ALL` + `no-new-privileges` + `--network none` + non-root user.
- `haas/events.py`: `nativeType` allowlist filtering, `sequenceNumber` per-invocation补齐 at event-log layer, double redaction at projection boundary.
- `haas/policy/controller.py`: `_is_private_or_special` IP literal check, userinfo/query/fragment rejection in model proxy.
- `haas/artifacts/`: `safe_relative_path` traversal protection + `X-Content-Type-Options: nosniff`.

---

## 3. P0 Optimization Themes (Confirmed Defects)

### Theme P0-1: Secretless Redaction Gaps

**Finding IDs**: S4-001 (major), S4-002 (minor), S3-007 (minor), S1-005 (minor)
**Classification**: Confirmed defect (all four)

#### Evidence

- **S4-001**: `haas/security/patterns.json:1-47` — `token_patterns` includes `openai_style_key`, `bearer_token`, `jwt`, but no pattern for `haas_mp_`-prefixed model proxy runtime tokens. Verified: `redact('token=haas_mp_aGVsbG8uMT.abc...')` returns the token unredacted. `haas_mp_<base64>.<gen>.<nonce>` is the loopback bearer credential for the model proxy (issued in `haas/model_proxy/token.py`).
- **S4-002**: `haas/model_proxy/proxy.py:156` — `data = json.loads(json.dumps(data).replace(credential, '[REDACTED]'))` — pure string replace on serialized JSON. If `credential` contains `"` or `\`, `json.dumps` escapes them and `replace` misses. Verified: `credential='sk-real"key'` leaks after replace. Same issue at `proxy.py:341-342` (stream relay path).
- **S3-007**: `haas/runtime/delegation.py:144-145` — `detail = stderr.decode(errors="replace").strip() or stdout.decode(...); raise DelegatedContainerUnavailable(detail or "docker_command_failed")` — raw docker CLI stderr/stdout enters exception text. Docker errors may contain host paths, volume names, mount source paths. Contrast with `opensandbox.py:137` which uses `safe_upstream_body` redaction.
- **S1-005**: `haas/api.py:625` — `_execution_ready` catches `Exception as exc` and returns `str(exc)` as `safe_reason`, which is then returned in the 503 response body (lines 646-652). If `adapter.probe()` raises an exception containing provider baseUrl, socket path, or upstream connection details, it leaks to the northbound client.

#### Root cause

Redaction was designed around known provider key patterns but did not include HaaS-internal credential formats. The model proxy redaction uses string replace instead of recursive dict traversal, assuming credentials are ASCII-safe. Error paths were built with `str(exc)` before the secretless hard boundary was fully enforced.

#### Design change

1. **Add `haas_mp_` token pattern** to `haas/security/patterns.json`:
   ```json
   {"name": "haas_model_proxy_token", "pattern": "\\bhaas_mp_[A-Za-z0-9._\\-]{20,}\\b"}
   ```
   Add reverse-assertion test: `redact({'token': 'haas_mp_x.1.abcdefghijklmnopqrstuvwx'}) == {'token': '[REDACTED]'}`.
2. **Fix model proxy redaction**: replace string-replace-on-serialized-JSON with recursive dict traversal on the deserialized object:
   ```python
   def _redact_credential(obj, credential):
       if isinstance(obj, str):
           return obj.replace(credential, '[REDACTED]')
       if isinstance(obj, dict):
           return {k: _redact_credential(v, credential) for k, v in obj.items()}
       if isinstance(obj, list):
           return [_redact_credential(v, credential) for v in obj]
       return obj
   ```
   Apply to both non-streaming (`proxy.py:156`) and streaming (`proxy.py:341-342`) paths.
3. **Redact docker stderr**: in `delegation.py:144-145`, pass `detail` through `safe_upstream_body` (from `haas/security/redact.py`) before raising; keep only stable error classification in the exception message.
4. **Fix `safe_reason` leak**: in `haas/api.py:625`, map probe exceptions to fixed safe reason codes (`adapter_probe_failed`, `adapter_not_ready`); raw `str(exc)` only goes to internal logs (behind `trace_content` gate).

#### Compatibility / Security impact

- **Security hardening**: all changes tighten secretless boundary; no protocol field changes.
- `safe_reason` values change from raw exception text to fixed codes — this is a behavior change but `safe_reason` was never a stable contract (it was supposed to be safe). Document in `haas-protocol` spec.
- No performance impact (recursive traversal is on small response objects).

#### Test plan

1. `tests/test_security.py` — add `test_redact_haas_mp_token`; assert `haas_mp_` tokens are redacted in strings, dicts, and nested structures.
2. `tests/test_model_proxy.py` — mock provider response containing credential with `"` and `\`; assert response payload does not contain the credential.
3. `tests/test_opensandbox.py` (or new `test_delegation_security.py`) — mock docker run failure with stderr containing `/etc/secret/path`; assert `DelegatedContainerUnavailable` message does not contain the path.
4. `tests/test_api.py` — mock `adapter.probe()` raising `RuntimeError("connect to https://secret-provider.internal:8443 failed")`; assert 503 body does not contain the URL.
5. Regression: existing `test_security.py`, `test_model_proxy*.py` pass.

#### Measurable acceptance criteria

- `haas_mp_` token redaction: 100% of test vectors redacted.
- Credential with JSON special chars: 0% leakage in model proxy responses.
- Docker stderr: no host paths in exception messages.
- Probe failure: no internal URLs in 503 body.
- `make secret-scan` passes on all changed files.

#### Rollout / Rollback

- **Rollout**: all changes are security hardening; no flag needed. Deploy in next release.
- **Rollback**: not applicable (security fix); if regression found, revert specific commit.

---

### Theme P0-2: SSE Protocol Hardening

**Finding IDs**: S1-003 (major), S1-004 (major)
**Classification**: Confirmed defect (spec implementation gap)

#### Evidence

- **S1-003 + S1-004**: `haas/api.py:2671-2729` — the `/run_sse` POST path constructs:
  - Line 2671: `queue: asyncio.Queue[str | None] = asyncio.Queue()` — **unbounded queue** (no `maxsize`).
  - Lines 2682-2683, 2712: `queue.put_nowait(f"data: {json.dumps(adk_event, separators=(',', ':'))}\n\n")` — **manual `data:`-only frames**, no `id:`, `event:`, or `retry:` lines.
  - Line 2728: `return StreamingResponse(frames(), ...)` — uses raw `StreamingResponse`, not `EventSourceResponse`.
  - No reading of `Last-Event-ID` request header anywhere in this path.
- **Spec requirement** (`specs/event-log-sse/README.md:43-45`):
  - Line 43: "Replay missed events before entering a live stream using `Last-Event-ID` / `after_event_id`."
  - Line 45: "Use a bounded per-subscriber delivery queue. Persistent canonical events are never dropped or coalesced. When a subscriber cannot keep up, disconnect it with its last successfully delivered event id; the client replays from persistent storage."
- **Dependency verified**: `uv.lock` pins `fastapi = "0.141.1"`; both `fastapi.sse.EventSourceResponse`/`ServerSentEvent` and `sse_starlette 3.4.11` are importable in the locked environment.

This constitutes a **clear spec implementation gap**: the implementation satisfies neither the bounded-queue requirement nor the Last-Event-ID replay requirement, and the frames lack `id:` fields needed for clients to track replay position.

> **Note on client type**: `POST /run_sse` is an ADK-compatible endpoint consumed by ADK/HTTP clients that support SSE framing. It is not a browser-native `EventSource` GET endpoint (which would use GET with query params). The `id:`/`retry:` fields are still valuable because any SSE-aware client (including the manager's `stream_bridge.py` and generic ADK SDKs) can use them for replay cursor tracking and reconnection timing.

#### Root cause

The SSE implementation predates the `fastapi.sse` module and was built as raw string framing for the manager's internal consumption. The queue and replay were designed for single-tenant manager use, not for generic ADK client compatibility as required by `event-log-sse` spec.

#### Design change

1. **Migrate to `EventSourceResponse` + `ServerSentEvent`** (available in locked FastAPI 0.141.1):
   - Each frame: `ServerSentEvent(data=payload, id=str(eventId), retry=15000)`.
   - Omit `event=` (defaults to `message` channel for backward compatibility).
   - `id` uses the globally unique `eventId` (`evt_NNN`), which is the session-scoped replay cursor per `event-log-sse` spec line 135.
   - Heartbeat continues as SSE comment (`: keep-alive\n`) per existing convention and spec line 44.
2. **Bounded queue + backpressure**: set `maxsize=256`; on `put_nowait` raising `QueueFull`, disconnect the stream with a final `ServerSentEvent(event="error", data={"code": "haas_sse_backpressure", "lastEventId": last_id})`, per spec line 45.
3. **Last-Event-ID replay**: read `Last-Event-ID` header (or `after_event_id` query param for HaaS-native streams) at the start of `/run_sse`; replay persisted events with `eventId > cursor` before entering the live stream, per spec line 43.
4. **sequenceNumber**: no change needed — `event-log-sse` spec lines 38, 131-136 already define per-invocation scope; implementation is compliant. (S2-009 was a false positive; see §2.3.)

#### Compatibility / Security impact

- **Additive**: `id:` and `retry:` lines are additive to the SSE wire format; existing `data:` payload is unchanged. ADK clients that ignore `id:`/`retry:` continue to work.
- **Behavior change**: slow consumers will now be disconnected (with `haas_sse_backpressure`) instead of causing unbounded memory growth. This is the spec-required behavior.
- **New capability**: `Last-Event-ID` replay enables clients to resume after disconnect.
- No security impact; `id` is the already-public `eventId`.

#### Test plan

1. `tests/test_events.py` — assert `sequenceNumber` is per-invocation and `eventId` is session-scoped (regression guard for S2-009 false positive).
2. `tests/test_api.py` — `POST /run_sse` with a fake harness producing 3 events; assert raw SSE frames contain `id: evt_0`, `id: evt_1`, `retry: 15000`; assert `Last-Event-ID: evt_1` on reconnect skips events 0–1 and starts at evt_2.
3. **Backpressure**: integration test with a slow consumer (sleep 1s per event) producing 300 events; assert stream disconnects with `haas_sse_backpressure` and memory does not grow unboundedly.
4. **Contract**: `make adk-compat` — verify ADK 2.0 SSE client can connect, receive events, and auto-reconnect with `Last-Event-ID`.
5. Regression: existing `test_api.py`, `test_s3_cancel_replay.py` pass.

#### Measurable acceptance criteria

- `POST /run_sse` raw output contains `id:` lines for every event.
- Reconnect with `Last-Event-ID: evt_N` skips events ≤ N.
- Slow consumer at 256+ backlog disconnects within 1s of queue saturation with `haas_sse_backpressure`.
- `make adk-compat` passes.
- No existing SSE test regressions.

#### Rollout / Rollback

- **Rollout**: feature-flag `HAAS_SSE_STRUCTURED_FRAMES=1` (default off initially); enable in staging; promote to default after 1 release cycle.
- **Rollback**: set flag to `0` to restore raw `data:` framing; no data migration needed.

---

### Theme P0-3: Terminal State Persistence Atomicity

**Finding IDs**: S2-005 (major), S2-004 (minor), S2-006 (minor)
**Classification**: Confirmed defect (S2-005) + Confirmed structural risk (S2-004, S2-006)

#### Evidence

- **S2-005**: `haas/sessions.py:987-1014` — `_persist_terminal_event` performs 7+ independent SQLite commits: `put_invocation` → `put_turn` → `put_session` (line 990) → `put_session` (line 996/1001, pause/cancel branch) → `put_session` (line 1008, pending policy) → `event_log.append` → `put_invocation`/`put_turn` again. Each is `isolation_level=None` autocommit (`haas/stores/sqlite.py:208,263,269`). A crash between commits leaves half-committed terminal state (e.g., terminal event persisted but session row still `running`).
- **S2-004**: `haas/stores/sqlite.py:56-59` — `PRAGMA journal_mode=WAL` and `foreign_keys=ON` are set, but no `PRAGMA busy_timeout`. Single connection + `threading.RLock` serializes in-process, but a second process/connection gets immediate `SQLITE_BUSY` instead of waiting.
- **S2-006**: `haas/stores/sqlite.py:208-214` — `SQLiteStore` does not override `get_session`/`get_invocation`/`get_turn`/`read_*`; all reads hit the in-memory dict from `MemoryStore`. Write path: `super().put_*()` (mutates memory) → `_put_record` (writes SQLite). If `_put_record` raises (disk full, I/O error), memory is already mutated but DB is not.

#### Root cause

The persistence layer was designed as write-through cache (memory first, then DB) without transaction boundaries or write-failure rollback. The terminal event path was built incrementally without wrapping the multi-record update in a single transaction.

#### Design change

1. **Atomic terminal persistence**: wrap `_persist_terminal_event` in a single SQLite transaction:
   - Add `SQLiteStore.transaction()` context manager: `BEGIN IMMEDIATE` ... `COMMIT` / `ROLLBACK`.
   - In `_persist_terminal_event`, collect all record mutations (invocation, turn, session, event) and flush them in one `transaction()` block.
   - Reduce `put_session` calls from 3+ to 1 (compute final session state in memory, then persist once).
2. **busy_timeout**: add `PRAGMA busy_timeout=5000` after `journal_mode=WAL`.
3. **Write-failure rollback**: if `_put_record` raises, roll back the in-memory mutation to the last known persisted state (keep a pre-write snapshot of the affected record). Alternatively, on write failure, mark the store as `degraded` and reject further writes with a structured error.

#### Compatibility / Security impact

- No protocol change; internal persistence only.
- `BEGIN IMMEDIATE` may increase write latency slightly but ensures atomicity.
- Write-failure rollback changes failure semantics from silent divergence to explicit error — this is a correctness fix.

#### Test plan

1. **Atomicity**: fault-injection test — monkeypatch `_put_record` to raise on the 3rd write in `_persist_terminal_event`; assert after restart that readback converges to a consistent state (all-or-nothing), not a half-committed `running` with terminal event.
2. **busy_timeout**: open a second `sqlite3` connection with a write lock; assert the store's write waits up to 5s instead of immediate `SQLITE_BUSY`.
3. **Write-failure rollback**: monkeypatch `_put_record` to raise `OSError`; assert subsequent `get_session` returns the pre-write state (or a `degraded` error), not the new unpersisted state.
4. Regression: existing `test_stores_sqlite.py`, `test_sessions.py` pass.

#### Measurable acceptance criteria

- Fault-injection test: 0% of crashes leave half-committed terminal state.
- busy_timeout: second-connection write waits ≥ 1s before failing (not immediate).
- Write-failure: memory and DB never diverge silently.
- `test_sessions.py` + `test_stores_sqlite.py` pass.

#### Rollout / Rollback

- **Rollout**: atomic persistence behind `HAAS_ATOMIC_TERMINAL_PERSIST=1` flag; enable after staging validation.
- **Rollback**: flag off restores per-record autocommit; no data migration.

---

### Theme P0-4: Adapter Capability Honesty and Recovery

**Finding IDs**: S3-006 (major), S3-001 (major), S3-005 (major)
**Classification**: Confirmed defect (all three)

#### Evidence

- **S3-006**: `haas/harnesses/codex_app_server/adapter.py:536-542` — `turn_params` includes `threadId`, `input`, `sandboxPolicy`, `approvalPolicy`, `cwd`, but **no `disabledTools` or `tools` field**. The policy layer compiles `ToolsPolicy.disabled` (`haas/policy/models.py:33-35`), and the runtime passes it in `adapter_policy` (`haas/sessions.py:364`), but the adapter never reads `request.policy["tools"]["disabled"]`. The adapter declares `toolRestriction="advisory"` in capabilities (`adapter.py:420`), and `haas/mcp/runtime.py:88-111` reports `enforcement=advisory` — but the advisory itself is not implemented. The only tool restriction is hardcoded `web_search: disabled` (`adapter.py:909`).
- **S3-001**: `haas/harnesses/codex_app_server/adapter.py:547` — `turn_result = await self._rpc.request("turn/start", turn_params)`. If this raises `CodexRequestTimeout` or `CodexConnectionError`, it bubbles to `haas/sessions.py:603` generic `except Exception`, which emits a bare `"failed"` terminal event with no error code and no `retryable` flag. Contrast with the `stream_events` path (`adapter.py:687-700`), which on connection disconnect produces structured `incomplete` + `haas_adapter_unavailable` + `retryable=True`.
- **S3-005**: `haas/harnesses/codex_app_server/adapter.py:237` — `if "type" in item: codex_input.append(item)` — `_to_codex_input` passes any northbound input item with a `"type"` key directly to Codex, bypassing the ADK `{role, parts}` shape validation.

#### Root cause

- S3-006: The disabled-tools policy was added to the policy layer but the Codex adapter integration was never completed; the capability declaration was set to "advisory" optimistically.
- S3-001: The `start_turn` path was built before the structured error code system existed in the stream path; it was never retrofitted.
- S3-005: The native passthrough was likely added for testing/internal use but never gated to internal paths only.

#### Design change

1. **S3-006 (disabled tools)**: append disabled tool names to the `instructions` field in `turn/start` params (e.g., `"Do not use the following tools: <names>. They are disabled by policy."`). This is instruction-level advisory, matching the declared capability. Add test asserting instructions contain disabled tool names.
2. **S3-001 (start_turn error codes)**: in `adapter.start_turn()`, catch `CodexRequestTimeout` and `CodexConnectionError` explicitly; raise a structured `AdapterTurnStartError(code="haas_request_timeout"|"haas_adapter_unavailable", retryable=True)`. In `sessions.py:603`, map this exception to a structured terminal event with the code and `retryable=True`, matching the stream path pattern.
3. **S3-005 (native passthrough)**: gate the `"type" in item` passthrough behind an explicit internal flag (e.g., `_allow_native_passthrough=False` on the request context). For northbound ADK paths, validate input items against `{role, parts}` shape and reject unknown fields with a structured error (`haas_input_invalid`). Only test fixtures and internal delegation paths set the flag.

#### Compatibility / Security impact

- **S3-006**: Adding instructions text changes the system prompt sent to Codex — additive, no protocol change.
- **S3-001**: Terminal event gains `error`/`code`/`retryable` fields on start_turn failure — additive to the event schema; clients that ignore these fields see `"failed"` as before.
- **S3-005**: Rejecting malformed input is a behavior change — previously accepted, now rejected. This is a correctness fix; document as behavior tightening in `haas-protocol` spec.

#### Test plan

1. **S3-006**: `tests/test_codex_adapter.py` — configure policy with disabled tools `["web_search", "custom_tool"]`; assert `turn/start` params `instructions` contains both tool names.
2. **S3-001**: `tests/test_codex_recovery.py` — mock `turn/start` to raise `CodexRequestTimeout`; assert terminal event contains `code=haas_request_timeout` and `retryable=True`.
3. **S3-005**: `tests/test_codex_adapter.py` — send input item `{"type": "text", "text": "x", "evil": 1}` on northbound path; assert structured `haas_input_invalid` error; assert on internal path (flag set) the item passes through.
4. Regression: `tests/test_harness_adapter_contract.py`, `tests/test_codex_adapter.py`, `tests/test_codex_recovery.py` pass.

#### Measurable acceptance criteria

- S3-006: disabled tools appear in instructions; catalog capability matches implementation.
- S3-001: start_turn timeout produces structured error code + retryable flag.
- S3-005: northbound malformed input rejected; internal passthrough still works.
- Adapter contract tests pass.

#### Rollout / Rollback

- **Rollout**: S3-006 and S3-001 are additive; S3-005 is a behavior tightening. Deploy together with spec update.
- **Rollback**: S3-005 can be reverted by setting `_allow_native_passthrough=True` by default (temporary escape hatch).

---

### Theme P0-5: Async Event Loop Blocking I/O (Phase 0 Gated)

**Finding IDs**: O-ASYNC-1 (merged S1-010 + S2-007, major)
**Classification**: Confirmed structural risk; **Phase 0 measurement required before implementation commitment**

#### Evidence

- `haas/api.py:779` — `run_sse` is `async def`; `_run`/`_drive` are async generators.
- `haas/sessions.py:509,671,815,987-1014` — `_drive` calls `store.assert_lease()`, `store.put_session()`, `event_log.append()` directly in the event loop thread.
- `haas/stores/sqlite.py:56` — `SQLiteStore` uses synchronous `sqlite3.connect(check_same_thread=False)` with `isolation_level=None` (autocommit). All writes are synchronous `.execute()`/`.commit()`.
- No `run_in_executor`, `asyncer.asyncify`, or `aiosqlite` wrapping anywhere in the write path.

#### Phase 0 measurement

Add `haas/observability/metrics.py` histogram `event_loop_blocked_ms` (measure `loop.time()` delta before/after each store write). Run `make test-e2e` with a high-event-count fake harness (1000+ events) under concurrent 5x SSE streams.

- **Go criterion**: P99 event loop lag > 50ms → proceed to thread-pool offload.
- **No-go criterion**: P99 ≤ 50ms → close as not_issue under current single-tenant concurrency assumptions; record residual risk for multi-tenant future.

#### Design change (if Phase 0 go)

Offload store writes to a thread pool:
- Add `async def _offload(fn, *args)` helper using `asyncio.get_running_loop().run_in_executor(None, fn, *args)`.
- Wrap `store.put_session()`, `store.put_turn()`, `store.append()`, `store.assert_lease()` in `_offload`.
- Reads (`get_session`, `read_events`) stay synchronous (in-memory cache hit).
- `SQLiteStore` already uses `threading.RLock` (`haas/stores/sqlite.py:56`), so thread-pool access is safe.

#### Test plan (if go)

1. Phase 0 benchmark: `pytest tests/test_observability.py -k event_loop_lag` — assert metric is emitted.
2. Offload unit: mock `run_in_executor` and assert store writes are offloaded; assert reads are not.
3. Integration: concurrent 5x `POST /run_sse` with 500-event fake harness; assert P99 inter-event latency < 100ms (vs baseline).

#### Rollout / Rollback

- **Rollout**: Phase 0 telemetry first (no behavior change); offload behind `HAAS_STORE_OFFLOAD=1` flag.
- **Rollback**: flag off restores direct synchronous writes.

---

## 4. P1 Optimization Themes (Full Design)

### Theme P1-1: API Dependency Injection, Response Models, and Route Organization

**Finding IDs**: S1-001 (major), S1-002 (major), S1-006 (major)
**Classification**: Confirmed structural risk

#### Evidence

- **S1-001**: `haas/api.py` — `grep -c 'Depends(' haas/api.py` returns 1 (only `UploadFile`). All 50+ routes manually call `principal = await _authenticate(runtime.identity, request)` and read `runtime` from the `build_app` closure. No `PrincipalDep`/`RuntimeDep` type aliases. Routes that forget `_authenticate` silently run unauthenticated.
- **S1-002**: `haas/api.py` — `grep -c 'response_model' haas/api.py` returns 0. All routes return raw `dict` / `Any`. Sensitive field filtering relies on manual `pop()` in `haas/events.py:185` (4 fields).
- **S1-006**: `haas/api.py:584` — 50+ routes all on a single `FastAPI` instance `@app.*`, no `APIRouter` grouping. ADK routes and `/v1/haas/*` routes are mixed in one 3302-line `build_app` closure.

#### Root cause

The API was built as a single monolithic file before FastAPI DI conventions and `response_model` were adopted. The `build_app` closure pattern was chosen for dependency injection via closure capture, which predates `Annotated[..., Depends(...)]` type aliases.

#### Phase 0 gate

Before committing to the full refactor, measure:
- Count routes missing `_authenticate` (M-04): if ≥3 routes lack auth, prioritize DI refactor for safety.
- Count routes by functional group to determine router split boundaries.

#### Design change (staged plan)

**Stage 1 — DI aliases (low risk, high safety value)**:
- Extract `PrincipalDep = Annotated[Principal, Depends(get_principal)]` and `RuntimeDep = Annotated[_Runtime, Depends(get_runtime)]`.
- Move `_authenticate` logic into `get_principal` dependency.
- Route signatures replace manual `_authenticate` calls with `principal: PrincipalDep`.
- This stage alone eliminates the "forgot auth = silent bypass" risk.

**Stage 2 — response models (medium risk)**:
- Define view models (`SessionView`, `HarnessView`, `ProfileView`, `RunResponse`) with only public fields.
- Add `response_model=...` to routes returning internal records.
- Add reverse tests asserting internal fields (e.g., `credentialRef`, provider config) never appear in dumped output.

**Stage 3 — APIRouter split (largest refactor, last)**:
- Split into `sessions_router`, `harnesses_router`, `delegation_router`, `artifacts_router`, `haas_native_router`.
- `build_app` only includes routers with shared `dependencies=[Depends(get_principal)]`.
- Each router can be tested independently.
- This stage requires `make full-check` due to cross-component impact.

Each stage is an independent PR with its own test + review gate. Stage 3 can be deferred if Stages 1–2 satisfy the safety and maintainability goals.

#### Compatibility / Security impact

- Stage 1: internal only; no protocol change.
- Stage 2: `response_model` may filter fields that were previously (accidentally) exposed — this is a security improvement. If any client depends on an internal field, it must be explicitly added to the view model.
- Stage 3: internal refactor; routes and paths unchanged.

#### Test plan

1. Stage 1: assert every route has `PrincipalDep` in signature (grep-based test); assert routes without auth are only `/health`, `/ready`.
2. Stage 2: reverse-assertion tests for each view model.
3. Stage 3: existing `test_api*.py` suite passes unchanged (route paths and behaviors identical).

#### Measurable acceptance criteria

- Stage 1: 0 routes with manual `_authenticate` calls; 0 unauthenticated routes except health/ready.
- Stage 2: all routes returning internal records have `response_model`; reverse tests pass.
- Stage 3: `grep -c 'APIRouter(' haas/api.py` ≥ 4; `test_api*.py` 100% pass.

#### Rollout / Rollback

- Each stage is an independent PR; `git revert` per stage if regression.
- No data migration; no protocol change beyond Stage 2 field filtering (security fix).

---

### Theme P1-2: Resource Bounds and Memory Cleanup

**Finding IDs**: S2-001 (major), S2-002 (major), S2-003 (major), S2-010 (minor), S1-012 (nit)
**Classification**: Confirmed structural risk (all)

#### Evidence

- **S2-001**: `haas/stores/memory.py:506` — `delete_session` only does `self._sessions.pop(key, None)`. `_events_by_session`, `_events_by_invocation`, `_invocations`, `_turns`, `_idempotency` are never cleaned. No TTL or capacity sweep.
- **S2-002**: `haas/artifacts/store.py:31,74,121` — `self._content: dict[str, bytes]` holds all artifact bytes in memory. `register_produced()` replaces content but only removes old id from `_by_session` (lines 121-125); old bytes remain in `_content`. No global capacity limit, no LRU.
- **S2-003**: `haas/artifacts/models.py:49-50` — `ArtifactPolicy.maxFiles=1000`, `maxPublishFiles=20` declared but `grep` shows store.py never reads these fields. `register()` only checks `maxFileBytes` (line 51), not file count.
- **S2-010**: `haas/stores/memory.py:849-916` — idempotency records only lazily tombstoned on re-access; `_idempotency` dict grows unboundedly.
- **S1-012**: `manager/coworker/haas/stream_bridge.py:77-79,567-569` — `_seen`/`_tool_seen`/`_content_seen` dedup sets grow unboundedly and are persisted in `to_dict`.

#### Root cause

The in-memory stores were designed for short-lived sidecar processes (desktop app session) without long-running server deployment in mind. Artifact content was kept in memory for fast download without considering multi-turn accumulation. Quota fields were declared in the policy model but the enforcement was never wired into the store.

#### Design change

1. **delete_session cascade**: in `haas/stores/memory.py:506`, after `self._sessions.pop(key, None)`, also clean:
   - `[self._events_by_session.pop((app, user, sid, eid), None) for eid in session_event_ids]`
   - `self._events_by_invocation.pop(inv_id, None)` for each invocation in the session
   - `self._invocations.pop(inv_id, None)`, `self._turns.pop(turn_id, None)`
   - Idempotency records for the session's keys.
2. **Artifact content bounds**:
   - Add `maxContentBytes` to `ArtifactPolicy` (e.g., 512 MiB default).
   - In `register_produced()`, when replacing content at a path, `pop` the old file_id's bytes from `self._content`.
   - Add LRU eviction: when total `_content` bytes exceed `maxContentBytes`, evict least-recently-accessed non-current-version artifacts.
3. **Quota enforcement**: in `register()`/`register_produced()`, count files per session; if `len(_by_session[session_key]) >= maxFiles`, reject with structured `ArtifactQuotaExceeded` error. Publish path checks `maxPublishFiles`.
4. **Idempotency sweep**: add a `sweep_expired(now_ms)` method that removes expired tombstones; call it lazily in `reserve()` (when touching the dict) or via a periodic background task.
5. **stream_bridge dedup truncation**: after invocation completion (terminal event received), clear `_seen`/`_tool_seen`/`_content_seen` sets for that invocation.

#### Compatibility / Security impact

- `ArtifactQuotaExceeded` is a new error code — additive.
- delete_session cascade changes behavior: previously deleted session's events remained accessible via replay; after fix, they are removed. This matches the expected semantics of session deletion. Document in `session-runtime` spec.
- No protocol field changes.

#### Test plan

1. `tests/test_stores_memory.py` — create session → produce N events → delete_session → assert `len(_events_by_session) == 0` and `len(_invocations)` decreased.
2. `tests/test_artifacts.py` — register_produced same path twice with large content; assert old file_id bytes removed from `_content`.
3. `tests/test_artifacts.py` — register >1000 files; assert 1001st raises `ArtifactQuotaExceeded`.
4. `tests/test_stores_memory.py` — write expired idempotency keys, advance clock, call sweep; assert `_idempotency` shrinks.
5. `manager/tests/test_haas_stream_bridge.py` — after terminal event, assert `_seen` sets cleared.

#### Measurable acceptance criteria

- delete_session: 0 orphaned events/invocations after deletion.
- Artifact supersede: old bytes freed from `_content`.
- Quota: >maxFiles registrations rejected.
- Idempotency sweep: expired keys removed.
- stream_bridge: dedup sets cleared after invocation completion.

#### Rollout / Rollback

- **Rollout**: all changes are internal memory management; no flag needed. LRU eviction behind `HAAS_ARTIFACT_LRU=1` if cautious.
- **Rollback**: `git revert`; no data migration (in-memory only).

---

### Theme P1-3: Adapter Type Safety

**Finding IDs**: S3-003 (major), S3-004 (major)
**Classification**: Confirmed structural risk

#### Evidence

- **S3-003**: `haas/harnesses/base.py:47-52` — `StartTurnRequest` fields `sandbox`, `policy`, `credentials`, `mcpServers` are all `dict[str, Any]` / `list[dict[str, Any]]`. Adapter internally does string-key access (`adapter.py:511 sandbox.get("mode")`, `adapter.py:899-900 credentials["baseUrl"]`). No schema validation at boundary; typos cause runtime `KeyError`.
- **S3-004**: `haas/harnesses/base.py:78` — `HarnessEvent.type: str` is a free string. Event type mapping is maintained separately in `haas/events.py:29` (`_HARNESS_EVENT_TYPES` dict). Unregistered types silently degrade to `"haas.adapter.event_unparsed"` (`events.py:92`).

#### Root cause

The adapter interface was designed before Pydantic modeling was adopted for internal contracts. The dict-bag pattern allows rapid iteration but sacrifices type safety and boundary validation. The event type system uses a runtime dict mapping instead of a static discriminated union.

#### Design change

1. **Typed StartTurnRequest models**:
   ```python
   class SandboxSpec(BaseModel):
       mode: Literal["local", "delegated"]
       network: NetworkPolicy | None = None
       writableRoot: str | None = None

   class CredentialHandle(BaseModel):
       ref: str  # secret:// reference, never plaintext

   class McpServerConfig(BaseModel):
       name: str
       url: HttpUrl | None = None
       transport: Literal["stdio", "sse", "http"]

   class StartTurnRequest(BaseModel):
       sandbox: SandboxSpec
       policy: TurnPolicy  # typed, with ToolsPolicy nested
       credentials: list[CredentialHandle]
       mcpServers: list[McpServerConfig]
   ```
   Replace dict bags in `base.py:47-52`. Adapter internal code changes from `sandbox.get("mode")` to `sandbox.mode`.
2. **Discriminated HarnessEvent union**:
   ```python
   class HarnessTextDelta(BaseModel):
       type: Literal["harness.text.delta"]
       text: str
   class HarnessToolStarted(BaseModel):
       type: Literal["harness.tool.started"]
       toolName: str
   # ... all known types
   HarnessEvent = HarnessTextDelta | HarnessToolStarted | ...
   ```
   Unknown types fail validation at adapter boundary instead of silently degrading. The `_HARNESS_EVENT_TYPES` dict in `events.py` is replaced by the union's type discriminator.

#### Compatibility / Security impact

- Internal adapter interface change; no northbound protocol impact.
- Typed models provide boundary validation that may reject previously-accepted malformed input — this is a correctness improvement.
- `mypy` coverage improves for adapter code.

#### Test plan

1. `tests/test_harness_adapter_contract.py` — assert `StartTurnRequest` rejects unknown sandbox modes, missing credential refs, malformed MCP URLs.
2. `tests/test_codex_adapter.py` — construct `HarnessEvent` with unknown type; assert validation error.
3. `tests/test_events.py` — assert all known event types parse correctly through the union.
4. `mypy haas` — assert 0 new type errors after migration.

#### Measurable acceptance criteria

- `StartTurnRequest` fields are typed Pydantic models, not `dict[str, Any]`.
- `HarnessEvent` is a discriminated union; unknown types rejected at boundary.
- `mypy haas` passes with 0 issues.
- Adapter contract tests pass.

#### Rollout / Rollback

- **Rollout**: migrate `StartTurnRequest` first (lower risk), then `HarnessEvent` (higher touch). Each is an independent PR.
- **Rollback**: `git revert` per PR; no data migration.

---

### Theme P1-4: Observability Gaps

**Finding IDs**: S4-004 (minor), S1-008 (nit), S3-012 (minor)
**Classification**: Confirmed structural risk

#### Evidence

- **S4-004**: `haas/model_proxy/runtime.py:327` — `uvicorn.Config(proxy_app, log_level='error', access_log=False, lifespan='off')` — model proxy sub-service has no `StructuredLogger`, no access log, no audit events. Credential resolution failures, URL authorization denials, provider errors, stream interruptions are not auditable.
- **S1-008**: `haas/api.py:143,605,641` — `_haas_error_content` generates a new `f"tr_{uuid4()}"` per serialization; `/health`/`/ready` return hardcoded `"tr_local"`. No request-level traceId correlation between response and logs.
- **S3-012**: `haas/harnesses/codex_app_server/normalizer.py:361` — unknown Codex notification method returns `None` (silently dropped) with no counter or placeholder event.

#### Root cause

The model proxy was built as a minimal loopback relay without observability requirements. TraceId was added ad-hoc to error responses without a request-scoped correlation mechanism. The normalizer's unknown-method path was designed to be lenient (don't crash on new Codex versions) without adding observability for schema drift.

#### Design change

1. **Model proxy audit logging**: in `haas/model_proxy/proxy.py`, emit `StructuredLogger.event` on:
   - Authenticate failure (invalid/expired token)
   - URL authorization denial (`_authorize_url` reject)
   - Provider error (non-2xx response)
   - Stream interruption (client disconnect / provider disconnect)
   Fields only: `sessionId`, `harnessId`, `providerCode`, `statusCode`, `latencyMs`. **Never** body, token, or credential.
2. **Request-scoped traceId**: add a FastAPI middleware that generates `traceId` once per request and stores in `request.state.trace_id`. Error responses and log entries use the same traceId. `/health`/`/ready` also get a generated traceId (remove hardcoded `"tr_local"`).
3. **Unknown notification observability**: in `normalizer.py:361`, instead of `return None` silently, increment a counter `adapter_event_unparsed_total{method=...}` and emit a debug-level log. Optionally emit a `haas.adapter.event_unparsed` placeholder event (already exists in `events.py:92`).

#### Compatibility / Security impact

- Observability only; no protocol or behavior change.
- Audit log fields are strictly limited to non-sensitive identifiers.
- Counter labels use method name (low cardinality, bounded by known Codex notification methods).

#### Test plan

1. `tests/test_model_proxy.py` — mock auth failure; assert `StructuredLogger.event` was called with `event=model_proxy_auth_failed` and no credential fields.
2. `tests/test_api.py` — send a request that produces an error; assert response `traceId` matches the traceId in captured logs.
3. `tests/test_codex_normalizer.py` — inject unknown notification method; assert counter incremented or debug log emitted.

#### Measurable acceptance criteria

- Model proxy: 4 audit event types emitted on corresponding failure paths.
- traceId: response and logs share the same traceId for a given request.
- Unknown notification: counter or log emitted (not silent).
- No sensitive data in audit events (reverse-assertion test).

#### Rollout / Rollback

- **Rollout**: no flag needed; observability is additive.
- **Rollback**: `git revert`; no data migration.

---

### Theme P1-5: SSRF and Network Policy

**Finding IDs**: O-SSRF-1 (merged S3-009 + S4-003, minor), S3-008 (minor)
**Classification**: Confirmed structural risk (O-SSRF-1) + Hypothesis (S3-008, Phase 0 gated)

#### Evidence

- **O-SSRF-1**: `haas/policy/controller.py:204-221` — `authorize_network` does hostname string matching + IP literal check, no DNS resolution. A public hostname resolving to a private IP (DNS rebinding) passes the string check. Delegated containers are isolated by `--network none`, but in-process policy controller standalone use is incomplete.
- **S3-008**: `haas/runtime/models.py:10` — `SandboxNetwork.defaultAction: str = "allow"`. `sandbox.py` comments say "fail-closed", but the default is allow. `compiler.py:55` overrides with policy.network, but direct `SandboxSpec()` construction gets allow.

#### Phase 0 for S3-008

`grep -rn 'SandboxSpec(' haas/ tests/` — verify all construction paths go through `compiler.py`. If 0 direct constructions, this is not exploitable → close as `false_positive` with a defensive test. If ≥1 path exists, change default to `"deny"`.

#### Design change

- **O-SSRF-1**: document residual risk in `policy-controller` spec; optionally add resolved-IP validation at httpx transport layer (verify sockaddr not in private range after connect). Delegated container `--network none` is the hard boundary; in-process check is defense-in-depth.
- **S3-008**: per Phase 0 result.

#### Test plan

1. O-SSRF-1: use a hostname resolving to 127.0.0.1; record current behavior (passes string check). Document as known residual risk.
2. S3-008: grep-based test asserting all `SandboxSpec(` constructions go through compiler.

#### Rollout / Rollback

- Spec documentation only for O-SSRF-1; S3-008 per Phase 0.

---

## 5. P2 Backlog

| ID | Finding | File | Classification | One-line action |
|---|---|---|---|---|
| P2-01 | S1-007 error code by string equality | haas/api.py:2614 | Confirmed defect | Add `.reason` attribute to SessionBusyError |
| P2-02 | S1-009 lifespan internal override | haas/config.py:317 | Confirmed structural risk | Use standard lifespan + AsyncExitStack |
| P2-03 | S1-011 ADK timestamp epoch float | haas/events.py:171 | Hypothesis (M-03) | Verify ADK 2.0 Event.timestamp type in openapi.yaml; epoch float is per `specs/README.md:251` ADK contract → likely close |
| P2-04 | S2-008 lease clock mixing | haas/sessions.py:402,404 | Confirmed structural risk | Unify to monotonic clock for lease |
| P2-05 | S2-011 manager sqlite no WAL | manager/coworker/memory/sqlite_store.py:21 | Confirmed structural risk | Enable WAL + busy_timeout for consistency |
| P2-06 | S2-012 bare dataclass records | haas/stores/memory.py:105 | Confirmed structural risk | Migrate key records to Pydantic or add validation in _construct_record |
| P2-07 | S2-013 empty events not guarded | haas/sessions.py:296 | Nit | Add explicit structured error for empty events |
| P2-08 | S2-014 multiple put_session in terminal | haas/sessions.py:990 | Nit | Compute final state, put once (covered in P0-3 design) |
| P2-09 | S3-002 native error in exception | haas/harnesses/codex_app_server/rpc.py:229 | Confirmed structural risk | Use structured error codes, not native message |
| P2-10 | S3-010 LimitOverrunError unhandled | haas/harnesses/codex_app_server/transport.py:151 | Confirmed structural risk | Catch and convert to CodexTransportError |
| P2-11 | S3-011 respond_interaction KeyError | haas/harnesses/codex_app_server/adapter.py:810 | Confirmed defect | Use .get() + structured error |
| P2-12 | S4-005 ModelProxyError not whitelisted | haas/model_proxy/app.py:110 | Confirmed structural risk | Map all errors to haas_ prefix codes |
| P2-13 | S4-006 listen config ValueError | haas/model_proxy/runtime.py:299 | Nit | Validate listen format at startup |
| P2-14 | S4-007 credential_ref in exception | haas/model_proxy/secret.py:89 | Nit | Remove ref from exception message |
| P2-15 | S4-008 path redaction false-positive boundary | haas/security/redact.py:50 | Nit | Add test locking URL path non-redaction |
| P2-16 | S4-009 metrics cardinality | haas/observability/metrics.py:1 | Nit | Add constraint: name must be static string |
| P2-17 | S3-013 duplicate import os | haas/harnesses/codex_app_server/adapter.py:408 | Nit | Remove function-level import |
| P2-18 | Profile version accepts boolean because `bool` is an `int` subtype | haas/api.py:416-427 | Confirmed defect | Require `type(profileVersion) is int`; regression-test boolean and non-positive values |
| P2-19 | Explicit empty `delegationPolicySnapshot` is treated as omitted during existing delegated-session binding comparison | haas/api.py:400-413 | Confirmed defect | Distinguish missing key from explicit empty value so idempotent reuse cannot accept a conflicting policy |

P2-18 and P2-19 were discovered by the coverage-driven API contract suite. Both are safe, local validation fixes: valid existing requests remain unchanged; previously accepted malformed/conflicting requests fail with the existing structured `invalid_input` or delegated-session conflict response. Tests must prove valid integer versions and omitted optional policy still work, while boolean versions and explicit conflicting empty policy are rejected. Rollback is a targeted revert with no data migration.

---

## 6. Phase 0 Measurement Tasks

| ID | Item | Measurement | Pass criterion for proceeding |
|---|---|---|---|
| M-01 | O-ASYNC-1 event loop lag | Add `event_loop_blocked_ms` histogram; run 1000-event fake harness turn under concurrent 5x SSE | P99 lag > 50ms → proceed to offload; ≤ 50ms → close as not_issue |
| M-02 | S3-008 SandboxSpec default allow | `grep -rn 'SandboxSpec(' haas/ tests/`; verify all paths go through compiler.py | 0 direct constructions → close as false_positive; ≥1 → proceed to deny-by-default |
| M-03 | S1-011 ADK timestamp format | Read `specs/haas-protocol/*.openapi.yaml` Event.timestamp field; cross-ref `specs/README.md:251` (ADK contract = float seconds) | If epoch float per spec → close; if ISO-8601 → proceed to fix |
| M-04 | P1-1 route auth coverage | Count routes by group; identify routes missing `_authenticate` | ≥3 routes missing auth → prioritize DI refactor; <3 → defer Stage 1 |

---

## 7. Component Impact Analysis

| Component | Impact | Required action | Compatibility |
|---|---|---|---|
| `haas/api.py` | P0-2 (SSE frames), P0-1 (safe_reason), P1-1 (DI/response_model/routers), P1-4 (traceId) | Modify routes, add dependencies, split routers | SSE additive; DI internal; response_model additive |
| `haas/sessions.py` | P0-5 (offload), P0-3 (atomic terminal), P0-4 (start_turn error), P2-04/P2-07/P2-08 | Modify persistence path, add error mapping | Internal; terminal event additive fields |
| `haas/stores/sqlite.py` | P0-3 (transactions, busy_timeout, rollback) | Add transaction context manager, busy_timeout, rollback | Internal persistence only |
| `haas/stores/memory.py` | P1-2 (cascade delete, idempotency sweep) | Modify delete_session, add sweep | Internal |
| `haas/artifacts/store.py` + `models.py` | P1-2 (content bounds, quota enforcement) | Add LRU, enforce maxFiles | Internal; new error code additive |
| `haas/harnesses/base.py` | P1-3 (typed models, event union), P0-4 (native passthrough gate) | Replace dict bags, add discriminated union | Internal adapter interface; northbound input validation tightening |
| `haas/harnesses/codex_app_server/adapter.py` | P0-4 (disabled tools, start_turn error, native passthrough), P2-09/P2-10/P2-11/P2-17 | Modify turn params, error handling, input validation | Instructions additive; error codes additive; input rejection behavior change |
| `haas/runtime/delegation.py` | P0-1 (docker stderr redaction) | Add safe_upstream_body | Internal error messages |
| `haas/model_proxy/proxy.py` | P0-1 (recursive redaction), P1-4 (audit logging) | Replace string replace, add events | Internal; security fix |
| `haas/security/patterns.json` | P0-1 (haas_mp_ pattern) | Add pattern | Security hardening |
| `haas/observability/` | P1-4 (structured logging, traceId, counters), P0-5 (metrics) | Add events, metrics | Observability only |
| `haas/policy/controller.py` | P1-5 (SSRF documentation) | Spec update only | No code change in this wave |
| `manager/coworker/haas/stream_bridge.py` | P1-2 (dedup truncation) | Clear sets after invocation | Internal |
| `specs/event-log-sse/` | P0-2 (replay/queue spec already exists; implementation gap) | No spec change needed; implementation must align | Spec already correct (S2-009 false positive confirmed) |
| `specs/haas-protocol/` | P0-1 (safe_reason codes), P0-4 (input validation) | Update spec | Protocol documentation |
| `specs/harness-adapter/` | P0-4 (toolRestriction), P1-3 (typed interface) | Update spec | Adapter contract |
| `specs/session-runtime/` | P1-2 (delete_session cascade semantics) | Update spec | Behavior documentation |
| `tests/` | All themes | Add unit + integration + contract tests | Test additions only |

**Decision**: This is a new aggregated spec (`python-backend-optimization`) rather than updates to each component spec, because the optimization program spans 15+ components and needs a single prioritized task breakdown and rollout plan. Component specs are referenced and will be updated as part of each theme's implementation task.

---

## 8. Task Breakdown and Gates

| Order | Priority | Task | Finding IDs | Deliverable | Dependency | Gate |
|---|---|---|---|---|---|---|
| 1 | Phase 0 | Measurement baseline | M-01–M-04 | Benchmark results + go/no-go | None | Unit tests for metrics |
| 2 | P0 | Secretless redaction hardening | S4-001, S4-002, S3-007, S1-005 | Code + tests + spec delta | None | 2x code-review → brooks → pre-commit |
| 3 | P0 | SSE protocol hardening | S1-003, S1-004 | Code + tests + spec delta | None | 2x code-review → brooks → adk-compat → pre-commit |
| 4 | P0 | Terminal persistence atomicity | S2-005, S2-004, S2-006 | Code + fault-injection tests | None | 2x code-review → brooks → pre-commit |
| 5 | P0 | Adapter honesty + recovery | S3-006, S3-001, S3-005 | Code + tests + spec delta | None | 2x code-review → brooks → pre-commit |
| 6 | P0 | Async offload (if M-01 go) | O-ASYNC-1 | Code + benchmark | Task 1 | 2x code-review → brooks → pre-commit |
| 7 | P1 | Resource bounds + memory cleanup | S2-001, S2-002, S2-003, S2-010, S1-012 | Code + tests | None | 2x code-review → brooks → pre-commit |
| 8 | P1 | Adapter type safety | S3-003, S3-004 | Code + mypy + schema tests | None | 2x code-review → brooks → pre-commit |
| 9 | P1 | Observability gaps | S4-004, S1-008, S3-012 | Code + tests | None | 2x code-review → brooks → pre-commit |
| 10 | P1 | API DI Stage 1 (auth aliases) | S1-001 | Code + tests | Task 1 (M-04) | 2x code-review → brooks → pre-commit |
| 11 | P1 | API response models (Stage 2) | S1-002 | Code + reverse tests | Task 10 | 2x code-review → brooks → pre-commit |
| 12 | P1 | API APIRouter split (Stage 3, optional) | S1-006 | Code + full test suite | Task 11 | 2x code-review → brooks → full-check → pre-commit |
| 13 | P1 | SSRF doc + SandboxSpec default | O-SSRF-1, S3-008 | Spec update + M-02 result | Task 1 (M-02) | Spec review → pre-commit |
| 14 | P2 | Backlog items | P2-01–P2-17 | As applicable | P0/P1 complete | Opportunistic |

**Every implementation task** must follow: TDD (failing test first) → implementation → minimal unit test → integration test → E2E/smoke (where applicable) → two-round code-review → brooks-review → brooks-test → `make pre-commit` (and `make full-check` for cross-component tasks like API router split).

---

## 9. Non-goals

- No runtime behavior changes beyond the specific fixes described (no refactoring unrelated code).
- No introduction of new dependencies unless explicitly justified (`fastapi.sse` is available in locked FastAPI 0.141.1; thread-pool offload uses stdlib `run_in_executor`).
- No changes to the ADK 2.0 protocol contract beyond additive fields (SSE `id:`/`retry:`, terminal event error codes).
- No changes to the Tauri/Rust desktop layer or manager GUI.
- No Docker/container changes in this wave.
- No migration of `manager/coworker/` product code — only `haas/` sidecar and the `manager/coworker/haas/` bridge are in scope.
- P2 backlog items are not committed for this wave; they are recorded for future prioritization.
- No change to `sequenceNumber` semantics — `event-log-sse` spec already defines per-invocation scope (S2-009 false positive).

---

## 10. Compatibility, Migration, and Rollback

### Compatibility surfaces affected

| Surface | Change | Type | Mitigation |
|---|---|---|---|
| SSE wire format | Add `id:` and `retry:` lines | Additive | Clients ignoring these fields work unchanged |
| SSE slow-consumer behavior | Disconnect with `haas_sse_backpressure` instead of unbounded memory | Behavior change (spec-required) | Document; clients should handle disconnect + replay via `Last-Event-ID` |
| SSE replay | `Last-Event-ID` header now honored | New capability | Additive; clients not sending header get full stream |
| Terminal event | Add `error.code` + `retryable` on start_turn failure | Additive | Existing `"failed"` status unchanged |
| Northbound input validation | Reject malformed `{type}` input items | Behavior tightening | Document in haas-protocol; internal paths gated |
| `safe_reason` in 503 | Change from raw exception text to fixed codes | Behavior change (was never stable) | Document; clients should not parse safe_reason |
| Adapter `toolRestriction` | Advisory now actually implemented (instructions) | Capability fulfillment | No catalog change needed |
| Artifact `register()` | New `ArtifactQuotaExceeded` error | Additive | New error code; existing valid registrations unchanged |
| delete_session | Events/invocations now cascade deleted | Behavior change (matches expected semantics) | Document in session-runtime spec |

### Migration

- No data migration needed for any P0/P1 item.
- SSE `id:` field uses existing `eventId`; no schema change.
- Atomic persistence changes the write pattern but not the data format; existing SQLite databases are compatible.

### Rollback

- All P0 items are either behind feature flags (SSE structured frames, store offload, atomic persistence) or are security fixes with no rollback needed (redaction gaps).
- P1-1 (API DI/routers) is staged: each stage is an independent PR with `git revert` as rollback.
- No database schema changes in this wave, so no rollback migration needed.

---

## 11. Acceptance Criteria for This Spec

- [x] All P0 items have evidence (file:line), root cause, design, test plan, and measurable acceptance.
- [x] P1 items (resource bounds, adapter type safety, observability) have full Evidence→Root cause→Design→Impact→Test→Acceptance→Rollback.
- [x] P1-1 API refactor has staged plan with Phase 0 gate.
- [x] Phase 0 measurement tasks are defined with pass/fail criteria.
- [x] False positives are documented with resolution evidence (S2-009).
- [x] Component impact analysis covers all affected components.
- [x] Task breakdown is traceable to finding IDs and includes TDD + review gates.
- [x] Non-goals, compatibility, migration, and rollback are explicit.
- [x] Spec review is complete with no blocking items (revision 2).
- [x] Spec is added to `specs/README.md` index.
- [x] Static tools re-verified: ruff, mypy, git diff --check, make pre-commit all pass.
- [x] FastAPI dependency verified: locked 0.141.1, `fastapi.sse` + `sse_starlette` importable.
- [x] SSE evidence cites exact line range (`haas/api.py:2671-2729`) and spec requirement (`event-log-sse:43-45`).
