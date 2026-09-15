# Implementation Roadmap 组件规格

[English](README.md) | **简体中文**

Status: Draft
Last reviewed: 2026-09-12

## 1. 组件定位

Implementation Roadmap 定义 HaaS 从文档基线进入首期实现的分层任务顺序。它不是临时项目计划，而是为了保证后续代码改动可以从 specs 追溯到协议、组件和测试合同。

## 2. 来源与依据

| 来源 | 采用内容 |
|------|----------|
| Architecture spec | HaaS sidecar-first 架构和首期 Codex app-server 目标 |
| HaaS Protocol spec | ADK 2.0-compatible API 和 HaaS native extension |
| Harness Adapter spec | adapter seam 与多 harness 扩展规则 |
| Sandbox Runtime spec | Lite Docker 与 OpenSandbox AIO 的共同 policy 投影 |
| Container Runtime spec | OpenSandbox AIO base image 与 health/ready 约束 |

## 3. 上游与下游关系

| 方向 | 对象 | 关系 |
|------|------|------|
| 上游 | 开发者 / AI agent | 按本路线选择下一步实现范围 |
| 下游 | 所有 specs | 每个任务引用对应组件合同 |
| 下游 | 代码、测试、Dockerfile | 后续实现交付物 |

## 4. 职责边界

负责：

- 定义首期实现顺序。
- 定义每个阶段的输入、输出和进入/退出标准。
- 限制 scope creep，保证先跑通 ADK 2.0 协议 + Codex app-server 最小链路，再补 Sandbox Runtime 标准化。

不负责：

- 不替代具体组件 spec。
- 不保存临时验证日志。
- 不承诺具体排期日期。

## 5. 核心接口

本组件不提供运行时 API。任务接口是文档化的阶段边界：

| Stage | Entry criteria | Exit criteria |
|-------|----------------|---------------|
| S0 specs baseline | 当前文档已创建 | specs lint/link/schema 检查通过 |
| S1 project skeleton | S0 complete | Python package、FastAPI app、Makefile、tests skeleton 可运行 |
| S2 protocol core | S1 complete | ADK `/list-apps`、`/run`、`/run_sse`、session 路径 + 错误 + schema 测试通过 |
| S3 fake adapter | S2 complete | fake harness 完成 streaming/non-stream/cancel/session replay 测试 |
| S4 Codex adapter | S3 complete | Codex app-server handshake、thread/turn、cancel E2E 通过 |
| S5 sandbox + container | S4 complete | 默认 Lite arm64/amd64 与可选 AIO amd64 的共同隔离通过 |
| S6 extended features | S5 complete | files/artifacts/MCP/skills/model proxy/admission control 按 specs 逐项通过 |
| S7 manager delegation backend | S6 complete，或一个显式收窄的 vertical slice 具备等价 store/SSE/proxy/container 门禁 | 全新 OpenHarness profile 默认 HaaS `local_managed` + autostart；符合条件的 chat 无需 keyword gate 即绑定 HaaS、执行 delegated Codex work、流式事件、TTL 后恢复，并 fail closed 且不静默本地 fallback |

## 6. 数据模型

```json
{
  "stage": "S2",
  "status": "ready",
  "dependsOn": ["S1"],
  "specs": ["haas-protocol", "session-runtime", "event-log-sse"],
  "exitEvidence": [
    "unit tests",
    "integration tests",
    "ADK compatibility checks"
  ]
}
```

## 7. 运行模型与状态机

```text
S0 specs baseline
  -> S1 project skeleton
  -> S2 ADK protocol core
  -> S3 fake adapter
  -> S4 Codex app-server adapter
  -> S5 Sandbox Runtime + OpenSandbox AIO container
  -> S6 Extended features (model proxy / MCP / skills / artifacts / admission control)
  -> S7 Manager Delegation backend
  -> S8 additional harness adapters
```

Do not start S4 before S3 fake adapter proves that the public protocol and session runtime are independent of Codex.

准入口径澄清：`S3 fake adapter` 是协议/组件的**解耦验证**（离线单元+集成）；`铁律 #9
「最小端到端」` 指首期 P0 harness（Codex）的最终准出，落在 **S4**。两者不冲突：
S2-S3 证明协议与 harness 无关，S4 完成真实链路（见 [architecture](../architecture/README.zh-CN.md)）。

