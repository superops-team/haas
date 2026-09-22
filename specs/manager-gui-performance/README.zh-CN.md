# Manager GUI 性能规格

[English](README.md) | **简体中文**

状态：已评审；阻塞项已清零；基线已验证；待实施
最近评审：2026-09-22
Change ID：manager-gui-performance-convergence
相关规格：[Manager HaaS Sidecar Backend](../manager-haas-sidecar-backend/README.zh-CN.md)、[Manager Product Identity](../manager-product-identity/README.zh-CN.md)

## 1. 组件角色

Manager GUI Performance 负责 OpenHarness React/Vite 界面的运行效率合同，包括首屏
JavaScript 交付、React 更新隔离、实时 transcript 对账和 Manager 本地数据刷新归属。它不
重新定义 HaaS 或 ADK 协议。

该组件用于约束“保持正确性的优化”：只有不破坏 missed-terminal 恢复时才能移除轮询；只有
实时状态、本地化和展开状态仍正确时才能引入新的渲染边界。

## 2. 依据与现状

2026-09-22 本机基线先使用仓库内置的 Fallow 和 React 性能规则寻找候选，再结合源码与
Vite 生产构建人工核验。已确认的基线是：

- 生产入口 chunk 为 1,042.06 kB（gzip 316.92 kB）；低频产品页面被 `App.tsx` 静态导入；
  PDF、XLSX 预览代码已经拆包；
- session 运行期间，GUI 每三秒获取一次完整持久 transcript 并转换完整消息数组；接口没有
  cursor 或 terminal-only 查询，interval 也没有 single-flight 保护；
- live text、reasoning 和 model-stage snapshot 已按 33 ms 合并，但 React state 由顶层
  `App` 持有，每次发布仍进入完整 App render；
- Connectors 与 Inbox Configure 页面存在多个同时挂载、轮询相同资源的 owner，包括叠加的
  快速轮询和基础轮询；
- `ApprovalCard.tsx` 与 `humanize.ts` 形成生产依赖环；
- 五个生产源码文件从当前应用入口不可达。

Fallow 分数、复杂度和 unused export 只作为发现线索，不直接作为验收事实。动态 CSS 候选及
仅测试使用的 export 不能据此删除。运行时性能结论必须通过下文专项测试确认。

### 2.1 已验证的本机基线

2026-09-22 使用 `vite preview` 托管生产 Vite 构建，并通过 Chromium 与仓库 Web 专用
Playwright transport fixture 对上述候选重新验证。fixture 只拦截 Manager HTTP/WebSocket
响应，不依赖真实 provider，也不修改用户真实数据；浏览器 timer、fetch、WebSocket 分发和
React render 仍走生产代码路径。结果如下：

- 生产构建只有一个 1,042.06 kB minified / 316.92 kB gzip 的入口 JS；PDF 与 XLSX 是仅有的
  独立应用功能 JS chunk，没有可选页面级 route chunk；
- 页面初始请求稳定后，Connectors 可见的 10.5 秒内发出四次 `GET /v1/connectors`，MCP 与
  Slack status 各两次，确认 connector list 存在两个刷新 owner；
- Inbox Configure 可见的 10.5 秒内，unrouted、inbox routing、recent channels 各读取四次，
  session list 读取六次；最后一项包含 App 全局 session refresher，其他资源由 Configure 卡片
  重复获取；
- 打开 running session，在把 response latency 人为设为 3.5 秒后观察 7.2 秒，共出现四次完整
  transcript 请求，最大并发为三，确认 open/immediate read 与 interval reconcile 没有
  single-flight；
- 四十个 WebSocket assistant delta 在具名开发诊断中产生 47 次顶层 `App` render
  participation，`Sidebar`、`Transcript`、`Composer` 和 `RightRail` 各约 90 次；生产 preview
  复测重现了同样的根/分支更新形态（生产组件名已 minify）。数值包含开发 React `StrictMode`
  的放大，但“未变化分支耦合到 live publication”的关系不受影响。

Fallow 入口遍历与独立 import search 还共同重现了
`ApprovalCard.tsx -> humanize.ts -> ApprovalCard.tsx` 两文件依赖环，以及五个生产可达性
候选。入口不可达只是删除前置条件，不代表删除已被证明行为安全；实施阶段仍须跑 route 与
package 回归。

## 3. 上下游关系

```text
Manager 本地 HTTP/WebSocket API
  -> 查询/对账 owner
  -> App/session state
  -> 页面 surface 或 live transcript 边界
  -> React DOM / Tauri WebView
```

