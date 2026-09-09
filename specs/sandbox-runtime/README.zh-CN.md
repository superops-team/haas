# Sandbox Runtime 组件规格

[English](README.md) | **简体中文**

Status: Draft
Last reviewed: 2026-08-26
Related specs: [Architecture](../architecture/README.zh-CN.md), [Policy Controller](../policy-controller/README.zh-CN.md), [Harness Adapter](../harness-adapter/README.zh-CN.md), [Container Runtime](../container-runtime/README.zh-CN.md), [Security Boundary](../security-boundary/README.zh-CN.md), [Manager Delegation](../manager-delegation/README.zh-CN.md)

## 1. 组件定位

Sandbox Runtime 是 HaaS 多 harness 运行环境标准化的承载体。它把 Policy Controller 编译出的 `EffectivePolicy` 与 harness adapter 声明的 sandbox 需求，统一投影为 OpenSandbox AIO 的 sandbox/execd/credential vault 配置，使不同 harness（Codex、Pi、OpenCode、AMP）在同一个隔离模型下执行。

它解决的问题：harness 各自携带 sandbox 语义（Codex sandbox policy、OpenCode permission config、Pi workspace isolation）。Sandbox Runtime 让这些差异收敛到 OpenSandbox 一套底层能力上，而不是让 HaaS 为每个 harness 拼装不同的隔离策略。

## 2. 来源与依据

| 来源 | 采用内容 |
|------|----------|
| OpenSandbox docs | sandbox lifecycle（create/start/stop/delete）、execd 命令执行、credential vault、egress policy |
| OpenSandbox AIO | 镜像内 shell/file/browser 能力、sandbox API 端口 `8080` |
| Codex app-server | 自带 sandbox policy，只能作为内层，不替代 HaaS 外层 sandbox |
| Policy Controller | workspace/network/tool/approval 的有效策略投影 |

## 3. 上游与下游关系

| 方向 | 对象 | 关系 |
|------|------|------|
| 上游 | Policy Controller | 提供 `EffectivePolicy`（workspace/network/tool/approval） |
| 上游 | Harness Adapter | 提供 harness-specific sandbox 声明（writableRoots、cwd、approvalMode） |
| 上游 | Session Runtime | 请求为 session/invocation 创建 sandbox 实例 |
| 上游 | Artifact Store | 报告 workspace 文件索引与产物路径 |
| 下游 | OpenSandbox sandbox API | 创建/销毁 sandbox 实例 |
| 下游 | OpenSandbox execd | 在 sandbox 内执行命令，支持 SSE result |
| 下游 | OpenSandbox credential vault | 存 provider/key 引用，按 session 签发短 token |
| 下游 | OpenSandbox egress | 网络 egress policy |

## 4. 职责边界

负责：

- 把 `EffectivePolicy` 编译为 `SandboxSpec`（workspace mounts、writable roots、network egress、resource limit）。
- 校验并投影 delegated session 中 manager 已授权的 mount manifest。
- 为每个 session/invocation 创建并跟踪 OpenSandbox sandbox 实例生命周期。
- 把 harness adapter 的 sandbox 声明收窄投影（只允许小于等于 policy 的范围，不允许扩大）。
- 把 provider/key 密钥写入 credential vault，adapter 只拿 vault 引用或短 token。
- 在 sandbox 内运行 harness runtime 进程，并把 execd 输出桥接给 adapter。
- 记录 sandbox 生命周期与安全事件。

不负责：

- 不决定 workspace/network/tool policy（只消费 Policy Controller 输出）。
- 不接受 harness 或 adapter 任意传入的 host path；delegated-session host path 必须已由 manager 授权并经 Policy Controller 校验。
- 不执行 harness 的原生协议（那是 adapter 职责）。
- 不替代 HaaS protocol 的 public API。
- 不保存长期 provider credential 明文；vault 内容是引用。

## 5. 核心接口

```python
async def compile_sandbox_spec(policy: EffectivePolicy, decl: HarnessSandboxDecl) -> SandboxSpec: ...
async def create_sandbox(session_id: str, spec: SandboxSpec) -> SandboxHandle: ...
async def run(sandbox_id: str, command: list[str], cwd: str, env: dict) -> ExecStream: ...
async def write_secret(session_id: str, audience: str, ref: str, ttl: int) -> VaultRef: ...
async def project_egress(spec: SandboxSpec) -> EgressPolicy: ...
async def validate_mount_manifest(manifest: MountManifest, policy: EffectivePolicy) -> MountValidation: ...
async def destroy_sandbox(sandbox_id: str) -> None: ...
async def inspect_sandbox(sandbox_id: str) -> SandboxInspection: ...
```

## 6. 数据模型

### 6.1 SandboxSpec

```json
{
  "sessionId": "hsess_abc",
  "workspaceRoot": "/workspace",
  "writableRoots": ["/workspace"],
  "readOnlyRoots": ["/mnt/extra/shared"],
  "mounts": [
    {
      "hostPathCanonical": "/Users/example/workspace/project",
      "containerPath": "/workspace",
      "access": "rw"
    },
    {
      "hostPathCanonical": "/Users/example/workspace/shared",
      "containerPath": "/mnt/extra/shared",
      "access": "ro"
    }
  ],
  "isolatedWritableRoots": ["/home/haas", "/tmp", "/data/haas/cache"],
  "network": {
    "defaultAction": "deny",
    "allow": ["https://api.openai.com"]
  },
  "resources": {
    "cpu": 2,
    "memoryMb": 4096,
    "timeoutSeconds": 1800
  },
  "credentialVault": {
    "providerKeyRef": "secret://tenant/workspace/provider/default"
  }
}
```

