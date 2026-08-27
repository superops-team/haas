# Implementation Roadmap 组件规格

Status: Draft
Last reviewed: 2026-08-26

## 1. 组件定位

Implementation Roadmap 定义 HaaS 从文档基线进入首期实现的分层任务顺序。它不是临时项目计划，而是为了保证后续代码改动可以从 specs 追溯到协议、组件和测试合同。

## 2. 来源与依据

| 来源 | 采用内容 |
|------|----------|
| Architecture spec | HaaS sidecar-first 架构和首期 Codex app-server 目标 |
| HaaS Protocol spec | ADK 2.0-compatible API 和 HaaS native extension |
| Harness Adapter spec | adapter seam 与多 harness 扩展规则 |
| Sandbox Runtime spec | OpenSandbox sandbox/execd/credential vault 统一投影 |
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
| S5 sandbox + AIO container | S4 complete | Sandbox Runtime 投影 + OpenSandbox AIO-derived image smoke 通过 |
| S6 extended features | S5 complete | files/artifacts/MCP/skills/model proxy/admission control 按 specs 逐项通过 |

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
  -> S7 additional harness adapters
```

Do not start S4 before S3 fake adapter proves that the public protocol and session runtime are independent of Codex.

## 8. 安全与权限

- Security Boundary spec gates every stage.
- New stages cannot relax secretless rules without updating Security Boundary.
- Any runtime stage that touches Docker, provider credentials, MCP headers or artifact download must add negative tests before implementation is considered done.

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

## 11. 测试计划与验收

- S0: docs-only checks and OpenAPI parse/ref validation.
- S1: package import, app factory startup, Makefile commands exist.
- S2: ADK schema、route 和 error 测试；`/run` vs `/run_sse` parity。
- S3: fake adapter contract tests.
- S4: real Codex app-server E2E.
- S5: Sandbox Runtime 投影验证 + Docker build/run smoke on OpenSandbox AIO-derived image.
- S6/S7: feature-specific integration, security and ADK compatibility expansion.
