# Implementation Roadmap Component Specification

**English** | [简体中文](README.zh-CN.md)

Status: Draft
Last reviewed: 2026-08-26

## 1. Component Role

The Implementation Roadmap defines the staged task sequence for taking HaaS from the documentation baseline into its initial implementation. It is not a temporary project plan; it ensures that subsequent code changes are traceable from the specs to protocol, component, and test contracts.

## 2. Sources and Rationale

| Source | Adopted content |
|------|----------|
| Architecture spec | HaaS sidecar-first architecture and initial Codex app-server target |
| HaaS Protocol spec | ADK 2.0-compatible API and HaaS native extension |
| Harness Adapter spec | Adapter seam and multi-harness extension rules |
| Sandbox Runtime spec | Unified projection to OpenSandbox sandbox/execd/credential vault |
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
| S5 sandbox + AIO container | S4 complete | Sandbox Runtime projection and OpenSandbox AIO-derived image smoke pass |
| S6 extended features | S5 complete | Files/artifacts/MCP/skills/model proxy/admission control pass their respective specs |

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
  -> S7 additional harness adapters
```

Do not start S4 before S3 fake adapter proves that the public protocol and session runtime are independent of Codex.

Exit-criteria clarification: `S3 fake adapter` is a **decoupling verification** for the protocol/components (offline unit + integration). The minimum end-to-end requirement in Iron Rule #9 refers to the final release gate for the initial P0 harness (Codex), which occurs in **S4**. These requirements do not conflict: S2-S3 prove that the protocol is harness-independent, and S4 completes the real execution path (see [architecture](../architecture/README.md)).

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

## 11. Test Plan and Acceptance

- S0: docs-only checks and OpenAPI parse/ref validation.
- S1: package import, app factory startup, Makefile commands exist.
- S2: ADK schema, route, and error tests; `/run` vs `/run_sse` parity.
- S3: fake adapter contract tests.
- S4: real Codex app-server E2E.
- S5: Sandbox Runtime projection verification + Docker build/run smoke on an OpenSandbox AIO-derived image.
- S6/S7: feature-specific integration, security and ADK compatibility expansion.
