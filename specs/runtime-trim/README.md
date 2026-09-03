# Runtime Trim Component Specification

**English** | [简体中文](README.zh-CN.md)

Status: Draft
Last reviewed: 2026-09-03
Change ID: haas-runtime-trim
Related specs: [Container Runtime](../container-runtime/README.md), [Startup](../startup/README.md), [Sandbox Runtime](../sandbox-runtime/README.md), [Security Boundary](../security-boundary/README.md), [Observability](../observability/README.md)

## 1. Component Role

Runtime Trim defines how HaaS **disables unneeded AIO services** in the OpenSandbox AIO base image to reduce runtime memory, CPU and port usage, and attack surface. It is the sole authoritative owner of the cross-cutting concern of AIO service trimming: [Container Runtime](../container-runtime/README.md) defines image/process/port boundaries, while this component defines only which AIO capabilities are preserved, which are disabled, how they are disabled, and how the result is verified.

Core constraint: trimming MUST only disable services at runtime through **officially supported AIO environment variables**. It MUST NOT fork or privately modify AIO startup scripts or supervisor definitions, or delete files from the AIO base image. This component explicitly distinguishes two kinds of optimization:

- **Runtime disabling (in scope for this component, P1)**: use `DISABLE_*` / `NODE_VERSION` to stop processes from starting. This reduces memory/CPU/port usage and attack surface, but **does not reduce image-layer size**.
- **Image-layer trimming (a non-goal for this component; see §4)**: use multi-stage copying or flattening to actually reclaim bytes from base layers. This entails greater risk and engineering effort and is separate follow-up work outside this iteration.

## 2. Sources and Rationale

| Source | Adopted content |
|--------|-----------------|
| OpenSandbox AIO `gem_env.sh` / `gem.sh` (local inventory of pinned digest, 2026-09-03) | semantics of `DISABLE_CODE_SERVER`, `DISABLE_JUPYTER`, `DISABLE_NODEJS_REPL`, `NODE_VERSION`, and `AUTOSTART_*` |
| AIO `supervisord/*.conf` | autostart for code-server / jupyter / nodejs-repl / gost / mcp-browser / vnc / browser is controlled by `AUTOSTART_*` |
| AIO `gem.sh` gost branch | gost starts only when `PROXY_SERVER` is set; otherwise its supervisor definition is removed during startup |
| Local validation (2026-09-03) | `rm` in a child layer does not reduce image size; ENV `DISABLE_*` correctly maps to `AUTOSTART_*=false`; nginx/`chromium`/`Xtigervnc`/`xdotool`/`python3.12`/`node`/`/opt/gem/run.sh` remain after trimming |
| AGENTS.md, “Docker and OpenSandbox AIO” | MUST NOT fork or privately modify base AIO capabilities; preserve `/opt/gem/run.sh` |

## 3. Upstream and Downstream Relationships

| Direction | Component | Relationship |
|-----------|-----------|--------------|
| Upstream | Container Runtime | Sets trim environment variables in the runtime ENV layer; this component refines its trim contract |
| Upstream | Startup | Confirms trimmed services are not on the critical path and do not participate in readiness decisions |
| Downstream | OpenSandbox AIO `gem_env.sh`/`gem.sh` | Consumes `DISABLE_*` / `NODE_VERSION` and produces `AUTOSTART_*` |
| Downstream | AIO supervisor | Determines whether each program starts based on `AUTOSTART_*` |
| Downstream | Sandbox Runtime | Depends on trimming not affecting sandbox/execd/credential vault |
| Downstream | Observability | Records trim configuration and the list of disabled services |

## 4. Responsibility Boundaries

Responsibilities:

- Define the AIO services HaaS disables by default and their corresponding official switches.
- Define the AIO capabilities that MUST be preserved (the CUA/BUA, sandbox, and Codex readiness substrate).
- Constrain trimming to official AIO environment variables, without changing AIO scripts/supervisor.
- Define trim acceptance checks (static assertions plus negative assertions in container smoke tests).
- Record why gost/`18080` do not conflict by default and the revalidation required if gost is enabled in the future.

Non-responsibilities:

- It does not perform image-layer trimming (multi-stage copying, flattening, or squashing). If actual size reduction is needed in the future, it MUST be a **separate project**, and this spec MUST be extended with a trim inventory, size baseline, and evidence that CUA/BUA/sandbox have not regressed; this iteration explicitly does not implement it.
- It does not define the image base, process topology, port ownership, or health/ready behavior; those belong to Container Runtime / Startup.
- It does not add or remove services outside AIO.
- It does not change any northbound API, event, or error code; this component has zero impact on the public compatibility surface.

## 5. Core Interface

The trimming interface consists of official AIO environment variables declared in the Container Runtime runtime ENV layer.

### 5.1 Disabled by Default (without affecting CUA/BUA, sandbox, or Codex readiness)

| Service | AIO environment variable | Reason for disabling |
|---------|--------------------------|----------------------|
| code-server (VSCode) | `DISABLE_CODE_SERVER=true` | HaaS does not provide an online IDE |
| JupyterLab | `DISABLE_JUPYTER=true` | HaaS does not provide notebooks |
| Multi-version Node.js REPL server (20/22/24) | `DISABLE_NODEJS_REPL=true` | HaaS does not use the AIO REPL |
| Redundant Node version autostart | `NODE_VERSION=node22` | Pins one default version, matching the AIO default |

