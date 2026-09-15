# Manager HaaS Sidecar Backend 规格

[English](README.md) | **简体中文**

Status: Draft
Last reviewed: 2026-09-14
Change ID: manager-haas-sidecar-spec, unified-runtime-approval-policy
Related specs: [HaaS Protocol](../haas-protocol/README.zh-CN.md), [Manager Delegation](../manager-delegation/README.zh-CN.md), [Harness Profile](../harness-profile/README.zh-CN.md), [Container Runtime](../container-runtime/README.zh-CN.md), [Config](../config/README.zh-CN.md), [Security Boundary](../security-boundary/README.zh-CN.md)

## 1. 组件定位

OpenHarness 默认内置非容器化 HaaS `local_managed`，且 autostart 开启。打包 App 启动本地 HaaS sidecar，并通过 HaaS `/run_sse` 执行普通桌面 turn（`execution_mode=local_api`）。Remote HaaS 与 delegated container session 是可选产品能力，但不是默认桌面执行路径。所有 HaaS-backed 模式共用只走 HTTP/SSE 的 `HaasClient`；Manager 不允许 import HaaS service object 或直接调用 Codex/provider/MCP。

```text
GUI -> Manager local API/session owner -> HaasClient -> HaaS control sidecar
    -> session runtime -> harness adapter -> workspace/model/MCP
```

### 1.1 打包运行时

桌面安装包必须随 HaaS sidecar 附带原生 Codex 0.152.1 可执行文件。构建时由 `COWORKER_CODEX_BIN` 指定输入，文件缺失或版本不匹配必须阻断打包。打包 supervisor 只从自身资源解析 Codex，不依赖用户安装的 CLI 或 Node 包装脚本；包内可执行文件缺失时不启动 sidecar。开发启动仍可使用开发者 PATH。不打包或继承凭证与个人 Codex 配置。

验收必须在最小系统 PATH、隔离 HOME 下启动包内 sidecar，识别包内版本，并经 Manager 与 HaaS 完成使用所配置 provider 的 turn。Health/readiness 不足以证明模型配置、凭证解析和任务执行成功。先验证错误构建输入被拒绝及 supervisor 资源解析，再重建 App 并执行隔离的打包 smoke。此要求不改变 ADK/HaaS 字段或容器镜像。
执行 `tauri dev` 时，桌面壳必须优先解析仓库 Manager 虚拟环境中的 server，再考虑 `src-tauri/binaries` 下可能早于当前源码的 staged release sidecar；release 构建仍解析包内资源。`COWORKER_SERVER_BIN` 在两种模式下始终是最高优先级的显式覆盖。

Manager 的 assistant text 投影必须排除 ADK 中 `thought=true` 的 part。Reasoning 不得拼入最终 assistant message 或作为回答文本持久化；HaaS canonical event 保持不变。混合 thought/text fixture 与浏览器 transcript 检查验证 local 和 delegated 路径的此边界。

### 1.2 本地模型配置应用

内置模型目录必须使用 provider 公布的 API 标识符，不使用展示名称或其他 provider 的标识符。火山方舟标准接口推荐 `doubao-seed-2-1-turbo-260628`，模型建议与能力矩阵必须一致。不静默改写已有用户显式选择；不可用模型仍返回失败，用户可在同一聊天选择修正后的模型。此目录修正不改变 ADK/HaaS 字段或 provider 身份。离线测试固定目录一致性，显式打包 smoke 验证配置 provider 的 Responses 接口确实接受该模型。

Local API turn 前，Manager 从会话选择的模型、provider descriptor 与 SecretStore 设置解析 provider；providerId 和 name 分别保留，模型名不含 vendor 前缀。先登记会话级凭证授权，通过 HTTP 同步已验证 active profile，首次使用 pin 版本。配置变化在提交前显式执行非委托 profile-rebind，保留同一 session；占用时自动等待，不 fallback。相同配置复用 profile，并发会话不能覆盖彼此已应用 route。Descriptor 显式声明 local HaaS Responses 支持（OpenAI、BytePlus Ark、Ark Agent Plan、Volcengine Ark）；自定义 endpoint 可用显式 api_type 设置选择 Responses。其他协议直接拒绝，不按 API key 或 URL 猜测。

本地 profile 同步是可恢复的两阶段操作。创建 profile 或 rebind session 前，Manager 必须读取 `GET /v1/haas/sessions/{sessionId}/profile`，并对账本地陈旧 checkpoint。不含 credential 的执行意图未变化时，只能在同一 session 与当前 supervisor grant 内复用 profile；opaque `credentialRef` 的轮换属于运行时材料，本身不构成用户配置变化。若 HaaS 已完成 rebind、但 Manager 在持久化前崩溃，下次发送应在请求意图一致时采纳权威 applied reference；确需刷新凭证句柄时，则从该权威版本执行一次 compare-and-swap rebind。Manager 必须在 capability discovery 或 `/run_sse` 前持久化同步后的 profile checkpoint。不得重复创建等价 profile、持续使用陈旧 expected version、跨 session 共享有 scope 的 credential handle，或绕过真实的并发配置变化。

本地凭证解析使用仅 supervising Manager 与 HaaS 子进程拥有的继承 Unix socketpair。启动环境仅传 descriptor 编号，并在启动 harness 前移除。授权绑定 reference、harness、session、model 和精确 provider URL；带序号请求拒绝重放和未知授权。不新增文件系统凭证入口或公开 credential API。非当前 supervisor 拥有的进程不能获取授权。缺凭证、不支持的 API type、IPC 不可用均以安全错误 fail closed，settings 仍可用。

验收覆盖受控 Responses server 加真实包内 Codex、同会话两轮、修改模型但不新建会话、并发配置隔离、IPC 越权/重放拒绝、SSE 取消/超时、provider 错误回读和 secret 反向断言。本切片不改变远程凭证配置、容器 broker 与 MCP 物化合同。

### 1.3 运行交互与完成完整性

桌面体验必须展示 harness 已产生的执行事实，不能把整个 turn 压缩成累计 assistant 文本。每个 accepted invocation 中，Manager 并发消费 ADK 兼容流与 canonical HaaS invocation 流，按因果顺序向 GUI 投影类型化 reasoning/progress、tool lifecycle、approval/input request 和权威终态。Raw chain-of-thought、完整 tool 参数/结果、provider body、credential 和 host path 保持私有；可见 reasoning 仅为有界、脱敏的进度摘要。

GUI 所选模式必须与实际 HaaS capability 一致。只有 capability discovery 返回 `approval.mode=human_bridge` 且支持结构化 input request 时，HaaS-backed chat 才能选择“Ask for approval”。Manager 将该模式投影为 `policy.tools.approvalMode=on-request`；HaaS 桥接 Codex command/file approval 与 `request_user_input` server request，期间不结束或替换 turn。门禁通过前，UI 必须明确显示 HaaS 限制，不能暗示交互确认已经生效。

Invocation 终态与用户任务终态是不同事实。`turn_end` 只报告权威 invocation 结果；Manager 另行维护 task phase（`running|waiting_for_input|waiting_for_approval|verifying|completed|incomplete|failed|cancelled`）。Failed/incomplete/cancelled invocation、未解决的 blocking request 或未完成的显式计划均不得渲染或持久化为任务完成。Partial assistant text 需要保留并标记为阶段性进展。安全自动续跑必须有界并复用同一逻辑 session；需要人工输入或重放可能重复外部副作用时禁止自动续跑。

GUI 消费 Manager 拥有的 activity 投影，不直接渲染 HaaS event 对象。协议事件继续表达事实且与展示无关；Manager 将生命周期折叠为语义工作单元，GUI 决定展示密度与布局。该边界避免 adapter 名、event key 和安全 metadata 字段名成为用户文案，同时保留 canonical event 用于 replay 与审计。