- 上游：Manager session、inbox、connector、automation、artifact、settings endpoint，以及
  session/app-wide WebSocket。
- 下游：React 页面、transcript/activity renderer、sidebar、composer 和 right rail。
- 构建边界：Vite 负责生成 chunk，Tauri 消费同一组静态资源；浏览器与打包桌面的行为必须一致。

## 4. 目标、非目标与用户场景

### 4.1 目标

1. session 与 transcript 规模增长后，前台流式输出仍保持响应。
2. 保持可见数据新鲜，同时避免重复或重叠的本地 API 读取。
3. 将低频页面代码移出首屏入口 chunk。
4. 建立无环模块归属，并清理确认不可达的生产代码。
5. 建立可重复的本机性能门禁，明确区分实测结果与静态候选。

### 4.2 非目标

- 不修改 ADK 或 `/v1/haas/*` schema、事件、错误码、凭证、artifact 或容器合同。
- 不做视觉重设计、路由重命名、功能删除、transcript 截断或历史丢弃。
- 默认不引入新的服务端状态框架。只有仓库自有轻量协调器无法满足去重和取消时，才评估查询库。
- PDF、XLSX 已经 lazy，不属于首屏 bundle 缺陷。
- 列表虚拟化属于独立后续，不由本变更隐式纳入。

### 4.3 用户场景

- Given 一个大 transcript 的长任务，When live delta 到达，Then 当前进度更新且无关导航、
  设置页面不被反复渲染。
- Given 用户打开 Connectors 或 Inbox Configure，When 页面刷新，Then 每个资源只有一个
  请求 owner 且最多一个在途请求。
- Given session WebSocket 正常，When turn 正在执行，Then GUI 不为发现终态而反复下载完整
  transcript。
- Given 应用打开在 session 页面，When 首屏 JavaScript 加载，Then 未打开的设置、Inbox、
  Audit、Automation 和 Connectors 页面代码延迟加载。

## 5. 职责边界与功能需求

### P0-1：有界终态对账

- session WebSocket 仍是实时事件与终态的第一事实来源。
- 完整 transcript readback 是恢复动作，不是无条件的三秒心跳；只允许在连接中断、重连或进程
  恢复时仍有持久 running turn、WebSocket `ready` 与 session-list liveness 冲突，或明确怀疑终态
  完整性时运行。健康 transport 上的普通 event 静默绝不触发 readback；只有独立 transport-health
  信号也过期时，静默 watchdog 才可触发。
- 对账以 session 为粒度 single-flight；慢请求不得与下一次尝试重叠。
- 成功但未终态的 readback 使用有上限退避；相关事件或用户动作可重置延迟。符合条件的恢复
  trigger 立即探测；仍判断为 running 且可信度尚未恢复时，依次按 3、6、12 秒重试，之后上限为
  30 秒。后台窗口暂停非必要重试；只有已处于 `probing` 或 `recovery_required` 的 session 才可
  在进入后台时再执行一次完整性探测，健康 session 不得因窗口隐藏而探测。
- 必须保留 FV-33：丢失 `turn_done` 时不得重提任务，且只有匹配最新本地 intent occurrence
  才能清除 running。
- 第一阶段可继续使用当前完整消息接口，但改为事件驱动触发。若新增 Manager 本地
  status/cursor 接口，实施前必须补独立 API delta。

### P0-2：隔离 live React 投影

- 可变 live text、reasoning、model-stage state 归属于窄化的 live-turn 边界，不由拥有全部
  应用 surface 的组件持有。
- 一次合并后的 live 发布只更新当前 transcript 分支；sidebar、inactive route、composer 与
  right rail 外层的具名 React Profiler boundary 不得仅因 token delta 调用 `onRender`。
- 按 Manager HaaS Sidecar Backend 规格，以 `items` identity 和 running 边界缓存历史分组；
  未变化 Markdown 不重新解析。
- terminal flush 对 canonical buffer 保持同步，不得丢失最后 delta；事件顺序、task status 和
  恢复语义不变。

### P1-1：页面级拆包

- Settings、Integrations、Scheduled、Audit、Inbox、Persona 是 lazy route boundary；首次打开
  时只加载一次，并展示稳定、可访问的 loading 状态。
