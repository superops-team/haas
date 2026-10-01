# Manager GUI 开发指令

[English](AGENTS.md) | **简体中文**

以下规则补充根 `AGENTS.md`，不放宽其门禁。

- 修改 UI 前阅读 [DESIGN.md](../../../DESIGN.md)（[中文版](../../../DESIGN.zh-CN.md)）和受影响组件 spec。
  对话开发遵循[对话规格](../../../specs/manager-conversation-experience/README.zh-CN.md)。
- 复用语义 token 和现有 AI 组件职责。分离 transport 事实、命令意图和本地 UI 状态；视觉组件不自行推断生命周期。
- 共享设计规则变化时，先同步 DESIGN 中英文及相关 spec，再实现。运行时 token 数值仍由 `src/styles.css` 负责。
- 评审受影响状态的双主题、键盘交互和窄屏布局；preview 验证前重建 production 资源。
  浏览器证据与打包原生证据分别报告。
- 遵循 DESIGN 验收映射与根 review 门禁。不得仅凭文档或截图认定合规，不保留永久重复 renderer。