## 2. 来源与依据

已确认：默认 HaaS、不按关键词路由；打包本地执行默认使用内置非容器化 HaaS local API；delegated session 是独立的容器化模式。选择 delegated 模式时，配置就地替换同一 delegated session；忙时自动等待；确认 24h 幂等过期后下次使用自动 new turn；容器镜像默认 Lite，Mac Apple Silicon 用 Docker CLI 运行 arm64。均为目标合同，不等于当前 runtime 已支持。

## 3. 上游与下游关系

GUI 拥有用户意图和审批；Manager 拥有 transcript、send queue、endpoint settings 和持久 binding。HaaS 拥有 accepted invocation、canonical event、配置应用、policy 与执行。容器是可替换资源，不是 manager chat 身份。

## 4. 职责边界

- Local supervisor 只管理进程，不定义执行语义。
- Manager 提供授权配置意图；HaaS 验证并材料化。
- Provider/MCP credential 留在 manager secret store 或 operator vault，只按 Security Boundary §7.1 配置到可信 resolver/broker，不进入 worker。
- HaaS 失败不能隐式本地执行；`Run locally` 仅为首 turn 前显式退出默认策略，或另建本地 chat。
- P0 remote local-project 执行仍不支持，直到 workspace transfer/sync/conflict 合同实现；保存远程 endpoint 不代表 workspace mode 可用。

## 5. 核心接口

### 5.1 Settings 与 Endpoint 身份

```toml
[execution]
backend = "haas"
[haas]
mode = "local_managed"
execution_mode = "local_api"
base_url = "http://127.0.0.1:8092"
token_ref = "secret://manager/haas/default"
connect_timeout_seconds = 5
response_header_timeout_seconds = 30
stream_idle_timeout_seconds = 90
turn_timeout_seconds = 900
reconnect_max_attempts = 8
reconnect_backoff_initial_ms = 250
reconnect_backoff_max_ms = 10000
[haas.local_managed]
autostart = true
host = "127.0.0.1"
port = 8092
port_selection = "fixed"
data_dir = "<manager-state>/haas"
log_file = "<manager-state>/logs/haas-sidecar.log"
image_variant = "lite"
container_backend = "disabled"
[haas.remote]
base_url = "https://haas.example.com"
tls_verify = true
capability_probe = true
```

`mode` 选择 endpoint owner，不改变协议。`execution_mode=local_api` 通过内置非容器化 HaaS sidecar 执行，不创建 delegated session 或容器。`execution_mode=delegated_session` 是显式容器化模式，遵循 Manager Delegation。`local_managed` 只允许 loopback URL。容器模式使用 Lite 时按 Docker 执行节点选择架构；Mac arm64 不要求 Linux 构建机或 amd64 模拟。Remote 要求 HTTPS；insecure-development override 必须显式并在界面可见，拒绝 URL 内嵌 credential。

Manager 持久化 `endpointId`，引用含 canonical URL、mode、server identity、TLS policy、token reference 的 endpoint record。Binding 保存 endpointId 与 URL fingerprint，不能只有不可逆 hash。更换全局 endpoint 仅为新 chat 创建/选择记录；旧 binding 引用期间保留旧记录。Token rotation 修改对应 endpoint credential，不能把新全局 token 替换到旧 binding。Local auto-port 改变须通过带认证启动握手。Backend migration 不等于配置替换。

### 5.1.1 本地 Bootstrap 握手

1. 读写 token/port/log 前先拿 per-install OS lock；state directory 用户拥有且 0700，拒绝 symlink，supervision 期间持锁。第二实例仅在验证已有带认证 sidecar ownership 后附加，或使用独立目录，不能覆盖第一实例文件。
2. 复用已有合法 token；首次安装才生成至少 256 random bits，原子创建 0600 token file；`HAAS_STATIC_TOKEN_FILE` 只传路径。文件缺失/为空 fail closed，不回退 dev-token。
3. `HAAS_STATIC_PRINCIPAL_JSON` 是 base64 编码的合法非 secret JSON，由 Identity 映射 `{principalId,tenantId,workspaceId,defaultUserId,allowedUserIds,roles}`；默认本地 user 为 `manager`。Role 来自用户拥有的 launch config，不能来自 project/request。
4. Fixed 绑定指定 loopback port，auto 绑定 0。Manager 传新的非 secret `HAAS_LAUNCH_ID`；socket bind 后 sidecar 原子写 `{port,pid,launchId}` 到 owner-only `HAAS_PORT_READY_FILE`。只接受预期 launch id、live child 和 authenticated capability 结果。不假定 OS listening signal；最多轮询 30 秒，陈旧文件/无关 listener 不证明 ownership。对需要凭证的本地 endpoint，`running` 还要求当前 Manager 同时拥有存活子进程及其继承的 credential channel。只有健康 loopback listener、但没有该 ownership 时绝不能复用，也不能报告为 running。桌面开发热重启时，旧 child 必须跟随旧 Manager 生命周期退出；替代 Manager 可以有界等待已验证前任 drain，随后启动新的 child/channel。若 listener 持续存在或无法安全归属，启动以 `local_sidecar_not_owned` fail closed，且不得终止或附加未知进程。
5. Control ready 只要求 config/identity/store，此时可读取 unavailable execution 的 capability。空 registry 下 scoped launch-only `HAAS_BOOTSTRAP_HARNESS_ID/BASE/NAME` 只种一次 Codex identity，不含 active profile。Manager 创建、验证、激活初始授权 profile；重复 bootstrap 不覆盖用户配置。只有工作提交要求 execution ready。
6. 显式传环境：安全进程基础项（`PATH`、隔离 `HOME`/temporary path、locale）与 Config 定义、来自用户 launch settings 的 `HAAS_*`。不继承 provider/cloud credential、cookie、MCP token；Docker context/auth 单独验证，不复制进 worker env。

Launch 字段由 Config §5.2 拥有，不能成为 supervisor 私有约定。进程/端口/store/backend 改动 drain 后重启；provider/MCP/skill/AGENTS.md 更新走 `/policy`，不重启 host sidecar。脱敏日志按 32 MiB 滚动、保留 5 份。启动失败保留 settings 可用，不本地执行任务。

### 5.2 HaasClient

同一 HTTP client 处理 local/remote endpoint、结构化 envelope/error、独立 timeout、稳定 mutation key 与有界 replay。必需 surface：

- health、ready、status、capabilities、list apps/models；
- harness list/get/create/update/delete；
- profile list/get/create/update/validate/activate；
- delegated create/get/restore/update policy；
- `/run_sse`、invocation GET、native invocation/session event、events-page；
- approval list/decision、structured input list/answer、pause/continue/cancel、session GET/PATCH/DELETE；
- capability 可用时的 file upload/list/download/archive。

除无副作用 validate 外所有 mutation 使用已持久化的 operation-scoped Idempotency-Key。`profile-rebind` 仅供非 delegated client；Manager 不对 delegated chat 发送该请求。成功 header 带合法 `X-HaaS-Invocation-ID` 与 `X-HaaS-Session-ID` 后才构造 `RunSseStream`，GUI 输出前持久化 acceptance。Header 前结构化 integrity error 的 `accepted=true` 同样表示已接受工作，触发 readback，不替换执行。Pause、Continue 与 Stop 是不同的 Manager intent。Pause 必须等源 invocation 的 `haas.turn.interrupted` terminal 后 GUI 才进入 paused。Continue 创建关联源记录的新 HaaS invocation，重新接入双流 bridge，并在渲染输出前持久化新的 acceptance header。Stop 使用 cancel，之后不再暴露 Continue。

