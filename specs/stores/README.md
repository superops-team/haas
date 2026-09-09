# Stores Component Specification

**English** | [简体中文](README.zh-CN.md)

Status: Draft
Last reviewed: 2026-09-07
Related specs: [Session Runtime](../session-runtime/README.md), [Event Log & SSE](../event-log-sse/README.md), [Harness Registry](../harness-registry/README.md), [Admission Control](../admission-control/README.md), [Manager Delegation](../manager-delegation/README.md)

## 1. Component Role

Stores is HaaS's persistent source of truth. It defines common interfaces, schemas, version migrations, retention, and transaction boundaries for all stores, including registry, session, event log, idempotency, and admission counters, so that individual components do not invent their own storage implementations.

Initial implementation sequence: S2 provides an `in-memory` implementation to support the protocol and fake adapter. **SQLite is the production default for a single-host, single-process deployment.** Postgres is a reserved backend for multi-replica deployments, with no interface changes. Tests MUST use in-memory isolation by default and MUST NOT access real disk paths.

## 2. Sources and Rationale

| Source | Adopted concepts |
|--------|------------------|
| Session Runtime | Session/invocation/turn records, approval waits, leases, and idempotency reservations |
| Event Log & SSE | Canonical event persistence and cursor reads |
| Harness Registry | Harness configuration persistence |
| Admission Control | Shared quota/rate counters that MUST be shared within a deployment |
| Manager Delegation | Delegated-session contracts, policy snapshots, mount manifests, runtime generations, and workspace locks |

## 3. Upstream and Downstream Relationships

| Direction | Component | Relationship |
|-----------|-----------|--------------|
| Upstream | Session Runtime | Reads and writes sessions/invocations/turns/idempotency/leases |
| Upstream | Event Log & SSE | Appends and reads events; replays cursors |
| Upstream | Harness Registry | Reads and writes harness configurations |
| Upstream | Admission Control | Reads and writes quota/rate counters |
| Upstream | Manager Delegation | Reads and writes delegated-session contracts and workspace lock state |
| Downstream | Concrete backend | in-memory / SQLite / Postgres |

## 4. Responsibility Boundaries

Responsibilities:

- Define a unified `Store` interface and domain-specific store interfaces.
- Define the schema and `schemaVersion` for every persistent object.
- Define forward-only schema migrations.
- Define retention for events, session TTLs, and idempotency-key expiry.
- Define transaction and lease semantics, which are prerequisites for distributed active-turn mutual exclusion.
- Ensure data is persisted only after redaction; stores do not retain plaintext secrets.

Non-responsibilities:

- Does not authenticate or make business-policy decisions.
- Does not store plaintext credentials; only references and fingerprints are stored, as specified by Security Boundary.
- Does not implement an auth provider or secret manager.

## 5. Core Interfaces

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

> `AccountKey` = `(tenantId, workspaceId)`; either value MAY be `None` for a
> single-node deployment not bound to a tenant. Store performs only equality
> filtering by key and makes no authorization decisions. Harness Registry and
> Security Boundary define scope semantics and unauthorized-access response codes;
> see [harness-registry](../harness-registry/README.md) §5.1.3.

class SessionStore(Protocol):
    async def get_session(self, key: SessionKey) -> SessionRecord | None: ...
    async def put_session(self, s: SessionRecord) -> SessionRecord: ...
    async def delete_session(self, key: SessionKey) -> None: ...
    async def count_sessions(self) -> int: ...
    async def put_invocation(self, inv: InvocationRecord) -> InvocationRecord: ...
    async def put_turn(self, t: TurnRecord) -> TurnRecord: ...
    async def acquire_lease(self, key: SessionKey, holder: str, ttl_ms: int) -> Lease: ...
    async def renew_lease(self, key: SessionKey, holder: str, token: int, ttl_ms: int) -> Lease: ...
    async def assert_lease(self, key: SessionKey, holder: str, token: int) -> None: ...
    async def release_lease(self, key: SessionKey, holder: str, token: int | None = None) -> None: ...

