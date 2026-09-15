# Startup 组件规格

[English](README.md) | **简体中文**

Status: Draft
Last reviewed: 2026-09-02
Change ID: haas-standard-startup
Related specs: [Architecture](../architecture/README.zh-CN.md), [Container Runtime](../container-runtime/README.zh-CN.md), [Codex App-Server Adapter](../codex-app-server-adapter/README.zh-CN.md), [HaaS Protocol](../haas-protocol/README.zh-CN.md), [Observability](../observability/README.zh-CN.md)

## 1. 组件定位

Startup 定义 Lite、AIO 与宿主控制 sidecar 编排。本文 nginx/AIO 指令仅适用于 AIO 镜像，Lite 使用独立最小 entrypoint 并跳过这些阶段。宿主控制 sidecar 监听 loopback，独立 Lite 将容器 8092 publish 到 host loopback，delegated worker 仅使用私有服务 transport（Container Runtime §5.3）。HaaS 拥有 northbound readiness。

`ready?scope=control` 仅依赖 identity/config/store 初始化，空 registry 或 Codex 不可用时仍可就绪。`ready?scope=execution`（也是默认 `/ready` scope）额外验证 adapter 握手、runtime 和隔离；下文 Codex/整体 ready 规则只针对 execution scope。宿主 delegation controller 检查 Docker 执行路径，不要求无关的 host Codex 进程。Control ready 后即可读取包含 unavailable feature 的 capabilities，不以 execution ready 为前提。

目标是让最小服务入口尽快可用，同时不把非关键能力误判为 ready，也不让可异步初始化的能力阻塞整体服务。

## 2. 来源与依据

| 来源 | 采用内容 |
|------|----------|
| HaaS AGENTS.md | nginx/AIO 容器边界、`/health`/`/ready` 语义、Codex app-server 首期约束、secretless 和可恢复性 |
| Container Runtime | AIO `/opt/gem/run.sh`、端口归属、sidecar critical、进程和 shutdown 合同 |
| Codex App-Server Adapter | Unix socket transport、`initialize`/`initialized` 握手、generation 和恢复语义 |
| 本地 `mpa-codex-worker` 配置参考 | nginx upstream 挂载、supervisor priority、sidecar/Codex 分离启动、背景 warmup |

## 3. 上游与下游关系

| 方向 | 对象 | 关系 |
|------|------|------|
| 上游 | Container runtime / `/opt/gem/run.sh` | 进程启动、信号转发和 AIO 基础服务 |
| 上游 | Config | 端口、socket、timeout 和启动策略 |
| 下游 | nginx | 唯一对外 HTTP/SSE 入口，转发到 HaaS sidecar |
| 下游 | HaaS sidecar | API、Codex readiness 聚合、ready 信号 |
| 下游 | Codex app-server adapter | Codex 启动/连接和标准握手探测 |
| 异步下游 | AIO optional services、Model Proxy、MCP、Browser、Skills、Artifact warmup | 默认不阻塞整体 ready |

## 4. 职责边界

负责：

- 生成并校验 nginx 配置，把 HaaS northbound API 统一挂载到 sidecar loopback upstream。
- 规划 nginx、sidecar、Codex adapter 的启动顺序和并行关系。
- 定义整体 ready 的唯一判定：sidecar 存活且 Codex readiness probe 已完成。
- 定义 Codex readiness probe 的真实验证步骤、超时、重试和状态投影。
- 把非关键初始化放入可观测 background task；普通可选任务不得改变已发布的 ready 事实，只有被明确声明为 Codex 执行安全硬依赖的任务失败才可撤销 ready。
- 定义启动阶段、耗时、失败原因和 graceful shutdown 行为。

不负责：

- 不实现 nginx、Codex JSON-RPC 或 OpenSandbox AIO 本身。
- 不让 nginx 自己判断 Codex readiness；nginx 只代理 sidecar 的结构化状态。
- 不把模型 provider 成功、MCP 全量发现、browser、skills 下载或 artifact 清理默认加入整体 ready。
- 不把 Codex 原生 endpoint、thread id、turn id 或 JSON-RPC payload 暴露给上游。

## 5. 核心接口

### 5.1 对外入口

nginx 是容器唯一对外监听者，复用 AIO 保留的 `8080` listener；sidecar、Codex socket、model proxy `18080` 和 MCP proxy `18081` 默认只绑定 loopback 或 Unix socket。AIO 的内部 routes 继续由同一 nginx server 提供。

nginx 必须代理以下 HaaS surface，保持路径和方法不变：

- ADK-compatible：`/list-apps`、`/run`、`/run_sse`、`/apps/{app}/users/{user}/sessions/{session}`。
- HaaS native：`/v1/haas/*`，至少包含 `/v1/haas/health`、`/v1/haas/ready`、`/v1/haas/status`。