### 5.3 Discovery 与路由

`GET /v1/haas/capabilities` 是唯一稳定能力发现合同；验证 HaaS envelope 以及 status/mode/enforcement。只有所选 harness 的 `pausing.status=available|degraded` 时 Manager 才展示 Pause，不得从 cancellation 与 session continuation 组合猜测。未知值 fail closed。Readiness 表示当前可用而非支持。匹配 harness/profile fingerprint 与镜像能力，不泄漏 native transport/id 或 host path。

顺序：已有 binding → 首 turn 前显式 local choice → 已保存 backend 偏好 → input capability → execution mode。`local_api` 直接用配置的 harness id/user/session 和显式 sandbox workspace root 提交到 HaaS `/run_sse`。`delegated_session` 额外执行 persona eligibility、workspace 授权、workspace capability 与 delegated-session prepare 门禁。没有关键词或 model intent classifier。门禁失败展示原因，不隐式转本地。

P0 支持授权本地 `bind_mount` 与文本；`snapshot_upload`/`remote_workspace` 和 human approval 未支持时隐藏/不可选。Human approval UI 要求 `human_bridge`，不能以 Codex P0 `unattended_only` 代替。Probe 失败可以保存 unavailable 配置，但不能视为配置成功可执行。

### 5.4 保存配置与使用

Manager 使用统一 `ProviderRoute`、`McpServerConfig`、`SkillBundle`、`AgentsMdConfig` 和 policy schema 创建递增 profile。引用内容由 `/v1/haas/files` 上传，`contentRef` 是 immutable file id，不是 Manager-local artifact URI。Provider 保留 providerId/name；`wireApi=responses` 要求 Responses，`openai-compatible` 还须 apiType。MCP URL 是授权上游，不是 worker loopback relay。

已绑定 delegated chat 通过 `/delegated-sessions/{id}/policy` 提交完整新 profileRef；授权 mount/policy/image 同端点更新。Manager Delegation §5.1.1 拥有 desiredRevision/appliedRevision 与 update result。配置 pending 时 Manager 持久化本地 send queue、查询状态，appliedRevision 追平 desiredRevision 后自动提交。应用失败保留上次 applied state，但不使用旧配置偷跑等待发送。修正/重试配置不新建 chat；全局 endpoint 更换不迁移 binding。

#### 5.4.1 用户可控网络访问

Execution 设置页必须提供带明确标签的 HaaS workspace 与 network access 控制，作为新会话
默认值。Composer 权限选择器是当前会话唯一的 approval 控制；Settings 不得再提供平行的
approval 选择器。Fresh session 默认 `workspace-write`、开启公网访问、`on-request`。设置读取返回 effective
value，保存只接受闭合 schema；UI 说明公网访问扩大暴露面，但 private/link-local/metadata/
control-plane 路由仍阻断。Credential、签名 URL 和请求内容都不能被推断为网络授权。已有
session 展示 applied revision，不能假装新保存的默认值已经改变 running work。

对 `local_api`，Manager 在首次 `/run_sse` 创建 session 时把保存值投影为
`policy.network.defaultAction=allow|deny`（allow list 为空），HaaS 将其物化为 revision 1。
后续 run body 不得修改或放宽该 snapshot；已有 session 的变更走 revisioned session policy
端点。已 accepted invocation 继续使用冻结策略，下一 invocation 使用验证后 applied revision。
旧版本缺少字段时只迁移一次到新的显式默认值并记录 migration，
不得每次读取时重新解释。Settings 显示实际网络状态，并用
一个能区分拒绝与允许 egress 的真实任务验收。

对 `delegated_session`，Manager 把同一意图写入完整 delegated policy snapshot，变更通过
带 revision 的 `/policy` 屏障提交。Running invocation 保留 applied revision；新 invocation
等待 `appliedRevision >= desiredRevision`。若所选容器变体无法同时提供并执行请求的 egress，
更新或下一 invocation 返回 `haas_policy_unsupported`；Manager 不得把开关显示为已生效，
也不得退化为无限制 Docker network。

Composer permission mode 与 HaaS policy 是同一个 control，不能各自维护平行状态。`Ask for
approval` 映射为 `on-request`，且始终是持久化的新 session 默认值。Capability discovery
只决定交互审批 UI 当前是否可用，不改变 `on-request` 的语义：live `human_bridge` 不可用时，
UI 显示默认模式暂不可用；需要 grant 的动作必须显式失败或阻塞，不能静默改写为 `never`，
也不能 auto-approve。`Bypass approvals` 映射为 `never`，但不放宽
workspace/network policy：边界内动作无需询问，需 escalation 的动作显式失败。Adapter 如能
真实支持，可展示 `Always ask`。已有 HaaS session 修改 Composer mode 时，必须带 expected
revision 和 durable idempotency key 调用 HaaS session policy mutation。Settings 保存的
workspace/network 值仅作为新会话默认值；未来若增加当前会话编辑器，必须走同一 revisioned
mutation。Running work 保持 applied
revision，下一次 send 等 desired revision applied 后再提交。单张 approval card 只解析当前精确
pending action，绝不修改 saved mode。

### 5.5 交互状态

| 状态 | 行为/操作 |
|------|-----------|
| sidecar_starting / capability_probing | 安全进度；重试、看脱敏日志、改 settings |
| prepared | Candidate 存在但未接受工作；取消/重试准备 |
| configuration_waiting / configuration_applying | 当前工作继续，发送自动等待 |
| configuration_applied | 刷新 fingerprint，提交等待发送 |
| configuration_failed | 安全失败原因；修正/重试，不静默用旧配置 |
| waiting_workspace_lock / restoring | 有界队列/恢复进度；取消排队发送或等待 |
| running | Cancel invocation，而非 session |
| reconnecting | 工作继续，原 attempt replay |
| accepted_recovery_required | 查 invocation 和有界历史，不盲目替换 turn |
| resumed_as_new_turn | 确认 24h replay 过期后，使用时创建关联新 attempt，无确认框 |
| session_expired | HaaS retention 结束，本地 transcript 保留；新建 session，不复用过期 id |
| waiting_for_approval | 仅 human_bridge 展示安全请求，经 HaaS 明确决策 |
| waiting_for_input | 展示结构化问题/选项；仅在用户明确回答后续接同一 invocation |
| verifying | 执行结束但声明的验收检查或计划项仍未完成 |
| failed_retryable / failed_non_retryable | 保留 partial text/安全错误，区分 transport replay 和新工作 |
| incomplete | 保留阶段性进展，展示稳定安全原因并按 replay 安全性提供 resume/retry；不得表现为成功 |

当 invocation readback 已终态、但匹配的 canonical terminal event 缺失或不一致时，Manager 使用稳定 task code `haas_terminal_integrity_error`。Manager 退出本地 busy、持久化 task phase `incomplete`、抑制陈旧 approval/input 卡片，并且只允许同 attempt readback/replay；不得伪造缺失 terminal 或提交替代工作。

Chat header 始终显示 binding 的 endpoint/mode，而非新全局默认。Settings 显示 Docker/credential/image ready 与支持能力。Runtime internal、raw argument 和 credential 不进入 GUI。

“停止”是执行 mutation，不是本地渲染提示。对 HaaS-backed turn，Manager 先在本地 engine
记录 stop intent；accepted headers 提供 `(haasSessionId, invocationId)` 后立即调用
`POST /v1/haas/sessions/{sid}/invocations/{id}/cancel`，并携带
`Idempotency-Key: mgr-cancel:{invocationId}`。Stop 可能与 acceptance 竞态，因此 intent 必须
覆盖整个 Manager turn，且在 identity 可用后恰好投递一次。重复点击/重连复用同一 key。
只取消本地 SSE consumer 或关闭 WebSocket 不算停止成功。

