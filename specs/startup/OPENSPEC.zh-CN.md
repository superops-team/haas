# Startup OpenSpec 任务拆解

[English](OPENSPEC.md) | **简体中文**

Change ID: `haas-standard-startup`
Source: [README.zh-CN.md](README.zh-CN.md)
Verification: [CASES.zh-CN.md](CASES.zh-CN.md)

## Plan

```text
S1 startup contracts/models
  -> S2 Codex readiness probe
  -> S3 sidecar readiness integration
  -> S4 nginx entrypoint/config
  -> S5 async warmup and lifecycle
  -> S6 functional cases + container E2E
```

S2 and S4 can be developed in parallel after S1; S3 depends on S1/S2; S5 depends on S3; S6 depends on all P0 implementation tasks.

## Tasks

| ID | Task | Scope | Acceptance | Cases | Dependency |
|----|------|-------|------------|-------|------------|
| ST-T1 | Startup state contract | Define typed phases, generation, safe readiness state, phase timing and public response projection. | `service_ready` is the only ready phase; no secret/native payload projection; unit tests cover transitions. | ST-002, ST-006, ST-007, ST-015 | none |
| ST-T2 | Dedicated Codex probe | Implement Unix socket connect plus initialize success plus initialized notification write/flush; bounded timeout and resource cleanup. | Probe returns ready only after handshake completion; failures stay fail-closed; notification does not wait for response. | ST-003, ST-004, ST-005, ST-011 | ST-T1 |
| ST-T3 | Generation-safe publication | Guard probe completion with active-generation check and atomically publish/revoke readiness. | Stale old-generation result can never write ready=true; socket replacement revokes old readiness. | ST-006, ST-007 | ST-T1, ST-T2 |
| ST-T4 | Sidecar readiness API | Integrate startup state with `/v1/haas/health`, `/v1/haas/ready`, aliases and structured 503/error behavior. | Health remains available while Codex is unavailable; ready reflects sidecar + Codex + drain state only. | ST-002, ST-003, ST-004, ST-008, ST-012 | ST-T1, ST-T2, ST-T3 |
| ST-T5 | nginx HaaS entrypoint | Add validated nginx config/upstream for ADK and HaaS paths, SSE settings, forwarded headers and syntax gate. | Nginx is sole external listener, proxies to sidecar, does not fake ready, and no legacy shim is added. | ST-001, ST-008, ST-013 | ST-T4 |
| ST-T6 | Async startup registry | Start AIO/Codex/sidecar/nginx without unnecessary serial waits; run optional warmups in cancellable background tasks with capability status. | Slow optional tasks do not block ready; ordinary failure does not revoke ready; execution-safety dependency failure does. | ST-009, ST-010, ST-015 | ST-T4 |
| ST-T7 | Drain and restart lifecycle | Implement ready revocation before SIGTERM drain, stop accepting work, cancel/settle turns, stop background tasks and re-probe new generation. | Shutdown and restart are bounded, observable and fail closed. | ST-007, ST-012 | ST-T3, ST-T4, ST-T6 |
| ST-T8 | Security/observability | Add safe startup events/metrics, secretless redaction assertions, loopback checks and phase timing. | No token/raw JSON-RPC/prompt/credential in logs/status/events; required startup phase and ready latency metrics are emitted. | ST-013, ST-015 | ST-T1, ST-T4 |
| ST-T9 | Functional and container verification | Execute all P0/P1 cases, real nginx/container smoke and record evidence in delivery summary. | All P0 cases pass; P1 pass or explicit `not_run` risk; real container path verifies nginx + sidecar + AIO + Codex. | ST-001..ST-015 | ST-T1..ST-T8 |

## Alignment gate

- Every P0/P1 requirement in `README.md` maps to at least one Case in `CASES.md`.
- Every implementation task maps to one or more Cases; ST-T9 is the execution task for all Cases.
- No task introduces `/v1/codex-worker/*`, public Codex JSON-RPC, mutable readiness shortcuts, or provider/MCP calls in the critical path.
- Any task that changes the ADK/HaaS public schema must update the relevant protocol spec and OpenAPI before implementation.
- Container and real Codex validation remain explicitly gated; default tests stay offline.
