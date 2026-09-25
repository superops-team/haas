# HaaS 开发规范指南

本仓库是 Harness As A Service（HaaS）项目，定位为
`mpa-codex-worker` 的重构升级版本。HaaS 对上提供稳定 HTTP/SSE
协议，对下纳管 Codex、Pi、OpenCode、AMP 等主流 agent harness。首期
实现只交付 Codex app-server adapter，但所有架构与协议设计必须为多
harness 扩展保留清晰边界。

`AGENTS.md` 是本仓库 AI 代理和开发流程的最高优先级约束文件。任何更近
目录下的 `AGENTS.md` 只允许收窄规则，不得放宽本文件的协议、安全、测试
和文档门禁。

## 项目定位

HaaS 的核心产品边界是：

```text
Client / Manager
  -> HaaS HTTP + SSE protocol
  -> sidecar control plane
  -> harness adapter
  -> concrete harness runtime
  -> workspace / tools / MCP / model provider
```

核心原则：

1. 上游调用方只依赖 HaaS 协议，不依赖 Codex、Pi、OpenCode、AMP 等
   harness 的内部协议。
2. `harness` 是完整 agent runtime，不是 LLM provider。一个 harness
   拥有自己的会话、工具、文件写入、审批、事件和恢复语义。
3. `configured harness` 是 `base + configuration`，是上游选择执行能力
   的单位。`base` 可为 `codex`、`pi`、`opencode`、`amp` 等开放字符串。
4. 首期 Codex 实现必须走 Codex app-server；`codex exec` 只能作为本地
   诊断或显式 fallback，不得成为生产主路径。
5. Docker runtime 提供两个稳定变体：**`lite`（默认）**是最小沙箱镜像，不依赖
   OpenSandbox AIO 服务，支持 `linux/amd64` + `linux/arm64` 多架构，可在 Mac
   Docker CLI 下运行 arm64 容器，不要求 Linux 构建主机；**`aio`** 基于开源 OpenSandbox AIO 镜像
   （默认参考 `ghcr.io/agent-infra/sandbox:<tag-or-digest>`），仅 `linux/amd64`，
   面向需要桌面/VNC/浏览器/CUA/BUA 的场景。生产镜像必须 pin digest；`latest`
   只允许本地实验。AIO 变体的平台合同详见「Docker 与镜像变体」章节。
6. Harness 纳管协议以 Google ADK 2.0 REST API 协议层为首个兼容目标，同时保留
   HaaS 自有 control-plane 扩展。Northbound 只包含 ADK 与 `/v1/haas/*` 两个协议面。

## AI 开发铁律

以下规则适用于本仓库所有开发工作，违反任意一条即阻塞交付：

1. **Specs 先行**：所有功能、重构、Bug 修复必须先在 `specs/` 下有清晰
   组件规格或协议 delta。本仓库不提交 `prd-spec/` 与 `docs/verification/`；
   如本地临时生成这类材料，交付前必须清理。
2. **实现前必须 review spec**：进入运行时代码前必须完成 spec review。
   阻塞项必须全部修复，剩余风险必须写入对应组件 spec 或最终交付说明，不能
   只留在聊天上下文。
3. **协议优先于实现**：`specs/` 是长期组件合同。实现代码、测试、SDK、
   README、示例和部署脚本必须与 specs 对齐；发现冲突时先更新或修正
   specs，再实现。
4. **兼容承诺必须显式**：公共 HTTP/SSE API、错误码、事件名、Header、
   session/response id、artifact URL 和配置字段都是兼容面。只能做加法；
   需要删除或改义时必须有新版本、迁移计划、旧入口退休条件和回滚路径。
5. **适配器隔离**：任何 harness 原生协议只能存在于对应 adapter 内。
   Codex app-server JSON-RPC、Pi JSONL、OpenCode JSON、AMP 协议或其他
   harness 细节不得泄漏到 HaaS 对上 API。
6. **事件是事实，不是渲染**：SSE 事件必须表达发生了什么，不表达 UI 应该
   怎么画。所有 event 必须有稳定类型、单调序号、终态事件和脱敏 payload。
7. **Secretless 是硬边界**：真实 API key、Authorization、cookie、presigned
   URL、raw prompt、完整工具参数不得进入 harness env/config、日志、事件、
   metrics、artifact 或验证报告，也不得进入 git 仓库（pre-commit 拦截，见
   「安全与日志」）。无法满足 secretless 的 adapter 默认不可进入生产。
