# Startup Component Specification

**English** | [简体中文](README.zh-CN.md)

Status: Draft
Last reviewed: 2026-09-02
Change ID: haas-standard-startup
Related specs: [Architecture](../architecture/README.md), [Container Runtime](../container-runtime/README.md), [Codex App-Server Adapter](../codex-app-server-adapter/README.md), [HaaS Protocol](../haas-protocol/README.md), [Observability](../observability/README.md)

## 1. Component Role

Startup defines Lite, AIO and host control-sidecar orchestration. nginx/AIO instructions in this document apply only to the AIO image; Lite skips those phases and uses its dedicated minimal entrypoint. The host control sidecar listens on loopback, standalone Lite publishes its container 8092 listener to host loopback, and delegated workers use only private service transport (Container Runtime §5.3). HaaS owns northbound readiness.

`ready?scope=control` depends only on identity/config/store initialization and is available with an empty registry or unavailable Codex. `ready?scope=execution` (also the default `/ready` scope) adds adapter handshake, runtime and isolation checks. The Codex/overall-ready rules below refer only to execution scope. A host delegation controller checks its Docker execution path instead of requiring an unrelated host Codex process. Capability discovery is readable once control is ready, including unavailable features; it does not require execution readiness.

The goal is to make the minimal service entry point available as quickly as possible without misclassifying non-critical capabilities as ready or allowing asynchronously initialized capabilities to block the overall service.

## 2. Sources and Rationale

| Source | Adopted Content |
|--------|-----------------|
| HaaS AGENTS.md | nginx/AIO container boundaries, `/health`/`/ready` semantics, initial Codex app-server constraints, secretless operation, and recoverability |
| Container Runtime | AIO `/opt/gem/run.sh`, port ownership, sidecar criticality, and process and shutdown contracts |
| Codex App-Server Adapter | Unix socket transport, `initialize`/`initialized` handshake, generation, and recovery semantics |
| Local `mpa-codex-worker` configuration reference | nginx upstream mounting, supervisor priority, separate sidecar/Codex startup, and background warmup |

## 3. Upstream and Downstream Relationships

| Direction | Object | Relationship |
|-----------|--------|--------------|
| Upstream | Container runtime / `/opt/gem/run.sh` | Process startup, signal forwarding, and AIO base services |
| Upstream | Config | Ports, sockets, timeouts, and startup policy |
| Downstream | nginx | The sole external HTTP/SSE entry point; forwards to the HaaS sidecar |
| Downstream | HaaS sidecar | API, aggregated Codex readiness, and ready signal |
| Downstream | Codex app-server adapter | Codex startup/connection and standard handshake probe |
| Asynchronous downstream | AIO optional services, Model Proxy, MCP, Browser, Skills, Artifact warmup | MUST NOT block overall ready status by default |

## 4. Responsibilities and Boundaries

Responsibilities:

- Generate and validate the nginx configuration, mounting the HaaS northbound API uniformly on the sidecar loopback upstream.
- Plan the startup order and parallelism of nginx, the sidecar, and the Codex adapter.
- Define the sole condition for overall readiness: the sidecar is live and the Codex readiness probe has completed.
- Define the actual validation steps, timeouts, retries, and state projection for the Codex readiness probe.
- Place non-critical initialization in observable background tasks. Ordinary optional tasks MUST NOT change a published ready state. Only failure of a task explicitly declared as a hard dependency for safe Codex execution MAY revoke readiness.
- Define startup phases, durations, failure reasons, and graceful shutdown behavior.

Non-responsibilities:

- Does not implement nginx, Codex JSON-RPC, or OpenSandbox AIO itself.
- Does not allow nginx to determine Codex readiness itself; nginx only proxies the sidecar's structured state.
- Does not include successful model provider initialization, full MCP discovery, browser initialization, skills downloads, or artifact cleanup in overall readiness by default.
- Does not expose native Codex endpoints, thread IDs, turn IDs, or JSON-RPC payloads upstream.

## 5. Core Interfaces

### 5.1 External Entry Point

nginx is the container's sole external listener and reuses the AIO-reserved `8080` listener. The sidecar, Codex socket, model proxy `18080`, and MCP proxy `18081` bind only to loopback or a Unix socket by default. AIO internal routes continue to be served by the same nginx server.

nginx MUST proxy the following HaaS surfaces without changing paths or methods:

- ADK-compatible: `/list-apps`, `/run`, `/run_sse`, and `/apps/{app}/users/{user}/sessions/{session}`.
- HaaS native: `/v1/haas/*`, including at least `/v1/haas/health`, `/v1/haas/ready`, and `/v1/haas/status`.

Proxy requirements:

- The upstream MUST point to `http://127.0.0.1:8092` by default; it MUST NOT point to a public network or the Codex socket.
- SSE routes MUST use HTTP/1.1, disable response buffering, set `Cache-Control: no-cache`, and permit a long read timeout.
- Streaming requests forward `Host`, `X-Real-IP`, `X-Forwarded-For`, and `X-Forwarded-Proto`; WebSocket is used only for explicitly declared internal/adapter routes.
- The nginx configuration MUST pass a syntax check before startup. On failure, the container MUST exit nonzero and MUST NOT use a partial configuration.
- nginx MUST NOT fabricate a service-ready response. `/health` and `/ready` are both supplied as structured results by the sidecar and passed through by nginx.

