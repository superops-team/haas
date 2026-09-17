# Config Component Specification

**English** | [简体中文](README.zh-CN.md)

Status: Draft
Last reviewed: 2026-09-15
Change ID: unified-runtime-approval-policy, long-task-model-proxy-stability
Related specs: [Container Runtime](../container-runtime/README.md), [Stores](../stores/README.md), [Identity](../identity/README.md), [HaaS Protocol](../haas-protocol/README.md), [Harness Profile](../harness-profile/README.md), [Manager Delegation](../manager-delegation/README.md)

## 1. Component Role

Config defines the configuration and assembly contract for HaaS: environment variables, configuration files, the port table, service discovery, and the app factory signature. It eliminates ambiguity caused by individual components inventing their own environment variables and `create_app` parameters.

Embedded local assembly consumes `HAAS_CREDENTIAL_FD` once as an inherited private socket descriptor, never as a credential. It starts the model proxy at `model_proxy.listen` (loopback only; port 0 selects a free port) inside the application lifespan. Failure to establish this channel or listener leaves control APIs available but execution unavailable. Neither descriptor nor provider environment variables reach harness children.

## 2. Sources and Rationale

| Source | Adopted content |
|------|----------|
| Container Runtime | Port conventions (8080/8092/18080/18081), `create_app --factory` |
| Architecture | Python + FastAPI, sidecar-first single process |
| Security Boundary | Secrets are reference-only and MUST NOT be stored in plaintext configuration |

## 3. Upstream and Downstream Relationships

| Direction | Component | Relationship |
|------|------|------|
| Upstream | Deployment / container entrypoint | Injects environment variables and the configuration file path |
| Downstream | All components | Provides `AppConfig` as the sole configuration source |
| Downstream | Observability | Records a redacted summary of the effective configuration |

## 4. Responsibility Boundaries

Responsibilities:

- Define the environment-variable prefix, configuration file format, and three-tier precedence.
- Define the `AppConfig` data model and the `load_config()` / `create_app(config)` contracts.
- Define the port table and service discovery using loopback addresses.
- Require secret configuration to use references (`credentialRef`); plaintext values MUST NOT be accepted.

Non-responsibilities:

- Does not parse plaintext secrets.
- Does not support process-level runtime hot reload in the initial release;
  `AppConfig` is static, with reload reserved for a later release.
- Does not carry dynamic business configuration for harness profiles. Runtime
  updates to provider, MCP, skills, AGENTS.md, workspace/policy, and budget
  settings are owned by Harness Profile.

## 5. Core Interfaces

```python
@dataclass
class AppConfig:
    server: ServerConfig
    store: StoreConfig
    identity: IdentityConfig
    model_proxy: ModelProxyConfig
    mcp_proxy: McpProxyConfig
    adapters: AdaptersConfig
    session_runtime: SessionRuntimeConfig
    delegation: DelegationConfig
    observability: ObservabilityConfig

def load_config(path: str | None) -> AppConfig: ...
def create_app(config: AppConfig | None = None) -> FastAPI: ...

When uvicorn invokes `create_app()` with no arguments via `--factory`, it runs
`load_config(HAAS_CONFIG)` internally and then assembles the app. A path that passes
`config` explicitly, such as a test, skips the loading step.
```

### 5.1 Adapter Assembly Contract (S6)

`create_app()` MUST assemble the real harness adapter according to configuration. The
production entry point MUST NOT silently fall back to the test-only `FakeAdapter`:

| `adapters.default_base` | Assembly result |
|-------------------------|----------|
| `codex` (default) | `CodexAdapter`, with transport/socket taken from `adapters.codex` |
| `fake` | `FakeAdapter`, for local development and testing only |

The `HAAS_ADAPTER_BASE` environment variable MAY override `default_base`.

Assembly only establishes connection configuration; it does not require harness
connectivity at startup. The process MUST still start when Codex is not ready, and
`/v1/haas/ready?scope=execution` and `/v1/haas/status` MUST accurately report
`not_ready` while `/health` remains `ok`. This allows the container to start before
the harness is ready, consistent with the health/ready separation defined by
[Container Runtime](../container-runtime/README.md).