代理要求：

- upstream 默认指向 `http://127.0.0.1:8092`；不得指向公网或 Codex socket。
- SSE 路由使用 HTTP/1.1，关闭 response buffering，设置 `Cache-Control: no-cache`，并允许长 read timeout。
- streaming request 转发 `Host`、`X-Real-IP`、`X-Forwarded-For`、`X-Forwarded-Proto`；WebSocket 仅用于明确声明的内部/adapter 路由。
- nginx 配置启动前执行 syntax check；失败时容器非零退出，不能使用半成品配置。
- nginx 不伪造业务 ready 响应；`/health` 与 `/ready` 均由 sidecar 提供结构化结果并由 nginx 透传。

### 5.2 Sidecar readiness contract

sidecar 是 ready 事实 owner。`GET /v1/haas/health` 在 sidecar 可响应时返回存活状态，不要求 Codex ready。

`GET /v1/haas/ready` 只有同时满足以下条件才返回 `ready=true`：

1. sidecar HTTP server 正在监听并能执行 readiness handler；
2. Codex adapter readiness probe 已完成；
3. probe 使用的 Codex generation 仍是当前 active generation；
4. sidecar 未进入 draining 或 stopped。

Codex 未 ready 时返回非 2xx 的结构化未就绪响应，建议状态码 `503`，错误码使用已有 `haas_adapter_unavailable`，不得让调用方解析人类文本。

### 5.3 Codex readiness probe

标准探测固定为：

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

规则：

- Unix socket 存在不是 ready；只有 socket 可连接且 `initialize` 成功、`initialized` 已发送完成才算 ready。
- readiness connection 设置有限的 connect、initialize 和 total timeout，不能无限等待。
- probe 连接与实际 session/turn connection 生命周期隔离，不把 probe 连接当作业务 session。
- `initialize` 失败、超时、协议版本不匹配、socket 被替换或 generation 变化时，ready 回落为 false。
- probe 完成发布前必须原子校验其 generation 仍是 active generation；旧 generation 的迟到 probe 结果不得覆盖新的 `ready=false` 或发布 `ready=true`。
- `initialized` 是 notification，没有 JSON-RPC response；其“完成”定义为 notification 已按协议写入 readiness connection 并成功 flush，随后 connection 标记为 handshake-complete。
- probe 不执行 model request、thread/start、turn/start、MCP 全量探测或用户 prompt。
- Codex 原生错误进入 adapter/observability 的安全诊断；northbound 只看到结构化 HaaS readiness/error。

## 6. 数据模型与状态机

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

真实绝对 socket 路径、认证 token、原始 JSON-RPC、prompt 和 provider credential 不得出现在对外 status、日志或事件中。

### 6.2 Startup phase

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

`service_ready` 是唯一允许 `/v1/haas/ready` 返回 `ready=true` 的 phase。`nginx_listening`、`sidecar_listening` 和 `codex_probing` 都不等于整体 ready。

## 7. 启动编排与时延预算

关键路径只包含本地、可界定时延的操作：

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

AIO、nginx、sidecar 和 Codex app-server 应并行启动；只有 nginx 配置校验、sidecar 可响应、Codex readiness probe 三者形成整体 ready 依赖。不得把 AIO 全量 ready 作为 sidecar ready 的前置条件。

AIO 通过官方 `DISABLE_CODE_SERVER` / `DISABLE_JUPYTER` / `DISABLE_NODEJS_REPL` 关闭的服务
（见 [Runtime Trim](../runtime-trim/README.zh-CN.md)）不在关键路径上，也不参与整体 ready 判定；它们的
缺席不得改变 nginx、sidecar 或 Codex readiness 行为。browser/VNC 仍按可选能力异步 warmup。

启动实现必须：

- 为每个 phase 记录 monotonic start/end、duration、status 和 safe reason。
- 使用 bounded timeout 和有限重试；重试期间 `/health` 可成功，`/ready` 保持 false。
- 不在 critical path 执行网络下载、provider 请求、MCP 远端探测、browser、skill materialization 或全量 workspace 扫描。
- background task 使用独立任务组和取消边界，不因未完成阻塞 HTTP server 或 ready。
- background task 失败更新能力状态和安全诊断；普通可选任务不撤销已发布 ready，明确的 Codex 执行安全硬依赖失败才由 sidecar 将 ready 回落为 false。

`startup_ready_duration_ms` 从容器启动时间点计时，到 sidecar 首次发布 `ready=true` 结束。默认目标为本地无冷缓存异常时 P95 ≤ 5 秒；不得通过跳过 Codex 握手降低目标。

