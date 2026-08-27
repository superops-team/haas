# Architecture 组件规格

Status: Draft
Last reviewed: 2026-08-26

## 1. 组件定位

Architecture 规格定义 HaaS 的系统级边界、依赖方向、事实归属和首期落地顺序。它是 `specs/` 下其他组件规格的总设计依据。

HaaS 采用 sidecar-first 架构，运行时语言为 Python + FastAPI：

```text
Client / Manager / SDK / CLI / ADK web UI
  |
  | ADK 2.0 HTTP JSON + SSE
  v
HaaS Sidecar API (FastAPI)
  |
  +-- Protocol Mapper (ADK <-> internal)
  +-- Harness Registry
  +-- Session Runtime
  +-- Admission Control
  +-- Event Log & SSE Replay
  +-- Policy Controller
  +-- Artifact Store
  +-- Security Boundary
  +-- Observability
  |
  +-- Model Proxy ---------> Model providers
  +-- MCP / Tool / Skill Runtime --> MCP servers / local tools / skills
  |
  v
Harness Adapter Interface
  |
  +-- Codex app-server adapter  (first implementation)
  +-- Pi adapter                (future)
  +-- OpenCode adapter          (future)
  +-- AMP adapter               (future)
  |
  v
Sandbox Runtime (OpenSandbox sandbox/execd/credential vault projection)
  |
  v
OpenSandbox AIO container runtime
```

核心分层：

1. 上游只依赖 ADK 2.0 协议层，不依赖任何 harness 原生协议。
2. `harness adapter` 是完整 agent runtime，不是 LLM provider。
3. `configured harness` 是执行能力单位，`appName` 即 configured harness `id`。
4. `Sandbox Runtime` 把各 harness 的执行 sandbox 统一投影到 OpenSandbox AIO 的 sandbox/execd/credential vault——这是多 harness 标准化的运行时承载体，不是只把 AIO 当 base image。
5. `Admission Control` 负责服务化的配额、限流、并发与队列准入。

## 2. 来源与依据

| 来源 | 采用内容 |
|------|----------|
| ADK 2.0 REST API | `/list-apps`、`/run`、`/run_sse`、session 路径、Event schema、SSE framing |
| `mpa-codex-worker` 经验 | sidecar API、Codex app-server、event log、SSE replay、session registry、secretless runtime |
| Codex app-server 当前行为 | `initialize`、`initialized`、`thread/start`、`thread/resume`、`turn/start`、`turn/interrupt` |
| OpenSandbox AIO | AIO image、port `8080`、`/opt/gem/run.sh`、sandbox lifecycle、execd、credential vault、egress policy |

## 3. 上游与下游关系

| 方向 | 对象 | 关系 |
|------|------|------|
| 上游 | Client / Manager / SDK / CLI | 只依赖 ADK 2.0 HTTP/SSE |
| 下游 | HaaS Protocol | 定义 public contract |
| 下游 | Harness Registry | 管理 configured harness（ADK app） |
| 下游 | Session Runtime | 管理 session/invocation/turn |
| 下游 | Admission Control | 配额、限流、并发、队列准入 |
| 下游 | Harness Adapter | 隔离具体 harness |
| 下游 | Sandbox Runtime | 统一投影执行 sandbox 到 OpenSandbox |
| 下游 | Container Runtime | 承接 OpenSandbox AIO 镜像与进程拓扑 |
| 下游 | Security Boundary | 约束所有跨边界数据 |

## 4. 职责边界

负责：

- 定义系统级组件边界和依赖方向。
- 定义 ADK 主协议、HaaS native extension、legacy shim 的关系。
- 定义 P0/P1/P2 首期落地顺序。
- 明确每个事实的 authority owner。
- 明确负路径、恢复策略和验证门禁。

不负责：

- 不替代具体组件 spec。
- 不描述运行时代码实现细节。
- 不保存临时调研或验证日志。

## 5. 核心接口