8. **失败必须可恢复或可解释**：timeout、cancel、adapter crash、SSE 断线、
   event log 写失败、model/MCP provider 失败、sandbox 重启和进程退出都必须
   有明确状态、错误码、恢复动作和验证证据。
9. **先跑通最小端到端**：新 harness 首先实现一个真实可运行的最小任务链路：
   discovery -> create response -> stream events -> terminal response -> read back。
   通过后再扩展文件、MCP、skills、审批和高级恢复。
10. **测试默认隔离**：默认测试不得访问真实网络、真实用户 HOME、真实云服务
    或真实 provider。真实 provider、Docker、OpenSandbox、Codex app-server
    验证必须使用显式环境开关和专项 smoke/E2E 命令。
11. **文档不能漂移**：任何协议或组件行为变化必须同步更新 `specs/`。
    不能让 README、OpenAPI/schema、实现和测试各说各话。
12. **最小必要抽象**：adapter 接口是必要抽象；除此之外不引入预防性工厂、
    策略层或未被首期/近期 harness 证明需要的复杂度。
13. **保留证据但不提交临时报告**：调研、spec review、测试、E2E、容器验证、
    兼容性验证和 review 结果必须在最终交付说明或 PR/MR 描述中记录。只有长期
    合同和稳定设计进入 `specs/`；临时验证报告不得提交到本仓库。

## 强制研发流程

任何非纯问答需求都必须按以下顺序推进：

### 0. 证据与执行依据

1. 每个非平凡需求定义稳定 `<change-id>`，默认与 spec 主题目录或分支名一致。
2. 关键证据在最终交付说明或 PR/MR 描述中记录；不要提交
   `docs/verification/` 目录。
3. 证据必须包含执行时间或上下文、输入范围、命令或资料来源、关键结论、失败/
   风险、修复动作、复验结果和下一阶段准入结论。
4. 缺数据时写 `blocked` 或 `not_run` 并说明原因，不能把未执行说成已通过。
5. 进入任意阶段前先读取已提交的相关 `specs/`，不凭记忆判断协议或组件边界。

### 1. Specs 阶段

1. 先创建或更新 `specs/` 下对应组件规格。
2. Spec 必须写清背景、目标、非目标、用户场景、功能需求、接口/状态/数据/
   权限影响、实现边界、测试计划、验收标准和任务拆分。
3. P0/P1/P2 在本仓库表示落地顺序，不是默认裁剪范围。未完成项必须明确
   记录并获得确认。
4. Specs 完成前不得修改 `haas/`、`tests/`、Dockerfile、Makefile、运行脚本
   或其他生产代码。

### 2. 组件影响分析

1. 每次变更必须逐项分析对 `specs/` 下各组件的影响。
2. 影响 API、事件、状态机、数据模型、错误码、权限、session、model proxy、
   MCP、skills、container runtime、observability 或兼容层时，必须更新对应
   `specs/<component>/README.md`。
3. 若判断核心组件不受影响，必须在最终交付说明或相关 spec 中说明原因。
4. 组件 spec 中的接口、事件、错误码和状态迁移必须能直接转化为测试。

### 3. Spec Review 阶段

1. Specs 更新后必须执行 spec review。
2. Review 范围至少覆盖：上下文一致性、接口完整性、组件边界、状态机、
   错误码、安全、数据脱敏、测试策略、可观测性、兼容性、落地步骤和验收标准。
3. Review 结论记录在最终交付说明或 PR/MR 描述中；不得提交
   `docs/verification/` 报告。
4. 阻塞项未清零前不得进入实现。

### 4. 实现计划 + SDD + TDD 阶段

1. 重大变更必须先在 `specs/` 中形成任务拆解或协议 delta；如使用外部项目管理
   或 OpenSpec 工具，产物不提交到本仓库，组件合同仍以 `specs/` 为准。
2. 每个实现任务必须追溯到组件 spec 和明确的任务项。
3. 先写失败测试或契约测试，再实现，再重构。
4. 每个文件变更前后必须做影响分析，说明为什么改、影响哪些调用方、如何验证。

### 5. 测试与覆盖率阶段

1. 默认先跑影响面最小的测试，再按风险升级。
2. API、SSE、session、adapter、model proxy、MCP、skills、container runtime、
   secretless 或恢复语义变更必须有单元 + 集成 + E2E/smoke 证据。
