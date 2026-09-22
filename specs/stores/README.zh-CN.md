# Stores 组件规格

[English](README.md) | **简体中文**

Status: Draft
Last reviewed: 2026-09-15
Change ID: long-task-model-proxy-stability
Related specs: [Session Runtime](../session-runtime/README.zh-CN.md), [Event Log & SSE](../event-log-sse/README.zh-CN.md), [Harness Registry](../harness-registry/README.zh-CN.md), [Harness Profile](../harness-profile/README.zh-CN.md), [Admission Control](../admission-control/README.zh-CN.md), [Manager Delegation](../manager-delegation/README.zh-CN.md)

## 1. 组件定位

Stores 是 HaaS 的持久化事实源。它统一定义 registry、session、event log、idempotency 和 admission 计数等所有「store」的接口、schema、版本迁移、retention 和事务边界，避免各组件各自发明存储实现。

首期实现顺序：S2 提供 `in-memory` 实现支撑协议与 fake adapter；**生产默认选型为 SQLite（单机单进程）**，Postgres 作为多副本部署的预留 backend，接口不变。默认测试必须用 in-memory 隔离，不访问真实磁盘路径。

## 2. 来源与依据

| 来源 | 采用内容 |
|------|----------|
| Session Runtime | session/invocation/turn record、approval wait、lease、idempotency reservation |
| Event Log & SSE | canonical event 持久化与 cursor read |
| Harness Registry / Harness Profile | harness identity、active profile pointer、profile revision 持久化 |
| Admission Control | 共享配额/速率计数，要求「部署内共享」 |
| Manager Delegation | delegated-session contract、policy snapshot、mount manifest、runtime generation 和 workspace lock |

## 3. 上游与下游关系

| 方向 | 对象 | 关系 |
|------|------|------|
| 上游 | Session Runtime | 读写 session/invocation/turn/idempotency/lease |
| 上游 | Event Log & SSE | append/read event、cursor replay |
| 上游 | Harness Registry | 读写 harness config |
| 上游 | Admission Control | 读写配额/速率计数 |
| 上游 | Manager Delegation | 读写 delegated-session contract 与 workspace lock 状态 |
| 下游 | 具体 backend | in-memory / SQLite / Postgres |

## 4. 职责边界

负责：

- 定义统一 `Store` 接口与分域 store 接口。
- 定义每个持久化对象的 schema 与 `schemaVersion`。
- 定义只向前（forward-only）的 schema migration。
- 定义 retention：event 保留期、session TTL、idempotency 键过期。
- 定义事务与 lease 语义（active-turn 互斥的分布式前提）。
- 保证 redaction 之后才落盘，store 不保存明文 secret。

不负责：

- 不鉴权、不做业务策略判断。
- 不保存 credential 明文（只存 ref/fingerprint，见 Security Boundary）。
- 不实现 auth provider 或 secret manager。

## 5. 核心接口

