# Container Runtime 组件规格

[English](README.md) | **简体中文**

Status: Draft
Last reviewed: 2026-09-10
Change ID: manager-haas-sidecar-spec
Related specs: [Startup](../startup/README.zh-CN.md), [Sandbox Runtime](../sandbox-runtime/README.zh-CN.md), [Config](../config/README.zh-CN.md), [Manager Delegation](../manager-delegation/README.zh-CN.md), [Security Boundary](../security-boundary/README.zh-CN.md)

## 1. 组件定位

Container Runtime 管理镜像选择、Docker 生命周期、挂载、持久运行数据、进程监督和平台验证。镜像变体、CPU 架构、执行后端身份是三个独立维度；两个镜像变体使用同一 HaaS 协议。

## 2. 来源与依据

产品默认 Lite。Mac Apple Silicon 使用 Docker CLI 在本机构建和运行 arm64 容器，不需要 Linux 构建主机或 amd64 交叉构建。镜像是 Linux OCI 镜像（`linux/arm64`），不是 Darwin 容器；所需宿主虚拟化由 Docker Engine 运行环境提供。Apple `container` 不是必备后端，不承诺 Linux 容器完全无 VM。

## 3. 上游与下游关系

Manager 选择执行端点并授权 workspace。Config 选择 Docker backend 和镜像目录。Sandbox Runtime 校验隔离，Container Runtime 创建资源，harness adapter 管理原生执行。公共 session/invocation 的持久事实由控制 sidecar 管理，不由 container id 决定。

## 4. 职责边界

- 同时支持 Lite/AIO，不降低认证、secretless、workspace、资源与 egress policy。
- Docker daemon 权限仅属于可信控制 sidecar，禁止把 socket 挂载进执行容器。
- 仅 AIO 保留原启动脚本和官方 service-trim 配置。
- 退出前停止接收工作、收敛活动 invocation、flush 状态并向子进程组转发 SIGTERM。
- 必需 worker/sidecar 死亡时容器非零退出；原生 adapter 失败使 execution unavailable，直到恢复。
- 不把模型调用、远程下载或可选 browser/MCP warmup 放在进程存活关键路径。

## 5. 核心接口

### 5.1 镜像与平台矩阵

| Variant | Dockerfile 目标 | 平台 | 内容 |
|---------|-----------------|------|------|
| `lite`（默认） | `docker/Dockerfile.lite` | `linux/arm64`、`linux/amd64` | slim Python runtime、锁定的 HaaS 依赖、pin 的 Codex binary、git、CA 证书、shell、tini；仅支持执行所必需的依赖 |
| `aio`（可选） | 现有根目录 `Dockerfile` | `linux/amd64` | digest pin 的 OpenSandbox AIO 加 HaaS/Codex；保留 browser/VNC 和官方启动链 |

这是目标合同，不代表 Lite 已实现。保留现有 AIO Dockerfile 路径，不重复创建第二份 AIO Dockerfile。Lite 不含 AIO、nginx、browser、VNC、IDE、notebook 和开发/测试依赖组；其他语言工具链通过显式派生镜像提供，不放入默认 Lite。

Release base 和 Codex 分发物按架构 pin digest/checksum。可变本地 tag 需要显式开发开关。记录 OCI index digest 与实际选中的 platform manifest digest；restore 使用记录的 manifest，不重新解析 `latest`。

### 5.2 构建与运行合同

