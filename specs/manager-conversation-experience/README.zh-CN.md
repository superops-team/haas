# Manager 对话体验规格

[English](README.md) | **简体中文**

状态：MCX-001 至 MCX-063 已实施；等待 owner 视觉验收
最近评审：2026-09-30
Change ID：`manager-conversation-interaction-v2`
相关规格：[Manager HaaS Sidecar Backend](../manager-haas-sidecar-backend/README.zh-CN.md)、[Manager 项目工作台体验](../manager-project-workspace-experience/README.zh-CN.md)、[Manager Delegation](../manager-delegation/README.zh-CN.md)、[Manager GUI Performance](../manager-gui-performance/README.zh-CN.md)、[Event Log & SSE](../event-log-sse/README.zh-CN.md)、[Session Runtime](../session-runtime/README.zh-CN.md)、[Manager Product Identity](../manager-product-identity/README.zh-CN.md)、[Security Boundary](../security-boundary/README.zh-CN.md)

## 1. 组件职责与产品优先级

Manager 对话体验负责用户在 OpenHarness 中创建、运行、控制、恢复与回看 Agent 任务的端到端体验。它定义 Manager 侧对话投影、命令准入反馈、per-session 草稿与追问队列、标准 React AI 组件边界、视觉 token、响应式行为、无障碍，以及在不永久保留 legacy 路径的前提下替换当前对话实现的渐进式迁移过程。

组件优先级固定且具有规范性：

1. 系统与任务执行稳定性；
2. 一致、克制、专业的设计品质；
3. 明确、可容错的交互行为；
4. 对齐优秀主流 Agent 任务管理体验；
5. 功能广度。

后一项不得削弱前一项。任何会丢失草稿、重复执行 turn、错误描述任务进度、隐藏恢复动作或降低长会话渲染质量的视觉优化，都不满足本规格。

### 1.1 统一设计规范入口

[ZCode 对齐交付合同](ALIGNMENT.zh-CN.md)定义完整项目范围、同条件对照方法、迭代顺序、覆盖率和原生验收，补充而不替代下述要求。

[DESIGN.md](../../DESIGN.md)（[中文版](../../DESIGN.zh-CN.md)）是 Manager UI 产品语义、组件职责、视觉层级、token 和交互评审的统一入口。它将 ZCode 参考提炼为 HaaS 规则，并映射到本规格的验收用例。本组件 spec 仍是生命周期、数据、命令、兼容性和验收合同的权威来源；`manager/surfaces/gui/src/styles.css` 负责运行时 token 值。修改 GUI 前必须阅读 DESIGN.md 和受影响组件 spec。共享设计规则发生变化时，先同步两种语言及相关 spec，再修改代码。

设计合规必须由可复用组件默认行为、聚焦行为验证和受影响状态的双主题视觉评审共同证明。仅有文档或单元测试通过不能证明合规；没有对应证据，不得声称已有全库 token 自动门禁或已完成原生验证。现有验收用例继续作为准出要求，本设计入口不表示这些要求已全部实现。

组件影响：本次规范治理与控制区样式 delta 仅影响 Manager 展示，不新增或改变 ADK/HaaS native API、事件、状态迁移、持久化、权限、adapter、proxy、MCP/skill、容器或可观测性行为。诊断详情继续遵守现有授权证据与脱敏合同。

## 2. 证据与已批准方向

### 2.1 当前实现证据

2026-09-26 对当前 React/Vite Manager 的评审确认了以下基线：

- production GUI build 通过，入口 chunk 为 659.65 kB minified / 216.99 kB gzip；
- 发送/流式输出、审批、重试、运行中 turn 恢复、停止、阶段展示、滚动锚定和跳到最新等 19 个聚焦 Playwright 用例通过；
- 当前 GUI 已提供单一自上而下 transcript、紧凑的任务命名 model stage、贴近 composer 的审批区、执行证据 inspector、390/1440 响应式布局和 terminal readback 恢复；
- `Composer` 调用返回 `void` 的 `onSend` 后立即清理文字与附件，而不是等待权威 acceptance；
- 切换 session 会主动清除未发送草稿，而不是为每个 session 保留独立草稿；
- turn 运行期间普通输入被阻止，只有当输入本身是某个 proposal gate 的回答时例外；
- `WsEvent.data` 和若干 transcript 字段类型较弱，`Transcript` 根据相邻 UI item kind 重建 turn 边界；
- 当前 transcript 会渲染全部历史 block，没有有界列表虚拟化；
- live model stage 可见时摘要仍可能显示 `Working · 0 activities`，原因是 stage 与 tool count 来自不同数据源；
- composer textarea 移除了原生 outline，却没有等价的 focus-visible 指示；以及
- faint 文本 token 在浅色 canvas 上实测为 4.43:1，在深色 canvas 上为 3.06:1，低于使用该 token 的小字号文本所需 4.5:1。

### 2.2 参考分析

ZCode 是设计和架构参考，不是源码依赖。采纳的思路包括：

- 权威 turn/row/work identity，以及位于传输事实与渲染之间的 projection；
- per-session 持久草稿和感知 acceptance 的提交清理；
- Agent 忙时已接受输入的一等队列；
- 虚拟历史与非虚拟 live tail 分离；
- 标准化 conversation、turn、activity、interaction、queue、composer、status 与 completion 组件；
- 细粒度 selector，避免 token delta 触发无关 surface 重渲染；以及
- 浅色与深色主题共享的语义视觉 token。

HaaS 不采纳 ZCode 的仓库结构、workflow graph、产品专属命令体系，或超大型 session/composer 单体实现。

### 2.3 已批准产品方向

已批准方向为 **Focused Workbench（聚焦型工作台）**：

- conversation 保持主要 surface 和阅读顺序；
- 当前工作压缩为一个紧凑、可展开的 activity 区域；
- 后台或跨 turn 工作与产出物可进入次级 status surface；
- follow-up queue 在视觉上附着于 composer；
- 阻塞式人机交互统一占据 composer 上方的稳定 dock；
- 执行证据详情在 inspector 中打开，不替换或重复 timeline；以及
- 窄屏按优先级逐步收起次要控制，而不是等比例压缩所有元素。

### 2.4 实现后产品证据与纠偏决策

2026-09-26 的 packaged-desktop 真机运行暴露出功能、无障碍与性能门禁未捕获的设计失败：运行中的一个 turn 用八张同级 model-call 卡片填满主视口；每张卡片重复 status、step count、command 或内部 commentary 及 token accounting；已完成调用在下一次调用运行时继续展开。用户需求与最终回答被挤出首屏，usage 缺失比当前工作更醒目，timeline 显示 running 的同时 Composer 出现 Continue 与 Stop。Assistant stream 还会在达到词数阈值后切换容器，造成文本延迟出现与布局跳动。

以下纠偏决策具有规范性：

- 主投影单位必须是 **product turn**，绝不能是 model call；
- model-call boundary 与 identifier 只用于 correlation/evidence，不得生成同级 timeline card、heading、count 或用户可见 workflow phase；
- 一个 turn 只拥有一个 work summary、零到多个渐进披露 work segment、最多一个 active interaction，以及一个稳定 assistant-response surface；
- assistant content 从第一段被分类为用户可见的 delta 起到 terminal sealing 始终位于同一 response surface；不得按 word count、elapsed time、adjacency 或 tool arrival 迁移；
- 同一 product turn 的重复或多 model-call `assistant_message` fact 必须更新该 response owner，不得追加第二条 assistant row。权威非空消息替换临时文本；仅 metadata 的消息保留现有文本，同时更新 usage、reasoning、evidence 和 terminal state；
- timeline state、header state、loading treatment 与 Composer control 必须消费同一个 canonical presentation selector；以及
- internal reasoning/commentary 与缺失 accounting 数据不得升级为标题、warning 或 primary status。

这是 projection 纠偏，不是新 conversation mode。现有 stage-card hierarchy、word-threshold stream gate 与独立 lifecycle selector 属于必须删除的过渡实现缺陷，不是需要保留的兼容行为。

## 3. 目标、非目标与成功度量

### 3.1 目标

1. 在发送、重连、切换 session、刷新、backend 重启或配置应用期间，绝不丢失已接受或尚未发送的用户意图。
2. 绝不因传输结果不确定或重试而重复执行同一份用户提交。
3. 通过同一个确定性 projection 渲染 live、replay 和 restore 的对话事实。
4. Agent 忙时仍允许继续编写内容，并显式管理排队的 follow-up。
5. running、waiting-for-user、paused、failed、recovering 和 completed 状态必须通过文字与结构区分，不能只依赖颜色或动画。
6. 保持主 transcript 克制、易读，同时让技术细节一键可达。
7. 支持 10,000 条持久 row，并保持 DOM 有界和滚动/选区稳定。
8. 提供可复用、可独立测试、订阅状态切片足够小的 React AI 组件。
9. browser production preview 与 Tauri desktop 安装包行为等价。
10. 通过同一语义 token 合同完整交付浅色与深色主题。
11. 迁移结束时不保留永久 compatibility renderer、重复状态 owner、legacy CSS 路径或长期 feature flag。
12. 将 work telemetry 置于用户意图与回答之下，保持用户阅读上下文。
13. 从第一段可见 delta 到完成，让 assistant response 始终由同一个 DOM 与视觉 owner 承载。

### 3.2 非目标

- 替换 ADK 2.0 或 `/v1/haas/*` northbound 协议。
- 向 GUI 暴露 Codex app-server 或其他 harness-native event。
- 构建 ZCode 的 workflow graph、plugin system、多 pane 编辑器或命令词表。
- 重做 Settings、Connectors、Automations、Inbox 或 artifact 内容 renderer；共享 token 或由 conversation 打开的入口除外。
- 增加 cloud identity、云端草稿同步或跨设备草稿同步。
- 把新旧 conversation 实现都保留成长期用户选项。
- 改变 Lite/AIO container 合同、provider routing 或 credential 归属。

### 3.3 成功度量

- acceptance suite 中草稿丢失与重复执行失败数均为零；
- 每个 pending 或 failed 状态都展示一个明确下一步，或者给出真实终态说明；
- 任意渲染帧中不出现互相矛盾的任务状态或计数；
- 所有支持的交互路径可仅使用键盘完成；
- 浅色与深色主题的文本/控制对比度通过 WCAG 2.2 AA；
- 10,000-row fixture 下挂载的 conversation row 不超过 200；
- Profiler 计数清零后，30 次 live publication 对 Sidebar、inactive route、Composer、关闭的 Status/Inspector 分支产生零次 React commit；
- 每个 animation frame 最多提交一次 live publication；以及
- 最终 legacy removal 门禁找不到任何仅由旧 conversation 路径使用的 import、selector、CSS hook、测试或 flag。
- running turn 在主 timeline 中不渲染 model-call card，也不使用 internal reasoning/commentary 作为 activity title；
- 第一段 assistant delta 在一次合并 publication 内可见，并在 terminal sealing 前保持同一 semantic response element；
- 任一渲染帧不出现互相冲突的 lifecycle action，例如 running 时同时出现 Continue 与 Stop；以及
- 展开 work 必须由用户明确触发，在 streaming 期间保持选择，并且不在阅读锚点上方挤动 live answer。

### 3.4 用户与系统场景

- 用户编写一段详细需求后切换到其他 session、重启桌面应用并返回；准确草稿与 staged reference 只在原 session 恢复。
- 用户在 backend 响应缓慢时提交；UI 在 durable acceptance 前显示 submitting，ACK 丢失时通过对账收敛，既不丢草稿也不重复执行。
- 用户在长 turn 期间输入 follow-up；输入成为可编辑 queued item，成功后继续执行，或在 stop/failure 后保持暂停直到用户决定。
- Turn 请求 approval 或 structured input；统一 dock 展示决策、保留 composer 草稿、阻止重复 resolution，并在重连后正确恢复。
- 用户阅读较早输出时 token 继续到达；viewport 与文本 selection 保持不动，直到用户主动选择 Jump to latest。
- 用户打开 10,000-row session；初始历史、搜索/导航、当前工作与 Composer 保持响应，不渲染完整 transcript。
- 键盘或 screen reader 用户可在两种主题下理解并操作相同生命周期，包括 error 与 recovery。
- Browser 与 packaged desktop client 消费同一个 Manager projection；即使 desktop-only file/window capability 不同，也产生等价任务行为。

## 4. 产品与交互原则

1. **事实优先于装饰。** UI 状态来自权威 Manager/HaaS 事实，不从 spinner、socket-open 布尔值、计时器或文本启发式推断。
2. **一个事实，一个 owner，一个主展示面。** 当前 turn 工作属于 timeline；跨 turn/后台工作属于 Status Panel；待人处理事项属于 Interaction Dock。
3. **输入是持久用户资产。** 草稿、附件和已接受命令不是可随意丢弃的组件状态。
4. **渐进披露。** 默认视图只显示意图、当前工作、结果与恢复；原始命令、参数、完整输出和计量细节必须通过一次明确操作访问。
5. **稳定几何结构。** 流式文本只向下增长，不得在用户阅读锚点上方反复插入、删除或移动大块内容。
6. **克制的专业工作台气质。** 中性 surface、一个 accent、节制的状态色、紧凑字体和最小动画优先于装饰卡片或渐变。
7. **键盘等价。** 每个 pointer 操作都有键盘路径；允许停止时 Escape 只停止当前聚焦 conversation，并为已打开 dialog 或命令面板让路。
8. **禁止静默 fallback。** 远端/本地 routing、queue disposition、approval mode、retry 和 recovery 都必须显式；HaaS 失败时不得静默转为本地执行。

### 4.1 需求清单与交付优先级

P0、P1、P2 表示实现顺序，不表示可选范围。

