# Config 组件规格

[English](README.md) | **简体中文**

Status: Draft
Last reviewed: 2026-09-14
Change ID: unified-runtime-approval-policy
Related specs: [Container Runtime](../container-runtime/README.zh-CN.md), [Stores](../stores/README.zh-CN.md), [Identity](../identity/README.zh-CN.md), [HaaS Protocol](../haas-protocol/README.zh-CN.md), [Harness Profile](../harness-profile/README.zh-CN.md), [Manager Delegation](../manager-delegation/README.zh-CN.md)

## 1. 组件定位

Config 定义 HaaS 的配置与装配契约：环境变量、配置文件、端口表、服务发现和 app factory 签名。它消除「每个组件各自发明环境变量和 create_app 参数」的歧义。

内置本地装配一次性消费 `HAAS_CREDENTIAL_FD`，仅表示继承的私有 socket descriptor，不是凭证。在 application lifespan 中按 `model_proxy.listen` 启动仅 loopback 的 model proxy，端口 0 自动分配空闲端口。通道或 listener 不可用时 control API 仍可用，execution 不可用。Descriptor 和 provider 环境变量均不传给 harness 子进程。

## 2. 来源与依据

| 来源 | 采用内容 |
|------|----------|
| Container Runtime | 端口约定（8080/8092/18080/18081）、`create_app --factory` |
| Architecture | Python + FastAPI、sidecar-first 单进程 |
| Security Boundary | secret 只走引用，不落明文配置 |

## 3. 上游与下游关系

| 方向 | 对象 | 关系 |
|------|------|------|
| 上游 | Deployment / container entrypoint | 注入 env 与配置文件路径 |
| 下游 | 所有组件 | 提供 `AppConfig` 作为唯一配置来源 |
| 下游 | Observability | 记录生效配置摘要（脱敏） |

## 4. 职责边界

负责：

- 定义 env 前缀、config 文件格式与三层优先级。
- 定义 `AppConfig` 数据模型与 `load_config()` / `create_app(config)` 契约。
- 定义端口表与服务发现（loopback 地址）。
- 定义 secret 类配置只能引用（`credentialRef`），不接受明文。

不负责：

- 不解析 secret 明文。
- 不做进程级运行时热更新（首期 `AppConfig` 静态；reload 为后续预留）。
- 不承载 harness/profile 的动态业务配置；provider、MCP、skills、AGENTS.md、
  workspace/policy 和 budget 的运行时更新由 Harness Profile 负责。

## 5. 核心接口

```python
@dataclass
class AppConfig:
    server: ServerConfig
    store: StoreConfig
    identity: IdentityConfig
    model_proxy: ModelProxyConfig
    mcp_proxy: McpProxyConfig
    adapters: AdaptersConfig
    session_runtime: SessionRuntimeConfig
    delegation: DelegationConfig
    observability: ObservabilityConfig

def load_config(path: str | None) -> AppConfig: ...
def create_app(config: AppConfig | None = None) -> FastAPI: ...

`create_app()` 被 uvicorn `--factory` 无参调用时，内部执行 `load_config(HAAS_CONFIG)`
再装配 app；显式传 `config` 的路径（如测试）跳过加载步骤。
```

### 5.1 Adapter 装配契约（S6）

`create_app()` 必须按配置装配真实 harness adapter，不得让生产入口静默落到
测试用 `FakeAdapter`：

| `adapters.default_base` | 装配结果 |
|-------------------------|----------|
| `codex`（默认） | `CodexAdapter`，transport/socket 取自 `adapters.codex` |
| `fake` | `FakeAdapter`，仅供本地开发与测试 |

环境变量 `HAAS_ADAPTER_BASE` 可覆盖 `default_base`。

装配只建立连接配置，不在启动时强制连通 harness：Codex 未就绪时进程仍需启动，
由 `/v1/haas/ready?scope=execution` 与 `/v1/haas/status` 如实反映
`not_ready`（`/health` 仍为 ok）。这样容器可先起来再等 harness 就绪，符合
[Container Runtime](../container-runtime/README.zh-CN.md) 的 health/ready 分离约定。

