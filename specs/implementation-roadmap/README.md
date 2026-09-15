# Implementation Roadmap Component Specification

**English** | [简体中文](README.zh-CN.md)

Status: Draft
Last reviewed: 2026-09-12

## 1. Component Role

The Implementation Roadmap defines the staged task sequence for taking HaaS from the documentation baseline into its initial implementation. It is not a temporary project plan; it ensures that subsequent code changes are traceable from the specs to protocol, component, and test contracts.

## 2. Sources and Rationale

| Source | Adopted content |
|------|----------|
| Architecture spec | HaaS sidecar-first architecture and initial Codex app-server target |
| HaaS Protocol spec | ADK 2.0-compatible API and HaaS native extension |
| Harness Adapter spec | Adapter seam and multi-harness extension rules |
| Sandbox Runtime spec | Common policy projection to Lite Docker and OpenSandbox AIO |
| Container Runtime spec | OpenSandbox AIO base image and health/ready constraints |

## 3. Upstream and Downstream Relationships

| Direction | Object | Relationship |
|------|------|------|
| Upstream | Developer / AI agent | Selects the next implementation scope according to this roadmap |
| Downstream | All specs | Each task references the applicable component contracts |
| Downstream | Code, tests, Dockerfile | Subsequent implementation deliverables |

## 4. Responsibility Boundaries

Responsibilities:

- Define the initial implementation sequence.
- Define the inputs, outputs, and entry/exit criteria for each stage.
- Limit scope creep and ensure that the minimum ADK 2.0 protocol + Codex app-server path works before adding Sandbox Runtime standardization.

Non-responsibilities:

- Does not replace individual component specs.
- Does not retain temporary verification logs.
- Does not commit to specific delivery dates.

## 5. Core Interface

This component provides no runtime API. Its task interface consists of documented stage boundaries:

| Stage | Entry criteria | Exit criteria |
|-------|----------------|---------------|
| S0 specs baseline | Current documents have been created | Specs lint/link/schema checks pass |
| S1 project skeleton | S0 complete | Python package, FastAPI app, Makefile, and test skeleton are runnable |
| S2 protocol core | S1 complete | Tests pass for ADK `/list-apps`, `/run`, `/run_sse`, session paths, errors, and schemas |
| S3 fake adapter | S2 complete | Fake harness passes streaming/non-stream/cancel/session replay tests |
| S4 Codex adapter | S3 complete | Codex app-server handshake, thread/turn, and cancel E2E pass |
| S5 sandbox + container | S4 complete | Common isolation passes on default Lite arm64/amd64 and optional AIO amd64 |
| S6 extended features | S5 complete | Files/artifacts/MCP/skills/model proxy/admission control pass their respective specs |
| S7 manager delegation backend | S6 complete, or an explicitly scoped vertical slice has equivalent store/SSE/proxy/container gates | A fresh OpenHarness profile defaults to HaaS `local_managed` + autostart, binds eligible chats without keyword gating, runs delegated Codex work, streams events, restores after TTL, and fails closed without silent local fallback |

## 6. Data Model

```json
{
  "stage": "S2",
  "status": "ready",
  "dependsOn": ["S1"],
  "specs": ["haas-protocol", "session-runtime", "event-log-sse"],
  "exitEvidence": [
    "unit tests",
    "integration tests",
    "ADK compatibility checks"
  ]
}
```

## 7. Runtime Model and State Machine

```text
S0 specs baseline
  -> S1 project skeleton
  -> S2 ADK protocol core
  -> S3 fake adapter
  -> S4 Codex app-server adapter
  -> S5 Sandbox Runtime + OpenSandbox AIO container
  -> S6 Extended features (model proxy / MCP / skills / artifacts / admission control)
  -> S7 Manager Delegation backend
  -> S8 additional harness adapters
```

Do not start S4 before S3 fake adapter proves that the public protocol and session runtime are independent of Codex.

Exit-criteria clarification: `S3 fake adapter` is a **decoupling verification** for the protocol/components (offline unit + integration). The minimum end-to-end requirement in Iron Rule #9 refers to the final release gate for the initial P0 harness (Codex), which occurs in **S4**. These requirements do not conflict: S2-S3 prove that the protocol is harness-independent, and S4 completes the real execution path (see [architecture](../architecture/README.md)).