| 需求 | 优先级 | 合同 |
|---|---|---|
| MCX-R01 权威投影 | P0 | 一个 typed projection 负责 turn、row、work、interaction、queue 与 outcome state |
| MCX-R02 带确认的提交 | P0 | Persisted command receipt 与 idempotency 先于草稿清理和可见 acceptance |
| MCX-R03 持久草稿 | P0 | Per-session draft 经得住 switch/refresh/restart，且永不进入 telemetry |
| MCX-R04 追问队列 | P0 | Busy input 被接受进可操作、持久化、可跨 restart 的 queue |
| MCX-R05 恢复完整性 | P0 | Unknown acceptance、reconnect、gap、stop 与 terminal mismatch 无盲目 resend 地收敛 |
| MCX-R06 可访问双主题基础 | P0 | Semantic token、WCAG AA contrast、focus visibility、keyboard flow 与 reduced motion 均为 release gate |
| MCX-R07 标准 React component cutover | P1 | 命名 AI component 替换当前单体渲染，每个事实只有一个 owner |
| MCX-R08 Focused Workbench 布局 | P1 | 单一主 timeline、自适应 status surface、稳定 interaction dock 与 responsive composer |
| MCX-R09 有界渲染 | P1 | Virtual history、隔离 live tail、稳定 selector 与 scroll anchoring 满足性能预算 |
| MCX-R10 Completion 与 evidence | P1 | Final result 保持主要地位，权威 usage/artifact 与有界 evidence 可达 |
| MCX-R11 Conversation navigation | P2 | Search、turn navigation 与 Jump-to-latest 使用稳定 row/turn identity |
| MCX-R12 语义 context 展示 | P2 | Skill、file、session 与其他受支持 context 渲染为 typed chip，而不是 raw syntax |
| MCX-R13 Legacy zero | P0 最终门禁 | Parity 后删除旧 writer/renderer/style/test/flag，不保留永久 dual path |
| MCX-R14 Product-turn projection | P0 | Model call 只保留为 evidence metadata；一个 product turn 只拥有一个 work summary 与一个 assistant response |
| MCX-R15 稳定 assistant stream | P0 | 第一段用户可见 delta 挂载最终 response owner；任何 heuristic 都不得隐藏或迁移内容 |
| MCX-R16 Canonical lifecycle presentation | P0 | Timeline、header、loading、interaction dock 与 Composer action 使用同一 selector，不能互相矛盾 |
| MCX-R17 安全 work disclosure | P0 | 每个 model inference round 拥有一条安全摘要 row；历史终态 round 默认折叠，最新 running round 默认展开，tool detail 有界地留在所属 round 内 |
| MCX-R18 动效与密度纪律 | P1 | 每个状态只有一个动画 owner；稳定几何、semantic token 与可度量信息密度共同约束页面 |
| MCX-R19 Session activation 完整性 | P0 | 选择已有 session 时原子激活其 identity 与有界缓存 projection，再与权威 history 对账；绝不能展示新会话 empty state，也不能让过期 response 覆盖当前 session |

## 5. 信息架构与响应式布局

### 5.1 稳定区域

| 区域 | 负责 | 不负责 |
|---|---|---|
| Session navigation | Session identity、attention、liveness、搜索/新任务 | Turn 详情、重复的 plan/todo 内容 |
| Conversation header | 标题、configured harness/persona、当前 model、workspace identity | Live tool 状态、瞬时 token delta |
| Conversation timeline | 用户输入、turn work、assistant result、持久 notice、completion summary | 跨 session inbox、重复的后台目录 |
| Conversation status panel | Plan 摘要与跨 turn/后台 terminal、Agent 或 workflow work | 已在 timeline 可见的当前 turn tool row |
| Interaction dock | 恰好一个活动 approval/input/policy/recovery request 及后续排队请求 | 历史已解决 interaction |
| Follow-up queue | 已接受但尚未开始的用户提交 | Deployment admission queue 或其他用户的工作 |
| Composer | 当前 per-session 草稿、附件/context、mode/model 控制、send/stop | 权威 running 状态或 accepted queue 归属 |
| Evidence inspector | 所选 activity 的有界详情与安全 evidence read | 第二份 transcript 或未脱敏原始 event feed |

在宽布局中，Conversation Status Panel 替换当前 right rail 的 Progress section，并与 Artifacts、Files、Access 共用该 rail；它不是额外的第四列。Rail 被隐藏或宽度受限时，同一 model 以 compact capsule/drawer 出现。Timeline、rail 与 capsule 绝不同时渲染同一份 current-turn step list。

### 5.2 宽度行为

- 可用 conversation 宽度大于等于 1200 CSS px 时，transcript 保持居中，Status Panel 可 inline 展开且最大宽度 320 px。
- 760–1199 CSS px 时，Status Panel 默认压成紧凑 capsule，并作为 anchored overlay 或非模态 drawer 打开，不得覆盖 Composer 或 Interaction Dock。
- 小于 760 CSS px 时，navigation 与次级 status 变成 drawer。Composer 常驻附件与主要 mode
  indicator。Model、microphone 与 Send/Stop 在所有支持宽度下组成不可拆分的尾部 control
  cluster；usage 和其他 secondary action 必须先让位或移走，不能让该 cluster 的成员消失。
- 尾部 cluster 顺序固定为 `model -> microphone -> Send/Stop`，peer 之间只使用一个 compact
  spacing token，且禁止换行。Model control 是唯一可伸缩成员：使用 `min-width: 0`，长 localized/
  model label 以尾部 ellipsis 处理；microphone 与 Send/Stop 保持固定 hit target。录音时中间内容区
  可以变成 waveform，但 model、microphone/record-stop 与 lifecycle action 仍相邻可见。
- 在 320 CSS px 和 200% zoom 下，任何必需内容或动作都不得裁切、重叠，也不得藏在没有提示的滚动边界之外。
- 当可用 chat 宽度可能不同于 window 宽度时，断点使用 container query。

### 5.3 阅读顺序

DOM 与视觉顺序为：header context、durable timeline、current live tail、follow-up queue、pending interaction、composer。Status Panel 与 Inspector 是补充 landmark，不得中断 timeline 的语义 heading 顺序。

### 5.4 状态展示矩阵

| 状态 | 主要可见表现 | 主要动作 | 次级详情 |
|---|---|---|---|
| empty/idle | 聚焦 prompt 与三条任务相关建议 | Send | Header 或 composer control 中的 harness/model/workspace |
| 切换 session，cache hit | 立即显示目标 session 最近一次 canonical projection，同时刷新权威 history | Composer 等待目标 history 与 socket barrier | 一个不改变布局的克制刷新状态 |
| 切换 session，cache miss | Conversation measure 内显示有界加载 skeleton；绝不展示新会话 prompt | Composer 等待目标 history 与 socket barrier | History 加载失败时提供可重试安全错误 |
| submitting | 冻结用户意图，composer dock 显示 `Submitting` | 仅在安全时停止等待 | 不展示 optimistic success 或伪造 turn |
| running | 一个紧凑 activity summary 与 live tail | Stop；仅 capability 可用时展示 Pause | 展开当前工作或打开 evidence |
| waiting for user | Interaction Dock 替代活动 composer control surface | 与决策对应的动作 | 有界详情与后续 request 数量 |
| queued follow-up | Queue tray 附着在 Composer 上方 | 编辑或保持排队 | 排序、删除、interrupt-and-send |
| paused | 持久 paused 标签与已保留 partial result | Continue | End task |
| recovering | 稳定 recovery banner；现有内容仍可读 | Retry readback/reconnect | 安全 diagnostic reference |
| failed/incomplete | 在所属 turn 附近展开第一个可操作失败 | 仅允许时 Retry/resume | Evidence Inspector |
| completed | 最终 assistant result 与安静 completion footer | 继续对话 | Usage、artifact、activity detail |

每个状态的 primary control slot 最多只有一个 lifecycle command。该 slot 不一定使用 accent fill：destructive command 使用 danger semantics，不能仅因为时效性借用 accent fill。任何关键 recovery action 都不得藏在 disclosure 后。

### 5.5 Conversation Measure 与对齐

- User/assistant prose 共用一个 leading alignment edge，最大可读 measure 为 72ch；code、table 与 evidence 在各自有界容器内滚动，不能撑宽 transcript。
- Work summary、response、completion footer、notice 与 error 对齐到同一 conversation measure。Inspector 与 Status Panel 使用独立 landmark，不能在 timeline 中制造零散文字边线。
- Group spacing 至少为 item spacing 的两倍；先用空间表达层级，再考虑 border 或 tinted surface。
- Text container 使用 min-height 与 wrapping，不使用 fixed height。Pseudo-localized action label 与长 safe summary 换行后，primary action 仍在 viewport 内。
- 方向相关 spacing 使用 logical property；mixed-direction path、id、command 与 count 在需要时通过显式 `dir`/`bdi` 保持顺序。

## 6. 权威状态与投影模型

### 6.1 归属

| 事实 | 权威 owner | Manager 投影行为 |
|---|---|---|
| Session/invocation/turn terminal state | delegated work 由 HaaS Session Runtime 与 canonical Event Log 负责；local work 由 Manager engine 负责 | 保留来源 identity，归一化为同一个 `ConversationTurnState` |
| Canonical event ordering | HaaS Event Log 或 Manager local journal | 按 source scope + event id 去重并单调应用 |
| Manager chat binding 与 command receipt | Manager backend | 展示为 accepted 前先持久化 |
| Follow-up queue | Manager backend | 持久化有序 queue item；renderer 不得自行构造 |
| Pending approval/input request | Manager backend；delegated 时还包含 HaaS canonical request | 重连后先重建，再启用冲突输入 |
| Unsent draft | 本机 Manager `ConversationDraftStore` | 按 endpoint/workspace/session 划分；不得进入 telemetry |
| Disclosure、hover、selection、panel size | React local UI state | 永远不持久化为执行事实 |

Manager 在现有本地 state database 中事务性持久化 command receipt 与 queue payload。`conversation_commands` 负责 command identity、idempotency key、status、disposition、turn/queue reference、安全 error code 与时间戳。`conversation_queue` 负责有序 payload reference、delivery intent、revision 与 drain policy。Acceptance 及 running/queued disposition 必须在 ACK 前由同一事务提交。若现有 Manager store 支持 encryption，则 raw prompt content 必须加密；否则继承现有仅 owner 可访问的 state-file 权限，并继续排除在 diagnostic 与 telemetry 之外。

### 6.2 Conversation projection

GUI 消费强类型 `ConversationSnapshot`，不再消费可变 `Item[]` 加多组独立 live buffer。内部 TypeScript 合同为：

```ts
type ConversationRunState =
  | "idle" | "submitting" | "running" | "pausing" | "paused"
  | "resuming" | "stopping" | "waiting" | "recovering"
  | "completed" | "failed" | "cancelled";

interface ConversationSnapshot {
  sessionId: string;
  revision: number;
  lastEventId: string | null;
  phase: ConversationRunState;
  turns: ConversationTurn[];
  activeTurnId: string | null;
  activeWork: WorkSummary[];
  backgroundWork: WorkSummary[];
  pendingInteractions: PendingInteraction[];
  pendingSubmission: SubmissionState | null;
  queue: FollowUpQueueItem[];
  outcome: ConversationOutcome | null;
}
```

每个 `ConversationTurn` 都有稳定 `turnId`、有序稳定 `rowId`、可选 `invocationId`、显式 work segment，且最多只有一个 terminal outcome。Tool row 通过 `toolCallId` 关联；interaction row 通过 `interactionId` 关联；queued submission 通过 `queueItemId` 和 `clientCommandId` 关联。Renderer 不得通过相邻内容、时间戳、tool name 或展示文本推断这些 identity。

面向产品的 turn shape 必须显式定义：

```ts
interface ConversationTurn {
  turnId: string;
  invocationId: string | null;
  phase: ConversationRunState;
  userRows: ConversationRow[];
  work: TurnWorkProjection;
  assistantResponse: AssistantResponseProjection | null;
  interactionIds: string[];
  outcome: ConversationOutcome | null;
}

interface TurnWorkProjection {
  state: "idle" | "working" | "waiting" | "succeeded" |
    "failed" | "cancelled";
  safeSummary: string;
  startedAtMs: number | null;
  completedAtMs: number | null;
  inferenceRounds: InferenceRoundProjection[];
  segments: WorkSegment[];
  aggregate: {
    activityCount: number;
    durationMs?: number;
    usage?: AuthoritativeUsage;
    artifactCount?: number;
  };
}

interface InferenceRoundProjection {
  roundId: string;
  state: "running" | "succeeded" | "failed" | "cancelled";
  safeSummary: string | null;
  activityRefs: string[];
}

interface WorkSegment {
  segmentId: string;
  kind: "reasoning" | "tool" | "progress" | "recovery";
  state: "pending" | "running" | "succeeded" | "failed" | "cancelled";
  safeTitle: string;
  safeSummary?: string;
  activityRefs: string[];
  evidenceRef?: string;
}

interface AssistantResponseProjection {
  rowId: string;
  text: string;
  state: "streaming" | "sealed" | "interrupted";
  firstVisibleDeltaAtMs: number;
}
```

`segmentId` 与 `roundId` 是 Manager 产品 identity。`roundId` 将一次 canonical model-stage occurrence
映射为一条 inference-summary row，但 native `modelCallId`、provider request id、native reasoning item
id 与 token-usage arrival boundary 绝不进入可见 label 或 accessible name，只作为 projection boundary
后的私有 correlation metadata。Model round 可以改变有界 work-detail row 数量，但不得创建同级
assistant message 或改变主 turn 顺序。

其余持久 Manager record 必须显式且带版本：

