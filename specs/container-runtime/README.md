# Container Runtime 组件规格

Status: Draft
Last reviewed: 2026-08-31
Related specs: [Startup](../startup/README.md), [Security Boundary](../security-boundary/README.md), [Codex App-Server Adapter](../codex-app-server-adapter/README.md), [Observability](../observability/README.md)

## 1. 组件定位

Container Runtime 定义 HaaS 镜像、进程拓扑、端口、volume、health/ready 和 shutdown 语义。HaaS runtime image 必须基于开源 OpenSandbox AIO 镜像构建，继承 AIO 的 shell、file、browser 和 sandbox service 能力。

启动编排、nginx 总入口和 Codex readiness 的详细合同由 [Startup](../startup/README.md) 定义；本组件只保留容器、进程和端口边界。

## 2. 来源与依据

| 来源 | 采用内容 |
|------|----------|
| OpenSandbox README | 官方镜像 registry、sandbox lifecycle、credential vault、network policy |
| OpenSandbox AIO example | `ghcr.io/agent-infra/sandbox:latest`、`/opt/gem/run.sh`、AIO port `8080` |
| OpenSandbox API docs | lifecycle API、execd API、SSE command execution、file API |
| `mpa-codex-worker` container runtime spec | `/health`/`/ready` 分离、runtime root、socket、shutdown、容器验证 |
| 本组件总览 | Dockerfile 切换到 OpenSandbox AIO |

## 3. 上游与下游关系

| 方向 | 对象 | 关系 |
|------|------|------|
| 上游 | Deployment system / developer | 构建和运行 HaaS image |
| 上游 | HaaS sidecar | 读取 runtime dirs、ports、AIO endpoint、process status |
| 上游 | Sandbox Runtime | 消费 AIO sandbox/execd/credential vault 服务 |
| 下游 | OpenSandbox AIO | 基础 shell/file/browser/sandbox/execd/vault service |
| 下游 | Codex app-server | 首期 harness runtime process |
| 下游 | Model Proxy / MCP Proxy | loopback service |
| 下游 | Observability | process logs、health、ready、resource metrics |

## 4. 职责边界

负责：

- 定义 Dockerfile base image、dependency layer、runtime layer 和 entrypoint。
- 保留或委托 OpenSandbox AIO `/opt/gem/run.sh`。
- 保证 AIO 的 sandbox/execd/credential vault 服务可用，供 Sandbox Runtime 使用。
- 启动 HaaS sidecar、Codex app-server、model proxy、MCP proxy 以及必要 watchdog。
- 定义 `8080`、`8092`、`18080`、`18081` 端口归属。
- AIO 基础镜像的 node22 REPL 默认占用 `8092`，与 HaaS sidecar 冲突。必须通过
  AIO 自身的 `NODEJS_REPL_PORT_22` 覆盖为 `8093`（在 Dockerfile 中设置），
  不得 fork 或私改 AIO 启动脚本。新增容器内服务前必须先确认端口未被 AIO 占用。
- Sidecar 是容器存在的理由：entrypoint 必须监控其存活，sidecar 退出时容器以
  非零码退出，禁止出现「容器 Up 但 API 不可用」的静默失败。AIO/Codex 退出只
  记录告警，由 `/ready` 如实反映能力降级。
- 定义 runtime root、workspace root、artifact root、state root 和 socket root。
- 定义 health/ready/status 的容器语义。
- 将 nginx 作为容器对外总入口，并把 HaaS sidecar readiness 作为对外 ready 的事实来源；具体启动 DAG 见 Startup spec。
- 定义 SIGTERM drain：停止接新任务、flush event log、标记 ready=false、取消或落盘 active turn。
- 定义 base image digest pin 和升级验证。

不负责：

- 不实现 OpenSandbox lifecycle server。
- 不替代 HaaS Protocol 的 public API。
- 不保存 provider secret。
- 不用 Docker privileged 或 root 身份直接放宽 agent 工具权限。
- 不在启动 critical path 执行模型请求、MCP 全量探测、skill 远端下载或长时间恢复。

## 5. 核心接口

### 5.1 Dockerfile Contract

```dockerfile
ARG HAAS_BASE_IMAGE=ghcr.io/agent-infra/sandbox@sha256:<production-pinned-digest>
FROM ${HAAS_BASE_IMAGE}

# install HaaS runtime dependencies after base AIO layers
# install/pin Codex CLI and optional future harness CLIs
# copy haas source after dependency layers
# preserve /opt/gem/run.sh and add HaaS entrypoint wrapper
```

Rules:

- Local experiments may use `ghcr.io/agent-infra/sandbox:latest`.
- The Dockerfile default must remain the production-pinned digest. A local or CI build may set `HAAS_BASE_IMAGE` to a trusted digest-pinned mirror/cache reference; release builds may not use a mutable tag.
- Dependency install layers must precede source code copy. npm and uv downloads use BuildKit cache mounts and remain governed by `uv.lock` and package pins.
- `make docker-build` is the standard local build entrypoint; the override does not change the production default or AIO service contract.
- Runtime env must be placed near the final runtime layer so it does not bust dependency cache.
- The Dockerfile must not embed provider keys, MCP tokens, cookies or user auth files.

