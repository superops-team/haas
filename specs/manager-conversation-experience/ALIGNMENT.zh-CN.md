# ZCode 对话体验对齐交付合同

[English](ALIGNMENT.md) | **简体中文**

Change ID：`manager-conversation-interaction-v2`。范围：HaaS Manager 聊天区域及保障其可靠性的 session/command 链路。
上位：[组件规格](README.zh-CN.md)。设计入口：[DESIGN.md](../../DESIGN.zh-CN.md)。
参考 checkout：ZCode `29628c9acdb81b703bbd4080c207a0e7ce5e276e`，读取于 2026-09-27。

状态：实现、自动化门禁、production 浏览器场景、package smoke 与本机可见窗口启动均通过。已观察该固定 revision 的 ZCode 隔离空任务工作台；由于本机为 Node 22（ZCode 要求 Node 24）且隔离环境没有可用 provider，ZCode 真实任务未作为对照通过证据。活动任务对齐证据由源码不变量与 HaaS 合成任务 fixture 提供。

## 产品目标与边界

用户应能以与 ZCode 同等清晰、连续的体验启动任务、通过受支持输入路径引导任务、控制、阅读、恢复和回看任务。
交付覆盖视觉一致性、交互、渲染性能与日常操作，不能仅完成 CSS 刷新。现有 MCX 要求全部保留；P0/P1/P2 表示顺序，不裁剪后续范围。

保留 HaaS 产品身份、协议、安全和 configured harness 能力。本项目不新增 ZCode 专用 workflow engine，也不为模仿按钮虚构后端能力。
要求受支持常见聊天场景的语义体验等价，不要求品牌像素完全一致。

## 基线与对照方法

在同一主机使用相同合成内容、历史长度、工具数量、状态、语言、视口、主题及 reduced-motion 设置对照。
证据记录参考 revision、build mode、测试范围和时间。仅静态读源码不能证明视觉或运行时对齐。

| 场景 | 必须满足的结果 | 验收 |
|---|---|---|
| 空白/编辑/发送/接受/拒绝/未知 | 输入几何稳定，拒绝或更新草稿完整，无重复 turn | MCX-001–004/015/016/028 |
| 流式答复与多个工具 | 首段及时出现；一个产品 turn；答复不迁移、无竞争指示器 | MCX-012/029–035/039 |
| 暂停/继续/停止与追问队列 | 控制含义清楚；队列暂停与任务暂停独立；接受后只执行一次 | MCX-005–008/031 |
| 审批/输入决策 | 单一 dock，重连保留决策，键盘可处理且焦点归还 | MCX-011/014/015 |
| 工具详情与产物 | 安全命令可读，懒加载证据失败可解释，不丢阅读上下文即可打开结果 | MCX-017/023/025/033 |
| 重连/重启/删除/切 session | identity/scope 正确，不丢输入、不重放副作用、不复活已删除草稿 | MCX-003/004/009/010/013 |
| 长历史查找/滚动/选择 | 可查可见流式文本和虚拟历史；选择与阅读锚点稳定 | MCX-020/027/030/038 |
| 双主题、窄屏、EN/ZH | 层级相同，无遮挡、必要动作裁切、不可读文字或焦点 | MCX-014–019/024/037 |

操作对齐记录动作次数、焦点和阅读上下文是否保留。发送、停止、决策处理、工具预览等常用路径不能额外绕到其他页面。
设计品质通过成对状态截图按 DESIGN 的层级、对齐、密度、动效和披露规则评审；截图 diff 通过不等于设计品质达标。

性能保留 MCX-020/021/022/030/038：10,000 行时历史挂载行数 <=200；计数清零后 30 次 live publication 中无关组件 commit 为 0；
每帧最多一次 publication 且终态 flush；首段 response owner 稳定；指定场景锚点偏移 <=2 CSS px。
同主机 production build 测量输入/首段绘制和滚动，报告重复同条件运行的 median/p95。HaaS 更慢时定位原因，不用不同 fixture 或硬件宣称对齐。
若 ZCode 无法运行，对照证据记为缺失，项目保持未完成，同时继续推进可独立测量的验收项。

## 纵向迭代顺序

每批遵循 spec review → 行为失败测试 → 实现 → 聚焦回归 → 必要的扩大门禁。新增接口/状态前先更新所属组件合同。