```ts
interface ConversationDraftRecord {
  schemaVersion: 1;
  scopeKey: string;
  revision: number;
  text: string;
  editorState?: string;
  attachmentRefs: string[];
  contextRefs: string[];
  context?: ContextReference[];
  skill?: { name: string; description: string; scope: "global" | "project"; enabled: boolean };
  model?: string;
  mode?: string;
  updatedAtMs: number;
}

interface ContextReference {
  kind: "skill" | "file" | "session";
  id: string;
  label: string;
  path?: string;
  unavailable?: boolean;
}

interface FollowUpQueueItem {
  queueItemId: string;
  clientCommandId: string;
  position: number;
  state: "queued" | "dispatching" | "running";
  requestedDelivery: "start_now" | "enqueue" | "interrupt_then_start";
  safePreview: string;
  attachmentCount: number;
  contextCount: number;
  revision: number;
  createdAtMs: number;
}
```

原始 queued content 保留在 Manager scoped local store，绝不由 list、diagnostic、metric 或 notification API 返回。`safePreview` 是本地 display projection，不是 log field。

HaaS canonical `eventId`、`sequenceNumber`、`sessionId`、`turnId`、`invocationId` 与 `toolCallId` 继续作为权威。Manager-local execution 必须投影等价 identity，不得暴露 runtime-native 细节。未知 event 只增加不含内容的诊断计数并保持不可见；不得转成 assistant output 或猜测的 success。


Manager 将 `_managerTurnId` 与 `_managerRowId` 持久化为仅供展示的消息 sidecar，并在内部 WebSocket 中投影为 `turnId`/`rowId`。这些字段不得进入模型输入。唯一的历史迁移 adapter 在持久化 user/connector 意图边界为旧记录分配确定性身份；模型调用边界不得创建产品 Turn。最后一个已完成 Turn 保留在 live-tail 槽位，直到下一个 Turn 开始，保证封存期间正文 DOM 不变。

### 6.3 投影更新规则

- 初次加载先应用一个有界 snapshot，再 replay `lastEventId` 之后的 event，最后进入 live。
- 已完成的 HaaS-backed turn 只能通过持久化 `/messages` 投影恢复。打开或重连该历史会话时
  不得重新获取或发送其 process-event 历史。非终态恢复继续服从本节与 MCX-010 的既有 cursor
  规则；MCX-054 不改变其 live ordering 或 retry 语义。
- Bridge 进入终态后，持久化必须删除临时 event/content/tool 去重窗口。这些窗口属于 transport
  recovery state，不是会话历史，终态收敛后不得继续膨胀 Manager session binding。既有终态记录
  在 readback 时原位压缩，但不得删除 assistant text、activity、model-stage summary、evidence
  reference、task outcome 或 canonical id。
- 已按 `eventId` 应用的 event 为 no-op。
- 低 revision 不得覆盖高 revision。
- 文本 delta 按 sequence 只追加一次；final text 封存 live row，不创建第二份回答。
- 第一段 canonical user-visible assistant delta 创建 `assistantResponse`；后续 delta 只能更新其 text/state。不得以 word、time、activity 或 adjacency threshold 暂存，也不得在 work、narration 与 answer 容器之间移动。
- User-visible answer、user-visible progress、internal reasoning 与 tool evidence 必须由 typed transport fact 分类。来源不能证明 subtype 时，安全 assistant text 按 answer content 处理；GUI 不得从 prose 猜 subtype。
- Model-call start/finish 与 usage update 只能更新 evidence correlation 和 aggregate fact，不得追加主 timeline row 或切换 disclosure。
- 每个 started work item 最多接收一个 terminal state。
- 没有新 `turnId`/`invocationId` 时，terminal state 不得回到 running。
- Live sealing 与历史 replay 中，terminal parent turn 是 incomplete child snapshot 的权威状态。
  没有自身 terminal event 的 `running`、`pending` 或 `waiting` activity 在 completed/failed/
  incomplete turn 中归一为 failed，并沿用既有 missing-event 安全原因；cancelled turn 中归一为
  cancelled，不能因父 turn completed 而伪造 tool success。残留 running model stage 仅在父 turn
  completed 时归一为 completed，其他情况跟随 failed/cancelled 终态。原始持久化 evidence 不修改，
  非终态/paused turn 保留真实 child state。
- Reconnect `ready` snapshot 为 `running=false` 且 execution control 为 idle/cancelled 时，不得因
  较旧的 persisted task outcome 仍为 running 而复活 terminal transcript。History 加载后，无论
  ready/history 先后顺序如何，都由其 terminal outcome 胜出。只有 `running=true`、有效 non-idle
  control state 或新的 turn identity 才能重新进入 running。
- 在 Composer 启用冲突命令前恢复 pending interaction。
- Reconciliation 只能用权威 snapshot 或 event page 替换不确定 derived state；不得自动重发原用户命令。

### 6.4 Canonical presentation selector

一个 pure selector 从权威 snapshot 与 pending command receipt 派生 `ConversationPresentation`。Conversation header、Turn status、loading slot、Interaction Dock 与 Composer 必须消费该结果，不能各自解释 `running`、`taskPhase`、model stage、socket state 或 local button state。

```ts
interface ConversationPresentation {
  phase: ConversationRunState;
  statusLabel: string;
  primaryAction: "send" | "stop" | "continue" | "resolve" | "retry" | null;
  secondaryActions: Array<"pause" | "end_task" | "discard" | "open_evidence">;
  composerMode: "compose" | "queue" | "blocked";
  showWorkingIndicator: boolean;
}
```

Selector 强制满足：

- 可见 primary action 恰好为零或一个；
- `continue` 只允许出现在权威 `paused` 状态；
- `stop` 只在 submitting、running、pausing、resuming 或 stopping 仍可取消时出现。Paused 状态以 `continue` 为 primary，永久终止是单独命名的 secondary danger action，绝不作为同级 `Stop` 按钮；
- active interaction 独占 primary action，并抑制重复 ordinary loading；
- socket disconnect 本身不能把 terminal turn 改回 running；以及
- phase change 在同一个 React commit 中更新所有消费 surface。

Composer 生命周期控件采用克制的工具栏样式（MCX-031/015）：暂停为中性文字操作，停止为
32 px 方形图标按钮，保留本地化无障碍名称及 tooltip。静止时不使用边框、强调色或危险色填充、
光晕、spinner。暂停态的继续与结束任务使用同一紧凑文字控件，永久终止仍须明确命名。
处理中及过渡状态为不可点击的次级文字。Hover 只增加中性背景，键盘焦点保持清晰。
复用 canonical selector、handler、能力门控及断线/过渡禁用规则，不改变生命周期语义。
验证回调与运行/暂停/过渡状态，并覆盖双主题 390/1440 px 下的可见性及焦点。
本次不影响公共 API、事件、持久化、权限、adapter、容器或可观测性组件。

### 6.5 Live-tail 生命周期

最后一个 active product turn 作为唯一非虚拟 live tail 渲染；historical turn 留在有界 virtual list。Terminal sealing 时，完整 turn projection 原子移入 history，且不改变 `turnId`、row identity、disclosure preference 或 assistant response element 语义。新 turn 创建新 live tail，绝不复用上一 turn 的 response node。

## 7. 提交、草稿与追问队列合同

### 7.1 Per-session 草稿

`ConversationDraftStore` 为每个 `(endpointId, workspaceIdentity, sessionId-or-draftId)` 持久化文字、editor structure、attachment ref、selected context 和预期 model/mode。

Browser 实现使用 origin-scoped IndexedDB；Tauri 使用同一接口，并可在可用时使用 Manager local store。Raw draft content 不得写入 `localStorage`。每份草稿最多包含 256 KiB text/editor metadata 和八个 attachment reference。超限内容继续在内存中可编辑，并给出明确的本地持久化 warning；不得静默截断。Orphan draft scope 在 30 天后过期，现有 session 的 draft 保留到 send、显式 discard 或 session deletion。

- 写入 debounce 不超过 500 ms，并在平台允许时于 session switch、page hide 和 app close 时 flush。
- 草稿文本与附件名称/ref 仅保存在本地，排除在 log、metric、trace、crash report 与 analytics 之外。
- 附件字节继续由现有有界 staging owner 管理；草稿只保存 opaque local ref，不重复保存 payload。
- successful accepted/duplicate receipt 只清理冻结的 submission revision；等待 receipt 期间新增的文字或附件保留在下一份草稿中。
- 明确的 server rejection 或 client-side validation failure 恢复冻结草稿。
- Timeout、disconnect 或任何无法证明 rejection 的结果进入 `reconciling`；冻结草稿保持可见，但在 Manager 查询 command receipt 前不得再次提交。Manager 绝不自动 resend。
- 删除 session 时清除该 session 的草稿与 staged attachment；其他草稿不受影响。

### 7.2 Manager 内部命令 envelope

迁移期间以加法形式扩展 Manager WebSocket command：

```json
{
  "type": "user_message",
  "clientCommandId": "cmd_...",
  "idempotencyKey": "idem_...",
  "delivery": "start_now",
  "text": "synthetic example",
  "attachmentRefs": [],
  "model": "provider:model",
  "skill": null
}
```

`delivery` 为 `start_now`、`enqueue` 或 `interrupt_then_start`。在所有支持 backend 暴露真实且经过测试的 steering capability 前，`steer` 暂缓；不得把 steer 别名为 enqueue。

Manager 返回：

```json
{
  "type": "command_ack",
  "clientCommandId": "cmd_...",
  "status": "accepted",
  "disposition": "running",
  "turnId": "turn_...",
  "queueItemId": null,
  "outcomeRef": null,
  "error": null
}
```

`status` 为 `accepted`、`duplicate` 或 `rejected`。accepted/duplicate submission 的 `disposition` 为 `running`、`queued` 或 `terminal`；已完成工作的 duplicate 返回原 turn identity 与 terminal `outcomeRef`。rejected response 包含结构化安全错误，不带 turn 或 queue identity。ACK 前先持久化 acceptance。HaaS-backed execution 复用现有 operation-scoped `Idempotency-Key`；GUI 不得为解决不确定 Manager 传输结果而生成第二次 HaaS attempt。

Receipt 对账使用 Manager 内部且不含内容的读回接口
`GET /v1/sessions/{sessionId}/conversation-commands/{idempotencyKey}`。命中时以
`status=duplicate` 返回原 command/turn/queue/outcome identity；只有
`404 command_not_found` 能证明 command 未被接受。该接口绝不返回 prompt 或 attachment 内容。

初始 client/server handshake 声明 `conversationProtocolVersion: 2`。版本不匹配的缓存 browser client 接收结构化 `client_upgrade_required` response 与 reload action；server 不得静默降级到无 ACK sender。Production hashed asset 与 packaged-app 原子替换使其成为有界部署迁移，而非永久 legacy 协议。

`error` 存在时结构为 `{code, safeMessage, retryable, recoveryAction?}`，绝不包含 raw prompt、完整 tool argument、credential material、signed URL 或 backend stack trace。Queue mutation command 携带 `queueItemId`、`expectedRevision` 与独立 idempotency key；stale revision 返回结构化 conflict 与最新 queue snapshot。
Command 集合为 `queue_edit`、`queue_delete`、`queue_move(targetPosition)` 与
`queue_send_now`。send-now 停止失败时返回 `queue_send_now_failed`，item 保持 queued，且不得开启自动 drain。

Submission 状态迁移为：

```text
draft -> submitting -> running -> terminal
                    -> queued -> dispatching -> running -> terminal
                    -> terminal (duplicate receipt of completed work)
                    -> rejected -> draft_restored
                    -> reconciling -> running | queued | terminal | rejected
```

`submitting` 与 `reconciling` 保留冻结 submission。只有 `accepted` 或 `duplicate` 可进入 `running`/`queued`；只有 `rejected` 可直接恢复。Timeout 本身绝不代表 rejection。

### 7.3 Follow-up queue

- turn 运行时允许用户继续编辑。
- 默认 Enter 遵循配置的 delivery policy：busy 时默认 `enqueue`，idle 时默认 `start_now`。
- 可见 modifier hint 提供 `interrupt_then_start`；必须完整说明破坏性后果，不得由未披露快捷键触发。
- Queue item 展示顺序位置、简短内容预览、附件/context 数量与状态。
- queued item 可撤回编辑、删除、排序或立即发送；dispatching/running item 锁定不可修改。
- `send now` 原子停止当前 foreground turn 并启动所选 item；停止失败时 item 保持 queued，并展示恢复动作。
- Stop 或 failure 默认暂停自动 drain。用户需显式 Resume 或发送一条选定 item；成功完成后按持久 policy drain。
- Queue 顺序和 mutation receipt 在 Manager restart 后保留。重连后 GUI 不把 optimistic order 当成权威。
- 此用户 follow-up queue 与 HaaS deployment Admission Control、workspace-writer queue 不同；UI 文案不得混淆这些概念。

Queue item 状态迁移为：

```text
queued -> editing -> queued
queued -> deleted
queued -> dispatching -> running -> terminal
                      -> queued (pre-start stop/admission failure)
```

`dispatching` 不可修改。Execution acceptance 之后的失败属于 running turn，不重新创建 queue item。

## 8. Turn、Work、Interaction 与 Completion 行为

### 8.1 Turn 展示

每个可见 turn 遵循一个稳定顺序：

1. 用户意图与结构化 context chip；
2. 紧凑的当前/已完成 activity summary；
3. 渐进披露的 tool activity 与 execution evidence；
4. 最终 assistant result；
5. completion summary 与持久恢复动作。

正常已完成工作默认折叠。运行中工作展示一个紧凑 active summary。failed、interrupted 或 incomplete work 默认展开到第一个可操作失败点。用户 selection 和 disclosure 在 live update 期间保持稳定。

默认 running Turn 有严格视觉预算：

- 一个 user-intent block；
- 一组有界 inference-round list，每个 canonical model round 严格对应一条单行安全摘要 row；
- 仅当需要用户立即感知或操作时，最多展示一个当前相关 tool 或第一个可操作 failure；以及
- 一块从第一段可见 delta 起出现的 assistant-response block。

