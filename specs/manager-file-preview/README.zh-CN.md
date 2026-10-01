# Manager 文件预览组件规格

[English](README.md) | **简体中文**

状态：MFP-001 至 MFP-010 已实施；等待 owner 视觉验收
Last reviewed: 2026-09-30
Change ID: manager-codemirror-file-preview
Related specs: [Artifact Store](../artifact-store/README.zh-CN.md)、[Manager GUI Performance](../manager-gui-performance/README.zh-CN.md)、[Manager Project Workbench Experience](../manager-project-workspace-experience/README.zh-CN.md)

## 1. 组件定位

Manager 文件预览组件负责只读展示从 Manager 的“产物”或“文件”区域打开的普通源码和
文本文件。它把既有 `ArtifactContent` 读取结果转换为安全、流畅的 CodeMirror 6
viewport，不改变 artifact 存储/读取 API，也不替代 Markdown、HTML、图片、PDF、CSV、
电子表格、目录与 Office 文档的专用预览器。

## 2. 来源与依据

| 来源 | 采用内容 |
|------|----------|
| 当前 Manager `RightRail` | 单一选中文件 owner、过期读取保护、artifact/file origin scope、专用媒体预览器 |
| 本地 ZCode 文件查看器（2026-09-29 检查） | 扩展名语言映射、按行只读展示、主题感知高亮、独立滚动、延迟工作和 256 KiB 语法高亮上限 |
| CodeMirror 6 | 只读 editor state、viewport 虚拟化、language extension、行号、选择/复制和显式生命周期销毁 |
| HaaS design token | 浅色/深色语义颜色、排版、边框、focus 与 selection 状态 |

ZCode 当前使用 `@pierre/diffs` + Shiki 渲染代码，并未使用 CodeMirror。HaaS 借鉴其
viewer contract 与大文件纪律，并按用户要求明确采用 CodeMirror 6；不复制 ZCode
实现代码、依赖或私有色值。

当前 Manager 的兜底是自动换行的 `<pre class="artifact-code">`，没有行号、语言识别、
语法高亮、editor viewport 虚拟化或明确的大文件降级。本组件直接替换普通 code/text
路径，不增加可选择的第二套 legacy viewer。

## 3. 上游与下游关系

| 方向 | 组件 | 关系 |
|------|------|------|
| 上游 | `RightRail` artifact viewer | 提供选中 path、`kind`、内容与截断状态；持有 loading/error/download 行为 |
| 上游 | Artifact Store / Manager sidecar | 提供有界 `ArtifactContent`；继续作为访问控制与过期请求保护的事实源 |
| 下游 | CodeMirror 6 core | 创建只读文档、viewport、行号 gutter 与选择行为 |
| 下游 | Language loader | 按 basename/扩展名解析安全的本地 parser 并按需加载 |
| 下游 | HaaS design token | 提供浅色/深色 viewer、gutter、syntax、selection 与 focus 颜色 |

## 4. 职责边界

负责：

- 用一个 lazy-loaded 只读 CodeMirror surface 展示 `kind=code` 与 `kind=text`。
- 仅根据展示 path 解析支持的语言，不读取内容进行猜测。
- 只加载当前语言 parser；大文件或截断内容跳过 parser。
- 保留行选择、浏览器复制、键盘滚动、代码横向滚动与普通文本换行。
- 文件切换/组件卸载时销毁旧 EditorView，并忽略过期 language load。
- CodeMirror base chunk 无法加载时，展示可访问、可复制的纯文本 fallback。

不负责：

- 不提供编辑、保存、diff、评论、折叠、minimap、查找替换、命令面板或 LSP。
- 不替换 Markdown、sandbox HTML、图片、PDF、CSV、电子表格、目录或 Office 预览器。
- 不做基于内容的语言猜测，也不执行文件内容。
- 不改变 artifact API、ADK、HaaS native protocol、session、event、credential、container 或持久化。

## 5. 核心接口与功能需求

### 5.1 React 边界

```ts
interface CodeFilePreviewProps {
  path: string;
  kind: "code" | "text" | string;
  content: string;
  truncated?: boolean;
}
```

`RightRail` 仅能在普通 code/text 分支通过 `React.lazy` 导入本组件。session shell、rail
列表和专用预览器不得同步导入 CodeMirror。

### 5.2 P0 需求