- session 核心 UI、审批/输入控件和当前 transcript 保留在初始路由。
- 初始同步 JavaScript graph（entry 加 session shell 可交互前所需的全部静态 JS 依赖）相对
  1,042.06 kB minified 基线至少降低 20%，即 minified 不超过 833.65 kB、gzip 不超过 250 kB；
  异步 PDF/XLSX chunk 不计入。每个可选页面 chunk 在 Vite manifest 中可单独识别。
- 构建门禁启用 Vite `build.manifest`：从 manifest entry 开始递归遍历 `imports`，每个产出 JS
  文件只计一次；minified 总量累加产出文件 byte，gzip 总量累加每个文件的 gzip byte。
  `dynamicImports`、PDF 与 XLSX 不进入初始 graph，需单独报告。
- 修改 `simple-icons` deep import 前必须用 bundle report 测量 barrel 成本；源码导入形式本身
  不是性能证据。

### P1-2：每个资源唯一查询 owner

- 已挂载 surface 按资源与有效参数组成的 key 建立唯一 refresh coordinator。
- 同 key 请求去重且 single-flight；消费者共享同一 snapshot，不启动平行 interval。
- 可见页面普通 baseline 为五秒；资源可声明唯一的更快 active 策略，Connectors authorization
  使用一秒刷新并替代五秒 timer。
- Connectors 页面只有一个基础刷新策略；authorization 期间由一个更快策略替代基础策略，
  不能叠加。
- Inbox Configure 的 routing、sessions、recent channels 和 unrouted snapshot 在卡片间共享；
  Configure 激活后，父 Inbox 停止获取已经下放给该 tab 的资源。
- 除正确性必需的请求外，surface 进入后台时暂停轮询。普通浏览器以 Page Visibility 加 window
  focus 判断；Tauri shell 还必须使用原生窗口 focus 信号，因为 WebView 的 document visibility
  不能单独作为权威依据。回到前台、mutation event 和 WebSocket fact 触发立即 revalidate。
- 请求失败时保留上一份安全 snapshot，使用已有错误/空状态表达，并按 5、10、20 秒重试，之后
  上限为 30 秒；focus、成功 mutation 或可信 WebSocket fact 立即恢复 active validation。

### P2-1：依赖与无效 surface 清理

- 将纯工具参数展示 helper 下沉到 React 组件层以下，消除
  `ApprovalCard.tsx <-> humanize.ts` 环。
- 只有 TypeScript import search、Vite entry reachability 与测试共同确认不可达，才能删除生产
  文件。首批候选为 `GalleryModal.tsx`、`PersonaHero.tsx`、`TodoPanel.tsx`、
  `brandIcons.tsx`、`connectors/CloudSignIn.tsx`。
- 测试使用或文档明确保留为公共模块 API 的 export，不能只因 production traversal 未引用就
  认定为 dead code。

## 6. 核心接口与数据模型

首期实现不要求修改 public wire interface。内部概念为：

```ts
type QueryKey = readonly [
  resource: string,
  endpoint: string,
  canonicalParameters: string,
];
type RefreshMode = "idle" | "baseline" | "active" | "backoff" | "paused";

interface RefreshState<T> {
  key: QueryKey;
  data: T | undefined;
  mode: RefreshMode;
  inFlight: boolean;
  lastSuccessAt: number | null;
  consecutiveFailures: number;
}
```

协调器仅是 GUI 内部机制，归属于一个内存 API-client 生命周期，不持久化，也不跨 WebView
共享。API endpoint/token client 重建时，旧 coordinator 必须先销毁：取消 timer 与在途
ownership、清空 snapshot，再允许新 consumer 订阅；应用/WebView reload 同样得到全新
coordinator。本合同不要求新增 credential-generation API。session id 等有效参数以确定顺序编码
进 `canonicalParameters`。协调器不持久化 response body、credential、authorization URL 或 raw
prompt；现有浏览器存储 key 不变。

## 7. 运行模型与状态机

刷新状态：

```text
mount/focus/mutation/event -> active -> request
request success           -> baseline
request failure           -> backoff
browser 或 native background/unmount -> paused
relevant event/focus      -> active
```

一个 query key 最多有一个 `request` 状态。active 快速 interval 替换基础 timer，不创建第二个
timer。unmount 必须安全 abort 或忽略完成结果。

终态对账按 session 独立：

```text
healthy event flow -> dormant
disconnect/restored running/liveness conflict/integrity suspicion -> probing
non-terminal readback -> delayed probing
matching terminal readback -> reconciled -> dormant
session change/unmount -> cancelled
```

event 静默本身不会让健康 transport 离开 `dormant`。只有符合条件的恢复 trigger 立即进入
`probing`，且只有该状态使用 3/6/12/30 秒重试序列。