## 7.1 Current Checkout Implementation Status (2026-09-12)

This matrix is a factual snapshot from repository inspection, not a release claim.
Statuses:

- `implemented_and_verified`: implementation exists and this review ran the required gate successfully.
- `implemented_not_reverified`: implementation/evidence exists, but this review did not rerun the full stage gate.
- `partial`: material implementation exists, but one or more current contracts or gates are missing.
- `spec_only`: contract is defined; implementation/tests are absent or still implement superseded semantics.
- `not_started`: no material implementation evidence found.
- `not_run`: verification was not executed in this review.

| Stage / capability | Status | Repository evidence | Missing before stage exit |
|--------------------|--------|---------------------|---------------------------|
| S0 specs baseline | `implemented_and_verified` | Component specs and bilingual docs updated; OpenAPI YAML/refs, error parity, links, critical EN/ZH parity, ambiguity scans, `git diff --check`, and pre-commit passed in this review | Re-run after implementation changes before advertising the protocol version |
| S1 project skeleton | `implemented_not_reverified` | `haas/`, FastAPI app, tests, Makefile, Docker files exist | Full S1 command set not rerun in this review |
| S2 protocol core (previous baseline) | `partial` | ADK routes, sessions, idempotency, SSE, errors and tests exist | Implement 2026-09-10 acceptance semantics, userId identity, version header, bounded session reads, query APIs |
| S3 fake adapter | `partial` | Fake/slow/blocking adapters and contract tests exist | Replace accepted-failure 502 tests/behavior with HTTP-200 terminal semantics; typed native event projection/migration |
| S4 Codex adapter | `partial`; real E2E `not_run` | App-server transport, normalizer, probe, recovery and schema tests exist | Acceptance preflight ordering, typed canonical projection, current-version conformance, real Codex E2E |
| S5 sandbox + container | `partial`; Docker smoke `not_run` | AIO Dockerfile/compiler/client/runtime tests exist; Lite only has target contracts | Implement Lite Dockerfile/private worker-broker/session volume, then run Lite arm64/amd64 and AIO amd64 smoke |
| S6 extended features | `partial` | Model proxy, MCP/skill, artifacts, admission and tests exist | SQLite production backend, upload idempotency, global HaaS-Version response middleware, bounded page stores, current protocol conformance |
| S7 Manager backend | `partial` | Manager delegation client/settings/supervisor/routing and HaaS delegated APIs exist | Apply product defaults, PREPARED→HAAS_BOUND commit, capability API, dual-stream canonical bridge, resilient cursor client, invocation/page APIs, P0 workspace gating |
| S8 additional adapters | `not_started` | No Pi/OpenCode/AMP adapter implementation found | Start only after S7 P0 gate passes |
| Human approval and input bridge | `spec_only`; release blocker | Approval store/resolve API and event schema exist; structured input contract is specified; Codex capability is still `unattended_only` and input handling is unsupported | Native Codex command/file approval and request-user-input bridge, reconnect recovery, UI/E2E; keep controls disabled until verified |
| Process visibility and task completion | `spec_only`; release blocker | 2026-09-12 packaged session proved native reasoning/tools exist, while typed projection, terminal integrity, interactive input, and task completion are incomplete | Implement FV-15–FV-19 before claiming the default desktop HaaS path production-ready |
| Snapshot/remote workspace | `spec_only`, P1 | Capability names documented as unsupported | Discriminated schemas, transfer/sync/conflict/retention/recovery design and implementation |

### 7.2 2026-09-10 P0 Contract Delta Implementation Queue

These items block advertising `HaaS-Version: 2026-09-10` and block the S7 product
exit. They are ordered as vertical dependency slices:

1. **Protocol/store migration:** rename runtime protocol constant; implement event
   schema v2 migration, typed `CanonicalEventRecord`, `project_haas()`, and terminal
   metadata validation.
2. **Durable acceptance:** reorder side-effect-free preflight before atomic
   `InvocationRecord(status=accepted)`; converge accepted failures on HTTP-200 ADK and
   native terminal events; update idempotency and integrity-failure handling.
3. **Recovery reads:** implement invocation GET, bounded events page, bounded ADK
   Session 413 behavior, and approval list (empty under P0 unattended mode).
