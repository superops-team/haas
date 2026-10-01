# OpenHarness Manager 设计规范

[English](DESIGN.md) | **简体中文**

供 coding agent 与开发者修改 Manager UI 时遵循的统一规范入口。
范围：桌面与浏览器 Manager；对话界面是首批迁移范围。
这是设计合同，不代表现有全部页面已经达标。

## 1. 规范职责与产品气质

修改 UI 前阅读本文件与受影响组件 spec。根 `AGENTS.md` 负责研发、安全和兼容性约束；
[对话规格](specs/manager-conversation-experience/README.zh-CN.md)负责生命周期、命令、数据合同和验收用例；
[性能规格](specs/manager-gui-performance/README.zh-CN.md)负责 GUI 性能要求；
[styles.css](manager/surfaces/gui/src/styles.css)负责运行时 token 值。共享规则变化时同步更新这些来源，
不得另起局部设计体系，也不得用视觉规则覆盖协议合同。

优先级：**稳定性 → 设计一致性 → 易用性 → 任务管理体验对齐 → 功能广度**。
OpenHarness 是安静、紧凑、适合长时间 Agent 任务的工作台。用户应能迅速找到自己的请求、
当前工作、待决策事项和结果。颜色、动效、卡片和计量信息不能争夺这条阅读主线。

用户可见产品术语将可选择的专用 persona 统一称为：英文 **AI Assistant**，中文 **AI助手**。
该产品对象不得继续暴露旧的泛化术语 `Coworker` 或 `同事`。稳定实现标识（`coworker`、
`.coworker/config.toml`、API field 与 package name）、用户自定义助手名称，以及确实指向人类同事
的文案保持不变；只在 UI presentation boundary 统一翻译。

## 2. AI 产品对象

以下定义产品职责，不要求每个名词都增加一张表或一套框架。复用现有类型模型和组件边界。

| 对象 | 职责 | 展示规则 |
|---|---|---|
| Session / 任务上下文 | 工作区、configured harness、模型、策略、历史 | 稳定上下文；切换面板不停止执行 |
| Product turn | 一次已接受请求及其工作和结果 | 一个顶层单元；model round 有界地留在其 work region 内 |
| Inference round | 一次 canonical model stage 及其安全进度摘要 | 一条紧凑 row；绝不暴露 model-call id、ordinal、provider label 或 raw reasoning |
| Work segment | 已接受引导或决策前后的有序工具动作 | Inline detail 归属 inference round；不得按 prose 虚构阶段 |
| Assistant response | 从首段 delta 到封存的用户可见答复 | 稳定 identity 和 DOM owner；不按长度阈值迁移文本 |
| Activity / tool | 类型化动作、安全摘要、状态、证据引用 | 先摘要；详情与模型证据按需 inline 展开 |
| Pending interaction | 需要用户处理的审批或结构化输入 | 一个稳定 dock、明确决策标识、作用范围和结果 |
| Input intent | 文本、附件/上下文引用、模型/模式、投递意图 | 从准入、队列、执行到历史保持已接受输入完整 |
| Artifact / completion | 交付物引用与权威终态 | 可打开的结果和事实 footer；不得虚构成功或计数 |

草稿是可编辑的本地意图；已接受队列项是服务端负责的工作；引导只有在后端明确支持时才能改变当前工作。
三者不能共用含义模糊的“发送”。不支持的能力按已有 availability 合同隐藏或解释，UI 不得模拟
steering、暂停或投递成功。

## 3. AI 交互不变量

| 规则 | 必须满足的行为 | 现有验收合同 |
|---|---|---|
| AI-01 以产品回合为中心 | 八次 model call 仍只生成一个 turn 与稳定答复；八条摘要只作为 work 内有界 row，不成为同级 card | MCX-029/030/032/059 |
| AI-02 分离事实、命令与本地状态 | ACK 只代表已接受；终态事实才能确认完成。准入未知时先核对原 identity 再重试 | MCX-001/002/003/009/010 |
| AI-03 保全用户意图 | 草稿按 session 隔离；只清理被接受版本；队列变更不丢上下文、不重复执行 | MCX-004/005/006/007/008/028 |
| AI-04 决策只有一个 owner | 冲突动作启用前先恢复待决策项；已处理后保留结果，不复制活动表单 | MCX-011/014/015 |
| AI-05 证据渐进披露 | 摘要、安全预览、授权详情、产物职责分明；详情缺失或过期应有解释 | MCX-017/025/033 |
| AI-06 如实呈现状态 | 状态与控件共用生命周期解释；缺失 usage 不展示；未知进度不伪装为 0% 或成功 | MCX-012/013/023/031/034 |
| AI-07 用户拥有阅读位置 | 流式 identity/选择稳定；虚拟历史与稳定 live tail 分开；跳到最新恢复跟随 | MCX-020/022/027/030/038 |
| AI-08 视图寿命不等于任务寿命 | session 数据长于面板；细粒度订阅隔离 live text 与草稿、导航、关闭面板 | MCX-004/010/021/024 |
| AI-09 克制且可访问的反馈 | 一个动效 owner 和有意义的状态播报；双主题、键盘路径均可用 | MCX-014/015/016/018/019/035/037/039 |

