# README 可视化叙事规格

[English](VISUAL-STORYTELLING.md) | **简体中文**

Status: 脚本与视觉方向已确认；初版已渲染并通过自动媒体检查；完整听审/音轨使用权、发布与 README 接入待完成
Last reviewed: 2026-09-12
Change ID: `readme-explainer-video`（替代 `readme-guided-trace-gifs` 的首页展示方式）

第 1–12 节记录现有 GIF 基线。第 13 节定义替代视频，对新视频制作与首页接入具有优先效力。

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

## 13. 旁白讲解视频

### 13.1 目标与已确认方向

现有 GIF 在讲清价值前就展示实现术语。用一部讲解视频替代首页三张循环动画，依次解释接入问题、产品定位、架构、执行生命周期和开发者价值。

目标受众：构建 Agent 应用的开发者，以及评估 runtime 接入方案的平台工程师。这是建议的受众定位，不是用户额外确认的要求。

用户于 2026-09-12 确认三个分镜的视觉方向，并选择英语旁白。采用克制的深蓝/深绿背景、薄荷绿强调色、大幅英文标题、渐进展开的简化架构图和同屏简体中文/英文字幕。不使用装饰性粒子、密集系统图截图，也不把动画伪造的终端输出当作证据。36 秒浏览器预览仅为分镜研究，不是完整成片或真实执行录像。

核心信息：**把 Agent 执行变成产品可以依赖的服务。** Harness 是完整 Agent Runtime，不是模型提供商。HaaS 统一面向应用的服务边界，不承诺所有 harness 能力完全一致，也不替代它们的执行引擎。

### 13.2 叙事与旁白母稿

目标约 3 分钟，允许范围 165–225 秒。以下时间是剪辑估计；最终字幕和分镜必须跟随实际语音时长，不得为满足固定时长截断讲话。英文为实际配音母稿；中文语义对齐，不新增能力声明。

| 分镜 | 大致位置 | 画面目标 |
|---|---|---|
| 1. 接入问题 | 0:00–0:25 | 一个产品面对不同会话、权限、事件和恢复语义 |
| 2. HaaS 是什么 | 0:25–0:47 | 应用 → 稳定服务合同 → 完整 runtime；区分 runtime 与 LLM |
| 3. 架构 | 0:47–1:12 | App/Manager → HaaS 控制 sidecar → adapter → runtime；单独展示凭据代理边界 |
| 4. 一次执行 | 1:12–1:42 | 发现 → 执行 → 实时事件 → 终态 → 回读；仅实测通过才展示真实脱敏证据 |
| 5. 生命周期为什么重要 | 1:42–2:04 | 重试、会话并发、断线、回放和明确失败，不承诺无条件恢复 |
| 6. 部署与真实状态 | 2:04–2:35 | 本地 sidecar 不等于容器；区分可选远端/容器模式和规划中的 adapter |
| 7. 产品价值 | 2:35–3:00 | 稳定接入、执行有据、权限有界；以产品目标收束 |

**1. 接入问题**

一个 Agent 可以完成任务，但把它做成可靠的产品是另一回事。每种 runtime 都有自己的会话、工具、权限、事件流和恢复规则。这些差异最终都落到你的应用身上。切换 runtime，往往意味着重做接入，而不仅是换一个模型名称。

**2. HaaS 是什么**

HaaS 是 Harness as a Service 的缩写。Harness 是完整的 Agent Runtime。它负责推理、使用工具、修改文件和维护会话。HaaS 为这个 runtime 建立稳定的服务边界。它不是又一个模型 API，也不替代 Agent 自己的执行引擎。

**3. 架构**

应用或 Manager 调用兼容 ADK、支持实时事件流的 Web API。HaaS 控制 sidecar 管理会话、执行记录、策略和事件。Adapter 把 harness 原生协议隔离在应用之外。安全设计把 provider 凭据保留在可信代理之后。Runtime 仅获得限定范围的访问能力。Codex app-server 是首个已实现的 adapter。

**4. 一次执行**

看一次执行。客户端先发现一个已配置的 harness，再通过执行端点提交任务。HaaS 在把工作交给 adapter 前检查身份和策略。Runtime 工作时，输出先成为 canonical event，再进入应用的实时事件流。执行以明确的终态结束。客户端可以回读已记录的结果。不必猜测流结束是否意味着成功。