4. **Identity:** add `defaultUserId`/`allowedUserIds`, static/JWT mapping, explicit
   delegated-user authorization, and cross-scope negative tests.
5. **Harness profile:** implement the versioned Harness Profile store/API,
   profile validate/activate, active profile fingerprints, AGENTS.md snapshots,
   session `EffectiveHarnessProfile` freezing, and explicit profile rebind.
6. **Capability discovery:** implement caller-scoped `/v1/haas/capabilities` from
   registry/adapter/runtime/profile facts; fail closed on unknown values in Manager.
7. **Manager binding/client:** implement HaaS-by-default local-managed profile,
   PREPARED→HAAS_BOUND commit at first accepted invocation, P0 bind-mount-only gating,
   stable idempotency key/cursors, dual-stream merge, timeout/backoff/recovery, and
   delegated desired/applied configuration barrier, durable send queue, dual-stream
   completion barrier, endpoint binding recovery, and confirmed-expiry linked attempts.
8. **Persistence/version:** implement actual SQLite production assembly, upload
   idempotency, HaaS-Version response middleware, and schema migrations.
9. **Lite/AIO runtime:** implement the separate Lite Dockerfile, private worker/broker
   isolation, per-session volume, arm64/amd64 Lite builds, and retain AIO amd64.
10. **P0 release evidence:** run offline unit/integration/ADK suites, coverage gates,
   Mac Docker Lite arm64, Lite amd64 and AIO amd64 build/smoke, real Codex E2E, secret scan, code-review,
   brooks-review, and brooks-test. Any unrun real-service gate remains `not_run` and
   blocks a production-ready claim.

### 7.3 Dev Loop Vertical Tasks and Case Mapping

Repository policy keeps this task plan in `specs/`; no temporary OpenSpec or verification-report directory is committed. Execute tasks in order. Each task starts with its listed failing tests and closes only when its functional cases pass.

| Task | Vertical deliverable | Primary files | Depends on | Verification cases |
|------|----------------------|---------------|------------|--------------------|
| DL-01 | SQLite Store protocols, migrations, transactions, idempotency expiry/tombstone, event schema v2 | `haas/stores/*`, `haas/events.py`, `haas/sessions.py` | none | FV-07, FV-13 |
| DL-02 | Versioned profile + frozen provider/content route | registry/profile/model proxy/MCP-skill runtime | DL-01 | FV-02, FV-05 |
| DL-03 | Delegated desired/applied model, fenced reconciler and turn admission barrier | `haas/api.py`, `haas/runtime/reconciler.py`, delegated store | DL-01, DL-02 | FV-03–FV-05 |
| DL-04 | Canonical pending/applied/failed events and restart recovery | event/session/runtime APIs | DL-01, DL-03 | FV-03, FV-04, FV-13 |
| DL-05 | Lite image, platform/digest resolver, session volume, hardened worker and private broker network | Dockerfile/Makefile/runtime/security | DL-01, DL-02 | FV-08–FV-11 |
| DL-06 | Manager endpoint/supervisor/typed client/routing and PREPARED acceptance | manager HaaS modules and settings/session store | DL-01–DL-03 | FV-01, FV-05, FV-12 |
| DL-07 | Manager durable send queue, three-stream bridge, completion barrier and linked attempts | manager store/stream/configuration modules | DL-03, DL-04, DL-06 | FV-03, FV-04, FV-06, FV-07 |
| DL-08 | AIO regression, migration/conformance, real E2E and release gates | quality scripts/E2E/Makefile | DL-01–DL-07 | FV-09–FV-14 |
| DL-09 | Typed Codex process events, unique terminal persistence, and concurrent Manager native/ADK bridge | adapter/event/session/manager stream modules | DL-01, DL-04, DL-07 | FV-15, FV-19 |
| DL-10 | Model capability lifetime/refresh and visible non-success recovery | model proxy/session/adapter/manager UI | DL-02, DL-09 | FV-17 |
| DL-11 | Codex approval plus structured-input bridge with durable reconnect recovery | app-server RPC/adapter/session/native API/Manager GUI | DL-09, DL-10 | FV-12, FV-16 |
| DL-12 | Manager task-completion controller, bounded continuation, verification gate, and packaged acceptance | manager task state/transcript/automation/E2E | DL-09–DL-11 | FV-18 |
| DL-13 | Session-page checkpointing, authoritative terminal reconciliation and stale-running reconnect recovery | Manager HaaS client/stream bridge/session binding/WebSocket startup | DL-07, DL-09, DL-12 | FV-24 |
| DL-14 | Foreground prompt transcript-follow epoch and post-layout live-progress scrolling | Manager GUI composer/transcript viewport and Playwright fixture | DL-09, DL-12 | FV-25 |
| DL-15 | Accepted-attempt retry readback and same-invocation event-stream recovery | Manager HaaS attempt/stream bridge/session binding | DL-07, DL-13 | FV-26 |