至少观测：`nginx_listen_ms`、`sidecar_listen_ms`、`codex_socket_connect_ms`、`codex_initialize_ms`、`service_ready_ms`。

## 8. 安全与权限

- nginx 只代理允许的 ADK/HaaS 路径，不把 caller URL 变成 proxy target。
- Codex Unix socket 权限限制到 HaaS runtime 用户/组；探测不得通过公开 TCP listener 绕过隔离。
- readiness token 只能来自 runtime secret handle 或受限文件，不进入命令行、日志、status 或事件。
- nginx access log、sidecar log、startup timing 和 status 脱敏；不得记录 raw JSON-RPC、raw prompt、完整 tool 参数或 provider credential。
- nginx 保留 `X-Content-Type-Options: nosniff` 等安全 header；SSE 不得关闭认证和 scope 检查。

## 9. 可观测性

事件：`haas.startup.phase_started`、`haas.startup.phase_completed`、`haas.startup.ready_published`、`haas.startup.ready_withheld`、`haas.startup.background_failed`、`haas.startup.draining`。

指标：

- `haas_startup_phase_duration_ms{phase,status}`
- `haas_startup_ready_total{status}`
- `haas_startup_ready_duration_ms`
- `haas_startup_codex_probe_total{status,reason}`
- `haas_startup_background_task_total{task,status}`

事件和 metrics 只使用 safe reason、phase、status、generation 和耗时等低敏字段。

## 10. 失败与恢复

| 场景 | 行为 |
|------|------|
| nginx 配置非法 | 启动失败，容器非零退出，不使用旧/半成品配置 |
| nginx 已启动但 sidecar 未监听 | `/health` 失败或 upstream error，`/ready` 不得为 true |
| sidecar 已监听但 Codex socket 未创建 | `/health` 成功，`/ready` 返回结构化 503，继续 bounded retry |
| socket 可连但 initialize 失败 | ready 保持 false，adapter degraded/restarting，按策略重建 generation |
| initialized 未完成 | ready 保持 false，不把进程存在升级为 ready |
| Codex generation 改变 | 立即撤销旧 generation 的 ready，重新探测 |
| 可选 background task 失败 | 记录安全原因并更新能力状态；不阻塞 control API，若影响 Codex 执行安全则 ready 回落 |
| sidecar 退出 | 容器必须以非零码退出，避免容器显示 Up 但 API 不可用 |
| SIGTERM | 先发布 ready=false，停止新执行请求，再取消/settle active turn、flush event log、停止 background tasks 和子进程 |

## 11. 测试计划与验收

### 11.1 配置与入口

- nginx syntax check 通过；HaaS ADK 与 `/v1/haas/*` 路径、方法、header、SSE streaming 行为与 spec 一致。
- sidecar、Codex socket、AIO `8080` 和 proxy loopback 不被错误暴露。
- nginx upstream 不直接指向 Codex socket，且 nginx 不生成伪造 ready。

### 11.2 Readiness contract

- sidecar 监听但 socket 不存在：health 通过、ready 失败。
- socket 存在但不可连接：ready 失败。
- socket 可连接但 initialize 返回错误：ready 失败。
- initialize 成功但 initialized 未完成：ready 失败。
- initialize + initialized 完成：sidecar 发布 ready=true，nginx 对外可读到同一结构化结果。
- Codex generation 重启或 socket 被替换：ready 先回落，再在新 generation 握手完成后恢复。

### 11.3 Startup latency and async behavior

- 使用 fake Codex app-server 测量各阶段耗时和首次 ready 时延。
- 注入慢 AIO、慢 MCP、慢 browser、慢 skills 和失败 background task，证明它们不阻塞 Codex ready。
- 注入 sidecar/Codex 启动失败、超时、重试和 SIGTERM，验证状态、错误码、非零退出和 drain。
- 真实 OpenSandbox AIO + Codex app-server 容器 smoke 验证 nginx 总入口、sidecar ready 和 AIO `8080` 并存。

### 11.4 安全

- secret scan 和日志反向断言验证 token、credential、raw JSON-RPC、prompt 与完整 tool 参数不进入配置、日志、status、事件或 artifact。

## 12. 任务拆分

- P0：nginx HaaS upstream、统一 `/health`/`/ready` 代理、配置 syntax gate。
- P0：sidecar readiness state machine 和结构化 ready response。
- P0：Codex Unix socket + `initialize`/`initialized` dedicated probe。
- P0：启动 phase timing、bounded retry、generation invalidation、SIGTERM drain。
- P1：background task registry、异步能力状态、失败隔离和诊断 status。
- P1：fake harness startup-latency suite 与真实容器 smoke。