| Variable | Meaning |
|------|------|
| `HAAS_ADAPTER_BASE` | Default harness base (`codex` / `fake`) |
| `HAAS_DEFAULT_IMAGE_VARIANT` | Sandbox image variant (`lite` default, `aio` opt-in). Manager and delegated-session `image.variant` override this per session. |
| `HAAS_SESSION_LEASE_TTL_MS` | Active-turn session lease TTL in milliseconds (default: `30000`) |
| `HAAS_SESSION_LEASE_RENEW_INTERVAL_MS` | Active-turn lease renewal interval in milliseconds (default: `10000`; must be less than half the TTL) |
| `HAAS_SESSION_TURN_TIMEOUT_SECONDS` | End-to-end adapter invocation deadline in seconds (default and maximum: `86400`; must be greater than lease TTL) |

`HAAS_SESSION_TURN_TIMEOUT_SECONDS` is the long-task safety deadline, not the
HTTP response-header timeout, SSE heartbeat timeout, model stream-idle timeout or
GUI WebSocket timeout. Values above `86400` are clamped to 24 hours. Values less
than or equal to zero are invalid. Packaged local-managed execution MUST use the
same default unless the user explicitly configures a shorter value.

The environment-variable prefix is uniformly `HAAS_`. For example:

| Variable | Meaning |
|------|------|
| `HAAS_CONFIG` | Configuration file path |
| `HAAS_SIDECAR_PORT` | Sidecar port (default: `8092`) |
| `HAAS_STORE_BACKEND` | Production default: `sqlite`; `memory` is test-only; `postgres` is reserved for multi-replica deployments |
| `HAAS_IDENTITY_PROVIDER` | `static` / `external_jwt` |
| `HAAS_STATIC_TOKEN_FILE` | User-private file containing the local static bearer token; when configured, missing/empty files fail closed instead of falling back to `dev-token` |
| `HAAS_DELEGATION_CONTAINER_BACKEND` | Standalone and packaged local-API default: `disabled`; `docker` enables the explicit delegated-container runtime profile. |
| `HAAS_DELEGATION_DOCKER_BIN` | Docker CLI path/name used when the delegated backend is `docker` |
| `HAAS_DELEGATION_DOCKER_NETWORK` | HaaS `isolated` network policy mode by default; internal worker network and non-routing broker egress; `none` is offline-only, raw host/bridge passthrough rejected |
| `HAAS_DELEGATION_ALLOW_UNPINNED_LOCAL_IMAGE` | Local-development escape hatch allowing an unpinned image tag such as `haas:local`; default `false`, production MUST keep digest pinning |
| `HAAS_DELEGATION_IDLE_TTL_SECONDS` | Default idle TTL for delegated-session containers (`1800`) |
| `HAAS_DELEGATION_MAX_CONTAINER_LIFETIME_SECONDS` | Default maximum delegated container lifetime (`28800`) |
| `HAAS_DELEGATION_RW_WORKSPACE_CONCURRENCY` | `single_writer` in the initial release |

The default configuration file is `haas.yaml`. Precedence is: defaults < configuration file < environment variables.

### 5.2 Managed Launch Fields

| Environment | AppConfig target / behavior |
|-------------|-----------------------------|
| `HAAS_STATIC_PRINCIPAL_JSON` | identity.static_principal; base64 JSON validated by Identity, launch-owned only |
| `HAAS_PORT_READY_FILE` | server.ready_file; owner-only atomic JSON `{port,pid,launchId}` after bind |
| `HAAS_LAUNCH_ID` | server.launch_id; non-secret per-start id, required with ready_file |
| `HAAS_BOOTSTRAP_HARNESS_ID/BASE/NAME` | registry seed tuple; scoped identity only, no active profile, no overwrite |
| `HAAS_LOG_FILE` | observability.log_file; redacted writer |
| `HAAS_LOG_ROTATE_MAX_MB` / `HAAS_LOG_ROTATE_KEEP` | observability rotation; defaults 32 / 5 |
| `HAAS_DEFAULT_IMAGE_VARIANT` | delegation.default_image_variant; lite by default |

`HAAS_SIDECAR_PORT=0` requests a bound ephemeral port. Supervisor lock precedes token/file creation. Host control mode binds loopback and needs no host Codex process; delegated workers cannot serve the public control API. Docker `isolated` is a HaaS network policy mode, not a raw Docker network name: create the internal worker/non-routing broker topology from Container Runtime. `none` is offline-only; arbitrary host/bridge passthrough is rejected. Process config changes require drained restart; session runtime config updates go through the delegated `/policy` barrier. Do not require execution-ready before profile/bootstrap control operations.

## 6. Data Model

