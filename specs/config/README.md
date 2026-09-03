# Config Component Specification

**English** | [简体中文](README.zh-CN.md)

Status: Draft
Last reviewed: 2026-08-30
Related specs: [Container Runtime](../container-runtime/README.md), [Stores](../stores/README.md), [Identity](../identity/README.md), [HaaS Protocol](../haas-protocol/README.md)

## 1. Component Role

Config defines the configuration and assembly contract for HaaS: environment variables, configuration files, the port table, service discovery, and the app factory signature. It eliminates ambiguity caused by individual components inventing their own environment variables and `create_app` parameters.

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
- Does not support runtime hot reload in the initial release; configuration is static, with hot reload reserved for a later release.

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

The environment-variable prefix is uniformly `HAAS_`. For example:

| Variable | Meaning |
|------|------|
| `HAAS_CONFIG` | Configuration file path |
| `HAAS_SIDECAR_PORT` | Sidecar port (default: `8092`) |
| `HAAS_STORE_BACKEND` | Production default: `sqlite`; `memory` is test-only; `postgres` is reserved for multi-replica deployments |
| `HAAS_IDENTITY_PROVIDER` | `static` / `external_jwt` |

The default configuration file is `haas.yaml`. Precedence is: defaults < configuration file < environment variables.

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
model_proxy:
  listen: "127.0.0.1:18080"
mcp_proxy:
  listen: "127.0.0.1:18081"
adapters:
  default_base: codex
  codex:
    transport: unix_websocket
    socket_path: /tmp/haas/codex.sock
```

Ports and service discovery are fixed as follows:

| Port | Service | Address |
|------|------|------|
| 8080 | nginx + OpenSandbox AIO | External entry point for the HaaS container; preserves internal AIO routes |
| 8092 | HaaS sidecar | nginx upstream, `127.0.0.1` only |
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

## 9. Observability

- `haas.config.loaded`
- `haas.config.validation_failed`
- A redacted summary of the effective configuration is written to the startup log.

## 10. Failure and Recovery

| Scenario | Behavior |
|------|------|
| Configuration missing/invalid | Startup fails fast and emits a safe reason |
| Configuration contains a plaintext secret | Startup is rejected with `haas_config_secret_invalid` |
| Port conflict | Startup fails and emits diagnostics |

## 11. Test Plan and Acceptance Criteria

- Unit: three-tier precedence, fail-fast behavior for invalid configuration, and secret rejection.
- Integration: `create_app(config)` starts with a memory store and static identity and passes `/v1/haas/health`.
- Security: configuration files and dumps contain no plaintext secrets.