Cancel request 发出后 Manager 继续消费/回放权威事件，task 保持 `cancelling`，直到 HaaS
给出 invocation terminal。Codex `turn/interrupt` 的立即响应只表示确认收到；后续
`turn/completed(status=interrupted)` 由 HaaS 归一化为 `haas.turn.cancelled`，这才是停止成功
条件。若 cancel 无法确认，Manager 显示安全的 `cancel_failed`/recovery 状态和 readback 动作，
不能伪造 `interrupted`。Partial reasoning、output 与 tool evidence 继续归属于 cancelled turn。

### 5.6 双流桥接与完成屏障

Manager 持久化 accepted header 后，从头订阅 native invocation replay，同时继续 drain ADK 流。ADK 文本和 native typed fact 使用独立持久 cursor/消费标记；native event 必须实时交付，不能等 ADK 关闭后才读取。去重 scope 为 `(endpointId, full session key, invocationId, eventId, projection)`；同记录 ADK 投影不能抑制 native 处理。Turn 外 native session lifecycle 使用独立 session cursor 或 delegated GET 重建；invocation-only 订阅无法显示空闲配置更新。

Native replay 使用 session-scoped `events-page` 时，transport cursor 必须按未过滤 page 的边界推进，绝不能按当前 invocation 命中的最后一个 event 推进。`nextCursor` 表示当前立即还有一页 retained event；与此独立，每个非空返回页的最后一个 event id 都成为后续轮询持久化的 `after_event_id` checkpoint，即使 `nextCursor` 为 null 也一样。必须先捕获 checkpoint，再做 invocation 过滤。因此，即使一页只有历史或并发 invocation 事件也必须继续前进；过滤结果为空不能证明当前 invocation 后面没有事件。Invocation 级去重继续由 bridge scoped claim set 负责，不新增第二套相互竞争的分页 cursor。

Native type 判定结果，stream close 不代表成功。Native terminal 到达不立即结束文本聚合：须等 ADK 到同 terminal id 且前序文本全部消费，或从 canonical event page 补齐缺失文本，再持久化唯一 assistant message 与 Manager `turn_end`。Failed/cancelled/incomplete 保留 partial output 和准确 status/code/safeReason；迟到投影不能覆盖终态或重复文本。

完成屏障由权威 readback 有界收敛，不能永久依赖两个 transport 同时确认。若 ADK 关闭、停滞或遗漏 terminal projection，Manager 必须把 canonical page 消费到 terminal event，并尝试与 invocation GET 对账。此前 canonical pages 全部消费后，已持久化的 canonical terminal 本身就是足够的权威证据，即使 invocation GET 暂时不可用也必须完成；readback 可用时，其 terminal status 以及若暴露的 terminal event id 必须一致后才能完成。随后 Manager 投影匹配的错误/task outcome 与唯一 `turn_end`，并把本地 binding 移出 `running`；不得无限等待 ADK 对同一 event id 的 claim。若 invocation readback 已终态但 canonical terminal 缺失，必须显示可恢复的完整性错误，绝不能永久显示执行中。

非 thought 的 ADK text → `assistant_delta`；`haas.output.reasoning.delta` → 类型化 `reasoning_delta`；`haas.usage.updated` → 实测 model-call usage；native tool fact → `tool_proposed/tool_started/tool_output_delta/tool_finished`；`haas.approval.required` → `permission_required`；`haas.input.required` → `question_requested`；delegated lifecycle → status；accepted header → 单次 `turn_start`；对账后 terminal → 单次 `turn_end`。Heartbeat 不创建 transcript。Native sequence 有序，不把最终聚合文本再当第二份 delta。过程事件必须 checkpoint，使重连后还原相同的有序 model-call stage、item 边界、tool card、usage、pending interaction、partial answer 与终态。Reasoning 绝不追加到 commentary 或 final-answer 文本。

Manager 还将已关联 native fact 折叠为有序 `ModelCallStageProjection`：

```json
{
  "modelCallId": "mcall_0002",
  "status": "running",
  "steps": [
    {"stepId": "item_msg_4", "kind": "commentary", "text": "正在检查事件桥接"},
    {
      "stepId": "item_reason_5:0",
      "kind": "reasoning_summary",
      "previewText": "native event 已包含这些字段",
      "previewFrozen": true,
      "text": "native event 已包含这些字段，完整 provider summary 在这里继续保留"
    },
    {"stepId": "call_7", "kind": "tool", "activityId": "inv_abc:call_7"}
  ],
  "usage": {
    "inputTokens": 8100,
    "outputTokens": 746,
    "reasoningOutputTokens": 214,
    "cacheReadTokens": 3600,
    "totalTokens": 8846
  }
}
```

`kind` 为 `output_pending|commentary|reasoning_summary|tool|result`；`output_pending` 只是实时临时状态，权威 phase 到达后不得继续保留。Step 保留 canonical event 顺序。只有 `itemId`（reasoning 还包括 `summaryIndex`）相同的连续 delta 才合并；不同 item 或 model call 绝不合并。Agent-message delta 没有权威 phase 时先成为 `output_pending`，等对应 item lifecycle 提供 `commentary|final_answer` 后原地分类；Manager 不得从自然语言猜测。若 legacy provider 缺少 item phase，对账完成的成功 invocation terminal 可作为最后 model-call stage 剩余输出的权威信号：Manager 必须在发布或持久化终态 assistant message 前，将其 `output_pending` step 重分类为 `result`。Failed、incomplete 或 cancelled terminal 不得使用这个成功结果 fallback。Model-call usage event 为该 stage 计量，但关联 tool 尚未终态时不能单独结束 stage。具有相同 `modelCallId` 的 tool lifecycle 即使在 usage event 后才 start，仍保留在触发它的 stage；全部已知关联 tool 终态或 model output 开启下一 stage 后，当前 stage 才完成；tool 完成后的新 model output 开启下一 stage。缺少关联字段时只创建明确的 `legacy` stage；缺 usage 表示 unavailable，绝不补零。

Reasoning-summary step 以 `(modelCallId, itemId, summaryIndex)` 为身份。`text` 为可展开详情保留 provider 提供的完整 canonical summary，`previewText` 则是有界的首屏投影。该 step 仍是当前尾部时，`previewText` 最多增长到 240 个 Unicode 字符，并在视觉上最多展示两行；达到字符上限即冻结。插入不同的后续 step、收到相同 `itemId` 的 item-completed event（冻结该 item 的全部 summary index），或 stage 进入终态后，预览也必须冻结（`previewFrozen=true`）。同一 reasoning identity 的迟到 delta 仍可补全 `text`，但不得改变已冻结预览。Canonical summary 仍受安全内容边界约束，不是 raw reasoning 或隐藏 chain-of-thought。Manager 不按标点拆分 summary，也不伪造中间推导步骤。Replay 与持久恢复必须重建完全相同的 preview 和冻结状态。旧持久记录若没有 `previewText`，GUI 从 `text` 派生前 240 个 Unicode 字符即可，不回写历史。

Stage usage 是 step 区域唯一的 token 声明位置，展示 `scope=model_call` 的实测 `inputTokens`、`outputTokens`、可选 `reasoningOutputTokens` 与 cache counter。Cache-read 作为 input 子集、reasoning-output 作为 output 子集展示，不能再次计入 stage total。Commentary、reasoning-summary、tool、result 子行只说明其计入该 stage，不得分摊或估算 token。Tool 执行自身没有模型 token，除非 HaaS 提供另一个带 scope 的实测 record。`cumulativeUsage` 只更新 task/session total，不能再次与 model-call usage 相加。

