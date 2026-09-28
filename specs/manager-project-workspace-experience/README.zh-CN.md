# Manager 项目工作台体验规格

[English](README.md) | **简体中文**

状态：MPW-022 至 MPW-034 已实施；自动化与本机打包验收通过，等待 owner 视觉验收
最近评审：2026-09-28
Change ID：`manager-project-workspace-experience`
相关规格：[Manager 对话体验](../manager-conversation-experience/README.zh-CN.md)、[Manager HaaS Sidecar Backend](../manager-haas-sidecar-backend/README.zh-CN.md)、[Manager 产品身份](../manager-product-identity/README.zh-CN.md)、[Manager Delegation](../manager-delegation/README.zh-CN.md)、[Stores](../stores/README.zh-CN.md)、[安全边界](../security-boundary/README.zh-CN.md)

## 1. 组件定位

Manager 项目工作台体验负责把项目、可执行工作区、HaaS 执行位置、Git 上下文、会话、
命令 activity 展开以及桌面窗口 chrome 组织为一套一致的 OpenHarness 任务工作台。

以下产品层级是规范性合同：

```text
Project
  -> 一个或多个 WorkspaceBinding（本地目录/worktree 或远程 HaaS workspace）
  -> 一个默认 ExecutionTarget（local-managed 或已配置的 remote HaaS endpoint）
  -> 绑定一个 workspace + execution-target 快照的 conversations
  -> turns 与按时间排列的 command/reasoning activity
```

本组件属于 Manager 产品面，不重新定义 ADK 字段、HaaS northbound session 语义或
harness 原生协议。

## 2. 来源与依据

设计依据包括：

- 用户提供的 ZCode 项目分组、新建项目、Git 分支、工作位置与紧凑命令详情截图；
- 当前 `manager/coworker/projects.py` 的 `project_key()`，它已通过 Git common directory
  把同仓库 worktree 聚合到一个项目；
- 当前 `EndpointRecordStore`，它已定义 secretless 的 `local_managed`/`remote` HaaS
  endpoint identity；
- 当前 Manager `SessionRecord`、HaaS binding、recent workspace、folder picker 和 Git
  summary 边界；以及
- packaged macOS 证据：自定义 WebView chrome 尚未形成完整的原生拖拽、双击和最大化合同。

评估过三种方案：

1. React 只按 session path 临时分组；
2. 每条 session 复制 project/workspace/endpoint 字段；
3. 持久化 Project aggregate，由 session 引用。

必须采用方案 3。方案 1 无法稳定表达重命名、remote workspace 与 Git worktree；方案 2
产生重复事实与迁移漂移。

## 3. 目标、非目标与场景

### 3.1 目标

- 侧边栏按稳定项目身份管理 conversations，不再只展示扁平 recent list。
- 新建项目时填写名称并选择 workspace location。
- 对齐参考的渐进式 project navigation：hover/focus 信息卡、单一 project-scoped
  overflow menu，以及独立 sidebar organization menu。
- 持久化 project pin、rename、manual order、archive/remove-from-sidebar 与 conversation sort
  preference，不在 client 再造第二套 project model。
- 同一 Git 仓库的多个 worktree 作为一个项目下的 workspace binding。
- 展示 Git branch、detached state 与 dirty count，并支持安全的搜索/创建/切换。
- 首次 turn 前选择 local-managed 或 remote HaaS 执行位置。
- command acceptance 时冻结 workspace/endpoint，避免运行中漂移。
- 命令列表及 inline Shell 详情与参考交互一致，不打开侧栏详情。
- 支持桌面原生拖拽、双击最大化/恢复、Dock/tray reopen 与平台窗控。
- 保持 streaming 性能、双主题、键盘可用性、secretless 以及 conversation-v2 单 owner。

### 3.2 非目标

- 通用 Git client、merge/rebase、remote fetch 或 conflict editor。
- 永久删除 project file、Git repository、worktree、conversation 或 transcript。
- 通过 drag-and-drop 在 project 之间任意重绑 conversation。
- 复制 ZCode 私有协议、遥测、视觉资产或 Electron 实现细节。
- 把 Manager host 绝对路径发送给 remote HaaS。
- 在 endpoint 间移动已 accepted/running 的 turn。
- 引入第二套 conversation renderer、project derivation、endpoint store 或 legacy session list。
- 修改 ADK `/run`、`/run_sse`、ADK Session/Event schema 或公开 HaaS error 语义。

### 3.3 主要场景

1. 用户新建 `haas`，选择本地 repo，随后会话显示在该项目下。
2. 仓库有多个 worktree/branch；它们归于一个项目，每条 session 保留自己的 workspace 与
   branch 快照。
3. 用户在同项目新建 draft，选择已配置 remote HaaS endpoint 及 endpoint-scoped workspace。
4. 用户展开一条命令，只在该 row 下看到一个包含安全 command/output 的 Shell panel。
5. 用户拖拽非交互 titlebar 区域，双击后按原生语义最大化/恢复。
6. 用户 hover 或 keyboard-focus project/conversation row，查看唯一 bounded summary card，
   不改变 selection 或 expansion；project card 提供 Pin 与 Edit 快捷操作。
7. 用户打开 project 尾部 overflow menu，执行 pin、edit、Finder reveal、创建永久
   worktree、归档 conversation 或从 active sidebar 移除，不删除数据。
8. 用户打开 project section overflow menu 修改 project/conversation order；设置在 restart
   后保留，pinned 与 active work 优先级不变。

## 4. 上下游关系

| 关系 | 合同 |
|---|---|
| Sidebar / Composer / 项目弹窗 | 只消费 Manager project projection，不自行推导项目身份 |
| Manager session runtime | 持久化 immutable accepted workspace/endpoint snapshot 与 mutable draft selection |
| `projects.py` | 现有本地目录/Git common-dir canonical key 的唯一推导 owner |
| Endpoint record store | endpoint id、URL fingerprint 的唯一 owner；token 保持 opaque secret ref |
| HaaS backend | 对冻结的 endpoint/workspace binding 执行并返回 canonical process event |
| Git service | 读取 repo 状态并执行受保护 mutation；React 不直接运行 shell |
| Tauri shell | 持有原生 drag/maximize/reopen；React 只声明 hit region 与 intent |
| Conversation projection | 持有 chronological work row 与 inline command disclosure；项目 UI 不重复 activity fact |
| Project navigation controller | 持有 project-list projection、order preference、mutation pending/error state 与 stable callback；Sidebar 不直接 fetch/persist project |
| Sidebar presentation | 渲染 memoized project/conversation row 并发出 typed intent；不持久化、不推导 project identity、不重复 overlay positioning |