class EventLogStore(Protocol):
    async def append(self, e: CanonicalEventRecord) -> None: ...
    async def read_invocation(self, key: SessionKey, invocation_id: str, after: int) -> list[CanonicalEventRecord]: ...
    async def read_session(self, key: SessionKey, after_cursor: str | None) -> list[CanonicalEventRecord]: ...

class IdempotencyStore(Protocol):
    async def reserve(self, key_hash: str, request_hash: str) -> IdempotencyReservation: ...
    async def replay(self, key_hash: str) -> IdempotencyResult | None: ...
    async def complete(self, key_hash: str, result: IdempotencyResult) -> None: ...
    async def release(self, key_hash: str) -> None: ...

class AdmissionStore(Protocol):
    async def incr_window(self, bucket: str, now_ms: int, window_ms: int) -> int: ...
    async def acquire_quota(self, bucket: str, limit: int) -> bool: ...
    async def release_quota(self, bucket: str) -> None: ...

> Admission Control injects and interprets the window size and quota limit for
> AdmissionStore. Stores supplies only stateless counting primitives:
> `incr_window` returns the count in the current window, `acquire_quota` consumes
> one quota unit when the count is below `limit`, and `release_quota` returns one.
> The S2 in-memory backend MAY implement these synchronously. The `async`
> signatures are the contract for SQLite/Postgres backends and will be unified
> when the first I/O backend is implemented.
>
> `count_sessions` serves only the low-cardinality `activeSessions` summary for
> Observability. It does not return record contents, apply scope filtering, or
> serve as a business listing entry point. Paginated listing across users is
> defined separately by `GET /v1/haas/sessions` in a later phase.

class DelegationStore(Protocol):
    async def put_delegated_session(self, record: DelegatedSessionRecord) -> DelegatedSessionRecord: ...
    async def get_delegated_session(self, delegated_session_id: str) -> DelegatedSessionRecord | None: ...
    async def get_by_manager_session(self, manager_session_id: str) -> DelegatedSessionRecord | None: ...
    async def update_runtime_generation(self, delegated_session_id: str, runtime: DelegatedRuntimeRecord) -> None: ...
    async def put_approval(self, approval: ApprovalRecord) -> ApprovalRecord: ...
    async def resolve_approval(self, approval_id: str, decision: ApprovalDecision) -> ApprovalRecord: ...
    async def acquire_workspace_lock(self, canonical_workspace: str, delegated_session_id: str, access: str, ttl_ms: int) -> WorkspaceLockResult: ...
    async def release_workspace_lock(self, canonical_workspace: str, delegated_session_id: str) -> None: ...
