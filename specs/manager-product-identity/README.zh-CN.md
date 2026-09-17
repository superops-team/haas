# Manager Product Identity 组件规格

[English](README.md) | **简体中文**

状态：Draft
最近评审：2026-09-15
Change ID：manager-openharness-no-login、manager-macos-dock-reopen、manager-macos-one-command-install
相关规格：[Manager Delegation](../manager-delegation/README.zh-CN.md)、[Security Boundary](../security-boundary/README.zh-CN.md)

## 1. 组件角色

Manager Product Identity 定义 HaaS 配套 manager 应用的对外产品身份。产品名为
**OpenHarness**。它是本地优先的桌面 harness 管理器，不需要云账号或登录即可使用。

## 2. 范围

本次范围：

- 将桌面壳、GUI chrome、启动文案、设置文案和本仓本地文档中的可见产品名从
  OpenWorker 替换为 OpenHarness。
- 提供 OpenHarness 专用 logo 和桌面/GUI 图标资源。
- 从用户界面移除 cloud account sign-in、sign-out、账号行、cloud telemetry、
  cloud gallery 和 managed one-click connector 登录入口。
- 禁用 cloud-auth HTTP endpoint，使本地 sidecar 不再发起或完成外部登录流程。
- 保留本地手动 provider key、本地 connector credential、本地 MCP OAuth、
  HaaS delegation settings 和本地目录授权能力；这些能力无需登录。
- 新安装默认使用 HaaS `local_managed` 执行后端并开启 sidecar autostart。该
  local-first 默认值不得引入 cloud login，HaaS 不可用时也不得静默回退到直接
  local execution。
- 为 GitHub 公开 release 提供仓库维护的一键 macOS 安装器。安装器下载 release
  DMG 及其 SHA-256 校验文件，将 OpenHarness 安装到 `/Applications`，并从最终 App
  上移除 quarantine 属性，使 unsigned 开发发行包可以启动。

非本次范围：

- 同时重命名内部 Python package、数据库/状态目录和 console script。现有
  `coworker` 包名与 `openworker-server` 入口可作为兼容实现细节保留，后续单独迁移。
- 清理所有历史测试 fixture、mockup 或非用户可见注释中的旧名称。

## 3. 产品规则

- 用户可见界面必须显示 `OpenHarness`，不得显示 `OpenWorker`。
- 应用不得渲染登录按钮、已登录账号行、cloud account badge 或 cloud telemetry
  控件。
- 过去依赖 OpenWorker Cloud 的功能必须隐藏、以无登录不可用状态呈现，或改为只展示
  manual/local credential setup。
- 发起或完成 cloud login 的 server route 必须返回稳定 disabled response，不得打开
  浏览器，也不得联系 Auth0/OpenWorker Cloud。
- 凭证继续保存在本地 manager SecretStore；产品身份变更不得把凭证迁移到云服务。

## 4. macOS 一键安装合同

- 对外复制即用命令必须通过 HTTPS 获取本仓库带版本的 `scripts/install.sh`。可用
  `VERSION=vX.Y.Z` 指定 release；未指定时解析 GitHub 最新的非 draft、非 prerelease
  正式 release。
- 安装器仅支持 macOS，且对 release 没有对应资产的架构必须明确拒绝。v0.2.1 仅
  支持 Apple Silicon（`arm64`/`aarch64`），在 Intel macOS 上必须清晰报错。
- DMG 与 `<dmg>.sha256` 必须来自同一个不可变 GitHub release。下载或 SHA-256 校验
  失败时，必须在 mount 或替换 App 之前终止；重定向只允许发生在 GitHub HTTPS
  release 下载链路内。安装器只能从校验文件读取并验证一个 64 位十六进制摘要，
  不得信任校验文件携带的文件名或路径。
- 安装器必须把已校验 DMG 挂载到私有临时目录，并要求其根目录恰好存在一个
  `OpenHarness.app`。成功、失败或中断时都必须清理临时文件与 mount。
