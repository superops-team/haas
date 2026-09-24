# Agent Skills 组件规格

[English](README.md) | **简体中文**

状态：已实施；审查与 pre-commit 门禁通过
最近评审：2026-09-24
变更 ID：agent-skills-tauri-python-adoption
相关规格：[Manager GUI 性能](../manager-gui-performance/README.zh-CN.md)、[Manager HaaS Sidecar 后端](../manager-haas-sidecar-backend/README.zh-CN.md)、[上下文工程审查](../../.agents/skills/context-engineering-review/SKILL.md)

## 1. 组件角色

Agent Skills 负责仓库本地 `.agents/skills/` 目录：模块化、自包含的知识包，将通用编码 Agent 转化为 HaaS 专用 Agent。它涵盖 skill 清单、质量标准、来源追踪、多工具兼容，以及从顶级开源项目适配高价值 skill 的引入。

本规格覆盖两个引入批次：

1. **Tauri/Rust 桌面批次**：多 runtime 调试、Tauri WebView 中 React 渲染性能、HaaS 专属 lens 并行代码审查、跨层质量检查增强。
2. **Python/FastAPI 后端批次**：FastAPI API/SSE 契约、async/并发、sidecar 生命周期、结构化日志、pytest 契约测试、类型检查、secretless 模式。第 5.4 节记录已完成的调研与引入结果。

本组件不修改运行时代码、协议 schema 或构建配置。它是开发者工具合同。

## 2. 来源与依据

### 2.1 现有 skill 清单（引入前）

仓库在 `.agents/skills/` 下有 11 个 skill：agent-browser、beads、code-automation、context-engineering-review、dogfood、fallow、react-best-practices、react-typescript-kit、requirement-spec、spec-coding、ui-automation。

### 2.2 已识别缺口

1. **无多 runtime 调试 skill**。HaaS 运行三个协作 runtime（React WebView / Tauri Rust shell / Python sidecar），bug 常跨越 IPC 边界，但无 skill 指导 runtime 选择、证据捕获和跨 runtime 复现。
2. **无组件级 Tauri WebView 渲染性能 skill**。`manager-gui-performance` spec 覆盖架构级优化（轮询去重、路由分割、状态边界），但缺少组件级诊断方法（身份稳定性、Context 切片、Profiler 验证）。
3. **无 Python 后端 skill**。HaaS sidecar 是 Python HTTP/SSE 服务，但无 skill 覆盖 FastAPI SSE 契约、async/并发、sidecar 生命周期、结构化日志、pytest 契约测试或 secretless 脱敏。

### 2.3 外部调研基线（Tauri/Rust 批次）

调研核验了 17 个项目（9 个通过实际 `tauri.conf.json` / `Cargo.toml` 确认为 Tauri+Rust）。仅两个 Tauri 项目含 `.agents/skills`：