### 6.2 HarnessSandboxDecl（adapter 声明）

```json
{
  "base": "codex",
  "cwd": "/workspace",
  "writableRoots": ["/workspace"],
  "approvalMode": "never",
  "nativeSandbox": {
    "supported": true,
    "mode": "workspace-write"
  }
}
```

`compile_sandbox_spec` 首期只消费 `cwd`、`writableRoots`、`approvalMode`；`base` 与 `nativeSandbox` 是 adapter 能力声明，供后续 OpenSandbox 投影（S5.3）使用。

### 6.3 Delegated Mount Manifest

Manager-delegated session 使用
[Manager Delegation](../manager-delegation/README.zh-CN.md) 定义的 mount manifest。
Sandbox Runtime 在容器创建前和每次 restore 前校验 manifest：

- primary project mount 必须严格是 `/workspace:rw`；
- extra mount 默认 `ro`，并使用 `/mnt/extra/*` 下的确定性路径；
- host path canonicalize 后与授权快照比较；
- symlink escape、路径消失、路径类型变化、Docker socket、用户 HOME、SSH 目录、
  credential store 和父目录扩大均 fail closed；
- sandbox 提供独立可写 HOME、cache 与 `/tmp`，不挂载 host HOME。

### 6.4 SandboxHandle

```json
{
  "sandboxId": "sbx_abc",
  "sessionId": "hsess_abc",
  "status": "running",
  "generation": 1,
  "createdAtMs": 1786400000000
}
```

## 7. 运行模型与状态机

```text
policy compiled
  -> harness sandbox decl collected
  -> sandbox spec compiled (narrow-only projection)
  -> sandbox created
  -> secret written to vault
  -> harness runtime started inside sandbox
  -> turn executes (execd streams back)
  -> sandbox destroyed at session close
```

Sandbox 实例状态：

```text
requested -> creating -> running -> draining -> stopped -> destroyed
                  |                     +-> failed
```

规则：

- sandbox 实例跟随 session；session 删除必须同步销毁 sandbox。
- adapter 声明的 writableRoots 若超出 policy，compile 阶段拒绝，不允许静默扩大。
- provider key 只能写入 vault，不能出现在 sandbox env 或启动命令参数中。
- 沙箱重启后 `generation` 增加；无法恢复的 harness thread 由 adapter 判定并上报。

## 8. 安全与权限

- Sandbox 隔离是运行时硬边界，不是唯一边界（纵深防御）。
- harness 自带的 sandbox 只能作为内层，不能绕过 OpenSandbox sandbox/egress。
- credential vault 的粒度到 session/audience，短 TTL，可撤销。
- 所有 workspace 路径 canonicalize 后与 policy 比较。
- 网络 egress 由 OpenSandbox egress policy 与 HaaS URL validator 双层约束，缺一不可。
- sandbox env、启动命令、execd 输出在进入日志/事件前必须脱敏。

## 9. 可观测性

Events/logs：

- `haas.sandbox.spec_compiled`
- `haas.sandbox.created`
- `haas.sandbox.destroyed`
- `haas.sandbox.run_failed`
- `haas.sandbox.vault_write`
- `haas.sandbox.widening_rejected`

Metrics：

- `haas_sandbox_active`
- `haas_sandbox_create_duration_ms{status}`
- `haas_sandbox_run_total{adapterBase,status}`
- `haas_sandbox_vault_write_total{audience}`

## 10. 失败与恢复

| 场景 | 行为 |
|------|------|
| sandbox 创建失败 | invocation failed；不 fallback 到无 sandbox 执行 |
| adapter 声明超出 policy | `haas_sandbox_widening_rejected`，fail closed |
| sandbox 重启 | generation 增加；adapter inspect 判定 thread 是否可恢复 |
| delegated mount manifest 漂移 | 返回 `haas_delegation_mount_invalid`；要求 manager 重新授权或 policy rebind |
| vault 写入失败 | turn 不启动，返回 `haas_vault_unavailable` |
| egress 阻断网络 | 按 deny 处理；安全事件记录 host（脱敏） |
| 销毁失败 | 保留清理队列，重试；不阻塞 session 状态收敛 |

## 11. 测试计划与验收

- Unit：SandboxSpec 编译、delegated mount 校验、narrow-only 投影拒绝、路径 canonicalization、egress 编译。
- Integration：OpenSandbox sandbox create/run/destroy、execd SSE result、credential vault 写入与短 token 撤销。
- Security：provider key 不进 sandbox env/启动命令/日志；widening 全部拒绝。
- E2E：Codex turn 在 sandbox 内完成文件读写并产出 artifact，verify 路径受限。

## 12. 下一步验证

- 确认 OpenSandbox sandbox API 与 execd 的本机可用版本和精确 endpoint（`Unknown`，待实现阶段用 pin commit 验证）。首期 `OpenSandboxClient` 假设 REST endpoint 为 `POST/GET/DELETE /sandboxes[/{id}]`、`POST /sandboxes/{id}/exec`、`POST /vault/secrets`，并作为类常量暴露以便探测后修正。
- 确认 Codex app-server 进程能稳定在 OpenSandbox sandbox 内以非 root 运行并点对点联通 loopback model/MCP proxy。
- 真实 OpenSandbox 探测用显式开关 `HAAS_E2E_OPEN_SANDBOX=1`；未开开关时 OpenSandbox client 测试为 offline mock（`httpx.MockTransport`）。