### 5.2 Sidecar Readiness Contract

The sidecar is the source of truth for readiness. `GET /v1/haas/health` returns a liveness status when the sidecar can respond and does not require Codex to be ready.

`GET /v1/haas/ready` returns `ready=true` only when all of the following conditions hold:

1. The sidecar HTTP server is listening and can execute the readiness handler.
2. The Codex adapter readiness probe has completed.
3. The Codex generation used by the probe is still the current active generation.
4. The sidecar has not entered `draining` or `stopped`.

When Codex is not ready, the endpoint returns a structured non-2xx not-ready response. The recommended status code is `503`, using the existing `haas_adapter_unavailable` error code. Callers MUST NOT be required to parse human-readable text.

### 5.3 Codex Readiness Probe

The standard probe is fixed as follows:

```text
start or connect to active Codex app-server
  -> connect to Unix socket
  -> create dedicated readiness connection
  -> send JSON-RPC initialize request
  -> validate successful initialize response
  -> send initialized notification
  -> mark connection handshake-complete
  -> publish codex_ready for observed generation
```

Rules:

- The existence of a Unix socket does not indicate readiness. Readiness requires the socket to be connectable, `initialize` to succeed, and `initialized` to have been sent successfully.
- The readiness connection MUST have bounded connect, initialize, and total timeouts and MUST NOT wait indefinitely.
- The probe connection is isolated from the lifecycle of actual session/turn connections and MUST NOT be treated as a business session.
- If `initialize` fails or times out, the protocol version does not match, the socket is replaced, or the generation changes, ready MUST fall back to false.
- Before publishing probe completion, the implementation MUST atomically verify that its generation is still the active generation. A late probe result from an old generation MUST NOT overwrite a newer `ready=false` or publish `ready=true`.
- `initialized` is a notification and has no JSON-RPC response. It is considered “complete” when the notification has been written to the readiness connection according to the protocol and successfully flushed, after which the connection is marked handshake-complete.
- The probe MUST NOT execute a model request, `thread/start`, `turn/start`, full MCP discovery, or a user prompt.
- Native Codex errors go to safe adapter/observability diagnostics. The northbound interface sees only structured HaaS readiness/errors.

## 6. Data Model and State Machine

### 6.1 StartupState

```json
{
  "phase": "service_ready",
  "generation": 1,
  "sidecar": {"status": "listening", "port": 8092},
  "codex": {
    "status": "ready",
    "transport": "unix_websocket",
    "handshake": "initialize_initialized",
    "lastProbeAt": "2026-09-02T00:00:00Z"
  },
  "background": {"pending": ["mcp_discovery", "browser_warmup"], "failed": []}
}
```

Actual absolute socket paths, authentication tokens, raw JSON-RPC, prompts, and provider credentials MUST NOT appear in externally visible status, logs, or events.

### 6.2 Startup Phases

```text
process_starting
  -> nginx_configuring
  -> nginx_listening
  -> sidecar_starting
  -> sidecar_listening
  -> codex_starting
  -> codex_probing
  -> service_ready
  -> degraded_or_restarting
  -> draining
  -> stopped
```

`service_ready` is the only phase in which `/v1/haas/ready` may return `ready=true`. `nginx_listening`, `sidecar_listening`, and `codex_probing` do not indicate overall readiness.

## 7. Startup Orchestration and Latency Budget

The critical path contains only local operations with bounded latency:

```text
prepare runtime dirs
  || start AIO /opt/gem/run.sh
  || start Codex app-server
  -> validate/render nginx config
  -> start nginx
  -> start sidecar
  -> sidecar starts Codex readiness probe
  -> Unix socket + initialize + initialized
  -> sidecar publishes ready=true
```

AIO, nginx, the sidecar, and Codex app-server SHOULD start in parallel. Only nginx configuration validation, sidecar responsiveness, and the Codex readiness probe form the overall readiness dependency. Full AIO readiness MUST NOT be a prerequisite for sidecar readiness.

Services disabled in AIO through the official `DISABLE_CODE_SERVER` / `DISABLE_JUPYTER` / `DISABLE_NODEJS_REPL` settings (see [Runtime Trim](../runtime-trim/README.md)) are not on the critical path and do not participate in the overall readiness decision. Their absence MUST NOT change nginx, sidecar, or Codex readiness behavior. Browser/VNC continues to warm up asynchronously as an optional capability.

The startup implementation MUST:

- Record monotonic start/end, duration, status, and safe reason for each phase.
- Use bounded timeouts and finite retries. During retries, `/health` MAY succeed while `/ready` remains false.
- Exclude network downloads, provider requests, remote MCP probes, browser initialization, skill materialization, and full workspace scans from the critical path.
- Use an independent task group and cancellation boundary for background tasks. Incomplete background tasks MUST NOT block the HTTP server or readiness.
- Update capability state and safe diagnostics when a background task fails. Ordinary optional tasks MUST NOT revoke published readiness; only failure of an explicit hard dependency for safe Codex execution causes the sidecar to fall back to ready=false.

