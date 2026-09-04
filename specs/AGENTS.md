# Specs AGENTS

**English** | [简体中文](AGENTS.zh-CN.md)

You are maintaining HaaS protocol and component contracts. Treat files under
`specs/` as source-of-truth for public interfaces, component boundaries, event
semantics, security constraints, and compatibility promises.

## Scope

- `README.md`
- `architecture/README.md`
- `architecture/WALKTHROUGH.md`
- `architecture/VISUAL-STORYTELLING.md`
- `architecture/BRAND-AND-REPOSITORY-METRICS.md`
- `config/README.md`
- `identity/README.md`
- `stores/README.md`
- `haas-protocol/README.md`
- `haas-protocol/*.openapi.yaml`
- `haas-protocol/ERROR-CODES.md`
- `harness-registry/README.md`
- `harness-adapter/README.md`
- `codex-app-server-adapter/README.md`
- `session-runtime/README.md`
- `admission-control/README.md`
- `event-log-sse/README.md`
- `policy-controller/README.md`
- `model-proxy/README.md`
- `mcp-tool-skill-runtime/README.md`
- `artifact-store/README.md`
- `sandbox-runtime/README.md`
- `container-runtime/README.md`
- `security-boundary/README.md`
- `runtime-trim/README.md`
- `startup/README.md`
- `startup/CASES.md`
- `startup/OPENSPEC.md`
- `observability/README.md`
- `implementation-roadmap/README.md`

## Contract Rules

Always:

- Maintain every human-authored specification in two synchronized language variants: English at the canonical path and Simplified Chinese at the sibling `*.zh-CN.md` path. The OpenAPI YAML remains a single language-neutral machine contract.
- Add reciprocal language switches below the H1 in both variants. Keep public interfaces, normative requirements, examples, and compatibility statements semantically identical across languages.
- Keep `specs/README.md` and child specs aligned when adding, renaming, or removing a component.
- Keep ADK-compatible endpoints, events, object shapes and error codes aligned with
  `specs/haas-protocol/`.
- Prefer additive changes for public API and persisted schemas.
- Update the OpenAPI skeleton when HTTP contract fields, routes, headers, or errors change.
- Record compatibility impact for the ADK-compatible API and the HaaS native API. The legacy `/v1/codex-worker/*` shim is out of scope for this project (see specs/README 3.1.1).
- Keep adapter-specific details inside the relevant adapter spec.
- Keep all examples secret-free.

Ask first:

- Breaking or renaming public fields, paths, event types, status values or error codes.
- Changing the selected northbound protocol away from ADK 2.0 compatibility.
- Changing Docker base image family away from OpenSandbox AIO.

Never:

- Put raw provider credentials, Authorization values, cookies, presigned URLs, raw prompts, or full tool payloads in specs or examples.
- Treat a generated schema as authoritative when the prose spec says otherwise.
- Let one harness runtime's native protocol become a public HaaS requirement.
