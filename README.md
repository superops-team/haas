# Harness As A Service

HaaS is the architecture baseline for a generalized harness sidecar. It is the
successor direction for `mpa-codex-worker`: Codex remains the first supported
runtime, but the system boundary is now a multi-harness service that can later
host Pi, OpenCode, AMP and other agent harnesses behind one HTTP/SSE protocol.

Current status: design and protocol/spec initialization. Runtime code is not
initialized yet.

## Start Here

- [AGENTS.md](AGENTS.md) - repository development rules and quality gates.
- [Component specs](specs/README.md) - architecture baseline and long-lived component contracts.
- [Architecture spec](specs/architecture/README.md) - system-level ownership and request flow.
- [OpenAPI skeleton](specs/haas-protocol/haas-2026-08-26.openapi.yaml) - first structural protocol contract.

## Protocol Direction

HaaS exposes an ADK 2.0-compatible public API (`/list-apps`, `/run`, `/run_sse`,
`/apps/{app}/users/{user}/sessions/{sid}`), with HaaS-native operational
extensions under `/v1/haas/*` and migration-only compatibility shims under
`/v1/codex-worker/*`.

The first implementation target is Codex app-server over an internal
stdio/WebSocket/Unix-socket adapter. Public clients should not depend on Codex
thread ids, turn ids, JSON-RPC methods, socket paths or rollout files.

## Runtime Direction

The container runtime will be built on the open-source OpenSandbox AIO image.
Local experiments may use `ghcr.io/agent-infra/sandbox:latest`; production
builds must pin the base image by digest.