Reasoning chunk、usage arrival 与 cache accounting 不占用同级卡片。Model round 只在有界 work region
内使用紧凑 row，不成为 card。Row label 使用该 round 最后一条非空安全 reasoning summary；同一
round 内多段 summary 只原位更新这一 row。Tool lifecycle 永远不拥有 row label。Running round 尚未
收到 summary 时使用本地化通用“处理中”。

最新 running round 默认展开，并且是唯一拥有持续动效的 row。更新的 round 开始时，上一条未被
用户操作的终态 round 自动折叠，新 round 展开。Completed、failed、cancelled 历史 round 保持可见
但默认折叠；用户显式 toggle 优先于后续 live update。展开 round 只在该 row 下显示其 canonical tool
activity。主 timeline 继续不展示 model-call ordinal/id、provider label、raw reasoning 或 per-call token panel。

### 8.2 Activity summary 与 evidence

- Summary title 依次优先采用显式本地化 product action、安全 tool/object summary、有界 command preview，最后使用本地化中性 fallback。
- Raw reasoning、raw commentary、model/provider prose、prompt、argument、stack trace、transport name 与 model-call ordinal 绝不得用作 title fallback。
- 一个 count 覆盖 product work segment 或 activity。Model-call count 与 reasoning-chunk count 属于 diagnostic，绝不表示任务进度。
- Running summary 不以数字 count 作为主要内容；可选 completed aggregate 只进入安静 completion footer 或展开详情。
- Running state 必须包含持久文字标签。动画可选，并在 reduced motion 下关闭。
- Activity row 展示 status、安全标题、可选 key result 和 duration；默认不展示 raw argument。
- 选择 activity row 时，在该 row 正下方切换一个 inline detail disclosure。它继续使用现有安全 evidence path，并保持焦点与滚动上下文；不得打开右侧 Inspector 或底部 drawer。显式请求 model-call evidence 时也遵守同一 inline disclosure 规则。
- 展开的 work row 保留后端提供的安全命令预览或对象摘要，以便区分多条命令。Inspector 立即
  展示该预览，在 evidence 加载、缺失、过期或不可用时仍然保留。完整命令只来自已授权 evidence，
  不得序列化 raw tool arguments 作为回退。选中项使用稳定 activity id，使状态、输出和后到达的
  evidence 引用自动更新，无须关闭重开。MCX-013 回归覆盖仅预览、evidence 过期/失败，以及
  详情保持打开时 running 到 terminal 的更新；先实现选中投影与预览展示，再更新浏览器基线。
  显式本地 engine 路径通过 sidecar spec 定义的 Manager 展示边界，提供相同的安全命令预览及
  稳定 tool identity；不改变 HaaS/ADK、工具执行权限或持久化格式。

Public activity preview 不暴露绝对 host path。当 command output 包含该命令已授权 working directory 时，adapter 先替换为 `workspace/` 加安全相对后缀，再执行 credential/URL/path 脱敏。因此在 workspace root 执行 `pwd` 时显示 `workspace/`；workspace 外 host path 继续显示 `[REDACTED_PATH]`。精确路径只通过有 scope 且未过期的 execution evidence 提供。

Reasoning summary 是持久、有界的进度 label，不是 raw reasoning transcript。每个 canonical model
round 在 live 与 restored history 中最多贡献一条 row；row 只保留该 round 最新安全 summary。Raw
chain-of-thought、commentary、provider prose、prompt、tool text 与 model-call metadata 绝不进入 label。
最终 assistant response 仍是该 turn 最后一段实质内容。

Tool work 使用统一 `ToolActivity` 合同，包含 header、安全 input summary、有界 result、status、duration 与 evidence action。只有 active tool 或第一个可操作 failure 可默认展开；成功完成的 tool 默认收起。Tool input/output 与 evidence 不得嵌套在 model-call card 中。

### 8.3 稳定 Assistant Response

- Assistant response 在完整生命周期内只有一个 `rowId`、一个 semantic article/container 和一个插入位置。
- 第一段用户可见 text delta 在下一次合并 publication 后进入该容器；不存在最少词数或人为 1–2 秒 hold。
- Work summary 可以在其上方更新，但 answer text 绝不从 loading/activity area 迁移到另一 bubble。
- Terminal text 封存同一 node；replay/restore 与 live turn 产生相同 semantic DOM 与顺序。
- 没有 answer delta 时，只允许一个紧凑 16 px status slot 展示 `Working`；第一段 delta 到达后移除重复 loading indicator，且不产生第二次状态播报。
- Streaming cursor 只是可选装饰，不能成为 streaming 的唯一提示，并在 reduced motion 下关闭。

### 8.4 Pending interaction dock

Approval、input request、directory permission、tool installation、plan/team/item proposal、policy conflict 与 recoverable execution failure 共用一个 `PendingInteractionDock` shell。

- 只能有一个 primary request；后续 request 显示数量并排队。
- 标题说明需要做出的决策；primary button 重复完整后果。
- Details 有界且可展开；outbound 或 destructive action 使用明确区分的视觉处理。
- 解决 request 后锁定重复动作，直到权威结果到达。
- Request 成为 primary 时，普通 Composer 保持 mounted 以保留草稿，但在视觉与可访问树中隐藏并设为 `inert`。Free-text feedback 在 dock 自有 response field 中输入，绝不消费或覆盖普通草稿。
- 合适时 request 到达会把 focus 移到 dock heading；解决后 focus 返回触发控件或 Composer。
- 存在恢复动作的 error 必须说明该动作；不可重试的终态 error 应明确任务无法继续，不展示无效 Retry。

### 8.5 持久 Mode Context 与 Durable Notice

Bypass approvals、sandbox/runtime identity、policy posture 等执行模式属于持久 header 或 Composer context chip，不得在每个 turn 生成大块 timeline notice。Timeline notice 只用于会改变前后工作解释方式的持久状态迁移；它必须紧凑、与 conversation measure 左对齐，且不能成为主要视觉 block。

### 8.6 Completion summary

完成的 turn 可展示一个安静 footer，包含 duration、activity count、权威时才显示的 token usage、changed/produced artifact count 与直接 artifact action。缺失 usage 时 footer 直接省略，不渲染成持续 warning。最终 assistant result 始终是主要内容。

Per-model-call input/output/reasoning/cache usage 只属于 Evidence Inspector。权威 aggregate turn usage 可出现在 completion footer。`Pending`、`unreported`、零值与缺失是不同事实；缺失或 pending accounting 在普通 conversation layout 中静默省略，除非它阻塞 billing、policy 或 task completion。

### 8.7 Lifecycle 词表与产品文案

Timeline、header、Composer、notification 与 accessibility name 共用一套 sentence-case lifecycle 词表：`Working`、`Waiting for approval`、`Waiting for input`、`Pausing`、`Paused`、`Continuing`、`Stopping`、`Completed`、`Failed`、`Cancelled`。本地化字符串保持相同语义区分，不能围绕 count 拼接句子片段。

Action label 必须 verb-first 且无歧义：`Send`、`Stop`、`Pause`、`Continue`、`End task`、`Retry`、`Reconnect`、`Show work`、`Hide work`、`Open evidence`。`Continue` 只表示恢复权威 paused task。重大 confirmation 重复其后果。Error 必须给出安全原因与下一步动作；可操作错误不能只显示 `Something went wrong`。缺失 telemetry 不使用 warning 文案。

## 9. 标准 React AI 组件架构

目标源码结构：

```text
manager/surfaces/gui/src/conversation/
  model/          # typed snapshot、turn/work/interaction/queue model
  transport/      # Manager HTTP/WS adapter 与 command receipt
  store/          # projection reducer、draft store、selector
  hooks/          # 细粒度 state 与 command hook
  components/
    ConversationShell.tsx
    ConversationTimeline.tsx
    TurnGroup.tsx
    TurnWorkSummary.tsx
    WorkDisclosure.tsx
    ReasoningDisclosure.tsx
    ToolActivity.tsx
    AssistantResponse.tsx
    PendingInteractionDock.tsx
    FollowUpQueue.tsx
    ConversationComposer.tsx
    ConversationStatusPanel.tsx
    TurnCompletion.tsx
    EvidenceInspector.tsx
  tokens/
    conversation.css
```

组件规则：

- presentational component 不直接打开 WebSocket、调用 REST endpoint、修改 canonical store、判断 backend type 或翻译 transport payload；
- transport adapter 产生 typed fact；一个 reducer 独占 projection 写入；
- projection store 通过仓库自有 hook 使用 React `useSyncExternalStore` 合同；本组件不引入新的通用 state-framework dependency；
- hook 只暴露最小 selector result 与稳定 command callback；
- 每个 export component 都有明确 props、loading/empty/error/disabled state、键盘行为、accessible name 和聚焦测试；
- 组件名描述产品语义，而非某个 harness 或当前视觉样式；
- tool-specific rendering 使用由 canonical activity kind 索引的 registry，并提供安全 generic fallback；
- `TurnGroup` 只负责排序；`TurnWorkSummary`、`WorkDisclosure`、`ReasoningDisclosure`、`ToolActivity` 与 `AssistantResponse` 只接收已投影的 product prop，绝不检查 model-call array；
- 主 timeline 内任何 export/internal component 都不得按 `modelCallId` 建 key、命名或视觉分组；model-call correlation 仅限 evidence adapter/Inspector；
- `AssistantResponse` 从第一段可见 delta 到 sealing 始终 mounted，并保留同一 accessible name、semantic role 与 row identity；
- disclosure preference 按 `turnId + disclosureKind` 标识，不被 token/stage update 重置，只随所属 turn/session 生命周期清理；
- 只有输入稳定且实测有收益时才使用 `React.memo`；跨 live boundary 的 callback 与 collection prop 保持 referentially stable；
- Context provider 按更新频率拆分。token-level/live projection state 不得与 shell navigation、Composer draft 或关闭的 panel 共用 provider value；
- `ConversationTimeline` 通过仓库自有 adapter 使用 `@tanstack/react-virtual`，把 row measurement、selection anchor 和未来替换 library 的影响限制在局部；
- 正在运行的 live tail 在封存前位于 virtual history 之外，避免 token delta 使历史 measurement cache 失效；以及
- 所有公共 component 行为都通过语义和用户动作测试，不依赖内部 class name。

## 10. 视觉 Token 与主题合同

### 10.1 产品气质

Conversation surface 必须克制、紧凑、可操作且具有桌面原生感。使用中性背景层级、节制的 border/shadow、一个 cobalt 交互 accent 与语义 status color。避免营销式大留白、装饰渐变、过度 card nesting，或仅为加强视觉而滥用颜色。

### 10.2 Tokens

组件只能消费 semantic token：

- surfaces：`--color-canvas`、`--color-chrome`、`--color-surface`、`--color-surface-raised`、`--color-popover`；
- text：`--color-text-primary`、`--color-text-secondary`、`--color-text-tertiary`、`--color-text-inverse`；
- structure：`--color-border`、`--color-border-strong`、`--color-focus-ring`；
- interaction：`--color-accent`、`--color-accent-hover`、`--color-accent-soft`；
- status：`--color-success`、`--color-warning`、`--color-danger` 及对应 surface/text role；
- typography：`--text-title`、`--text-heading`、`--text-body`、`--text-ui`、`--text-mono`、`--text-caption`；
- spacing：以 4 px 为基础，group spacing 至少是内部 item spacing 的两倍；
- radius：surface 12 px、nested surface/control 8 px、compact 6 px、pill 999 px；以及
- motion：高频反馈不超过 150 ms，只过渡明确属性，token streaming 期间不做 layout animation。

迁移后的 conversation component 禁止 raw color、任意 font size 和新增的一次性 radius/shadow 值。现有值必须在所属 component cutover 前迁移到 token。

初始 light/dark baseline 固定如下。任何变更都必须经过 contrast measurement 与成对 visual-regression 批准，不得在 component 内局部覆盖。

| Role | Light | Dark |
|---|---|---|
| canvas | `#FAFBFC` | `#191B1F` |
| chrome | `#F4F6F8` | `#15171A` |
| surface | `#FFFFFF` | `#1F2227` |
| surface-raised / popover | `#FFFFFF` | `#272B31` |
| text-primary | `#17191C` | `#EEF0F3` |
| text-secondary | `#4D535C` | `#B4BAC3` |
| text-tertiary | `#626A73` | `#A0A7B0` |
| accent / focus | `#2563EB` | `#78A8FF` |
| success | `#217A50` | `#70D6A2` |
| warning | `#8A4B08` | `#F0B35D` |
| danger | `#B42318` | `#FF8A80` |

Typography role 的初始 baseline 同样固定：title 20/25 px weight 600、heading 16/22 px weight 600、body 14/22 px weight 400、UI 13/18 px weight 400、mono 12/19 px weight 400、caption 11/15 px weight 600。Mobile editable input text 保持 16 px。Font family 继续使用仓库内置 Inter 与 JetBrains Mono；Manrope 仅用于产品 wordmark。

Conversation 密度还必须满足：

- User/assistant 主内容使用 `body`；control/status 使用 `ui`；`caption` 仅用于真正次级 metadata，不能承载必需 recovery 或 lifecycle 信息；
- 一个语义层级最多组合一层 enclosing border 与一层 nested divider；
- succeeded/completed work 不使用整卡 tinted background；
- 窄宽度下 metadata 换行到内容下方，不预留与正文竞争的 fixed column；以及
- migrated conversation component 中 raw hex/RGB color、任意 `text-[Npx]` 与一次性 shadow/radius 均阻塞 release。
- 变化中的 duration、count 与 usage 使用 tabular numeral；主 prose 与 evidence text 保持可选择；truncation 必须可通过 disclosure 或 Inspector 访问完整安全值。
- Hover-only treatment 按 pointer capability 启用；transition 明确列出 property 而非使用 `all`；theme switching 在切换 frame 抑制 color/background/border/shadow transition。

每个状态只能有一个 motion owner：