```yaml
server:
  host: "0.0.0.0"
  port: 8092
store:
  backend: sqlite           # Production default; memory is test-only; postgres is reserved for multi-replica deployments
  dsn: null
  event_retention_seconds: 2592000
identity:
  provider: static
  static_token_file: null
model_proxy:
  listen: "127.0.0.1:18080"
mcp_proxy:
  listen: "127.0.0.1:18081"
adapters:
  default_base: codex
  codex:
    transport: unix_websocket
    socket_path: /tmp/haas/codex.sock
session_runtime:
  lease_ttl_ms: 30000
  lease_renew_interval_ms: 10000
  turn_timeout_seconds: 86400
delegation:
  container_backend: disabled  # default; explicit delegated-session profile overrides to docker
  docker_bin: docker
  docker_network: isolated
  default_image_variant: lite
  allow_unpinned_local_image: false
  idle_ttl_seconds: 1800
  max_container_lifetime_seconds: 28800
  rw_workspace_concurrency: single_writer
  queue_policy: fifo
  restore_policy: fail_closed
  policy_change_mode: snapshot_per_session
  mount_policy: project_rw_extra_ro
```

`delegation` is an independent HaaS service configuration namespace. The standalone
service and packaged `local_api` sidecar default remain disabled so an operator does
not implicitly grant Docker control. The OpenHarness product profile is a separate
manager-owned launch profile: it defaults to HaaS `local_managed` + autostart and
non-containerized `/run_sse`; only an explicit delegated-session profile starts its
managed sidecar with `container_backend=docker`. Effective values are copied into the
delegated-session policy snapshot. Later configuration changes affect only new
delegated sessions unless an explicit policy update/rebind API call changes an existing
session.

The OpenHarness manager-owned launch profile materializes fresh-session policy defaults as
`workspace-write`, public-network `allow`, and approval `on-request`. These are profile/session
data, not process environment shortcuts. A one-time persisted-config migration writes missing
legacy fields with those values and a migration revision while preserving every explicit setting.
Process restart alone MUST NOT retroactively mutate an existing session; its policy changes only
through the revisioned session or delegated-session `/policy` endpoint.

Configuration-layer boundary: `AppConfig` decides process assembly, ports, store
backend, adapter assembly, and whether the delegation backend is available. It is
not the user-editable harness profile. Any manager-submitted runtime change to
provider/model, MCP, skills, AGENTS.md, workspace/policy, or budgets MUST be
written as a Harness Profile revision and must pass validate/activate or explicit
session rebind. Changing env/yaml MUST NOT implicitly drift running sessions.

Ports and service discovery are fixed as follows:

| Port | Service | Address |
|------|------|------|
| 8080 | nginx + OpenSandbox AIO | AIO only; unused in Lite |
| 8092 | HaaS sidecar | Host control: loopback; standalone Lite: container interface with host-loopback publication; AIO: nginx loopback upstream; delegated worker: private only |
| 18080 | model proxy | `127.0.0.1` only |
| 18081 | MCP/tool proxy | `127.0.0.1` only |

## 7. Runtime Model and State Machine

```text
startup -> load_config(HAAS_CONFIG) -> validate -> build AppConfig -> create_app(config) -> serve
```

## 8. Security and Authorization

- Provider keys and MCP tokens MUST NOT be accepted as plaintext environment variables or YAML values; only `credentialRef` is allowed.
- Configuration dumps and diagnostic output MUST be redacted and MUST NOT contain DSNs, keys, or passphrases.
- Loopback services (18080/18081) MUST bind only to `127.0.0.1`.
- Delegation policy configuration MUST NOT contain host paths or credentials. Host
  paths are supplied only through manager-approved mount manifests.

## 9. Observability

- `haas.config.loaded`
- `haas.config.validation_failed`
- A redacted summary of the effective configuration is written to the startup log.

## 10. Failure and Recovery

| Scenario | Behavior |
|------|------|
| Configuration missing/invalid | Startup fails fast and emits a safe reason |
| Configuration contains a plaintext secret | Startup is rejected with `haas_secret_input_invalid` |
| Port conflict | Startup fails and emits diagnostics |

## 11. Test Plan and Acceptance Criteria

- Unit: three-tier precedence, fail-fast behavior for invalid configuration, and secret rejection.
- Unit: standalone HaaS keeps delegated containers disabled by default, while the OpenHarness local-managed launch profile explicitly selects Docker; environment overrides produce a stable policy snapshot without mutating existing delegated sessions.
- Unit: changing `AppConfig` does not implicitly alter registry active profiles or
  existing session snapshots; dynamic harness configuration can become effective
  only through Harness Profile APIs.
- Integration: `create_app(config)` starts with a memory store and static identity and passes `/v1/haas/health`.
- Security: configuration files and dumps contain no plaintext secrets.