发布 GUI 状态前，Manager 将上述 transport action 折叠为内部 `ActivityProjection`。它不是 HaaS 公共 API，也不得回传 HaaS：

```json
{
  "activityId": "inv_abc:call_7",
  "taskId": "mtask_abc",
  "invocationId": "inv_abc",
  "kind": "command",
  "status": "running",
  "title": "运行测试",
  "safeSummary": "运行聚焦测试集",
  "commandPreview": "pytest tests/test_api.py -q",
  "workingDirectory": "workspace/",
  "outputPreview": "24 passed",
  "omittedLineCount": 0,
  "durationMs": 820,
  "exitCode": 0,
  "evidenceRef": "evd_opaque",
  "evidenceExpiresAtMs": 1789264000000,
  "recoveryGroupId": null,
  "facts": {
    "changedFileCount": null,
    "verificationStatus": null,
    "artifactIds": []
  }
}
```

`kind` 取值为 `progress|command|read|search|edit|tool|interaction|plan|recovery`；`status` 取值为 `pending|running|waiting|succeeded|failed|cancelled`。HaaS 提供 `activityKind=command|read|search|edit|tool` 时，它是 tool activity 分类的权威事实。缺失或未知值统一成为 `tool`；Manager 不得解析 `toolName`、argument 或人类摘要猜测分类。标题来自有界文案目录与协议安全事实。未知工具使用本地化通用工具标题，不得显示 `Used <native-name>` 或序列化 key/value argument。
首个可见行必须优先展示当前最具体的安全事实：command 展示有界 `commandPreview`，read/edit 展示安全文件或对象摘要，search 展示 query/target 摘要，其他 tool 展示 canonical safe tool summary。`运行命令` 之类的通用分类文案只能作为次级标签或兜底；存在更有意义的安全事实时，不得成为唯一主内容。完整 argument、完整 output、working directory 与结构化诊断继续只在 Inspector 展示。
存在已脱敏 `outputPreview` 时，默认 activity surface 还必须在动作下方展示紧凑关键
结果：最多两条有意义的行、最多 240 个显示字符。状态及可用的 exit code/duration
无需打开 Inspector 即可看到。该摘要只是辅助证据，不能替代短期完整 evidence，也不能
让无界长输出重新占满首屏。

同一 `toolCallId` 的所有事件原地更新一条 activity。HaaS tool completed 映射为 `succeeded`；只有 `haas.tool.failed`、cancel 或显式非零 command result 才映射为非成功。相邻同类成功 activity 只有在进入终态后才可折叠为计数摘要；选中项以及所有 running、waiting、failed、cancelled、recovery activity 必须保持可单独访问。只有持久化的 Manager retry/recovery action 或 invocation predecessor 明确关联时，失败与恢复才共享 `recoveryGroupId`；文本相似不能作为关联依据。

对 command activity，列表行在有数据时同时显示 semantic action 与有界
`commandPreview`，使用户无需打开 Inspector 也能识别真实动作。Preview 由 HaaS 从
native command 生成，只遮蔽 credential value，并限制为单显示行。持久 preview 仍遵守
Event Log 脱敏规则，因此 signed URL 在列表中可以显示为不可用，但在短期详情中仍保持
可用。Manager 在 lifecycle 合并中携带 `evidenceRef` 与 expiry，但不解析 evidence body，
也不把它持久化进 transcript。Terminal event 不得擦除 start 阶段得到的 evidence ref、
command preview 或 working-directory hint。

`facts` 是只从 canonical plan、artifact、tool terminal 与 verification record 组装的可扩展 typed map。Manager 不得从 assistant prose 推导修改文件数、测试结果或风险状态；未知事实保持 null/缺失，不能猜测。收敛后的结果摘要使用同一组 facts 与 task terminal state，确保 live view、replay、automation history 与完成态密度不会互相矛盾。
Source event id 与 dedupe key 保留在投影旁的 Manager 私有 checkpoint 中，不进入 GUI payload。

`turn_end.status` 为 `failed`、`incomplete` 或 `cancelled` 时，必须生成可见的非成功提示，包含稳定 `code`、`safeReason` 与明确计算的恢复操作。`turn_done` 只关闭 Manager 本地 busy 状态；不得表示成功，也不得在 task phase 非 `completed` 时把 automation run 标记成功。
Invocation terminal failure 是 turn-level fact，不是合成的 tool result。它必须显示在 task failure notice 中，不得替换所选 tool 的标题、摘要或 safe reason。同一次投影事务必须把该 turn 中所有 `running|pending|waiting` tool 收敛为 `failed`；invocation 为 cancelled/interrupted 时收敛为 `cancelled`。详情只能说明它缺少 tool terminal，并关联独立的 turn failure；不得声称 command 导致了 adapter、provider 或 transport failure。该对账同样适用于 live delivery、replay 与持久 transcript hydration。

### 5.7 任务完成控制器

Manager 仅根据结构化事实推导 task phase：invocation terminal status、pending approval/input record、显式 plan/work-item 状态、verification 结果，以及 harness 提供时的 assistant message phase。模型自然语言中的“完成”不是权威信号。Invocation 成功但仍有未完成 plan item 时进入 `verifying` 或启动一次有界 continuation turn；provider/transport/security failure 进入 `incomplete` 或 `failed` 并等待恢复。Continuation budget 按 task 持久化，默认最多三轮；耗尽后显示 `incomplete`，不得无限循环。

对于不提供 assistant message phase 的 legacy provider，只有 invocation completed、没有 blocking interaction 且没有显式 pending plan/work item 时，Manager 才能接受最后一条 assistant message 为最终答复。Stream loss、缺 terminal evidence、token failure 或验收所需 tool failure 后不得伪造完成。

Manager 为每次用户发送持久化一条 task record：

```json
{
  "taskId": "mtask_abc",
  "managerSessionId": "session_abc",
  "phase": "running",
  "currentInvocationId": "inv_abc",
  "continuationCount": 0,
  "continuationLimit": 3,
  "pendingPlanItemIds": [],
  "requiredVerificationIds": [],
  "lastTerminalStatus": null,
  "recoveryAction": null
}
```

Task transition 必须先持久化，再发布对应 GUI event。`waiting_for_input` 与 `waiting_for_approval` 续接当前 invocation；`verifying` 或安全 continuation 在同一 HaaS session 创建 linked invocation；`incomplete|failed|cancelled` 不得自动创建工作，除非 recovery action 被明确判定 replay-safe。即使复用同一 session，新的用户消息也创建新的 task record。

### 5.8 Codex 风格桌面 Activity 体验

Transcript 是每个 task 有序 activity stream 的唯一 owner。右侧 Activity Inspector 仅显示选中 activity 的详情，不是第二条时间线，也不得重复完整 activity 列表。交互遵循 Codex 将可变运行中工作与已提交 transcript history 分离的原则：

- task 活动期间，一个紧凑 activity 区域展示有界进度与 running/waiting activity，并原地更新生命周期；
- reasoning 只展示有界脱敏进度摘要，绝不展示 chain-of-thought；活动行最多展示两行预览，出现后续 step 后停止变化，完整 provider summary 只能通过该行的显式详情展开查看；
- commentary、provider reasoning summary、tool action 与 stage result 是不同的有序 row kind；reasoning 绝不拼接在 commentary 或 answer 文本之后；
- model-call stage header（而非每个子 row）展示真实 input/output/reasoning/cache token；native 未报告 usage 时显示“Token 未报告”，不估算；
- 每个 tool call 只有一个语义 activity，不为 started/output/completed 分别建行；
- approval 与 structured question 保持 inline blocking card，是用户决策/回答的唯一入口；Inspector 只读；
- 成功使用安静且不只依赖颜色的标记；红色只表示真实失败；失败展示安全原因，并在可用时展示 exit code 与 duration；
- 显式关联的失败与后续恢复作为一个 sequence 展示，但不能删除失败尝试；
- activity list 首行识别具体安全动作或目标；通用分类文案降为次级，完整 command/output/diagnostic detail 由 Inspector 承载；
- turn-level provider/adapter/transport error 保持为独立失败提示，不得挂在 tool detail 标题下伪装成该 tool 返回的错误；
- 非成功 task outcome 的 partial output 与恢复说明保持独立。