这些是必须满足的合同。实现状态由验收证据确定，本映射不表示用例已通过。

## 4. 数据流与 React 边界

```text
HaaS 事实 + Manager 命令回执
  → Manager transport 校验 / 归一化
  → session 投影与基于 identity 的核对恢复
  → canonical presentation selector + 细粒度组件 props
  → Timeline / Work / Inline Detail / Response / Decision Dock / Queue / Composer

用户动作 → 类型化命令意图 → 准入 → 权威投影
本地 UI 状态 → 草稿、展开、选择、焦点、滚动锚点
```

HaaS HTTP/SSE 事件描述事实。不得将 UI row、CSS 状态、卡片布局或 ZCode 私有协议作为新的
HaaS 公共 API 暴露。遵循现有 Manager transport 边界。

- 展示组件接收类型化数据与回调，不开 socket、不解析 harness payload、不从文字猜生命周期、不提前抓取诊断证据。
- 只有一个 projection owner 应用事实；乐观反馈不能成为执行真相。重放/回执恢复保留原 identity，不盲目重发副作用。
- 通过仓库自有 hook 提供稳定 external-store snapshot 和细粒度订阅；高频 live text 与 metadata、草稿、展开状态分离。
- 使用稳定 turn/response/activity key，不用数组下标、内容、时间戳或当前状态作 key。Memoization 需要稳定输入和实测收益。
- 按 spec 合并流式发布并在终态 flush；不能每个 token 重解析整个 transcript，也不能挂载隐藏的证据读取器。
- 分离历史虚拟化与活动尾部几何；程序测量不能被误判为用户选择跟随底部。
- 关闭面板释放视图订阅，绝不隐式取消执行。新增订阅层必须有具体使用方和明确清理职责。

## 5. 可复用 AI 组件

以下合同适用于现有组件，不要求额外增加包装层。

| 组件 / 当前落点 | 接收 | 负责 | 不得负责 |
|---|---|---|---|
| ConversationTimeline / ConversationView | 已投影 turn、live tail | 阅读顺序、锚点、有界历史 | transport 或 runtime 生命周期 |
| TurnWork | 工作事实、inference round、presentation、展开状态 | 每轮一条安全摘要、latest-running 动效、类型化 tool/evidence 展开 | native model-call label、raw reasoning transcript 或独立猜运行状态 |
| AssistantResponse / MessageContent | 稳定 response 与内容 | 渐进可读答复 | 答复文本迁移 |
| PendingInteractionDock | 类型化 interaction 与回调 | 单一决策区和焦点 | 影子审批状态 |
| ConversationComposer / ContextChips | 草稿 scope、availability、context | 编辑、准入反馈、克制控件 | 将 ACK 当成完成 |
| FollowUpQueue | 已接受队列项和允许动作 | 顺序、编辑/派发意图、恢复反馈 | 不可恢复的乐观删除 |
| ActivityInspector / ModelEvidenceInspector | turn/activity identity、安全预览/引用 | inline 懒加载证据及 loading/error/expired 状态 | 侧栏 ownership 或 timeline 标签/日志中的 raw payload |
| TurnCompletion | 权威终态与实测事实 | 一个事实 footer | 按计时器判断成功或虚构 usage |

实现位于 [conversation/components](manager/surfaces/gui/src/conversation/components)。工具布局统一为
kind/icon、主要摘要、可选次级详情、状态、展开入口。命令/文件/diff 的专用 renderer 复用同一外壳；
未知类型使用安全通用摘要。展开命令时，即使完整证据正在加载、缺失或过期，也应展示已有脱敏预览。
不得通过倾倒原始参数补齐预览。

修改共享组件时，明确适用的 loading、empty、error、disabled、recovery 行为，以及可访问名称、焦点和双主题。
先复用现有组件；只有能消除已出现的重复职责时才提取抽象。

