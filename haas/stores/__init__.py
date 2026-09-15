"""HaaS persistent stores (specs/stores/README.md).

S2 starts with an in-memory backend. SQLite/Postgres backends share the same
domain interfaces and are introduced in later stages.
"""

from haas.stores.memory import (
    ApprovalNotFoundError,
    ApprovalRecord,
    ApprovalStateConflictError,
    CanonicalEventRecord,
    CursorNotFoundError,
    DelegatedRuntimeRecord,
    DelegatedSessionNotFoundError,
    DelegatedSessionRecord,
    HarnessRecord,
    IdempotencyConflictError,
    IdempotencyExpiredError,
    IdempotencyReservation,
    InputRequestNotFoundError,
    InputRequestRecord,
    InputRequestStateConflictError,
    InvocationRecord,
    Lease,
    LeaseConflictError,
    LeaseFencingError,
    MemoryStore,
    ProfileRecord,
    ProviderConfig,
    SessionRecord,
    TurnRecord,
    WorkspaceLockResult,
)
from haas.stores.sqlite import SQLiteStore

__all__ = [
    "ApprovalNotFoundError",
    "ApprovalRecord",
    "ApprovalStateConflictError",
    "CanonicalEventRecord",
    "CursorNotFoundError",
    "DelegatedRuntimeRecord",
    "DelegatedSessionNotFoundError",
    "DelegatedSessionRecord",
    "HarnessRecord",
    "IdempotencyConflictError",
    "IdempotencyExpiredError",
    "IdempotencyReservation",
    "InvocationRecord",
    "InputRequestNotFoundError",
    "InputRequestRecord",
    "InputRequestStateConflictError",
    "Lease",
    "LeaseConflictError",
    "LeaseFencingError",
    "MemoryStore",
    "ProfileRecord",
    "ProviderConfig",
    "SessionRecord",
    "SQLiteStore",
    "TurnRecord",
    "WorkspaceLockResult",
]