### 5.2 Capabilities That MUST Be Preserved (CUA/BUA and Isolation Substrate)

browser (chromium), VNC/noVNC, MCP browser, sandbox/execd, credential vault, `/opt/gem/run.sh`, and nginx. These MUST NOT be trimmed or accidentally disabled.

### 5.3 Mechanism Rules

- AIO `gem.sh` normalizes `DISABLE_*` and maps it to `AUTOSTART_*=false`; supervisor then refrains from starting the corresponding program. This is the official AIO path and does not modify scripts.
- Trim variables MUST be placed in the runtime ENV near the final layer so they do not invalidate the dependency-layer cache.
- Using `rm` in a child layer, `sed -i`, or replacement supervisor files for “trimming” is prohibited: these approaches neither reduce size nor comply with the prohibition on privately modifying AIO.
- Before adding a trim item, it MUST be confirmed that the item is not a dependency of CUA/BUA, sandbox, or Codex readiness.

## 6. Data Model

Trim configuration is a set of boolean/enumerated environment variables with no persisted state:

```json
{
  "disableCodeServer": true,
  "disableJupyter": true,
  "disableNodejsRepl": true,
  "nodeVersion": "node22",
  "preserved": ["browser", "vnc", "mcp_browser", "sandbox", "execd", "credential_vault", "nginx", "opt_gem_run_sh"]
}
```

When trim configuration enters diagnostics status, it MUST be redacted and MUST NOT contain credentials, absolute socket paths, or tokens.

## 7. Runtime Model and State Machine

Trimming takes effect once during startup and has no independent state machine. It acts on the [Startup](../startup/README.md) startup DAG:

```text
container starting
  -> gem_env/gem.sh reads DISABLE_* / NODE_VERSION
  -> maps them to AUTOSTART_*=false
  -> supervisor skips code-server / jupyter / nodejs-repl
  -> preserved services and HaaS sidecar / Codex readiness start normally
```

The absence of trimmed services MUST NOT change nginx, sidecar, or Codex readiness behavior. They are not on the critical path and do not participate in the overall readiness decision.

## 8. Security and Permissions

- Reducing resident services reduces attack surface, but security boundaries remain defined by policy/sandbox/secret controls; trimming is not a substitute for a security control.
- Trim variables MUST NOT carry credentials, tokens, or sensitive paths.
- The gost forwarding proxy starts only when `PROXY_SERVER` is set; otherwise AIO removes its supervisor definition during startup, so there is no conflict by default with `18080`, which is reserved for the HaaS model proxy. **If gost is enabled in the future, ownership of `18080` MUST be reviewed and this spec and the Container Runtime port table MUST be updated.**
- Trimming MUST NOT disable VNC/browser, or CUA/BUA capabilities will regress to unavailable.

## 9. Observability

- Diagnostics status exposes the current redacted trim configuration so operators can confirm which AIO services are disabled.
- Events/logs use safe fields: disabled service names and `NODE_VERSION`, with no sensitive values.
- Adding no metrics is acceptable. If one is added, a one-time gauge named `haas_runtime_trim_disabled_total{service}` is recommended.

## 10. Failures and Recovery

| Scenario | Behavior |
|----------|----------|
| Preserved service accidentally disabled (for example, `DISABLE_BROWSER=true`) | CUA/BUA regress to unavailable; docker-check smoke MUST detect missing browser/VNC |
| Switch semantics change after an AIO upgrade | when upgrading the base digest, the `DISABLE_*`/`AUTOSTART_*` semantics MUST be inventoried again and smoke tests MUST run |
| Setting `PROXY_SERVER` triggers gost | `18080` may conflict; port ownership MUST be reviewed and MUST NOT be shared silently |
| Trimming makes sandbox/execd unavailable | this is a regression defect; fail closed and do not accept it |

## 11. Test Plan and Acceptance

- Static (default `make docker-check` layer): assert that the Dockerfile sets `DISABLE_CODE_SERVER=true`, `DISABLE_JUPYTER=true`, and `DISABLE_NODEJS_REPL=true`.
- Build/smoke (`HAAS_DOCKER_BUILD=1 make docker-check`):
  - Assert that code-server / jupyter are not running in the container.
  - Assert that AIO `8080`, HaaS `8092`, and assembly of the real codex adapter in `/v1/haas/status` remain functional.
  - Assert that the browser/VNC substrate remains present (an explicit browser/VNC readiness probe MAY be added later).
- Upgrade regression: rerun the smoke tests above when the base digest changes and verify that `DISABLE_*` semantics have not drifted.
- Security: secret scan confirms that trim ENV values contain no credentials.

## 12. Task Breakdown

- P1 (this iteration): set `DISABLE_CODE_SERVER`/`DISABLE_JUPYTER`/`DISABLE_NODEJS_REPL`/`NODE_VERSION` in the Dockerfile; add docker-check static + smoke gates; align container-runtime/startup references.
- P2 (future, separate project): actual image-layer trimming (multi-stage copying or flattening), including a size baseline, preservation of ENTRYPOINT/ENV, and evidence that CUA/BUA/sandbox have not regressed.
