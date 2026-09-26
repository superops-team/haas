# Manager 对话体验规格

[English](README.md) | **简体中文**

状态：已评审；阻塞项已清零；已记录批准方向
最近评审：2026-09-26
Change ID：`manager-conversation-interaction-v2`
相关规格：[Manager HaaS Sidecar Backend](../manager-haas-sidecar-backend/README.zh-CN.md)、[Manager Delegation](../manager-delegation/README.zh-CN.md)、[Manager GUI Performance](../manager-gui-performance/README.zh-CN.md)、[Event Log & SSE](../event-log-sse/README.zh-CN.md)、[Session Runtime](../session-runtime/README.zh-CN.md)、[Manager Product Identity](../manager-product-identity/README.zh-CN.md)、[Security Boundary](../security-boundary/README.zh-CN.md)

## 1. 组件职责与产品优先级

Manager 对话体验负责用户在 OpenHarness 中创建、运行、控制、恢复与回看 Agent 任务的端到端体验。它定义 Manager 侧对话投影、命令准入反馈、per-session 草稿与追问队列、标准 React AI 组件边界、视觉 token、响应式行为、无障碍，以及在不永久保留 legacy 路径的前提下替换当前对话实现的渐进式迁移过程。

组件优先级固定且具有规范性：

1. 系统与任务执行稳定性；
2. 一致、克制、专业的设计品质；
3. 明确、可容错的交互行为；
4. 对齐优秀主流 Agent 任务管理体验；
5. 功能广度。

后一项不得削弱前一项。任何会丢失草稿、重复执行 turn、错误描述任务进度、隐藏恢复动作或降低长会话渲染质量的视觉优化，都不满足本规格。

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
- 小于 760 CSS px 时，navigation 与次级 status 变成 drawer。Composer 常驻附件、主要 mode indicator 和 Send/Stop；model、usage 与次要动作进入一个有标签的配置菜单。
- 在 320 CSS px 和 200% zoom 下，任何必需内容或动作都不得裁切、重叠，也不得藏在没有提示的滚动边界之外。
- 当可用 chat 宽度可能不同于 window 宽度时，断点使用 container query。

### 5.3 阅读顺序

DOM 与视觉顺序为：header context、durable timeline、current live tail、follow-up queue、pending interaction、composer。Status Panel 与 Inspector 是补充 landmark，不得中断 timeline 的语义 heading 顺序。

### 5.4 状态展示矩阵

| 状态 | 主要可见表现 | 主要动作 | 次级详情 |
|---|---|---|---|
| empty/idle | 聚焦 prompt 与三条任务相关建议 | Send | Header 或 composer control 中的 harness/model/workspace |
| submitting | 冻结用户意图，composer dock 显示 `Submitting` | 仅在安全时停止等待 | 不展示 optimistic success 或伪造 turn |
| running | 一个紧凑 activity summary 与 live tail | Stop；仅 capability 可用时展示 Pause | 展开当前工作或打开 evidence |
| waiting for user | Interaction Dock 替代活动 composer control surface | 与决策对应的动作 | 有界详情与后续 request 数量 |
| queued follow-up | Queue tray 附着在 Composer 上方 | 编辑或保持排队 | 排序、删除、interrupt-and-send |
| paused | 持久 paused 标签与已保留 partial result | Continue | 永久 Stop |
| recovering | 稳定 recovery banner；现有内容仍可读 | Retry readback/reconnect | 安全 diagnostic reference |
| failed/incomplete | 在所属 turn 附近展开第一个可操作失败 | 仅允许时 Retry/resume | Evidence Inspector |
| completed | 最终 assistant result 与安静 completion footer | 继续对话 | Usage、artifact、activity detail |

每个状态最多只有一个 filled/accent primary action。Destructive action 使用 danger semantics，不能仅因为时效性而成为 primary。任何关键 recovery action 都不得藏在 disclosure 后。

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
interface ConversationSnapshot {
  sessionId: string;
  revision: number;
  lastEventId: string | null;
  phase: "idle" | "submitting" | "running" | "waiting" | "paused" |
    "recovering" | "completed" | "failed" | "cancelled";
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
  updatedAtMs: number;
}