Task phase 进入 `completed` 后，turn 默认收敛为 final answer、仅由结构化事实形成的
结果摘要，以及实际 tool activity 的紧凑行。每条紧凑行保留具体动作、有界关键结果和
状态，但不展开完整参数或输出。“查看活动”展开 reasoning 与完整有序 model-stage stream，
不显示原始协议 event dump。失败/恢复尝试、跳过的验证、未解决风险及任何非成功事实在
两种状态下都应保持醒目。用户展开状态仅属于本地展示状态，不改变 task/session。

运行时当前 model-call stage 默认展开；已完成的成功 stage 收起为单行 title、step count 与实测 usage，用户可展开任意 stage，且不改变 task state。失败 stage 保持展开并聚焦失败 action。等待 usage event 时显示“Token 尚未报告”；若 stage 终止仍未收到则变为“Token 未报告”。Turn footer 对 model-call usage 只求和一次，并可把最新 cumulative snapshot 作为单独标注值展示。只有 turn-level usage 的旧历史只在 turn scope 展示该总量，绝不补造 stage 数值。

桌面 viewport 宽度至少 1100 CSS pixel 时，选择 activity 后打开右侧 Inspector，宽度为 `clamp(320px, 30vw, 400px)`。较窄 viewport 使用 chat 工作区内部、composer 上方的非模态底部抽屉，最多占可用 chat 高度的一半，不得覆盖 composer 或 active approval/input card。Inspector/抽屉展示语义标题与状态、时间、duration、安全操作摘要、适用时的 exit code、最多五行持久 preview、省略行数、安全 artifact 和完整 transcript 入口。存在未过期 `evidenceRef` 时，打开 Inspector 按 scope 即时读取，并补充实际命令、工作目录与有界 command output。普通 HTTP(S) URL 可复制、可点击；signed 用户授权 URL 在 evidence 过期前也保持完整可点击，打开时使用 `noopener,noreferrer`，且 URL 不经过 analytics、telemetry 或 Manager redirect log。完整 URL 以外的 credential value 仍脱敏。Codex 0.152.1 的合并输出标为“命令输出”；只有 adapter 提供权威 stream label 时才显示 stdout/stderr 分栏。

Evidence response 仅用于展示：`Cache-Control: no-store`，client 不持久化、不复制到 transcript item，过期后不重试。`410` 将详情 body 替换为明确的“执行证据已过期”，同时保留持久 status、exit code 与 safe preview；`404` 按不可用/无权限处理，不暴露对象存在性。完整 transcript 入口仍指完整保留的**脱敏** transcript；大体量持久输出只有在 HaaS 明确发布为授权 artifact 时才可查看。

当 Inspector 会让 transcript column 小于 520 CSS pixel 时，即使 viewport 较宽也回退到底部抽屉，包括导航或其他产品 surface 已打开的情况。因此布局选择依赖 chat 工作区的实际可用宽度，而不是 user-agent 判断。进入 completed 收敛态时，普通隐藏成功项对应的 Inspector 关闭；选中的是保留的失败/恢复事实时，详情继续可用。展开“查看活动”时，只有稳定 activity id 仍存在才恢复原选择。

Activity row 和 control 必须支持键盘访问。在 row 上按 Enter 或 Space 会选择该项并将焦点移到 Inspector/抽屉标题；pointer 选择时，键盘焦点仍留在来源 control。Escape 关闭两种详情面并将焦点还给来源 row。非模态详情面不捕获 Tab。状态除颜色外还必须有文字/图标语义；实时进度使用 polite live region，新 output 不抢焦点。Replay 后如果稳定 activity id 仍存在则保持选择，否则清空选择，不能自动打开其他 activity。

用户提交新的前台 prompt 时显式开始一个新的 transcript 跟随周期。React 提交本地
用户消息后，即使用户此前停留在较早历史位置，视口也必须移动到最新内容；随后必须
持续跟随 turn start、waiting、reasoning、model stage、tool activity 与流式回答造成的
高度变化，让用户立即看到任务已被接受且正在推进。只有提交之后用户再次明确向上滚动
才退出该轮跟随；后台或 replay 更新不得抢走用户主动固定的阅读位置。程序化滚动必须在
布局提交后发生，且自身产生的中间 scroll event 不得被误判为用户操作。

Running indicator 来自 task/invocation state，不来自 WebSocket 是否连接。重连先恢复持久 activity projection 与 pending interaction，再续 live cursor。未知 event type 仅增加诊断计数并隐藏，不能转成 assistant text、success、approval UI 或猜测的 activity。

首屏加载、历史恢复、replay 或 live event 对账期间，同一个 keyed turn 的 Transcript
投影变化不得改变 React Hook 调用顺序。legacy 与 HaaS turn renderer 必须由 Hook
顺序稳定的 wrapper 路由，分支专用 state 归属于对应 child renderer。Turn 即使新增或
失去 `modelStages` 或 HaaS activity metadata，也不得卸载 transcript root、产生白屏或
丢失剩余会话。回归测试必须以同一 turn identity 覆盖 legacy → HaaS 与 HaaS →
legacy 两种 rerender。

兼容策略仅做加法。ADK `/run` 与 `/run_sse` 不变；HaaS native tool event 保留已有 required 字段，只增加可选语义事实；output/usage event 同样只增加可选 correlation/scope fact。因此旧 server 或旧存储 event 形成单个 `legacy` stage，使用通用 `tool` activity，并且只展示其实际报告的 usage scope；旧 consumer 继续忽略新增字段。本 UI 投影不要求 stored-event migration、替换 session 或协议版本协商。

Transport disconnect 不取消执行。ADK 用原 key/Last-Event-ID 重连，native 用独立 after_event_id；exponential backoff+jitter 受 attempt/turn budget 限制。Cursor 过期查 invocation/page，本身不触发新工作。ADK Session 413 转有界 native page，不静默截断历史。GUI 慢/断线不阻塞 HaaS 消费，重连恢复持久 transcript/状态；本地远程相同向量产生等价 Manager 投影。

## 6. 持久数据与幂等

Binding 保留 manager session id、endpointId/fingerprint、mode、delegatedSessionId、HaaS user/session/harness id、首 accepted invocation/time、applied configuration fingerprint 和 desired/applied revision。PREPARED 不是 HAAS_BOUND；首次持久 acceptance 提交 binding，accepted failure 也不降级。Backend 更换不改变旧 binding。

Turn recovery 保留 managerTurnId、attemptId、predecessorInvocationId、确切 key 或安全引用、session/invocation id、server expiry、独立 ADK/native/session cursor 及状态（`opening|accepted|streaming|reconnecting|terminal|recovery_required`）。输出前持久化，含 terminal record 保留到 transcript retention。不在 binding 中保存 token/raw config。

### 6.1 操作幂等键

| 操作 | 稳定 namespace |
|------|----------------|
| Delegated create | mgr-delegated-create:{managerSessionId} |
| Restore | mgr-delegated-restore:{delegatedSessionId}:{generation} |
| Policy update | mgr-delegated-policy:{delegatedSessionId}:{policyChangeId} |
| Turn attempt | mgr-turn:{managerSessionId}:{turnSeq}:{attemptId} |
| Cancel | mgr-cancel:{invocationId} |
| Approval | mgr-approval:{approvalId}:{decisionId} |
| File upload | mgr-file-upload:{fileClientId} |
| Harness/profile mutation | mgr-config:{resourceId}:{operationId} |