## 5. 职责边界

### 5.1 Project authority

- `ProjectStore` 持有 project id、canonical key、display name、pinned state、manual order、
  archive、default 与 workspace membership。
- `project_key(workspace)` 继续是唯一 local canonical-key 推导。Git common directory 把所有
  worktree 映射到一个项目；非 Git folder 按 canonical resolved path 映射。
- React 只接收 `ProjectSummary[]`，禁止按 basename、raw path equality、branch 或 session title
  分组。
- 从 active sidebar 移除 project 精确等于 `archived=true`。Archive/unarchive 只做加法，
  不删除 project metadata、workspace binding、session、transcript 或 file。
- 归档 project conversation 只更新当前未归档 conversation，不归档或移除 project。
- Project display name 不参与身份，可以重复；UI 用安全 workspace/endpoint context 消歧。
  内置 `prj_personal` 使用 canonical key `manager://personal`，持有没有显式 workspace 的
  sessions，且不可删除或重命名。

### 5.2 Workspace authority

- `WorkspaceBinding` 是可执行位置，不是项目别名。
- Local binding 使用 canonical local path；remote binding 使用 endpoint-scoped opaque
  `remoteWorkspaceRef` 与安全 display label，禁止在 remote 上复用本机绝对路径。
- Git state 是观察快照，不参与 workspace identity；branch 变化不创建新 project。

### 5.3 Execution target authority

- `EndpointRecordStore` 继续持有 endpoint id、mode、base URL fingerprint、server identity、
  TLS policy 与 token ref。
- Project 可定义默认 endpoint；draft 可显式选择其他已配置 endpoint。
- 首次 command acceptance 冻结 `endpointId`、`workspaceBindingId`、`harnessId`、policy/profile
  revision 与 remote workspace ref。
- accepted work 后切 endpoint 默认创建新 conversation；只有空闲且没有消息的 draft 可 rebind。
  running/queued/waiting/paused session 结构化拒绝。

### 5.4 Window authority

- Tauri 持有 window operation；CSS 不模拟 maximize、drag、close、minimize 或 reopen。
- 非交互 titlebar background 走 Tauri 同步 drag-region；交互控件是显式 no-drag island。
- 双击 drag region 只触发一次 native maximize/restore；button、link、input、可选 transcript、
  popover 或 disclosure row 不触发。

## 6. 核心接口

以下是 Manager-local API，不是 HaaS northbound API。JSON 使用 camelCase；所有 mutation
接受 `Idempotency-Key`，重复 key 返回首次结果。

### 6.1 Project API

```text
GET    /v1/projects
POST   /v1/projects
PATCH  /v1/projects/{projectId}
POST   /v1/projects/reorder
POST   /v1/settings/sidebar-order
POST   /v1/projects/{projectId}/reveal
GET    /v1/projects/{projectId}/worktrees/preview?branchName=...
POST   /v1/projects/{projectId}/worktrees
POST   /v1/projects/{projectId}/sessions/archive
GET    /v1/projects/{projectId}/workspaces
POST   /v1/projects/{projectId}/workspaces
GET    /v1/projects/{projectId}/sessions
```

Local `POST /v1/projects`：

```json
{
  "name": "haas",
  "workspace": { "location": "local", "path": "/safe/user-selected/path" },
  "defaultEndpointId": "hep_local_managed"
}
```

Remote 创建：

```json
{
  "name": "haas-remote",
  "workspace": {
    "location": "remote",
    "endpointId": "hep_team_dev",
    "remoteWorkspaceRef": "wsref_opaque",
    "displayPath": "~/workspace/github/haas"
  },
  "defaultEndpointId": "hep_team_dev"
}
```

Response 不返回 endpoint bearer token、未授权 remote host path 或 raw project config。

`PATCH /v1/projects/{projectId}` 接受 `name`、`defaultEndpointId`、`pinned`、`archived`
的非空子集。`name` 保持 1..80 个 trim 后字符；新 default endpoint 必须已存在并通过
identity validation。`prj_personal` 拒绝 rename/pin/archive。Archive 会清除 `pinned`；
同时设两者为 true 的请求被拒绝。Unarchive 恢复已存 manual position，不从 display
name 重建 identity。拥有 running、waiting、queued、pausing、paused、resuming 或 stopping
conversation 时，archive/remove 被拒绝。

`POST /v1/projects/reorder` 接受完整 mutable、unarchived、non-Personal project id 顺序列表与
`observedRevision`，与当前 search/filter 无关。它原子改写连续 manual position；成员变化时
返回 `project_order_stale`；missing、duplicate、foreign、Personal 或 archived id 均拒绝。
Search/filter 活跃或选择了非 manual sort mode 时，manual movement disabled。

`GET /v1/projects` 在 `projects` 外 additively 返回 `orderRevision`。Project creation、rename、
pinned state、archive state 或 manual position 可能改变 visible ordering 时 revision 变化。
只读 `projects` 的旧 client 保持兼容。GUI transport 保留现有 `getProjects(): ProjectSummary[]`
wrapper 给旧 caller，并增加一个 mutation-aware sidebar 专用 projection reader；component 不直接
解析 response shape。

`POST /v1/settings/sidebar-order` 遵循现有 Manager settings mutation 惯例，持久化
`projectOrder` (`manual | recent | name`) 与
`conversationOrder` (`recent | oldest | name`) 作为 Manager UI preference，不改写 project
position 或 session timestamp。Preference mutation 与 Manager 其他 settings writer 串行，并使用 atomic
file replacement，避免并发 settings update 截断文件或丢失无关 key。

`POST /v1/projects/{projectId}/reveal` 是 capability-scoped local desktop action；只解析项目已验证
local primary binding，并请求 OS file manager reveal。Remote、missing 与 untrusted project 返回结构化
unsupported result。Server projection 提供 capability 与 safe disabled-reason field；React 不从 path
或 platform global 推导。