3. 新增核心模块目标覆盖率不得低于 90%；credential、redaction、policy、
   proxy token、artifact path 和日志脱敏路径不得低于 95%。
4. 不能执行某项验证时，必须记录原因、替代验证和残余风险。

### 6. Review 门禁阶段

开发和测试完成后，必须顺序执行：

1. `code-review`：两轮代码审查与修复。
2. `brooks-review`：架构和可维护性审查。
3. `brooks-test`：测试质量审查。

每轮 finding 必须有定位、严重级别和处置状态：`fixed`、`false_positive`、
   `accepted_risk`。默认不得保留 `accepted_risk`；确需保留时必须写入组件 spec
   或最终交付说明。

### 7. 本机准出阶段

1. 提交前至少执行 `make pre-commit`（或让 git pre-commit hook 自动执行），
   其中 `make secret-scan` 是提交安全门禁。命中敏感信息必须修复后才能提交；
   禁止用 `git commit --no-verify` 绕过，确需绕过（如环境无法运行 scanner）
   必须在最终交付说明记录原因、风险和后续清理计划。
2. 跨组件、高风险、最终合入或发布必须执行 `make full-check`；若本机资源
   不足，先降并发或停止无关重型进程。
3. 容器相关变更必须执行 Docker build/smoke；镜像推送必须得到用户明确确认。

## 项目结构

目标结构如下：

```text
haas/
├── haas/                         # HaaS sidecar/control-plane 源码
│   ├── api/                       # HTTP/SSE route、schema、middleware
│   ├── protocol/                  # HaaS/ADK 协议模型
│   ├── harnesses/                 # Harness adapter interface 与各 runtime adapter
│   │   ├── codex_app_server/
│   │   ├── pi/
│   │   ├── opencode/
│   │   └── amp/
│   ├── sessions/                  # session、response、turn、lease、idempotency
│   ├── events/                    # event log、SSE replay、projection
│   ├── policy/                    # workspace、tool、network、approval policy
│   ├── model_proxy/               # secretless model/provider proxy
│   ├── mcp/                       # MCP/tool/skill materialization
│   ├── artifacts/                 # input files, produced files, archive/download
│   ├── runtime/                   # OpenSandbox AIO container integration
│   └── observability/             # logs、metrics、traces、diagnostics
├── tests/                         # unit/integration/e2e
├── specs/                         # 长期组件规格
├── docker/                        # healthcheck、entrypoint、supervisor 配置
├── scripts/                       # 本地开发、验证、镜像构建脚本
├── Dockerfile
├── Makefile
└── pyproject.toml
```

当前源码目录未初始化时，文档与协议规范仍必须自洽；一旦新增 `haas/`、
`tests/`、Dockerfile 或 Makefile，相关门禁立即生效。

## 协议与接口规范

1. HaaS 的 northbound 主协议是 HTTP JSON + SSE，遵循 Google ADK 2.0 的 REST
   API 协议层（drop-in 兼容）：
   - `GET /list-apps`
   - `POST /run`
   - `POST /run_sse`
   - `GET /apps/{app_name}/users/{user_id}/sessions/{session_id}`
   - `PATCH /apps/{app_name}/users/{user_id}/sessions/{session_id}`
   - `DELETE /apps/{app_name}/users/{user_id}/sessions/{session_id}`
   - `appName` = configured harness `id`（`chrn_...`，`name` 可作别名）
   - 事件使用 ADK `Event` schema（`content.role/parts`、`actions`、`invocationId`）
2. HaaS 自有控制面使用 `/v1/haas/*`，只承载 runtime、diagnostics、profile、
   harness CRUD、session/event 管理、artifact 和内部运维能力；不得重新定义
   ADK 已有字段语义。
3. 所有新增能力一律定义在 ADK 面或 `/v1/haas/*`，不得增加其他 northbound
   兼容命名空间。
4. 所有 mutating API 必须支持 `Idempotency-Key`（可选传入，服务端去重）。重复
   key 必须返回第一次请求的结果，不得重复启动 harness。
5. `POST /run_sse` 返回 `text/event-stream`，事件为 `data:` 帧；`streaming:true`
   开启 token 级增量。invocation 完成即关闭 stream；heartbeat 使用 SSE comment
   不产生事件。`POST /run` 收集全部事件后一次性返回 JSON 数组，且与流式聚合
   输出一致（parity）。