- 目标命令 `make docker-build-lite` 使用 Docker CLI 构建一个选定平台；Lite 实现门禁通过后 `make docker-build` 默认委托它。Apple Silicon + 本地 arm64 Engine 显式选择 `linux/arm64`。
- `make docker-build-aio` 保留 `linux/amd64`；arm64 主机运行 AIO 需要显式可用的模拟环境，不能静默选择。
- Release 分别验证两种 Lite 平台，再组合 OCI index；本机测试不要求多架构 `--load`，也不要求 push registry。
- 按 Docker 执行节点选择架构，而非远程 Manager 的架构。Manager 本地 bind mount 要求 operator 确认的本地 Docker context，远程 context 不能解释 Manager 本地路径。
- Preflight 检查 Docker CLI/daemon、context locality、架构、image manifest、挂载权限、磁盘容量及必需隔离，不支持的组合在用户工作开始前失败。
- 目标 `make docker-check` 验证 Lite，`make docker-check-aio` 验证 AIO；迁移实现前现有 AIO 命令行为不变，不能因 spec 修改而宣告 runtime 支持。
- Lite 每平台压缩预算 400 MiB，解包预算 1.2 GiB，测量不计 build cache；这些是验收目标，不是实测结果。

### 5.3 进程、端口与就绪

Lite 使用 tini 和独立最小 entrypoint，不复用当前 AIO-only 启动脚本。AIO 保留 `/opt/gem/run.sh`，使用 `NODEJS_REPL_PORT_22` 把 node22 REPL 从 8092 移至 8093，并应用 [Runtime Trim](../runtime-trim/README.zh-CN.md)。

| Surface | Lite | AIO |
|---------|------|-----|
| Host control sidecar | Loopback 8092 或选定本地端口 | 相同 |
| 独立容器 API | 容器接口 8092，默认仅 publish 到 host loopback | nginx 8080 转发 sidecar loopback 8092 |
| Delegated worker API | 私有容器网络 listener，不 publish 到 host/public | 相同信任边界，不公开 AIO 辅助路由 |
| Harness model/MCP relay | Loopback 18080/18081，仅 scoped runtime token | 相同 |

`/health` 表示 HTTP 进程存活。`ready?scope=control` 只依赖 identity/config/store 初始化，不依赖 Codex 或已配置 profile。`ready?scope=execution` 额外要求 adapter/runtime/isolation 就绪。Profile 专属校验在 invocation acceptance 前完成；可选 AIO 服务不阻塞 Lite ready。握手及退出合同见 Startup。

### 5.4 内部 Delegated 执行

Host control sidecar 仅接受一次公共 invocation，持久化到确定性 worker execution id 的映射。Worker role 不独立接受公共 ADK session，也不能递归创建 delegated container。私有 start/inspect/cancel/event API 使用 generation-scoped 服务认证和控制端分配的 execution id，在 session volume 持久化去重事实。Worker 上报 normalized fact，仅控制 sidecar 分配 public event id 和终态。连接断开后 inspect/replay，不能盲目启动第二次 native turn；崩溃后无法确定 native start 结果时必须对账或明确失败，不能自动重执行。

执行容器接入 per-session internal Docker network。可信 model/MCP broker 同时接入该网络及独立 egress network，但不转发任意 IP 流量。Worker 不接外网或 host network。Loopback relay 将带认证的 model/MCP 请求交给 broker；broker 执行 frozen route policy，并在执行容器外解析 secret。不提供通用 CONNECT、任意 URL 转发或 credential-read API。执行约束及凭证配置见 Sandbox Runtime 和 Security Boundary。
Delegated policy snapshot 携带 `network.defaultAction` 与 `network.allow`。当前
`--network none` Docker backend 只支持 deny；在 isolated egress broker 能执行该策略前，
allow 请求必须以 `haas_policy_unsupported` 失败关闭。

## 6. 数据模型与持久化

`DelegatedImage` 记录 `reference`、可选 digest 输入、`variant` 和解析后的 `platform`；执行前记录选中 manifest digest。Runtime identity 为 `(delegatedSessionId, containerGeneration)`，不是新公共 session。

独立于容器可写层的持久资源：

- Control store：session、invocation、canonical event、目标/已应用配置、worker mapping 与幂等事实。
- 挂载到 `/data/haas` 的 per-session Docker volume：Codex home/history、配置材料化 generation、skill/AGENTS.md 字节及可恢复 worker receipt。
- Artifact Store 中的已发布文件字节，运行 volume 回收前完成复制。