## 7.1 当前 Checkout 实现状态（2026-09-12）

该矩阵来自仓库静态检查，不是 release claim。状态定义：

- `implemented_and_verified`：实现存在，且本次 review 已执行 required gate 并通过。
- `implemented_not_reverified`：实现/历史证据存在，但本次未重跑完整 stage gate。
- `partial`：存在实质实现，但缺少当前合同或门禁。
- `spec_only`：合同已定义；实现/测试缺失，或仍实现旧语义。
- `not_started`：未发现实质实现证据。
- `not_run`：本次未执行验证。

| Stage / capability | 状态 | 仓库证据 | Stage exit 前缺口 |
|--------------------|------|----------|-------------------|
| S0 specs baseline | `implemented_and_verified` | 本次已更新组件 spec 与双语文档；OpenAPI YAML/ref、error parity、link、关键 EN/ZH parity、歧义扫描、`git diff --check`、pre-commit 均通过 | 实现变更后、声明协议版本前必须重跑 |
| S1 project skeleton | `implemented_not_reverified` | 已有 `haas/`、FastAPI app、tests、Makefile、Docker 文件 | 本次未重跑完整 S1 命令集 |
| S2 protocol core（旧基线） | `partial` | 已有 ADK route、session、idempotency、SSE、error 与 tests | 实现 2026-09-10 acceptance、userId identity、version header、有界 session read、查询 API |
| S3 fake adapter | `partial` | 已有 fake/slow/blocking adapter 与 contract tests | 将 accepted-failure 502 行为/测试改为 HTTP-200 terminal；实现 typed native event projection/migration |
| S4 Codex adapter | `partial`；真实 E2E `not_run` | 已有 app-server transport、normalizer、probe、recovery、schema tests | acceptance preflight 顺序、typed canonical projection、当前版本 conformance、真实 Codex E2E |
| S5 sandbox + container | `partial`；Docker smoke `not_run` | 已有 AIO Dockerfile/compiler/client/runtime test；Lite 仅有目标合同 | 实现 Lite Dockerfile/private worker-broker/session volume，运行 Lite arm64/amd64 与 AIO amd64 smoke |
| S6 extended features | `partial` | 已有 model proxy、MCP/skill、artifact、admission 与 tests | SQLite production backend、upload idempotency、全局 HaaS-Version middleware、有界 page store、当前协议 conformance |
| S7 Manager backend | `partial` | 已有 manager delegation client/settings/supervisor/routing 与 HaaS delegated API | 产品默认值、PREPARED→HAAS_BOUND、capability API、dual-stream bridge、cursor client、invocation/page API、P0 workspace gate |
| S8 additional adapters | `not_started` | 未发现 Pi/OpenCode/AMP adapter 实现 | S7 P0 gate 通过后才能开始 |
| Human approval 与 input bridge | `spec_only`；release blocker | 已有 approval store/resolve API 与 event schema；structured input contract 已定义；Codex capability 仍为 `unattended_only` 且 input handling unsupported | Codex native command/file approval 与 request-user-input bridge、断线恢复、UI/E2E；通过前禁用控件 |
| 过程可见性与任务完成度 | `spec_only`；release blocker | 2026-09-12 打包会话证明 native reasoning/tool 已产生，但 typed projection、terminal integrity、interactive input 与 task completion 尚不完整 | 默认桌面 HaaS 路径宣称 production-ready 前实现 FV-15–FV-19 |
| Snapshot/remote workspace | `spec_only`，P1 | capability 名称存在并标记 unsupported | discriminated schema、transfer/sync/conflict/retention/recovery 设计与实现 |

### 7.2 2026-09-10 P0 合同 Delta 实现队列

以下事项阻塞声明 `HaaS-Version: 2026-09-10`，也阻塞 S7 产品准出；按垂直依赖顺序执行：

1. **Protocol/store migration：** 更新 runtime protocol constant；实现 event schema v2
   migration、typed `CanonicalEventRecord`、`project_haas()`、terminal metadata 校验。
2. **Durable acceptance：** 无副作用 preflight 位于 atomic
   `InvocationRecord(status=accepted)` 之前；accepted failure 收敛到 HTTP-200 ADK/native
   terminal event；更新 idempotency 与 integrity-failure handling。
3. **Recovery reads：** 实现 invocation GET、有界 events page、有界 ADK Session 413、
   approval list（P0 unattended mode 返回空）。