6. HTTP response、SSE event 和错误对象必须使用结构化 schema；禁止让调用方解析
   人类文本判断状态。

## Harness Adapter 规范

1. 每个 adapter 必须声明：
   - `base` 标识，例如 `codex`、`pi`、`opencode`、`amp`
   - 原生 transport，例如 Codex `app-server` WebSocket/stdio、CLI JSONL、
     state-db polling 或 SDK callback
   - 支持的输入、文件、工具、MCP、skill、审批、取消、恢复和 token usage 能力
   - credential 注入方式和 secretless 等级
   - 事件 normalizer 与 terminal 判定规则
2. Adapter 输出只能是 canonical HaaS event / response，不得把原生 event 直接
   暴露给上游。
3. Adapter 不得跨越 policy controller 自行放宽工具、网络、文件系统或审批权限。
4. 如果某个 harness 只能 instruction-level 禁用工具，catalog 必须如实标记为
   `enforcement=advisory`，不能声称 hard block。

## Codex App-Server 首期约束

1. Codex app-server 是首期唯一 P0 harness runtime。
2. app-server 内部 transport 优先使用 Unix socket 或 loopback WebSocket；该
   transport 只在 sidecar 内部可见。
3. 每个连接必须先发送 `initialize` request，再发送 `initialized` notification，
   然后才能调用 `thread/start`、`thread/resume` 或 `turn/start`。
4. Codex WebSocket transport 仍按实验能力处理，不直接暴露给上游公网。
5. Codex app-server schema 必须由当前 pin 的 Codex 版本生成或验证；升级 Codex
   版本必须重新生成/比对 schema 并更新 `specs/codex-app-server-adapter/`。

## Docker 与镜像变体

HaaS 从同一份源代码构建两个稳定镜像变体，均为公开合同：

- **Lite（默认，`docker/Dockerfile.lite`）**：最小 slim Python/HaaS/Codex、git、
  CA、shell、tini 与 loopback relay，不含 AIO/nginx/browser/VNC/IDE/notebook。
  支持 `linux/amd64` 与 `linux/arm64`；Mac Apple Silicon 用 Docker CLI 在本机
  构建运行 arm64，不要求 Linux 主机或 amd64 交叉构建。Docker Engine 提供所需
  虚拟化，不承诺完全无 VM。压缩 ≤400 MiB、解包 ≤1.2 GiB 是待实测验收目标。
- **AIO（现有根目录 `Dockerfile`）**：基于 digest-pinned OpenSandbox AIO，
  保留 shell/file/browser/sandbox/execd/vault 和官方启动能力，仅 amd64。

完整隔离、broker 凭证边界、session volume 与平台合同见 `specs/container-runtime/`。
目标 Dockerfile/命令未实现前不得宣称已交付，现有 AIO 构建入口保留到迁移完成。

规则：

1. 两份 Dockerfile 生产构建必须 digest pin。`latest` 只允许本地实验。Lite
   `HAAS_LITE_BASE` 与 AIO `HAAS_AIO_BASE` 都可指向可信 digest pin 的镜像镜像
   仓/缓存；release 构建不得使用可变 tag。
2. **平台合同分变体**：
   - Lite 发布 `linux/amd64` 与 `linux/arm64`；本地 `make docker-build-lite`
     只构建实际 Docker 执行节点的平台，Mac Apple Silicon 默认 arm64。发布时
     分别构建验证后组合 OCI index；本机无需多架构 `--load` 或 registry push。
   - AIO 只发布 `linux/amd64`；`make docker-build-aio` 显式 `--platform=linux/amd64`。
     非 amd64 主机通过 buildx/QEMU 交叉构建 amd64；不得以主机原生架构冒充交付。
3. HaaS 镜像只在 base 上叠加 sidecar、harness adapter、首期 Codex CLI/app-server
   依赖和启动脚本，不 fork 或私改 base 能力。
4. AIO 变体必须保留 AIO `/opt/gem/run.sh` 或等价启动链路。Lite 变体使用独立
   `/opt/haas/run.sh`（tini + sidecar + Codex adapter + loopback proxy），不启动
   nginx 或 AIO。
5. 端口约定：
   - `8080`：仅 AIO 变体使用（AIO sandbox 服务/统一入口）；lite 变体不占用、不暴露。
   - `8092`：HaaS sidecar HTTP/SSE API（两变体共用）。
   - `18080`：HaaS model proxy loopback（两变体共用）。
   - `18081`：HaaS MCP/tool proxy loopback（两变体共用）。
