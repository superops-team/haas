# HaaS 组件规格总览

[English](README.md) | **简体中文**

Status: Draft
Last reviewed: 2026-09-10
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
  +-- Harness Profile
  +-- Session Runtime
  +-- Admission Control
  +-- Event Log & SSE Replay
  +-- Policy Controller
  +-- Manager Delegation
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
Sandbox Runtime（共同 policy 合同：Lite Docker 或 OpenSandbox AIO）
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
| `mpa-codex-worker` 本地参考 | local checkout on 2026-09-10 | 旧项目已经验证 sidecar API、Codex app-server、event log、SSE replay、session registry、model proxy、MCP proxy、secretless runtime 和容器化边界 |
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

HaaS 对上提供两个 HTTP/SSE surface：

| Surface | 路径 | 兼容等级 | 用途 |
|---------|------|----------|------|
| ADK-compatible | `/list-apps`、`/run`、`/run_sse`、`/apps/{app}/users/{user}/sessions/{sid}` | Public API | 长期主协议，drop-in 兼容 ADK 2.0 client |
| HaaS native | `/v1/haas/*` | Public extension | health/ready/status、稳定 capability discovery、diagnostics、harness CRUD、session/event 管理、artifact 等 ADK 未覆盖能力 |

规则：

1. ADK-compatible surface 不得暴露具体 harness 原生字段；HaaS 扩展只放 `haas` 嵌套对象或 `/v1/haas/*`。
2. HaaS native 只能做加法扩展，不得改变 ADK 字段语义。
3. 所有 public surface 使用 `detail` + 结构化 `haasError` 错误形状。

### 3.2 Runtime 边界

目标默认是最小 Lite 镜像（Docker CLI、linux/arm64 与 linux/amd64）；OpenSandbox AIO 为可选 amd64 变体。Mac Apple Silicon 本机构建运行 arm64，不需要 Linux 构建主机。Container Runtime 拥有 image/platform/network/volume 边界，Lite 不依赖 AIO 服务。Lite 实现门禁通过前现有 AIO 工具保持原样；下文 8080 和 AIO 服务描述仅适用于 AIO。

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

Sandbox Runtime 把同一 policy 合同投影到 Lite Docker 或 OpenSandbox AIO；harness 自带 sandbox 仍是内层。Lite 使用隔离 worker/broker network 和 broker memory，AIO 使用 sandbox/execd/vault；两者都要求 runtime egress enforcement 与 URL validation。详见 [Sandbox Runtime](sandbox-runtime/README.zh-CN.md)。

## 4. 组件规划

| 优先级 | 组件 | 路径 | 主要职责 |
|--------|------|------|----------|
| P0 | Architecture | `specs/architecture/README.md` | 系统级分层、事实归属、依赖方向和首期落地顺序 |
| P0 | HaaS Protocol | `specs/haas-protocol/README.md` | ADK-compatible API、HaaS native API、错误和版本策略 |
| P0 | Harness Registry | `specs/harness-registry/README.md` | configured harness catalog、appName 解析、base/capability/model/provider discovery |
| P0 | Harness Profile | `specs/harness-profile/README.md` | 跨 harness 的 provider、MCP、skills、AGENTS.md、workspace/policy 和 budget 版本化配置、动态激活、session snapshot 与显式 rebind 合同 |
| P0 | Harness Adapter | `specs/harness-adapter/README.md` | 多 harness adapter 抽象、能力矩阵、ADK 事件规范化 |
| P0 | Codex App-Server Adapter | `specs/codex-app-server-adapter/README.md` | 首期 Codex app-server 连接、thread/turn、JSON-RPC、cancel、schema pin |
| P0 | Session Runtime | `specs/session-runtime/README.md` | session/invocation/turn/container lifecycle、idempotency、lease、continuation |
| P0 | Admission Control | `specs/admission-control/README.md` | 配额、限流、并发、队列准入 |
| P0 | Event Log & SSE | `specs/event-log-sse/README.md` | event log、SSE live/replay、ADK Event 投影 |
| P0 | Security Boundary | `specs/security-boundary/README.md` | secretless、object scope、SSRF、artifact path、redaction、audit |
| P0 | Policy Controller | `specs/policy-controller/README.md` | workspace、network、tool、approval、model policy 编译和准入 |
| P0 | Manager Delegation | `specs/manager-delegation/README.md` | 面向 manager 的 delegated-session binding、mount manifest、恢复、workspace single-writer 策略、approval relay 和 provider 委派合同 |
| P0 | Manager HaaS Sidecar Backend | `specs/manager-haas-sidecar-backend/README.md` | OpenHarness 本地托管与远程 HaaS sidecar backend 选择、统一 HaaS client 协议、session binding，以及 MCP/skill/model 物化交接 |
| P0 | Manager Product Identity | `specs/manager-product-identity/README.md` | OpenHarness 产品身份、无登录桌面行为和本地账号/连接器边界 |
| P0 | Stores | `specs/stores/README.md` | 持久化事实源：registry/session/event/idempotency/admission 接口、schema 与迁移 |
| P0 | Identity | `specs/identity/README.md` | bearer -> principal、tenant/workspace/userId scope、`IdentityProvider` 接口 |
| P0 | Config | `specs/config/README.md` | env/config 装配、端口表、`load_config`/`create_app` 契约 |
| P1 | Sandbox Runtime | `specs/sandbox-runtime/README.md` | 把共同隔离 policy 投影到 Lite Docker 或 OpenSandbox AIO |
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
| Harness Profile | `harness_profile` | `hprof_...` | Harness Profile / Harness Registry | 版本化 draft/active/retired |
| Invocation | `invocation` | `inv_...` | Session Runtime | 单次 `/run`，保留期内可读 |
| Session | `session` | caller-supplied `sessionId`（默认 `hsess_...`） | Session Runtime | `(appName, userId, sessionId)` 三元组 |
| Turn | `turn` | `turn_...` | Session Runtime | 一个 harness 执行回合 |
| Container | `container` | `cntr_...` | Container Runtime | 跟随 session |
| File | `file` | `file_...` | Artifact Store | 跟随 container/session |
| Event | none | invocation-scoped | Event Log | 保留期内可 replay |