## 8. 安全与权限

- 现有认证、session scope、脱敏与 no-store 行为不变。
- 共享 cache 只存在于内存，绑定一个 API-client/WebView 生命周期，并以 endpoint 和 canonical
  effective parameters 为 key。API-client 重建、endpoint 变化或 WebView reload 时，必须先销毁
  coordinator，再允许新 consumer 订阅；connector/account mutation 精确失效受影响的 key。数据
  不得跨 session 或 endpoint。
- 性能观测只记录 count、timing 和 component label，不记录 prompt、transcript 内容、工具参数、
  credential 或 signed URL。
- lazy loading 不得创建未认证的旁路路由，也不得在用户打开所属页面前获取受保护数据。

## 9. 可观测性

开发/测试观测必须在不采集生产内容的前提下度量：

- 每个 query key 的请求数与最大并发数；
- 完整 transcript 对账次数与触发原因；
- 脚本化 live delta 期间 App shell、历史 transcript、live turn 的 React Profiler boundary
  `onRender` 次数；
- Vite 初始/异步 chunk 的 minified/gzip 大小；
- fake timer 下的刷新状态迁移与 backoff。

生产日志继续禁止内容采集，不引入 always-on telemetry。

## 10. 失败、恢复、兼容与回滚

- lazy chunk 加载失败由 session shell 外侧的 error boundary 捕获，不销毁当前 session，并展示
  重试入口。首次重试创建新的 lazy loader attempt；再次失败时提供完整应用 reload，且已持久化
  的 session identity 可恢复。测试注入一次 import reject 后成功，断言错误态与恢复路径。
- refresh coordinator 失败时退回有界轮询，不得退回重复 interval。
- 若 rollout 中发现事件驱动对账漏终态，可通过 GUI 内部、默认关闭的单一开关临时恢复周期
  readback，同时保留 single-flight；启用时只记录不含内容的 reason。FV-33、健康静默和
  reconnect/missed-terminal packaged gate 在下一版本全部通过后必须删除该开关，不能保留为第二套
  永久恢复模式。
- 回滚可恢复 eager import，不变更持久数据。
- ADK 与 HaaS native 兼容面不受影响；未来若新增 Manager 本地 endpoint，只能做加法并单独写
  规格。
- session、artifact、credential、policy、MCP、container 与 event schema 不受影响。

## 11. 测试计划与验收

### 11.1 实现前基线验证

当前 checkout 上的全部适用检查已复现，问题据此视为客观存在：

1. Vite 生产构建记录 entry 与 async chunk；
2. browser fixture 对 Connectors、Inbox Configure 和 running session 至少观察两个轮询周期，
   记录请求次数与最大并发；
3. diagnostic Fiber instrumentation 在稳定 history 上发送至少 30 次合并 live update，记录
   render participation；实施时用持久化、具名 React Profiler boundary 断言替代该发现探针；
4. import graph 重现循环依赖和 entry-point reachability 候选；
5. 实现前现有 GUI 单测全部通过。

实测结果记录在 2.1 节。后续若运行观测与静态分析冲突，仍以运行结果为准，先修订 spec 再
实施。

### 11.2 TDD 实施顺序

1. 先增加失败的健康静默、请求计数、single-flight 与终态对账测试。
2. 在不改变 wire 行为的前提下实现有界恢复 controller。
3. 先增加失败的具名 React Profiler boundary 断言，再隔离 live-turn state 并稳定 props。
4. 实现带请求预算、后台行为和 fake-timer 退避测试的 refresh coordinator。
5. 增加 manifest graph 与 route-load 断言，再建立 lazy route boundary。
6. 独立拆除依赖环；route 回归后只删除确认不可达的文件。
7. 执行组件、浏览器、生产构建和打包桌面回归。

### 11.3 验收标准

- 健康 WebSocket 静默运行 60 秒时，完整 transcript fetch 为零。强制断连/missed-terminal 场景
  仍通过 FV-33；read latency 为 3.5 秒时最大并发为一，且绝不重提任务。
- `turn_start` 后清零计数；稳定 history 下发布 30 次 live projection，具名 sidebar、inactive
  route、composer、right-rail Profiler boundary 的 `onRender` 次数均为零；live-turn boundary
  正常更新，历史 Markdown render invocation 为零。terminal flush 单独断言。