| 批次 | 工作与归属 | 红态证据 / 准出条件 |
|---|---|---|
| A0 设计基础 | DESIGN、GUI 指令、共享 token 与克制运行控件 | 已有控制/对比度测试 + production 状态/几何评审 |
| A1 私有持久化 | 命令 DB 创建/重开、journal sidecar；安全与后端规格 | umask 022 下，写内容前 DB/WAL/SHM 为 0600；重开修复自有旧权限且不丢数据 |
| A2 准入/重启核对恢复 | 命令 store、Manager runtime/receipt readback | 已接受回执与首个 checkpoint 之间崩溃不会永久 running 或重放副作用 |
| A3 队列生命周期 | Store + Manager 控制 handler + FollowUpQueue | 暂停队列 Send now 可显式执行；resume 幂等且不解锁活动 dispatch；中断失败队列保持暂停 |
| A4 输入恢复 | 队列编辑回执、客户端核对、草稿生命周期 | 编辑回包丢失后完整输入恢复一次；重连正确解除未知准入；删活动 session 不会重存草稿 |
| A5 阅读与证据 | Timeline/live search/selection、Inspector | 流式 Find 可用；有界 DOM 下虚拟化保留选择；命令/证据/产物流程连贯 |
| A6 视觉与性能对照 | 公共 fixture、共享组件、双主题 | 成对参考评审与 MCX 性能/键盘/响应式门禁通过 |
| A7 桌面验收 | 当前 worktree bundle、隔离本地数据、真实 Tauri 窗口 | 原生发送/流式/控制/决策/工具/恢复/readback 与双主题走查，然后全量门禁 |

A1–A5 已知缺陷来自 2026-09-27 review 的隔离探针或明确调用/store 链路，均不是 accepted risk。
每批 spec 必须先消除恢复语义歧义再开发；不得通过静默发起第二次 invocation 修补未知执行。

## 测试、覆盖率与证据

- 单测覆盖 reducer、receipt/queue 状态迁移、草稿事务和 presentation selector。集成测试使用实际隔离 store 和协议 shape 驱动 Manager handler。
  浏览器测试使用 production bundle、真实 IndexedDB 与合成 transport。
- 新核心模块覆盖率目标 >=90%；credential/redaction/policy/path/log 安全路径 >=95%。报告实测 line/branch 覆盖率和实际模块/测试分母。
  无关旧覆盖率不能替代变更路径测试；私有文件失败分支需明确测试。不能用后端覆盖率推断 GUI 覆盖率。
- 每个行为回归保留红绿结果。证据记录命令、环境、输入范围、计数、失败、修复动作、复验与阶段结论，不包含真实 prompt、完整工具参数、
  credential、cookie、signed URL 或 transcript。
- 实现后依次执行两轮 code-review → brooks-review → brooks-test，修复确认项并重跑受影响检查。
  `make pre-commit`、`make secret-scan` 必须执行；跨组件/高风险与最终交付执行 `make full-check` 和 production GUI smoke。
- 原生验证使用当前 worktree 构建包和隔离测试 profile；区分真正原生交互、浏览器 mock、仅进程/health smoke。
  显式本机 harness/provider 验证使用合成任务。仅容器代码变化时要求 Docker build；本请求不包含 registry push 或外部发布。

## 兼容性、迁移与完成条件

除非后续 reviewed delta 明确改变，公共 ADK/HaaS HTTP/SSE schema、artifact 授权、adapter 隔离、credential 处理及 Lite/AIO 变体保持原合同。
Manager 私有 command/queue 变更必须加法兼容、可重启并写入后端 spec。原 command identity 和已接受 payload 在迁移中保留。
回滚不能启动不确定工作或丢弃待处理输入；保持持久记录可读，暂停不确定派发。不得用两个活动 renderer 或双 state writer 作为回滚策略。

对话 spec 负责 UI 验收；后端与安全 spec 负责受影响持久化/恢复边界；性能预算变化归性能 spec。
Registry、model proxy、MCP/skills、observability、container 合同未变时无需更新其 spec。每批重新检查影响矩阵。

完成要求：所有适用 MCX 用例、review finding 清零、实测覆盖率、同条件对照证据、当前 worktree 原生走查。
仅软件启动、单测通过或 DESIGN 发布都不算完成。持续记录余项并迭代；外部证据不可用时如实记录，不能记为通过。
