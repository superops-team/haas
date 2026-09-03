# Startup 功能验证 Case

[English](CASES.md) | **简体中文**

这些 Case 是 `specs/startup/README.zh-CN.md` 的可执行验收补充。默认使用离线 fake Codex app-server 和本地 loopback，不访问真实 provider、云服务或用户 HOME。真实 nginx/AIO/Codex 容器验证使用显式 E2E 开关。

## 执行约定

- 工作目录：仓库根目录。
- 最小回归：`uv run --extra dev pytest -q tests/test_startup.py tests/test_startup_probe.py`。
- 入口集成：`HAAS_E2E=1 make test-integration`。
- 容器专项：`HAAS_E2E=1 HAAS_E2E_OPEN_SANDBOX=1 make test-e2e`，若环境未启用则标记 `not_run`，不得宣称通过。
- 证据：保留 pytest 输出、nginx syntax 输出、startup phase JSONL 和容器 smoke 日志；不提交临时 verification report。

## Case 清单

| ID | 优先级 | 目标 | 前置/命令 | 通过标准 |
|----|--------|------|-----------|----------|
| ST-001 | P0 | nginx 统一代理 ADK/HaaS 入口 | fake sidecar + nginx config fixture；`pytest -q -k ST_001` | ADK 路径和 `/v1/haas/*` 均代理到 `127.0.0.1:8092`；未出现 `/v1/codex-worker/*` 路由；nginx syntax 通过 |
| ST-002 | P0 | health 与 ready 分离 | sidecar listening、Codex socket 不存在；`pytest -q -k ST_002` | `/health` 返回存活结构；`/v1/haas/ready` 为结构化 503/`ready=false` |
| ST-003 | P0 | Unix socket + initialize/initialized readiness | fake Codex server；`pytest -q -k ST_003` | socket 可连接、initialize 成功、initialized notification 成功 flush 后，sidecar 首次发布 `ready=true` |
| ST-004 | P0 | initialize 失败 fail closed | fake server 返回 JSON-RPC error；`pytest -q -k ST_004` | health 仍可用；ready 保持 false；northbound 不泄漏原生错误 payload |
| ST-005 | P0 | initialized 写入未完成 fail closed | fake transport 阻塞 notification flush；`pytest -q -k ST_005` | ready 保持 false；达到 bounded timeout 后产生 safe reason；连接资源释放 |
| ST-006 | P0 | generation 竞态防 stale publish | 并行启动旧/新 generation probe；`pytest -q -k ST_006` | 旧 probe 迟到完成不能覆盖新 generation 状态或写回 `ready=true` |
| ST-007 | P0 | socket 替换触发 ready 回落 | ready 后替换 socket/重启 Codex；`pytest -q -k ST_007` | 旧 generation ready 撤销；新 generation 完成握手后恢复 ready |
| ST-008 | P0 | nginx 不伪造 ready | sidecar 未 ready；`pytest -q -k ST_008` | nginx 对外 ready 与 sidecar 结构化结果一致，不返回固定成功体 |
| ST-009 | P1 | 异步 warmup 不阻塞 ready | 注入慢 MCP/browser/skills/background task；`pytest -q -k ST_009` | Codex 握手完成后 ready；后台任务仍可 pending；普通后台失败不撤销 ready |
| ST-010 | P1 | Codex 安全硬依赖失败撤销 ready | 将 background task 标为 execution-safety dependency；`pytest -q -k ST_010` | 任务失败后 sidecar 发布 ready=false 和 safe reason |
| ST-011 | P0 | bounded timeout 与重试 | socket/connect/initialize 分别超时；`pytest -q -k ST_011` | 无无限等待；health 可响应；ready false；重试次数和耗时可观测 |
| ST-012 | P0 | SIGTERM drain | ready 状态下发送 SIGTERM；`pytest -q -k ST_012` | 先 ready=false，再拒绝新执行、停止 background、settle active turn 并以约定状态退出 |
| ST-013 | P0 | loopback 与 secretless | 检查监听地址、日志、status、事件；`pytest -q -k ST_013` | sidecar/Codex/proxy 不暴露公网；token/raw JSON-RPC/prompt/credential 不出现在输出 |
| ST-014 | P0 | 真实容器总入口 | `HAAS_E2E=1 HAAS_E2E_OPEN_SANDBOX=1 make test-e2e` | nginx 对外提供 health/ready；sidecar 8092、AIO 8080 并存；ready 由真实 Codex handshake 决定 |
| ST-015 | P1 | 启动时延预算 | fake/real startup timing；`pytest -q -k ST_015` | 输出 nginx、sidecar、socket、initialize、service ready 各阶段耗时；不以跳过 handshake 达标 |

## 覆盖矩阵

| Spec 需求 | Cases |
|-----------|-------|
| nginx 唯一入口与 HaaS upstream | ST-001, ST-008, ST-014 |
| health/ready 语义与 sidecar ready owner | ST-002, ST-003, ST-008, ST-012 |
| Unix socket + initialize + initialized | ST-003, ST-004, ST-005, ST-011, ST-014 |
| generation 校验与 stale publish 防护 | ST-006, ST-007 |
| 异步启动与失败隔离 | ST-009, ST-010 |
| bounded timeout/retry/recovery | ST-004, ST-005, ST-007, ST-011 |
| 安全、loopback、secretless | ST-013 |
| 启动观测和时延目标 | ST-011, ST-015 |
| graceful shutdown | ST-012 |

## Case 门禁

- 所有 P0 Case 必须通过，才能进入 Code Review 和 E2E。
- P1 Case 不得无声跳过；环境不具备时记录 `not_run`、原因、替代验证和残余风险。
- 任一 Case 失败必须回到实现阶段，修复后重新执行受影响 Case。