- 首次 load 稳定并清零计数后，Connectors 或 Inbox Configure 可见的 10.5 秒窗口内，每项资源
  最多两次调度读取、最大并发为一、每个 key 只有一个 timer policy；mutation/event/focus 的立即
  revalidation 必须标记原因并单独计数。后台 surface 的普通 polling 为零；只有已处于 probing 的
  recovery session 可做一次最终完整性读取；回到前台后立即 revalidate。
- 生产初始 entry 满足 P1-1 预算；PDF/XLSX 仍按需加载；所有 lazy surface 在 browser 与打包
  app smoke 中可成功打开。
- production import graph 不再有 `ApprovalCard`/`humanize` 环，本变更不保留已确认不可达源码。
- `npm test -- --run`、`npm run build`、专项 Playwright、`make pre-commit`，以及最终集成时
  `make full-check` 全部通过。

## 12. 任务拆解与优先级

### 12.1 功能验证 Case

| Case | 优先级 | 覆盖需求 | 执行路径 | 通过标准 | 证据 |
|---|---|---|---|---|---|
| FV-GUI-PERF-01 | P0 | P0-1 健康 transport 静默 | Vitest 渲染 `App`，通过真实 GUI `Session` 抽象启动 running turn，在没有断连、重连、liveness 冲突时推进 fake timer 60 秒 | 清零计数后 `getSessionMessages` 不被调用；任务不被重提 | Vitest 输出与请求计数断言 |
| FV-GUI-PERF-02 | P0 | P0-1 missed-terminal 恢复与 single-flight | Vitest 或 Playwright 构造符合恢复触发条件的 running session、3.5 秒延迟 `getSessionMessages`，并返回包含最新匹配终态的 transcript | 完整 transcript 最大并发为一；running 被清除；对外 `user_message` 次数仍为一 | Vitest/Playwright 输出与最大并发计数 |
| FV-GUI-PERF-03 | P0 | P0-2 live projection 隔离 | 聚焦 React 测试用具名 Profiler boundary 包裹 shell 分支，并在 `turn_start` 后发送至少 30 个 assistant delta | 清零计数后 sidebar、inactive route、composer、right-rail boundary 的 `onRender` 均为零；live transcript 更新且 terminal flush 保留最后 delta | 带 Profiler 计数的 Vitest 输出 |
| FV-GUI-PERF-04 | P1 | P1-2 共享 refresh owner | Playwright 在 production preview 打开 Connectors 与 Inbox Configure，页面稳定后清零 HTTP 计数 | 10.5 秒内每个资源最多两次调度读取、最大并发一；authorization 快速策略替代基础 timer | Playwright 输出与分资源计数 |
| FV-GUI-PERF-05 | P1 | P1-1 页面级拆包 | 启用 Vite manifest 执行 `npm run build`，从 entry chunk 遍历 manifest | 初始同步 JS graph 不超过 833.65 kB minified 与 250 kB gzip；可选页面只出现在 `dynamicImports`；PDF/XLSX 仍为 async | build 输出与 manifest budget 脚本 |
| FV-GUI-PERF-06 | P1 | P1-1 lazy route 运行行为 | Playwright production preview 依次打开 Settings、Integrations、Scheduled、Audit、Inbox、Persona | 首次打开成功；注入一次 chunk reject 后 retry 可恢复；session shell 不被销毁 | Playwright route smoke 输出 |
| FV-GUI-PERF-07 | P2 | P2-1 import cycle 清理 | Fallow 或仓库 import-graph 检查加 TypeScript 构建 | 不再存在 `ApprovalCard.tsx <-> humanize.ts` 环；删除候选没有 production 或 test import 引用 | graph 输出、`npm run build`、`npm test -- --run` |

功能验证只记录 count、timing、route label 与 component label 证据，不记录 prompt、transcript
内容、工具参数、credential 或 signed URL。

### 12.2 OpenSpec 任务映射

| 顺序 | 优先级 | 任务 | 交付物 | 依赖 |
|---|---|---|---|---|
| 1 | P0 | 复现并固化请求/render/bundle 基线 | 专项测试与 bundle budget | 无 |
| 2 | P0 | 收敛终态对账 | single-flight、事件触发的恢复机制 | 任务 1；FV-GUI-PERF-01、FV-GUI-PERF-02 |
| 3 | P0 | 隔离 live projection render | live-turn 状态边界与 profiler 门禁 | 任务 1；FV-GUI-PERF-03 |
| 4 | P1 | 合并可见页面查询 | 每个 key 一个 coordinator 与刷新策略 | 任务 1；FV-GUI-PERF-04 |
| 5 | P1 | 拆分可选页面 | Manifest budget、lazy boundary 与可重试 loading 状态 | 任务 1；FV-GUI-PERF-05、FV-GUI-PERF-06 |
| 6 | P2 | 拆除 import cycle | 纯 helper module 与无环 import graph | 任务 1；FV-GUI-PERF-07 |
| 7 | P2 | 仅删除确认无效 surface | 逐文件可达性与 route/package 证据 | 任务 5-6；FV-GUI-PERF-07 |
| 8 | P0 | 回归与发布审查 | browser/package 证据及强制 review | 任务 2-7；全部 FV-GUI-PERF case |