`POST /v1/projects/{projectId}/worktrees` 接受已验证 branch name 与可选 safe display name，
通过 Manager Git service 创建一个 persistent Git worktree。只对 idle local Git project 可用；
继续使用第 6.2 节 dirty/conflict/stale/occupied guard。Confirmation view 预览 server 选择的
sibling destination `<repo-parent>/<repo-name>-<sanitized-branch>`，collision 使用确定性数字
suffix。Client 不发送 destination path。成功后在同一 project 下创建新 WorkspaceBinding，
不改变当前 conversation 的 frozen binding。

`GET /v1/projects/{projectId}/worktrees/preview` 执行同一套 capability、branch validation 与
collision-resolution logic，但不 mutation Git/filesystem。它只返回 local user-visible `displayPath`；
后续 create request 仍不携带 destination path。

`POST /v1/projects/{projectId}/sessions/archive` 原子归档项目当前未归档 session，并返回
affected count。Running、waiting、pausing、paused、resuming、stopping 或 queued session 返回
`project_sessions_busy`；不隐式 interrupt turn。

成功 mutation 返回 authoritative projection，不要求 client 猜测 optimistic state：

```text
PATCH project            -> { project: ProjectSummary, orderRevision }
POST reorder             -> { projects: ProjectSummary[], orderRevision }
POST sidebar-order       -> { projectOrder, conversationOrder }
POST reveal              -> { ok: true }
POST worktrees           -> { project, workspace, git, orderRevision }
POST sessions/archive    -> { archivedSessionIds: string[], archivedCount: integer }
```

Mutation idempotency 在进入共享 mutation store 前按 operation 与 target id 划分命名空间；
同一 caller-generated key 被误用到两个不同 operation 时返回 conflict，不 replay 无关结果。

稳定 Manager-local error：

| Code | 含义 |
|---|---|
| `project_invalid` | Name 或 source workspace 无效 |
| `project_workspace_exists` | Canonical workspace 已属于某项目；response 指明该 project |
| `workspace_unavailable` | Local path 缺失/不可信，或 remote workspace 无法解析 |
| `endpoint_unavailable` | Endpoint identity/health/capability 验证失败 |
| `git_state_stale` | Observed Git revision 已变化 |
| `git_checkout_blocked` | Dirty/conflict/worktree/active-session guard 拒绝 mutation |
| `session_binding_frozen` | Accepted session 不能改变 workspace/endpoint |
| `project_not_found` | Stable project id 不存在 |
| `project_protected` | 内置 Personal project 拒绝 rename/archive/remove |
| `project_order_stale` | 观测后 visible project 成员或顺序发生变化 |
| `project_capability_unsupported` | 当前 project/runtime 不支持 reveal/worktree action |
| `project_sessions_busy` | 项目存在 active conversation，不能归档 |

Conflict/validation 使用带安全字段的结构化 409/422，caller 不解析人类文本判断。

### 6.2 Git context API

```text
GET  /v1/workspaces/{workspaceBindingId}/git
POST /v1/workspaces/{workspaceBindingId}/git/switch
POST /v1/workspaces/{workspaceBindingId}/git/branches
```

Read response 包含 `isRepository`、`headRefType`、`branchName`、`dirtyFileCount`、local
branches、当前 worktree 占用与 observed revision；不返回文件内容或 diff。

Mutation 要求 idle local workspace 和 observed revision。Server 必须拒绝 stale revision、
未解决 conflict、destructive overwrite、active turn 或被其他 worktree 占用的 branch。
checkout 会破坏并发 session 隔离时，UI 改为提供新 worktree。React 与 agent tool path 均不得
直接执行该 mutation。

### 6.3 Endpoint 与 session projection

```text
GET /v1/haas/endpoints
GET /v1/sessions?projectId={projectId}
```

Session list response 增加可选字段：

```json
{
  "projectId": "prj_...",
  "workspaceBindingId": "wsb_...",
  "endpointId": "hep_local_managed",
  "executionLocation": "local",
  "branchSnapshot": "main"
}
```

旧 client 继续读取 `workspace`。新 client 优先使用 project/workspace identity。Migration
adapter 只存在于 server projection boundary，不增加第二个 React renderer。

### 6.4 Window command

Tauri bridge 提供类型化 `startDragging`、`toggleMaximize`、`isMaximized`、`minimize`、
`closeToTray`、`showMain`。Command 返回结构化成功/失败，不能把 unsupported platform 吞成
成功。权限显式列入 `src-tauri/capabilities/default.json`。

## 7. 数据模型

### 7.1 Project

```text
Project {
  projectId: prj_...
  canonicalKey: string          # project_key / manager://personal / remote digest
  name: string                  # 1..80 个用户可见字符
  primaryWorkspaceBindingId: wsb_...
  defaultEndpointId: hep_...
  pinned: boolean
  order: integer
  archived: boolean
  createdAtMs / updatedAtMs: int64
}
```

```text
SidebarOrderPreferences {
  projectOrder: manual | recent | name
  conversationOrder: recent | oldest | name
}

ProjectListProjection {
  orderRevision: int64
  projects: ProjectSummary[]
}

ProjectSummary {
  ...Project
  workspaceCount / sessionCount / activeSessionCount / archivedSessionCount: integer
  workspaces: WorkspaceBinding[]
  capabilities: {
    reveal: { enabled: boolean, reasonCode: string? }
    createWorktree: { enabled: boolean, reasonCode: string? }
  }
}
```

### 7.2 WorkspaceBinding

```text
WorkspaceBinding {
  workspaceBindingId: wsb_...
  projectId: prj_...
  location: local | remote
  endpointId: hep_...
  localPath: string?             # 仅 local，owner-readable persistence
  remoteWorkspaceRef: string?    # 仅 remote，opaque
  displayPath: string            # home-collapsed / endpoint-safe
  git: GitSnapshot?
  state: available | missing | reconnecting | unavailable
  createdAtMs / updatedAtMs: int64
}
```

### 7.3 Session binding 增量

```text
ManagerSessionBinding {
  projectId: prj_...
  workspaceBindingId: wsb_...
  endpointId: hep_...
  endpointFingerprint: sha256:...
  branchSnapshot: string?
  workspaceRevision: string?
}
```