- 目标固定为 `/Applications/OpenHarness.app`。覆盖前必须把已有安装移动到同文件系统
  backup；之后任一步失败都必须恢复旧版本。只有新 App 复制完成且 post-install 检查
  通过后才能删除 backup。
- caller 无法写 `/Applications` 时，可明确通过 `sudo` 请求提权；不得静默改装到其他目录。
  备份、替换、quarantine 清理和回滚必须使用同一提权路径，使部分完成的提权安装仍可恢复。
- 复制完成后必须执行
  `/usr/bin/xattr -dr com.apple.quarantine /Applications/OpenHarness.app`。这是 unsigned
  build 的显式分发例外，只能作用于已安装的 OpenHarness bundle，不得修改更宽目录。
- 安装器必须打印解析出的 release、下载/校验、mount、备份/替换、quarantine 清理和
  最终 App 路径。错误必须说明失败阶段并以非零状态退出。安装器不得自动启动 App。

## 5. 验收

- 桌面窗口、tray title、package metadata、启动文案、侧栏 wordmark 和设置文案使用
  OpenHarness。
- 桌面首次启动必须先隐藏构建主窗口，只有 WebView 报告首次页面加载完成后，才仅执行一次
  显式显示和聚焦；后续 reload 或导航不得把用户已关闭到托盘的窗口重新弹出。该顺序既避免
  可见的首帧白屏，又保证窗口最终可见且可交互。只有用户关闭
  这个可见窗口后才允许 close-to-tray 隐藏；sidecar 进程健康但窗口数为零，或窗口可见
  但 WebView 首帧未提交而保持纯白，均不满足启动验收。
- 在 macOS 上，OpenHarness 进程已运行但没有可见窗口时，用户点击 Dock 图标必须恢复、
  取消最小化、显示并聚焦已有主窗口。该原生 reopen 动作必须复用已有 WebView 与 sidecar，
  不得创建第二个窗口、进程、session 或 backend；已有可见窗口时不得抢占焦点。Tray Open
  与 single-instance 再次启动必须保持相同的恢复语义。
- 桌面开发服务器必须忽略 `src-tauri/target/**`；Rust 构建和 sidecar 打包写入其中的
  文件不是前端源码，不得触发 WebView reload。
- macOS 原生生命周期测试必须覆盖：主窗口关闭到托盘后，从 Dock 激活 OpenHarness，并断言
  同一进程恢复为一个可见且位于前台的窗口。
- GUI 测试断言 sidebar 不再显示 cloud sign-in 控件。
- API 测试断言 `/v1/cloud/login`、`/auth/callback` 和 cloud logout 是 disabled/no-login
  流程。
- 现有 HaaS delegation settings 流程在品牌变更后仍可用。
- 全新 no-login 安装会启动 managed local HaaS sidecar；符合条件的新 chat 无需
  trigger keyword 即通过 HaaS 执行，同时保留可见的 session 级显式 local opt-out。
  Release smoke 必须使用打包后的 app/server 二进制和隔离 state dir，并预置包含前向兼容
  HaaS SQLite 记录的 fixture；随后提交一条 WebSocket task，断言失败会作为结构化
  transcript error 投递，而不是后台 task exception 或静默不执行。
- 打包 release smoke 还必须执行端口占用变体：配置的本地 HaaS 端口已被非 HaaS
  loopback listener 占用时，主 sidecar 仍保持健康，任务路径必须返回结构化
  `local_sidecar_port_occupied` error 加 `turn_done`，settings 诊断必须包含
  `local_status.reason`、`local_status.managerLogPath` 与 `local_status.logPath`，
  且不得暴露 token material。
- 离线合同测试覆盖 platform/architecture 拒绝、不可变 release 资产选择、先校验后
  mount、限定范围的 `xattr`、清理和回滚。release smoke 必须通过公开命令安装已上传
  DMG，并确认 `/Applications/OpenHarness.app` 存在且不带 quarantine 属性。