| 项目 | Stars | Skills | 核心可迁移价值 |
|---|---:|---|---|
| [GitButler](https://github.com/gitbutlerapp/gitbutler) | 21.7k | 5 | `lite-render-perf`：React Compiler memoization 分析、Context selector 纪律、CDP 验证。`but-performance-tests`：Hyperfine shell 性能场景模式。 |
| [EcoPaste](https://github.com/EcoPasteHub/EcoPaste) | 7.4k | 13 | `trellis-check`：跨层数据流验证、代码复用检查、spec 同步。Trellis 会话生命周期工作流。 |

[Logseq](https://github.com/logseq/logseq)（45k stars，Electron 非 Tauri）含 11 个 skills。其 `logseq-debug-workflow`（多 runtime 证据闭环调试）和 `logseq-review-workflow`（并行多 lens 审查）与技术栈无关，可直接迁移。[Bruno](https://github.com/usebruno/bruno)（47k stars，Electron）在 `.claude/skills` 中提供 6-lens 并行代码审查模式。

高星 Tauri 项目无 `.agents/skills`：Spacedrive（39k）、Cap（22.7k）、Pake（61.7k）、cc-switch（133.8k）、Clash Verge Rev（~147k）。

### 2.4 外部调研基线（Python/FastAPI 批次）

调研通过 `gh api` 核验了 16 个顶级 Python/FastAPI 项目。仅 5 个含 `.agents/skills`，且全部集中在 tiangolo/pydantic 生态系：

| 项目 | Stars | Skills | 核心可迁移价值 |
|---|---:|---|---|
| [FastAPI](https://github.com/fastapi/fastapi) | 102.6k | 1 skill + 6 references | `references/streaming.md`：`EventSourceResponse` + `ServerSentEvent` SSE 模式（直接用于 `POST /run_sse`）；`responses.md`：response_model 敏感字段过滤（secretless）；Annotated 依赖注入；async/sync 规则 |
| [Pydantic](https://github.com/pydantic/pydantic) | 28.9k | 1 skill | Field() 元数据分类、Annotated 模式、union 元数据位置陷阱——用于 HaaS 协议/ADK Event schema |
| [Pydantic AI](https://github.com/pydantic/pydantic-ai) | 20.1k | 6 个 dev-workflow skills | `adding-a-provider-api-feature`："先找现有 abstraction 再设计" adapter 扩展方法论；`complete-partial-pr`：streaming/non-streaming、sync/async 完整性检查清单 |
| [SQLModel](https://github.com/fastapi/sqlmodel) | 18.3k | 1 skill | table=True 与 non-table schema 分离——低-中价值（HaaS DB 层尚未指定） |
| [Full Stack FastAPI Template](https://github.com/fastapi/full-stack-fastapi-template) | 45.7k | symlinks + library-skills | Wheel 打包 skills 模式（从 `.venv` symlink）——仅设计参考 |

无 `.agents/skills` 的项目：Django（91k）、Flask（74.8k）、uv（90.1k，有 `.codex/skills`）、Celery（28.9k）、Starlette（12.6k）、Uvicorn（11k）、HTTPX（15.5k）、Typer（20k）、FastAPI Best Practices（18.1k）、FastAPI Users（6.2k）、SQLAlchemy（12.2k）。`.agents/skills` 约定在 tiangolo 生态外的 Python 后端生态采用率极低。

HaaS 空白维度（无任何开源 skill 覆盖）：sidecar 生命周期管理、async session/状态机、结构化脱敏日志、pytest contract/集成/E2E、secretless 凭证处理、错误恢复矩阵、Docker 多架构构建、SSE 断线重连/replay。这些仍为 HaaS 原创工作。

## 3. 上下游关系

```text
根 AGENTS.md（权威）
  -> specs/agent-skills（本组件：清单 + 质量合同）
  -> .agents/skills/*（skill 实现）
  -> .agents/skills/SOURCES.md（来源追踪）
  -> .claude/skills -> ../.agents/skills（多工具兼容符号链接）
  -> 编码 Agent 运行时（按 frontmatter 触发加载 skills）
```

## 4. 目标、非目标与用户场景

### 4.1 目标

1. 新增多 runtime 调试 skill，覆盖 WebView / Rust shell / Python sidecar，证据闭环 before/after 验证。
2. 新增 Tauri WebView React 渲染性能 skill，适配 HaaS 本地状态模型（不依赖 React Compiler），覆盖身份稳定性、Context 边界、Profiler 验证。
3. 增强 `code-automation`，增加跨层数据流验证和 spec 同步检查，不重复现有验证指南。
4. 新增并行代码审查 skill，含 HaaS 专属 lens（secretless、协议兼容、adapter 隔离、恢复语义、跨平台），与现有两轮 code-review + brooks-review + brooks-test 门禁对齐。
5. 新增 `.claude/skills` 符号链接指向 `../.agents/skills`，确认对 `.gitignore` 和工作树状态安全。
6. 更新 `SOURCES.md`，记录所有新引入或适配 skill 的来源。
7. Python/FastAPI 批次：引入调研发现的高价值 skill，与现有和 Tauri/Rust 批次 skills 去重。

### 4.2 非目标

- 不修改运行时代码（Python、Rust、TypeScript）。本规格仅覆盖开发者工具。
- 不引入 React Compiler。渲染性能 skill 必须适用于 HaaS 当前 React 18 + 本地状态模型。
- 本批次不引入 Rust CLI 性能测试 skill 或 Rust CLI 模式 skill。这些是 P2 未来项，待 Rust 侧（`openharness-desktop`）超出 `lib.rs` + `main.rs` 后再考虑。
- 不创建 `prd-spec/`、`docs/verification/` 或临时验证报告。
- 不修改用户在现有 skills（`code-automation`、`spec-coding`、`ui-automation`）中的未提交改动。增强仅为追加式。
- 不引入 TUI 测试、Logseq 专属 REPL 或其他 HaaS 不存在的技术专属模式。

### 4.3 用户场景

- 给定一个 sidecar 重启后 GUI 显示过期数据的 bug，Agent 加载 `haas-debug-workflow`，选择 Python sidecar runtime，用日志证据复现，应用修复，跨三个 runtime 验证。
- 给定一个实时 transcript 导致每个 token delta 都重渲染 sidebar 的问题，Agent 加载 `tauri-react-render-perf`，用 React Profiler 验证身份稳定性，应用窄状态边界。
- 给定一个同时触及 `haas/api/`、`haas/protocol/`、`haas/harnesses/` 的变更，Agent 的 `code-automation` 增强检查验证跨层数据流并确认 spec 同步。
- 给定一个 Tauri bundle 变更的 PR，Agent 加载 `parallel-code-review`，并行运行 secretless、协议兼容、跨平台 lens，产出去重 findings 报告。

## 5. 责任边界与功能需求

### 5.1 P0：haas-debug-workflow

**范围**：HaaS 桌面 + sidecar 多 runtime 调试。

- 定义三个 runtime 及选择标准：
  - `webview`：React 渲染、DOM、UI 状态、WebSocket 投递、Tauri WebView 专属行为（窗口焦点、document visibility、IPC 调用结果）。
  - `rust-shell`：Tauri Rust shell、sidecar 进程监管、托盘图标、autostart、single-instance、updater、notification、STT 引擎、跨平台 IPC。
  - `python-sidecar`：HTTP/SSE API、session runtime、event log、harness adapter、model proxy、MCP、policy、artifact store、持久化。
- 强制证据闭环工作流：选择 runtime 并说明理由 → 编辑前复现 → 捕获具体证据（日志、REPL 输出、失败测试、CLI 输出）→ 应用最小合理修复 → 重新运行相同复现 → 捕获修复后证据 → 当 bug 涉及设置、启动、持久化、窗口创建或跨进程行为时，包含重启/重载/重开验证。
- 列出每个 runtime 的 HaaS 专属日志位置和检查命令。
- 定义必需输出结构：选择的 runtime、修复前复现、根因、应用的修复、修复后验证、额外验证、缺口或阻塞。
- 明确声明：当 bug 跨越 runtime 或 IPC 边界时，仅单元测试不足。
- **适配来源**：[Logseq `logseq-debug-workflow`](https://github.com/logseq/logseq/tree/main/.agents/skills/logseq-debug-workflow)，技术无关模式适配为 HaaS runtimes。

### 5.2 P0：tauri-react-render-perf

**范围**：Tauri WebView 中组件级 React 渲染性能，适配 HaaS 本地状态模型。

- 覆盖三个不依赖 React Compiler 的机制：
  1. **身份稳定性**：派生值、context 值、props、effect 依赖在数据不变时必须返回稳定身份。reducer 在无操作更新时必须 early-return。hook 结果解构必须避免新身份。
  2. **Context 边界**：`use(Context)` 在身份变化时重渲染每个 consumer，无 selector。每行或每分支状态必须使用窄订阅（HaaS 使用本地状态而非 Redux——skill 必须适配为 `useSyncExternalStore` 或组件级状态模式，而非 Redux `useAppSelector`）。
  3. **Profiler 验证**：命名 React Profiler 边界必须验证实时 token delta 不重渲染非活跃分支（sidebar、composer、right rail）。引用 `manager-gui-performance` spec P0-2 验收标准。
- 提供 HaaS 专属验证命令：Vitest + Profiler 计数器、生产 Vite build + preview Playwright 渲染观察。
- 明确声明本批次不引入 React Compiler；身份纪律和 Context 边界模式与 compiler 无关。
- 不重复 `react-best-practices`（通用 Vercel 规则）或 `react-typescript-kit`（HaaS 实现指南）。聚焦于 Tauri WebView 中的渲染性能诊断。
- **适配来源**：[GitButler `lite-render-perf`](https://github.com/gitbutlerapp/gitbutler/tree/main/.agents/skills/lite-render-perf)，Redux/react-query 示例替换为 HaaS 本地状态模式。

### 5.3 P1：code-automation 增强（跨层 + spec 同步）

**范围**：对现有 `.agents/skills/code-automation/SKILL.md` 的追加式增强。现有内容（按语言选择验证、单元测试资产计划、结果格式）保持不变。

- 新增**跨层数据流检查**：当变更文件跨越 3+ HaaS 层（api、protocol、harnesses、sessions、events、policy、model_proxy、mcp、artifacts、runtime、observability）时，验证读流（storage → service → API → UI）和写流（UI → API → service → storage）追踪正确，类型/schema 在层间传递，错误传播到调用方。
- 新增**spec 同步检查**：当行为契约变更时，确认 `specs/<component>/README.md` 已更新。当发现非显然模式或经验时，询问相关 spec 是否应捕获。引用 AGENTS.md "文档不能漂移"。
- 新增**代码复用检查**：创建新工具函数或常量前，搜索现有相似代码。若 2+ 处定义相同值，提取为共享常量。
- 增强不重复 `spec-coding`（覆盖 spec 编写工作流）或 `context-engineering-review`（审计上下文质量）。它为变更代码检查增加验证步骤。
- **适配来源**：[EcoPaste `trellis-check`](https://github.com/EcoPasteHub/EcoPaste/tree/main/.agents/skills/trellis-check)，跨层和 spec 同步章节适配为 HaaS 层名。

### 5.4 P0/P1：Python/FastAPI 批次

调研识别出三个高价值 skill，与现有和 Tauri/Rust 批次 skills 无重叠。

#### 5.4.1 P0：fastapi-backend

**范围**：HaaS sidecar 的 FastAPI 实现指南，渐进式披露（SKILL.md 总览 + `references/streaming.md` 深度）。

- SKILL.md 必须覆盖：`Annotated[..., Depends(...)]` 依赖注入并创建 type alias 复用；`yield` 依赖用于资源清理（DB session、连接池）；async/sync 选择规则（默认 `def` 在线程池执行；阻塞代码绝不能在 `async def` 中运行；混合调用使用 Asyncer `asyncify`/`syncify`）；`response_model` 自动过滤敏感字段；路由组织（prefix/tags/dependencies）。
- `references/streaming.md` 必须覆盖：`EventSourceResponse` + `ServerSentEvent` 模式用于 `POST /run_sse`；`event`/`id`/`retry`/`comment` 字段；`StreamingResponse` 用于 bytes/JSON Lines；与 `haas-protocol` spec 的 SSE 约定对齐（`data:` 帧、heartbeat 用 SSE comment、invocation 完成即关闭 stream、`POST /run` 与 `/run_sse` 输出 parity）。
- 必须引用 HaaS 专属命令：`uv run --extra dev pytest tests/ -q`、`uv run --extra dev mypy haas`、`make test-integration`。
- 不重复 `code-automation`（验证命令选择）或 `haas-debug-workflow`（调试工作流）。聚焦 FastAPI 实现模式。
- **适配来源**：[FastAPI 官方 skill](https://github.com/fastapi/fastapi/tree/master/fastapi/.agents/skills/fastapi)，HaaS 适配版，SSE 参考与 `haas-protocol` 对齐。

#### 5.4.2 P0：pydantic-modeling

**范围**：HaaS 协议 schema、ADK Event payload 和内部记录的 Pydantic 数据建模最佳实践。

- 必须覆盖：`Field()` 元数据分类（field-specific vs type-specific）；优先使用 `Annotated` + `Field()` 而非裸 `Field()` 赋值；union 元数据位置陷阱（field-specific 元数据如 `deprecated` 必须包裹整个 union，不能放在 `Annotated[int | None, Field(...)]` 内）；模型层级与子类模式；UTC datetime 处理。
- 必须引用 HaaS schema 位置：`haas/protocol/`、`specs/haas-protocol/*.openapi.yaml`、ADK `Event` schema。
- 不重复 `fastapi-backend`（API 层模式）。本 skill 聚焦模型层设计。
- **适配来源**：[Pydantic 官方 skill](https://github.com/pydantic/pydantic/blob/main/.agents/skills/pydantic/SKILL.md)，HaaS 适配版。

#### 5.4.3 P1：adapter-extension

**范围**：Harness adapter 扩展（Codex → Pi/OpenCode/AMP）方法论和 adapter 变更完整性验证。

- 必须覆盖"先找现有 abstraction 再设计"决策框架：枚举 sibling adapter 如何暴露同一能力；已有 abstraction 覆盖时复用，不加 adapter-specific knob；≥3 个 adapter 共享概念时提升为共享字段；类型用 `Literal`，禁止 `extra_body` 或无类型 `**kwargs`；default-on vs opt-in 决策树（default-on 仅在无行为变化且无成本增加时；preview/高成本/限速功能必须 opt-in）。
- 必须覆盖 adapter 变更完整性检查清单：streaming/non-streaming parity、sync/async 路径、roundtrip 验证、parse/dump 一致性、错误路径覆盖。
- 必须引用 `harness-adapter` spec 和 `codex-app-server-adapter` spec。
- 不重复 `parallel-code-review`（审查 lens）。本 skill 是 adapter 扩展设计方法论，在新增或修改 adapter 时触发。
- **适配来源**：[Pydantic AI `adding-a-provider-api-feature`](https://github.com/pydantic/pydantic-ai/tree/main/.agents/skills) + [`complete-partial-pr`](https://github.com/pydantic/pydantic-ai/tree/main/.agents/skills)，HaaS 适配版用于 harness adapter。

#### 5.4.4 未引入（仅调研结论）

- SQLModel skill：低-中价值，HaaS DB 层尚未指定。
- Full Stack FastAPI Template library-skills wheel 模式：仅设计参考，不作为 skill 引入。
- uv `.codex/skills` 和 hooks：uv 专属 Codex 自动化流程，不直接复用。
- sidecar 生命周期、session 状态机、脱敏日志、pytest contract 测试、secretless 凭证处理、错误恢复、Docker 多架构、SSE 断线重连/replay：无开源 skill 发现，仍为 HaaS 原创工作，按需开发。

### 5.5 P1：parallel-code-review

**范围**：HaaS 变更的并行多 lens 代码审查。

- 定义 HaaS 专属审查 lens：
  - `secretless`：代码、日志、事件、测试或 artifact 中无真实凭证、Authorization header、cookie、presigned URL、raw prompt 或完整 tool 参数。
  - `protocol-compatibility`：ADK 和 `/v1/haas/*` 字段仅做加法；event 名、错误码、header、session/response ID、artifact URL 无破坏性变更，除非有版本化和迁移计划。
  - `adapter-isolation`：harness 原生协议（Codex JSON-RPC 等）不泄漏到对上 API；adapter 不绕过 policy controller。
  - `recovery-semantics`：timeout、cancel、crash、SSE 断线、event log 写失败、provider 失败有明确状态、错误码、恢复动作和测试证据。
  - `cross-platform`：Tauri bundle、macOS entitlements、Windows WebView2、Linux 行为、sidecar 二进制路径处理。
  - `tests`：新行为有单元 + 集成证据；bug 修复有回归测试；spec 中验收用例被覆盖。
- 与现有审查门禁对齐：两轮 code-review，然后 brooks-review（架构/可维护性），然后 brooks-test（测试质量）。并行 lens 加速第一轮 code-review；不替代 brooks-review 或 brooks-test。
- 定义 findings 去重和验证步骤：每个 finding 有定位、严重级别和处置状态（fixed / false_positive / accepted_risk）。`accepted_risk` 必须记录在相关 spec 或最终交付说明中。
- **适配来源**：[Logseq `logseq-review-workflow`](https://github.com/logseq/logseq/tree/main/.agents/skills/logseq-review-workflow)（编排模式）+ [Bruno `code-review`](https://github.com/usebruno/bruno/tree/main/.claude/skills/code-review)（6-lens 模式），lens 替换为 HaaS 专属关注点。

### 5.6 P1：.claude/skills 符号链接

- 创建 `.claude/skills` 为指向 `../.agents/skills` 的符号链接。
- 确认 `.gitignore` 不排除 `.claude/skills`（仅忽略 `.claude/settings.json` 和 `.claude/settings.local.json`）。
- 确认现有 `.claude/settings.json` 被保留。
- 符号链接使 Claude Code 和其他读取 `.claude/skills` 的工具使用与 `.agents/skills` 相同的 skill 清单。
- **模式来源**：Logseq 和 GitButler 均使用此符号链接模式。

### 5.7 P1：SOURCES.md 更新

- 为所有新引入或适配的 skill 添加条目，含：skill 名称、来源仓库 URL、revision/commit、license、适配说明。
- 从外部来源适配的 skill 必须保留上游版权头（如适用）。
- HaaS 原创 skill（无外部来源）标记为 "HaaS-original"。

## 6. 核心接口与数据模型

### 6.1 Skill 文件结构

每个 skill 遵循 `skill-creator-for-work` 约定：

```text
.agents/skills/<skill-name>/
├── SKILL.md         （必需：YAML frontmatter + Markdown 正文）
├── scripts/         （可选：可执行代码）
├── references/      （可选：按需加载的文档）
└── assets/          （可选：输出中使用的文件）
```

SKILL.md frontmatter 恰好包含两个字段：`name` 和 `description`。`description` 是主要触发机制，必须同时包含 skill 做什么和何时使用。除非现有约定需要（如 vendored skill 使用的 `license`、`allowed-tools`），否则不添加额外 frontmatter 字段。

SKILL.md 正文不超过 500 行。详细参考材料移至 `references/` 文件，从 SKILL.md 链接。

### 6.2 命名约定

- Skill 名称使用 kebab-case：`haas-debug-workflow`、`tauri-react-render-perf`、`parallel-code-review`。
- HaaS 专属且非技术特定的 skill 可使用 `haas-` 前缀（如 `haas-debug-workflow`）。
- 技术特定的 skill 使用技术前缀（如 `tauri-react-render-perf`）。
- 无 skill 名称与现有 skill 重复。

### 6.3 来源追踪

`.agents/skills/SOURCES.md` 是权威来源记录。每个条目含：skill 名称、安装来源（URL + revision）、license、适配说明。

## 7. 运行时模型与状态机

Skills 由编码 Agent 运行时根据 frontmatter `description` 匹配加载。无持久状态。Skills 是只读知识包；除通过 Agent 正常文件编辑操作外，不改变仓库状态。

`.claude/skills` 符号链接是文件系统级兼容层。不创建独立 skill 清单；指向同一 `.agents/skills` 目录。

## 8. 安全与权限

- Skills 不得包含真实凭证、API key、token 或 presigned URL。示例必须使用占位符。
- Skill 脚本不得在无明确用户指令下外泄数据或访问网络资源。
- `.claude/skills` 符号链接不得暴露 `.claude/settings.json`（可能含本地配置）——符号链接仅指向 `skills` 子目录，不指向整个 `.claude` 目录。
- Vendored skills 保留上游 license 头。适配 skill 必须在 `SOURCES.md` 中注明来源和 license。

## 9. 可观测性

- Skill 使用不在仓库级别埋点。编码 Agent 运行时可追踪 skill 触发，但这在仓库范围外。
- Skill 质量通过以下方式验证：frontmatter 验证（name + description 存在）、结构验证（SKILL.md 存在、无多余 README/CHANGELOG）、触发准确性审查（description 与 skill 内容匹配）、交叉引用检查（无断裂内部链接）。

## 10. 失败、恢复、兼容与回滚

- 若新 skill 与现有 skill 范围冲突，重叠内容必须合并到更具体的 skill，较不具体的必须收窄或移除。无两个 skill 可覆盖相同主要工作流而无明确委托关系。
- 若 `.claude/skills` 符号链接对不跟随符号链接的工具造成问题，可移除符号链接而不影响 `.agents/skills`。回滚为 `rm .claude/skills`。
- 若适配 skill 的上游来源显著变更，本地适配是独立的，不自动更新。`SOURCES.md` 记录引入时的 revision。
- 无运行时、协议或构建兼容影响。Skills 仅为开发者工具。
- 任何 skill 新增的回滚为 `rm -rf .agents/skills/<skill-name>` 并移除其 `SOURCES.md` 条目。

## 11. 测试计划与验收

### 11.1 结构验证

每个新的或修改的 skill：
1. SKILL.md 存在，有有效 YAML frontmatter，恰好含 `name` 和 `description`（vendored skill 可加约定所需字段）。
2. `description` 非空，同时包含 skill 做什么和何时使用。
3. SKILL.md 正文不超过 500 行。
4. skill 目录中无多余文件（README.md、CHANGELOG.md、INSTALLATION.md）。
5. 内部引用（指向其他 skill、spec、文件的链接）正确解析。
6. skill 内容中无真实凭证或密钥。

### 11.2 内容验证

1. `haas-debug-workflow`：定义全部三个 HaaS runtime 及选择标准；强制证据闭环工作流；列出 HaaS 日志位置；定义必需输出结构；不引用 Logseq 专属 REPL 或 CLI。
2. `tauri-react-render-perf`：覆盖身份稳定性、Context 边界、Profiler 验证，不依赖 React Compiler；引用 `manager-gui-performance` P0-2；使用 HaaS 本地状态模式（非 Redux/react-query）；不重复 `react-best-practices`。
3. `code-automation` 增强：现有内容保留；跨层检查使用 HaaS 层名；spec 同步检查引用 AGENTS.md；不重复 `spec-coding`。
4. `parallel-code-review`：定义全部六个 HaaS lens；与两轮 code-review + brooks-review + brooks-test 对齐；定义 findings 去重；不替代 brooks 门禁。
5. `.claude/skills` 符号链接：指向 `../.agents/skills`；`.gitignore` 不排除它；现有 `.claude/settings.json` 保留。
6. `SOURCES.md`：所有新 skill 有来源条目；license 已记录。
7. `fastapi-backend`：覆盖 Annotated 依赖注入、async/sync 规则、response_model 过滤；`references/streaming.md` 覆盖 EventSourceResponse + ServerSentEvent 并与 haas-protocol SSE 约定对齐；引用 HaaS 命令；不重复 code-automation 或 haas-debug-workflow。
8. `pydantic-modeling`：覆盖 Field 元数据分类、Annotated 模式、union 元数据陷阱；引用 HaaS 协议 schema 位置；不重复 fastapi-backend。
9. `adapter-extension`：覆盖"先找现有 abstraction"框架、default-on vs opt-in 决策树、完整性检查清单（streaming/non-streaming、sync/async、roundtrip）；引用 harness-adapter spec；不重复 parallel-code-review。

### 11.3 去重验证

1. 无新 skill 重复现有 skill 的主要工作流。
2. `tauri-react-render-perf` 不重复 `react-best-practices` 或 `react-typescript-kit`。
3. `haas-debug-workflow` 不重复 `code-automation`（后者覆盖验证命令选择，非调试工作流）。
4. `parallel-code-review` 不重复 `code-automation` 或 `spec-coding`。
5. Python/FastAPI 批次 skills 不重复 Tauri/Rust 批次 skills 或现有 skills：`fastapi-backend`（API 模式）vs `code-automation`（验证命令）；`pydantic-modeling`（schema 设计）vs `fastapi-backend`（API 模式）；`adapter-extension`（设计方法论）vs `parallel-code-review`（审查 lens）。

### 11.4 验收标准

- 所有结构验证检查通过。
- 所有内容验证检查通过。
- 所有去重验证检查通过。
- `make pre-commit` 通过（含 `make secret-scan`）。
- Spec review 完成，无阻塞项。
- 两轮 code-review、brooks-review、brooks-test 完成，findings 已处置。
- 无用户未提交改动被覆盖或回退。

## 12. 任务拆分与优先级

| 顺序 | 优先级 | 任务 | 交付物 | 依赖 |
|---|---|---|---|---|
| 1 | P0 | 创建 spec（本文档）+ spec review | `specs/agent-skills/README.md` + `README.zh-CN.md` | 无 |
| 2 | P0 | 创建 `haas-debug-workflow` skill | `.agents/skills/haas-debug-workflow/SKILL.md` | 任务 1 |
| 3 | P0 | 创建 `tauri-react-render-perf` skill | `.agents/skills/tauri-react-render-perf/SKILL.md` | 任务 1 |
| 4 | P1 | 增强 `code-automation`（跨层 + spec 同步） | `.agents/skills/code-automation/SKILL.md`（追加式） | 任务 1 |
| 5 | P1 | 创建 `parallel-code-review` skill | `.agents/skills/parallel-code-review/SKILL.md` | 任务 1 |
| 6 | P1 | 创建 `.claude/skills` 符号链接 | `.claude/skills -> ../.agents/skills` | 任务 1；gitignore 安全确认 |
| 7 | P1 | 更新 `SOURCES.md` | `.agents/skills/SOURCES.md` | 任务 2-5 |
| 8 | P0 | Python/FastAPI 调研完成；引入 3 个 skill | 调研报告 + `fastapi-backend`（SKILL.md + references/streaming.md）、`pydantic-modeling`、`adapter-extension` | 任务 1；调研完成 |
| 8a | P0 | 更新 spec 2.4 + 5.4 填入 Python 调研结果 | spec 章节填充 | 任务 8 调研 |
| 8b | P0 | 创建 `fastapi-backend` skill + `references/streaming.md` | `.agents/skills/fastapi-backend/` | 任务 8a |
| 8c | P0 | 创建 `pydantic-modeling` skill | `.agents/skills/pydantic-modeling/SKILL.md` | 任务 8a |
| 8d | P1 | 创建 `adapter-extension` skill | `.agents/skills/adapter-extension/SKILL.md` | 任务 8a |
| 8e | P1 | 更新 SOURCES.md 加入 Python 来源 | `.agents/skills/SOURCES.md` | 任务 8b-8d |
| 9 | P0 | 结构 + 内容 + 去重验证 | 验证证据 | 任务 2-8 |
| 10 | P0 | 两轮 code-review + brooks-review + brooks-test | 审查 findings 及处置 | 任务 2-9 |
| 11 | P0 | `make pre-commit` + 最终交付报告 | Pre-commit 证据 + 最终报告 | 任务 1-10 |

## 13. 组件影响分析

| 组件 | 影响 | 所需动作 | 兼容结论 |
|---|---|---|---|
| 根 AGENTS.md | 无；skills 实现现有 AI 开发规则 | 无变更 | 完全不变 |
| specs/README.md | 在组件索引中添加 `agent-skills` | 添加一行 | 仅追加 |
| .agents/skills/ | 新增 skills；code-automation 追加式增强 | 创建 6 个新 skill 目录（3 个 Tauri/Rust + 3 个 Python）；追加式修改 1 个现有 skill；更新 SOURCES.md | 无现有 skill 被移除或收窄 |
| .claude/ | 添加 skills 符号链接 | `ln -s ../.agents/skills .claude/skills` | settings.json 保留；无行为变更 |
| haas/（运行时代码） | 无 | 无变更 | 完全不变 |
| tests/ | 无（skills 为开发者工具；结构验证通过命令执行，非 pytest） | 无变更 | 完全不变 |
| Dockerfile / Makefile | 无 | 无变更 | 完全不变 |
| manager-gui-performance spec | 被 tauri-react-render-perf skill 引用 | 无 spec 变更；skill 引用现有 P0-2 | 不变 |
| Python/FastAPI 批次 | 引入 3 个新 skill（fastapi-backend、pydantic-modeling、adapter-extension） | 创建 3 个 skill 目录（1 个含 references/）；更新 SOURCES.md | 仅追加；无现有 skill 冲突 |

## 14. 交付顺序与估算

| 阶段 | 估算 | 工作 | 退出证据 |
|---|---:|---|---|
| S0 | 0.5 天 | Spec 创建 + review | Spec 已评审，阻塞项清零 |
| S1 | 0.5 天 | 创建 haas-debug-workflow + tauri-react-render-perf | 结构 + 内容验证通过 |
| S2 | 0.5 天 | code-automation 增强 + parallel-code-review + 符号链接 + SOURCES.md | 验证通过；git diff 确认仅追加 |
| S3 | 1 天 | Python/FastAPI 调研 + skill 引入（如适用） | 调研报告；新 skills 验证通过 |
| S4 | 0.5 天 | 完整验证 + 审查 + pre-commit | 所有门禁通过；最终报告 |

预期窗口：2.5-3 天（含风险缓冲）。Python/FastAPI 批次如发现多个高价值 skill，可能延长 0.5-1 天。
