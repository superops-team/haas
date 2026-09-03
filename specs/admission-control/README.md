# Admission Control Component Specification

**English** | [简体中文](README.zh-CN.md)

Status: Draft
Last reviewed: 2026-08-26
Related specs: [HaaS Protocol](../haas-protocol/README.md), [Session Runtime](../session-runtime/README.md), [Observability](../observability/README.md)

## 1. Component Role

Admission Control is the admission boundary for HaaS as a service. Before a request enters execution, it decides along principal, tenant, and workspace dimensions whether quota permits the request, whether the rate limit has been exceeded, whether deployment concurrency is exhausted, and whether the request should wait in a queue. It is the common prerequisite gate for `/run`, `/run_sse`, session mutations, and harness mutations.

It addresses a core requirement: HaaS is a multi-tenant hosted service, so per-session mutual exclusion alone is insufficient. Fairness, capacity, and quotas across requests require a single owner. Session-level `session_busy` mutual exclusion remains the responsibility of Session Runtime; this component handles only cross-request admission.

## 2. Sources and Rationale

| Source | Adopted concepts |
|--------|------------------|
| `mpa-codex-worker` experience | Sidecar requirements for concurrent sessions, task queues, and rate limiting |
| ADK API server | No built-in quotas or queues; HaaS MUST supply service-level constraints |
| Observability spec | This component owns the `queues` summary in status |

## 3. Upstream and Downstream Relationships

| Direction | Component | Relationship |
|-----------|-----------|--------------|
| Upstream | HaaS Protocol | Admission calls before `/run`, `/run_sse`, and session/harness mutations |
| Upstream | Harness Registry | Queries harness execution cost and declared concurrency limits |
| Downstream | Session Runtime | Creates the invocation/session after admission |
| Downstream | Observability | Reports queue depth, rate limiting, and admission decisions |

## 4. Responsibility Boundaries

Responsibilities:

- Define and enforce quotas: maximum active runs, maximum sessions, and throughput per principal, tenant, and workspace.
- Define and enforce rate limits using windows or token buckets, scoped by principal and resource.
- Define and enforce deployment-level concurrency limits and bounded queues.
- Handle dequeue and timeout; a queue timeout returns `429` or `503`.
- Emit low-cardinality metrics and safe reasons.

Non-responsibilities:

- Does not handle single-active-turn mutual exclusion within a session; that is Session Runtime's `session_busy`.
- Does not parse native harness protocols.
- Does not store secrets.

## 5. Core Interfaces

```python
async def admit_run(ctx: RequestContext, req: AdmissionInput) -> AdmissionDecision: ...
async def release_run(ctx: RequestContext, run_id: str) -> None: ...
async def admit_harness_mutation(ctx: RequestContext, action: str) -> AdmissionDecision: ...
async def snapshot_queues(ctx: RequestContext) -> QueueSnapshot: ...
```

## 6. Data Model

### 6.1 AdmissionInput

```json
{
  "principalHash": "sha256:abc",
  "tenantId": "tenant_1",
  "workspaceId": "workspace_1",
  "appName": "chrn_codex_default",
  "resource": "run",
  "estimatedCost": 1
}
```

### 6.2 AdmissionDecision

```json
{
  "allowed": true,
  "queued": false,
  "leaseId": "adm_abc",
  "limitName": "runs_per_tenant",
  "remaining": 9
}
```

When rejected:

```json
{
  "allowed": false,
  "code": "haas_rate_limited",
  "safeReason": "rate_limit_exceeded",
  "retryAfterMs": 5000
}
```

### 6.3 QueueSnapshot

```json
{
  "runQueueDepth": 3,
  "runQueueLimit": 100,
  "activeRuns": 12,
  "activeRunsLimit": 20
}
```

## 7. Runtime Model and State Machine

```text
admission requested
  -> rate limit check (fail -> 429 with retry-after)
  -> quota check (fail -> 429 quota_exceeded)
  -> deployment concurrency check (free -> admit; full -> queue)
  -> queue with bounded depth + timeout (overflow -> 503 queue_full)
  -> lease granted
  -> run finishes -> lease released
```

Unix semantics:

- Rate limits and quotas MUST use a store shared within the deployment; single-process memory alone is insufficient.
- The queue is FIFO. If a request is not dequeued before its timeout, return `haas_queue_timeout`.
- Rejections and dequeues MUST be observable and explainable.

## 8. Security and Authorization

- Quota dimensions MUST use only a redacted principal/tenant hash or a safe ID.
- Error details MUST NOT expose quota state, queue contents, or run information from other tenants.
- Metric labels MUST be low-cardinality and MUST NOT contain user IDs, prompts, or paths.

## 9. Observability

Metrics:

- `haas_admission_decision_total{resource,decision,reason}`
- `haas_admission_active_total{resource,scope}`
- `haas_admission_queue_depth{resource}`
- `haas_admission_queue_wait_ms{resource,status}`

Logs:

- `haas.admission.allowed`
- `haas.admission.rate_limited`
- `haas.admission.queued`
- `haas.admission.queue_timeout`

## 10. Failure and Recovery

| Scenario | Behavior |
|----------|----------|
| Store unavailable | Fail closed; running runs continue, and new runs are rejected with `503` |
| Queue full | `503 queue_full` |
| Queue timeout | `429 haas_queue_timeout` |
| Lease lost due to process restart | Recalculate active runs and release orphaned leases, or fail closed |

## 11. Test Plan and Acceptance Criteria

- Unit: rate windows, token buckets, quota limits, and FIFO/bounded/timeout queue behavior.
- Integration: when concurrent `/run` requests exceed the deployment limit, queue and rejection paths behave correctly; the `retry-after` header is correct.
- Recovery: quota/queue state remains consistent after a sidecar restart, with no duplicate admission.
- Observability: all decision and queue-depth metrics are low-cardinality.
