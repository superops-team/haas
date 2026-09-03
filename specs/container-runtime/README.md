# Container Runtime Component Specification

**English** | [简体中文](README.zh-CN.md)

Status: Draft
Last reviewed: 2026-09-03
Related specs: [Startup](../startup/README.md), [Runtime Trim](../runtime-trim/README.md), [Security Boundary](../security-boundary/README.md), [Codex App-Server Adapter](../codex-app-server-adapter/README.md), [Observability](../observability/README.md)

## 1. Component Role

Container Runtime defines HaaS image, process topology, port, volume, health/ready, and shutdown semantics. The HaaS runtime image MUST be built on the open-source OpenSandbox AIO image and inherit AIO shell, file, browser, and sandbox service capabilities.

The detailed contracts for startup orchestration, the nginx unified entrypoint, and Codex readiness are defined by [Startup](../startup/README.md). This component retains only the container, process, and port boundaries.

## 2. Sources and Rationale

| Source | Adopted content |
|--------|-----------------|
| OpenSandbox README | official image registry, sandbox lifecycle, credential vault, network policy |
| OpenSandbox AIO example | `ghcr.io/agent-infra/sandbox:latest`, `/opt/gem/run.sh`, AIO port `8080` |
| OpenSandbox API docs | lifecycle API, execd API, SSE command execution, file API |
| `mpa-codex-worker` container runtime spec | separation of `/health` and `/ready`, runtime root, socket, shutdown, container validation |
| Component overview | migration of the Dockerfile to OpenSandbox AIO |

## 3. Upstream and Downstream Relationships

| Direction | Component | Relationship |
|-----------|-----------|--------------|
| Upstream | Deployment system / developer | Builds and runs the HaaS image |
| Upstream | HaaS sidecar | Reads runtime directories, ports, AIO endpoint, and process status |
| Upstream | Sandbox Runtime | Consumes AIO sandbox/execd/credential vault services |
| Downstream | OpenSandbox AIO | Base shell/file/browser/sandbox/execd/vault services |
| Downstream | Codex app-server | Initial harness runtime process |
| Downstream | Model Proxy / MCP Proxy | loopback services |
| Downstream | Observability | process logs, health, ready, and resource metrics |

## 4. Responsibility Boundaries

Responsibilities:

- Define the Dockerfile base image, dependency layer, runtime layer, and entrypoint.
- Preserve or delegate to OpenSandbox AIO `/opt/gem/run.sh`.
- Ensure that AIO sandbox/execd/credential vault services are available to Sandbox Runtime.
- Start the HaaS sidecar, Codex app-server, model proxy, MCP proxy, and any required watchdog.
- Define ownership of ports `8080`, `8092`, `18080`, and `18081`.
- The node22 REPL in the AIO base image occupies `8092` by default and conflicts with the HaaS sidecar. It MUST be overridden to `8093` through AIO's own `NODEJS_REPL_PORT_22` setting (set in the Dockerfile); the AIO startup scripts MUST NOT be forked or privately modified. Before adding an in-container service, its port MUST first be confirmed as unused by AIO.
- The sidecar is the reason for the container's existence: the entrypoint MUST monitor its liveness. When the sidecar exits, the container MUST exit with a nonzero code; a silent failure in which the container is Up but the API is unavailable is prohibited. AIO/Codex exits only generate warnings, while `/ready` accurately reflects degraded capabilities.
- Define the runtime root, workspace root, artifact root, state root, and socket root.
- Define container semantics for health/ready/status.
- Use nginx as the container's unified external entrypoint and the HaaS sidecar's readiness as the source of truth for external readiness; see the Startup spec for the concrete startup DAG.
- Define SIGTERM draining: stop accepting new work, flush the event log, set ready=false, and cancel or persist active turns.
- Define base-image digest pinning and upgrade validation.
- Apply the AIO service-trimming variables defined by [Runtime Trim](../runtime-trim/README.md) in the runtime ENV layer, ensuring that CUA/BUA, sandbox, and Codex readiness are unaffected.

Non-responsibilities:

