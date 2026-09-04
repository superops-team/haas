# README 可视化叙事规格

[English](VISUAL-STORYTELLING.md) | **简体中文**

Status: Implemented
Last reviewed: 2026-09-04
Change ID: `readme-guided-trace-gifs`

## 1. 背景

README 已包含通过验证的 Archify 架构图和时序图。它们准确且可交互，但新读者仍需先理解信息密度较高的静态预览，才能把握产品全貌。本变更新增简短的引导式 GIF，在详细图表和规格之前解释核心概念与请求生命周期。

GIF 是由同一组架构事实派生的沟通资产，不是新的协议合同，也不得成为独立事实源。

## 2. 目标

- 让首次访问仓库的读者在约 30 秒内理解 HaaS 产品边界。
- 解释稳定北向协议、adapter 隔离、sandbox 边界和 secretless 加工模型。
- 展示 `/run_sse` 执行路径，以及 raw request 如何转化为安全、canonical、持久化和可恢复的结果。
- 提供事实一致的英文默认版和简体中文版。
- 保留现有 Archify HTML，作为深入阅读的交互入口。

## 3. 非目标

- 不改变任何 HTTP/SSE API、事件、状态、错误码、权限或运行时行为。
- 不替代交互式 Archify 图表或组件规格。
- 不把 Pi、OpenCode 或 AMP 描述为已实现 runtime。
- 不暗示支持 `/v1/codex-worker/*`。
- 不增加托管动效服务、外部图片依赖、遥测或 README JavaScript。
- 不包含 credential、raw prompt、完整 tool argument 或生产数据。

## 4. 读者场景

1. 新工程师能够理解 HaaS 位于哪些系统之间、统一了什么，以及哪些能力仍属于 harness。
2. 集成方能够看到 `POST /run_sse` 如何经过准入、执行、归一化、持久化和流式返回。
3. Reviewer 能看到 policy、credential、redaction、storage、replay 与 recovery 分别在哪一层发生。
4. 读者点击 GIF 可进入对应的 Archify 图或 walkthrough。

## 5. 视觉语言

所有动效采用已确认的 **Guided Trace（引导式路径高亮）**：

- 只有一条明确主路径，每次只高亮一个 active stage。
- 已完成阶段以较低强调度持续可见，使进度具有累积感。
- 每个阶段用一句短文案解释当前架构事实。
- 不闪烁、不弹跳、不使用视差或装饰性粒子。
- 循环重启前，完整终态必须停留足够时间。
- 使用深色画布、高对比文字和 Archify cyan/emerald/rose/violet 语义色板。

每个动效暂停在最终帧时，仍必须表达完整含义。

## 6. 动效集合

### 6.1 HaaS 整体概念

输出：`haas-concept.gif` 与 `haas-concept.zh-CN.gif`。目标时长：9–11 秒。

分镜：

1. Client 进入统一稳定的 **Google ADK 2.0 REST + SSE** 协议面。
2. HaaS 解析 identity、configured harness、policy 和 session 事实。
3. Harness Adapter 边界隔离各 runtime 原生协议。
4. Codex app-server 标记为已实现；Pi、OpenCode 和 AMP 明确标记为 planned。
5. Loopback model/MCP proxy 与 OpenSandbox AIO 展示 secretless 和执行隔离边界。
6. 最终文案：“一个稳定协议，纳管多个隔离的 agent runtime。”

点击目标：对应语言的 system architecture 交互 HTML。

### 6.2 `/run_sse` 请求流程

输出：`run-sse-flow.gif` 与 `run-sse-flow.zh-CN.gif`。目标时长：11–13 秒。

分镜：

1. Client 发送 `POST /run_sse`。
2. HaaS 完成 authenticate、解析 `appName` 和 admission control。
3. Idempotency 与 session lease 防止重复或并发启动。
4. Policy 被编译为 sandbox specification。
5. Codex adapter 执行 `initialize -> initialized -> turn`。
6. 原生 harness event 返回 Session Runtime。
7. Event 经脱敏、append、投影为 ADK Event，并作为 SSE frame 输出。
8. Stream 关闭前先持久化 terminal state。

点击目标：对应语言的 `/run_sse` sequence 交互 HTML。

### 6.3 安全请求加工

输出：`request-processing.gif` 与 `request-processing.zh-CN.gif`。目标时长：11–13 秒。

分镜：

