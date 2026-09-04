# Specs AGENTS

[English](AGENTS.md) | **简体中文**

你正在维护 HaaS 协议与组件合同。`specs/` 下的文件是公共接口、组件边界、事件语义、
安全约束和兼容性承诺的事实来源。

## 范围

- `README.md`
- `architecture/README.md`
- `architecture/WALKTHROUGH.md`
- `architecture/VISUAL-STORYTELLING.md`
- `config/README.md`
- `identity/README.md`
- `stores/README.md`
- `haas-protocol/README.md`
- `haas-protocol/*.openapi.yaml`
- `haas-protocol/ERROR-CODES.md`
- `harness-registry/README.md`
- `harness-adapter/README.md`
- `codex-app-server-adapter/README.md`
- `session-runtime/README.md`
- `admission-control/README.md`
- `event-log-sse/README.md`
- `policy-controller/README.md`
- `model-proxy/README.md`
- `mcp-tool-skill-runtime/README.md`
- `artifact-store/README.md`
- `sandbox-runtime/README.md`
- `container-runtime/README.md`
- `security-boundary/README.md`
- `runtime-trim/README.md`
- `startup/README.md`
- `startup/CASES.md`
- `startup/OPENSPEC.md`
- `observability/README.md`
- `implementation-roadmap/README.md`

## 合同规则

始终：

- 每份人工编写的规格都必须维护两个同步语言版本：英文位于规范路径，简体中文位于同目录的 `*.zh-CN.md`。OpenAPI YAML 保持单一、语言无关的机器合同。
- 两个版本的 H1 下都必须提供双向语言切换。公共接口、规范性要求、示例和兼容性声明在两种语言中必须语义一致。
- 新增、重命名或删除组件时，保持 `specs/README.md` 与子规格一致。
- 保持 ADK 兼容端点、事件、对象结构和错误码与 `specs/haas-protocol/` 一致。
- 公共 API 与持久化 schema 优先采用增量变更。
- HTTP 合同字段、路由、Header 或错误发生变化时，更新 OpenAPI 骨架。
- 记录对 ADK 兼容 API 与 HaaS 原生 API 的兼容性影响。旧 `/v1/codex-worker/*` shim 不在本项目范围内（见 specs/README 3.1.1）。
- 将 adapter 专属细节限制在对应 adapter 规格内。
- 所有示例必须不含 secret。

以下情况先询问：

- 对公共字段、路径、事件类型、状态值或错误码做破坏性修改或重命名。
- 将选定的北向协议从 ADK 2.0 兼容协议改为其他协议。
- 将 Docker 基础镜像族从 OpenSandbox AIO 改为其他镜像。

绝不：

- 在规格或示例中放入原始 provider credential、Authorization 值、cookie、presigned URL、raw prompt 或完整 tool payload。
- 当正文规格另有定义时，将生成的 schema 视为权威来源。
- 让某个 harness runtime 的原生协议成为公共 HaaS 要求。
