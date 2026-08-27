# Observability 组件规格

Status: Draft
Last reviewed: 2026-08-26
Related specs: [HaaS Protocol](../haas-protocol/README.md), [Session Runtime](../session-runtime/README.md), [Event Log & SSE](../event-log-sse/README.md), [Security Boundary](../security-boundary/README.md)

## 1. 组件定位

Observability 提供 HaaS 的运行可见性：health、ready、status、结构化日志、metrics、trace、diagnostics 和验证证据。它帮助定位协议、adapter、session、SSE、model proxy、MCP 和 OpenSandbox AIO 的问题，但不拥有业务状态。

## 2. 来源与依据

| 来源 | 采用内容 |
|------|----------|
| `mpa-codex-worker` observability specs | health/ready/status、execution log、startup ready、secret redaction |
| ADK 2.0 compatibility | 兼容性验证报告是协议对齐证据 |
| OpenSandbox server docs | `/health`、runtime status、OTLP metrics、诊断 API |
| 本组件总览 | observability、metrics、diagnostics、conformance evidence |

## 3. 上游与下游关系

| 方向 | 对象 | 关系 |
|------|------|------|
| 上游 | HaaS Protocol | 暴露 health/ready/status/diagnostics |
| 上游 | 所有组件 | 发送结构化日志、metrics、trace spans |
| 下游 | Event Log | 关联 invocation/session/turn event |
| 下游 | Security Boundary | redaction 和安全字段策略 |
| 下游 | Verification reports | 保存测试和 conformance 证据 |

## 4. 职责边界

负责：

- 定义 liveness、readiness 和 status 的语义。
- 收集低基数 metrics。
- 生成结构化日志并应用 redaction。
- 关联 trace id、request id、harness id、session id、invocation id、turn id。
- 输出 diagnostics summary。
- 管理 conformance、E2E、coverage、review 报告格式。

不负责：

- 不修改 session/invocation/turn 状态。
- 不直接读取 secret value。
- 不直接执行 adapter 或 provider request。
- 不作为业务事实源；业务事实来自 Registry、Session Runtime、Event Log 或 Artifact Store。

## 5. 核心接口

### 5.1 Public Endpoints

| Method | Path | 说明 |
|--------|------|------|
| GET | `/v1/haas/health` | liveness，进程可响应 |
| GET | `/v1/haas/ready?scope=control` | 可接控制面请求 |
| GET | `/v1/haas/ready?scope=execution` | 可开始 harness turn |
| GET | `/v1/haas/status` | 运行状态摘要 |
| GET | `/v1/haas/diagnostics` | 脱敏诊断摘要 |

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

## 6. 数据模型

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
    "haasVersion": "2026-08-26",
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

## 7. 运行模型与状态机

```text
component emits structured signal
  -> redaction
  -> log/metric/span sink
  -> status aggregator snapshot
  -> diagnostics endpoint
  -> verification report references command/evidence
```

Health/ready states:

- `healthy` means HaaS HTTP process responds.
- `control_ready` means config/store/registry/control APIs can run.
- `execution_ready` means selected adapter and required runtime dependencies can start turns.
- `degraded` means non-critical capability is unavailable but core request handling still works.

## 8. 安全与权限

- Metrics labels must be low-cardinality and secret-free.
- Trace attributes must not contain raw prompt, file contents, headers or tokens.
- Diagnostics require admin/debug scope when they include per-session details.
- Verification reports must redact commands or outputs that include credentials.
- Public status may include fingerprints and safe reason, not raw values.

## 9. 可观测性

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

Baseline logs:

- JSON structured logs to stdout/stderr by default.
- Every log line should carry `traceId` when request-scoped.
- Access logs must use route templates, not raw path with query strings.

## 10. 失败与恢复

| 场景 | 行为 |
|------|------|
| metrics exporter unavailable | fail open; readiness unaffected unless explicitly configured |
| log sink unavailable | fallback to stderr; record degraded status |
| trace exporter unavailable | fail open and increment exporter failure metric |
| diagnostics collection timeout | return partial diagnostics with `haas_diagnostics_partial` |
| redaction failure | fail closed before writing sensitive data |
| conformance report missing | release readiness blocked for protocol changes |

## 11. 测试计划与验收

- Unit：redaction on log fields、low-cardinality metric labels、ready/status snapshot builders。
- Integration：health/ready/status endpoints with fake component states。
- E2E：run a Codex task and verify trace ids connect request, events, response and logs。
- Failure injection：metrics exporter failure and diagnostics timeout do not block core execution。
- Release evidence：ADK compatibility result, Docker smoke result and code/spec review conclusions are recorded in the final handoff or PR/MR description.
