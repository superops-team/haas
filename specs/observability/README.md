# Observability Component Specification

**English** | [简体中文](README.zh-CN.md)

Status: Draft
Last reviewed: 2026-09-10
Related specs: [HaaS Protocol](../haas-protocol/README.md), [Session Runtime](../session-runtime/README.md), [Event Log & SSE](../event-log-sse/README.md), [Security Boundary](../security-boundary/README.md)

## 1. Component Role

Observability provides operational visibility into HaaS: health, readiness, status, structured logs, metrics, traces, diagnostics, and verification evidence. It helps diagnose issues in the protocol, adapters, sessions, SSE, model proxy, MCP, and OpenSandbox AIO, but it does not own business state.

## 2. Sources and Rationale

| Source | Adopted Content |
|--------|-----------------|
| `mpa-codex-worker` observability specs | health/ready/status, execution logs, startup readiness, and secret redaction |
| ADK 2.0 compatibility | Compatibility verification reports as evidence of protocol alignment |
| OpenSandbox server docs | `/health`, runtime status, OTLP metrics, and diagnostics API |
| This component overview | Observability, metrics, diagnostics, and conformance evidence |

## 3. Upstream and Downstream Relationships

| Direction | Object | Relationship |
|-----------|--------|--------------|
| Upstream | HaaS Protocol | Exposes health/ready/status/diagnostics |
| Upstream | All components | Emit structured logs, metrics, and trace spans |
| Downstream | Event Log | Correlates invocation/session/turn events |
| Downstream | Security Boundary | Redaction and safe-field policy |
| Downstream | Verification reports | Preserve test and conformance evidence |

## 4. Responsibilities and Boundaries

Responsibilities:

- Define liveness, readiness, and status semantics.
- Collect low-cardinality metrics.
- Generate structured logs and apply redaction.
- Correlate trace ID, request ID, harness ID, session ID, invocation ID, and turn ID.
- Produce a diagnostics summary.
- Manage formats for conformance, E2E, coverage, and review reports.

Non-responsibilities:

- Does not modify session/invocation/turn state.
- Does not directly read secret values.
- Does not directly execute adapter or provider requests.
- Is not a source of truth for business state; business facts come from the Registry, Session Runtime, Event Log, or Artifact Store.
- Does not own stable feature discovery. `GET /v1/haas/capabilities` is owned by HaaS Protocol and composed from registry, adapter, and runtime facts; clients MUST NOT infer support from `/status`.

## 5. Core Interfaces

### 5.1 Public Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/v1/haas/health` | Liveness; the process can respond |
| GET | `/v1/haas/ready?scope=control` | Can accept control-plane requests |
| GET | `/v1/haas/ready?scope=execution` | Can start a harness turn |
| GET | `/v1/haas/ready?scope=capability` | Optional capability (MCP/skill/browser) warmup is complete |
| GET | `/v1/haas/status` | Runtime status summary; not a stable capability-discovery contract |
| GET | `/v1/haas/diagnostics` | Redacted diagnostics summary |

### 5.2 Internal API

```python
def log_event(name: str, fields: dict) -> None: ...
def record_metric(name: str, value: float, labels: dict) -> None: ...
def start_span(name: str, attrs: dict) -> Span: ...
def build_health() -> HealthSnapshot: ...
def build_ready(scope: str) -> ReadySnapshot: ...
def build_status(include_sessions: bool, include_checks: bool) -> StatusSnapshot: ...
def write_verification_report(change_id: str, report_type: str, report: dict) -> None: ...
```

## 6. Data Model

### 6.1 ReadySnapshot

```json
{
  "ready": false,
  "scope": "execution",
  "reason": "adapter_not_ready",
  "retryable": true,
  "checks": [
    {
      "name": "sidecar",
      "status": "passed",
      "durationMs": 2
    },
    {
      "name": "codex_app_server",
      "status": "failed",
      "safeReason": "socket_not_ready",
      "durationMs": 1
    }
  ]
}
```

### 6.2 StatusSnapshot

```json
{
  "status": "degraded",
  "version": "dev",
  "uptimeSeconds": 120,
  "protocol": {
    "haasVersion": "2026-09-10",
    "adkProtocol": "2.0",
    "capability": "run/run_sse/sessions"
  },
  "adapters": [],
  "queues": {},
  "eventLog": {},
  "container": {},
  "lastErrorSafeReason": "codex_app_server_not_ready"
}
```

## 7. Runtime Model and State Machine

```text
component emits structured signal
  -> redaction
  -> log/metric/span sink
  -> status aggregator snapshot
  -> diagnostics endpoint
  -> verification report references command/evidence
```

Health/ready states:

- `healthy` means the HaaS HTTP process responds.
- `control_ready` means config/store/registry/control APIs can run.
- `execution_ready` means the selected adapter and required runtime dependencies can start turns.
- `capability_ready` means optional MCP/skill/browser capability warmups have completed.
- `degraded` means a non-critical capability is unavailable but core request handling still works.

## 8. Security and Permissions

- Metric labels MUST be low-cardinality and secret-free.
- Trace attributes MUST NOT contain raw prompts, file contents, headers, or tokens.
- Diagnostics require admin/debug scope when they include per-session details.
- Verification reports MUST redact commands or outputs that include credentials.
- Public status MAY include fingerprints and safe reasons, but not raw values.
- `protocol.haasVersion` MUST report the runtime-implemented version. It may report `2026-09-10` only after the roadmap conformance gate passes; spec publication alone does not advance runtime status.

## 9. Observability

Baseline metrics:

- `haas_http_request_duration_ms{route,status}`
- `haas_ready_state{scope,ready}`
- `haas_response_duration_ms{adapterBase,status}`
- `haas_response_terminal_total{adapterBase,status}`
- `haas_adapter_probe_total{adapterBase,status}`
- `haas_model_proxy_request_total{provider,wireApi,status}`
- `haas_mcp_probe_total{transport,status}`
- `haas_event_log_lag_ms`
- `haas_container_startup_duration_ms{phase,status}`
- `haas_lifecycle_control_total{action,stage,status}`
- `haas_lifecycle_control_duration_ms{action,status}`

Baseline logs:

- JSON structured logs go to stdout/stderr by default.
- Every request-scoped log line SHOULD carry `traceId`.
- Access logs MUST use route templates, not raw paths with query strings.
- Lifecycle logs carry safe session/invocation/operation ids, action and stage only; they never include continuation instructions or native thread ids.

## 10. Failure and Recovery

| Scenario | Behavior |
|----------|----------|
| Metrics exporter unavailable | Fail open; readiness is unaffected unless explicitly configured |
| Log sink unavailable | Fall back to stderr; record degraded status |
| Trace exporter unavailable | Fail open and increment the exporter failure metric |
| Diagnostics collection timeout | Return partial diagnostics (HTTP 200 with `partial: true` in the payload; this is not a `haasError.code`) |
| Redaction failure | Fail closed before writing sensitive data |
| Conformance report missing | Block release readiness for protocol changes |

## 11. Test Plan and Acceptance Criteria

- Unit: redaction of log fields, low-cardinality metric labels, and ready/status snapshot builders.
- Integration: health/ready/status endpoints with fake component states.
- E2E: run a Codex task and verify that trace IDs connect the request, events, response, and logs.
- Failure injection: metrics exporter failure and diagnostics timeout do not block core execution.
- Release evidence: ADK compatibility results, Docker smoke results, and code/spec review conclusions are recorded in the final handoff or PR/MR description.