6. `/health` 只表示进程存活；`/ready` 表示是否可接新 session 或执行 turn，
   两变体规则一致。
7. Manager 与独立 HaaS 默认使用 `lite` 变体；通过
   `HAAS_DEFAULT_IMAGE_VARIANT=aio` 或 delegated-session `image.variant=aio`
   显式选择。普通 restore 保持记录的 variant；显式 `/policy` 更新可在当前 turn
   完成后重建运行资源，保持同一逻辑 session，须通过安全与原生续接验证。

## 开发命令

仓库初始化完成后应提供以下 Makefile 入口，并把实际命令固定在 Makefile 或
`scripts/quality/` 中：

| 命令 | 用途 | 阻塞级别 |
|------|------|----------|
| `make setup` | 创建本地开发环境并安装依赖 | 准备阶段 |
| `make install-hooks` | 安装 `.git/hooks/pre-commit` secret scan 拦截钩子 | 准备阶段 |
| `make secret-scan` | 扫描暂存变更中的敏感信息（凭证/私钥/URL 等） | pre-commit 阻塞 |
| `make fmt` | 自动格式化 | 开发阶段 |
| `make lint` | 静态检查 | pre-commit 阻塞 |
| `make type` | 类型检查 | pre-commit 阻塞 |
| `make gui-test` | Manager GUI Vitest 单元测试 | GUI 变更 |
| `make gui-build` | Manager GUI TypeScript + production Vite build | GUI 变更 |
| `make gui-check` | Manager GUI 单元测试 + production build | `full-check` 子门禁 |
| `make gui-preview-smoke` | production Vite preview Playwright smoke | GUI 性能/路由/artifact 变更 |
| `make test-affected` | 按 diff 运行最小充分测试 | pre-commit 阻塞 |
| `make test-fast` | 快速离线测试 | 专项回归 |
| `make test-integration` | API/SSE/session/adapter 集成测试 | 跨组件变更 |
| `make test-e2e` | 本地 HaaS + harness E2E | 运行链路变更 |
| `make docker-check` | Dockerfile/AIO/health/ready 检查 | 容器变更 |
| `make adk-compat` | ADK 2.0 协议兼容性套件 | 协议变更 |
| `make full-check` | 完整本机准出，包含 GUI unit/build、后端、Docker 静态和 secret scan | 最终合入/发布 |

当前已提供上述常用本机门禁；容器真实 build/smoke 仍需显式
`HAAS_DOCKER_BUILD=1`。文档初始化类变更仍以 `git diff --check` 和链接/占位符
自审作为最低验证。

## 安全与日志

1. 所有日志、metrics、events、artifact metadata 和验证报告默认脱敏。
2. raw prompt 默认不得写入持久日志；如必须为调试保留，必须通过显式
   `trace_content=true` 类开关、最短 retention、访问控制和测试反向断言。
3. 上游传入的 provider credential 必须进入 secret store、credential vault 或
   sidecar 内存态 secret handle；不得作为持久配置明文传给 harness。
4. 所有 caller-controlled URL 必须通过 allowlist、scheme、host、port、path 和
   private-network 策略校验，防止 SSRF。
5. artifact 下载必须防 path traversal，并设置 `X-Content-Type-Options: nosniff`。
6. 提交安全：真实凭证、私有密钥、`.env`、证书、presigned URL、Authorization/
   Cookie 值、raw prompt 与完整 tool 参数禁止进入 git 仓库。pre-commit hook
   与 `make secret-scan` 双重拦截；`.gitignore` 必须覆盖 `.env*`、`*.pem`、
   `*.key`、`*.p12`、`*.jks`、`credentials*.json` 等敏感文件。仅测试 fixture
   允许 `# haas-secret-ignore` 行内标记绕过单行扫描，且必须经过 code review。

## 提交前准出

最终交付说明必须列出：

- 组件 spec 更新列表
- 兼容面影响，包括 ADK 与 HaaS native
- 测试命令与结果
- 未执行验证、原因和残余风险
- 安全/脱敏/容器/事件流相关结论
- 提交安全结论：`make pre-commit` / `make secret-scan` 结果与放行/拦截说明
 pre-commit` / `make secret-scan` 结果与放行/拦截说明
