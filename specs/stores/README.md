# Stores Component Specification

**English** | [简体中文](README.zh-CN.md)

Status: Draft
Last reviewed: 2026-08-26
Related specs: [Session Runtime](../session-runtime/README.md), [Event Log & SSE](../event-log-sse/README.md), [Harness Registry](../harness-registry/README.md), [Admission Control](../admission-control/README.md)

## 1. Component Role

Stores is HaaS's persistent source of truth. It defines common interfaces, schemas, version migrations, retention, and transaction boundaries for all stores, including registry, session, event log, idempotency, and admission counters, so that individual components do not invent their own storage implementations.

Initial implementation sequence: S2 provides an `in-memory` implementation to support the protocol and fake adapter. **SQLite is the production default for a single-host, single-process deployment.** Postgres is a reserved backend for multi-replica deployments, with no interface changes. Tests MUST use in-memory isolation by default and MUST NOT access real disk paths.

## 2. Sources and Rationale

| Source | Adopted concepts |
|--------|------------------|
| Session Runtime | Session/invocation/turn records, leases, and idempotency reservations |
| Event Log & SSE | Canonical event persistence and cursor reads |
| Harness Registry | Harness configuration persistence |
| Admission Control | Shared quota/rate counters that MUST be shared within a deployment |

## 3. Upstream and Downstream Relationships

| Direction | Component | Relationship |
|-----------|-----------|--------------|
| Upstream | Session Runtime | Reads and writes sessions/invocations/turns/idempotency/leases |
| Upstream | Event Log & SSE | Appends and reads events; replays cursors |
| Upstream | Harness Registry | Reads and writes harness configurations |
| Upstream | Admission Control | Reads and writes quota/rate counters |
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
| Idempotency key | 24 hours, or released after the request reaches a terminal state |
| Admission counters | Rolling window, whose size defines the dimension |

All timestamps use **integer epoch milliseconds**. Conversion to the public ADK float-seconds representation occurs only in the projection layer; see the global convention in `specs/README.md`.

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

`acquire_lease` is the basis for active-turn mutual exclusion: only one holder MAY hold a lease for a given `SessionKey`. An expired lease MAY be taken over, and the new holder MUST be able to explain the previous holder's final state; otherwise it MUST fail closed.

## 8. Security and Authorization

- Store persists only redacted data. Raw prompts, secrets, and complete tool arguments MUST NOT enter a store.
- Only hashes of idempotency keys and request hashes are stored.
- Credentials are stored as `credentialRef` + `fingerprint`, never as plaintext.
- The upper layer enforces scope checks for unauthorized access; Store performs no authorization.

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
| Lease expires | Takeover is allowed; inspect the previous holder's state before takeover |
| Partial write | Roll back the transaction; no distributed transaction is guaranteed across stores, and use an Outbox when necessary |

## 11. Test Plan and Acceptance Criteria

- Unit: UPSERT for each record, cursor reads, and idempotency replay/release.
- Integration: use the in-memory store to exercise the complete S2 protocol and S3 fake-adapter path.
- Recovery: after writing and restarting the process, recover session/event/idempotency data from the store.
- Schema: old data remains readable after a forward migration; rollback is explicitly unsupported.
- Security: a full store audit contains no plaintext secrets, verified with negative assertions after constructed inputs.
