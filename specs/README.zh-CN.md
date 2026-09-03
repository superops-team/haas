# HaaS 组件规格总览

[English](README.md) | **简体中文**

Status: Draft
Last reviewed: 2026-09-02
Change ID: haas-platform-foundation

`specs/` 是 HaaS 的长期技术规格入口。它把 Harness As A Service 的协议、组件边界、状态机、安全与容器运行时定义成可实现、可测试、可审查的工程合同。

本目录必须自包含：组件 spec 可以引用 `specs/` 下的同级组件文档，不能依赖仓库外路径或临时报告才能理解设计。

## 1. 系统定位

HaaS 是多 harness 的运行托管 sidecar，northbound 协议遵循 Google ADK 2.0 REST API 协议层：

```text
Client / Manager / SDK / CLI / ADK web UI
  |
  |  ADK 2.0 HTTP + SSE
  v
HaaS Sidecar API
  |
  +-- Protocol Mapper (ADK <-> internal)
  +-- Harness Registry
  +-- Session Runtime
  +-- Admission Control
  +-- Event Log & SSE Replay
  +-- Policy Controller
  +-- Model Proxy
  +-- MCP / Tool / Skill Runtime
  +-- Artifact Store
  +-- Security Boundary
  +-- Observability
  |
  v
Harness Adapter Interface
  |
  +-- Codex app-server adapter  (P0)
  +-- Pi adapter                (future)
  +-- OpenCode adapter          (future)
  +-- AMP adapter               (future)
  +-- Other harness adapters    (future)
  |
  v
Sandbox Runtime (OpenSandbox sandbox/execd/credential vault projection)
  |
  v
OpenSandbox AIO container runtime
```

HaaS 不是模型代理本身，也不是单一 Codex Worker。模型代理是 HaaS 为 secretless 和兼容性提供的内部能力；Codex 是首期 harness adapter；Sandbox Runtime 是多 harness 运行环境标准化的承载体。

### 1.1 调研基线

本轮设计参考了以下外部事实；这些是设计输入，不是仓库内文件依赖：

| 来源 | Revision / version | 关键结论 |
|------|--------------------|----------|
| ADK 2.0 docs | fetched 2026-08-26 | REST API 协议层：`/list-apps`、`/run`、`/run_sse`、`/apps/{app}/users/{user}/sessions/{sid}`、camelCase、`newMessage{role,parts}`、Event 含 `nodeInfo`/`output`、SSE `data:` 帧 |
| `mpa-codex-worker` 本地参考 | local checkout on 2026-08-26 | 旧项目已经验证 sidecar API、Codex app-server、event log、SSE replay、session registry、model proxy、MCP proxy、secretless runtime 和容器化边界 |
| Codex manual | fetched 2026-08-26 | `codex app-server` 支持 `stdio://`、`ws://IP:PORT`、`unix://`；连接必须先 `initialize` 再 `initialized` |
| Local Codex CLI | `codex-cli 0.149.1` | `codex app-server --help` 暴露 `--listen`、`--ws-auth`、`generate-ts`、`generate-json-schema` |
| OpenSandbox | commit `cfca10537a0af7afd11e67b8574e55b2bb2603ad` | sandbox lifecycle、execd、ingress、egress、credential vault、SDK/CLI/MCP |
| AIO Sandbox | commit `89186e8c9fb3f78b2f67cc472a36c1cb63e25ccb` | AIO 镜像 `ghcr.io/agent-infra/sandbox`，端口 `8080`，入口 `/opt/gem/run.sh`，含 browser、shell、file、VSCode、Jupyter、MCP |

## 2. 文档分层

| 文档层 | 位置 | 生命周期 | 职责 |
|--------|------|----------|------|
| 组件 spec | `specs/<component>/README.md` | 长期维护 | 固化组件职责、接口、状态机、安全、恢复、测试合同 |
| Protocol schema | `specs/haas-protocol/*.openapi.yaml` | 随协议版本 | 定义可生成类型的 HTTP schema |
| 实现代码 | `haas/`、`docker/`、`scripts/` | 版本演进 | 实现 specs 中的合同 |
| 临时证据 | 不提交 | 单次准出 | 在最终说明或 PR/MR 描述中记录命令、结果、风险 |