`invocation` 是 public 运行单元；`turn` 是内部执行单元。首期一一对应，但协议上不依赖两者永远相同。`harness` 是 ADK app identity，`harness_profile` 是可版本化执行配置；session 创建时冻结 active profile，后续动态更新只影响新 session，已有 session 必须显式 rebind 才能切换。

## 7. 全局 HTTP 约定

### 7.1 Header

| Header | 必填 | 说明 |
|--------|------|------|
| `Authorization: Bearer <token>` | 除 health/ready probe 外必填 | HaaS caller token |
| `Idempotency-Key` | 可选（服务端支持去重） | mutating API 幂等；重复 key 返回首次结果，不重复启动 harness |
| `Last-Event-ID` | 可选 | ADK `/run_sse` 重连 cursor；HaaS native stream/page 使用 `after_event_id` query parameter |
| `X-HaaS-Invocation-ID` | 成功 `/run`、`/run_sse` 必填 | 持久 accepted invocation id |
| `X-HaaS-Session-ID` | 成功 `/run`、`/run_sse` 必填 | 生效 session id，包括服务端生成值 |
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

Pre-acceptance error 与 post-acceptance integrity error 使用以下结构化形状；正常 accepted execution failure 使用 HTTP 200 terminal event，不使用该 error response：

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
| harness profile | `hprof_` | Harness Profile |
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
- 内部 `CanonicalEventRecord` 持久化稳定 `haas.*` `type`、type-specific 安全 `haas` metadata、`sequenceNumber`、`eventId`；adapter `nativeType` 永不持久化。
- 投影为 ADK `Event` 时只输出 ADK 字段。HaaS native stream 输出强类型 public `CanonicalHaasEvent`，携带稳定顶层 `type` 与经过校验的 `haas` metadata，并剥离内部/原生字段。
- 每个 invocation 的 terminal outcome 只能由唯一稳定 type 表达：`haas.turn.completed`、`haas.turn.failed`、`haas.turn.incomplete` 或 `haas.turn.cancelled`。Client 不得从人类文本或 stream close 推断 terminal outcome。
- 对每种 accepted terminal outcome，非流式 `/run` 输出必须等于 `/run_sse` 的 ADK 流式聚合输出（parity），两者均保持 HTTP 200。Pre-acceptance failure 使用结构化 4xx/5xx；post-acceptance terminal-store integrity failure 携带 accepted metadata 供恢复。该 parity 不要求 HaaS-only native 字段进入 ADK surface。

## 9. 兼容与版本策略

1. HaaS 首个协议版本为 `2026-09-10`，northbound 协议层对齐 ADK 2.0 REST API。
2. HaaS native responses 必须返回 `HaaS-Version: 2026-09-10`。
3. 同版本内只能新增 optional field、事件 part 类型或 `haas_` 前缀错误码。
4. 删除、改名、改义、增加必填字段或收紧约束必须发布新版本。

版本基线说明：

- `2026-08-26` 是内部 draft baseline，从未作为 production compatibility release 发布。
- `2026-09-10` 是首个包含稳定 capability discovery、typed native event、持久 execution acceptance、prepared-to-bound delegation、有界恢复读取的 candidate contract。
- Runtime code 与 conformance test 未实现该合同时，服务不得宣称 `HaaS-Version: 2026-09-10`；必须报告实际已实现版本或保持未发布状态。

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
sandbox runtime -> Lite Docker worker/broker 或 OpenSandbox AIO
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
- Sandbox：验证 Lite/AIO 共同隔离；AIO 额外验证 sandbox/execd/vault。
- Container：OpenSandbox AIO build、ports、health/ready、SIGTERM drain。
- Runtime trim：`DISABLE_*` 使 code-server/jupyter 不启动，而 browser/VNC/sandbox 与 Codex readiness 不回归（container smoke）。
- Security：secret scan、redaction inverse assertions、SSRF allowlist、artifact traversal probes。

## 12. 维护规则

1. 新增组件先更新本 README 的组件规划表，再创建组件 spec。
2. 变更 public protocol 必须同步更新 schema、tests 和 compatibility notes。
3. 新增 harness adapter 只能通过 `harness-adapter` 定义的 interface 接入。
4. Codex app-server 版本升级必须重新验证本机 `codex app-server --help` 与 generated schema。
5. OpenSandbox AIO base image 升级必须更新 digest、来源和 Docker smoke 证据。
