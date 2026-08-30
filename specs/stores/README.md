# Stores 组件规格

Status: Draft
Last reviewed: 2026-08-26
Related specs: [Session Runtime](../session-runtime/README.md), [Event Log & SSE](../event-log-sse/README.md), [Harness Registry](../harness-registry/README.md), [Admission Control](../admission-control/README.md)

## 1. 组件定位

Stores 是 HaaS 的持久化事实源。它统一定义 registry、session、event log、idempotency 和 admission 计数等所有「store」的接口、schema、版本迁移、retention 和事务边界，避免各组件各自发明存储实现。

首期实现顺序：S2 提供 `in-memory` 实现支撑协议与 fake adapter；**生产默认选型为 SQLite（单机单进程）**，Postgres 作为多副本部署的预留 backend，接口不变。默认测试必须用 in-memory 隔离，不访问真实磁盘路径。

## 2. 来源与依据

| 来源 | 采用内容 |
|------|----------|
| Session Runtime | session/invocation/turn record、lease、idempotency reservation |
| Event Log & SSE | canonical event 持久化与 cursor read |
| Harness Registry | harness config 持久化 |
| Admission Control | 共享配额/速率计数，要求「部署内共享」 |

## 3. 上游与下游关系

| 方向 | 对象 | 关系 |
|------|------|------|
| 上游 | Session Runtime | 读写 session/invocation/turn/idempotency/lease |
| 上游 | Event Log & SSE | append/read event、cursor replay |
| 上游 | Harness Registry | 读写 harness config |
| 上游 | Admission Control | 读写配额/速率计数 |
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

class SessionStore(Protocol):
    async def get_session(self, key: SessionKey) -> SessionRecord | None: ...
    async def put_session(self, s: SessionRecord) -> SessionRecord: ...
    async def delete_session(self, key: SessionKey) -> None: ...
    async def count_sessions(self) -> int: ...
    async def put_invocation(self, inv: InvocationRecord) -> InvocationRecord: ...
    async def put_turn(self, t: TurnRecord) -> TurnRecord: ...
    async def acquire_lease(self, key: SessionKey, holder: str) -> Lease: ...
    async def release_lease(self, key: SessionKey, holder: str) -> None: ...

class EventLogStore(Protocol):
    async def append(self, e: CanonicalEventRecord) -> None: ...
    async def read_invocation(self, invocation_id: str, after: int) -> list[CanonicalEventRecord]: ...
    async def read_session(self, session_id: str, after_cursor: str | None) -> list[CanonicalEventRecord]: ...

class IdempotencyStore(Protocol):
    async def reserve(self, key_hash: str, request_hash: str) -> IdempotencyReservation: ...
    async def replay(self, key_hash: str) -> IdempotencyResult | None: ...
    async def complete(self, key_hash: str, result: IdempotencyResult) -> None: ...
    async def release(self, key_hash: str) -> None: ...

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
```

## 6. 数据模型

每个持久化 record 携带统一的元数据：

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
| Idempotency key | 24 小时或请求终态后释放 |
| Admission 计数 | 滚动窗口（窗口大小即维度定义） |

时间戳一律使用**毫秒 epoch（integer）**，与 ADK 公开 float 秒的转换只发生在投影层（见 `specs/README.md` 全局约定）。

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

`acquire_lease` 是 active-turn 互斥的基础：同一 `SessionKey` 只允许一个 holder 持 lease；lease 过期可被接管，接管者必须能 explain 前 holder 的 final state（fail closed）。

## 8. 安全与权限

- Store 只存脱敏后的数据；raw prompt、secret、完整 tool arg 不得入 store。
- Idempotency key 只存 hash；request hash 只存 hash。
- credential 以 `credentialRef` + `fingerprint` 形式存储，不存明文。
- 越权访问由上层 scope 检查保证，store 不做授权。

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
| event append 失败 | invocation 不得宣称 completed（Session Runtime 写 failure evidence） |
| migration 失败 | startup fail closed，不裸跑旧 schema |
| lease 过期 | 允许接管；接管前 inspect 前 holder 状态 |
| 部分写入 | 事务回滚；无跨 store 的分布式事务保证，必要时用 Outbox 补 |

## 11. 测试计划与验收

- Unit：各 record UPSERT、cursor read、idempotency replay/release。
- Integration：in-memory store 跑通 S2 协议与 S3 fake adapter 全链路。
- Recovery：写入后重启进程，session/event/idempotency 可从 store 恢复。
- Schema：forward migration 后旧数据可读；rollback 明确不支持。
- Security：store 全量审计不包含明文 secret（构造输入后反向断言）。