Alignment result: all P0/P1 requirements in Container Runtime, Manager Backend, Manager Delegation, Harness Profile, Event Log, Stores, Security Boundary and HaaS Protocol map to FV-01–FV-26 and one or more DL tasks. Remote workspace remains an honest capability negative under FV-12. Human approval/input remains unadvertised until DL-11 passes, but it is now a release blocker for the desktop interaction contract rather than deferred polish.

## 8. Security and Permissions

- Security Boundary spec gates every stage.
- New stages cannot relax secretless rules without updating Security Boundary.
- Any runtime stage that touches Docker, provider credentials, MCP headers or artifact download must add negative tests before implementation is considered done.

## 8.1 Test Isolation Flags and Coverage

- Tests MUST be offline by default: they MUST NOT access the real network, real HOME, real providers, or real Codex/OpenSandbox.
- Real-service tests use explicit flags: `HAAS_E2E=1` (master flag) or `HAAS_E2E_CODEX=1` / `HAAS_E2E_OPEN_SANDBOX=1` (individual flags). If a flag is not enabled, the test is skipped and recorded as `not_run`; it MUST NOT be reported as passed.
- Test framework: pytest + pytest-asyncio + coverage.
- Coverage gates (AGENTS.md): core modules ≥90%; credential, redaction, policy, proxy token, artifact path, and log-redaction paths ≥95%.

## 9. Observability

Each implementation stage must report:

- changed specs and code files;
- commands run and result;
- unrun checks and reason;
- known risks and owner;
- next-stage readiness.

## 10. Failure and Recovery

| Scenario | Behavior |
|------|------|
| Stage exit evidence missing | Do not start dependent stage |
| Spec conflict discovered | Update affected specs before code |
| ADK compatibility fails | Fix protocol implementation or downgrade advertised capability |
| Codex E2E fails | Keep adapter unavailable; do not mark `codex` base ready |
| Sandbox Runtime smoke fails | Do not claim harness sandbox standardization; block S6 |
| Docker smoke fails | Do not publish runtime image |
| Manager delegation restore fails | Keep the manager session HaaS-bound, return a safe delegated error, and do not fall back to local execution |

## 11. Test Plan and Acceptance

- S0: docs-only checks and OpenAPI parse/ref validation.
- S1: package import, app factory startup, Makefile commands exist.
- S2: ADK schema, route, and error tests; durable invocation acceptance boundary; HTTP-200 `/run` vs `/run_sse` parity for every accepted terminal outcome; pre-acceptance 4xx/5xx and post-acceptance store-integrity recovery.
- S3: fake adapter contract tests, including accepted start/stream/finalize failures, partial-output retention, and idempotent HTTP-200 replay without duplicate execution.
- S4: real Codex app-server E2E.
- S5: common Sandbox Runtime verification + Lite arm64/amd64 and AIO amd64 Docker build/run smoke.
- S6: feature-specific integration, security and ADK compatibility expansion.
- S7: OpenHarness default HaaS `local_managed` + autostart profile, explicit local opt-out, no trigger-keyword routing gate, stable caller-scoped `GET /v1/haas/capabilities`, typed native `CanonicalHaasEvent` with stable `type`/`haas` metadata, Volcengine Ark provider identity, HaaS native delegated-session APIs, live `/run_sse`, persistent store, one-container-per-delegated-session lifecycle, `/workspace:rw` mount validation, workspace single-writer lock, capability-gated human approval and structured input, model proxy secretless path, task-completion integrity, and Docker/Codex smoke behind explicit flags.
- S8: additional harness adapter expansion after the manager delegation backend is stable.