| 状态/变化 | 允许的 motion owner | 禁止的竞争 motion |
|---|---|---|
| 第一段 answer delta 前等待 | 紧凑 working indicator | Pulsing card、animated border 与 background gradient |
| Streaming answer | 可选 terminal cursor/fade | 移动 answer container 或动画其上方 layout |
| Tool running | 展开时的 tool status glyph | Whole-card shimmer 或多个 stage spinner |
| Disclosure 开合 | 最多 180 ms 的 height/opacity transition | Spring/bounce 与 scroll-anchor movement |
| Turn completion | 最多 150 ms 的一次 status cross-fade | 持续 glow 或 completed-item animation |
| Reduced motion | 无非必要 motion | 只能依靠动画理解的状态 |

即使每项动画单独使用合法 token，同一状态同时新增第二个动画也违反本规格。

Visual-regression fixture 覆盖 empty/idle、running with tools、waiting for approval、queued follow-up、recoverable failure、completed-with-artifacts 与 long-content，并在两种主题的 390 和 1440 px 下执行。另以 320 px / 200% zoom 做功能验证。出现重复事实、一个状态超过一个 filled primary action、不可访问的 truncation、任意 token，或改变 reading anchor 的 layout shift 时，评审必须拒绝。

### 10.3 主题与无障碍

- 浅色和深色主题实现同一套完整 semantic token；缺失的 dark token 不得 fallback 到 light 值。
- 小于 18 pt 的 normal text 通过 4.5:1，大字号通过 3:1，focus 与非文本 UI indicator 对相邻颜色通过 3:1。
- 所有 interactive element 在可用时使用 native semantics，并有至少 2 px 的可见 `:focus-visible` indicator。
  文本 Composer 使用包围输入区的 1 px 中性焦点边框与原生光标；内部控件仍保留各自键盘焦点指示。
  Composer 聚焦不得增加品牌色外圈、光晕、背景染色或阴影。使用深浅主题均显式定义的同一个语义
  边框 token，对相邻表面至少 3:1 对比度，聚焦不得引发布局位移。MCX-015 视觉检查覆盖两主题、
  390/1440 px 下的空闲/编辑与键盘导航。本次仅修正展示，不改变输入投递、持久化、权限、事件或公共 API。
- 控件最小 target 为 24×24 CSS px；在桌面密度允许时目标为 40×40。
- 状态不得只通过颜色或动画表达。
- 动态状态通知只使用一个稳定 polite live region，且只发布有意义的 phase transition；不得逐条播报 token、timer 或 progress increment。
- `prefers-reduced-motion` 移除非必要运动；没有 cursor blink 或 animated spinner 时，streaming 与 progress 仍可理解。
- 320 px 与 200% zoom 下内容保持可用；mobile Web input 使用 16 px，避免 iOS 非自愿 zoom。

## 11. 性能与渲染合同

- 10,000-row fixture 下，historical DOM 最多挂载 200 个 conversation row，包括 overscan，但不含 live tail 与已打开 Inspector。
- Live text/reasoning/activity update 最多每 animation frame 发布一次 projection，并在 terminal/error/cancel 时保留最后 delta。
- Profiler 计数清零后，30 次 live publication 对 Sidebar、inactive route、Composer、关闭的 Status Panel 与关闭的 Inspector 产生零次 commit。
- Active Turn subtree 可以更新；selector input 未变化的 completed Turn group 不更新。
- 向上滚动、选择文字、打开 disclosure 或 Inspector 会关闭自动 bottom following；Jump-to-latest 显式重新开启。
- Virtual row replacement 应在可行时让 selected row 或可见语义 anchor 偏移不超过 2 CSS px；terminal compaction 时绝不能跳到顶部。
- Initial bundle budget 与 route splitting 继续由 Manager GUI Performance 负责。本功能不得把 optional artifact、settings 或 connector 代码重新拉回 entry graph。
- Request coordination 保持 single-flight 且感知 visibility。Conversation component 不得引入自己的 polling loop。

## 12. 失败、恢复、兼容与回滚

### 12.1 失败与恢复

| 失败 | 必须行为 |
|---|---|
| Acceptance 前被拒绝 | 恢复冻结草稿与 attachment ref，展示结构化原因 |
| 传输断开且 acceptance 不确定 | 进入 reconciling，查询 receipt/snapshot，绝不自动 resend |
| Duplicate command | 复用原 turn/queue identity，只渲染一次 |
| Event gap 或 cursor 过期 | 停止 live projection，执行有界权威 readback，展示 recovery state |
| Manager 重启时存在 queued item | 启用冲突动作前恢复 queue order 与 paused/draining policy |
| Turn 中 HaaS/backend 重启 | 恢复 accepted invocation 与 cursor；除非生命周期合同明确要求，不创建 replacement turn |
| Stop 失败 | 保持当前状态与 queued item，展示 retry/readback 动作 |
| Projection invariant 违规 | 对受影响 conversation surface fail closed，保留原始 durable fact，暴露不含内容的诊断 |
| Assistant text 无法分类 | 脱敏后进入稳定 assistant response；不得隐藏、按词数提升或作为 reasoning 暴露 |
| Usage 缺失或延迟 | 普通 conversation UI 省略 usage；权威数据到达后只更新 aggregate completion metadata，不改变 layout owner |
| Component chunk 失败 | 保持 session 与 draft，展示可重试 surface loading error |

### 12.2 兼容性

- ADK route、ADK Event 语义与现有 `/v1/haas/*` route 不变。
- 复用现有 canonical HaaS event id 与 correlation field。缺失的 Manager-local identity 增加到 Manager projection 或内部 WebSocket envelope，UI 不得自行推断。
- 纠偏期间，现有 model-call/stage payload 可继续作为内部 evidence input，但它不是 GUI compatibility surface。它只能由 evidence adapter 消费，并可在 product work segment 获得 canonical input 后删除。
- 迁移期间 Manager 内部 command field 做加法扩展。最终 GUI 只使用带 ACK 的 command path，随后删除旧的无 ACK sender。
- Browser 与 Tauri 共用 component、projection、queue 与 draft contract。平台 wrapper 只增加 native file picking、notification 和 window lifecycle。
- 旧持久 transcript 通过 versioned persistence decoder 或一次性 migration 转换为新 typed model。该兼容代码隔离在 React 之外，并有明确 retained-data sunset。旧 renderer、UI item model、dual writer 与旧 CSS 均不得保留。

### 12.3 回滚

每个 migration wave 都有明确 rollback commit。初始 migration 可在 cutover gate 前使用一个临时 internal flag。实现后 C0-C4 纠偏直接修改现有 v2 path，不得增加第二套 renderer flag；rollback 以完整 slice 为单位回退。Slice 验收后，被替代代码必须在同一或紧随其后的 slice 删除。最终 release 不存在用户可见 old/new toggle 或永久 dual-write path。

## 13. 渐进交付与 Legacy 删除

| Wave | 范围 | 准入门禁 | 准出门禁 |
|---|---|---|---|
| W0 Baseline | 固定当前行为、截图、状态迁移、render/request count | 已批准 spec | 为草稿丢失、queue 缺失、identity 推断、focus、contrast 和长 DOM 建立可复现失败测试 |
| W1 Foundations | Semantic token、双主题、focus primitive、component shell primitive | W0 | Token lint、主题截图、keyboard/contrast gate 通过；无视觉行为回归 |
| W2 Projection shadow | Typed model、projector、store、selector；与旧 view 对比但不改变输出 | W0 | Live/replay/reconnect golden parity 和 invariant test 通过；fixture divergence 为零 |
| W3 Input lifecycle | DraftStore、command ACK、idempotency、follow-up queue、Stop/Escape/recovery | W2 | 故障测试中无草稿丢失、重复执行、stale queue 或跨 session restore |
| W4 Component cutover | Timeline、Turn、Activity、Interaction Dock、Composer、Queue、Status、Completion、Inspector | W1-W3 | Browser/Tauri 行为与视觉 parity；320/390/1440 及双主题通过 |
| W5 Performance | Virtual history/live tail、selector isolation、scroll anchor、bundle check | W4 | 10,000-row DOM bound 与 profiler/request/scroll budget 通过 |
| W6 Legacy zero | 删除旧 `Item` 推断、live buffer、旧 Transcript/Composer 路径、过时 CSS/test/flag | W2-W5 | Import/grep/coverage 证据证明无旧 owner、dual write、compatibility renderer 或永久 flag |

只要同一事实仍由新旧 writer 同时修改，任何 wave 都不得标记完成。临时 shadow projector 只能只读并产生 comparison diagnostic。

W6 明确删除或替换当前 `WsEvent.data: any`、transcript `Item` adjacency grouping、顶层 `streamingRef`/`reasoningRef`/`modelStagesRef` ownership、`resetKey` draft clearing、旧 `Transcript`/`Composer` render path、其专属 CSS hook，以及临时 conversation-v2 migration flag。门禁基于 import/usage 证据，而不是只看文件名。

### 13.1 Packaged-product 证据后的纠偏演进

第一版实现通过了原技术门禁，但不满足 2.4 节的产品信息层级。纠偏在现有 v2 path 上按 vertical slice 推进；禁止新增 v3 renderer、第二份 transcript 或长期 UI flag。

| Slice | 范围 | 必须先建立的失败证据 | 准出门禁 |
|---|---|---|---|
| C0 Projection contract | Product-turn/work/answer model 与 canonical presentation selector | 当前 fixture 产生同级 model-stage card 与矛盾 action | Selector/invariant test 证明一个 turn、一个 response owner、一个合法 primary action |
| C1 Stable live answer | 删除 word-count gate，把第一段 delta 绑定到 `AssistantResponse` | 测试证明短 response 被隐藏或迁移 | 短/长/tool-interleaved stream 保持同一 row 与 semantic node |
| C2 Calm work disclosure | 用有界 per-round 安全摘要 row 与所属 tool disclosure 替换 raw stage/evidence chrome | 八次调用 fixture 占满主视口 | 默认在 320 px region 内每个 inference round 一条紧凑 row，历史折叠，只展开最新 running/actionable detail |
| C3 Hierarchy and motion | 移动 persistent mode context，删除 token warning/raw color，统一 type/layout/motion | 成对截图复现密集卡片与竞争动画 | 双主题及所有目标宽度通过 visual、motion、contrast 与 reading-anchor review |
| C4 Legacy correction | 删除 `streamGate`、stage-card UI/CSS/copy、独立 lifecycle selector 与过时测试 | Grep/import inventory 找到每个旧 owner | 不保留旧 heuristic、component、style、translation key、selector 或 dual projection |

每个 slice 都更新同一 typed projection 与 component tree。C1、C2 仅在中间态仍保证单一稳定 response owner，且主 timeline 无 model-call card 时才能分开落地。

## 14. 测试计划与验收用例

### 14.1 测试层级

- Unit：reducer、selector、状态机、token validation、queue ordering、draft revision、idempotency 和 rendering variant。
- Integration：Manager WebSocket command ACK、persistence、reconnect、HaaS/local normalization、interaction recovery 和 terminal convergence。
- Hermetic GUI E2E：使用仓库 fixture 覆盖浅/深主题和窄/宽 viewport 的全部用户流。
- Production preview：fresh Vite build、request/console/chunk/long-task observation 与 screenshot。
- Packaged desktop：真实 Tauri shell、sidecar restart、session restoration、native focus 与 file action。

### 14.2 可执行验收矩阵