4. **Identity：** 增加 `defaultUserId`/`allowedUserIds`、static/JWT mapping、显式
   delegated-user authorization、跨 scope negative tests。
5. **Harness profile：** 实现 versioned Harness Profile store/API、profile
   validate/activate、active profile fingerprint、AGENTS.md snapshot、session
   `EffectiveHarnessProfile` freeze，以及显式 profile rebind。
6. **Capability discovery：** 从 registry/adapter/runtime/profile 事实实现 caller-scoped
   `/v1/haas/capabilities`；Manager 对未知值 fail closed。
7. **Manager binding/client：** 实现默认 HaaS local-managed profile、首个 accepted
   invocation 时 PREPARED→HAAS_BOUND、P0 bind-mount-only gate、稳定 idempotency
   key/cursor、delegated desired/applied 配置屏障、持久 send queue、双流完成屏障、
   endpoint binding 恢复和确认过期后的关联新 attempt。
8. **Persistence/version：** 实现真实 SQLite production assembly、upload idempotency、
   HaaS-Version response middleware、schema migration。
9. **Lite/AIO runtime：** 实现独立 Lite Dockerfile、private worker/broker 隔离、
   per-session volume、arm64/amd64 Lite 构建，并保留 AIO amd64。
10. **P0 release evidence：** 运行 offline unit/integration/ADK suites、coverage gate、
   Mac Docker Lite arm64、Lite amd64、AIO amd64 build/smoke、真实 Codex E2E、secret scan、code-review、
   brooks-review、brooks-test。任何未运行 real-service gate 都保持 `not_run`，并阻塞
   production-ready claim。

### 7.3 Dev Loop 纵向任务与 Case 映射

仓库规则要求任务计划留在 `specs/`，不提交临时 OpenSpec/verification-report 目录。按顺序执行；每个 task 先写对应失败测试，只有功能 Case 通过才关闭。

| Task | 纵向交付 | 主要文件 | 依赖 | 验证 Case |
|------|----------|----------|------|-----------|
| DL-01 | SQLite Store protocol/migration/transaction、幂等 expiry/tombstone、event schema v2 | `haas/stores/*`、`haas/events.py`、`haas/sessions.py` | 无 | FV-07、FV-13 |
| DL-02 | Versioned profile 与 frozen provider/content route | registry/profile/model proxy/MCP-skill runtime | DL-01 | FV-02、FV-05 |
| DL-03 | Delegated desired/applied model、fenced reconciler、turn admission barrier | `haas/api.py`、`haas/runtime/reconciler.py`、delegated store | DL-01、DL-02 | FV-03–FV-05 |
| DL-04 | Canonical pending/applied/failed event 与 restart recovery | event/session/runtime API | DL-01、DL-03 | FV-03、FV-04、FV-13 |
| DL-05 | Lite image、platform/digest resolver、session volume、hardened worker、private broker network | Dockerfile/Makefile/runtime/security | DL-01、DL-02 | FV-08–FV-11 |
| DL-06 | Manager endpoint/supervisor/typed client/routing、PREPARED acceptance | manager HaaS module 与 settings/session store | DL-01–DL-03 | FV-01、FV-05、FV-12 |
| DL-07 | Manager durable send queue、三流 bridge、completion barrier、linked attempt | manager store/stream/configuration module | DL-03、DL-04、DL-06 | FV-03、FV-04、FV-06、FV-07 |
| DL-08 | AIO 回归、migration/conformance、真实 E2E、release gate | quality script/E2E/Makefile | DL-01–DL-07 | FV-09–FV-14 |
| DL-09 | Typed Codex 过程事件、唯一 terminal 持久化、Manager native/ADK 并发桥接 | adapter/event/session/manager stream module | DL-01、DL-04、DL-07 | FV-15、FV-19 |
| DL-10 | Model capability 生命周期/刷新与可见非成功恢复 | model proxy/session/adapter/manager UI | DL-02、DL-09 | FV-17 |
| DL-11 | Codex approval 与 structured-input bridge，支持持久重连恢复 | app-server RPC/adapter/session/native API/Manager GUI | DL-09、DL-10 | FV-12、FV-16 |
| DL-12 | Manager task-completion controller、有界 continuation、verification gate 与打包验收 | manager task state/transcript/automation/E2E | DL-09–DL-11 | FV-18 |
| DL-13 | Session-page checkpoint、authoritative terminal reconciliation 与 stale-running 重连恢复 | Manager HaaS client/stream bridge/session binding/WebSocket startup | DL-07、DL-09、DL-12 | FV-24 |
| DL-14 | 前台 prompt 的 transcript 跟随周期与布局提交后的实时进展滚动 | Manager GUI composer/transcript viewport 与 Playwright fixture | DL-09、DL-12 | FV-25 |
| DL-15 | 已 accepted attempt 的重试 readback 与同 invocation 事件流恢复 | Manager HaaS attempt/stream bridge/session binding | DL-07、DL-13 | FV-26 |