首次 acceptance 后 session binding immutable；只有 idle empty draft 可显式 rebind。Project
rename/order/default 变化不改写历史 session binding。

### 7.4 Migration

首次读取 legacy session 时：

1. 存在已验证的显式历史 project/workspace binding 时优先使用；
2. 否则有 workspace 时使用 `project_key(workspace)`，无 workspace 时使用
   `manager://personal`；
3. upsert 一个 Project 与适用的 WorkspaceBinding；
4. 除非已验证历史 HaaS binding 指向其他 endpoint，否则绑定 `hep_local_managed`；
5. transactionally 持久化；
6. 返回唯一 normalized projection。

迁移幂等、可恢复，不移动 workspace 文件，也不改写 transcript。
`GET /v1/projects` 是 migration barrier：返回前完成归一化，GUI 不保留 flat legacy-session
renderer 或临时 migration group。

Additive migration 初始化 `pinned=false`，保留所有现有 `position` 与 `archived` 值，
创建唯一 monotonic order-revision record，并将缺失 sidebar preference 初始化为 `manual`
project order 和 `recent` conversation order。

Remote workspace 的 `canonicalKey` 是
`endpointId + NUL + remoteWorkspaceRef` 的 opaque digest；displayPath/name 不参与 identity。

## 8. 产品与交互合同

### 8.1 Project sidebar

- 唯一层级为 `Projects -> conversations`。Server migration barrier 在 projection 前绑定全部
  legacy session；没有 workspace 的 session 显示在内置 Personal 下。
- 初始加载必须区分“project projection 尚未完成”与“权威 project 列表为空”。未完成期间导航只
  渲染固定高度的中性 project skeleton，不得显示 session-only legacy list。首次请求 settle 后，
  权威空结果才可使用适用的 non-project layout；后续后台刷新在新结果 settle 前保留上一份 projection，
  不重新回到 loading shell。
- Project 名称和 conversation 标题统一使用 12 CSS px、1.35 行高的共享 navigation role。
  活动项目字重上限为 500；相对时间继续使用 11 CSS px caption 和等宽数字。字体不得继承
  14 px 正文阅读 role，也不得用 600/700 字重作为主要选中信号。Project/conversation 行仍
  保持至少 28 CSS px hit target，在提升信息密度的同时不牺牲可访问性。
- Project section header 只暴露两个克制控件：只打开 Create Project 的纯 `+`，以及
  `...` organization menu。旧 folder-plus glyph 退役。`+` 始终可见；`...` 在 section hover 或
  `:focus-within` 时显示。两者预留至少 28 CSS px hit target，可键盘访问且不移动 heading。
- Project row 使用 semantic primary disclosure button 与 sibling action，不得嵌套 interactive
  control。它展示 folder/repository icon、截断 name、expand/collapse、availability，以及由 row
  hover 或 `:focus-within` 显示的尾部 Edit 与 `...` control。预留尾部区域使 name/
  chevron 不位移。Conversation row 同样使用 semantic selection button 与 sibling Pin/Unpin、
  Archive shortcut。Shortcut/menu event 不 toggle project 或 select conversation。
- Hover 300 ms 或 keyboard-focus project row 时，展示 summary card，包含 project name、active task
  count、safe display path/remote label 与 Pin/Edit 快捷操作。Safe hover corridor 保证指针
  从 row 移向 card 时不关闭。Conversation row card 只展示 title、owning project 与 relative
  update age。离开 anchor/card、focus departure、Escape、ancestor scroll、row unmount 或 menu open
  时关闭 card；card 不改变 selection。
- Pointer hover 使用 300 ms open delay 和 120 ms corridor-close delay；keyboard focus 立即打开。
  拥有 summary card 的 row 移除 native `title` tooltip，避免两个 hover surface 竞争。
- Project 与 conversation hover 是同一个 discriminated overlay state 的不同 variant。调度不同
  anchor 时同步关闭当前 preview，再启动 dwell timer；打开任一 variant 都替换另一个。DOM 中最多
  存在一个 hover card。打开 project、organization 或 conversation action menu 时，必须先取消
  hover state。
- Conversation metadata 与渐进式 row action 共用一个预留尾部 grid cell。Hover、focus、menu-open
  以及在展开/折叠 project group 之间移动时，只改变 opacity/visibility 和 pointer availability；
  row 的 `x/y/width/height`、title width 与 project expansion state 保持不变。
- Interactive project card 使用有 label 的 non-modal `role="group"`；focus 可进入其 Pin/Edit
  control 而不关闭。Informational conversation card 使用 `role="tooltip"`，anchor 只在 card
  存在时设置 `aria-describedby`。Project disclosure button 暴露 `aria-expanded`；overflow trigger
  暴露 `aria-haspopup="menu"` 与 `aria-expanded`。
- Project overflow menu 固定顺序为：Pin/Unpin、Edit project、Reveal in Finder、Create
  persistent worktree、Move（仅 Manual 模式下的 Up/Down）、Archive conversations、Remove from
  sidebar。Unsupported action 保持可见但 disabled，并显示 safe reason。Personal 不可
  rename/remove。具有破坏感的 action 在同一 menu 内要求第二次显式确认。Edit
  复用 project dialog 的 edit mode，不新增第二套 form。
- Edit mode 只修改 display name 与供 future draft 使用的已验证 default execution endpoint。
  现有 workspace binding 只读展示，accepted session 保留冻结的 endpoint/workspace snapshot。
  Add/relink workspace 仍是独立显式流程。
- `Remove from sidebar` 设置 project archived flag，并可从 archived-project management 恢复。
  File、Git state、workspace binding、session、transcript、artifact 与 immutable accepted-session
  binding 全部保留。`Archive conversations` 保留 project row。
- Section organization menu 拥有两个 submenu，并有 `Archived projects`，用于列出隐藏项目并
  原样恢复，不重建记录。Project order 支持 Manual、Recent activity 和 Name；
  Conversation order 支持 Recent update、Oldest update 和 Name。Pinned project/conversation 始终领先；
  active/running work 在其余 conversation 中领先。Manual project movement 作为 project menu 中的
  Move up/Move down，并在 server 持久化。
