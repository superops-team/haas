# 品牌与仓库指标规格

[English](BRAND-AND-REPOSITORY-METRICS.md) | **简体中文**

Status: Draft
Last reviewed: 2026-09-04
Change ID: `readme-brand-and-repository-metrics`

## 1. 背景

仓库首页已经能够准确解释 HaaS，但缺少易识别的产品标志和紧凑、可信的项目健康
摘要。本变更新增已确认的 A1「Protocol Bridge」品牌标识与仓库自维护指标徽章，
不改变 runtime 或协议行为。

## 2. 目标与非目标

目标：

- 提供可用于 README、方形图标和小尺寸单色场景的简洁 HaaS 标志。
- 两份 README 顶部居中，继续以英文为默认语言。
- 基于可复现的仓库数据展示 build、commit 数、源码行数和测试覆盖率。
- 英文与中文页面保持结构等价。

非目标：

- 不改变 HaaS API、event、runtime、storage、adapter、policy 或 container。
- 不声明下载量、star 数、生产可用性或 benchmark 结果。
- 不引入托管分析服务、第三方数值 badge 服务或 README JavaScript。
- 生成的 HTML/GIF、spec、lock file 与 vendored dependency 不计入源码行数。

## 3. A1 品牌系统

Logo 是名为 **Protocol Bridge** 的几何 `H`。横向桥梁表示稳定的 Google ADK 2.0
REST + SSE 协议面；两条竖向轨道表示 HaaS control plane 与 harness runtime 之间
的隔离。青、绿、紫配色复用现有 Archify 语义色板。图形在单色与 32 px 下仍必须
可识别。

提交以下资产：

| 资产 | 用途 |
|---|---|
| `docs/brand/haas-logo.svg` | README 横向 Logo 与 wordmark |
| `docs/brand/haas-mark.svg` | 方形图标 / social avatar 源文件 |
| `docs/brand/haas-mark-monochrome.svg` | 单色与打印 fallback |

SVG 必须声明固有尺寸与 `viewBox`，不得包含 script、外部资源、内嵌位图、远程
字体、tracking 或本机元数据。横向 wordmark 使用确定性几何图形，不依赖系统字体。

## 4. README 顶部

两份 README 均以 GitHub 支持的居中 HTML masthead 开始：

1. 共用的横向 Logo。
2. 对应语言的一行产品承诺。
3. 使用仓库内 SVG 渲染的 Commits、Lines 与 Coverage 徽章。
4. English / 简体中文切换入口。

每张图片都必须有明确 alt。动态 badge 自动化不可用时，README、Logo、导航与架构内容
仍保持可用。

## 5. 指标定义

| 指标 | 数据源 | 定义 |
|---|---|---|
| Commits | Git | 在 `main` 完整历史执行 `git rev-list --count HEAD` |
| Lines | Git + generator | `haas/`、`tests/`、`scripts/` 下受 Git 跟踪源码文件的非空物理行数 |
| Coverage | coverage.py | Coverage suite 成功后 `coverage json` 中的 `totals.percent_covered` |

行数统计使用显式 allowlist，只包含上述目录当前使用的 Python、shell 与 JavaScript
源码扩展名；排除未跟踪文件、缓存、生成媒体和文档。Coverage 是验证证据而非
估算，badge 将 JSON 数值四舍五入为一位小数。

## 6. 发布模型

`.github/workflows/repository-metrics.yml` 在每次 `main` push、每日定时和
`workflow_dispatch` 时运行。即使源码行数不变，commit 数也会变化，因此不设置
path filter。Job 完整 checkout 历史，通过 `uv` 安装锁定的开发
环境，运行 coverage，调用仓库指标生成器，并只把生成的 SVG 发布到独立
`metrics` 分支。

Workflow 只授予 `contents: write` 权限并设置 concurrency。它不得 force-push
`main`、重写用户历史、带写权限执行 fork 代码或发布部分结果。`metrics` 是输出
通道而不是源码分支。

README 徽章使用 `docs/brand/badges/` 下的仓库内 SVG，保证分支、fork、私有仓库
视图和受限网络渲染器中 masthead 仍能稳定展示。workflow 发布的 `metrics` 分支
仍作为自动化输出，用于按需刷新这些数值。

只有 commits、lines 与 coverage 三个数值 SVG 发布到 `metrics`。发布过程在新的临时 Git worktree 中复制上一版输出，再一次性
替换三个 SVG、创建普通 commit，并以非 force 方式 push。Push race 安全失败，
由下一次串行或定时任务恢复。

## 7. 生成器与失败合同

`scripts/quality/` 下的脚本收集 commit 与行数、读取 coverage JSON、校验全部数值，
并把确定且可访问的 SVG 写入显式输出目录。

- 拒绝缺失、负数、非有限值和超出范围的 coverage。
- 转义所有 SVG 文本，不插入不可信 markup。
- 使用固定尺寸、配色、label 与 `<title>`。
- 全部 badge 在临时目录成功生成后才发布。
- 相同输入生成字节一致的输出；SVG 不包含时间戳。
- 源码行收集使用 `git ls-files -z -- haas tests scripts` 和 `.py`、`.sh`、
  `.js`、`.mjs`、`.cjs` allowlist，只统计至少包含一个非空白字符的行。
- Shallow checkout、coverage 失败、JSON 缺失或指标非法均为硬失败，旧 badge 不变。
- 日志只输出聚合值，不包含 prompt、payload、credential、Authorization、本机路径
  或完整 tool 参数。
- 发布只使用 workflow token 与 GitHub Actions identity，不引入个人 token。

## 8. 组件影响

Architecture/documentation 新增品牌和首页合同；仓库自动化新增一个 metrics workflow
与确定性生成器。Protocol、session、event、adapter、proxy、MCP、skill、policy、
artifact、observability、OpenSandbox 和 `linux/amd64` 合同均不受影响。这是纯新增
文档展示面，不修改 API 或持久化 schema。

## 9. 测试计划与验收

1. 校验 SVG XML、尺寸、`viewBox`、禁止元素、外部引用及原始/32 px 可读性。
2. 使用 tracked source 和 ignored/generated fixture 测试行数统计。
3. 测试数值校验、确定性输出、转义与失败原子性。
4. 确认 coverage 与 `coverage.json` 一致，commit 数与完整 Git 历史一致。
5. 校验 workflow YAML、最小权限与 concurrency。
6. 检查两份 README masthead、图片/链接、语言切换和 badge 失败降级。
7. 执行两轮 code review、架构 review、测试质量 review、`git diff --check` 和
   `make pre-commit`。

验收要求：A1 彩色/单色资产有效；双语 masthead 居中且结构等价；三个 badge 均
链接到证据；指标定义准确；发布保持原子性；无阻塞 review finding。由于不改变
runtime 行为，runtime、Docker、OpenSandbox 与 provider E2E 记为 `not_run`。

## 10. 任务拆解

1. 创建并验证三份 A1 SVG。
2. 实现并测试指标收集/渲染器。
3. 添加最小权限发布 workflow。
4. 更新两份 README masthead。
5. 通过真实 workflow 生成并检查 badge。
6. 执行 review 与仓库门禁。

## 11. 回滚

Revert README masthead 与 workflow commit 即可停止消费和发布指标。之后可独立删除
`metrics` 分支，不影响 `main` 或任何 runtime 产物。变更期间 README masthead
以下的原有内容始终保留。