```

## 6. Data Model

Every persistent record carries common metadata:

```json
{
  "schemaVersion": 1,
  "createdAtMs": 1786400000000,
  "updatedAtMs": 1786400000000
}
```

Default retention values:

| Data | Default retention |
|------|-------------------|
| Event | Same as session retention, or configurable |
| Session | Configurable TTL, 30 days by default; unreadable after `expiresAtMs` |
| Delegated session contract | Same as session retention; idle container TTL MUST NOT delete it |
| Approval record | Same as invocation/event retention |
| Workspace lock | Lease-backed; expires only after confirmed terminal/cleanup state or lock TTL takeover |
| Idempotency key | 24 hours, or released after the request reaches a terminal state |
| Admission counters | Rolling window, whose size defines the dimension |

All timestamps use **integer epoch milliseconds**. Conversion to the public ADK float-seconds representation occurs only in the projection layer; see the global convention in `specs/README.md`.

Event-log records MUST be indexed by the complete `SessionKey`
`(appName, userId, sessionId)` for session reads and by
`(appName, userId, sessionId, invocationId)` for invocation reads. A bare
caller-supplied `sessionId` is not unique and MUST NOT be used as a store key
for event replay.

## 7. Runtime Model and State Machine

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

`acquire_lease` is the basis for active-turn mutual exclusion: only one holder MAY hold a lease for a given `SessionKey`. The returned `Lease` includes an opaque, monotonically increasing fencing token (`token`). Every write performed on behalf of an active turn MUST be guarded by the holder and token. If the current lease is absent, expired, held by another holder, or has a different token, the write MUST fail closed instead of appending events or overwriting session/invocation/turn state.

A running holder MUST renew its lease before expiry. `renew_lease` succeeds only for the current holder/token and extends `expiresAtMs` without changing the token. An expired lease MAY be taken over by a new holder, and takeover MUST allocate a strictly newer token. The new holder MUST be able to explain the previous holder's final state; otherwise it MUST fail closed. Release is best-effort and MUST remove the lease only when holder and, when supplied, token match the current lease.

Delegated-session records are the recovery source for manager-delegated execution. A
container id, process id, socket path, or port allocation is not a durable fact. When a
container is destroyed by idle TTL, Stores retain the delegated-session contract,
policy snapshot, mount manifest, approval history, HaaS session id, native session
reference, and container generation.

Workspace locks are keyed by canonical host workspace and access mode. For `rw`
delegated sessions, only one active holder is allowed; `ro` locks may be shared.
Takeover after expiry MUST first verify that the previous holder reached terminal or
cleanup state, or fail closed.

## 8. Security and Authorization

- Store persists only redacted data. Raw prompts, secrets, and complete tool arguments MUST NOT enter a store.
- Only hashes of idempotency keys and request hashes are stored.
- Credentials are stored as `credentialRef` + `fingerprint`, never as plaintext.
- The upper layer enforces scope checks for unauthorized access; Store performs no authorization.
- Store-level event indexes still enforce namespace separation by requiring the
  full session scope key on reads; API-layer filtering alone is insufficient.

## 9. Observability

Metrics:

- `haas_store_op_duration_ms{store,op,status}`
- `haas_store_open_total{backend,status}`
- `haas_store_migration_total{from_version,status}`
- `haas_store_retention_evicted_total{store}`

Logs:

- `haas.store.migrated`
- `haas.store.unavailable`
- `haas.store.retention_evicted`

## 10. Failure and Recovery

| Scenario | Behavior |
|----------|----------|
| Store unavailable | New requests fail closed; reads for frozen sessions MAY degrade to unavailable |
| Event append fails | The invocation MUST NOT claim `completed`; Session Runtime writes failure evidence |
| Migration fails | Startup fails closed; the service MUST NOT run against an old schema |
| Lease expires | Takeover is allowed only with a newer fencing token; stale holders cannot append events or overwrite records and the new holder must inspect/explain previous state before proceeding |
| Delegated runtime record is missing | Restore fails closed; do not infer configuration from a live container |
| Workspace lock holder is ambiguous | Do not grant a second `rw` lock; return `haas_workspace_lock_busy` or timeout |
| Approval decision conflicts with resolved record | Return `haas_approval_state_conflict` |
| Partial write | Roll back the transaction; no distributed transaction is guaranteed across stores, and use an Outbox when necessary |

## 11. Test Plan and Acceptance Criteria

- Unit: UPSERT for each record, cursor reads, lease acquire/renew/assert/release including fencing rejection, and idempotency replay/release.
- Integration: use the in-memory store to exercise the complete S2 protocol and S3 fake-adapter path.
- Recovery: after writing and restarting the process, recover session/event/idempotency data from the store.
- Recovery: after idle TTL cleanup and process restart, recover the delegated-session contract, policy snapshot, approval waits, and workspace-lock state.
- Concurrency: store-backed workspace lock prevents two active `rw` delegated sessions for the same canonical workspace.
- Schema: old data remains readable after a forward migration; rollback is explicitly unsupported.
- Security: a full store audit contains no plaintext secrets, verified with negative assertions after constructed inputs.