interface FollowUpQueueItem {
  queueItemId: string;
  clientCommandId: string;
  position: number;
  state: "queued" | "dispatching" | "running";
  submissionRef: string;
  requestedDelivery: "enqueue" | "interrupt_then_start";
  safePreview: string;
  attachmentCount: number;
  contextCount: number;
  revision: number;
  createdAtMs: number;
}
```

原始 queued content 保留在 Manager scoped local store，绝不由 list、diagnostic、metric 或 notification API 返回。`safePreview` 是本地 display projection，不是 log field。

HaaS canonical `eventId`、`sequenceNumber`、`sessionId`、`turnId`、`invocationId` 与 `toolCallId` 继续作为权威。Manager-local execution 必须投影等价 identity，不得暴露 runtime-native 细节。未知 event 只增加不含内容的诊断计数并保持不可见；不得转成 assistant output 或猜测的 success。

### 6.3 投影更新规则

- 初次加载先应用一个有界 snapshot，再 replay `lastEventId` 之后的 event，最后进入 live。
- 已按 `eventId` 应用的 event 为 no-op。
- 低 revision 不得覆盖高 revision。
- 文本 delta 按 sequence 只追加一次；final text 封存 live row，不创建第二份回答。
- 每个 started work item 最多接收一个 terminal state。
- 没有新 `turnId`/`invocationId` 时，terminal state 不得回到 running。
- 在 Composer 启用冲突命令前恢复 pending interaction。
- Reconciliation 只能用权威 snapshot 或 event page 替换不确定 derived state；不得自动重发原用户命令。

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

初始 client/server handshake 声明 `conversationProtocolVersion: 2`。版本不匹配的缓存 browser client 接收结构化 `client_upgrade_required` response 与 reload action；server 不得静默降级到无 ACK sender。Production hashed asset 与 packaged-app 原子替换使其成为有界部署迁移，而非永久 legacy 协议。

`error` 存在时结构为 `{code, safeMessage, retryable, recoveryAction?}`，绝不包含 raw prompt、完整 tool argument、credential material、signed URL 或 backend stack trace。Queue mutation command 携带 `queueItemId`、`expectedQueueRevision` 与独立 idempotency key；stale revision 返回结构化 conflict 与最新 queue snapshot。

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
3. 渐进披露的 reasoning 与 work row；
4. 最终 assistant result；
5. completion summary 与持久恢复动作。

正常已完成工作默认折叠。运行中工作展示一个紧凑 active summary。failed、interrupted 或 incomplete work 默认展开到第一个可操作失败点。用户 selection 和 disclosure 在 live update 期间保持稳定。

### 8.2 Activity summary 与 evidence

- Summary title 依次优先采用安全 task/tool summary、command preview、action summary，最后使用本地化中性 fallback。
- 一个 count 覆盖所有可见 work kind；绝不允许可见 running stage 与 zero-activity summary 同时出现。
- Running state 必须包含持久文字标签。动画可选，并在 reduced motion 下关闭。
- Activity row 展示 status、安全标题、可选 key result 和 duration；默认不展示 raw argument。
- 选择 row 后，通过现有安全 evidence path 打开 Inspector。窄屏使用不覆盖 Composer 或 Interaction Dock 的非模态 bottom drawer。

### 8.3 Pending interaction dock

Approval、input request、directory permission、tool installation、plan/team/item proposal、policy conflict 与 recoverable execution failure 共用一个 `PendingInteractionDock` shell。

- 只能有一个 primary request；后续 request 显示数量并排队。
- 标题说明需要做出的决策；primary button 重复完整后果。
- Details 有界且可展开；outbound 或 destructive action 使用明确区分的视觉处理。
- 解决 request 后锁定重复动作，直到权威结果到达。
- Request 成为 primary 时，普通 Composer 保持 mounted 以保留草稿，但在视觉与可访问树中隐藏并设为 `inert`。Free-text feedback 在 dock 自有 response field 中输入，绝不消费或覆盖普通草稿。
- 合适时 request 到达会把 focus 移到 dock heading；解决后 focus 返回触发控件或 Composer。
- 存在恢复动作的 error 必须说明该动作；不可重试的终态 error 应明确任务无法继续，不展示无效 Retry。

### 8.4 Completion summary

完成的 turn 可展示一个安静 footer，包含 duration、activity count、权威时才显示的 token usage、changed/produced artifact count 与直接 artifact action。缺失 usage 时 footer 直接省略，不渲染成持续 warning。最终 assistant result 始终是主要内容。

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
    ActivitySummary.tsx
    ActivityRow.tsx
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

Visual-regression fixture 覆盖 empty/idle、running with tools、waiting for approval、queued follow-up、recoverable failure、completed-with-artifacts 与 long-content，并在两种主题的 390 和 1440 px 下执行。另以 320 px / 200% zoom 做功能验证。出现重复事实、一个状态超过一个 filled primary action、不可访问的 truncation、任意 token，或改变 reading anchor 的 layout shift 时，评审必须拒绝。

### 10.3 主题与无障碍

- 浅色和深色主题实现同一套完整 semantic token；缺失的 dark token 不得 fallback 到 light 值。
- 小于 18 pt 的 normal text 通过 4.5:1，大字号通过 3:1，focus 与非文本 UI indicator 对相邻颜色通过 3:1。
- 所有 interactive element 在可用时使用 native semantics，并有至少 2 px 的可见 `:focus-visible` indicator。
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
| Component chunk 失败 | 保持 session 与 draft，展示可重试 surface loading error |

### 12.2 兼容性

- ADK route、ADK Event 语义与现有 `/v1/haas/*` route 不变。
- 复用现有 canonical HaaS event id 与 correlation field。缺失的 Manager-local identity 增加到 Manager projection 或内部 WebSocket envelope，UI 不得自行推断。
- 迁移期间 Manager 内部 command field 做加法扩展。最终 GUI 只使用带 ACK 的 command path，随后删除旧的无 ACK sender。
- Browser 与 Tauri 共用 component、projection、queue 与 draft contract。平台 wrapper 只增加 native file picking、notification 和 window lifecycle。
- 旧持久 transcript 通过 versioned persistence decoder 或一次性 migration 转换为新 typed model。该兼容代码隔离在 React 之外，并有明确 retained-data sunset。旧 renderer、UI item model、dual writer 与旧 CSS 均不得保留。

### 12.3 回滚

每个 migration wave 只有一个临时 internal flag 和一个有记录的 rollback commit。Wave 只能在 parity/cutover gate 之前回滚到上一实现；wave 验收后，其 flag 与被替代代码必须在下一 wave 删除。最终 release 不存在用户可见 old/new toggle 或永久 dual-write path。

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
| MCX-012 | P0 | Status accuracy | 可见 stage/tool count 与 summary 不矛盾 |
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
| MCX-027 | P2 | Search 与 turn navigation | Virtualized history 中所有匹配项可达，导航保持 reading anchor |
| MCX-028 | P2 | Semantic context chip | Skill/file/session/context ref 正确渲染、复制和打开，不暴露 raw transport syntax |

### 14.3 需求到用例追溯

| 需求 | 验收用例 |
|---|---|
| MCX-R01 | MCX-009、MCX-010、MCX-012 |
| MCX-R02 | MCX-001、MCX-002、MCX-003 |
| MCX-R03 | MCX-002、MCX-004、MCX-017 |
| MCX-R04 | MCX-005、MCX-006、MCX-007、MCX-008 |
| MCX-R05 | MCX-003、MCX-008、MCX-009、MCX-010、MCX-011、MCX-013 |
| MCX-R06 | MCX-014、MCX-015、MCX-016、MCX-018、MCX-019 |
| MCX-R07 | MCX-024、MCX-025、MCX-026 |
| MCX-R08 | MCX-011、MCX-012、MCX-018、MCX-024 |
| MCX-R09 | MCX-020、MCX-021、MCX-022 |
| MCX-R10 | MCX-013、MCX-023 |
| MCX-R11 | MCX-020、MCX-027 |
| MCX-R12 | MCX-025、MCX-028 |
| MCX-R13 | MCX-026 |

### 14.4 命令

实现时先执行聚焦命令，再执行仓库门禁：

```bash
cd manager/surfaces/gui
npm test -- --run
npm run build
npx playwright test \
  e2e/conversation-submission.spec.ts \
  e2e/conversation-queue.spec.ts \
  e2e/conversation-projection.spec.ts \
  e2e/conversation-accessibility.spec.ts \
  e2e/conversation-visual.spec.ts \
  e2e/conversation-performance.spec.ts

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
| 10 | 切换 timeline component | Turn/activity/result/completion/inspector | MCX-009/012/013/023 | 3,5 |
| 11 | 增加 virtualization/live tail | 有界列表与 scroll anchoring | MCX-020-022 | 10 |
| 12 | 增加 conversation navigation 与 semantic context | Search/turn navigation 与 typed context chip | MCX-027/028 | 10-11 |
| 13 | Responsive 与 theme polish | 全目标宽度/主题的 Focused Workbench | MCX-016/018/019/024 | 9-12 |
| 14 | 删除 legacy | 移除旧 owner、CSS、test 与 migration flag | MCX-026 | 2-13 |
| 15 | Release review | 必需 review 与完整 gate | 全部用例 | 14 |

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

## 16. 组件影响分析

| 组件 | 影响 | 必须动作 | 兼容结论 |
|---|---|---|---|
| Manager GUI Performance | 新 projection/selector、virtual history 与 component boundary | 扩展 profiler、DOM、scroll 与 bundle gate | 保留现有 budget，要求更严格 |
| Manager HaaS Sidecar Backend | 带 ACK 的 Manager command、queue persistence 与 normalized projection | 增加内部 command receipt，复用 canonical HaaS id/cursor | HaaS public protocol 不变 |
| Manager Delegation | Durable local send queue 变为可见可操作 | 保留 policy-application gate 与 accepted invocation 语义 | 澄清现有 delegation 合同，不削弱 |
| Event Log & SSE | 提供 canonical correlation 与 replay fact | 复用现有 event/turn/invocation/tool id | 初始 cutover 不需要新 public event |
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