- **MFP-001 — 单一只读查看器：** 每个成功的普通 `code`/`text` 结果都使用
  `CodeFilePreview`。CodeMirror 同时设置 `EditorState.readOnly` 与
  `EditorView.editable.of(false)`。保留文本选择、复制、键盘/触控板滚动，不允许修改文档。
- **MFP-002 — 确定性语言映射：** path 匹配忽略大小写、识别 basename、移除 query/
  fragment 且不依赖内容。P0 支持 JavaScript/JSX、TypeScript/TSX、JSON、HTML、CSS、
  Python、POSIX shell/Bash/Zsh、SQL 与 YAML。未知扩展名和无扩展名文件使用纯文本；
  Markdown 继续使用现有渲染器。
- **MFP-003 — 按需 parser：** 仅在打开 code/text 预览后加载 CodeMirror base；每个语言
  family 必须使用可静态分析的 dynamic import。打开 Python 不得执行 JavaScript、SQL
  或 YAML loader；重复打开可复用浏览器 module cache。
- **MFP-004 — 大文件降级：** 语法解析上限为 `256 * 1024` UTF-8 bytes，与已验证的
  ZCode viewer 策略一致。达到或超过上限，或 `ArtifactContent.truncated === true` 时，
  文件仍在带行号、可选择的 CodeMirror viewport 中打开，但不加载 language parser。
  本地化提示区分“文件过大、已关闭高亮”和“展示内容已截断”。普通降级不得走第二套 legacy
  `<pre>` 路径。
- **MFP-005 — 展示：** 行号可见且不可选择；代码保留空白并使用独立横纵滚动、不自动换行；
  有界普通文本自动换行。大文件或截断文本关闭换行，改用 viewport 内横向滚动，避免超长单行
  阻塞布局。viewport 填满 artifact preview body，使用仓库 mono 字体与既定 13 px 字号，
  不产生 body/page 滚动。
- **MFP-006 — 主题与可访问性：** viewer surface 与 highlight class 全部来自 HaaS semantic
  CSS variables，并随 `html[data-theme]` 更新。focus 可见但不持续显示高对比外框；浅/深主题
  下 selection 均可读；viewport 可通过键盘到达；accessible label 包含文件名和只读状态。
- **MFP-007 — 生命周期与失败：** 挂载文件始终只有一个 EditorView。path/content/kind/
  truncation 变化替换 state，过期 parser promise 不得覆盖新文件；unmount 必须 destroy view。
  base chunk 或 parser 失败不得丢失内容：base 失败展示有界可复制 fallback；parser 失败保留
  CodeMirror 纯文本模式。
- **MFP-010 — 查看器 action 不被遮挡：** 文件 header 持有 action popover 的视觉层叠上下文。
  打开 `...` menu 后，在 320/390/760/1440 CSS px 与 200% zoom 下，全部可见 menu item 必须
  位于 preview renderer 之上、visual viewport 与 artifact rail 之内。CodeMirror、电子表格、
  PDF、图片、HTML 以及 loading/error surface 均不得覆盖或裁剪 menu。Preview 继续独立滚动，
  打开 menu 不改变其 scroll position。

P0 resolver 表为规范性合同：

| Language id | 忽略大小写的 path match |
|-------------|--------------------------|
| `javascript` | `.js`、`.mjs`、`.cjs` |
| `jsx` | `.jsx` |
| `typescript` | `.ts`、`.mts`、`.cts` |
| `tsx` | `.tsx` |
| `json` | `.json` |
| `jsonc` | `.jsonc` |
| `html` | `.html`、`.htm`、`.xhtml` |
| `css` | `.css` |
| `python` | `.py`、`.pyw` |
| `shell` | `.sh`、`.bash`、`.zsh`、basename `.bashrc`、`.zshrc` |
| `sql` | `.sql` |
| `yaml` | `.yaml`、`.yml` |
| `plaintext` | 其他所有 path，包括空 path 或未命中 basename |

resolver 只为语言选择移除末尾 query/fragment，不改变传给 artifact API 的 path。已识别语言
即使被 legacy producer 标记为 `kind=text`，仍按 code 处理且不换行；只有解析为 plaintext
的 `kind=text` 才自动换行。

### 5.3 P1 需求

- **MFP-008 — 更多语言：** Go、Rust、Java、C/C++、TOML 与常见配置 basename 只能通过
  同一 resolver/loader registry、focused test 与 async chunk budget 增量加入。
