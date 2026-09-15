# Sandbox Runtime 组件规格

[English](README.md) | **简体中文**

Status: Draft
Last reviewed: 2026-09-14
Change ID: unified-runtime-approval-policy
Related specs: [Architecture](../architecture/README.zh-CN.md), [Policy Controller](../policy-controller/README.zh-CN.md), [Harness Adapter](../harness-adapter/README.zh-CN.md), [Container Runtime](../container-runtime/README.zh-CN.md), [Security Boundary](../security-boundary/README.zh-CN.md), [Manager Delegation](../manager-delegation/README.zh-CN.md)

## 1. 组件定位

Sandbox Runtime 把 `EffectivePolicy` 与 adapter 声明投影为一个隔离合同，由 Lite Docker（默认）或 OpenSandbox AIO 实现。Harness-native sandbox 仍是内层；两种实现都执行外层 workspace、resource、network 和 credential 边界。

下文 OpenSandbox sandbox/execd/vault API 仅适用于 AIO。Lite 使用 Docker 生命周期、private worker/broker network 和 retained session volume，不模拟不存在的 AIO endpoint。

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
| 下游 | Lite Docker runtime | 默认 container lifecycle、mount、resource limit 与 private worker/broker network |
| 下游 | OpenSandbox AIO | 可选 sandbox/execd/vault/egress 投影 |

## 4. 职责边界

负责：

- 把 `EffectivePolicy` 编译为 `SandboxSpec`（workspace mounts、writable roots、network egress、resource limit）。
- 校验并投影 delegated session 中 manager 已授权的 mount manifest。
- 为每个 delegated session 创建并跟踪选定的 Lite/AIO sandbox 实现。
- 把 harness adapter 的 sandbox 声明收窄投影（只允许小于等于 policy 的范围，不允许扩大）。
- 真实 secret 只配置到 AIO vault 或可信 Lite broker memory，adapter 只拿 scoped 短 token。
- 通过选定 private worker transport 运行 harness process，并为 adapter 归一化结果。
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

### 5.1 Lite 隔离与共同运行保证

- Lite 使用现有 runtime interface、Docker CPU/memory/PID 限制、只读根文件系统、独立可写 workspace/home/tmp、non-root worker、drop capabilities 与 no-new-privileges；AIO 除服务投影外满足同样的有效约束。
- Worker 只接入 Container Runtime 的 per-session internal network。外部 broker 不是 router，只接受冻结 invocation revision 对应的认证 model/MCP 操作。必须用网络边界而非指令禁止 worker internet、DNS 绕过、host/metadata 和跨 session broker 访问，覆盖 IPv4/IPv6、redirect 与 DNS rebinding。
- Lite P0 支持 shell/file 和 brokered model/MCP HTTP/SSE，不支持任意工具外网和 browser/VNC；需要不支持的 egress/执行能力时返回 `haas_policy_unsupported`，不能改用无限制 Docker network。
- 只有 broker 解析长期 credential。Worker 内 loopback relay 只持有短 TTL 的 session/audience/generation token，原生进程不接收真实上游凭证；broker 失败阻止执行，不开放直连。
- `create_sandbox` 挂载保留的 session volume，验证全部授权 mount root（含重叠）及 enforcement，再允许执行。Mount 更新在 drain 后重建资源，保持逻辑 session；只有 native reference 而没有 native state 不算 restore 成功。
- Control sidecar 拥有 public acceptance/event id/terminal。Worker start 使用持久去重 execution id 和 fenced generation，私有 inspect/cancel/replay 核对双方状态，不能创建第二个公共 invocation。
- Workspace writer ownership 覆盖所有 writable mount 及不同 session 的父子重叠目录；必须确认旧 worker 不能再写才释放，不能只看 terminal 数据库字段。队列默认最多 100 个等待 turn、等待 300 秒；timeout/cancel 移除 waiter。Admission、配置应用和 volume 清理使用相同 ownership 边界。

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
    "defaultAction": "allow",
    "allow": []
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
  "approvalMode": "on-request",
  "nativeSandbox": {
    "supported": true,
    "mode": "workspace-write"
  }
}
```

`compile_sandbox_spec` 首期只消费 `cwd`、`writableRoots`、`approvalMode`；`base` 与 `nativeSandbox` 是 adapter 能力声明，供后续 OpenSandbox 投影（S5.3）使用。

Fresh session 产品默认值是 `workspace-write + 公网 allow + on-request`。三个维度彼此
独立：修改 approval mode 不改变 mount 或 egress，批准单个动作也不重建或放宽外层 sandbox。
公网允许不等于 host network；private/link-local/metadata/control-plane/跨 session 路由继续
阻断。动作依赖所选 Lite/AIO 变体无法提供的外层能力时不可审批，并返回
`haas_policy_unsupported`。

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
  -> 创建选定 Lite/AIO sandbox
  -> 签发短 token，真实 secret 仅配置到 broker/vault
  -> 通过 private worker transport 启动 harness runtime
  -> turn 执行并回传 normalized event
  -> sandbox destroyed at session close
```

