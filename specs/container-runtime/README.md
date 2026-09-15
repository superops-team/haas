# Container Runtime Component Specification

**English** | [简体中文](README.zh-CN.md)

Status: Draft
Last reviewed: 2026-09-10
Change ID: manager-haas-sidecar-spec
Related specs: [Startup](../startup/README.md), [Sandbox Runtime](../sandbox-runtime/README.md), [Config](../config/README.md), [Manager Delegation](../manager-delegation/README.md), [Security Boundary](../security-boundary/README.md)

## 1. Component Role

Container Runtime owns image selection, Docker lifecycle, mounts, durable runtime data, process supervision, and platform verification. Image variant is separate from CPU architecture and from execution-backend identity. Both image variants expose the same HaaS protocol.

## 2. Sources and Rationale

The approved product default is Lite. Mac Apple Silicon uses Docker CLI to build and run an arm64 container locally; no Linux build host or amd64 cross-compilation is required. These are Linux OCI images (`linux/arm64`), not Darwin containers. Docker Engine supplies any required host virtualization. Apple `container` is not a required backend and this design does not promise VM-free Linux execution.

## 3. Upstream and Downstream Relationships

Manager chooses the execution endpoint and authorized workspace. Config selects the Docker backend and image catalog. Sandbox Runtime validates isolation. Container Runtime creates resources; the harness adapter owns native execution. The control sidecar, not the container id, owns durable public session/invocation state.

## 4. Responsibility Boundaries

- Support Lite and AIO without weakening authentication, secretless, workspace, resource, or egress policy.
- Docker daemon access belongs only to the trusted control sidecar; never mount its socket into an execution container.
- Preserve AIO startup scripts and official service-trim settings for AIO only.
- Stop admitting work, settle active invocations, flush state, and forward SIGTERM to child process groups before exit.
- A container with a dead required worker/sidecar exits nonzero; native adapter failure instead makes execution unavailable until recovered.
- Do not perform model calls, remote downloads, or optional browser/MCP warmup on the process-liveness critical path.

## 5. Core Interfaces

### 5.1 Image and Platform Matrix

| Variant | Dockerfile target | Platforms | Contents |
|---------|-------------------|-----------|----------|
| `lite` (default) | `docker/Dockerfile.lite` | `linux/arm64`, `linux/amd64` | Slim Python runtime, locked HaaS dependencies, pinned Codex binary, git, CA certificates, shell, tini; only dependencies required for supported execution |
| `aio` (optional) | existing root `Dockerfile` | `linux/amd64` | Digest-pinned OpenSandbox AIO plus HaaS/Codex; retains browser/VNC and official startup chain |

These are target contracts, not a claim that Lite is already implemented. Keep the existing AIO Dockerfile path; do not duplicate it into a second AIO Dockerfile. Lite excludes AIO, nginx, browser, VNC, IDE, notebook, and development/test dependency groups. Additional language toolchains belong to explicit derived images, not the default Lite image.

Release bases and Codex distributions are digest/checksum pinned per architecture. A mutable local tag requires an explicit development override. Record both OCI index digest and selected platform manifest digest; restore uses the recorded manifest, never a fresh `latest` resolution.

### 5.2 Build and Run Contract

- Target `make docker-build-lite` builds one selected platform with Docker CLI; `make docker-build` defaults to it after the Lite implementation gate passes. Apple Silicon with a local arm64 Engine selects `linux/arm64` explicitly.
- `make docker-build-aio` retains `linux/amd64`. AIO on arm64 requires explicitly available emulation; never silently select it.
- Release builds verify both Lite platforms separately and assemble an OCI index. Local testing does not require multi-platform `--load` or a registry push.
- Select architecture from the Docker execution node, not a remote Manager's architecture. Manager local bind mounts require an operator-confirmed local Docker context; remote contexts cannot interpret the Manager's local paths.
- Preflight checks Docker CLI/daemon, context locality, supported architecture, image manifest, mount access, disk capacity, and required isolation. Unsupported combinations fail before user work starts.
- Target `make docker-check` verifies Lite; `make docker-check-aio` verifies AIO. Existing AIO commands retain their current behavior until their documented migration is implemented; no spec-only change advances runtime capability advertising.
- Lite size budgets are compressed 400 MiB and unpacked 1.2 GiB per platform, measured without build cache. They are acceptance targets, not measured results.

### 5.3 Processes, Ports, and Readiness

Lite uses tini and a dedicated minimal entrypoint, not the existing AIO-only startup script. AIO preserves `/opt/gem/run.sh`, relocates node22 REPL from 8092 to 8093 with `NODEJS_REPL_PORT_22`, and applies [Runtime Trim](../runtime-trim/README.md).

| Surface | Lite | AIO |
|---------|------|-----|
| Host control sidecar | Loopback 8092 or selected local port | Same |
| Standalone container API | 8092 on container interface; publish to host loopback by default | nginx 8080 forwards to sidecar loopback 8092 |
| Delegated worker API | Private container-network listener, never published to host/public | Same trust boundary, no public AIO auxiliary routes |
| Harness model/MCP relay | Loopback 18080/18081, scoped runtime tokens only | Same |

`/health` means the HTTP process is alive. `ready?scope=control` depends on identity/config/store initialization, not Codex or a populated profile. `ready?scope=execution` additionally requires adapter/runtime/isolation readiness. Profile-specific validation occurs before invocation acceptance. Optional AIO services never gate Lite readiness. See Startup for the handshake and shutdown contracts.

### 5.4 Internal Delegated Execution