## 6. 布局与控件层级

- 阅读顺序：**请求 → 紧凑工作过程 → 答复 → 结果引用**。后台状态、计划详情、遥测和证据保持次级。
  一个事实只有一个展开 owner；其他位置的快捷入口打开该 owner，不复制内容。
- 用户文本、工作、答复、导航与 Composer 对齐同一 conversation measure。导航和决策 dock 占据正常文档流槽位。
  浮动控件不得遮挡 transcript 行、详情或 Composer；菜单必须处理视口边界、关闭与焦点归还。
- 工作默认紧凑折叠，用户展开偏好在更新间保持；失败和决策应直接可发现，不需展开全部成功工具。
- 窄屏先收起次要 chrome。长本地化标签换行；路径/命令可在详情内部滚动。截断不得隐藏唯一可用动作。
- 搜索 overlay 使用一个带语义圆角的 input shell。键盘焦点只改变其中性 border token，不能在 shell 内绘制矩形品牌色 outline；active result row 使用中性 chrome，不整行染品牌色。
- macOS overlay window 的原生 traffic light 与相邻 sidebar/panel 展开、折叠控件共用一条
  titlebar 中心线。Tauri/tao 的 `traffic_light_position(..., y)` 是 AppKit container
  inset，既不是原生按钮中心，也不是模拟圆点的 CSS `top`；中心必须从原生 button frame
  在 packaged app 中实测。当前 pin 的 Tauri/tao 与 macOS 组合下，`y=24` 得到 WebView 相对
  22 CSS px 的 control 中心，因此 12 px browser 模拟圆点使用 `top: 16px`。wordmark 与 title
  在同一个 44 px 条带内做光学对齐，但不能反向定义交互控件中心。浏览器几何只作为快速
  回归门禁；packaged macOS 截图是最终视觉合同，并覆盖任何推算 offset。
- 用户主动折叠 sidebar 后，该状态必须稳定。titlebar 上的显式展开按钮只响应点击/键盘；
  pointer 停留在原折叠按钮坐标时不得立即触发 peek 或重新展开。Hover peek 只归属于
  titlebar 下方的窄左边缘发现区域。
- 每个局部动作组最多一个填充式主按钮。模型中的 `primaryAction` 表示行为优先级，不代表必须着色。
- Composer 尾部控件组成一个禁止换行的 cluster，顺序固定为 `model -> microphone ->
  Send/Stop`。它们在所有支持宽度以及 recording/running 状态下始终可见。只有 model control
  可以收缩并显示 ellipsis；microphone 与 lifecycle-action hit target 保持固定，相邻 peer 只使用
  一个 compact spacing token，usage/secondary control 必须先让位。
- User 与 assistant message 不显示可见 speaker heading：user message 在中性填充 surface 上右对齐，
  assistant response 在 canvas 左对齐；辅助技术仍可获得 accessible name。Conversation 不再拥有
  Find/上一条/下一条 toolbar，也不拦截 Cmd/Ctrl+F；保留 browser/WebView 原生查找。
- Composer model menu 是 capability surface，不是 catalog：只渲染 backend 返回的 usable-model
  list，绝不重新注入不可用的 current/default model。完整发现与 credential 配置留在 Settings。
- Settings 的已启用模型列表为 model identity、provider 和 row action 使用稳定列。默认徽标与
  “设为默认”占用同一个 action 槽，因此切换默认模型不能推动 provider 列；长 model/provider
  label 只在各自列内 ellipsis。
- 桌面信息架构以 Project 为先：Project 聚合 conversations 与一个或多个可执行
  workspace binding。展开 Project 不等于切换 conversation。Draft context 将 Project、Work
  location 与 Git branch 作为克制的同级控件；accepted work 冻结这些身份，后续变化创建新
  conversation，不能静默改写历史目标。
- 三个 draft target 控件必须组成一个在视觉上衔接于 Composer 背后的 context shelf，而不是
  三个悬浮 pill 或一行游离 metadata。Shelf 从 Composer 两侧各内收 16 CSS px，使用中性 chrome
  surface，只露出外侧顶部圆角，并由前景 Composer surface 覆盖 12 CSS px。Project、Work location 与 Git branch
  共用一条 30 px 中心线、compact 同级间距、14 px 单一线宽 icon 与 UI 字号。默认态没有各自的
  border/fill；neutral hover/focus/open feedback 可以显示一个 compact control surface。本机目标
  使用设备图标，不得使用代码括号。三个控件保持单行；长 label 只做 ellipsis，不把同级控件推出
  shelf，也不改变 shelf/Composer geometry。非 Git workspace 直接省略 Branch，不预留虚假的第三项。