```python
class Store(Protocol):
    name: str
    schema_version: int
    async def begin(self) -> Transaction: ...
    async def migrate(self) -> None: ...

class RegistryStore(Protocol):
    async def save_harness(self, h: HarnessRecord) -> HarnessRecord: ...
    async def get_harness(self, harness_id: str) -> HarnessRecord | None: ...
    async def list_harnesses(self, account: AccountKey) -> list[HarnessRecord]: ...
    async def delete_harness(self, harness_id: str) -> None: ...
    async def save_profile(self, p: HarnessProfileRecord) -> HarnessProfileRecord: ...
    async def get_profile(self, profile_id: str) -> HarnessProfileRecord | None: ...
    async def list_profiles(self, account: AccountKey, harness_id: str | None, status: str | None, cursor: str | None, limit: int) -> ProfilePage: ...
    async def activate_profile(self, harness_id: str, profile_id: str, expected_version: int | None) -> HarnessRecord: ...

> `AccountKey` = `(tenantId, workspaceId)`，两者均可为 `None`（未绑定租户的
> 单节点部署）。Store 只按 key 做等值过滤，不做授权判断；scope 语义与越权
> 响应码由 Harness Registry 与 Security Boundary 决定（见
> [harness-registry](../harness-registry/README.zh-CN.md) §5.1.3）。

class SessionStore(Protocol):
    async def get_session(self, key: SessionKey) -> SessionRecord | None: ...
    async def put_session(self, s: SessionRecord) -> SessionRecord: ...
    async def delete_session(self, key: SessionKey) -> None: ...
    async def count_sessions(self) -> int: ...
    async def put_invocation(self, inv: InvocationRecord) -> InvocationRecord: ...
    async def get_invocation(self, invocation_id: str) -> InvocationRecord | None: ...
    async def put_turn(self, t: TurnRecord) -> TurnRecord: ...
    async def acquire_lease(self, key: SessionKey, holder: str, ttl_ms: int) -> Lease: ...
    async def renew_lease(self, key: SessionKey, holder: str, token: int, ttl_ms: int) -> Lease: ...
    async def assert_lease(self, key: SessionKey, holder: str, token: int) -> None: ...
    async def release_lease(self, key: SessionKey, holder: str, token: int | None = None) -> None: ...

class EventLogStore(Protocol):
    async def append(self, e: CanonicalEventRecord) -> None: ...
    async def read_invocation(self, key: SessionKey, invocation_id: str, after: int) -> list[CanonicalEventRecord]: ...
    async def read_session(self, key: SessionKey, after_cursor: str | None, limit: int) -> EventPage: ...

class IdempotencyStore(Protocol):
    async def reserve(self, key_hash: str, request_hash: str) -> IdempotencyReservation: ...
    async def replay(self, key_hash: str) -> IdempotencyResult | None: ...
    async def complete(self, key_hash: str, result: IdempotencyResult) -> None: ...
    async def release(self, key_hash: str) -> None: ...

Session store 独立于 profile 历史保留私有非 secret `effectiveProfile` snapshot 及其解析时间；公开投影不得包含其内容。Invocation store 同样保留用于精确 Pause/Continue 恢复的私有非 secret `executionContext` 快照；它包含生效 sandbox、policy 与 principal identity，但不得包含 credential、raw prompt 或完整 tool arguments，公开投影不得包含其内容。Profile-rebind 幂等性按 principal、完整 session identity 和 operation 隔离，严格匹配请求并重放原响应。

Execution mutation 的 `IdempotencyResult` 是持久协议状态：

```json
{
  "accepted": true,
  "invocationId": "inv_abc",
  "httpStatus": 200,
  "events": [],
  "error": null,
  "completedAtMs": 1786401000000
}
```

接受前 reservation 可释放且不保留 result。接受后，正常
completed/failed/incomplete/interrupted/cancelled result 一律使用 `httpStatus=200` 并保留有序 ADK
event sequence。若接受后 terminal event 持久化失败，则保留 `accepted=true`、
invocation id、`httpStatus=503` 与安全 `haas_store_unavailable` error，确保 replay 不会
启动第二次执行。Store 不得把 accepted adapter failure 持久化为 HTTP 502。

class AdmissionStore(Protocol):
    async def incr_window(self, bucket: str, now_ms: int, window_ms: int) -> int: ...
    async def acquire_quota(self, bucket: str, limit: int) -> bool: ...
    async def release_quota(self, bucket: str) -> None: ...

> AdmissionStore 的窗口大小与配额上限由 Admission Control（组件）注入并解释，
> Stores 只提供无状态的计数原语：`incr_window` 返回当前窗口内的计数，
> `acquire_quota` 在计数低于 `limit` 时占一个配额，`release_quota` 归还一个。
> S2 的 in-memory 后端可为同步实现；`async` 签名是面向 SQLite/Postgres 后端的
> 合同，首个 I/O 后端落地时统一。
>
> `count_sessions` 只服务 Observability 的 `activeSessions` 摘要（低基数聚合），
> 不返回 record 内容、不做 scope 过滤、不作为业务列举入口。跨 user 的分页列举
> 由 `GET /v1/haas/sessions` 单独定义，落在后续阶段。

class DelegationStore(Protocol):
    async def put_delegated_session(self, record: DelegatedSessionRecord) -> DelegatedSessionRecord: ...
    async def get_delegated_session(self, delegated_session_id: str) -> DelegatedSessionRecord | None: ...
    async def get_by_manager_session(self, manager_session_id: str) -> DelegatedSessionRecord | None: ...
    async def update_runtime_generation(self, delegated_session_id: str, runtime: DelegatedRuntimeRecord) -> None: ...
    async def put_approval(self, approval: ApprovalRecord) -> ApprovalRecord: ...
    async def resolve_approval(self, approval_id: str, decision: ApprovalDecision) -> ApprovalRecord: ...
    async def list_approvals(self, key: SessionKey, status: str, cursor: str | None, limit: int) -> ApprovalPage: ...
    async def acquire_workspace_lock(self, canonical_workspace: str, delegated_session_id: str, access: str, ttl_ms: int) -> WorkspaceLockResult: ...
    async def release_workspace_lock(self, canonical_workspace: str, delegated_session_id: str) -> None: ...
```