不把控制数据库或宿主用户 HOME 挂载到 worker。Worker/broker 使用独立身份，无 privileged container、无 Docker socket，仅保留最小内核 capability。Native 文件属于私有执行数据，不进入 diagnostics/artifact，不作为 secret store。只有 native reference 而没有必要字节，不算可恢复。

## 7. 生命周期与配置更新

每个 delegated session 最多一个 active writer container。追问复用容器；idle TTL 默认 1800 秒，最大存活 28800 秒。达到最大存活时间后允许当前工作完成，随后在等待的追问开始前自动重建。

Idle TTL 与配置重建只移除进程/容器层，不删除 session volume、公共历史或逻辑 binding。应用配置时等待 invocation 完全停止、flush native state、停止旧进程组、重新验证挂载，必要时重建资源。Mount 或 variant 变更使用授权的 `/policy` 更新，保持逻辑 session；原生续接不支持时报告应用失败，不静默新建 chat。准备资源失败不能降低任何已发布 profile version。

删除先关闭 admission 并取消 pending 更新/turn，收敛 worker、撤销 broker token，随后释放 writer ownership。状态/artifact 立即不可访问；物理清理失败继续 fencing 并重试。Volume retention 跟随 session，而不是 idle TTL；旧清理结果不确定时 restore 不启动第二个 writer。

Pause 后 delegated runtime 与 session volume 仍可继续。Idle TTL 可以回收 container，但 Continue 必须先从持久 volume 恢复，再启动关联的新 invocation。Cancel 撤销可恢复性，并遵循 delete/cleanup fencing 规则。

## 8. 安全与权限

Model/provider/MCP credential 不进入 worker env、mount、config、command line 或 artifact。Worker-to-broker token 按 session/audience/generation 限定，短 TTL 且可撤销。Broker/network 故障不允许直连 provider fallback。Create/update/restore 都验证 mount 授权、可写目录重叠与 symlink/path drift。

## 9. 可观测性

安全诊断区分 image variant、选定架构、image digest、Docker availability、control/execution ready、配置 revision、runtime generation 和 cleanup state。Public discovery 不返回 container id/host path；native-only 细节需要独立授权 diagnostics。

## 10. 失败与恢复

| 失败 | 行为 |
|------|------|
| Docker 不可用/context 错误 | 结构化 preflight 失败；settings 仍可用 |
| 平台不支持/digest 不一致 | 拒绝，不静默换镜像或架构 |
| Egress/secret broker 不可用 | execution unavailable，无 raw credential/network fallback |
| Native session 数据缺失 | 安全失败，保留公共 session 与 non-resumable 证据 |
| 配置材料化失败 | 保持 applied revision，暴露失败，不使用 pending revision 执行 |
| TTL/最大存活时间 | 从持久数据重建，generation 递增 |
| SIGTERM/host 重启 | drain 或核对持久 worker 状态，不仅凭进程退出判定完成 |

## 11. 测试计划与验收

- 分别构建 Lite arm64/amd64 和 AIO amd64，验证依赖锁与 per-platform digest；Mac arm64 在 Mac 上通过 Docker CLI 构建运行。
- 各支持组合真实执行 discovery → first turn → pause → interrupted readback → 关联新 turn 的 Continue → Stop/cancel → readback；fake 不替代真实门禁。
- paused worker 在 idle TTL 后回收时，必须用同一 session volume 恢复并续接原生 session；native bytes 缺失时以 non-resumable 失败，不得本地或无上下文 fallback。
- TTL 和配置更新后销毁/重建 worker，验证 native history、冻结内容及 artifact 下载保留。
- 拒绝 worker 直连 internet、host/metadata、broker 任意转发、跨 session token、Docker socket 及 broker credential 读取。
- 验证无 Codex 时 control ready、握手/隔离通过后的 execution ready，以及所有入口的渐进 SSE。
- 验证 single-writer fencing、pending 更新失败/重启、有界退出、磁盘满和清理重试。
- 测量 Lite 压缩/解包大小；真实安全和执行 smoke 通过前不宣告新 runtime 支持。