### 5.2 Entrypoint Contract

```text
/opt/haas/run.sh
  -> prepare runtime directories
  -> start or delegate OpenSandbox AIO /opt/gem/run.sh
  -> start HaaS sidecar on 8092
  -> start Codex app-server listener for codex adapter
  -> forward SIGTERM to all child process groups
```

If a process manager is used, it must not make supervisor RUNNING equal HaaS ready. Readiness must be based on actual sidecar and adapter probes.

### 5.3 Health / Ready

| Endpoint | Meaning |
|----------|---------|
| `/health` or `/v1/haas/health` | HaaS sidecar process responds |
| `/ready?scope=control` | HaaS can accept lightweight control-plane operations |
| `/ready?scope=execution` | HaaS can start a harness turn |
| AIO `/v1/shell/sessions` | AIO service readiness probe |

## 6. 数据模型

### 6.1 RuntimeLayout

```json
{
  "workspaceRoot": "/workspace",
  "dataRoot": "/data/haas",
  "runtimeRoot": "/tmp/haas",
  "stateRoot": "/data/haas/state",
  "artifactRoot": "/data/haas/artifacts",
  "codexHome": "/data/haas/harnesses/codex/home",
  "codexSocketPath": "/tmp/haas/codex.sock",
  "aioBase": {
    "image": "ghcr.io/agent-infra/sandbox@sha256:<digest>",
    "servicePort": 8080,
    "entrypoint": "/opt/gem/run.sh",
    "services": ["shell", "file", "browser", "sandbox", "execd", "credential_vault"]
  }
}
```

### 6.2 RuntimeProcess

```json
{
  "name": "haas-sidecar",
  "command": ["/app/haas/.venv/bin/uvicorn", "haas.api.app:create_app", "--factory"],
  "port": 8092,
  "critical": true,
  "restart": "on_failure",
  "healthEndpoint": "/v1/haas/health"
}
```

## 7. 运行模型与状态机

```text
image built
  -> container starting
  -> aio starting
  -> sidecar listening
  -> control ready
  -> adapter probe pending
  -> execution ready
  -> draining
  -> stopped
```

Startup rules:

- `/health` must become available before optional capability warmup completes.
- `/ready?scope=control` and the default `/ready` are overall service readiness signals; they remain false until Codex app-server readiness probe completes.
- `/ready?scope=execution` may use the same Codex gate for the P0 adapter; model provider, MCP discovery or browser startup remain outside the default gate unless declared a Codex execution-safety dependency.
- AIO readiness and HaaS readiness are reported separately.

## 8. 安全与权限

- Container root or privileged mode is not a substitute for harness sandbox policy.
- Runtime secret files must be owner-only and excluded from artifacts.
- AIO service endpoints should be bound to loopback unless intentionally exposed through a controlled proxy.
- HaaS sidecar bearer auth is required for non-health endpoints.
- Docker build args and image layers must not contain secrets.
- Base image digest must be recorded for release builds.

## 9. 可观测性

Container status must include:

- image reference and digest;
- AIO process status and port;
- HaaS sidecar process status and port;
- adapter process status;
- model/MCP proxy status;
- startup phase timings;
- drain state;
- last safe error reason.

Logs must go to stdout/stderr or configured log files with redaction.

## 10. 失败与恢复

| 场景 | 行为 |
|------|------|
| AIO service not ready | HaaS control ready may be true; execution ready false with safe reason |
| HaaS sidecar not listening | container health fails |
| Codex app-server not ready | execution ready false; session create can be pending only if API contract allows |
| SIGTERM | enter draining, reject new tasks, flush event log, cancel/settle active turns |
| base image unavailable | build fails; do not silently switch image |
| digest mismatch | release blocked |
| port conflict | startup fails with safe diagnostics |

## 11. 测试计划与验收

- Dockerfile lint/static check for base image pin in release mode.
- Build smoke from current checkout（`HAAS_DOCKER_BUILD=1 make docker-check`）。
  静态检查不得作为容器变更的唯一证据：曾出现静态全绿但镜像根本无法构建
  （`pyproject` 声明的 `README.md` 未 COPY）。容器相关变更必须跑 build 层。
- Container run smoke verifies AIO port `8080` and HaaS port `8092`，并断言
  `/v1/haas/status` 中装配的是真实 harness adapter（非测试替身），以及
  sidecar 被杀后容器以非零码退出。AIO 启动慢于 sidecar，就绪判定需轮询。
- Health/ready tests verify `/health` is not gated by optional warmups.
- Shutdown test sends SIGTERM and asserts drain events/status.
- Secret scan verifies build args, env, logs and image metadata do not contain provider credentials.