- Project navigation 使用 progressive disclosure。Section header 保留纯 `+` 创建项目，并在
  hover/focus 时显示唯一 organization menu。每个 project row 使用 primary disclosure button 与
  sibling 新建会话/`...` action；conversation row 使用 primary selection button 与 sibling Pin/Archive
  action。预留的尾部区域避免 layout shift。Project hover card 可提供 Pin/新建会话 shortcut，
  conversation card 保持 informational。新建会话把 fresh draft 绑定到目标 Project 的 primary
  workspace/default execution target；项目资料编辑只有 Project `...` menu 一个 owner。所有
  card/menu 共用同一 collision-aware overlay 家族，
  使用中性 surface，保留键盘路径，出现时不改变 row geometry、selection 或 expansion。
- Project 与 conversation preview 是同一个 hover controller 的互斥状态。进入不同 anchor 时，
  必须先关闭旧 preview，再开始新的 300 ms dwell；延迟关闭可以保留通往当前 card 的 corridor，
  但不能让旧 card 与下一个 card 共存。Hover metadata 与 row action 占用同一个预留尾部 grid
  cell，只切换 opacity/visibility，不切换 `display` 或 row width。
- Project ordering 是基于唯一 authoritative projection 的 pure selector。Project/Conversation row
  是使用 stable callback 的 memoized leaf；一个 controller 持有 mutation，一个 overlay host 持有
  hover timer、viewport positioning、dismissal 与 focus return。
- 初始导航必须区分“project projection 尚未完成”和“权威 project 列表为空”。Pending 期间只显示
  一套固定、克制的 project skeleton，不得闪现 legacy flat conversation hierarchy；后续后台刷新
  在新结果 settle 前保留当前 projection。
- Project management 默认 non-destructive。移除 project 仅表示隐藏/archive sidebar record，
  必须有明确 restore 路径；不删除 file、worktree、conversation、transcript、artifact 或 accepted
  binding。Pinned project 始终高于已选 project sort mode；选择 project 绝不对其重排序。
  Active/running conversation 可在所属 project 内继续保持优先级。
- Command work 使用紧凑 chronological row list。折叠 command 严格只有一条视觉行：model 中
  保留完整脱敏值，visible label 按可用宽度显示尾部 ellipsis，并移除冗余分类副标题；terminal
  state 与 disclosure 固定在尾部。点击后只在该 row 正下方展开一个 inline Shell panel，安全换行
  展示完整 `$ command` 与 bounded output；不得再打开第二个 Inspector owner，也不得同时重复
  full evidence 与 preview。
- 所有折叠 activity kind 共用同一个单行 row shell；safe summary 只做 ellipsis，status/disclosure
  固定在尾部，category metadata 不生成第二行。Work 收起时不显示任何 child row，包括失败项。
  展开 work 最大高度 320 CSS px，超出后内部滚动；完整详情仍由唯一 inline inspector 提供。
- 终态历史 disclosure 不得抢占滚动所有权。展开 work、reasoning、activity 或 evidence 时不调用
  `scrollIntoView`；用户滚动有界 work 区域或 transcript 后，延迟详情渲染必须保持当前位置。
  只有显式 Jump to latest、session 切换或新的前台 turn 才可恢复 transcript following。
- 活跃 work 为每个 canonical inference round 渲染一条单行 ellipsis 的安全摘要 row。同一 round 的
  多段 reasoning chunk 只更新所属 row；tool lifecycle 不得提供该 label。历史终态 row 默认折叠，
  最新 running row 默认展开并拥有唯一旋转状态 indicator；Reduced motion 下 indicator 静止。Native
  model id/ordinal 与 raw reasoning 继续隐藏。终态 failure/outcome summary 位于有界 round/activity list
  之后，assistant response 仍是最后一段实质内容。
- Document 根节点固定且不可滚动。在空白 chrome 上的双指/wheel 手势不得移动或 rubber-band
  整个 WebView；只有明确的滚动容器消费手势，并在自身边界阻断 overscroll chaining。
- 所有 full-page route 共用原生 title drag 合同：route title 与非交互 top chrome 可拖拽；button、
  link、form control、menu、可选文本与 scrollbar 保持 no-drag。双击 route drag region 只执行一次
  maximize/restore。