## 3. 架构基线

### 3.1 Northbound 协议

HaaS 对上提供三个层级的 HTTP/SSE surface：

| Surface | 路径 | 兼容等级 | 用途 |
|---------|------|----------|------|
| ADK-compatible | `/list-apps`、`/run`、`/run_sse`、`/apps/{app}/users/{user}/sessions/{sid}` | Public API | 长期主协议，drop-in 兼容 ADK 2.0 client |
| HaaS native | `/v1/haas/*` | Public extension | health/ready/status、diagnostics、harness CRUD、session/event 管理、artifact 等 ADK 未覆盖能力 |
| ~~Legacy sidecar shim~~ | ~~`/v1/codex-worker/*`~~ | **本项目不实现** | 见 §3.1.1 |

规则：

1. ADK-compatible surface 不得暴露具体 harness 原生字段；HaaS 扩展只放 `haas` 嵌套对象或 `/v1/haas/*`。
2. HaaS native 只能做加法扩展，不得改变 ADK 字段语义。
3. 所有 public surface 使用 `detail` + 结构化 `haasError` 错误形状。

### 3.1.1 范围决策：不实现 `mpa-codex-worker` 迁移 shim

**决策（2026-08-30）**：HaaS 与 `mpa-codex-worker` 只是架构同构，不承担其迁移
职责。`/v1/codex-worker/*` shim **不属于本项目范围**，不实现、不测试、不在
准出门禁中要求。若将来确需迁移旧上游，**单独立项**处理。

影响与处理方式：

- 各组件 spec 中残留的 legacy/shim 描述视为**历史背景与未来可选项**，不是待办；
  实现时不得据此新增 `/v1/codex-worker/*` 路由。
- `mpa-codex-worker` 仍可作为**设计参考来源**（它已验证过 sidecar API、event log、
  SSE replay、model proxy、secretless 边界等），这与「不实现 shim」并不冲突。
- 错误码 `haas_legacy_request_invalid` 与 OpenAPI 中的 legacy 条目予以保留，
  避免改动已发布的兼容面；它们在本项目中处于**未使用**状态。
- 新能力一律定义在 ADK 面或 HaaS native 面。

### 3.2 Runtime 边界

HaaS 运行在 OpenSandbox AIO 基础镜像上。AIO 提供 shell、file、browser、exec、sandbox lifecycle、credential vault 等基础能力；HaaS 叠加 sidecar、adapter、proxy、event log、policy 和 Sandbox Runtime。

容器内默认端口：

| 端口 | 归属 | 用途 |
|------|------|------|
| `8080` | OpenSandbox AIO | AIO service / shell / browser / file / sandbox API |
| `8092` | HaaS sidecar | HaaS ADK/HTTP/SSE API |
| `18080` | HaaS model proxy | harness-facing model proxy loopback |
| `18081` | HaaS MCP/tool proxy | harness-facing MCP/tool proxy loopback |

### 3.3 Adapter 边界

每个 harness adapter 是原生 runtime 的唯一 owner：

- Codex adapter 持有 Codex app-server JSON-RPC/WebSocket/stdio 细节。
- Pi adapter 后续持有 Pi CLI JSON stream、session-id 和 config 细节。
- OpenCode adapter 后续持有 OpenCode JSON stream、permission config 和 MCP config 细节。
- AMP adapter 后续持有 AMP 原生协议、会话和工具权限细节。

上游只能看到 ADK `Event`/`Session`/错误/artifact，不能看到任何原生 runtime 细节。

### 3.4 Sandbox 标准化边界

Sandbox Runtime 把 Policy Controller 的 workspace/network/tool policy 与 harness adapter 的 sandbox 声明，统一投影为 OpenSandbox sandbox/execd 配置。harness 自带 sandbox（如 Codex sandbox）只能在其内运行；provider credential 走 credential vault；网络 egress 由 OpenSandbox egress policy 与 HaaS URL validator 双层约束。详见 [Sandbox Runtime](sandbox-runtime/README.zh-CN.md)。