- It does not implement the OpenSandbox lifecycle server.
- It does not replace the HaaS Protocol public API.
- It does not store provider secrets.
- It does not use Docker privileged mode or root identity to directly expand agent tool permissions.
- It does not perform model requests, exhaustive MCP probing, remote skill downloads, or long-running recovery on the startup critical path.

## 5. Core Interfaces

### 5.1 Dockerfile Contract

```dockerfile
ARG HAAS_BASE_IMAGE=ghcr.io/agent-infra/sandbox@sha256:<production-pinned-digest>
FROM ${HAAS_BASE_IMAGE}

# install HaaS runtime dependencies after base AIO layers
# install/pin Codex CLI and optional future harness CLIs
# copy haas source after dependency layers
# preserve /opt/gem/run.sh and add HaaS entrypoint wrapper
```

Rules:

- Local experiments MAY use `ghcr.io/agent-infra/sandbox:latest`.
- The Dockerfile default MUST remain the production-pinned digest. A local or CI build MAY set `HAAS_BASE_IMAGE` to a trusted digest-pinned mirror/cache reference; release builds MUST NOT use a mutable tag.
- All HaaS images MUST be built and run for `linux/amd64`. This is a hard delivery contract: `make docker-build` and `make docker-check` pin `--platform=linux/amd64` (via `HAAS_PLATFORM`), and non-amd64 hosts (e.g. Apple Silicon) MUST cross-build amd64 through buildx/QEMU. Native-architecture images MUST NOT be shipped as deliverables.
- Dependency installation layers MUST precede source-code copying. npm and uv downloads use BuildKit cache mounts and remain governed by `uv.lock` and package pins.
- `make docker-build` is the standard local build entrypoint; the override does not change the production default or AIO service contract.
- Runtime env MUST be placed near the final runtime layer so it does not invalidate the dependency cache.
- The Dockerfile MUST NOT embed provider keys, MCP tokens, cookies, or user auth files.

### 5.1.1 AIO Service Trim

HaaS consumes only AIO shell/file/browser/sandbox/execd/credential vault capabilities and does not use AIO's built-in IDE, notebook, or multi-version REPL services. These services are disabled in the runtime ENV layer through **officially supported AIO `DISABLE_*` / `NODE_VERSION` environment variables** (by default: `DISABLE_CODE_SERVER`, `DISABLE_JUPYTER`, `DISABLE_NODEJS_REPL`, and `NODE_VERSION=node22`), without forking or privately modifying AIO startup scripts.

The complete trim contract—the disable list, capabilities that MUST be preserved, mechanism rules, the gost/`18080` precondition, and the boundary that “runtime disabling only reduces resource usage and does not reduce image-layer size”—is authoritatively defined by [Runtime Trim](../runtime-trim/README.md). This component only applies these variables in the runtime ENV layer and ensures that CUA/BUA, sandbox, and Codex readiness are unaffected.

### 5.2 Entrypoint Contract

```text
/opt/haas/run.sh
  -> prepare runtime directories
  -> start or delegate OpenSandbox AIO /opt/gem/run.sh
  -> start HaaS sidecar on 8092
  -> start Codex app-server listener for codex adapter
  -> forward SIGTERM to all child process groups
```

If a process manager is used, it MUST NOT treat supervisor RUNNING as equivalent to HaaS ready. Readiness MUST be based on actual sidecar and adapter probes.

### 5.3 Health / Ready

| Endpoint | Meaning |
|----------|---------|
| `/health` or `/v1/haas/health` | HaaS sidecar process responds |
| `/ready?scope=control` | HaaS can accept lightweight control-plane operations |
| `/ready?scope=execution` | HaaS can start a harness turn |
| AIO `/v1/shell/sessions` | AIO service readiness probe |

## 6. Data Models

### 6.1 RuntimeLayout