- 44 px desktop chrome 是原生交互面，不是装饰 padding。空白 chrome 可拖拽，interactive
  control 是 no-drag island；双击 draggable chrome 只触发一次平台 maximize/restore。

| 状态 | Composer / 控制区表现 |
|---|---|
| 空闲 / 终态 | 一个发送动作；不可用时禁用；恢复遵循 spec |
| 运行中，草稿为空 | 支持时显示中性“暂停”文字；中性 32 px“停止”图标 |
| 运行中，已有草稿 | 保留运行控件，另提供明确命名的“加入追问队列”动作 |
| 暂停中 / 恢复中 / 停止中 / 提交中 / 核对恢复中 | 普通次级状态文字；不重复显示生命周期按钮 |
| 已暂停 | “继续”和“结束任务”使用同族轻量文字控件 |
| 等待决策 | 决策 dock 负责处理；Composer 不提供冲突发送 |
| 断线 | 保留可辨识控件；无法投递的命令禁用 |

停止使用方块图形、本地化可访问名称和 tooltip、可见键盘焦点。Hover 只出现中性底色；结束任务可在 hover
时使用 danger 文字。普通处理状态不用 pill 或类似禁用按钮的外形。同一工作已在 timeline 指示时，不再增加 spinner。

## 7. Token、主题与动效

采用 HaaS 语义 token，不复制 ZCode 名称或色值。CSS 声明负责运行时数值，以下角色约束其使用。

| 角色 | Token / 尺度 | 规则 |
|---|---|---|
| 结构 | `--color-chrome`、`--color-canvas` | 导航退后，对话为主 |
| 内容 / 浮层 | `--color-surface`、`--color-surface-raised`、`--color-popover` | 区分结构、内容与浮层；避免同级卡片嵌套 |
| 文字 | `--color-text-primary/secondary/tertiary/inverse` | 必要状态与动作始终可读 |
| 字体 | `--text-title/heading/body/ui/navigation/mono/caption` | 20/16/14/13/12/12/11 px 角色；navigation 用于高密度侧栏，caption 只承载 metadata |
| 间距 | `--space-1` 至 `--space-6` | 4 px 节奏；组间距至少为组内间距两倍 |
| 圆角 | `--radius-surface/control/compact` | 12/8/6 px；内部可见容器圆角不应更大 |
| 强调 / 反馈 | `--color-accent`、success/warning/danger 角色 | 少量强调或真实语义状态 |
| 焦点 | `--color-focus-ring`、`--color-field-focus-border` | 文本字段与 Composer 共用单层中性边框，无外圈、光晕、染色或阴影；离散控件保留可访问 focus ring |
| 动效 | `--motion-fast`、`--motion-work` | 短反馈；适用时仅一个紧凑工作指示 |

新增或已迁移对话组件不得添加 raw color、任意字号/圆角/阴影或新 alias 家族。共享 primitive 已要求的 alias
可复用，但不能扩大兼容层。新增共享 token 必须具备双主题值并更新 spec。不得通过改变 root 字号缩小 UI。
移动端可编辑文字使用 spec 的 16 px 兼容规则，不新增通用字体层级。

先用字体和间距建立层级，再考虑边框与填充。普通控件保持中性，品牌色只用于有意义的强调。
避免渐变、输入框光晕、巨型处理按钮、重复 badge 和普通活动的状态色整卡染色。

对话与 full-page route 的可编辑文本控件共用一种 focus 处理。带边框的 input、textarea 或
select 聚焦时只把 idle border 替换为 `--color-field-focus-border`，不增加 outline 或 shadow；
无边框控件通过 `:focus-within` 把同一反馈交给所属 shell。品牌蓝 focus ring 只用于 button、
link、checkbox、radio 与自定义 interactive widget 等离散控件的键盘焦点。

项目导航属于高密度控制面，不是正文阅读区。项目名和会话标题统一使用 12 px、1.35 行高的
`--text-navigation`；相对时间继续使用 11 px caption 与等宽数字。活动项目最多提升到 500
字重，不使用 600/700；选中感主要由颜色和既有低对比行填充承担。行点击区域仍不得低于
28 CSS px，不能通过缩小 hit target 换取紧凑感。
Section label 使用 11 px caption 与 500 字重。非活动 conversation label 使用 secondary 文字色
和 400 字重；只有当前选中的 conversation 可以使用 primary 文字色和 500 字重。