## 4. 组件规划

| 优先级 | 组件 | 路径 | 主要职责 |
|--------|------|------|----------|
| P0 | Architecture | `specs/architecture/README.md` | 系统级分层、事实归属、依赖方向和首期落地顺序 |
| P0 | HaaS Protocol | `specs/haas-protocol/README.md` | ADK-compatible API、HaaS native API、错误和版本策略 |
| P0 | Harness Registry | `specs/harness-registry/README.md` | configured harness catalog、appName 解析、base/capability/model/provider discovery |
| P0 | Harness Adapter | `specs/harness-adapter/README.md` | 多 harness adapter 抽象、能力矩阵、ADK 事件规范化 |
| P0 | Codex App-Server Adapter | `specs/codex-app-server-adapter/README.md` | 首期 Codex app-server 连接、thread/turn、JSON-RPC、cancel、schema pin |
| P0 | Session Runtime | `specs/session-runtime/README.md` | session/invocation/turn/container lifecycle、idempotency、lease、continuation |
| P0 | Admission Control | `specs/admission-control/README.md` | 配额、限流、并发、队列准入 |
| P0 | Event Log & SSE | `specs/event-log-sse/README.md` | event log、SSE live/replay、ADK Event 投影 |
| P0 | Security Boundary | `specs/security-boundary/README.md` | secretless、object scope、SSRF、artifact path、redaction、audit |
| P0 | Policy Controller | `specs/policy-controller/README.md` | workspace、network、tool、approval、model policy 编译和准入 |
| P0 | Stores | `specs/stores/README.md` | 持久化事实源：registry/session/event/idempotency/admission 接口、schema 与迁移 |
| P0 | Identity | `specs/identity/README.md` | bearer -> principal、tenant/workspace/userId scope、`IdentityProvider` 接口 |
| P0 | Config | `specs/config/README.md` | env/config 装配、端口表、`load_config`/`create_app` 契约 |
| P1 | Sandbox Runtime | `specs/sandbox-runtime/README.md` | 统一投影 harness sandbox 到 OpenSandbox sandbox/execd/credential vault |
| P1 | Model Proxy | `specs/model-proxy/README.md` | provider credential 隔离、OpenAI-compatible relay、usage normalization |
| P1 | MCP / Tool / Skill Runtime | `specs/mcp-tool-skill-runtime/README.md` | MCP server、MCP proxy、tools、skills materialization、tool restriction |
| P1 | Artifact Store | `specs/artifact-store/README.md` | 输入文件、session 产物、下载、归档和路径安全 |
| P1 | Container Runtime | `specs/container-runtime/README.md` | OpenSandbox AIO Dockerfile、entrypoint、ports、health/ready、shutdown |
| P1 | Runtime Trim | `specs/runtime-trim/README.md` | 用 AIO 官方 `DISABLE_*`/`NODE_VERSION` 关闭 HaaS 用不到的 AIO 服务，降低运行时占用 |
| P1 | Startup | `specs/startup/README.md` | nginx 总入口、sidecar ready、Codex Unix socket 握手探测、启动 DAG、异步 warmup 和时延预算 |
| P1 | Observability | `specs/observability/README.md` | logs、metrics、trace、diagnostics、conformance evidence |
| P1 | Implementation Roadmap | `specs/implementation-roadmap/README.md` | 后续实现阶段、依赖、出口证据和风险收敛 |

## 5. 单篇组件 Spec 标准结构

每个组件 spec 必须包含以下章节。章节可以精简，但不能缺失；暂无结论时必须写明 `Unknown`、影响和下一步验证。

1. `组件定位`
2. `来源与依据`
3. `上游与下游关系`
4. `职责边界`
5. `核心接口`
6. `数据模型`
7. `运行模型与状态机`
8. `安全与权限`
9. `可观测性`
10. `失败与恢复`
11. `测试计划与验收`

## 6. 全局对象模型