- **MFP-009 — 可选控件：** wrap toggle、find、jump-to-line 可后续加入且无需改变 artifact
  API；本轮不实现。

## 6. 数据模型

UI 在挂载 viewer 前派生不含内容的 descriptor：

```ts
type FilePreviewMode = "highlighted" | "plain-large" | "plain-truncated" | "plain-unknown";

interface FilePreviewDescriptor {
  languageId: "javascript" | "jsx" | "typescript" | "tsx" | "json" | "jsonc" | "html" | "css" |
    "python" | "shell" | "sql" | "yaml" | "plaintext";
  mode: FilePreviewMode;
  wrapLines: boolean;
  byteLength: number;
}
```

descriptor 与任何诊断证据不得包含文件内容、host 绝对路径、prompt、完整工具参数、credential、
URL 或已选择文本。

## 7. 运行模型与状态机

```text
selected code/text ArtifactContent
  -> lazy-load CodeFilePreview + CodeMirror core
  -> 根据 path/kind/UTF-8 length/truncated 派生 descriptor
      -> large 或 truncated：挂载只读 plain CodeMirror
      -> known 且有界：立即挂载 plain CodeMirror
                        -> 加载当前 parser -> 原地 reconfigure 当前 view
      -> unknown：挂载只读 plain CodeMirror
  -> path/content 改变：废弃 loader generation -> 替换 EditorView
  -> unmount：废弃 loader generation -> destroy EditorView

base chunk failure -> 可复制只读 fallback
parser failure     -> 同一 CodeMirror viewport 的纯文本模式
```

CodeMirror base chunk 加载期间，loading placeholder 占据最终 viewport 尺寸。core 可用后，
有界已知语言文件在 parser resolve 前即可用 plain mode 阅读；parser 到达后原地 reconfigure
当前 view，不替换 document、selection 或 scroll position。Parser 解析不得阻塞 artifact
header 或 actions。

## 8. 安全与权限

- CodeMirror 将内容视为 document text；不得通过 `innerHTML` 注入或作为 script/style/markup 执行。
- 语言选择为本地 allowlist；扩展名不能选择任意 import URL 或 package。
- 不请求读取 clipboard；现有 copy action 仍只由用户显式触发。
- 内容、selection、host 绝对路径、prompt、完整工具参数、credential 不得进入 log、metric、
  analytics、test snapshot、chunk name 或 error message。
- Artifact object scope、download authorization、sandbox HTML、`nosniff`、path traversal 防护及
  remote/local action 约束继续由现有组件持有。

## 9. 可观测性与性能

- Production manifest 证据必须证明 CodeMirror core/language package 不在 initial synchronous
  entry graph，仅可通过 dynamic import 到达。
- 本地 production-preview fixture 中，冷启动有界代码预览应在 content ready 后 200 ms 内
  展示 viewport；warm repeat 应在 100 ms 内展示。
- 10,000 行有界 fixture 与跳过 parser 的 512 KiB fixture 在打开/初始滚动时不得产生超过
  100 ms 的 long task，并保持 session shell 可响应。
- 测试与性能输出只记录 language id、byte bucket、mode、timing、chunk/request count 与
  long-task count，不打印内容或 host path。
- 本组件不引入 polling、network request、persistent cache、event、metric 或 log sink。

## 10. 失败、恢复、兼容与回滚

| 场景 | 必须行为 |
|------|----------|
| 不支持扩展名或无扩展名 | 立即使用 plain-text CodeMirror 打开 |
| 内容达到 256 KiB | 跳过 parser 与换行、展示 size notice、保留行号/复制/viewport 滚动 |
| server 标记内容 truncated | 跳过 parser 与换行、展示 truncation notice、展示全部返回文本 |
| 选中 parser import 失败 | 保留内容并切为 plain-text CodeMirror，不显示空白 pane |
| CodeMirror base import/render 失败 | 在 viewport 有界、独立滚动、可复制的只读 fallback 中展示全部返回字符，并显示安全本地化错误 |
| parser 加载时切换文件 | 忽略过期结果，仅显示当前文件 |
| 打开时切换主题 | 原地更新颜色，不重置 document 或 scroll position |
| 组件卸载 | destroy EditorView，不残留 observer/listener |
| header action menu 在任意 preview renderer 上打开 | menu 完整可见且 pointer 可达；preview 几何与 scroll 不变 |

