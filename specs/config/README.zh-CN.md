# Config 组件规格

[English](README.md) | **简体中文**

Status: Draft
Last reviewed: 2026-09-07
Related specs: [Container Runtime](../container-runtime/README.zh-CN.md), [Stores](../stores/README.zh-CN.md), [Identity](../identity/README.zh-CN.md), [HaaS Protocol](../haas-protocol/README.zh-CN.md)

## 1. 组件定位

Config 定义 HaaS 的配置与装配契约：环境变量、配置文件、端口表、服务发现和 app factory 签名。它消除「每个组件各自发明环境变量和 create_app 参数」的歧义。

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
- 不做运行时热更新（首期静态；热更新为后续预留）。

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

配置文件默认 `haas.yaml`，优先级：默认值 < 配置文件 < 环境变量。

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
```

端口与服务发现固定为：

| 端口 | 服务 | 地址 |
|------|------|------|
| 8080 | nginx + OpenSandbox AIO | HaaS 容器对外总入口；保留 AIO 内部路由 |
| 8092 | HaaS sidecar | nginx upstream，仅 `127.0.0.1` |
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

## 9. 可观测性

- `haas.config.loaded`
- `haas.config.validation_failed`
- 生效配置摘要（脱敏）写入启动日志。

## 10. 失败与恢复

| 场景 | 行为 |
|------|------|
| 配置缺失/非法 | startup fail fast，输出 safe reason |
| 配置含明文 secret | 拒绝启动，`haas_config_secret_invalid` |
| 端口冲突 | startup fail，输出诊断 |

## 11. 测试计划与验收

- Unit：三层优先级、非法配置 fail fast、secret 拒绝。
- Integration：`create_app(config)` 用 memory store + static identity 启动并过 `/v1/haas/health`。
- Security：config 文件与 dump 不出现明文 secret。
