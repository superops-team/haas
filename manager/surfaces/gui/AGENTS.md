# Manager GUI Instructions

**English** | [简体中文](AGENTS.zh-CN.md)

These rules supplement the repository root `AGENTS.md`; they do not relax its gates.

- Before editing UI, read [DESIGN.md](../../../DESIGN.md) and the affected component spec.
  Conversation work follows [the conversation spec](../../../specs/manager-conversation-experience/README.md).
- Reuse semantic tokens and existing AI component responsibilities. Keep transport facts,
  command intent, and local UI state separate. Do not infer lifecycle inside visual components.
- Changes to shared design rules update DESIGN's English/Chinese variants and relevant specs
  before implementation. Numeric runtime tokens remain in `src/styles.css`.
- Review affected states in both themes, keyboard interaction, and narrow layouts. Rebuild
  production assets before preview validation. Report browser and packaged-native evidence separately.
- Follow DESIGN's acceptance mapping and root review gates. Do not treat documentation or
  snapshots alone as proof of compliance; do not retain permanent duplicate renderers.