**5. 生命周期为什么重要**

服务接入的难点就在这里。重试不应该重复启动同一份工作。两个 turn 不应该同时通过同一 session 写入。流式连接丢失，不应该被误认为任务取消。HaaS 用幂等、会话租约和事件回放处理这些情况。恢复仍取决于 runtime 能力和保留的状态，失败必须明确呈现。

**6. 部署与真实状态**

同一个服务边界把产品与执行位置分开。桌面设计使用本地托管 sidecar。远端端点和委托容器是显式替代方案。本地执行不自动意味着容器隔离。Lite 面向最小容器执行；AIO 增加桌面与浏览器环境。它们是不同运行方式，不是可以互换的安全承诺。Codex 首先实现，Pi、OpenCode 和 AMP 仍在规划中。仓库里有代码，不等于已经完成发布验证。

**7. 产品价值**

目标不是再造一个 Agent，而是让每个产品不必为每种 Agent 重做外围执行基础设施：稳定的接入边界，可以追踪的执行过程，能够理解的权限规则。从现有 Codex adapter 开始，核对文档中的能力边界。面向 HaaS 服务合同构建你的产品。把 Agent 执行变成服务，把精力留给真正的产品。在 GitHub 上探索代码与文档。项目位于 Superops Team 的 HaaS 仓库。

### 13.3 证据与能力边界

- 对照当前 architecture、protocol、session-runtime、event-log-sse、security-boundary、manager/container specs 及实现校验旁白。Specs 定义目标行为；仅存在源码不能证明真实执行成功或发布准出。
- Codex 可以称为首个已实现 adapter，不能泛称已全面生产可用。Pi/OpenCode/AMP 必须在画面中标记为规划中。
- 本地托管 sidecar、可选远端端点和委托容器执行是不同模式。不得把每次本地执行都画进容器，也不得承诺远端 workspace 同步。
- 没有对应 build/run 证据时，Lite/AIO 仅描述架构与目标能力。没有测量，不得宣传镜像体积、提速、成本节约或双架构发布已完成。
- 不得把 accepted 边界、恢复、重试、保留期和配置 revision 简化为恰好执行一次、无限回放或任何进程崩溃后都自动恢复。
- 分镜 4 必须尝试隔离的 discovery → run → stream → terminal → readback 验证。真实 Codex/provider 要求显式 E2E 开关和已授权的隔离环境。Fake adapter 可以证明 API 行为，但相关画面必须标记“协议演示 · 测试 adapter”，不得称为真实 Codex 执行。无法验证时使用清晰标记的架构流程示意，在交付说明记录 `not_run`/失败，不得伪造日志或结果。
- 视频不得包含用户项目文件、raw prompt、完整工具参数、credential、个人 HOME 路径、请求 Header 或 provider 配置。旁白是公开项目说明，不是用户会话内容。

### 13.4 媒体与旁白合同

- 母版：1920×1080、16:9、恒定 30 fps、H.264/yuv420p MP4、fast-start metadata；英语 AAC 音频、48 kHz。最终文件必须完整解码无错误。
- 同时交付双语字幕烧录版和保留同一英语旁白的无烧录字幕画面版；独立 `en.srt` 与 `zh-CN.srt` 供 YouTube 选择字幕，无字幕画面版可避免重复显示字幕。
- 英文声音清晰、中性、从容，约每分钟 145–160 词，不模仿任何真人。初稿优先使用本地已有语音合成；若听感不适合公开发布或使用权不明确，标记为草稿音轨，在替换为可发布音轨前不得声称可直接上线 YouTube。不得静默向收费/外部语音服务发送文案。
- 2026-09-12 用户要求优化口播：HaaS 按单音节“Hass”连读，不逐字母朗读。缩写字母之间不插逗号。HTTP 在口播中用“web API with live event streaming”自然表达，技术图仍保留 HTTP/SSE。AIO 口播为“all-in-one”，字幕仍保留 AIO。每段合成字幕应为完整句子，避免半句之间产生语调重启；自然度仍需听审。
- 最后一幕持续醒目显示 `github.com/superops-team/haas`，并增加邀请查看代码和文档的收尾旁白；地址已对照 Git remote 和 README 核实。本地播放器与上传说明也加入 HTTPS 链接，不在旁白逐个朗读 URL 标点。
- 初版不使用背景音乐，避免未授权素材与人声竞争。不模仿平台人物声音，不下载字体，不使用未授权 stock 素材。
- 对白目标约 -16 LUFS integrated，true peak 不高于 -1.5 dBTP。同时检查测量值与实际听感；不得截字或出现无解释的长段静音。
- 单条字幕通常停留 2–7 秒，按短句切分，位于画面 5% 安全区内。1080p 下中文目标 34–40 px、英文 28–32 px，每种语言最多两行。超长字幕应拆句，不缩小字体。中文换行应按词边界均衡分配，不让标点独立行首或末行只剩一个词。烧录字幕下方至少保留 90 px 的桌面播放器控件空间，架构图不得进入字幕区。逐条检查 1280×720 和缩小 YouTube 播放器下的可读性；手机观看可要求横屏/全屏。
- 可复现的制作源放在 `scripts/` 与 `docs/architecture/`；生成的 MP4/WAV、中间帧、contact sheet 和渲染凭据放在已忽略的 `dist/` 或临时目录。Git 仅保存小尺寸封面和创作源，不保存视频大文件。

