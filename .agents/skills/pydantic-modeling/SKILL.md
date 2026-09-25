---
name: pydantic-modeling
description: Pydantic model-layer design for HaaS protocol and session schemas. Use when defining, changing, or reviewing a request/response model, an ADK Event projection, a session/response record, or any Field() / Annotated metadata in haas/protocol, specs/haas-protocol, or harness adapters. Triggers on "BaseModel", "Field(", "Annotated", "deprecated", "Union", "datetime", "model_dump", "validation", or new schema fields. Covers field-vs-type metadata, the union-metadata trap, model hierarchy, and UTC datetimes. Does NOT cover FastAPI route wiring or response_model selection — that is fastapi-backend.
---

# Pydantic Modeling

Rules for designing the **model layer** of HaaS: canonical protocol schemas,
ADK `Event` projections, session/response records, and adapter-normalized
payloads. This is about *model design*, not about wiring models into routes.

## Authority

- Root `AGENTS.md`: events are facts (stable type, monotonic sequence, terminal
  state, redacted payload); compatible surfaces are additive-only.
- Canonical schema homes:
  - `haas/protocol/` — Python model definitions for HaaS/ADK protocol objects. (Planned per AGENTS.md target structure; the current `haas/` package is flat until the protocol module is scaffolded.)
  - `specs/haas-protocol/haas-2026-09-10.openapi.yaml` — the long-lived contract.
  - ADK `Event` schema (`content.role/parts`, `actions`, `invocationId`) — the
    northbound event shape HaaS projects onto.
- When a model, the OpenAPI spec, and tests disagree, update the spec first,
  then the model, then the test. Do not let them drift.

## 1. Field metadata: field-specific vs type-specific

`Field()` metadata falls into two buckets. Know which one you are using.

- **Field-specific metadata** describes the *field slot* (this property on this
  model), e.g. `deprecated=True`, a human `description` for this slot, an
  example, or a serialization alias for this field. It belongs to the field.
- **Type-specific metadata** describes the *type itself* and can be reused, e.g.
  constraints on a common `SessionId` or `SequenceNumber` newtype.

Prefer attaching metadata through `Annotated[..., Field(...)]` rather than a bare
`Field()` default assignment, because `Annotated` keeps the type clear and makes
the metadata explicitly reusable.

```python
from typing import Annotated
from pydantic import BaseModel, Field

# Good: type alias with reusable type-level metadata.
SessionId = Annotated[str, Field(min_length=1, pattern=r"^sesn_[a-z0-9]+$")]

class RunRequest(BaseModel):
    session_id: SessionId
    # field-specific metadata lives on the field, via Annotated + Field
    legacy_turn: Annotated[
        str | None,
        Field(default=None, deprecated=True, description="Superseded by turn_id"),
    ]
```

Avoid:

```python
# Less clear: default assignment hides the type and duplicates constraints.
class RunRequest(BaseModel):
    session_id: str = Field(min_length=1, pattern=r"^sesn_[a-z0-9]+$")
```

## 2. The union metadata position trap

This is the most common bug. **Field-specific metadata (e.g. `deprecated`) must
go on the OUTER union field, not inside the `Annotated` member of the union.**

Putting `deprecated=True` on a member of a union (`Annotated[int | None,
Field(deprecated=True)]`) does NOT mark the field as deprecated — it attaches
metadata to an individual union branch, which Pydantic treats as part of type
constraints and silently does not surface as "this field is deprecated".

Correct — metadata on the outer field:

```python
from typing import Annotated
from pydantic import BaseModel, Field

class Session(BaseModel):
    # Outer Annotated wraps the whole union; deprecated applies to the field.
    ttl_seconds: Annotated[int | None, Field(default=None, deprecated=True)]
```

Wrong — metadata buried inside a union arm (deprecated applies only to the
`int` branch, not the field as a whole):

```python
class Session(BaseModel):
    # deprecated sits on the int arm only; the None arm is not deprecated.
    ttl_seconds: Annotated[int, Field(deprecated=True)] | None
```

When adding `deprecated`, `description`, `alias`, or examples to a union-typed
field, double-check: is the `Field(...)` wrapping the whole `A | B` expression,
or only one arm? Only the outer placement has the intended effect.

## 3. Model hierarchy and subclassing

- Build canonical objects as small, composable base models and extend them,
  rather than repeating the same fields across every request/response.
- Use inheritance for shared shape (e.g. a base `EventEnvelope` with
  `sequenceNumber`, `eventId`, `type`); put type-specific payloads in a
  dedicated sub-model or discriminated union keyed by `type`.
- Prefer a **discriminated union** for events / actions so the `type` field
  selects the variant — this matches ADK `Event` and gives safe, fast parsing.
- Do not subclass to add optional internal-only fields to a public response;
  instead keep an internal model and a public `response_model` view (see
  `fastapi-backend`). Public schema is additive-only.

```python
from typing import Literal
from pydantic import BaseModel, Field

class BaseEvent(BaseModel):
    type: str
    sequence_number: int
    event_id: str

class TextEvent(BaseEvent):
    type: Literal["text"] = "text"
    text: str

class TerminalEvent(BaseEvent):
    type: Literal["terminal"] = "terminal"
    status: Literal["completed", "failed", "cancelled"]

Event = TextEvent | TerminalEvent   # discriminated by `type`
```

## 4. UTC datetimes

- Always store and transmit **timezone-aware UTC** datetimes. Use
  `datetime.now(timezone.utc)` / `AwareDatetime` patterns; never naive
  `datetime.now()`.
- Serialize to ISO-8601 with an explicit `Z` / offset. Reject naive datetimes at
  the model boundary so a local-time value can never silently enter the event log.
- Session timestamps, event timestamps, and artifact timestamps all follow this;
  consistency matters because clients replay and sort by them.

```python
from datetime import datetime, timezone
from pydantic import BaseModel, Field

class EventEnvelope(BaseModel):
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
```

## 5. Parse/dump consistency and redaction

- Treat `model_validate()` (incoming) and `model_dump()` /
  `model_dump_json()` (outgoing) as round-trips. A canonical event must survive
  parse → dump → parse without losing or renaming fields.
- Keep serialization field names stable (no renaming between major versions);
  aliases are additive only and old names must be retained during a deprecation
  window.
- Redaction happens at the model/adapter boundary: raw prompts, full tool args,
  credentials, and provider payloads must never be a modeled field that reaches
  the northbound schema. Add a reverse test asserting the sensitive field is
  absent from dumped output.

## Scope boundaries

- **Not `fastapi-backend`**: that skill owns route wiring, `response_model`
  selection, `Depends`, and async/sync choice. This skill is the *model design*
  layer — what fields exist, what metadata they carry, how they validate and dump.
- **Not `adapter-extension`**: that skill decides whether a new capability should
  be a shared adapter field; this skill implements the resulting Pydantic schema.
- Before shipping a schema change, check `specs/haas-protocol/*.openapi.yaml` —
  public fields, event names, and error codes are compatible surfaces and are
  additive-only.