双主题保持相同层级、禁用语义与焦点表现。普通文字对比度至少 4.5:1，大文字 3:1，必要 UI/焦点指示与相邻
表面至少 3:1。控件命中区至少 24×24 CSS px；紧凑运行控件为 32 px。

动效遵守 spec 的状态 owner 矩阵：内容出现前的紧凑指示、可选答复光标、短展开反馈、克制终态过渡。
Token 更新不得驱动布局动效。避免 `transition: all`、弹簧动效和主题整体拖影。Reduced motion 下保留完整静态含义。
仅一个 polite live region 播报阶段变化，不逐 token 播报。

## 8. 修改与评审流程

1. 确定受影响产品对象、状态迁移和验收 ID。行为或共享设计规则变化时，先更新组件 spec，评审通过后实现。
2. 复用组件/token，保持一个状态 owner 和活动 renderer。按完整纵向链路迁移；切换时删除旧 renderer、CSS、
   双写和 flag（MCX-026）。
3. 行为变化先复现问题并验证用户结果；局部样式复用生命周期测试，并检查几何、主题、焦点和可读性，避免镜像 CSS 的测试。
4. 受影响状态验证双主题 390/1440 px；布局变化增加 320 px 和 200% zoom。按影响面覆盖长 EN/ZH 文本、键盘及 reduced motion。
   完整 fixture 集仍以 MCX-014 至 MCX-039 为准。
5. 执行相关 GUI unit/build 和 production-preview 检查；preview 前重新构建 `dist`。执行 code-review、brooks-review、
   brooks-test 和 pre-commit。最终合入/发布要求 `make full-check`；桌面一致性必须有实际打包验证。
6. 明确报告验证范围和缺失证据。浏览器 mock 证明展示，不能证明 provider 恢复或 Tauri 原生行为。不得盲目更新截图接受回归。
   临时观察和报告不进入跟踪文档。

约束通过四层落地：GUI `AGENTS.md` 指向本规范；共享组件固化默认行为；行为测试检查状态合同；主题/几何评审检查实际效果。
本次增加规范入口，不表示已有全库 CSS/token 自动 lint。现有未达标界面仍需迁移，不能仅凭本文件标记合规。

## 9. ZCode 来源映射

2026-09-27 读取本地 ZCode checkout。以下路径相对于其仓库根目录，不是 HaaS 依赖。
它们记录观察到的范式，不表示 ZCode 没有缺陷。

| 来源 | 借鉴范式 | HaaS 适配 |
|---|---|---|
| `DESIGN.md`、`AGENTS.md` | 统一规范入口 | 本文件 + GUI 指令 + spec；保留 HaaS token |
| `packages/shared/src/zcode-protocol-v4/snapshot.ts`、`rows.ts` | 显式 availability、输入路由、稳定类型化 row | Manager model/presentation；公共 HTTP/SSE 不增加 UI 投影 |
| `packages/shared/src/zcode-protocol-v4/input-intent.ts` | 投递全过程保留完整意图 | 草稿/context 和命令/队列合同；引导受 capability 门控 |
| `packages/ui/src/v4/conversationProjectionStore.ts`、`ackActivationBarrier.ts` | 事实与乐观状态分离；顺序/gap 核对恢复 | 已有 HaaS cursor/receipt 恢复，不复制私有 wire protocol |
| `packages/ui/src/v4/sessionDataLayer.ts` | Session 订阅长于视图 | Store 寿命/清理，不增加通用框架 |
| `packages/ui/src/v4/conversationTurnRenderUnits.ts`、`conversationTurnWorkSegments.ts` | 产品回合与事实计时 | Product-turn 投影与稳定 response owner |
| `packages/ui/src/v4/conversationTimelineLiveTail.ts`、`timelineScrollAnchor.ts` | 独立 live tail；用户控制跟随 | 几何、选择、导航合同 |
| `packages/shared/src/zcode-protocol-v4/toolDisplay.ts`、`packages/ui/src/ToolCallBlocks/ToolLayout.tsx`、`ToolSummaryRow.tsx` | 类型化标准工具披露 | 安全摘要/预览与授权懒加载 inline detail |
| `packages/ui/src/v4/conversationStatusPanelModel.ts`、`pendingInteractionAdapter.ts` | 决策/状态模型与显式缺失事实 | 单一 dock/status owner；不移植 legacy adapter |

不整体复制巨型 SessionPane、兼容 adapter、fallback 主题矩阵、专有 workflow graph 或 store shape。
采纳职责边界和可验证不变量，保留 HaaS 协议、安全边界、现有技术栈与克制产品风格。
