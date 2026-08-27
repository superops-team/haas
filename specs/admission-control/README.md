# Admission Control 组件规格

Status: Draft
Last reviewed: 2026-08-26
Related specs: [HaaS Protocol](../haas-protocol/README.md), [Session Runtime](../session-runtime/README.md), [Observability](../observability/README.md)

## 1. 组件定位

Admission Control 是 HaaS 服务化的准入边界。它在请求进入执行前，按 principal/tenant/workspace 维度决策：配额是否允许、速率是否超限、部署并发是否已满、是否入队等待。它是 `/run`、`/run_sse`、session mutation 与 harness mutation 的共同前置关卡。

它回答的核心问题：HaaS 是多租户托管服务，不能只看单 session 互斥；跨请求的公平性、容量和配额需要一个统一 owner。Session 级的 `session_busy` 互斥仍归 Session Runtime，本组件只做跨请求准入。

## 2. 来源与依据

| 来源 | 采用内容 |
|------|----------|
| `mpa-codex-worker` 经验 | sidecar 对并发 session、任务队列和限流的需求 |
| ADK API server | 无内置配额/队列；HaaS 必须自行补充服务化约束 |
| Observability spec | status 中 `queues` 摘要的 owner 归本组件 |

## 3. 上游与下游关系

| 方向 | 对象 | 关系 |
|------|------|------|
| 上游 | HaaS Protocol | `/run`、`/run_sse`、session/harness mutation 前的准入调用 |
| 上游 | Harness Registry | 查询 harness 执行成本与并发上限声明 |
| 下游 | Session Runtime | 放行后创建 invocation/session |
| 下游 | Observability | 报告队列深度、限流、准入决策 |

## 4. 职责边界

负责：

- 定义并执行配额（每 principal/tenant/workspace 的 max active runs、max sessions、吞吐）。
- 定义并执行速率限制（窗口/令牌桶，按 principal + resource 维度）。
- 定义并执行部署级并发上限与 bounded 队列。
- 出队与超时（入队超时返回 `429` 或 `503`）。
- 输出低保基数 metrics 和 safe reason。

不负责：

- 不处理同 session 单活跃 turn 互斥（Session Runtime 的 `session_busy`）。
- 不解析 harness 原生协议。
- 不保存 secret。

## 5. 核心接口

```python
async def admit_run(ctx: RequestContext, req: AdmissionInput) -> AdmissionDecision: ...
async def release_run(ctx: RequestContext, run_id: str) -> None: ...
async def admit_harness_mutation(ctx: RequestContext, action: str) -> AdmissionDecision: ...
async def snapshot_queues(ctx: RequestContext) -> QueueSnapshot: ...
```

## 6. 数据模型

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

拒绝时：

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

## 7. 运行模型与状态机

```text
admission requested
  -> rate limit check (fail -> 429 with retry-after)
  -> quota check (fail -> 429 quota_exceeded)
  -> deployment concurrency check (free -> admit; full -> queue)
  -> queue with bounded depth + timeout (overflow -> 503 queue_full)
  -> lease granted
  -> run finishes -> lease released
```

Unix 语义：

- 速率与配额必须基于部署内共享 store，不能只靠单进程内存。
- 队列 FIFO，超时未出队返回 `haas_queue_timeout`。
- 拒绝与出队都必须可观测、可解释。

## 8. 安全与权限

- 配额维度只能使用脱敏 principal/tenant hash 或安全 id。
- 不得把 quota 状态、队列内容、其他 tenant 的运行信息暴露在 error detail 中。
- Metrics label 低基数，不包含 user id、prompt、path。

## 9. 可观测性

Metrics：

- `haas_admission_decision_total{resource,decision,reason}`
- `haas_admission_active_total{resource,scope}`
- `haas_admission_queue_depth{resource}`
- `haas_admission_queue_wait_ms{resource,status}`

Logs：

- `haas.admission.allowed`
- `haas.admission.rate_limited`
- `haas.admission.queued`
- `haas.admission.queue_timeout`

## 10. 失败与恢复

| 场景 | 行为 |
|------|------|
| store 不可用 | fail closed；正在运行的 run 继续，新 run 拒绝 `503` |
| 队列满 | `503 queue_full` |
| 入队超时 | `429 haas_queue_timeout` |
| lease 丢失（进程重启） | 重算 active run 并释放孤儿 lease，或 fail closed |

## 11. 测试计划与验收

- Unit：速率窗口、令牌桶、配额上限、队列 FIFO/bounded/timeout。
- Integration：并发 `/run` 超过部署上限时入队/拒绝路径正确；`retry-after` 头正确。
- Recovery：sidecar 重启后 quota/queue 状态一致，不重复放行。
- Observability：所有决策与队列深度指标低基数。