```json
{
  "workspaceRoot": "/workspace",
  "dataRoot": "/data/haas",
  "runtimeRoot": "/tmp/haas",
  "stateRoot": "/data/haas/state",
  "artifactRoot": "/data/haas/artifacts",
  "codexHome": "/data/haas/harnesses/codex/home",
  "codexSocketPath": "/tmp/haas/codex.sock",
  "aioBase": {
    "image": "ghcr.io/agent-infra/sandbox@sha256:<digest>",
    "servicePort": 8080,
    "entrypoint": "/opt/gem/run.sh",
    "services": ["shell", "file", "browser", "sandbox", "execd", "credential_vault"]
  }
}
```

### 6.2 RuntimeProcess

```json
{
  "name": "haas-sidecar",
  "command": ["/app/haas/.venv/bin/uvicorn", "haas.api.app:create_app", "--factory"],
  "port": 8092,
  "critical": true,
  "restart": "on_failure",
  "healthEndpoint": "/v1/haas/health"
}
```

## 7. Runtime Model and State Machine

```text
image built
  -> container starting
  -> aio starting
  -> sidecar listening
  -> control ready
  -> adapter probe pending
  -> execution ready
  -> draining
  -> stopped
```

Startup rules:

- `/health` MUST become available before optional capability warmup completes.
- `/ready?scope=control` and the default `/ready` are overall service readiness signals; they remain false until the Codex app-server readiness probe completes.
- `/ready?scope=execution` MAY use the same Codex gate for the P0 adapter; model provider, MCP discovery, and browser startup remain outside the default gate unless declared a Codex execution-safety dependency.
- AIO readiness and HaaS readiness are reported separately.

## 8. Security and Permissions

- Container root or privileged mode is not a substitute for harness sandbox policy.
- Runtime secret files MUST be owner-only and excluded from artifacts.
- AIO service endpoints SHOULD be bound to loopback unless intentionally exposed through a controlled proxy.
- HaaS sidecar bearer auth is required for non-health endpoints.
- Docker build args and image layers MUST NOT contain secrets.
- The base-image digest MUST be recorded for release builds.

## 9. Observability

Container status MUST include:

- image reference and digest;
- AIO process status and port;
- HaaS sidecar process status and port;
- adapter process status;
- model/MCP proxy status;
- startup phase timings;
- drain state;
- last safe error reason.

Logs MUST go to stdout/stderr or configured log files with redaction.

## 10. Failures and Recovery

| Scenario | Behavior |
|----------|----------|
| AIO service not ready | HaaS control ready may be true; execution ready is false with a safe reason |
| HaaS sidecar not listening | container health fails |
| Codex app-server not ready | execution ready is false; session creation can be pending only if the API contract allows it |
| SIGTERM | enter draining, reject new tasks, flush the event log, and cancel/settle active turns |
| base image unavailable | build fails; MUST NOT silently switch images |
| digest mismatch | release blocked |
| port conflict | startup fails with safe diagnostics |

## 11. Test Plan and Acceptance

- Dockerfile lint/static check for base-image pinning in release mode.
- Build smoke from the current checkout (`HAAS_DOCKER_BUILD=1 make docker-check`). Static checks MUST NOT be the only evidence for container changes: there has been a case where all static checks passed but the image could not be built (`README.md` declared by `pyproject` was not copied). Container-related changes MUST run the build layer.
- Container run smoke verifies AIO port `8080` and HaaS port `8092`, asserts that `/v1/haas/status` assembles a real harness adapter (not a test double), and asserts that the container exits with a nonzero code after the sidecar is killed. AIO starts more slowly than the sidecar, so readiness checks MUST poll.
- Health/ready tests verify that `/health` is not gated by optional warmups.
- Shutdown test sends SIGTERM and asserts drain events/status.
- Secret scan verifies that build args, env, logs, and image metadata do not contain provider credentials.
- Service-trim check (§5.1.1 / [Runtime Trim](../runtime-trim/README.md)): statically assert that the Dockerfile sets `DISABLE_CODE_SERVER`, `DISABLE_JUPYTER`, and `DISABLE_NODEJS_REPL`; build/smoke asserts that code-server/jupyter are not listening while browser/VNC/sandbox and Codex readiness remain functional.