ID 生成一次、请求前持久化，不含 secret/host path。服务端还按 principal/operation 隔离去重。

### 6.2 24h Replay 过期

HaaS 返回 `Idempotency-Expires-At`（epoch 毫秒）和 Invocation.idempotencyExpiresAtMs。非终态 reservation 不驱逐；轻量 key hash/acceptance/expiry tombstone 保留至 session retention。旧 key 返回 `410 haas_idempotency_expired`，不静默创建新执行。

确认过期后，下次 use/send/resume 自动分配一次新 turn attempt/key，提交前持久化与 predecessor 关系，不带旧 Last-Event-ID，显示 resumed_as_new_turn，不要求确认。重试/Manager restart 复用该 attempt。旧工作仍活动时先等待/对账；单纯 timer 过期或打开已完成 chat 不触发后台重跑。新工作可能重复副作用，保留可见历史，不能称为原生 turn 无缝续接。

已 accepted 且未终态的 attempt 被重试时属于恢复，不是重新提交。若 attempt 已有
`invocationId`，Manager 必须读取该 invocation，并从持久 native cursor 续接其 canonical
invocation event stream；不得先同步 profile、重建 `/run_sse` body，也不得用原幂等 key
重新 POST。accepted 之后这些可变输入可能发生漂移，重发会把安全重试变成
`haas_idempotency_conflict`；换新 key 则可能重复工具副作用。若 readback 已终态，沿正常
terminal barrier 对账；只有服务端明确确认幂等过期，才允许进入 linked-attempt 路径。

404/401/network/store error、cursor expiry 不等于幂等 expiry。使用服务端时间/readback，不猜本地 key age。Control mutation 不套 auto-new-turn：读取当前状态，policy intent 去重保留到 session retention，不改变已决 approval，也不把旧配置覆盖新 revision。

## 7. 生命周期

桌面执行状态为 `idle|running|pausing|paused|resuming|stopping`。Running 时 Composer
分别展示 Pause 与 Stop；paused 时展示 Continue 与 Stop；过渡态禁用重复操作。重连时状态
来自 HaaS 持久 readback，不依赖乐观 boolean。Reviewer pause 必须使用不同文案，且不改变
execution lifecycle。
Manager session WebSocket 承载 `pause`、`continue`、`interrupt` 三种 intent。
`ready.data.execution_control` 快照与后续 `execution_control` 事件统一使用
`controlState`、`supportsResume`、`resumableInvocationId`；收到 `pause` 后立即发布
`pausing`，但只有权威 interrupted readback 到达后才能发布 `paused`。`continue` 先发布
`resuming`，在首个输出前持久化新 invocation acceptance，再发布 `running`。paused 状态下
Stop 必须对 `resumableInvocationId` 调用 HaaS cancel，撤销 resumability，且不改写已经终态
的 source invocation。成功的 cancel acknowledgement 如携带权威 `sessionControl`，Manager
必须立即持久化并发布，即使 interrupted source stream 尚未退出 Manager 的 `finally`；内存中的
active-turn 记录不得在 acknowledgement 后继续让桌面停留于 `stopping`。
WebSocket 收包循环不得内联等待耗时 Pause 请求：Pause 要等待权威 terminal readback，
其后的 Stop 仍必须被并发接收并下发，使更强的 cancel intent 赢得竞态。控制任务属于
session 生命周期；查看窗口的 socket 断开不等于取消控制请求。
若控制 mutation 在生效前被拒绝，Manager 必须先恢复发布最近一次权威状态，再报告安全错误：
活动任务的 Pause/Stop 回到 `running`，Continue 或 paused Stop 回到 `paused`。`turn_done` 仅是
传输/渲染边界；它不得让桌面永久停在过渡态，也不得在没有权威 readback 时自行推断 `paused`。
Manager 在向桌面发送前归一化 HaaS 持久终态控制状态：`cancelled` 及其他非 paused
终态投影为桌面 `idle`，任务与 invocation outcome event 仍保留精确终态。GUI 不得收到
六值执行状态合同之外的 lifecycle state。

启动 → 拿 supervisor lock → 初始化 owned endpoint → control ready/discovery → initial profile → execution ready。合格新 chat 建 PREPARED、restore runtime、提交持久 attempt，acceptance 后 HAAS_BOUND。追问保留 binding，应用 pending 配置，必要时 restore，再执行新 turn。Host exit 仅 drain 拥有的资源；附加到 shared sidecar 不获得停止原 supervisor 的权力。

## 8. 安全与权限

除 health/ready 外 bearer auth，token ref 绑定 endpoint。Workspace-local config 不能选择 backend/endpoint/image 或扩大 grant。Remote config 不得请求 Manager host mount。Private worker/broker credential 和 bootstrap file 不进入 public read response。配置内容是不可信输入，应用前验证 schema/path/secret/policy。

## 9. 可观测性

安全 manager event 包括 backend_selected、local_sidecar_starting/ready/degraded、remote_probe_failed、binding_created/reused、materialization_requested 和关联 attempt 创建。`X-HaaS-Trace-ID` 为可选有界 correlation header，不作授权。Desired/applied revision 与安全更新失败独立于 invocation 结果展示。

## 10. 失败与恢复

Acceptance 前展示未启动错误；之后保留 binding/partial output 并核对终态。配置失败阻止未来 turn，不影响当前已运行 invocation。Docker/image/secret 故障不能开启 local fallback。Remote P0 workspace 限制明确，session expiry 与 idempotency expiry 使用不同恢复路径。Active invocation 收到 `invalid_token` 或 `token_expired` 属于 model-proxy lifecycle failure，必须使用稳定安全 code，不能退化为泛化的成功 stream close。Manager 保留同一 binding，只提供 replay-safe recovery。

## 11. 测试计划与验收

### 11.1 已观测基线与发布阻塞项（2026-09-12）

一次打包 local session（`execution_mode=local_api`、Codex 0.152.1）运行 93.6 秒，产生 11 个 native reasoning item、24 次 function call 与 24 个 function result。HaaS event store 中有 1,007 个 text delta、0 个 typed reasoning event、0 个 typed tool event、8 个 unparsed event，并且同一 invocation 有 2 个 terminal event。请求跨过 Manager 的 90 秒 SSE idle timeout，但 HaaS 没有发送周期 heartbeat；response 被关闭后 Session Runtime cleanup 在 Codex 仍运行时撤销 invocation capability，下一次模型调用最终报 `invalid_token`。HaaS 记录为 `incomplete`；Manager 只持久化了累计的过程 assistant 文本。一次 blocking `request_user_input` 返回 unavailable，执行在没有真实用户答案的情况下继续。

以上证据使下列项目成为发布阻塞项，而非可选体验优化：

1. 保留显式 normalized event type，并保证每个 invocation 只有一个 terminal。
2. 在 turn 运行期间向 Manager 投递脱敏 reasoning/progress 和完整 tool lifecycle。
3. 展示 failed/incomplete/cancelled outcome，且绝不把 `turn_done` 当作成功。
4. 在完整 accepted invocation 生命周期内保持或安全刷新 model capability。
5. HaaS-backed chat 只有在 approval/input bridge 实现后才能展示“Ask for approval”。
6. 增加 task-level completion/verification/continuation 状态，防止中间模型消息结束未完成任务。

### 11.2 实施切片