| 变量 | 含义 |
|------|------|
| `HAAS_ADAPTER_BASE` | 默认 harness base（`codex` / `fake`） |
| `HAAS_DEFAULT_IMAGE_VARIANT` | 沙箱镜像变体（`lite` 默认；`aio` 手动切换）。Manager 与 delegated-session `image.variant` 可按 session 覆盖。 |
| `HAAS_SESSION_LEASE_TTL_MS` | active-turn session lease TTL（毫秒，默认 `30000`） |
| `HAAS_SESSION_LEASE_RENEW_INTERVAL_MS` | active-turn lease 续租周期（毫秒，默认 `10000`；必须小于 TTL 一半） |
| `HAAS_SESSION_TURN_TIMEOUT_SECONDS` | adapter invocation 端到端超时（秒，默认 `900`；必须大于 lease TTL） |

环境变量前缀统一为 `HAAS_`，例如：

| 变量 | 含义 |
|------|------|
| `HAAS_CONFIG` | 配置文件路径 |
| `HAAS_SIDECAR_PORT` | sidecar 端口（默认 `8092`） |
| `HAAS_STORE_BACKEND` | 生产默认 `sqlite`；`memory` 仅测试；`postgres` 多副本预留 |
| `HAAS_IDENTITY_PROVIDER` | `static` / `external_jwt` |
| `HAAS_STATIC_TOKEN_FILE` | 包含本地 static bearer token 的用户私有文件；一旦配置，文件缺失或为空时必须 fail closed，不得回退到 `dev-token` |
| `HAAS_DELEGATION_CONTAINER_BACKEND` | 独立服务与打包 local-API 默认 `disabled`；`docker` 启用显式 delegated-container runtime profile。 |
| `HAAS_DELEGATION_DOCKER_BIN` | delegated backend 为 `docker` 时使用的 Docker CLI 路径/名称 |
| `HAAS_DELEGATION_DOCKER_NETWORK` | 默认 HaaS `isolated` policy mode：internal worker network 加不转发 IP 的 broker egress；`none` 仅离线，拒绝 raw host/bridge 透传 |
| `HAAS_DELEGATION_ALLOW_UNPINNED_LOCAL_IMAGE` | 本地开发逃生口，允许 `haas:local` 这类未 pin digest 的镜像 tag；默认 `false`，生产必须保持 digest pinning |
| `HAAS_DELEGATION_IDLE_TTL_SECONDS` | delegated-session container 默认 idle TTL（`1800`） |
| `HAAS_DELEGATION_MAX_CONTAINER_LIFETIME_SECONDS` | delegated container 默认最大存活时间（`28800`） |
| `HAAS_DELEGATION_RW_WORKSPACE_CONCURRENCY` | 首期为 `single_writer` |

配置文件默认 `haas.yaml`，优先级：默认值 < 配置文件 < 环境变量。

### 5.2 Managed Launch 字段

| Environment | AppConfig 目标/行为 |
|-------------|--------------------|
| `HAAS_STATIC_PRINCIPAL_JSON` | identity.static_principal；Identity 验证的 base64 JSON，仅 launch 拥有 |
| `HAAS_PORT_READY_FILE` | server.ready_file；bind 后原子写 owner-only JSON `{port,pid,launchId}` |
| `HAAS_LAUNCH_ID` | server.launch_id；每次启动非 secret id，与 ready_file 一起必填 |
| `HAAS_BOOTSTRAP_HARNESS_ID/BASE/NAME` | registry seed tuple；仅 scoped identity，无 active profile，不覆盖 |
| `HAAS_LOG_FILE` | observability.log_file；脱敏写入 |
| `HAAS_LOG_ROTATE_MAX_MB` / `HAAS_LOG_ROTATE_KEEP` | observability rotation，默认 32 / 5 |
| `HAAS_DEFAULT_IMAGE_VARIANT` | delegation.default_image_variant，默认 lite |

`HAAS_SIDECAR_PORT=0` 绑定临时端口；supervisor lock 必须先于 token/file 创建。Host control 只绑定 loopback，不依赖 host Codex；delegated worker 不提供 public control API。Docker `isolated` 是 HaaS policy mode 而非 Docker network name，创建 Container Runtime 定义的 internal worker/non-routing broker 网络。`none` 仅离线，拒绝任意 host/bridge 透传。进程配置修改 drain 后重启，session 配置走 delegated `/policy` 屏障；profile/bootstrap 控制操作不依赖 execution-ready。

## 6. 数据模型