- Project sort 必须确定：pinned explicit project 优先，其次为 active explicit project，然后按
  已选 mode；tie 使用 persisted manual position，最后用 `projectId`。Personal 始终是最后一个
  active group，且不参与 pin/reorder/archive。Conversation sort 先 pinned，再 active/running，
  再按已选 mode；tie 使用 normalized `updated_at` 降序，最后用 `session_id`。Recent
  project activity 为所属 unarchived conversation 的最大 `updated_at`，没有时 fallback 到
  project `updatedAtMs`。
- Conversation 在项目内按已选 stable order 排序，同一 session 只出现一次。
- Project/conversation row 使用 stable id，每个展开 project 受配置的 peek limit 限制；一个
  turn streaming 不得让无关 project header rerender。
- Project group 展示 task count 与安全 display path 或 remote endpoint label，不暴露 endpoint
  credential URL。
- 可见 task count 使用 `activeSessionCount`，不包含 archived conversation。Total/archived count
  只在 archived-management/detail surface 中使用。
- 所有 menu/card 通过同一 shared anchored-overlay primitive 渲染并做 collision handling。
  在 320/390/760/1440 px 下不越界、不覆盖 Composer，也不增加第二套 sidebar renderer。
- 同时只打开一个 sidebar overlay。Menu 支持 ArrowUp/ArrowDown、Home/End、Enter/Space、
  Escape、outside-click dismissal、focus return 与 disabled-item skipping。Coarse pointer 不因 hover
  打开 card；row shortcut 与 `...` control 在 coarse pointer 下保持可见，anchor 仍可键盘访问。
- Overlay owner 通过 sidebar scroll clip 外的 portal 唯一渲染，使用 anchor 推导的 fixed
  coordinate，相对 visual viewport 和 Composer top edge 做 flip/clamp，resize 时重算。Ancestor
  scroll 直接 dismiss，不留下脱离 anchor 的 overlay。
- Overlay width 使用共享 popover token，最大 320 CSS px，与 viewport 保留 8 CSS px gutter。
  深浅主题都继承 semantic surface/border/shadow/radius token，不引入复制的 ZCode color、
  shadow 或 asset 值。
- 不允许现有大型 Sidebar 为每行吸收独立 timer 与 mutation workflow。Pure ordering selector
  放在 React 之外；memoized ProjectRow/ConversationRow 只接收 primitive 或 stable prop；一个
  project-navigation controller 持有 request，一个 overlay host 持有 timer/listener。Transcript token update
  不得重建 project ordering 或 overlay descriptor。

### 8.2 Create project dialog

- 入口为 project section `+`、empty state 与 command palette。
- 必填 name，并有一个 source workspace 区域。
- Local mode 使用 native folder picker，确认前展示 canonical safe path。
- Remote mode 选择已配置的 remote HaaS endpoint，并输入 endpoint-scoped workspace ref；
  readiness/capability 在首个任务接受前验证。Endpoint 必须声明所选 harness/session capability；
  endpoint 创建仍在 Settings。
- Validation 成功前 submit disabled；Cancel 不保留 partial project record。
- Local canonical key 重复时打开 existing project，并提供把路径作为另一个 workspace 加入，
  不创建重复 project。

### 8.3 Composer context bar

- Draft 在输入框上方显示三个克制控件：Project、Work location，以及适用时的 Git branch。
- 三个控件渲染为一个衔接在 Composer 背后的紧凑 context shelf。中性 shelf 从 Composer 两侧各内收
  16 CSS px，只露出外侧顶部圆角并向前景 Composer 边缘之后延伸 12 CSS px；二者之间不得出现 canvas 色断层，也不得
  成为无关的 floating-chip row。
- Project、Work location 与 Git branch 是位于同一条 30 CSS px 中心线上的无边框同级 trigger，
  使用 UI 字号、14 CSS px 单一线宽语义 icon、可读的 primary text 与统一 compact 横向节奏。
  本机 trigger 使用设备图标，不使用代码括号。独立中性 fill 只在 hover、键盘 focus 或 menu open
  时出现。
- 在 320/390/760/1440 CSS px，以及单独的桌面 200% zoom fixture 下，shelf 保持居中且不越界，三个可用 trigger 不换行；
  长 project/endpoint/branch label 只在自身内部 ellipsis，不把同级 trigger 推出 shelf，也不改变
  shelf/Composer overlap。Branch 被省略时不占用空间。
- 每个 trigger 暴露 `aria-haspopup="menu"`、真实 `aria-expanded`、可见键盘 focus indicator，以及
  至少 30 CSS px hit height。打开/关闭 menu 不改变 trigger、shelf 或 Composer geometry。
- Work location 列出 `Local` 与已配置 remote HaaS endpoint；send 时重新验证 readiness。
  unavailable endpoint 保留选中状态与安全重试提示，但不能接受任务。
- 若 configured remote endpoint 尚未绑定到 active project，work-location menu 收集其
  endpoint-scoped workspace ref，幂等创建 binding，并把它选为新 draft；绝不改写已接受会话。
- Remote endpoint 未声明 Git mutation capability 时 branch 只读。
- 空 draft 切 project/workspace/endpoint 会更新 scoped draft key；存在文本/附件时提示迁移 draft，
  不静默丢弃。
- Accepted work 后更换 endpoint/workspace 会在同项目创建新 session。

### 8.4 Git branch interaction

- Trigger 展示 current branch 或 `Detached HEAD`，dirty count 为次级信息。
- Popover 支持 search、键盘导航、当前选择和 `Create new branch`。
- Read-only display 为 P0；safe switch/create 为 P1，遵守 §6.2 guard。
- Branch 已由其他 worktree 使用时不 destructive checkout，改为打开该 worktree 或创建新 worktree。

### 8.5 Command activity disclosure bugfix

- Completed work 按参考图呈现紧凑 chronological list：read/search row 与每次 execution 的 command
  row，包含 semantic icon、安全 summary/command、terminal state 和尾部 chevron。
- 折叠 command row 只有一条视觉行：安全 command 是唯一主标签，超出可用宽度时尾部显示
  ellipsis，terminal state 与 disclosure chevron 固定在尾部；不再显示冗余的 `已运行命令`
  分类第二行。省略只属于 presentation，JavaScript、persistence、event payload、copy/evidence
  retrieval 与 accessible command name 都保留完整的脱敏命令。
