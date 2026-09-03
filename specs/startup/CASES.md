# Startup Functional Verification Cases

**English** | [简体中文](CASES.zh-CN.md)

These cases are executable acceptance supplements to `specs/startup/README.md`. By default, they use an offline fake Codex app-server and local loopback and do not access a real provider, cloud service, or user HOME. Real nginx/AIO/Codex container verification uses an explicit E2E flag.

## Execution Conventions

- Working directory: repository root.
- Minimum regression: `uv run --extra dev pytest -q tests/test_startup.py tests/test_startup_probe.py`.
- Entry-point integration: `HAAS_E2E=1 make test-integration`.
- Container-specific: `HAAS_E2E=1 HAAS_E2E_OPEN_SANDBOX=1 make test-e2e`. If the environment is not enabled, mark it `not_run`; it MUST NOT be claimed as passed.
- Evidence: retain pytest output, nginx syntax output, startup phase JSONL, and container smoke logs; do not commit temporary verification reports.

## Case List

| ID | Priority | Objective | Prerequisite/Command | Pass Criteria |
|----|----------|-----------|----------------------|---------------|
| ST-001 | P0 | nginx uniformly proxies ADK/HaaS entry points | fake sidecar + nginx config fixture; `pytest -q -k ST_001` | ADK paths and `/v1/haas/*` are both proxied to `127.0.0.1:8092`; no `/v1/codex-worker/*` route exists; nginx syntax passes |
| ST-002 | P0 | Separate health from ready | sidecar listening, Codex socket absent; `pytest -q -k ST_002` | `/health` returns a liveness structure; `/v1/haas/ready` returns a structured 503/`ready=false` |
| ST-003 | P0 | Unix socket + initialize/initialized readiness | fake Codex server; `pytest -q -k ST_003` | After the socket is connectable, initialize succeeds, and the initialized notification flushes successfully, the sidecar first publishes `ready=true` |
| ST-004 | P0 | initialize failure fails closed | fake server returns a JSON-RPC error; `pytest -q -k ST_004` | Health remains available; ready remains false; the northbound interface does not leak the native error payload |
| ST-005 | P0 | Incomplete initialized write fails closed | fake transport blocks notification flush; `pytest -q -k ST_005` | Ready remains false; a safe reason is produced after the bounded timeout; connection resources are released |
| ST-006 | P0 | Generation race prevents stale publication | start old/new generation probes concurrently; `pytest -q -k ST_006` | Late completion of the old probe cannot overwrite the new generation state or write back `ready=true` |
| ST-007 | P0 | Socket replacement causes ready fallback | replace socket/restart Codex after ready; `pytest -q -k ST_007` | Old-generation readiness is revoked; readiness recovers after the new generation completes the handshake |
| ST-008 | P0 | nginx does not fabricate ready | sidecar not ready; `pytest -q -k ST_008` | Externally visible nginx readiness matches the sidecar's structured result and does not return a fixed success body |
| ST-009 | P1 | Asynchronous warmup does not block ready | inject slow MCP/browser/skills/background task; `pytest -q -k ST_009` | Ready is published after the Codex handshake; background tasks may remain pending; ordinary background failure does not revoke readiness |
| ST-010 | P1 | Failure of a Codex safety hard dependency revokes ready | mark a background task as an execution-safety dependency; `pytest -q -k ST_010` | After the task fails, the sidecar publishes ready=false and a safe reason |
| ST-011 | P0 | Bounded timeout and retry | time out socket/connect/initialize separately; `pytest -q -k ST_011` | No indefinite wait; health responds; ready is false; retry count and duration are observable |
| ST-012 | P0 | SIGTERM drain | send SIGTERM while ready; `pytest -q -k ST_012` | Publish ready=false first, then reject new executions, stop background tasks, settle active turns, and exit with the specified status |
| ST-013 | P0 | Loopback and secretless operation | inspect listener addresses, logs, status, and events; `pytest -q -k ST_013` | sidecar/Codex/proxy are not publicly exposed; token/raw JSON-RPC/prompt/credential do not appear in output |
| ST-014 | P0 | Real container aggregate entry point | `HAAS_E2E=1 HAAS_E2E_OPEN_SANDBOX=1 make test-e2e` | nginx exposes health/ready externally; sidecar 8092 and AIO 8080 coexist; readiness is determined by a real Codex handshake |
| ST-015 | P1 | Startup latency budget | fake/real startup timing; `pytest -q -k ST_015` | Output nginx, sidecar, socket, initialize, and service-ready phase durations; the target is not met by skipping the handshake |

## Coverage Matrix

| Spec Requirement | Cases |
|------------------|-------|
| Sole nginx entry point and HaaS upstream | ST-001, ST-008, ST-014 |
| health/ready semantics and sidecar readiness ownership | ST-002, ST-003, ST-008, ST-012 |
| Unix socket + initialize + initialized | ST-003, ST-004, ST-005, ST-011, ST-014 |
| Generation validation and stale-publication protection | ST-006, ST-007 |
| Asynchronous startup and failure isolation | ST-009, ST-010 |
| Bounded timeout/retry/recovery | ST-004, ST-005, ST-007, ST-011 |
| Security, loopback, and secretless operation | ST-013 |
| Startup observation and latency target | ST-011, ST-015 |
| Graceful shutdown | ST-012 |

## Case Gates

- All P0 cases MUST pass before proceeding to Code Review and E2E.
- P1 cases MUST NOT be silently skipped. When the environment is unavailable, record `not_run`, the reason, alternative verification, and residual risk.
- Any case failure MUST return to the implementation phase; rerun the affected cases after the fix.