## 6. 数据模型

每个持久化 record 携带统一 metadata。`CanonicalEventRecord` 还要持久化稳定
`type` 和经过校验的 type-specific `haas` metadata；不得持久化 adapter
`nativeType`。Public native projection 会剥离内部 `userId`、`adapterId`、
`schemaVersion`、`redactionApplied`：

```json
{
  "schemaVersion": 1,
  "createdAtMs": 1786400000000,
  "updatedAtMs": 1786400000000
}
```

Retention 默认值：

| 数据 | 默认保留 |
|------|----------|
| Event | 与 session 保留期同或按配置 |
| Session | TTL 可配，默认 30 天；`expiresAtMs` 过期不可读 |
| Delegated session contract | 与 session 保留期同；idle container TTL 不得删除它 |
| Harness profile revision | 删除 harness 后仍按审计保留期保留；active/retired revision 不原地修改 |
| Approval record | 与 invocation/event 保留期同 |
| Workspace lock | lease-backed；只在确认 terminal/cleanup 状态后或 lock TTL takeover 后过期 |
| Idempotency reservation/result | 接受前失败释放 reservation；一旦 accepted，terminal result 或 integrity-failure envelope 默认保留 24 小时 |
| Admission 计数 | 滚动窗口（窗口大小即维度定义） |

时间戳一律使用**毫秒 epoch（integer）**，与 ADK 公开 float 秒的转换只发生在投影层（见 `specs/README.md` 全局约定）。

`CanonicalEventRecord` 使用 schema version 2。Version 1 到 version 2 的 forward
migration 由 Event Log & SSE §6.3.1 定义，必须在提供 native event replay 前完成。
Migration 必须幂等，保留原 id/order/content/actions；语义不明确的历史 record 映射为
`haas.adapter.event_unparsed`，不得虚构语义。

持久化 record 解码必须对加法字段前向兼容。较新 writer 可能存入当前进程 dataclass
尚不认识的可选字段；SQLite/Postgres loader 必须忽略未知 key，记录安全 migration
诊断，归一化已接受 alias，并对缺失可选字段使用 dataclass 默认值。已知 alias 属于
migration 合同：`control_state` 与 `controlState` 在构造 `SessionRecord` 前归一为同一
session control 字段。已知字段类型不符、未知 schema version、非法 terminal state 或
secret-shaped value 仍必须 fail closed。单条历史 record 不可读不得阻止 sidecar 提供
health/control API；execution readiness 可降级，直到相关 record 被隔离或迁移。

未发布 2026-08-26 baseline 的 invocation/delegation migration 也只向前：

- 旧 invocation 有 `startedAtMs` 时用它填充 `acceptedAtMs`，否则使用 record
  `createdAtMs`；保留 terminal/running status；
- 旧 `haas_bound` delegated contract 使用同一 HaaS session 最早持久 invocation 作为
  `acceptedInvocationId`，并使用其 acceptance timestamp；
- 若不存在 invocation，则迁移为 `prepared`，不得保留 `haas_bound`；
- 歧义/不一致 record 必须使 migration 失败，不得虚构 accepted work。
- 生命周期字段只做加法：session 缺少 `controlState` 时默认 `idle`，但存在 active invocation 时必须先对账；invocation/turn 缺少 continuation link 时默认为 null。`interrupted` 与其他 terminal record 使用相同 retention 和不可变保证。