| 顺序 | 切片 | 必须先写的失败测试 | 退出条件 |
|------|------|--------------------|----------|
| 1 | Terminal 与 token 完整性 | 跨 stream-idle 长任务、注入 invalid/expired token、adapter terminal 加 finalize | 精确安全失败或成功 refresh；唯一 terminal；保留 partial output |
| 2 | Canonical 过程事件 | 真实 0.152.1 reasoning/item start/output/completed fixture | 稳定、脱敏 reasoning/tool type；受支持事件不再 unparsed |
| 3 | Manager 并发双流桥接 | ADK/native 到达排列、实时 native event、断线 replay | GUI 在执行中收到有序过程事件，cursor 独立且无重复 |
| 4 | 交互桥接 | Command/file approval 与 blocking input request，覆盖断线重连 | 一条持久请求、一次显式响应、同 invocation 续接；全部通过后 capability 才升级为 `human_bridge` |
| 5 | Task completion controller | Partial result、必需 tool failure、pending plan、verification、continuation 耗尽 | Task state 与 invocation state 分离；只有已验证完成的工作才进入 completed |
| 6 | 语义 Activity projector | Tool lifecycle replay、缺失/未知 activity kind、command exit 与 recovery 关联 | 每个 tool call 一个稳定 activity；显式规范状态；不出现机器字段文案或启发式 kind 推断 |
| 7 | Transcript 与 Inspector UI | Running/completed/非成功状态、键盘导航与响应式宽度矩阵 | Codex 风格运行流、完成摘要、右侧 Inspector/底部抽屉与 secretless 详情符合 §5.8 |
| 8 | 短期 command evidence | Adapter capture、scoped memory store、鉴权读取、过期和 GUI no-store 渲染 | 有效期内提供完整有效证据；credential 被遮蔽；授权 URL 可用但不进入任何持久 surface |
| 9 | 打包验收 | 3–5 分钟真实 provider 任务，包含 tool、interaction、reconnect、verification | App 展示完整生命周期，实际完成任务，并通过 secret/terminal/event-volume/UI 检查 |
| 10 | Terminal reconciliation recovery | 超过一页 session event、ADK stream 缺失 terminal projection、稍后到达的 canonical failure，以及带 stale running binding 的重连 | 未过滤 page checkpoint 持续推进，authoritative terminal readback 关闭 bridge，只产生一次 failure/`turn_end`，重连在 `ready` 前呈现 `failed`/`idle` 而不是恢复 `running` |

- 空 registry、缺 Docker/provider、token/port 竞态、陈旧 ready file、双 Manager；control API 可用且 owned file 不被覆盖。
- Local/remote 相同合同向量，token rotation、更换全局 endpoint 后打开旧 binding。
- Accepted/running/cancelling 时保存配置、排队发送、重启 Manager/HaaS，验证同一 chat 仅一次使用 applied revision 发送。
- Native terminal 先到、投影反序、断线、cursor 过期、Session 413、idle config event、跨 session 相同 event id。
- 服务端确认 replay 过期后使用时仅一个关联新 attempt；无 timer 重跑，404 不触发；旧 policy/approval retry 不撤销新状态。
- Mac Docker Lite arm64、Lite amd64、AIO amd64 的真实首 turn/追问/TTL/cancel/readback；unsupported remote workspace/human approval 不可选。
- GUI/binding/log/public event 不含 provider/MCP secret、raw tool argument、host path 或 native id；spec/schema 检查不代表 runtime 完成。
- 真实打包 turn 运行超过 90 秒 stream-idle 区间后仍保持 active，至少展示一个 reasoning/progress 更新及每个 tool start/terminal 对，并最终只产生一个权威 terminal event。注入 `invalid_token`/`token_expired` 必须可见且不能渲染为成功。
- 交互式 local HaaS 验收覆盖 command approval、file-change approval、blocking `request_user_input`、等待期间断线重连、approve/deny/cancel 以及同 invocation 续接；在用例通过前 capability 与 mode selector 保持禁用。
- Fresh-session 默认值端到端可见且真实生效：授权 root 内 workspace write 成功、公网 egress
  成功、需要 escalation 的命令产生一张 action-scoped 人工 approval card；private/metadata/
  control route 仍保持阻断，解析卡片不改变 saved mode 或授权后续动作。
- Turn 运行期间修改 approval、network 与 workspace；UI 展示 desired/applied revision，当前
  work 保持冻结，下一 send 等待，apply failure 不得退回 stale policy 或直接本地执行。
- 任务完成验收覆盖包含失败工具、未完成 plan、verification、有界 continuation 和最终完成的多步骤任务。`turn_done` 本身不得标记成功；budget 耗尽或基础设施失败后 task 保持可见且可恢复的 `incomplete`。
- 向后 replay 覆盖缺少可选 activity 字段的历史/公共 tool event：保持普通 `tool` activity、canonical 顺序与状态，不猜测 command/read/search/edit 语义。
- 1440/1100 CSS pixel 桌面截图及 1099/390 窄屏截图验证 Inspector/抽屉 breakpoint、完成态收敛、无重叠、有界文本、焦点返回和失败/恢复可见性。
- Command evidence 测试覆盖 start-to-terminal lifecycle 合并、准确 command/cwd、credential 遮蔽、普通与 signed authorization link、expiry、scope 隐藏 404、过期 410、no-store header、合并输出标签及不进入持久 transcript。

## stream-timeout-approval-recovery

ADK EOF 或读取异常仅代表交付状态。已接受请求在状态为 accepted/running/cancelling 时，继续对同一 invocation 轮询 canonical 事件，设置有界恢复 deadline 和非忙等间隔。空页不代表终态丢失。保留 attempt 与 cursor，不重新提交任务。已终态但缺少 canonical 事件仍为完整性错误。测试覆盖 EOF/读取失败、空页、审批期间延迟失败、唯一终态投影及无第二次提交。实施顺序：deadline 回归与修复、Manager 恢复回归与修复、集成/smoke 与审查。HTTP/SSE schema 与错误码保持不变；registry、profile、policy、credential、MCP、artifact、container 合同不受影响。

非成功终态还必须关闭当前 turn 未解决的 HaaS 审批卡。历史卡片与非 HaaS 审批不受影响。关闭卡片表示交互已取消，不得向服务端发送批准决定。回归测试必须断言此投影，避免旧卡在超时后继续发起变更。

## haas-context-recovery-and-recall

Manager transcript 与 Cowork memory 是 HaaS-backed chat 的恢复来源，但 Manager
不得根据关键词推断 continuation intent，也不得把手工拼装的历史上下文改写进用户
prompt。可见 user message 必须原样提交给 HaaS。提交已 accepted 的 HaaS turn 前，
Manager 仍必须先把可见 user message 持久化到本地，确保浏览器刷新、WebSocket
断线、Manager 重启或 HaaS timeout 后不会只剩 HaaS binding 而丢失用户意图。

模型侧 recall 通过 Manager 注入 active local HaaS profile 的内置 MCP source 实现，
名称固定为 `manager-cowork-recall`。该 source 暴露单个 `recall` tool，按
`X-HaaS-Session-ID` 识别的 HaaS session 返回 Cowork 数据库中的有界结构化数据：
global memory、workspace memory，以及近期脱敏 session transcript facts。是否调用
该工具由 Codex 根据任务自行决定，历史恢复不得绑定到某种语言的触发词。Tool response
不得包含 raw tool arguments、host path、credential、完整 command output，也不得包含
超过已保留可见 transcript 范围的 raw prompt。该 MCP source 是 optional；不可用时
turn 可以继续，但 Manager 必须通过 HaaS profile 和普通 task outcome 路径记录降级。
Local Codex 路径在完整 MCP runtime contract 实现前仍不支持任意外部 MCP materialization。