| ID | 优先级 | 场景 | 期望证据 |
|---|---|---|---|
| MCX-001 | P0 | Send accepted | 持久 ACK 后才清草稿；一个 user row、一个 turn |
| MCX-002 | P0 | Send rejected | 原样恢复冻结草稿与 attachment ref；展示结构化恢复 |
| MCX-003 | P0 | Acceptance 后 ACK 丢失 | Receipt reconciliation 找到原 identity；不产生第二次执行 |
| MCX-004 | P0 | Session switch/refresh/app restart | 每个 session 只恢复自己的 draft 与 queue |
| MCX-005 | P0 | Busy Enter | Submission 变成一个可见 queued item，不并发启动 |
| MCX-006 | P0 | Queue edit/delete/reorder | 重启后持久顺序与 UI 一致；locked item 拒绝 mutation |
| MCX-007 | P0 | Send now | Stop + selected dispatch 原子执行；失败时 queue 保持完整 |
| MCX-008 | P0 | Stop/error with queue | Auto-drain 暂停且显式 Resume 有效 |
| MCX-009 | P0 | Live/replay parity | 相同 canonical fixture 产生相同 typed turn 与 terminal result |
| MCX-010 | P0 | Mid-turn reconnect | 从 cursor 恢复原 turn；不重复 user row 或 tool |
| MCX-011 | P0 | Pending interaction restore | 启用冲突 Composer action 前先出现 Dock |
| MCX-012 | P0 | Status accuracy | 可见 work/tool state、summary 与 canonical lifecycle action 不矛盾 |
| MCX-013 | P0 | Failure recovery | 仅在允许时展示 Retry，并创建/复用合同规定的 identity |
| MCX-014 | P0 | Keyboard flow | Compose、send、stop、queue、approval、disclosure、inspector、recovery 无需 pointer |
| MCX-015 | P0 | Focus | 每个 focusable control 有可见 focus；modal/dock 正确归还 focus |
| MCX-016 | P0 | Theme contrast | 自动 token pair 与 browser-computed light/dark surface 通过 WCAG AA |
| MCX-017 | P0 | Secretless | Log/event/metric/telemetry 不含 draft、raw prompt、完整 tool arg、credential 或 signed URL |
| MCX-018 | P1 | Responsive | 320/390/760/1200/1440 与 200% zoom 保留全部必需动作 |
| MCX-019 | P1 | Reduced motion | 无必要状态依赖动画；streaming 与 progress 仍清晰 |
| MCX-020 | P1 | 10,000 rows | Mounted conversation row <=200；selection 与 scroll anchor 稳定 |
| MCX-021 | P1 | 30 live publications | 无关 Profiler boundary 在 reset 后 commit 为零 |
| MCX-022 | P1 | Long streaming turn | Publication rate <= one/frame，terminal flush 保留 final delta |
| MCX-023 | P1 | Completion summary | Duration/count/usage/artifact fact 只渲染一次；缺失 usage 省略 |
| MCX-024 | P1 | Browser/Tauri parity | 同一 fixture 在两个 surface 产生等价 semantic DOM 与 action |
| MCX-025 | P1 | Component API | 每个导出 AI component 都有聚焦 state/keyboard/theme test |
| MCX-026 | P0 | Legacy-zero gate | 旧 symbol、CSS hook、renderer import、dual write 与 migration flag 均不存在 |
| MCX-027 | 已由 MCX-049 退役 | 不提供 conversation-local search 与上一条/下一条控件；Cmd/Ctrl+F 交由 browser/WebView 原生处理，global session search 继续可用 |
| MCX-028 | P2 | Semantic context chip | Skill/file/session/context ref 正确渲染、复制和打开，不暴露 raw transport syntax |
| MCX-029 | P0 | Product-turn hierarchy | 含八次 model call 的 fixture 在主 timeline 只渲染一个 Turn、一个 work summary、一个 assistant response、零 model-call card |
| MCX-030 | P0 | First-delta stability | 1-39 词 answer 在一次合并 publication 后可见；经过 40 词、tool arrival 与 terminal sealing 时 `rowId` 和 semantic DOM owner 不变 |
| MCX-031 | P0 | Lifecycle action matrix | 每种 phase/receipt/interaction 组合最多一个 primary action；running 不显示 Continue，paused 不把 Stop 作为同级 primary action |
| MCX-032 | P0 | Work disclosure ownership | Running/completed multi-call work 默认只有一个 summary；用户 disclosure 选择经受所有 live update 与 completion rule |
| MCX-033 | P0 | 安全 activity copy | Raw reasoning/commentary/provider prose 与注入的类 secret argument 不进入 title、summary、status、notification 或 accessible name |
| MCX-034 | P0 | Usage hierarchy | Missing/pending usage 不产生 warning；权威 aggregate 只在 completion 出现一次；per-call usage 保留为内部 diagnostic data，不在会话中渲染 |
| MCX-035 | P1 | Motion ownership | 每个状态最多一个允许动画 owner；token update 不动画 layout；transition 不使用 `all`；theme swap 无拖影；reduced-motion 截图与行为保持完整 |
| MCX-036 | P1 | Persistent mode context | Bypass/policy/runtime mode 在 header/Composer context 只渲染一次，不产生重复或强势 timeline notice |
| MCX-037 | P1 | Narrow work layout | 320/390 px 与 200% zoom 下，长及 pseudo-localized command/title/metadata 在 72ch conversation measure 内换行，不出现 fixed metadata column、action 裁切或 card 高度爆炸 |
| MCX-038 | P0 | Live-tail geometry | 阅读旧内容期间经过 100 次 delta、tool update 与 completion，semantic anchor 偏移不超过 2 CSS px，直到 Jump to latest |
| MCX-039 | P0 | Dynamic accessibility owner | 一个 polite live region 只播报有意义 phase change；token、usage、stage 与 timer update 不产生重复播报 |
| MCX-040 | P0 | Assistant response 幂等 | 同一 turn 的两条 assistant-message fact（包括不同 transport row id 或 replay）只渲染一个 response owner 和一份权威文本；reload 与 live 输出一致 |
| MCX-041 | P0 | Chronological tool activity | Tool activity 按 canonical 发生顺序仅在所属 Manager-owned inference round 下渲染一次。Round 只展示最新安全 summary，不出现 raw reasoning-parent container、native model-call metadata 或重复 tool row |
| MCX-042 | P0 | Inline 安全 activity detail | 点击 command row 后详情直接在其下方展开，不打开侧边/底部 Inspector；workspace 内路径使用 `workspace/`，外部 host path 继续脱敏，evidence 过期时仍保留安全 command/preview |
| MCX-043 | P1 | Search overlay focus | 全局搜索在双主题使用带语义圆角的 input shell 与中性 focus border；不出现内部矩形品牌色 outline 或品牌色整行 active fill |
| MCX-044 | P1 | macOS titlebar 对齐 | overlay 模式下，原生 traffic light 与 sidebar/panel 展开、折叠控件共用 packaged AppKit button frame 的实测中心，sidebar 展开/折叠前后中心偏差均不超过 1 CSS px。当前 pin 的 `traffic_light_position(..., y=24)` 组合实测 WebView 相对中心为 22 CSS px，12 px browser simulator 使用 `top:16px`；任何推算 geometry 都不得覆盖 packaged 证据。文字块保持自身排版 baseline，不作为 control center 的对齐参考。Browser 与 packaged screenshot 必须覆盖 expanded、collapsed、maximize 与 restore 状态 |
| MCX-045 | P0 | Composer 尾部 cluster | 双主题 320/390/760/1440 px 下，model、microphone、Send/Stop 按该顺序保持可见，以 peer gap <=8 CSS px 组成不换行的尾部 cluster；mic/action hit target 固定，仅长 model label 显示 ellipsis，idle、running 与 recording fixture 保持同一 ownership |
| MCX-046 | P0 | Terminal child-state convergence | Live sealing 与历史 replay 的 completed/failed/cancelled turn 不得继续把 child activity 或 model stage 显示为 running/pending/waiting；dangling tool 除 cancelled 外归一为 failed，stale model stage 跟随 parent terminal state，且不修改持久化 evidence |
| MCX-047 | P0 | Reconnect terminal monotonicity | `ready -> history` 与 `history -> ready` 两种顺序下，`running=false` 加 idle/cancelled control 不得用 stale non-terminal task outcome 覆盖 terminal transcript；UI 不显示 working indicator/Stop，同时真实 running snapshot 仍能恢复这些状态。权威 invocation readback 已终态但缺少 `terminalEventId` 时，Manager 必须立即收敛为安全 `incomplete`，不得扫描完整事件历史，也不得留下无法生效的 Stop action |
| MCX-048 | P0 | 可选模型可用性 | Composer 模型菜单只包含已显式启用且 routed provider 当前可用的模型：凭证型 provider 必须已配置 credential，OAuth provider 必须有已登录 profile，keyless local provider 必须通过 live discovery。配置 provider 不得隐式启用其整个静态模型矩阵；setup 只能显式启用该 provider 的 recommended model，其他 catalog model 必须经过用户勾选或添加。不可用或未启用的当前/default model 可作为 immutable session fact 继续显示，但不得被注入 selectable option。无 selectable model 时 Composer 显示连接模型；存在其他 selectable model 时只提供该列表。Settings 保留完整 suggested catalog 用于配置。Response 与 GUI 不包含 credential material |
| MCX-049 | P0 | 聚焦运行时展示 | User message 使用右对齐中性填充 surface，assistant response 在 canvas 左对齐；不显示 `你`/`助手` heading，但保留 accessible response name。Model-switch 与 lifecycle marker 使用统一克制 divider。Work 收起时只显示 canonical summary，即使 child failed 也不泄漏；展开 work 最大高度 320 CSS px，内部滚动。移除会话内 Find/上一条/下一条 toolbar 及 Cmd/Ctrl+F 拦截，保留浏览器原生查找 |
| MCX-050 | P0 | 终态 disclosure 滚动所有权 | 在 completed、failed、cancelled 或 paused turn 中展开 activity、evidence 或 work disclosure 时，不得调度 `scrollIntoView`，也不得重新开启 transcript auto-follow。用户将有界 work 区域滚到任意位置后，延迟详情渲染与父级重渲染在至少 1 秒内必须使 work 区域与 transcript 滚动偏移都保持在 2 CSS px 内。只有新的前台 turn、session 切换或用户显式点击 Jump to latest 才可恢复 transcript following |
| MCX-051 | P0 | Per-round 推理进度 | 每个 canonical model inference round 严格渲染一条 ellipsis 安全摘要 row。同一 round 的多段 reasoning chunk 只更新该 row；tool start/output/completion 不得提供 label。最新 running round 默认展开，在首条安全 summary 前使用本地化“处理中”，并拥有唯一克制的旋转状态 indicator。历史 completed/failed/cancelled round 保持可见且默认折叠；用户 disclosure override 在后续 update 中保持。Reduced motion 停止旋转，但保留 running label 与状态形状。最终 assistant summary 仍是 turn 最后一段实质内容 |
| MCX-052 | P0 | Desktop 根滚动隔离 | `html`、`body`、`#root` 与 application shell 不得成为滚动容器，也不得出现 WebView rubber-band 位移。在非滚动空白 chrome 上使用双指/wheel 时 document 偏移始终为零。Transcript、sidebar、settings 与有界 work-detail 区域仍各自可滚动，并在边界停止 overscroll chaining |
| MCX-053 | P0 | 原子 session 内容激活 | 选择已有 session 时，如存在缓存，必须同步激活该 session 的 canonical transcript，再通过 `GET /v1/sessions/{id}/messages` 刷新。Cache miss 只渲染一个有界的“正在加载会话”状态，且新会话 Hero/Setup surface 数量为零。目标 history 与 socket 未 ready 前 Composer 不得提交。快速 `A -> B -> A` 切换必须忽略过期 response，并从最多保留五个 session projection 的有界 MRU cache 立即恢复 A；refresh 失败时保留缓存内容，或展示唯一安全 retry state，且不得清空其他 session |
| MCX-054 | P0 | 有界终态会话恢复 | 选择或重新打开一个所在 session 至少包含 25,000 个 process event 的已完成 HaaS 会话时，必须直接渲染持久化 canonical transcript，不调用 HaaS event-page replay，不发送 replayed reasoning/model-stage delta，也不得阻塞 Manager `/v1/health`。在 packaged local fixture 中，history request 与 conversation socket 必须在 2 秒内 ready，首次加载后 Manager sidecar CPU 回落到 20% 以下；终态 bridge 持久化的临时 dedupe window 为空，同时保留 assistant text、activity、model stage、task outcome、evidence reference 与 canonical id。重启 packaged App 后再次选择同一会话，在不清理用户数据的条件下必须通过相同断言 |
| MCX-055 | P0 | Codex 对齐的 activity timeline | 展开 inference round 后，其所属的每个 canonical tool activity 严格按发生顺序渲染一个单行 row。Read/search/edit/command 使用语义 kind；MCP/dynamic tool 在可安全识别时保留 safe tool identity 与具体 normalized action，不得显示 `Call MCP tool` 等 transport 通用文案。Command row 可使用有界 command preview；非 command row 优先显示语义 action text，底层 command 只允许进入该 row 的 inline detail。Model stage 只提供私有 round correlation、排序、终态收敛与 aggregate usage；会话内不得渲染“执行详情”“模型调用 N”、model-call id、provider label、raw reasoning transcript 或 per-call token panel。点击 activity 只在当前 row 下直接展开，不出现侧栏或额外 evidence hierarchy。Raw MCP argument、host path、credential 与 provider-native payload 继续隐藏。缺少 safe tool identity 的历史记录使用本地化 kind fallback，不得恢复 transport 通用文案 |
| MCX-059 | P0 | 多轮 inference disclosure | 含三个 canonical model stage（completed summary、completed summary/tool、running summary/tool）的 fixture 按发生顺序渲染三条 inference row。前两条历史 row 默认折叠；只有最新 running row 展开并有动效。其 label 来自自己的 reasoning summary，绝不来自 tool summary。第四个 round 开始时，未被用户操作的上一 round 折叠、第四个展开，且更早 row identity 不变。Turn 完成后全部 motion 停止，live 与 REST replay 都保留折叠历史摘要 row，最终 assistant response 保持最后。无 summary 的 round 使用本地化 processing/completed fallback；中文请求不得暴露非中文或 tool-like fallback |
| MCX-056 | P0 | Final-answer 所有权 | HaaS phase-aware output 必须让 commentary 与 reasoning 保持在 assistant response 之外。ADK text 仅可作为“不提供 native phase fact”的 adapter fallback；当 native model-stage stream 负责分类时，不得直接发布为可见 assistant delta。`messagePhase=commentary` 只更新进度，`messagePhase=final_answer` 才进入回答正文。成功终态遇到 legacy 无 phase output 时，只把最后一次 model call 的最后一个 output item 提升为 `result`，更早内容继续视为 commentary。Failed/cancelled/incomplete turn 没有 `result` 时不得伪造 assistant response。Live completion、REST rehydration 与 restart 必须选出同一 final result，不得拼接步骤摘要。同一 invocation 已持久化的 assistant row 如果仍含 commentary 或非终态 outcome，权威 rehydration 必须原位替换其 response/activity/outcome 字段，同时保留 `ts`、`_managerRowId` 与 `_managerTurnId`；不得保留 stale text、追加重复 row 或改变 transcript 顺序 |
| MCX-057 | P0 | 语言对齐的 reasoning 进度 | 每条 inference row 只能消费 provider 提供的安全 reasoning summary。Codex turn instruction 必须要求 summary 跟随最新用户消息语言；中文输入因此产生简洁的简体中文意图/进展描述。Summary 描述操作目标或已观察到的进展，不得复述 literal tool name、command fragment、path、transport label 或 tool lifecycle 文案。GUI 不翻译 prose，也不从文本猜 subtype；若中文 turn 仍收到非中文 summary，该 row 回退到本地化 processing/completed 状态，不得暴露语言不匹配文本。Tool fact 只保留在所属 round 展开后的 chronological activity row |
| MCX-058 | P0 | 可执行的本地 turn 依赖 | 本地 HaaS turn 必须能跨过输出前的可重试 provider response，并完成内置 recall MCP 握手。重建 streaming retry 时必须保留 scalar connect/read/write/pool timeout，不能嵌套 httpx extension dictionary。Stateless recall endpoint 必须用 HTTP 202 且不带 JSON-RPC error 响应 `notifications/initialized`，随后 `tools/list`/`tools/call` 可继续执行。唯一的内置、只读、session-scoped `recall` tool 在 Codex 配置中显式 auto-approve；该例外不得扩展到任意 MCP server/tool。这些路径不得产生虚假的 MCP failure、未捕获 proxy exception 或缺失 canonical terminal event |
| MCX-060 | P0 | 无摘要 round 的语义标题 | Terminal model inference round 没有安全 reasoning summary 时，必须使用其最后一个 canonical activity 的语义 kind label 作为标题：command/read/search/edit/已识别 recall/通用 tool 分别映射为本地化产品文案。只要 terminal round 拥有 activity，就不得使用原始命令、任意 tool summary、transport 文案或通用终态 `Completed` / `已完成`。Terminal stage 同时没有安全摘要与 activity 时必须整体省略；若它只承载 final output，则 assistant response 是唯一可见 owner。Running 且无摘要时继续使用 MCX-051/057 规定的本地化“处理中”。Manager-owned recall activity 的安全摘要为 `Recall context` 或 `Recalled context` 时必须渲染 `Recall context` / `检索上下文`，不得把未翻译的 i18n key 输出到 DOM。该 fallback 使用语义 kind 而非 tool summary，因此继续遵守 MCX-059 的 reasoning 所有权。Running/failed/cancelled status 继续单独播报，展开后的 activity row 保留更丰富的安全标题与 status metadata。Live projection、持久 replay、双主题及 320/390/1440 宽度使用同一规则。 |
| MCX-061 | P0 | 有界 work 滚动样式 | 320 px 有界 inference/activity 区域在展开内容溢出时仍可独立使用滚轮、触控板与触摸滚动，但不得渲染持续可见的 scrollbar track、thumb 或预留 gutter。折叠到不再溢出时，内部滚动样式必须立即消失并收敛 offset，且不得改变 transcript scroll 或布局宽度。Round disclosure chevron 必须保持可见，不能与 scrollbar chrome 重叠。同一隐藏 scrollbar 规则同时适用于没有 round 的旧 work list，以及 macOS、Windows、Linux、浅色和深色主题。 |
| MCX-062 | P0 | 真实连续的运行中推理轮次 | 每次完成 tool batch 后发生的 native model inference 都必须拥有新的稳定 round；即使模型没有发送 reasoning-summary event、而是直接发起下一次 tool call，也不能继续合并到旧 round。当 parent turn 仍 active 但已观察到的 stage 都已终态时，projection 必须追加一个 stable provisional next round，在下一 canonical stage 到来时原位替换，并保持克制的 running 动效；活动任务不能只显示终态对勾。Provider 产生的 `reasoning_summary` 是首选单行标题；同一 round 中位于 tool 前的 agent message 必须归类为安全 commentary，并可作为该 round 标题。存在模型摘要时，tool name、command 或 tool lifecycle 文案不得覆盖摘要。没有安全摘要/commentary 时，仅最新 active round 使用本地化“处理中”；terminal round 继续使用 MCX-060 的语义 kind fallback。Live、持久 replay 与 restart 必须产生相同的 round 边界、顺序及 final-answer ownership。 |
| MCX-063 | P0 | 统一可编辑字段焦点 | 对话与 full-page route 中所有带边框的文本 input、textarea 和 select，都必须通过 `--color-field-focus-border` 使用与 Composer 一致的中性 focus border；不得渲染外部 outline、glow、tint 或 shadow。无边框可编辑控件通过 `:focus-within` 把 focus 指示交给所属 shell。Button、link、checkbox/radio/range 与自定义 interactive widget 继续使用可访问的 `--color-focus-ring`。Activity filter、Settings field、Connectors、Inbox、search、Composer、双主题、键盘/指针 focus、390/1440 px 与 200% zoom 使用同一 ownership 规则，且不得改变 geometry。 |
| MCX-065 | P0 | 已启用模型稳定列 | Settings 的已启用模型列表让 model identity、provider 和 action 对齐共享列。默认徽标与“设为默认”占用同一 action track，切换默认模型不改变 provider x 坐标或 row 宽度。双主题 390/760/1440 px 下，长本地化 label 只在各自列内 ellipsis。 |