- 点击 row 后只在该 row 正下方展开一个 inline panel，不使用 right rail/bottom drawer。
- Panel 包含克制的 `Shell` heading、可安全换行的完整脱敏 `$ <safe command>`、bounded output
  preview、omitted count，以及必要的 exit code/duration；full evidence 只在授权读取后出现。
- Full evidence output 替换 persisted preview，不重复。再次点击 source row 或 Escape 收起并归还焦点。
- Raw tool args、credential、外部 host path 与 signed URL 继续排除。

### 8.6 Native desktop window behavior

- 顶部 44 px chrome 的所有非交互区域都可拖拽，包括 sidebar header、中央标题背景与左右空白。
- Interactive control 是 no-drag island，click/keyboard 行为不变。
- 双击 draggable chrome 只调用一次 Tauri `toggleMaximize`。即使用户 macOS 系统 titlebar
  preference 为 Minimize，本应用也固定提供 maximize/restore；Windows/Linux 使用相同的平台
  maximize contract。
- Maximized 状态拖拽遵循平台行为，不从 transcript selection 启动。
- sidebar 展开/折叠、zoom、maximize、restore、fullscreen 与 theme change 后，traffic light/window
  control 均保持对齐。
- Close-to-tray、Dock reopen、single-instance、minimum size、restored bounds 与 multi-display clamp
  保持有效。

## 9. 运行模型与状态机

### 9.1 Project creation

```text
idle -> validating -> creating -> ready
                    -> duplicate_existing
                    -> failed -> editing
cancel from idle/validating/failed -> closed（无 record）
```

Filesystem/remote validation 在 transaction 前完成；Project + initial WorkspaceBinding 原子提交。

### 9.2 Project navigation mutation

```text
idle -> mutating -> committed -> refreshed
                 -> stale -> refreshed（不保留 optimistic reorder）
                 -> failed -> idle（保留旧 projection）

visible -> remove-confirm -> archived
project-visible + conversations-visible -> archive-confirm -> project-visible + conversations-archived
```

同一 project 同时只允许一个 mutation。UI 可显示 transient pending affordance，但 server projection
始终是 authority。重复 idempotency key 返回首次结果。关闭 hover card/menu 不取消已接受 mutation。

Archive-conversations 与 remove-from-sidebar 和 Manager turn admission 共享 project-scoped
mutation gate。Handler 获取 gate 后解析当前 project membership，重新检查所有所属 session 的
authoritative lifecycle/queue state，再 commit 或 reject，最后释放 gate。新 turn 不得在 busy check
与 archive/remove commit 之间通过 admission。

### 9.3 Draft target binding

```text
unbound_draft -> bound_draft -> accepting -> accepted_frozen
bound_draft -- target change --> bound_draft
accepted_frozen -- target change --> new_bound_draft
```

Acceptance unknown 保留 target 与 draft；retry 先对账 command receipt，不直接二次发送。

### 9.4 Remote endpoint

```text
configured -> checking -> ready
                     -> unavailable
ready -> reconnecting -> ready | unavailable
```

Endpoint identity、TLS、token ref、capability 与 remote workspace scope 验证完成前不发送任务。
失败不得 fallback 到 local。

Manager 只能从冻结的 `SessionProjectBinding` 解析 remote session：加载匹配的
`EndpointRecordStore` 记录、校验 URL fingerprint、仅在内存中解析该记录的
`SecretRef`、检查 `/v1/haas/ready?scope=execution` 与 harness 交互能力，再调用远端
ADK `/run_sse`。`remoteWorkspaceRef` 原样作为远端 sandbox workspace root 发送，禁止
通过 Manager 主机文件系统做路径归一化。远端 HaaS 实例拥有其已激活的
harness/provider profile；Manager 禁止向远端转发本地 provider credential 或本地
mount manifest。endpoint 记录、凭据、workspace ref、readiness 或 capability 任一缺失，
都必须在任务接受前返回结构化 unavailable 结果。

## 10. 安全与权限

- Local folder selection 是显式用户意图，仍须经过 workspace trust/policy。
- Remote endpoint 默认要求 HTTPS + TLS verify；只有既有显式 development override 可放宽。
  URL 不允许 credential、path、query 或 fragment。
- Endpoint bearer 只保存在 SecretStore；project/session 只存 `tokenRef`、endpoint id 与 URL fingerprint。
- Project name、branch、display path 与 remote label 都是不可信展示文本，禁止 shell interpolation。
- Hover card/menu 只以 text 渲染这些值，不发送包含 name、title、path、branch 或 menu
  argument 的 analytics/persistent log。
- Reveal 与 persistent-worktree action 从 id 解析已存的 validated binding；client 不向这两个
  action 传 raw filesystem path。
- Git command 使用 argv、canonical cwd、timeout、output bound 与 operation allowlist。
- Branch mutation 必须来自用户直接 UI action，不能由 transcript、agent tool output、project config
  或 remote response 发起。
- Remote HaaS 只接收 endpoint-scoped workspace ref，禁止 Manager local absolute path/host mount。
- Log/metric 排除 raw prompt、full tool args、bearer、signed URL 与 exact local path。

## 11. 可观测性

只记录无内容信号：

- `manager_project_total{state}`
- `manager_project_migration_total{result}`
- `manager_workspace_binding_total{location,state}`
- `manager_execution_target_check_total{mode,result}`
- `manager_git_operation_total{operation,result}`
- `manager_window_action_total{action,result,platform}`
- `manager_activity_disclosure_total{action,kind}`
- `manager_project_action_total{action,result}`
- `manager_sidebar_order_change_total{dimension,result}`

Log 仅包含 trace id、project/session/workspace/endpoint id、安全 result code 与 elapsed time；不包含
project name、branch、path、command、output 或 endpoint URL。

## 12. 失败、恢复、兼容与回滚