对齐结论：Container Runtime、Manager Backend、Manager Delegation、Harness Profile、Event Log、Stores、Security Boundary、HaaS Protocol 的全部 P0/P1 要求均映射到 FV-01–FV-26 和至少一个 DL task。Remote workspace 继续由 FV-12 验证 capability negative。Human approval/input 在 DL-11 通过前保持不 advertise，但已是桌面交互合同的 release blocker，不再作为可延期体验优化。

## 8. 安全与权限

- Security Boundary spec gates every stage.
- New stages cannot relax secretless rules without updating Security Boundary.
- Any runtime stage that touches Docker, provider credentials, MCP headers or artifact download must add negative tests before implementation is considered done.

## 8.1 测试隔离开关与覆盖率

- 默认测试必须离线：不访问真实网络、真实 HOME、真实 provider、真实 Codex/OpenSandbox。
- 真实服务测试用显式开关：`HAAS_E2E=1`（总开关）或 `HAAS_E2E_CODEX=1` / `HAAS_E2E_OPEN_SANDBOX=1`（分项）；未开开关跳过，记为 `not_run`，不得写成通过。
- 测试框架：pytest + pytest-asyncio + coverage。
- 覆盖率门禁（AGENTS.md）：核心模块 ≥90%；credential、redaction、policy、proxy token、artifact path、日志脱敏路径 ≥95%。

## 9. 可观测性

Each implementation stage must report:

- changed specs and code files;
- commands run and result;
- unrun checks and reason;
- known risks and owner;
- next-stage readiness.

## 10. 失败与恢复

| 场景 | 行为 |
|------|------|
| Stage exit evidence missing | Do not start dependent stage |
| Spec conflict discovered | Update affected specs before code |
| ADK compatibility fails | Fix protocol implementation or downgrade advertised capability |
| Codex E2E fails | Keep adapter unavailable; do not mark `codex` base ready |
| Sandbox Runtime smoke fails | Do not claim harness sandbox 标准化；block S6 |
| Docker smoke fails | Do not publish runtime image |
| Manager delegation restore fails | 保持 manager session HaaS-bound，返回安全 delegated error，不回退本地执行 |

## 11. 测试计划与验收

- S0: docs-only checks and OpenAPI parse/ref validation.
- S1: package import, app factory startup, Makefile commands exist.
- S2: ADK schema、route、error 测试；持久 invocation acceptance 边界；每种 accepted terminal outcome 的 HTTP-200 `/run` vs `/run_sse` parity；pre-acceptance 4xx/5xx 与 post-acceptance store-integrity recovery。
- S3: fake adapter contract tests，包括 accepted start/stream/finalize failure、partial-output retention、幂等 HTTP-200 replay 且不重复执行。
- S4: real Codex app-server E2E.
- S5：共同 Sandbox Runtime 验证 + Lite arm64/amd64 与 AIO amd64 Docker build/run smoke。
- S6: feature-specific integration, security and ADK compatibility expansion.
- S7: OpenHarness 默认 HaaS `local_managed` + autostart profile、显式 local opt-out、无 trigger-keyword 路由门禁、稳定 caller-scoped `GET /v1/haas/capabilities`、带稳定 `type`/`haas` metadata 的 typed native `CanonicalHaasEvent`、Volcengine Ark provider identity、HaaS native delegated-session API、live `/run_sse`、persistent store、每 delegated session 一个容器、`/workspace:rw` mount 校验、workspace single-writer lock、capability-gated human approval 与 structured input、model proxy secretless path、task-completion integrity，以及显式 flag 下的 Docker/Codex smoke。
- S8: manager delegation backend 稳定后扩展 additional harness adapter。