对齐复核结论：每个 P0/P1 需求至少有一个可执行 Case 覆盖，每个任务都有验收引用，且没有
任务要求修改 public API/schema。第一段实施选择 S1 有界对账，因为它在保留 FV-33 正确性的
同时先消除重复完整 transcript 读取。

### 12.3 验证执行计划

按以下顺序执行：

1. `npm test -- --run src/App.lifecycle.test.tsx` 覆盖 FV-GUI-PERF-01 与 FV-GUI-PERF-02。
2. 引入 live boundary 后执行聚焦 React Profiler 单测，覆盖 FV-GUI-PERF-03。
3. `npm run build` 后跑 production-preview Playwright 计数，覆盖 FV-GUI-PERF-04 至
   FV-GUI-PERF-06。
4. import graph、`npm test -- --run`、`npm run build`、`make pre-commit` 与最终
   `make full-check` 覆盖 FV-GUI-PERF-07 与准出。

## 13. 组件影响分析

| 组件 | 影响 | 必要动作 | 兼容结论 |
|---|---|---|---|
| Manager HaaS Sidecar Backend | 终态 readback 调度与 GUI 投影 owner 改变；FV-33 语义不变 | 复用现有完整消息接口与 intent-occurrence guard | 不修改 API、事件、session 或持久 binding |
| Manager Product Identity | 无；GUI 继续 local-first、no-login | coordinator 只存在于当前 WebView/API-client 生命周期 | 不引入 cloud identity 或 login 依赖 |
| ADK 与 `/v1/haas/*` | 无 | 第一阶段不新增 route 或 schema | 完全不变 |
| Session/event projection | live React owner 移动，canonical event 顺序不变 | 保留 canonical ref、terminal flush 与 replay parity | 仅内部加法式重构 |
| Artifact viewer | 可选页面加载方式可能变化，artifact 协议不变 | 保留 PDF/XLSX 按需加载与 artifact 首次点击行为 | artifact URL、metadata、安全 header 不变 |
| Credential 与 redaction | 内存 snapshot 增加共享 owner | API-client/WebView 替换时销毁，并保持观测无内容 | 不缓存、记录或持久化 credential value |
| Container、model proxy、MCP、skills | 无 | 只执行回归门禁 | 不修改 runtime 或协议 |

## 14. 交付顺序与排期

| 阶段 | 估时 | 工作 | 进入依赖 | 准出证据 |
|---|---:|---|---|---|
| S0 | 0.5 天 | 固化持久化 request、Profiler、manifest 测试 helper | 本次已评审 spec | 失败基线门禁可重现 2.1 节 |
| S1 | 1-1.5 天 | 有界 reconciliation controller 与 FV-33 TDD | S0 | 健康静默 60 秒零读取；丢终态 single-flight 恢复 |
| S2 | 1.5-2 天 | live-turn boundary 与稳定 shell props | S0 | 具名 Profiler 门禁通过；terminal flush 正确 |
| S3 | 1.5-2 天 | Query coordinator、visibility/focus、backoff | S0 | 请求预算、后台与 mutation 测试通过 |
| S4 | 1-1.5 天 | 页面拆包、manifest budget、retry boundary | S0 | Bundle budget 与 browser/package route smoke 通过 |
| S5 | 0.5-1 天 | 拆环，并逐文件核验/删除 dead candidate | 删除仅依赖 S4 | 无环 graph 与逐文件证据 |
| S6 | 1 天 | 全量回归与强制 review 门禁 | S1-S5 | `code-review`、`brooks-review`、`brooks-test`、packaged smoke、`make full-check` 通过 |

预计工程窗口为 7-9 天，其中包含约一天风险缓冲。P0 reconciliation 与 live-render 切片应可
独立回滚；P1 不阻塞 P0 交付，dead-file 删除也不得拖延正确性修复。