- Local workspace 丢失：project 仍可见并标记 missing；session 可读；relink 修复 binding，不改 identity。
- Remote endpoint unavailable：block draft/send 并提供安全 retry，不 fallback local。
- Branch 被外部修改：刷新 observed revision，展示真实新状态，不覆盖 repo 恢复 UI 旧选择。
- Project creation 中 crash：atomic transaction 保证 Project/WorkspaceBinding 同时存在或同时不存在。
- Restart：project expand/order/active、draft scope 与 accepted binding 独立恢复；popover 不持久化。
- Mutation failure：保留旧 server projection，在 inline/menu 显示 safe error，使用新 idempotency key
  retry。Stale reorder 在下一次 move 前 refetch。
- Reveal/worktree unsupported：menu 保持可用，action disabled 并显示 reason；不 fallback 到
  shell execution 或 agent tool call。
- Remove/archive recovery：archived project/conversation 保持可查询可恢复；rollback 不重建 file，
  不改写 session binding。
- 移除当前选中且 idle 的 project 后，导航到下一个 visible project/session，再 fallback 到
  Personal/new session。Active 或 non-terminal project 在导航发生前被拒绝。
- Window action failure：bounds 不变并给安全诊断；不 reload WebView 或复制 sidecar。
- Manager API/persistence 只做加法；ADK 与 `/v1/haas/*` 不变。Legacy `workspace` 在一轮迁移期保留，
  只有 GUI/readback/packaged gate 全部使用 binding id 后才可退休。
- Rollback 保留新增 table/field；旧 build 忽略它们继续读 legacy 数据，不执行 destructive down migration。

## 13. 组件影响分析

| 组件 | 影响 |
|---|---|
| Manager Project Workbench | 扩展 authoritative store/API，增加 pin、rename、reorder、archive、restore、reveal/worktree capability 与渐进式 GUI surface |
| Manager Conversation Experience | 复用 TurnWork/ActivityInspector；增加 draft project/workspace/endpoint context 与 command disclosure 验收 |
| Manager HaaS Sidecar Backend | 暴露 configured endpoint list；冻结 endpoint/workspace；remote 不 fallback local |
| Manager Product Identity | 扩展 packaged native window behavior 与验收 |
| Manager Delegation | 复用 endpoint/session binding，不放宽 mount policy |
| Stores | 增加 project `pinned` field、order revision、sidebar-order preference 与 additive migration；现有 binding 不变 |
| Security Boundary | 复用 SecretRef、endpoint URL validation、workspace trust 与 path redaction |
| HaaS Protocol / ADK | 无影响；仅 Manager-local API |
| Adapter / Event Log / Model Proxy / MCP / Artifact / Container | 无协议或行为变化 |

## 14. 测试计划与验收

| ID | 优先级 | 验收用例 |
|---|---|---|
| MPW-001 | P0 | 同一 Git common dir 的两个 local worktree 生成一个 project、两个 workspace binding |
| MPW-002 | P0 | canonical path 不同的 plain folder 保持不同 project |
| MPW-003 | P0 | Legacy session 幂等迁移；显式 binding 优先；无 workspace session 只在 Personal 下渲染一次 |
| MPW-004 | P0 | Sidebar 按 project 分组，并用 peek limit 限制每个展开组；一个 turn streaming 对无关 project header 产生零 commit |
| MPW-005 | P0 | Create dialog 选择 local folder，验证并原子创建 Project + WorkspaceBinding |
| MPW-006 | P0 | Canonical folder 重复时打开 existing project，不生成 duplicate record |
| MPW-007 | P0 | Remote project/draft 只能选择 configured endpoint 与 endpoint-scoped workspace ref；send 前验证 readiness/capability |
| MPW-008 | P0 | Remote endpoint failure block send，绝不静默使用 local-managed HaaS |
| MPW-009 | P0 | First accepted command 冻结 project/workspace/endpoint；后续 default 变化不改写 |
| MPW-010 | P0 | Accepted work 后 target change 创建新 session，旧 transcript/binding 不变 |
| MPW-011 | P0 | Git trigger 展示 branch/detached/dirty；非 Git workspace 隐藏 trigger |
| MPW-012 | P1 | Safe branch search/switch/create 覆盖 stale revision、dirty overwrite、conflict 与其他 worktree ownership |
| MPW-013 | P0 | 长命令在 390/760/1440 px 下保持一条带 ellipsis 的折叠行，不显示冗余分类，并只在点击 row 下展开一个包含完整脱敏命令的 Shell panel |
| MPW-014 | P0 | Inline evidence 替换 duplicate preview；404/410 保留安全 fallback；收起归还焦点 |
| MPW-015 | P0 | Light/dark 390/760/1440 px token 一致、popover 不越界且不覆盖 Composer |
| MPW-016 | P0 | Packaged macOS 可从 sidebar/title/empty topbar 拖拽，control/selection 不触发 |
| MPW-017 | P0 | Packaged macOS 双击只切换一次 maximize/restore，保持同一 WebView/sidecar/session |
| MPW-018 | P0 | Sidebar 展开/折叠在 maximize/restore 前后保持一个原生 titlebar centerline |
| MPW-019 | P0 | Close-to-tray、Dock reopen、single-instance、restored bounds 与 multi-display clamp 通过 |
| MPW-020 | P0 | Secret scan/negative test 不出现 credential、remote token、raw prompt/tool args 或未授权 host path |
| MPW-021 | P0 | 重复 project display name 通过 canonical workspace/endpoint context 保持不同，persistence/React key 不冲突 |
| MPW-022 | P0 | Project/conversation hover/focus card 在 300 ms 后显示指定 safe summary，project shortcut 可访问，并按全部已定义边界消失，不改变 selection/expansion |
| MPW-023 | P0 | Project/conversation row 预留稳定尾部 action；Edit/`...` 与 Pin/Archive 在 hover/focus 显示，不引起 layout shift 或 row activation，所有 action 有 keyboard path |
| MPW-024 | P0 | Project-section `+` 为纯加号并只打开 Create Project；相邻 section `...` 拥有 project/conversation sort 与 archived-project management |
| MPW-025 | P0 | Pin、rename、manual move、archive conversations 与 remove/restore 跨 restart 持久；stale-order guard 拒绝过期列表，并可证明 concurrent turn 不能在 busy check 与 archive/remove commit 之间进入 |
| MPW-026 | P0 | Remove from sidebar 为 non-destructive：file、workspace、session、transcript、artifact 与 accepted binding 全部不变 |
| MPW-027 | P1 | Reveal in Finder 与 persistent worktree action 按 capability 限定、path-safe、幂等；remote/browser/missing/busy project disabled 且显示 reason |
| MPW-028 | P0 | Light/dark 320/390/760/1440 px 下 hover card 与两类 menu 不越界、不覆盖 Composer；streaming 时无关 project header 零 commit |
| MPW-029 | P0 | 并发 sidebar-order 与无关 settings write 同时保留两者；写入中断时上一份有效 preference document 仍可读 |
| MPW-030 | P0 | 深浅主题的 390/760/1440 px 下，project/conversation label computed size 均为 12 px、line-height 为 1.35，活动项目字重不超过 500，时间元信息为 11 px 等宽数字，并且每行仍保持至少 28 px hit target，中文和长英文标题均不裁切高度 |
| MPW-031 | P0 | Pointer 在 mixed expanded/collapsed group 的 project/conversation anchor 间快速移动时，最多渲染一个 hover card；旧内容在下一次 dwell 前消失，menu 抑制 hover，离开当前 anchor/card 后在 corridor delay 内关闭 |
| MPW-032 | P0 | Hover/focus/menu transition 不改变任何可见 project/conversation row bounding box 与 expansion state；section label 为 11 px/500，inactive conversation 为 12 px/400 secondary text，仅选中 conversation 提升为 12 px/500 primary text |
| MPW-033 | P0 | Sessions 先返回、project projection 延迟时，初始导航只显示一套固定 project-loading skeleton 且 legacy conversation row 数为零；projection 完成后原子显示 project hierarchy，无中间 list 闪现，后续 refresh 保留已 settle 的 hierarchy |
| MPW-034 | P0 | 深浅主题的 320/390/760/1440 px 与 200% zoom 下，Project/Work location/Git branch 渲染为一个衔接在 Composer 背后的中性 shelf：同一 30 px 中心线、idle 无边框、Local 使用设备 icon、不换行/不越界、menu 语义真实、hover/focus/open 几何稳定，且 shelf 与 Composer 之间没有 canvas 色断层 |