MCX-054 刻意限定为小范围 recovery 修复：

1. 先增加失败的 bridge serialization 测试，证明 completed projection 会删除临时 dedupe
   window，同时不丢失持久化展示字段。
2. 增加失败的 Manager reconnect 测试：completed bridge 包含至少 25,000 个 synthetic process
   event/dedupe claim；断言不调用 HaaS event-page client，且不产生 replay frame。
3. 对终态 process replay 做短路，并通过既有持久化 readback 路径压缩 bridge；非终态 cursor
   recovery 与 live broadcast 行为保持不变。
4. 重新构建、打包并覆盖安装现有应用；重新打开同一个真实会话，以不记录消息正文的方式采集
   history/socket ready、health latency、sidecar CPU、保留的 transcript/tool 数量及重启后行为。

该变更只影响 Manager 内部 reconnect projection。ADK REST/SSE、`/v1/haas/*`、持久化 canonical
event、evidence authorization、session id 与 live turn 顺序均不改变。回滚只需撤销 terminal replay
guard 与 serialization compaction；旧 reader 已能接受空 dedupe array，因此不需要 store migration。

MCX-048 纠偏复用现有 `prefs.models` 作为显式 enabled-model 集合。`add_model` 对 matrix 与 custom id
都进行持久化，`remove_model` 删除任意一种；`get_settings.models` 返回该集合与 provider
executability 的交集。既有显式/custom 条目继续启用；静态 matrix 与 active default 不再隐式成为
成员。Settings 继续渲染 provider `suggested_models`，因此 disabled model 仍可发现。聚焦测试覆盖：
配置了多个 suggested model 的 provider、add/remove 持久化、不可用历史 default，以及本机现有
preference shape。Settings 加载不新增实时 provider 调用，API key 值不离开 SecretStore。

MCX-055 只在 adapter normalizer 内使用 native `toolName` 生成安全语义 `activityKind` 与有界 human
action，随后在 bridge persistence 与 REST rehydration 前丢弃 raw MCP server/tool identity。Normalizer
根据 allowlist 内的 MCP tool-name verb 归类到既有 `read`、`search`、`edit`、`command` 或 `tool`
kind；未知名称退化为本地化 kind label，不暴露 argument。`TurnWork` 删除 model-evidence disclosure
及其专用组件/CSS/文案。Model stage 继续作为私有 projection input，并生成 Manager-owned MCX-059
round row，不暴露 native id、ordinal、provider label、reasoning transcript 或 usage；同时保证
chronological tool ownership、terminal child-state convergence 与 aggregate usage 不回归。测试覆盖
混合 chronological activity、MCP list-resource normalization、非 command 的 command
preview 抑制、历史 generic-summary fallback，以及 unit/production-preview 中不存在任何 model-call UI。
缺少 `kind` 的历史 activity 必须能安全接收后续 generic terminal update，不得抛异常，并保留其安全
summary、以 `tool` 作为 kind fallback。

MCX-056 在 `StreamBridgeState` 增加唯一 final-response selector，并同时供 terminal event publication
与 Manager REST rehydration 使用。既有 ADK text accumulator 只作为没有 phase-aware model stage 的
adapter fallback。Manager 不再发布中间 HaaS `assistant_delta`；reasoning summary 继续更新所属
MCX-051 inference row，
terminal `assistant_message` 只发布选中的 result。测试覆盖 `commentary -> tool -> final_answer`、成功的
legacy 无 phase output，以及失败且只有 commentary 的 turn，并验证 live completion 与 restart 一致。
Reconnect 用例还必须从同一 invocation 的已持久化 stale assistant row 开始，证明 REST history 会替换其
权威 content/activity/outcome 字段，同时保留稳定 row identity 与 timestamp。

MCX-059 是基于既有持久化 `modelStages` 的 GUI projection delta；不新增 HaaS/ADK route、event、
credential 或 storage schema。Projector 对每个 stage 生成一个 Manager-owned round，只选择该 stage
最后一条非空 `reasoning_summary`，并关联既有 tool reference。`TurnWork` 对 live publication 与 REST
history 使用同一 round model，列表限制为 320 px、保留 per-round disclosure override，并且只有一个
running motion owner。聚焦 projection/component test 覆盖同一 stage 多段 summary、多 stage、summary
缺失或语言不匹配、tool label 隔离、terminal convergence 与 replay parity；production-preview fixture
在不采集 summary 内容的前提下验证 row count/order、默认展开、animation/reduced-motion 与最终回答顺序。

### 14.3 需求到用例追溯

| 需求 | 验收用例 |
|---|---|
| MCX-R01 | MCX-009、MCX-010、MCX-012 |
| MCX-R02 | MCX-001、MCX-002、MCX-003 |
| MCX-R03 | MCX-002、MCX-004、MCX-017 |
| MCX-R04 | MCX-005、MCX-006、MCX-007、MCX-008 |
| MCX-R05 | MCX-003、MCX-008、MCX-009、MCX-010、MCX-011、MCX-013、MCX-046、MCX-047、MCX-054 |
| MCX-R06 | MCX-014、MCX-015、MCX-016、MCX-018、MCX-019、MCX-063 |
| MCX-R07 | MCX-024、MCX-025、MCX-026 |
| MCX-R08 | MCX-011、MCX-012、MCX-018、MCX-024 |
| MCX-R09 | MCX-020、MCX-021、MCX-022 |
| MCX-R10 | MCX-013、MCX-023 |
| MCX-R11 | MCX-020、MCX-027 |
| MCX-R12 | MCX-025、MCX-028 |
| MCX-R13 | MCX-026 |
| MCX-R14 | MCX-009、MCX-029、MCX-032、MCX-038 |
| MCX-R15 | MCX-022、MCX-030、MCX-038 |
| MCX-R16 | MCX-011、MCX-012、MCX-031、MCX-039、MCX-047 |
| MCX-R17 | MCX-023、MCX-029、MCX-032、MCX-033、MCX-034、MCX-046、MCX-051、MCX-059、MCX-060、MCX-061、MCX-062 |
| MCX-R18 | MCX-016、MCX-018、MCX-019、MCX-035、MCX-036、MCX-037、MCX-045 |
| MCX-R19 | MCX-004、MCX-009、MCX-010、MCX-012、MCX-020、MCX-053 |

### 14.4 命令

实现时先执行聚焦命令，再执行仓库门禁：

```bash
cd manager/surfaces/gui
npm test -- --run
npm run build
npx playwright test \
  e2e/conversation-queue.spec.ts \
  e2e/conversation-product-turn.spec.ts \
  e2e/conversation-reconnect.spec.ts \
  e2e/conversation-accessibility.spec.ts \
  e2e/conversation-visual.spec.ts \
  e2e/conversation-performance.spec.ts \
  e2e/haas-activity.spec.ts \
  e2e/transcript-scroll.spec.ts

cd ../../../
make gui-preview-smoke
make pre-commit
make full-check
```

Packaged desktop 证据使用 `manager/packaging/build_dmg.sh` 与 `manager/packaging/smoke_packaged_app.sh`。真实 provider/HaaS E2E 保持 opt-in，并只使用 synthetic content。

## 15. 任务拆解与 SDD/TDD 顺序

| 顺序 | 任务 | 主要交付物 | TDD 准入 | 依赖 |
|---:|---|---|---|---|
| 1 | 固定 baseline | 状态/视觉/性能/失败 fixture | W0 failing cases | 无 |
| 2 | 定义 projection type | `model/` contract 与 invariant test | MCX-009/010/012 | 1 |
| 3 | 实现 projector/store | 确定性 reducer 与细粒度 selector | Golden live/replay test | 2 |
| 4 | 增加 token system | Semantic CSS token、主题、token lint | MCX-015/016/019 | 1 |
| 5 | 增加 component primitive | Button/disclosure/dock/status/focus foundation | keyboard/theme component test | 4 |
| 6 | 增加带 ACK command | Manager receipt、idempotency 与 reconciliation | MCX-001/002/003 | 2-3 |
| 7 | 增加 DraftStore | Per-session revisioned draft 与 attachment ref | MCX-004 | 6 |
| 8 | 增加 FollowUpQueue | 持久 queue command 与 policy | MCX-005-008 | 6-7 |
| 9 | 切换 interaction dock/composer | 统一 pending state 与稳定输入 | MCX-011/014/015 | 5-8 |
| 10 | 切换 product-turn component | Turn/work/reasoning/tool/response/completion/inspector；无 model-call card | MCX-009/012/013/023/029-034 | 3,5 |
| 11 | 增加 virtualization/稳定 live tail | 有界 history、first-delta response owner 与 scroll anchoring | MCX-020-022/030/038 | 10 |
| 11a | 增加原子 session activation | 有界 per-session MRU projection cache、显式 loading/error state、stale-request guard 与 cached refresh | MCX-004/010/020/053 | 3,11 |
| 12 | 增加 conversation navigation 与 semantic context | Search/turn navigation 与 typed context chip | MCX-027/028 | 10-11 |
| 13 | Responsive、hierarchy 与 motion polish | 全目标宽度/主题的 Focused Workbench | MCX-016/018/019/024/035-037/039/045 | 9-12 |
| 14 | 删除 legacy | 移除旧 owner、stream heuristic、native model-stage/evidence UI、过时 test 与 migration flag | MCX-026/030/031 | 2-13 |
| 14a | 增加 per-round inference disclosure | Manager-owned round projection、无摘要语义标题、仅摘要 row、current-round motion、隐藏内部 scrollbar 与历史默认折叠 | MCX-051/055/059/060/061 | 14 |
| 15 | Release review | 必需 review 与完整 gate | 全部用例 | 14a |