Sandbox 实例状态：

```text
requested -> creating -> running -> draining -> stopped -> destroyed
                  |                     +-> failed
```

规则：

- sandbox instance 跟随 session；删除立即撤销 access/admission 并调度 fenced physical cleanup，清理失败也不能允许第二 writer。
- Running invocation 使用 immutable sandbox/policy revision。动态 approval、network、
  workspace、mount 或 image policy update 通过 session revision barrier 暂存，只能在后续
  invocation 前应用。若需改变外层 sandbox，必须 drain 并重建后才能推进
  `appliedRevision`，不得原地修改 running sandbox。
- 当前动作 approval 通过 adapter bridge 恢复等待中的 harness request；它绝不改变外层
  sandbox、mount set、network namespace、platform hard deny 或未来 invocation 默认值。
- adapter 声明的 writableRoots 若超出 policy，compile 阶段拒绝，不允许静默扩大。
- provider key 只能存在于 AIO vault 或可信 Lite broker memory，不能进入 worker env、mount、启动参数或 session volume。
- 沙箱重启后 `generation` 增加；无法恢复的 harness thread 由 adapter 判定并上报。

## 8. 安全与权限

- Sandbox 隔离是运行时硬边界，不是唯一边界（纵深防御）。
- harness 自带 sandbox 只能作为内层，不能绕过选定 Lite/AIO runtime enforcement。
- Runtime token 按 session/audience/generation 隔离，短 TTL、可撤销；真实 secret 留在 AIO vault 或 Lite broker memory。
- 所有 workspace 路径 canonicalize 后与 policy 比较。
- 网络 egress 由 Lite isolated broker network 或 AIO egress 加 HaaS URL validator 双层约束，runtime enforcement 与 URL validation 缺一不可。
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
| vault/broker 配置失败 | turn 不启动，返回 `haas_vault_unavailable` |
| egress 阻断网络 | 按 deny 处理；安全事件记录 host（脱敏） |
| Approval 请求外层 sandbox 不支持的能力 | 以 `haas_policy_unsupported` 拒绝为不可审批 |
| Policy revision 的 sandbox rebuild 失败 | 保留旧 applied revision、阻断新 turn 并提供安全重试动作 |
| 销毁失败 | 保留清理队列，重试；不阻塞 session 状态收敛 |

## 11. 测试计划与验收

- Unit：SandboxSpec 编译、delegated mount 校验、narrow-only 投影拒绝、路径 canonicalization、egress 编译。
- Integration：Lite/AIO 共同 lifecycle/token/replay test；AIO 额外覆盖 sandbox/execd/vault API。
- Security：provider key 不进 sandbox env/启动命令/日志；widening 全部拒绝。
- E2E：Codex turn 在 sandbox 内完成文件读写并产出 artifact，verify 路径受限。
- Defaults：fresh Lite/AIO session 使用 workspace write、公网 egress 与 on-request approval，
  同时 host/private/metadata/control-plane 路径保持不可达。
- Revision：command 执行期间修改 network/workspace/approval，证明当前 sandbox 不变，下一
  invocation 等待 replacement generation。
- Approval boundary：批准动作不能添加 host mount、加入 host network、泄露 credential，
  也不能开启所选 runtime 无法 enforce 的能力。

## 12. 下一步验证

- 确认 OpenSandbox sandbox API 与 execd 的本机可用版本和精确 endpoint（`Unknown`，待实现阶段用 pin commit 验证）。首期 `OpenSandboxClient` 假设 REST endpoint 为 `POST/GET/DELETE /sandboxes[/{id}]`、`POST /sandboxes/{id}/exec`、`POST /vault/secrets`，并作为类常量暴露以便探测后修正。
- 确认 Codex 在 Lite/AIO 内都以 non-root 运行，且只能通过 scoped model/MCP relay 连接。
- 真实 OpenSandbox 探测用显式开关 `HAAS_E2E_OPEN_SANDBOX=1`；未开开关时 OpenSandbox client 测试为 offline mock（`httpx.MockTransport`）。