要求执行：

```bash
cd manager/surfaces/gui
npm test -- --run
npm run build
npm run e2e -- \
  e2e/project-workbench.spec.ts \
  e2e/haas-activity.spec.ts \
  e2e/session-shell.spec.ts

cd ../../../
PYTHONPATH=manager manager/.venv/bin/pytest \
  manager/tests/test_project_workbench.py \
  manager/tests/test_project_api.py \
  manager/tests/test_haas_endpoint.py \
  manager/tests/test_haas_delegation.py -q
make gui-preview-smoke
make pre-commit
make full-check
```

Packaged acceptance 还要求 DMG build/smoke，以及 macOS 真机拖拽、双击 maximize/restore、sidebar
toggle geometry、local folder picker 与隔离 local/remote endpoint fixture。Project-navigation 真机流程还覆盖
Finder reveal、persistent-worktree confirmation、focus return 与 coarse-pointer fallback。Browser screenshot
不能替代 native window 证据。新增 project mutation/ordering module 行覆盖率至少 90%，path
validation 与 capability guard 至少 95%。

## 15. SDD/TDD 实施切片

| 顺序 | 切片 | 首个失败证据 | 出口条件 |
|---|---|---|---|
| 1 | Project store 与 migration | common-dir worktree、duplicate path、partial transaction、legacy session fixture | 单一 Project owner；additive normalized session projection |
| 2 | Project API 与 sidebar | API contract test；flat-list browser fixture | project-grouped virtual sidebar 与 create/edit entry |
| 3 | Create dialog 与 draft binding | folder cancel/duplicate/invalid/remote unavailable fixture | atomic local/remote creation 与 durable draft scope |
| 4 | Git 与 work location | non-Git/detached/dirty/worktree/endpoint failure fixture | safe context bar、read snapshot、guarded mutation、frozen acceptance |
| 5 | Command disclosure bugfix | reference-shaped chronological command fixture | compact list + 单一 inline Shell owner，无 duplicate output/side inspector |
| 6 | Native window lifecycle | Tauri command contract test + packaged failing walkthrough | macOS drag/double-click/maximize/reopen/bounds 通过 |
| 7 | Store 与 Manager API delta | migration、patch/reorder/archive 幂等、stale revision、Personal protection 与 non-destructive fixture | additive persisted field 与结构化 mutation contract |
| 8 | Shared overlay 与 row interaction | fake-timer hover/focus、corridor、Escape/scroll/unmount、event propagation、keyboard 与 render-count fixture | 单一 collision-aware overlay owner 与稳定 row geometry |
| 9 | Project 与 section menu | pin/edit/move/archive/remove/restore、sort preference、capability-disabled state 与 dialog reuse fixture | 完整参考形态 menu，无 duplicate form/renderer |
| 10 | Native capability | Finder reveal、persistent-worktree guard test 与 packaged smoke | path-safe capability action，无 shell/tool fallback |
| 11 | Sidebar 字体密度 | computed-style assertion 对过大/过重 label 与过小 hit target 先失败 | 双主题与全部支持宽度下，12 px navigation role、活动字重 <=500、11 px 时间 metadata、row >=28 px 全部通过 |
| 12 | 互斥 hover 与稳定尾部几何 | mixed-row 快速 hover 与 before/after bounding box 暴露重复 card 和宽度变化 | 单一 discriminated hover state + grid-stacked trailing content 保持单一 card 与稳定 row |
| 13 | 初始 project projection | 延迟 project response + 提前完成 sessions 暴露 legacy-list flash | 显式 unresolved/empty state 与固定 skeleton 保证 first paint 原子切换 |
| 14 | Composer context shelf 对齐 | Browser geometry/computed-style 断言暴露游离透明行、默认层级过弱、Local 代码括号 icon 与缺失 menu state 语义 | 单一 token-driven attached shelf 通过双主题、支持宽度、键盘/menu geometry、production preview 与 packaged-native 视觉对比 |
| 15 | Review 与 release gate | code-review、brooks-review、brooks-test manifest | 无 unresolved finding；full-check、preview、DMG/native 通过 |

P0/P1 表示实现顺序，不代表可静默裁剪。任何未完成 acceptance case 必须保持 open，并阻止该
change id 被宣称完成。