```yaml
server:
  host: "0.0.0.0"
  port: 8092
store:
  backend: sqlite           # 生产默认；memory 仅测试；postgres 多副本预留
  dsn: null
  event_retention_seconds: 2592000
identity:
  provider: static
  static_token_file: null
model_proxy:
  listen: "127.0.0.1:18080"
mcp_proxy:
  listen: "127.0.0.1:18081"
adapters:
  default_base: codex
  codex:
    transport: unix_websocket
    socket_path: /tmp/haas/codex.sock
session_runtime:
  lease_ttl_ms: 30000
  lease_renew_interval_ms: 10000
  turn_timeout_seconds: 900
delegation:
  container_backend: disabled  # 默认；显式 delegated-session profile 覆盖为 docker
  docker_bin: docker
  docker_network: isolated
  default_image_variant: lite
  allow_unpinned_local_image: false
  idle_ttl_seconds: 1800
  max_container_lifetime_seconds: 28800
  rw_workspace_concurrency: single_writer
  queue_policy: fifo
  restore_policy: fail_closed
  policy_change_mode: snapshot_per_session
  mount_policy: project_rw_extra_ro
```

`delegation` 是独立的 HaaS 服务配置命名空间。独立服务与打包 `local_api` sidecar
默认保持 disabled，避免 operator 未授权时隐式授予 Docker 控制权。OpenHarness 产品
profile 是 manager 拥有的独立启动 profile：默认使用 HaaS `local_managed` + autostart
以及非容器化 `/run_sse`；只有显式 delegated-session profile 才以
`container_backend=docker` 启动 managed sidecar。生效值会复制进
delegated-session policy snapshot。后续配置变更只影响新 delegated
session；已有 session 只有通过显式 policy update/rebind API 才会改变。

OpenHarness manager-owned launch profile 将新 session 默认 policy 显式物化为
`workspace-write`、公网 `allow` 与审批 `on-request`。这些值属于 profile/session data，
不是进程环境变量捷径。一次性持久配置 migration 只为缺少相应字段的旧配置写入上述值
和 migration revision，保留所有已显式设置。单纯重启进程不得追溯修改已有 session；
已有 session 只能通过带 revision 的 session 或 delegated-session `/policy` 端点变更。

配置层边界：`AppConfig` 只决定进程装配、端口、store backend、adapter 装配和
delegation backend 是否可用。它不是用户可编辑的 harness profile。任何由 Manager
动态提交的 provider/model、MCP、skill、AGENTS.md、workspace/policy 或 budget 变更
必须写入 Harness Profile revision，并经过 validate/activate 或显式 session rebind
流程。不得通过修改 env/yaml 让运行中的 session 隐式漂移。

端口与服务发现固定为：

| 端口 | 服务 | 地址 |
|------|------|------|
| 8080 | nginx + OpenSandbox AIO | 仅 AIO；Lite 不使用 |
| 8092 | HaaS sidecar | Host control：loopback；独立 Lite：容器接口 publish 到 host loopback；AIO：nginx loopback upstream；delegated worker：仅私有网络 |
| 18080 | model proxy | 仅 `127.0.0.1` |
| 18081 | MCP/tool proxy | 仅 `127.0.0.1` |

## 7. 运行模型与状态机

```text
startup -> load_config(HAAS_CONFIG) -> validate -> build AppConfig -> create_app(config) -> serve
```

## 8. 安全与权限

- provider key / MCP token 不接受明文 env 或 yaml；只能 `credentialRef`。
- 配置 dump/诊断输出必须脱敏，不包含 dsn、key、passphrase。
- loopback 服务（18080/18081）只绑定 `127.0.0.1`。
- delegation policy 配置不得包含 host path 或 credential；host path 只能通过 manager 已授权 mount manifest 提供。

## 9. 可观测性

- `haas.config.loaded`
- `haas.config.validation_failed`
- 生效配置摘要（脱敏）写入启动日志。

## 10. 失败与恢复

| 场景 | 行为 |
|------|------|
| 配置缺失/非法 | startup fail fast，输出 safe reason |
| 配置含明文 secret | 拒绝启动，`haas_secret_input_invalid` |
| 端口冲突 | startup fail，输出诊断 |

## 11. 测试计划与验收

- Unit：三层优先级、非法配置 fail fast、secret 拒绝。
- Unit：独立 HaaS 默认关闭 delegated container，而 OpenHarness local-managed 启动 profile 显式选择 Docker；环境变量覆盖能生成稳定 policy snapshot，且不改变已有 delegated session。
- Unit：修改 `AppConfig` 后不会隐式改变 registry 中 active profile 或既有 session
  snapshot；动态 harness 配置只能通过 Harness Profile API 生效。
- Integration：`create_app(config)` 用 memory store + static identity 启动并过 `/v1/haas/health`。
- Security：config 文件与 dump 不出现明文 secret。