Event log 记录的 session 读取索引必须使用完整 `SessionKey`
`(appName, userId, sessionId)`；invocation 读取索引必须使用
`(appName, userId, sessionId, invocationId)`。Invocation-scoped record 要求非空
invocation/turn id 与 gapless invocation sequence；session-scoped lifecycle record 使用
null invocation/turn id 和独立 gapless session-lifecycle sequence。Session replay 通过
session-scoped `eventId` 对两个 namespace 排序。caller-supplied 的裸
`sessionId` 不具备全局唯一性，禁止作为 event replay 的 store key。

## 7. 运行模型与状态机

```text
write path:
  -> validate + redact
  -> begin tx
  -> apply record (UPSERT)
  -> commit
  -> notify subscribers (event log only)

migration:
  store opens -> check schemaVersion -> apply forward migrations -> ready
```

`acquire_lease` 是 active-turn 互斥的基础：同一 `SessionKey` 只允许一个 holder 持 lease。返回的 `Lease` 包含不透明且单调递增的 fencing token（`token`）。所有代表 active turn 的写入都必须用 holder 与 token 做保护；如果当前 lease 不存在、已过期、归属其他 holder，或 token 不一致，写入必须 fail closed，不能追加 event 或覆盖 session/invocation/turn 状态。

运行中的 holder 必须在过期前续租。`renew_lease` 仅在当前 holder/token 匹配时成功，并延长 `expiresAtMs` 且不改变 token。过期 lease 可被新 holder 接管，接管必须分配更大的 token。新 holder 必须能 explain 前 holder 的 final state，否则 fail closed。release 是 best-effort，只有 holder 与（如提供）token 匹配当前 lease 时才删除 lease。

Delegated-session record 是 manager-delegated execution 的恢复事实源。container id、
process id、socket path 或 port allocation 都不是持久事实。容器被 idle TTL 销毁后，
Stores 仍保留 delegated-session contract、policy snapshot、mount manifest、approval
history、HaaS session id、native session reference 和 container generation。

Harness profile revision 是运行配置事实源。Store 必须以 append-only revision 保存
profile 内容、version、status、fingerprint 和安全 validation findings；`version`
只递增、不回滚。`activate_profile` 必须在同一事务中校验目标 profile 已通过 validation、
更新 harness active profile pointer、并把旧 active revision 标为 `retired`。无法留档历史的
部署可以每个 harness 只保留最新 revision。`EffectiveHarnessProfile` snapshot 存在
SessionRecord 中，后续 active pointer 变化不得改写已有 session。

Workspace lock 按 canonical host workspace 与 access mode 建模。对 `rw` delegated
session，同一时间只允许一个 active holder；`ro` lock 可共享。过期接管前必须确认
前 holder 已进入 terminal 或 cleanup 状态，否则 fail closed。

### 7.1 配置与恢复事务

持久化完整解析 desired/applied 配置、内容引用、授权证据、desiredRevision/appliedRevision、pending update 和 lastPolicyUpdateResult，public pending summary 不能代替真实目标。接受更新原子推进 desiredRevision 并写 receipt；fenced runtime 验证成功后才推进 appliedRevision。Update id/request hash receipt 保留到 session 删除，防止旧重试覆盖新配置。重启自动 reconciliation，不依赖下一 turn；见 Manager Delegation §5.1.1。

执行幂等结果在 acceptedAtMs 后 24h 过期，暴露 idempotencyExpiresAtMs/Idempotency-Expires-At。非终态 reservation 不驱逐；结果过期后 scoped key-hash/invocation/expiry tombstone 保留至 session retention，旧 key 返回 410 haas_idempotency_expired。新 attempt 使用新 key。按 principal/operation 隔离 key，policy/approval mutation 不套执行 auto-new-turn。

Session volume 跨 TTL/重建保留 native state 和完整材料化内容；control store 保存 worker execution/generation mapping 与去重 receipt。Profile 清理不能删除 pending/applied session 引用的内容。Artifact Store 独立于 worker lifetime 保存发布字节。Delete 先撤销 visibility/admission，收敛 writer 后清 volume/content；不确定清理保持 fencing。Private native 文件遵循 Security Boundary §7.1，不属于 public event-log storage。

旧 delegated record 仅在解析确切配置并确认 native state 后迁移为 desiredRevision=appliedRevision=1；证据缺失 fail closed，不绑定任意当前 profile。新字段/receipt 前向迁移及重启测试通过前，不能宣称 2026-09-10 conformance。