1. Raw request 进入 identity 与 scope 边界。
2. Policy 与 admission 生成允许执行的 envelope，否则在 harness 启动前 fail closed。
3. Effective configured-harness 与 session 配置被冻结。
4. Adapter 将 canonical turn 转换为 harness-native invocation，不向上游泄漏原生协议。
5. Model、MCP、tool 与 credential 访问使用 scoped loopback handle。
6. 原生输出被归一化并脱敏。
7. Canonical event 与 terminal state 被持久化。
8. ADK projection 提供 live SSE，并支持 `Last-Event-ID` replay。
9. 最终文案：“安全进入，标准加工，可恢复输出。”

点击目标：对应语言的 architecture walkthrough。

## 7. 资产与渲染合同

| 属性 | 要求 |
|---|---|
| 画布 | `1440x810`，16:9 |
| 语言 | 英文使用默认文件名；中文使用 `*.zh-CN.gif` |
| 循环 | 无限循环，语义上不得突然跳变 |
| 阶段停留 | 约 1.2–1.8 秒 |
| 终态停留 | 至少 2 秒 |
| 帧率 | 优化后 12–15 fps |
| 大小 | 目标 <= 4 MiB；5 MiB 为硬失败线 |
| 文字 | 在 GitHub README 展示宽度下可读；帧内不放正文段落 |
| 后备 | 保留现有静态 PNG 和交互 HTML |

语义源必须是带 `meta.animation: "trace"` 和 curated `meta.views` 的 Archify spec；若现有图源无法在不扭曲原用途的情况下表达故事，则增加专用 Archify 图源。每个图源捕获前必须通过 showcase validation。捕获帧不得添加 Archify 源中不存在的事实。

## 8. README 集成

两份根 README 都在开头状态说明之后新增章节：

- 英文：`## HaaS in 30 seconds`
- 中文：`## 30 秒了解 HaaS`

该章节按整体概念、请求流程、加工工程的顺序展示三张 GIF。每张图配一句简短说明并可点击。英文使用默认资产与目标；中文使用 `*.zh-CN.gif` 与中文目标。现有详细静态章节继续保留。

## 9. 影响与实现边界

- ADK API、HaaS API、SSE、状态、存储、adapter、proxy、sandbox、权限和 credential：无行为影响。
- 现有 architecture/sequence 图源与故事拓扑一致时应复用。
- 只为安全请求加工故事增加最小必要的新图源。
- 生成脚本放在 `scripts/`，资产放在 `docs/architecture/`。
- 不手工编辑 GIF，也不下载远端字体、图标、图片或 runtime 数据。
- 不提交临时帧、浏览器 receipt、contact sheet 或验证报告。

## 10. 失败处理

- Showcase validation 失败时在捕获前停止。
- 浏览器捕获不完整时不得组装旧帧。
- 资产超过 5 MiB 时，先减少冗余帧或色板复杂度，再考虑缩小文字。
- 中英文阶段数量或声明不一致时交付失败。
- 修复 GitHub GIF 渲染问题期间保留可用静态 PNG 链接。

## 11. 测试计划与验收

1. 所有 Archify 图源通过 9/9 showcase、0 error、0 warning。
2. 修改后的交互 HTML 执行 deliver 与 browser evidence。
3. 在原始尺寸和 README 展示宽度检查两种语言。
4. 验证尺寸、时长、帧率、循环、终态停留和文件大小。
5. 比较双语阶段数和机器标识。
6. 检查 README 链接、`git diff --check` 与 `make pre-commit`。

验收要求：6 个 GIF 可在 GitHub 渲染；中英文事实一致；终态完整可读；每个资产不超过 4 MiB，除非经 review 的例外仍低于 5 MiB；交互目标有效；不与组件 specs 冲突。由于不改变 runtime 行为，runtime、Docker 和 provider 测试记为 `not_run`。

## 12. 任务拆解

1. 编写或改造双语 Archify 图源与 story views。
2. Review 本规格并清零阻塞 finding。
3. 交付通过验证的交互资产。
4. 实现确定性捕获与 GIF 组装。
5. 生成并优化 6 个 GIF。
6. 执行自动化和感知视觉审查。
7. 嵌入两份 README 并验证链接。
8. 执行两轮变更 review 与 pre-commit 门禁。

使用以下命令重新生成完整的双语资产：

```bash
node scripts/generate-readme-gifs.mjs
```
