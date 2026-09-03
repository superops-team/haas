# Runtime Trim 组件规格

Status: Draft
Last reviewed: 2026-09-03
Change ID: haas-runtime-trim
Related specs: [Container Runtime](../container-runtime/README.md), [Startup](../startup/README.md), [Sandbox Runtime](../sandbox-runtime/README.md), [Security Boundary](../security-boundary/README.md), [Observability](../observability/README.md)

## 1. 组件定位

Runtime Trim 定义 HaaS 如何在 OpenSandbox AIO 基础镜像上**关闭自身用不到的 AIO 服务**，
以降低运行时内存、CPU、端口占用和攻击面。它是 AIO 服务裁剪这一横切关注点的唯一权威
owner：[Container Runtime](../container-runtime/README.md) 定义镜像/进程/端口边界，本
组件只定义“保留哪些 AIO 能力、关闭哪些、用什么机制关闭、如何验证”。

核心约束：裁剪只能通过 AIO **官方支持的环境变量**在运行时禁用服务，不 fork、不私改
AIO 启动脚本或 supervisor 定义，不删除 AIO 基础镜像的文件。本组件明确区分两类“优化”：

- **运行时禁用（本组件范围，P1）**：用 `DISABLE_*` / `NODE_VERSION` 关闭进程。降低
  内存/CPU/端口/攻击面，**不减小镜像层体积**。
- **镜像层裁剪（本组件非目标，见 §4）**：多阶段拷贝或 flatten 真正回收基础层字节。
  风险和工程量更高，属于独立后续工作，不在本轮范围。

## 2. 来源与依据

| 来源 | 采用内容 |
|------|----------|
| OpenSandbox AIO `gem_env.sh` / `gem.sh`（pinned digest 本机盘点 2026-09-03） | `DISABLE_CODE_SERVER`、`DISABLE_JUPYTER`、`DISABLE_NODEJS_REPL`、`NODE_VERSION`、`AUTOSTART_*` 语义 |
| AIO `supervisord/*.conf` | code-server / jupyter / nodejs-repl / gost / mcp-browser / vnc / browser 的 autostart 由 `AUTOSTART_*` 控制 |
| AIO `gem.sh` gost 分支 | gost 仅在设置 `PROXY_SERVER` 时启动，否则启动时移除其 supervisor 定义 |
| 本机验证（2026-09-03） | 子层 `rm` 不减小镜像；ENV `DISABLE_*` 正确映射为 `AUTOSTART_*=false`；裁剪后 nginx/`chromium`/`Xtigervnc`/`xdotool`/`python3.12`/`node`/`/opt/gem/run.sh` 均保留 |
| AGENTS.md「Docker 与 OpenSandbox AIO」 | 不得 fork 或私改 AIO 基础能力；保留 `/opt/gem/run.sh` |

## 3. 上游与下游关系

| 方向 | 对象 | 关系 |
|------|------|------|
| 上游 | Container Runtime | 在 runtime ENV 层设置裁剪环境变量；本组件是其裁剪合同的细化 |
| 上游 | Startup | 确认被裁剪服务不在关键路径、不参与 ready 判定 |
| 下游 | OpenSandbox AIO `gem_env.sh`/`gem.sh` | 消费 `DISABLE_*` / `NODE_VERSION`，产出 `AUTOSTART_*` |
| 下游 | AIO supervisor | 按 `AUTOSTART_*` 决定是否启动各 program |
| 下游 | Sandbox Runtime | 依赖裁剪不影响 sandbox/execd/credential vault |
| 下游 | Observability | 记录裁剪配置与被禁用服务清单 |

## 4. 职责边界

负责：

- 定义 HaaS 默认关闭的 AIO 服务清单与对应官方开关。
- 定义必须保留的 AIO 能力（CUA/BUA、sandbox、Codex readiness 底座）。
- 约束裁剪只能通过 AIO 官方环境变量实现，不改 AIO 脚本/supervisor。
- 定义裁剪的验收方式（静态断言 + 容器 smoke 反向断言）。
- 记录 gost/`18080` 默认无冲突的前提及未来启用 gost 时的复核要求。

不负责：

- 不做镜像层裁剪（多阶段拷贝、flatten、squash）。若未来需要真正缩体积，必须**单独立项**，
  并在本 spec 增补裁剪清单、体积基线和 CUA/BUA/sandbox 不回归证据；本轮明确不实现。
- 不定义镜像 base、进程拓扑、端口归属、health/ready（属 Container Runtime / Startup）。
- 不新增或删除 AIO 之外的服务。
- 不改变任何 northbound API、事件或错误码（本组件对 public 兼容面零影响）。

## 5. 核心接口

裁剪接口是 AIO 官方环境变量，在 Container Runtime 的 runtime ENV 层声明。

### 5.1 默认关闭（不影响 CUA/BUA、sandbox、Codex readiness）

| 服务 | AIO 环境变量 | 关闭理由 |
|------|--------------|----------|
| code-server（VSCode） | `DISABLE_CODE_SERVER=true` | HaaS 不提供在线 IDE |
| JupyterLab | `DISABLE_JUPYTER=true` | HaaS 不提供 notebook |
| 多版本 Node.js REPL server（20/22/24） | `DISABLE_NODEJS_REPL=true` | HaaS 不使用 AIO REPL |
| 冗余 Node 版本自启动 | `NODE_VERSION=node22` | 固定单一默认版本，与 AIO 默认一致 |