| Object | `object` value | Id | Authority | 生命周期 |
|--------|----------------|-----|-----------|----------|
| Harness | `harness` | `chrn_...`（即 ADK `appName`） | Harness Registry | 创建到删除 |
| Invocation | `invocation` | `inv_...` | Session Runtime | 单次 `/run`，保留期内可读 |
| Session | `session` | caller-supplied `sessionId`（默认 `hsess_...`） | Session Runtime | `(appName, userId, sessionId)` 三元组 |
| Turn | `turn` | `turn_...` | Session Runtime | 一个 harness 执行回合 |
| Container | `container` | `cntr_...` | Container Runtime | 跟随 session |
| File | `file` | `file_...` | Artifact Store | 跟随 container/session |
| Event | none | invocation-scoped | Event Log | 保留期内可 replay |

`invocation` 是 public 运行单元；`turn` 是内部执行单元。首期一一对应，但协议上不依赖两者永远相同。

## 7. 全局 HTTP 约定

### 7.1 Header

| Header | 必填 | 说明 |
|--------|------|------|
| `Authorization: Bearer <token>` | 除 health/ready probe 外必填 | HaaS caller token |
| `Idempotency-Key` | 可选（服务端支持去重） | mutating API 幂等；重复 key 返回首次结果，不重复启动 harness |
| `Last-Event-ID` | 可选 | `/run_sse` 与 HaaS native stream 的重连 replay cursor |
| `X-HaaS-Tenant-ID` | 条件 | 多租户部署必填或由 token 解析 |
| `X-HaaS-Workspace-ID` | 条件 | workspace scope，由 token 或 header 解析 |
| `X-HaaS-Trace-ID` | 可选 | 端到端 trace id；缺失时由服务端生成 |

### 7.2 ADK 字段命名

ADK-compatible path 的请求与响应统一使用 `camelCase`（`appName`、`userId`、`sessionId`、`newMessage`、`invocationId`、`lastUpdateTime`）。HaaS native path 沿用 HaaS envelope。

### 7.3 HaaS Envelope

HaaS native endpoints 使用：

```json
{
  "data": {},
  "traceId": "tr_abc"
}
```

分页：

```json
{
  "data": [],
  "nextCursor": null,
  "traceId": "tr_abc"
}
```

单资源（Harness、File、Invocation 等）、列表与诊断响应统一包裹在 `data` 中；
仅分页列表额外携带 `nextCursor`。Health/ready/status/diagnostics 同样返回该
envelope。

错误（所有 public surface 通用）：

```json
{
  "detail": "requested operation is not allowed",
  "haasError": {
    "type": "invalid_request_error",
    "code": "haas_policy_denied",
    "param": null,
    "safeReason": "tool_not_allowed",
    "retryable": false,
    "traceId": "tr_abc"
  }
}
```

HaaS 稳定错误码使用 `haas_` 前缀或 ADK 语义码（如 `session_busy`、`app_not_found`）。
错误码唯一目录见 [ERROR-CODES](haas-protocol/ERROR-CODES.zh-CN.md)，OpenAPI 的
`haasError.code` 与之一一对应。

### 7.4 时间戳约定

| 层面 | 格式 | 说明 |
|------|------|------|
| ADK-compatible public 字段 | float 秒 epoch（`1743712220.385936`） | `Event.timestamp`、`Session.lastUpdateTime`，是 ADK 契约，不可改 |
| HaaS 内部记录 + HaaS native API | 整数毫秒 epoch（`1786400000000`） | `createdAtMs`/`updatedAtMs`/`observedAtMs`/`expiresAtMs`（见 [Stores](stores/README.zh-CN.md)） |

投影层负责 `ms -> float 秒`（`ms / 1000.0`）的转换；任何组件不得在公开面输出
两种格式混用的时间戳。

### 7.5 ID 约定

| 对象 | 前缀 | 生成方 |
|------|------|--------|
| harness / ADK app | `chrn_` | Harness Registry |
| invocation | `inv_` | Session Runtime |
| turn | `turn_` | Session Runtime |
| container | `cntr_` | Container Runtime |
| file | `file_` | Artifact Store |
| event | `evt_` | Event Log |
| session | caller-supplied，默认 `hsess_` | 客户端或 Session Runtime |