### 13.5 README 与 YouTube 交接

1. 按已确认视觉方向制作 16:9 封面和清晰的播放提示，不伪造播放量、runtime 截图或性能声明。
2. 在拿到真实可访问的 YouTube URL，或明确授权的上传成功前，保持现有 README 链接可用。不得编造视频 ID 或插入失效占位链接。
3. 在两份 README 中，用一张本地封面图片链接真实视频，替换开头的三张 GIF；保留详细静态架构图和时序图链接。英文标题“Watch the HaaS explainer”，中文标题“观看 HaaS 项目讲解”。两种语言均标明英语旁白与中英字幕。
4. GitHub README 不支持任意 iframe 播放器。封面点击跳转 YouTube，不自动播放、不加跟踪脚本，不声称可在 GitHub 内嵌播放。
5. 交付上传文件及双语标题、说明和章节文案。YouTube 上传、发布、仓库 push 与第三方服务费用均需用户授权。用户当前提出未来上传，并未授权立即公开发布。

### 13.6 影响、验收与任务顺序

仅改变架构表达与 README。HaaS native/ADK API、事件、session、model、identity、registry/profile、store、policy、proxy、MCP/skill、artifact、container runtime、observability、startup 与兼容合同均无行为或 schema 变化；对应 specs 用于核对事实，不为了宣传叙事修改协议。保留工作区无关改动。

1. 确认视觉方向与英语旁白；制作前审阅完整旁白稿。
2. Review 本 delta 的事实准确性、安全、双语等价、可读节奏和发布范围，清零阻塞项。
3. 实现确定性时间轴与渲染流程，先校验字幕时间，再制作旁白、视觉、无字幕/双语视频、字幕文件和封面。
4. 验证隔离执行链路，或按 §13.3 明确采用带标记的示意画面。
5. 完整解码 MP4；检查音视频时长、帧率、格式、响度、字幕正时长/顺序/无重叠、双语时间边界一致和末条对齐。
6. 检查每幕代表帧、两种语言字幕、转场、首尾帧和浏览器播放/暂停/拖动/全屏交互。完整实时视听审查；自动检查不能证明声音自然或易懂。
7. 对渲染代码顺序执行两轮 code review、架构/可维护性 review、测试质量 review。指定 reviewer 工具不可用时，记录人工等价审查及局限，不声称已运行该工具。
8. 校验限定范围 diff 和链接，执行 `make pre-commit`/`make secret-scan`。Scanner 可能仅扫描暂存变更，须额外显式扫描交付文本源。记录基线失败，不修改无关变更或绕过 hook。纯文档变更不要求完整 runtime/container 发布验证，未执行必须记录为 `not_run` 而非通过。
9. 向用户交付本地成片与准确局限。真实视频 URL 存在后再接入 README。

进入制作要求完整脚本获用户确认，且 spec 无阻塞 finding。可上传要求完整成片、字幕/格式检查、视听审查、音频/素材可发布，以及如实描述能力边界。README 接入在真实视频 URL 到位前保持待办。