`startup_ready_duration_ms` is measured from container startup until the sidecar first publishes `ready=true`. The default target is P95 ≤ 5 seconds locally when there are no abnormal cold-cache conditions. The target MUST NOT be achieved by skipping the Codex handshake.

At minimum, observe `nginx_listen_ms`, `sidecar_listen_ms`, `codex_socket_connect_ms`, `codex_initialize_ms`, and `service_ready_ms`.

## 8. Security and Permissions

- nginx proxies only permitted ADK/HaaS paths and MUST NOT turn a caller-supplied URL into a proxy target.
- Codex Unix socket permissions are restricted to the HaaS runtime user/group. The probe MUST NOT bypass isolation through a public TCP listener.
- The readiness token may come only from a runtime secret handle or restricted file and MUST NOT enter command lines, logs, status, or events.
- nginx access logs, sidecar logs, startup timing, and status are redacted. Raw JSON-RPC, raw prompts, complete tool arguments, and provider credentials MUST NOT be recorded.
- nginx retains security headers such as `X-Content-Type-Options: nosniff`. SSE MUST NOT disable authentication or scope checks.

## 9. Observability

Events: `haas.startup.phase_started`, `haas.startup.phase_completed`, `haas.startup.ready_published`, `haas.startup.ready_withheld`, `haas.startup.background_failed`, `haas.startup.draining`.

Metrics:

- `haas_startup_phase_duration_ms{phase,status}`
- `haas_startup_ready_total{status}`
- `haas_startup_ready_duration_ms`
- `haas_startup_codex_probe_total{status,reason}`
- `haas_startup_background_task_total{task,status}`

Events and metrics use only low-sensitivity fields such as safe reason, phase, status, generation, and duration.

## 10. Failure and Recovery

| Scenario | Behavior |
|----------|----------|
| Invalid nginx configuration | Startup fails; the container exits nonzero and does not use an old or partial configuration |
| nginx started but sidecar not listening | `/health` fails or returns an upstream error; `/ready` MUST NOT be true |
| Sidecar listening but Codex socket not created | `/health` succeeds; `/ready` returns a structured 503; bounded retries continue |
| Socket connectable but initialize fails | Ready remains false; the adapter is degraded/restarting and rebuilds the generation according to policy |
| initialized not completed | Ready remains false; process existence is not promoted to readiness |
| Codex generation changes | Immediately revoke readiness for the old generation and probe again |
| Optional background task fails | Record a safe reason and update capability state; do not block the control API; if safe Codex execution is affected, readiness falls back to false |
| Sidecar exits | The container MUST exit with a nonzero code to avoid showing Up while the API is unavailable |
| SIGTERM | First publish ready=false and stop accepting new execution requests, then cancel/settle active turns, flush the event log, and stop background tasks and child processes |

## 11. Test Plan and Acceptance Criteria

### 11.1 Configuration and Entry Point

- The nginx syntax check passes. HaaS ADK and `/v1/haas/*` paths, methods, headers, and SSE streaming behavior conform to the spec.
- The sidecar, Codex socket, AIO `8080`, and proxy loopback interfaces are not exposed incorrectly.
- The nginx upstream does not point directly to the Codex socket, and nginx does not fabricate readiness.

### 11.2 Readiness Contract

- Sidecar listening but socket absent: health passes and ready fails.
- Socket present but not connectable: ready fails.
- Socket connectable but initialize returns an error: ready fails.
- initialize succeeds but initialized is incomplete: ready fails.
- initialize + initialized complete: the sidecar publishes ready=true and nginx exposes the same structured result externally.
- Codex generation restart or socket replacement: ready first falls back, then recovers after the new generation completes the handshake.

### 11.3 Startup Latency and Asynchronous Behavior

- Measure each phase and time to first ready using a fake Codex app-server.
- Inject slow AIO, MCP, browser, and skills behavior and a failing background task to prove that they do not block Codex readiness.
- Inject sidecar/Codex startup failures, timeouts, retries, and SIGTERM to verify states, error codes, nonzero exit, and drain behavior.
- Run a real OpenSandbox AIO + Codex app-server container smoke test to verify coexistence of the nginx aggregate entry point, sidecar readiness, and AIO `8080`.

### 11.4 Security

- Secret scanning and negative log assertions verify that tokens, credentials, raw JSON-RPC, prompts, and complete tool arguments do not enter configuration, logs, status, events, or artifacts.

## 12. Task Breakdown

- P0: nginx HaaS upstream, unified `/health`/`/ready` proxy, and configuration syntax gate.
- P0: sidecar readiness state machine and structured ready response.
- P0: dedicated Codex Unix socket + `initialize`/`initialized` probe.
- P0: startup phase timing, bounded retries, generation invalidation, and SIGTERM drain.
- P1: background task registry, asynchronous capability state, failure isolation, and diagnostic status.
- P1: fake harness startup-latency suite and real container smoke test.