本组件不暴露运行时 API。它定义架构上的公共入口：

| Surface | Path | Owner |
|---------|------|-------|
| ADK-compatible API | `/list-apps`、`/run`、`/run_sse`、`/apps/{app}/users/{user}/sessions/{sid}` | HaaS Protocol |
| HaaS native API | `/v1/haas/*` | HaaS Protocol |
| Legacy sidecar shim | `/v1/codex-worker/*` | HaaS Protocol |
| Adapter interface | internal Python async interface | Harness Adapter |
| Sandbox projection | internal SandboxRuntime API | Sandbox Runtime |
| Container entrypoint | `/opt/haas/run.sh` | Container Runtime |

## 6. 数据模型

关键对象：

| Object | Public? | Authority | Notes |
|--------|---------|-----------|-------|
| Harness | yes | Harness Registry | configured harness，`id`=ADK `appName`，`base` 开放字符串 |
| Run/Invocation | yes | Session Runtime | ADK 一次 `/run`/`/run_sse`，`invocationId` |
| Session | yes | Session Runtime | `(appName, userId, sessionId)` 三元组唯一 |
| Turn | internal | Session Runtime | adapter 执行单元，首期与 invocation 一一对应 |
| Event | yes | Event Log & SSE | ADK `Event` 投影，invocation-scoped |
| File | yes | Artifact Store | input files and produced artifacts |
| Policy | internal/public summary | Policy Controller | effective runtime constraints |
| RuntimeToken | internal | Security Boundary / Model Proxy | short TTL scoped token |

## 7. 运行模型与状态机

```text
request received
  -> protocol/auth/scope validation
  -> appName resolution (harness id/name)
  -> admission control (quota/rate/queue)
  -> session/run admission
  -> policy compilation
  -> sandbox projection (workspace/network/tool -> OpenSandbox)
  -> adapter execution
  -> event append + ADK projection
  -> invocation finalization
  -> artifact publication
```

Readiness model:

```text
process alive -> health ok
control plane initialized -> control ready
selected adapter ready + sandbox runtime ready -> execution ready
optional MCP/skill/browser warmup -> capability ready
```

## 8. 安全与权限

- Public API is authenticated except discovery/health/readiness probes.
- Object scope is enforced on every read, write, cancel, delete and artifact access；`userId`/`sessionId` 均按 principal scope 隔离。
- Secretless applies before adapter execution；provider key 进 OpenSandbox credential vault，harness 只拿短 token。
- Adapter native protocol data is never public by default.
- Sandbox isolation 是运行时硬边界；adapter 自带的 harness sandbox 只能在其内运行，不替代 HaaS 边界。

## 9. 可观测性

System status must be able to answer:

- Which ADK protocol version and HaaS native version are served.
- Which harness bases/apps are installed and ready.
- Which adapters are degraded and why.
- How many sessions, invocations and turns are active.
- Whether event log, model proxy, MCP proxy, sandbox runtime and AIO service are healthy.
- Which conformance/test gates were last run.

## 10. 失败与恢复

| Failure | Recovery |
|---------|----------|
| Public protocol incompatible | reject by schema/header, do not best-effort parse |
| Adapter unavailable | mark selected harness unavailable and fail new runs |
| Accepted run loses stream client | continue server-side and persist terminal state |
| Session runtime loses process | recover from store or mark non-resumable |
| Sandbox instance fails | recreate from session snapshot or fail closed |
| Container receives SIGTERM | drain, flush, settle active work, exit |
| Secret leakage detected | fail closed and block release |

## 11. 测试计划与验收

- Architecture review confirms every component has one owner and no reverse dependency.
- Protocol tests cover ADK-compatible API before HaaS native/legacy expansion.
- Adapter contract tests run against fake adapter and Codex app-server adapter.
- Sandbox tests verify OpenSandbox sandbox/execd/credential vault projection for each harness.
- Admission tests verify quota/rate/queue boundaries.
- Security tests cover every public surface and adapter env/config/log output.