`invocationId` 与 `turnId` 首期 1:1 但 id 不相等，映射关系持久化在
`InvocationRecord.turnId`。

## 8. 全局事件约定

Public 事件是 ADK `Event`。canonical event 必须能无损投影为 ADK `Event`：

```json
{
  "id": "evt_0000000001042",
  "invocationId": "inv_abc",
  "author": "codex",
  "timestamp": 1743712220.385936,
  "content": { "role": "model", "parts": [{ "text": "text" }] },
  "actions": { "stateDelta": {}, "artifactDelta": {}, "requestedAuthConfigs": {} },
  "longRunningToolIds": []
}
```

规则：

- `/run_sse` 事件按产生顺序 flush，`streaming:true` 时 `text` part 增量出现。
- invocation 完成即关闭 stream；`/run` 一次性返回事件数组。
- heartbeat 使用 SSE comment `: keep-alive`，不产生事件。
- HaaS 内部 canonical event 可携带 `sequenceNumber`/`eventId` 用于 replay，但投影到 ADK `Event` 时只输出 ADK 字段（HaaS native stream 可额外输出 `haas` 元数据）。
- 非流式 `/run` 输出必须等于 `/run_sse` 流式聚合输出（parity）。

## 9. 兼容与版本策略

1. HaaS 首个协议版本为 `2026-08-26`，northbound 协议层对齐 ADK 2.0 REST API。
2. HaaS native responses 必须返回 `HaaS-Version: 2026-08-26`。
3. 同版本内只能新增 optional field、事件 part 类型或 `haas_` 前缀错误码。
4. 删除、改名、改义、增加必填字段或收紧约束必须发布新版本。

## 10. 组件间依赖方向

```text
api -> protocol schemas
api -> identity / registry / session runtime / admission control / event log / observability
api -> config (create_app)
identity -> security-boundary
session runtime -> harness adapter interface
session runtime / registry / event log / admission control -> stores
admission control -> session runtime / registry / observability
harness adapter -> sandbox runtime / model proxy / mcp-tool-skill runtime / container runtime
sandbox runtime -> OpenSandbox sandbox/execd/credential vault
policy controller -> security-boundary
codex adapter -> Codex app-server native protocol only
model proxy -> provider clients
mcp-tool-skill runtime -> MCP servers / MCP proxy / skill stores
artifact store -> container/session workspace
observability <- all components through logging/metrics interfaces
security-boundary <- all public and adapter boundaries
```

禁止反向依赖：adapter 不 import API route；model proxy 不 import concrete adapter；observability 不改变业务状态。

## 11. 测试与准出

文档初始化阶段：

- `git diff --check`
- Markdown structure/self-review

实现阶段：

- ADK compatibility：用 ADK 官方 client 验证 `/list-apps`、`/run`、`/run_sse`、session 路径。
- API/schema：FastAPI ASGI integration tests。
- SSE：progressive flush、stream 关闭语义、heartbeat、disconnect/replay、parity。
- Codex app-server：真实 handshake、thread/start、turn/start、cancel、schema generation/probe。
- Sandbox：OpenSandbox sandbox/execd/credential vault 投影验证。
- Container：OpenSandbox AIO build、ports、health/ready、SIGTERM drain。
- Runtime trim：`DISABLE_*` 使 code-server/jupyter 不启动，而 browser/VNC/sandbox 与 Codex readiness 不回归（container smoke）。
- Security：secret scan、redaction inverse assertions、SSRF allowlist、artifact traversal probes。

## 12. 维护规则

1. 新增组件先更新本 README 的组件规划表，再创建组件 spec。
2. 变更 public protocol 必须同步更新 schema、tests 和 compatibility notes。
3. 新增 harness adapter 只能通过 `harness-adapter` 定义的 interface 接入。
4. Codex app-server 版本升级必须重新验证本机 `codex app-server --help` 与 generated schema。
5. OpenSandbox AIO base image 升级必须更新 digest、来源和 Docker smoke 证据。