每个实现任务必须先编写映射到上表的失败 unit、contract 或 E2E assertion，再实现最小行为，最后在命名组件边界内重构。

### 15.1 参考开发排期

| 阶段 | 预估 | 准出里程碑 |
|---|---:|---|
| W0 Baseline | 1 天 | 可复现的行为、故障和视觉 baseline |
| W1 Foundations | 2 天 | Token/theme/accessibility primitive 通过 |
| W2 Projection shadow | 3-4 天 | Fixture divergence 为零且 selector 稳定 |
| W3 Input lifecycle | 4-5 天 | ACK/draft/queue/recovery 故障套件通过 |
| W4 Component cutover | 5-7 天 | Focused Workbench 行为与视觉 parity |
| W5 Performance | 2-3 天 | 10k DOM、Profiler 与 scroll gate 通过 |
| W6 Legacy zero 与 release review | 2 天 | 旧路径删除且全部 release gate 通过 |

单人预期工程周期为 19-24 个工作日，其中 W2-W4 预留两天风险缓冲。各 wave 可独立评审，但 W6 属于完成条件，不是可选清理。

### 15.2 当前 worktree 的纠偏实施计划

现有 ACK、draft、queue、theme、virtualization 与 performance 工作只在符合纠偏后的 product-turn 合同时保留。剩余纠偏按单人 5-8 个工作日估算：

| 顺序 | 工作项 | TDD 红灯用例 | 交付物 | 预估 |
|---:|---|---|---|---:|
| 1 | 增加 product-turn projection invariant 与 presentation selector | MCX-029/031 对当前 stage/action output 失败 | Projector/selector 与 golden fixture | 1-2 天 |
| 2 | 替换 heuristic stream ownership | MCX-030 对短流/tool-interleaved stream 失败 | 稳定 `AssistantResponse` 并删除 word gate | 1 天 |
| 3 | 替换 model-stage timeline | MCX-032-034 对八调用 fixture 失败 | 当前动作 summary、tool disclosure、Inspector-only telemetry | 1-2 天 |
| 4 | 修正 hierarchy、mode context、窄屏 layout 与 motion | MCX-035-037/039 出现 visual/semantic failure | 双主题、所有目标宽度的 tokenized EN/ZH UI | 1-2 天 |
| 5 | 删除被替代代码并执行 release review | MCX-026 与 grep/import 失败 | 不保留 legacy stage UI/selector/style/copy；完整 gate 证据 | 1 天 |

Task 1 完成后，Task 2-4 可拆成小提交。Task 5 是完成的必要条件。当前 model-stage 视觉层级与 word-threshold stream 行为不构成兼容承诺。

## 16. 组件影响分析

| 组件 | 影响 | 必须动作 | 兼容结论 |
|---|---|---|---|
| Manager GUI Performance | 新 projection/selector、virtual history 与 component boundary | 扩展 profiler、DOM、scroll 与 bundle gate | 保留现有 budget，要求更严格 |
| Conversation Store | Session switch 当前会在 history 返回前清空唯一 snapshot | 增加最多五个 session 的有界 MRU projection cache 与显式 activation result；仍保持单一 active writer | 仅内部 GUI state；不改变持久 schema 或协议 |
| Manager HaaS Sidecar Backend | 带 ACK command、queue persistence、normalized projection 与仅供 evidence 的 model-call correlation | 增加内部 command receipt；投影 product turn/work；停止把 model stage 当作 GUI row | HaaS public protocol 不变；内部 GUI snapshot 可随 packaged asset 原子升级 |
| Manager Delegation | Durable local send queue 变为可见可操作 | 保留 policy-application gate 与 accepted invocation 语义 | 澄清现有 delegation 合同，不削弱 |
| Event Log & SSE | 提供 canonical correlation 与 replay fact | 复用现有 event/turn/invocation/tool id；进入 GUI projection 前分类 user-visible assistant text | 初始 cutover 不需要新 public event；不向 northbound 泄漏 model-native event |
| Session Runtime | Terminal 与 idempotency 事实源 | 保留 acceptance 与 terminal convergence | lifecycle 语义不变 |
| Manager Product Identity | Focused Workbench 成为 OpenHarness 主要 conversation 气质 | 保留 local-first/no-login 与 desktop-native 行为 | 不增加 identity 或 cloud 依赖 |
| Security Boundary | Draft/queue 与 evidence 包含敏感用户上下文 | local-only draft storage、不含内容的诊断、现有 evidence scope | 保持 Secretless 保证 |
| Artifact Store | Completion/status surface 链接产出文件 | 复用安全 metadata 与现有 viewer | Artifact URL/path 合同不变 |
| Container/adapter/model/MCP | 除回归外无影响 | 执行相关 full gate | runtime/platform 合同不变 |

## 17. 风险、缓解与延后项

| 风险 | 缓解措施 |
|---|---|
| Projection rewrite 改变排序 | 视觉切换前使用 shadow projection 和 golden live/replay comparison |
| 双 owner 产生竞态 | 每次 cutover 后新 store 独占写入；shadow mode 只读 |
| Queue 改变执行语义 | 先持久化 command receipt；对 stop/restart/duplicate 路径做故障注入 |
| Virtualization 破坏 selection/scroll | 分离 live tail、selection-aware anchor、确定性 10k fixture |
| Component 抽象膨胀成通用框架 | 只导出当前 OpenHarness surface 必需组件；本变更不设计 plugin API |
| Token 迁移产生大面积视觉抖动 | 逐 component 迁移并成对保留 light/dark screenshot |
| 删除 legacy 破坏旧持久历史 | 只在旧 durable record 仍需要时保留一个 data migration adapter；旧 renderer 必须删除 |
| 不同 harness 的 answer/reasoning 分类不同 | 在 transport/projection boundary 归一化；未知安全 assistant text 归入 answer，不从内容猜测 |
| 折叠 work 隐藏可操作 failure | Active interaction 与第一个可操作 failure 保持在默认折叠的成功详情之外 |
| 删除 stage UI 降低诊断能力 | Model-call correlation 与 per-call usage 保留在内部 diagnostics；有界 raw tool evidence 仅进入 inline activity detail，不得进入 model-call 会话面板 |
| Stable answer DOM 与 virtualization 冲突 | Active turn 留在非虚拟 live tail，完成时原子封存进 history |
| 缓存 history 过期或内存无界增长 | Cache 只负责即时展示，始终向权威 history 刷新；最多保留五个 session projection，并继续使用 request generation 丢弃过期 response |

以下事项推迟到独立批准的 spec：

- 跨设备/云端草稿同步；
- 任意 backend steering 语义；
- workflow graph 与多 pane conversation editing；
- 单一 compact professional 默认之外的用户可选 density theme；以及
- 把 React AI component kit 对外提取为独立 package。

## 18. 评审与发布门禁

只有 spec review 在上下文一致性、状态归属、接口完整性、恢复、兼容、安全、无障碍、性能、可测试性、迁移与 legacy 删除方面达到零 blocker，本规格才可进入实现。

实现完成必须按顺序满足：

1. 所有映射的 P0/P1/P2 验收用例；
2. `code-review`，每个 finding 已修复或明确关闭；
3. `brooks-review` 架构/可维护性评审；
4. `brooks-test` 测试质量评审；
5. browser production-preview 与 packaged Tauri smoke 证据；
6. `make pre-commit`，包括 secret scan；以及
7. `make full-check`。

W6 legacy-zero 证据缺失时，不得宣称本变更完成。

#### 切换后的能力保留规则

审核拒绝必须作为紧凑的可操作工作详情保留，即使成功工作已折叠。只有原权限事件明确
允许时才提供精确操作的一次性放行。Inspector 保留审批来源、常驻规则说明和隐私过滤
计数。历史失败不得重试较新的任务。所有迁移控件使用语义字号和颜色 token。完成耗时
只能来自任务计时，禁止对可能重叠的工具耗时求和。仅当本轮所有持久化 assistant 计费
记录均有权威 usage 时才聚合展示；部分或缺失计费数据省略。Manager 展示身份 sidecar
在进入 provider 前剥离，并由实时流与 REST 回读共用。

客户端在 10 秒未收到 ACK 后执行有界回执对账，即使连接仍开着。已接受任务不因 ACK
写入失败而丢失执行。上一会话的迟到 ACK 不得清空当前会话草稿。排队请求保持接受时
选择的模型直到调度。窄窗口初始收起次要面板，不继承桌面展开偏好；显式面板操作仍
可用。展开工作详情与打开证据一样暂停跟随。error 事件投影为失败，直到权威终态或
回读更新，不得因 transport turn 结束被误标完成。

重连核对是 Composer 状态迁移，不只是 transport callback。对应的 accepted/duplicate receipt 必须解除 `acceptanceUnknown`，且仅在 draft revision 仍相同时清除可见草稿；对应的 `command_not_found` 解除阻塞但保留当前草稿，并展示安全拒绝信息。更新版本草稿和其他 session 绝不能被清除。删除活动 session 时，在切换 scope 前通过同一 draft owner 发出一次性 discard signal，避免 cleanup effect 再次保存已删除文字或附件。


### v2 最终收尾合同

剩余 W4–W6 沿用现有组件树与存储，不新增替代 renderer 或公共协议。交付包含
MCX-004/009/010/024/026/027/028，不限于 C0–C4 修订子集。

- **类型化 transport 边界**：WebSocket JSON 先从 `unknown` 解码为可辨别 event union，再调用
  handler。校验 GUI 消费的字段与嵌套集合；已知畸形帧和未知类型只增加无内容计数器，不进入
  React、日志或伪造终态。未来可选字段保持加法兼容。原生工具参数在此边界保持不透明 record。
  Decoder 必须接受 Manager 的结构化 `delegated` 归属信息，以及 ready outcome、rejected ACK
  disposition、恢复草稿的可选项、审批 standing target 缺省时的显式 null。这些均为生产端已有
  形状，不能误判为畸形帧。MCX-013/026 验证须将这些帧送入 decoder，并在 production GUI
  打开委派命令详情；错误字段类型仍须拒绝。本次修正不改变 ADK/HaaS API、事件生产端、权限、
  持久化或日志合同。
- **导航**：MCX-027 定义的 conversation-local Find 与上一条/下一条控件已由 MCX-049 退役。
  Cmd/Ctrl+F 交由 browser/WebView 原生处理，global session search 继续作为产品级搜索入口。
  用户在虚拟历史行中选择文字时，只将该行固定在 virtual range，直到 browser selection
  收起或离开 timeline；滚动不能卸载 selection owner，额外固定行仍须满足 MCX-020 的 200 行上限。
- **语义上下文**：用户行与 Composer 共享已选 skill、暂存 file、引用 session 的类型化引用。
  标签不包含 provider framing；复制使用可读标签；打开只委托现有已授权文件/会话/skill 动作。
  引用缺失或权限撤销时仍可读并标记不可用。context、model、mode 随草稿恢复、拒绝、队列编辑及
  reload 保留，不在 renderer 解析前缀；历史 force-run 仅在历史边界规范化一次。
- **持久化**：IndexedDB 写入必须 transaction commit 后才成功；abort/error 保留内存草稿并
  提示持久化失败，生产路径不允许仅内存成功降级。删除会话只清理其草稿与暂存上下文；仅清理
  超过 30 天的孤儿 scope。队列重启保留顺序，不确定 dispatch 暂停而不重放已接受任务；
  显式 Resume queue 动作继续排队任务。
- **Readback**：会话加载与终态读取结果只可写入发起请求的 session/request generation，
  旧响应不能覆盖新选择的会话。
- **Legacy 门禁**：无未类型化 WS payload、renderer 身份推断、旧 live buffer owner、重复
  status/step 列表、旧 renderer import、词数阈值或迁移 flag。仅保留一处旧持久记录迁移 adapter。

实施顺序：transport/identity → navigation/context → durability/recovery → parity/review/package。
每个切片先写失败合同测试，随后 unit/build 和生产浏览器回归。浏览器 fixture 覆盖真实
IndexedDB 与重启；打包检查覆盖可见且可交互的窗口、已有历史、导航和双主题。外部 provider/
平台不可用必须与通过区分。ADK、HaaS 公共事件、artifact 权限及容器变体合同均不改变。

### 项目工作台边界

[Manager 项目工作台体验](../manager-project-workspace-experience/README.zh-CN.md) 持有 project
分组、workspace/execution-target draft context、Git branch control 与原生 window chrome。本组件
继续持有唯一 chronological tool-activity projection、短暂 current-action summary 与 inline ActivityInspector。Project surface
可打开该 owner，但不得再渲染第二个 command detail surface。Accepted project/workspace/endpoint
identity 是 conversation 输入，不能从 transcript 内容推断。

### MCX-064：工作目录初始化失败恢复

必需目录无效是会话初始化失败，不是任务已完成。Manager 在 error.data 上增加
`code=workspace_unavailable`、`retryable=false`、`recoveryAction=restore_workspace`，
以 1008 关闭连接。项目绑定失败同样携带结构化不可自动重试的初始化错误。GUI 停止
该会话自动重连，正文之外只显示一条本地化提示，保留草稿和历史，不生成成功状态或
重复重试消息。普通网络故障保留有界重连。提示提供恢复原目录后的主动重新检查、
新项目目录选择器两个入口。不同目录创建新会话，不重定向已接受绑定。ready 或导航
清除初始化阻塞，阻塞期间防止发送和执行重试。普通任务错误在乐观清理/turn_done 后
仍保持失败。

任务及验收：生产端/解码器增量协议、会话重连门禁及提示；验证重复失败经过重连周期后
只有一条提示且不新增连接/成功页脚、主动检查只连接一次、导航和普通网络故障恢复。
双主题、键盘及窄屏复用中性提示/按钮 token。仅 Manager 本地增量变更，不改 ADK/HaaS
API、事件、adapter、model/MCP 策略、容器和 secret 边界，错误无需携带原始路径。