The host control sidecar accepts the public invocation exactly once and records a durable mapping to a deterministic worker execution id. The worker role cannot independently accept public ADK sessions or recursively create delegated containers. Its private start/inspect/cancel/event APIs use generation-scoped service authentication and the controller-assigned execution id, with deduplication persisted on the session volume. It reports normalized facts; only the control sidecar assigns public event ids and terminal state. A connection loss invokes inspect/replay, never a blind second native turn. Ambiguous native start after a crash requires reconciliation or an explicit failed outcome, not automatic native re-execution.

Execution containers use a per-session internal Docker network. The trusted model/MCP broker joins that network and a separate egress network; it does not route arbitrary IP traffic. Workers have no external network attachment or host networking. Loopback relays forward authenticated model/MCP requests to the broker, which enforces the frozen route policy and resolves secrets outside the execution container. The broker exposes no general CONNECT, arbitrary URL forwarding, or credential-read API. See Sandbox Runtime and Security Boundary for enforcement and credential provisioning.
The delegated policy snapshot carries `network.defaultAction` and `network.allow`. The
current `--network none` Docker backend supports only deny. Requests for allow fail closed
with `haas_policy_unsupported` until the isolated egress broker can enforce that policy.

## 6. Data Model and Persistence

`DelegatedImage` records `reference`, optional digest input, `variant`, and resolved `platform`; the service records the selected manifest digest before execution. Runtime identity is `(delegatedSessionId, containerGeneration)` and is not a new public session.

Durable resources, independent of the writable container layer:

- Control-store data: sessions, invocations, canonical events, desired/applied configuration, worker mapping, and idempotency facts.
- Per-session Docker volume at `/data/haas`: native Codex home/history, materialized configuration generations, skill/AGENTS.md bytes, and recoverable worker receipts.
- Published artifact bytes in Artifact Store, copied before a runtime volume can be reclaimed.

Do not mount the control database or a host user HOME into workers. Use separate worker and broker identities, no privileged containers, no Docker socket, and only the minimum kernel capabilities. Native files are private execution data, never exposed in diagnostics/artifacts or used as a secret store. A retained native reference without its required bytes is not resumable.

## 7. Lifecycle and Configuration Updates

One delegated session has at most one active writer container. Follow-up turns reuse it; idle TTL defaults to 1800 seconds and maximum lifetime to 28800 seconds. Maximum lifetime lets active work finish, then automatically recreates the runtime before a waiting follow-up.

Idle TTL and configuration-driven recreation remove only runtime processes/container layers, not the session volume, public history, or logical binding. Configuration application waits for invocation quiescence, flushes native state, stops old process groups, revalidates mounts, then recreates resources if required. A mount or variant change is an authorized `/policy` update using the same logical session; unsupported native continuation reports an application failure rather than silently making a new chat. Resource preparation may fail without reducing any published profile version.

Deletion closes admission and cancels pending updates/turns, settles the worker, revokes broker tokens, and only then releases writer ownership. State/artifacts become inaccessible immediately; failed physical cleanup remains fenced and is retried. Volume retention follows session retention, not idle TTL. Restore never starts a second writer while cleanup is uncertain.

Pause keeps the delegated runtime and session volume eligible for continuation. Idle TTL may later reclaim the container, but Continue restores from the durable volume before starting the linked invocation. Cancel revokes resumability and follows deletion/cleanup fencing rules.

## 8. Security and Permissions

Model/provider/MCP credentials are absent from worker env, mounts, config, command lines, and artifacts. Worker-to-broker tokens are per-session/audience/generation, short-lived and revocable. Broker/network failure must not enable direct provider fallback. Mount authorization is checked at creation, update and restore, including overlapping writable roots and symlink/path drift.

## 9. Observability

Safe diagnostics distinguish image variant, selected architecture, image digest, Docker availability, control readiness, execution readiness, configuration revision, runtime generation, and cleanup state. Public discovery omits container ids and host paths. Native-only details require independently authorized diagnostics.

## 10. Failure and Recovery

| Failure | Behavior |
|---------|----------|
| Docker unavailable / wrong context | Structured preflight failure; settings stay usable |
| Unsupported platform / image digest mismatch | Reject; never silently change image or architecture |
| Egress or secret broker unavailable | Execution unavailable; no raw credential or network fallback |
| Native session data missing | Fail restore safely; preserve public session and report non-resumable evidence |
| Configuration materialization fails | Keep applied revision; expose failure, do not execute against the pending revision |
| TTL / max lifetime | Recreate from retained data with incremented generation |
| SIGTERM / host restart | Drain or reconcile persisted worker state; never infer completion from process exit alone |

## 11. Test Plan and Acceptance

- Separate Lite arm64/amd64 and AIO amd64 builds, locked dependencies and per-platform digest checks; Mac arm64 builds run through Docker CLI on the Mac.
- Actual HaaS discovery → first turn → pause → interrupted readback → Continue as a linked turn → Stop/cancel → readback on each supported image/platform; fake tests do not replace these gates.
- Reclaim a paused worker after idle TTL, restore the same session volume, and continue on the retained native session; missing native bytes fail non-resumable without local or context-free fallback.
- Destroy and recreate a worker after TTL and a configuration update; verify native history, frozen content and artifact downloads survive.
- Deny worker direct internet, host/metadata endpoints, broker arbitrary forwarding, cross-session tokens, Docker socket and broker credential reads.
- Verify control readiness without Codex, execution readiness after handshake/isolation, and progressive SSE through each exposed API path.
- Verify single-writer fencing, pending update failure/restart, bounded shutdown, disk-full handling and cleanup retry.
- Measure Lite compressed/unpacked size; keep new runtime support unavailable until real security and execution smoke tests pass.
