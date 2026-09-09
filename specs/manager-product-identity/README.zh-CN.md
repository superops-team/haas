# Manager Product Identity 组件规格

[English](README.md) | **简体中文**

状态：Draft
最近评审：2026-09-09
Change ID：manager-openharness-no-login
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

## 4. 验收

- 桌面窗口、tray title、package metadata、启动文案、侧栏 wordmark 和设置文案使用
  OpenHarness。
- GUI 测试断言 sidebar 不再显示 cloud sign-in 控件。
- API 测试断言 `/v1/cloud/login`、`/auth/callback` 和 cloud logout 是 disabled/no-login
  流程。
- 现有 HaaS delegation settings 流程在品牌变更后仍可用。
