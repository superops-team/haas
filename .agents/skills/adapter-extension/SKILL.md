---
name: adapter-extension
description: Design methodology for adding or modifying a harness adapter capability in HaaS. Use when introducing a new adapter, adding a feature/flag/option to an existing adapter (codex, pi, opencode, amp), or deciding whether a knob belongs in an adapter or on the shared contract. Triggers on "new adapter", "add provider feature", "adapter option", "adapter flag", "opt-in", "default-on", "extra_body", "**kwargs", "streaming parity", or any change under haas/harnesses/. Covers the "find the existing abstraction first" decision framework and the adapter-change completeness checklist. Does NOT run a code review — that is parallel-code-review.
---

# Adapter Extension

Methodology for extending the harness-adapter layer. HaaS normalizes many harness
runtimes behind one contract; the core risk is leaking a harness-specific knob or
inventing parallel abstractions that fragment the contract. This skill is a
*design decision* framework, applied whenever you add or change an adapter.

## Authority

- Root `AGENTS.md`: adapter isolation (harness-native protocol stays inside its
  adapter), secretless, events-as-facts, failure must be recoverable or
  explainable, "先跑通最小端到端".
- Component contracts:
  - `specs/harness-adapter/README.md` — the shared adapter interface and what
    every adapter must declare.
  - `specs/codex-app-server-adapter/README.md` — the first P0 reference adapter;
    match its shape when adding siblings.
- The northbound contract (`specs/haas-protocol/`) must not be polluted by
  harness-native concepts.

## Step 1: Find the existing abstraction before designing a new one

Do not reach for an adapter-specific knob on first instinct. Walk this checklist
in order:

1. **Enumerate how sibling adapters already expose this capability.**
   For the ability you need, list every existing adapter (codex, pi, opencode,
   amp) and how it currently surfaces the same concept. Read their adapters, not
   just their docs. You are asking "is this already a shared idea?"

2. **If an existing abstraction already covers it, reuse it.** Do not add a
   parallel adapter-specific field. Map your harness onto the existing shared
   field; if the mapping is lossy, extend the shared field rather than forking.

3. **If ≥ 3 adapters all have this concept, promote it to a shared field.**
   A concept that recurs across three or more adapters is a first-class part of
   the contract — add it to the shared adapter interface / request schema once,
   and have every adapter implement (or explicitly opt out of) it.

4. **Use typed fields (`Literal`), not escape hatches.** A new knob MUST be a
   typed Pydantic field (enum / `Literal` / bounded set). Never use `extra_body`
   or an untyped `**kwargs` bag to pass harness-specific options northbound —
   that defeats schema validation, OpenAPI generation, redaction, and the
   compatibility contract.

5. **default-on only when it changes neither observable behavior nor cost.**
   A new capability may be enabled by default ONLY if turning it on produces no
   difference the user can observe and adds no cost (no extra tokens, no extra
   provider calls, no extra container resources). If it is invisible and free,
   default-on is safe.

6. **preview, cost, or provider-limit risk ⇒ opt-in.** Anything that is
   preview/experimental, may increase cost (more tokens, more requests), or may
   trip provider rate/safety limits MUST be opt-in (default off, explicit flag).
   The caller chooses the risk.

Decision summary:

| Situation | Action |
|---|---|
| Existing shared field covers it | Reuse it; map your adapter onto it. |
| ≥3 adapters already have the concept | Promote to a shared typed field. |
| Only your adapter needs it | Add a typed field, not `extra_body`/`**kwargs`. |
| Invisible + free | May default-on. |
| Preview / cost / provider-limit risk | Must be opt-in (default off). |

## Step 2: Declare the adapter contract

When you add or change an adapter, it must declare (per `specs/harness-adapter/`):

- `base` identifier (`codex`, `pi`, `opencode`, `amp`, ...).
- Native transport used (e.g. app-server WebSocket/stdio, CLI JSONL).
- Supported input / files / tools / MCP / skills / approval / cancel / resume /
  token-usage capabilities.
- Credential injection method and secretless level.
- Event normalizer and terminal-state detection rule.

If a harness can only disable a tool at instruction level (not hard block), mark
it `enforcement=advisory` in the catalog. Never claim a hard block you cannot
enforce.

## Step 3: Adapter-change completeness checklist

A change to an adapter is not done until every row below is checked. This is the
parity/consistency gate, not a review opinion.

- **Streaming / non-streaming parity.** The streamed event sequence and the
  buffered (`POST /run`) result must agree: same event types, same order, same
  terminal state. Do not ship a streaming-only fix.
- **Sync / async paths.** If the adapter has both a sync and an async call path,
  the new behavior works on both (or the unsupported path is explicitly rejected
  with a structured error).
- **Round-trip verification.** Adapter-native event → canonical HaaS event →
  re-serialization survives a parse/dump round-trip without losing fields.
- **Parse / dump consistency.** Incoming request parses, outgoing response dumps,
  and the OpenAPI schema all agree on field names and types.
- **Error paths covered.** Timeout, cancel, adapter crash, SSE disconnect,
  provider/MCP failure, and sandbox restart each have a defined state, error
  code, recovery action, and a test. Happy-path-only is not done.
- **Secretless.** No real credential, raw prompt, or full tool argument reaches
  env/config, logs, events, metrics, artifacts, or tests. Add a reverse assertion.
- **No native leak.** Harness-native JSON-RPC / JSONL / protocol details stay
  inside the adapter; the northbound surface only sees canonical HaaS events.

## Step 4: Prove the minimal end-to-end path

Before extending files, MCP, skills, approvals, or advanced resume, land the
smallest real chain (AGENTS.md rule 9):

```text
discovery -> create response -> stream events -> terminal response -> read back
```

Only after that chain passes do you layer on the advanced features.

## Scope boundaries

- **Not `parallel-code-review`**: that skill is a review *lens* applied during
  code review. This skill is the *design methodology* you apply while you are
  writing or changing an adapter — before review.
- **Not `pydantic-modeling`**: once the decision to add a shared field is made,
  that skill implements the Pydantic schema (Field metadata, union traps, UTC).
- **Not `fastapi-backend`**: the HTTP/SSE delivery of canonical events is owned
  there; adapters only produce canonical events.

## Result format

When proposing an adapter extension, record:

```text
Capability: <what you are adding>
Sibling adapters already expose it how: <codex=..., pi=..., opencode=...>
Decision: <reuse existing | promote to shared field | new typed adapter field>
Typed field (if new): <Literal/enum name + values>
Default: <default-on only if invisible+free | opt-in otherwise>
Parity checklist: streaming=ok, sync/async=ok, roundtrip=ok, parse/dump=ok, errors=ok
Minimal E2E: discovery->create->stream->terminal->readback = passed|not_run, reason
```