兼容性为 additive 且仅影响 GUI。ADK 与 `/v1/haas/*` API、artifact schema、URL 行为、event
顺序、session、storage、security header 与 container variant 均不变。回滚只需在一个 commit
移除 lazy viewer 与依赖，不需要数据迁移或 server rollback。落地时删除旧 `.artifact-code`
普通渲染路径。

## 11. 测试计划与验收

### 11.1 功能验证用例

| ID | 优先级 | 验证 | 预期证据 |
|----|--------|------|----------|
| FV-MFP-01 | P0 | 用全部 P0 扩展名、大小写、basename、query/fragment 与 unknown 测 resolver | language id 确定；Markdown 仍在本 viewer 外 |
| FV-MFP-02 | P0 | 渲染有界 TypeScript 文件 | 只读 CodeMirror、行号、syntax token、代码不换行、可复制 selection |
| FV-MFP-03 | P0 | 渲染未知普通文本 | plain CodeMirror、文本换行、不执行 language loader |
| FV-MFP-04 | P0 | 渲染 256 KiB、单行 512 KiB 与 `truncated=true` fixture | 不执行 parser loader 或换行；正确本地化提示；返回文本可见可复制且只在 viewport 内滚动 |
| FV-MFP-05 | P0 | 延迟一个 loader，切换另一文件后再 resolve；unmount/reopen | 过期 parser 不替换当前 document；旧 EditorView 被销毁 |
| FV-MFP-06 | P0 | 注入 parser/base failure | parser 失败保留 plain CodeMirror；base 失败保留有界可复制 fallback |
| FV-MFP-07 | P0 | 切换 light/dark/auto，并在 200% zoom 下测试 keyboard focus | semantic color 更新、focus 可见、行号/内容对齐、action 不裁切 |
| FV-MFP-08 | P0 | build Vite manifest 并遍历 entry graph | synchronous graph 无 CodeMirror core/language；语言 family 保持 async |
| FV-MFP-09 | P0 | production-preview Playwright 打开 code/text、快速切换、滚动 10,000 行、打开 512 KiB | 当前文件正确、独立滚动、无 stale content、满足第 9 节预算 |
| FV-MFP-10 | P0 | 打包并 smoke Tauri app，打开本地 TypeScript 与大文本 | browser/Tauri 一致；字体、滚动、主题、复制可用 |
| FV-MFP-11 | P0 | 在 390 px 与 200% zoom 下，在 code 与 spreadsheet preview 上打开文件 `...` menu | menu bounds 位于 rail/viewport 内；每个 menu item 中心 hit 到该 item 而非 preview；menu 不被裁剪或覆盖 |

### 11.2 TDD 与实现顺序

1. 为 FV-MFP-01、-03、-04 添加失败的 resolver/descriptor 测试。
2. 为 FV-MFP-02、-05、-06、-07 添加失败的组件生命周期/只读测试。
3. 添加精确 CodeMirror core/language 依赖并实现 resolver、language loader、theme、component
   lifecycle 与 loading/failure boundary。
4. 替换普通 `<pre>` 分支、删除 obsolete CSS、添加本地化提示。
5. 为 FV-MFP-08、-09 添加 manifest 与 production-preview case，并在修改 header stacking 前为
   FV-MFP-11 添加失败的 geometry/hit-test case。
6. 执行 focused/full GUI gates、强制 reviews、root gates、DMG build 与 packaged smoke。

### 11.3 组件影响分析

| 组件 | 影响 | 必须动作 |
|------|------|----------|
| Artifact Store | 无 protocol/storage 变化；消费既有 `path`、`kind`、`content`、`truncated` | 保持 object-scope 与 bounded-read 测试不变 |
| Manager GUI Performance | 新增可选重依赖与 viewport work | 强制 lazy graph、parser split 与 runtime budget |
| Manager Project Workbench | Files 与 Artifacts 共用 viewer | 验证两个 origin 与快速切换 |
| Security Boundary | source text 进入新 renderer | 断言不执行、不远程 import、不记录内容、不走 unsafe HTML |
| ADK/HaaS protocol、session、event、model/MCP、container runtime | 无影响 | 现有 compatibility/full-check 证据足够 |

所有 P0 需求均映射到可执行用例。P1 项目明确延期，不影响移除普通文本 legacy path。