## 8. 安全与权限

- Store 只存脱敏后的数据；raw prompt、secret、完整 tool arg 不得入 store。
- Idempotency key 只存 hash；request hash 只存 hash。
- credential 以 `credentialRef` + `fingerprint` 形式存储，不存明文。
- 越权访问由上层 scope 检查保证，store 不做授权。
- Store 层 event index 仍必须通过完整 session scope key 实现命名空间隔离；
  只在 API 层过滤是不充分的。

### 8.1 Manager 状态备份与恢复

Manager V1 backup 是离线、用户自主管理的迁移机制，用于迁移会话历史、memory
索引数据、persona、偏好设置与计划任务定义。它不是实时 runtime checkpoint，
不得宣称能保留正在运行的 turn、活跃 HaaS child process、connector OAuth
session、sidecar token 或系统通知状态。

备份产物是 ZIP 文件，包含：

- `manifest.json`，字段包括 `format="openharness-state-backup"`、`version=1`、
  `createdAt`、`source`、`included`、`excluded` 和 warning。
- 以 Manager state directory 为根的可迁移文件白名单。
- 对合法 SQLite 数据库必须使用 SQLite backup API 复制，避免 WAL 模式下产生
  撕裂归档。

V1 可迁移白名单刻意收窄：

| 类别 | 纳入 |
|------|------|
| 会话 | `conversations/*.jsonl`，文件名必须是安全单级路径组件 |
| SQLite 数据 | `coworker.db`、`automation.db`、`journal.db`、`teams.db`、`chat.db` |
| 用户设置 | `prefs.json`、`memory-settings.json`、`personas.json`、`persona_connections.json`、`session_connections.json`、`session_skills.json`、`inbox_routing.json` |
| 团队附件 | `attachments/**` 中位于附件根目录下的普通文件 |

导出器必须排除已知携带 secret、机器绑定或 runtime-resume 语义的文件：
`secrets.json`、`.env`、`board-tokens.json`、`haas-token`、`sidecar-*.token`、
`haas-supervised.yaml`、`haas.db*`、`logs/**`、Python cache、临时文件、socket、
FIFO、设备文件、symlink，以及所有未命中白名单的路径。Manifest 只记录排除类别，
不得记录 secret 值。导出器必须拒绝把备份产物写到源 state directory 内。

Restore 只接受 V1 ZIP；所有 entry 必须是相对归一化路径，不得包含 `..`、不得是
绝对路径、不得是 link，并且必须满足配置的 entry 数量与总大小限制。默认只恢复到
全新的空 state directory。覆盖非空目标必须显式 force；即使 force，也必须先写入
同级临时 staging directory，完成校验和恢复后安全转换，再原子替换目标，或保持旧
目标不变。

恢复后的安全转换是强制的：

- 清空 `sessions` index 中的 session-scoped grants，让迁移后的会话重新询问授权；
- 禁用所有计划任务并清空 `next_run`，导入的自动化在用户重新启用前不得执行；
- 将导入时仍处于 running 的 task run 标记为带 restore-required 说明的 `error`；
- 保持 task/session id 和非执行类 project binding 便于检查，但移除 Manager
  `haas_delegation` runtime binding 与 runtime token。

CLI/API 必须返回纳入文件、排除类别、总字节和 warning，不得打印原始会话内容、
tool arguments、credential、token 或完整附件路径。Backup 与 restore 都是本地文件
系统操作，不访问云服务或模型 provider。

## 9. 可观测性

Metrics：

- `haas_store_op_duration_ms{store,op,status}`
- `haas_store_open_total{backend,status}`
- `haas_store_migration_total{from_version,status}`
- `haas_store_retention_evicted_total{store}`

Logs：

- `haas.store.migrated`
- `haas.store.unavailable`
- `haas.store.retention_evicted`

## 10. 失败与恢复