### 5.2 必须保留（CUA/BUA 与隔离底座）

browser（chromium）、VNC/noVNC、MCP browser、sandbox/execd、credential vault、
`/opt/gem/run.sh`、nginx。这些不得被裁剪或误关。

### 5.3 机制规则

- `DISABLE_*` 由 AIO `gem.sh` 归一化后映射为 `AUTOSTART_*=false`，supervisor 据此不启动对应
  program；这是 AIO 官方路径，不涉及脚本改写。
- 裁剪变量放在靠近末层的 runtime ENV，不破坏依赖层缓存。
- 禁止用子层 `rm`、`sed -i` 或替换 supervisor 文件来“裁剪”——既不减体积又违反“不私改 AIO”。
- 新增裁剪项前必须确认它不是 CUA/BUA、sandbox 或 Codex readiness 的依赖。

## 6. 数据模型

裁剪配置是一组布尔/枚举环境变量，无持久化状态：

```json
{
  "disableCodeServer": true,
  "disableJupyter": true,
  "disableNodejsRepl": true,
  "nodeVersion": "node22",
  "preserved": ["browser", "vnc", "mcp_browser", "sandbox", "execd", "credential_vault", "nginx", "opt_gem_run_sh"]
}
```

裁剪配置进入 diagnostics status 时必须脱敏（不含 credential、绝对 socket 路径、token）。

## 7. 运行模型与状态机

裁剪在启动时一次性生效，无独立状态机；它作用于 [Startup](../startup/README.md) 的启动 DAG：

```text
container starting
  -> gem_env/gem.sh 读取 DISABLE_* / NODE_VERSION
  -> 映射为 AUTOSTART_*=false
  -> supervisor 跳过 code-server / jupyter / nodejs-repl
  -> 保留服务与 HaaS sidecar / Codex readiness 正常启动
```

被裁剪服务的缺席不得改变 nginx、sidecar 或 Codex readiness 行为；它们不在关键路径，
也不参与整体 ready 判定。

## 8. 安全与权限

- 裁剪减少常驻服务即减少攻击面；但安全边界仍以 policy/sandbox/secret 为准，裁剪不是安全控制的替代。
- 裁剪变量不得携带 credential、token 或敏感路径。
- gost 转发代理仅在设置 `PROXY_SERVER` 时启动；否则 AIO 启动时移除其 supervisor 定义，
  HaaS model proxy 保留的 `18080` 因此默认无冲突。**若未来启用 gost，必须复核 `18080` 归属并更新本 spec 与 Container Runtime 端口表。**
- 裁剪不得关闭 VNC/browser，否则 CUA/BUA 能力回归为不可用。

## 9. 可观测性

- diagnostics status 暴露当前裁剪配置（脱敏），供运维确认哪些 AIO 服务被关闭。
- 事件/日志使用 safe 字段：被禁用服务名、`NODE_VERSION`，不含敏感值。
- 不新增 metrics 是可接受的；若新增，建议 `haas_runtime_trim_disabled_total{service}` 一次性 gauge。

## 10. 失败与恢复

| 场景 | 行为 |
|------|------|
| 误关保留服务（如 `DISABLE_BROWSER=true`） | CUA/BUA 回归不可用；docker-check smoke 必须捕获 browser/VNC 缺失 |
| AIO 升级后开关语义变化 | 升级 base digest 时必须重新盘点 `DISABLE_*`/`AUTOSTART_*` 并跑 smoke |
| 设置了 `PROXY_SERVER` 触发 gost | `18080` 可能冲突；必须复核端口归属，不得静默共用 |
| 裁剪导致 sandbox/execd 不可用 | 属回归缺陷，fail closed，不接受 |

## 11. 测试计划与验收

- 静态（`make docker-check` 默认层）：断言 Dockerfile 设置 `DISABLE_CODE_SERVER=true`、
  `DISABLE_JUPYTER=true`、`DISABLE_NODEJS_REPL=true`。
- Build/smoke（`HAAS_DOCKER_BUILD=1 make docker-check`）：
  - 断言 code-server / jupyter 未在容器内运行；
  - 断言 AIO `8080`、HaaS `8092`、`/v1/haas/status` 装配真实 codex adapter 仍正常；
  - 断言 browser/VNC 底座保留（未来可加对 browser/VNC 就绪的显式探测）。
- 升级回归：base digest 变更时重跑上述 smoke，并核对 `DISABLE_*` 语义未漂移。
- 安全：secret scan 确认裁剪 ENV 不含 credential。

## 12. 任务拆分

- P1（本轮）：`DISABLE_CODE_SERVER`/`DISABLE_JUPYTER`/`DISABLE_NODEJS_REPL`/`NODE_VERSION`
  在 Dockerfile 落地；docker-check 静态 + smoke 门禁；container-runtime/startup 引用对齐。
- P2（未来，独立立项）：真正的镜像层裁剪（多阶段拷贝或 flatten），含体积基线、
  ENTRYPOINT/ENV 保留和 CUA/BUA/sandbox 不回归证据。