| 场景 | 行为 |
|------|------|
| store 不可用 | 新请求 fail closed；已冻结 session 的 read 可降级为不可用 |
| event append 失败 | invocation 不得宣称 completed；Session Runtime 在权威状态 store 持久化 `failed`，用安全错误 envelope 完成幂等记录，并在 Event Log 之外记录 fallback diagnostics |
| migration 失败 | startup fail closed，不裸跑旧 schema |
| 当前 binary 不认识 additive record field | 忽略未知 key 并记录安全诊断，control API 继续可用；不得 crash startup |
| 持久数据出现已知字段 alias | dataclass 构造前归一化 alias，下次写入保留 canonical 字段 |
| 已知字段类型/取值非法 | 隔离记录或使 execution readiness fail closed；不得虚构替代值 |
| lease 过期 | 仅允许使用更新的 fencing token 接管；过期 holder 不能 append event 或覆盖 record，接管者必须先 inspect/explain 前 holder 状态 |
| delegated runtime record 缺失 | restore fail closed；不得从 live container 反推配置 |
| workspace lock holder 不明确 | 不授予第二个 `rw` lock；返回 `haas_workspace_lock_busy` 或 timeout |
| approval decision 与已存储的已解析决定相同 | 幂等返回既有记录，不重复回复 native request |
| approval decision 与已解析或 terminal-cancelled 记录冲突 | 返回 `haas_approval_state_conflict` |
| invocation 进入 `failed`、`incomplete`、`interrupted` 或 `cancelled` | 在同一 store 临界区内取消该 invocation 所有仍 waiting 的 approval/input，避免 restart/replay 暴露 stale interaction card |
| 部分写入 | 事务回滚；无跨 store 的分布式事务保证，必要时用 Outbox 补 |
| 备份包包含不安全路径、symlink、超限 payload 或不支持版本 | 写入恢复状态前拒绝归档 |
| restore 会覆盖非空目标且未显式 force | fail closed，并保持目标不变 |
| 恢复的自动化包含未来启用计划 | 禁用计划任务，要求用户显式重新启用后才能调度 |
| 恢复的 session 携带 standing grants | 清空 grants，并在恢复后的安装中重新请求授权 |

## 11. 测试计划与验收

- Unit：各 record UPSERT、稳定 event type/typed metadata 持久化、native type 泄漏拒绝、invocation/session-lifecycle sequence 分配、cursor read、lease acquire/renew/assert/release（含 fencing 拒绝）、idempotency replay/release。 Accepted failure result 持久化/replay HTTP 200 与相同 events；pre-acceptance reservation 释放；integrity-failure envelope replay HTTP 503 且不重复执行。
- Unit：profile revision append-only、activate pointer 原子性、profile fingerprint 稳定、非 draft 更新拒绝、session snapshot 不随 active pointer 漂移。
- Integration：in-memory store 跑通 S2 协议与 S3 fake adapter 全链路。
- Recovery：写入后重启进程，session/event/idempotency 可从 store 恢复。
- Recovery：idle TTL 清理和进程重启后，delegated-session contract、policy snapshot、approval wait 和 workspace-lock state 可恢复。
- Concurrency：store-backed workspace lock 阻止同一 canonical workspace 的两个 active `rw` delegated session。
- Schema：forward migration 后旧数据可读；rollback 明确不支持。 Event schema v1 fixture 必须确定性迁移到 v2，包括安全 unparsed fallback。
- Lifecycle schema：以加法方式迁移缺失的 `controlState`、snake_case/camelCase alias
  与 continuation link；未知可选字段不得导致 startup crash；重启和 retention 后仍保留
  interrupted 源记录及关联后继记录；operation receipt 保证重复 pause/continue key 不触发
  第二次 native action。
- Security：store 全量审计不包含明文 secret（构造输入后反向断言）。
- Manager backup：用包含 conversations、SQLite 数据、secrets、tokens、logs 与不安全
  文件名的 fixture 做 V1 archive 单测；断言归档只包含白名单 entry，manifest 只记录
  排除类别。
- Manager restore：单测覆盖恢复到空目录、拒绝路径穿越/超限/不支持版本、非 force
  不覆盖、session grants 清空、计划任务禁用，以及 running run 的恢复标记。

### Manager 自动化恢复边界

Manager automation.db 保留既有 task/run JSON schema，执行前持久化本次计划消耗。启动时在同一事务内将未知 running 记录标记为带 recovery_required 的 error 并停用所属计划，原 session ID 保留用于核对。不删除历史、不重放执行。HaaS store 接口不变。
