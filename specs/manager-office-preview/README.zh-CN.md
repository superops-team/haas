# Manager Office 预览组件规格

[English](README.md) | **简体中文**

Status: Approved
Last reviewed: 2026-09-30
Change ID: manager-safe-office-preview
Related specs: [Artifact Store](../artifact-store/README.zh-CN.md)、[Manager File Preview](../manager-file-preview/README.zh-CN.md)、[Manager GUI Performance](../manager-gui-performance/README.zh-CN.md)、[Security Boundary](../security-boundary/README.zh-CN.md)

## 1. 组件定位

Manager Office 预览负责安全、只读地展示从 Manager“产物”或“文件”区域打开的 `.xlsx`、
`.xlsm` 与 `.xls`。它用 lazy WebAssembly/Worker viewer 替换存在漏洞的 SheetJS 浏览器解析器，
同时保留本地/默认应用操作与既有 artifact 读取边界。

## 2. 来源与依据

| 来源 | 采用内容 |
|------|----------|
| 当前 HaaS Manager | `xlsx@0.18.5`、lazy `SheetViewer`、25 MiB binary preview 上限、local/remote action 控制 |
| npm audit（2026-09-29） | `xlsx@0.18.5` 存在 high 级 prototype-pollution 与 ReDoS advisory；npm 没有修复后的 SheetJS 版本 |
| 本地 ZCode Office preview | lazy Office dispatch、本地 WASM source、Worker-backed `@extend-ai/react-xlsx`、只读模式、主题映射、自定义可访问 sheet tabs、25 MiB 上限和错误边界 |
| `@extend-ai/react-xlsx@0.16.6` 评估 | MIT、兼容 React 18、隔离安装 production audit 为 0、tarball 5.1 MiB / 解包 19.4 MiB、WASM 4.2 MiB |

HaaS 借鉴 ZCode 架构，但不原样复制。第三方 viewer 会为 workbook hyperlink 调用
`window.open()`，并可渲染 workbook image。因此 HaaS 增加更严格的文档信任边界：不使用 URL
source、不渲染 workbook image、不允许 workbook 自己触发外部导航。

## 3. 上游与下游关系

| 方向 | 组件 | 关系 |
|------|------|------|
| 上游 | `RightRail` | 提供选中 artifact path 与 binary `data_url`；保留 header、copy/reveal/download 和 stale-read ownership |
| 上游 | Manager sidecar artifact read | 执行 scope 与 25 MiB binary limit；将 `.xlsx`、`.xlsm`、`.xls` 分类为 `sheet` |
| 下游 | `@extend-ai/react-xlsx` | 只读渲染 workbook，并暴露 sheet navigation controller state |
| 下游 | bundled Duke WASM + worker | 本地解析 workbook bytes，不使用 CDN 或外部 parser service |
| 下游 | HaaS theme token | 提供 light/dark viewer 状态与周边 surface |

## 4. 职责边界

负责：

- 不使用有漏洞的 `xlsx` npm package 预览支持的 spreadsheet。
- 完整 spreadsheet renderer、worker 与 WASM 不进入 initial synchronous graph。
- 在 parser 支持时展示格式、merged range、frozen pane、chart 与 sheet navigation，同时禁用
  编辑和 export 控件。
- Workbook bytes 只留在本地 WebView/worker，并阻止 workbook-originated navigation 或
  subresource loading。
- 对 input size、loading、error、theme、stale completion 与 unmount 行为设限。

不负责：

- 不提供 spreadsheet 编辑、formula mutation、save/export、macro execution、external data
  refresh、external image loading 或直接 workbook hyperlink navigation。
- 本轮不实现 DOC/DOCX/PPT/PPTX；继续使用默认应用打开。
- 不修改 ADK、`/v1/haas/*`、artifact identity、session/event ordering 或 container runtime。

## 5. 核心接口与功能需求

### 5.1 React 边界

```ts
interface SpreadsheetPreviewProps {
  dataUrl: string;
  filename: string;
}
```

`RightRail` 只能引用轻量 `SpreadsheetPreviewBoundary`。真实 viewer 和所有
`@extend-ai/react-xlsx` import 必须位于 `React.lazy` 之后。

### 5.2 P0 需求

- **MOP-001 — 移除有漏洞 parser：** 移除 `xlsx` package 的 direct/transitive production
  use。`npm ls xlsx` 必须为空，`npm audit --omit=dev` 不得包含 `xlsx` advisory。不得使用
  `npm audit fix --force`、非官方 SheetJS tarball 或 audit ignore。
- **MOP-002 — 本地 Worker/WASM 解析：** 精确 pin `@extend-ai/react-xlsx@0.16.6`；通过 Vite
  `?url` 导入 package 内 WASM，在 lazy module 中调用 `setWasmSource`，通过 `file` 传入
  `ArrayBuffer`，保持 `useWorker=true`；由于 package worker 自身包含 module import，Vite 必须
  设置 `worker.format="es"`，并必须从 dependency optimization 排除 `@extend-ai/react-xlsx`，
  同时显式包含其 CommonJS `regl` leaf 以提供 default-export interop；否则相对 worker URL
  `new URL("./xlsx-worker.js", import.meta.url)` 会解析到不存在该 asset 的 `.vite/deps`，或未打包
  viewer 无法导入 `regl`。不得使用 viewer `src` prop 或 CDN。
- **MOP-003 — 只读产品模式：** 设置 `readOnly=true`、`showDefaultToolbar=false`，不暴露
  export、mutation、formula editing、resize、form-control mutation 或 clipboard-write tool。
  不执行 workbook macro；唯一自定义控件为可访问 sheet tabs。
- **MOP-004 — Sheet navigation：** 多个可见 sheet 时展示 compact tablist，支持 active
  state、截断/title、click 与 Left/Right/Home/End 键盘移动；单 sheet 不占空 toolbar 行。
- **MOP-005 — 不可信文档边界：** 设置 `showImages=false`，workbook content 不得创建 remote
  image request。在第三方 cell/image/shape handler 调用 `window.open` 前，通过 capture phase
  取消 viewer-owned click navigation；仅放行 HaaS 自有 sheet-tab control。内部 sheet navigation
  使用 DOM grid（`experimentalCanvas=false`），确保该边界与 cell accessibility 可检查；内部
  sheet navigation 由 HaaS tabs 提供，不依赖 workbook hyperlink。
- **MOP-006 — 大小与失败：** 保持 Manager sidecar 25 MiB binary ceiling，并向 viewer 传入
  `maxFileSizeBytes=25 * 1024 * 1024`。在 `atob` 或 buffer allocation 前根据 base64 payload
  长度估算 decoded bytes，超过上限时 fail closed。invalid、encrypted、unsupported、oversized、worker、WASM
  或 render 失败展示本地化有界错误，保留既有 open/download/reveal actions；错误不得包含
  workbook 内容或 host path。
- **MOP-007 — 主题与布局：** 将 `html[data-theme]` 映射到 viewer `isDark`，通过 scoped
  observer 原地更新。viewer 只有一个有界滚动 surface，填满 artifact body，并在
  390/760/1440 px 与 200% zoom 下可用。
- **MOP-008 — 生命周期：** base64 conversion、WASM load、parse 与 render 必须 cancellable
  或 stale-safe。切换 artifact/session 或 unmount 后，旧 workbook 不得覆盖当前文件；HaaS
  持有的 observer、worker、listener 必须清理。
- **MOP-009 — 格式一致性：** local classification/listing 与 remote media-type mapping 将
  `.xlsx`、`.xlsm`、`.xls` 识别为 `sheet`。不支持的 legacy workbook 内容降级到本地化错误/
  open action，不得让 rail 崩溃。

## 6. 数据模型

不改变 public schema。沿用现有 `ArtifactContent` UI 边界：

```ts
interface ArtifactContent {
  path: string;
  kind: "sheet" | string;
  data_url?: string;
  preview_status?: "available" | "download_only" | "unavailable";
  download_status?: "available" | "unavailable";
}
```

lazy component 仅派生本地 decoded buffer、resolved theme、controller tabs、active tab 与安全
error state，不持久化 workbook cell 或 formula。

## 7. 运行模型与状态机

```text
selected sheet ArtifactContent
  -> lazy-load SpreadsheetPreview + local WASM URL
  -> 校验 estimated decoded size <=25 MiB
  -> decode existing data URL to ArrayBuffer 并再次校验 actual size <=25 MiB
  -> worker parse
      -> success: read-only viewer + optional HaaS sheet tabs
      -> invalid/unsupported/oversized: localized safe error
  -> selection changes: old boundary unmounts; stale result cannot replace current file
  -> unmount: disconnect theme observer and release viewer/controller resources
```

## 8. 安全与权限

- Workbook bytes 只留在本地 WebView 与 package worker；不得使用 remote `src`、CDN、telemetry
  或 external parser endpoint。
- `showImages=false` 防止 external relationship image 成为 DOM/canvas source。
- capture-phase navigation blocking 防止不可信 cell/image/shape hyperlink 调用 `window.open`、
  `javascript:`、custom scheme 或 privileged Tauri navigation。
- Read-only UI 是纵深防御；workbook macro、formula external reference、form control 或 embedded
  object 均不得执行。
- Log、metric、test output 与 error message 只含安全 kind/size/status，不含 workbook cell、
  formula、path、credential、signed URL 或 raw binary/base64。

## 9. 可观测性与性能

- Initial synchronous entry graph 不得包含 `xlsx`、`@extend-ai/react-xlsx`、worker 或 WASM。
- Spreadsheet JS viewer 必须是 dynamic chunk；WASM 必须是本地 emitted asset，raw 不超过
  5 MiB。打开 workbook 时不得请求 CDN/外部网络。
- 小型双 sheet workbook 应在 production preview 中 1.5 秒内展示首个 grid；打开与切换 sheet
  不得产生超过 200 ms 的 main-thread long task。
- 证据只记录 timing、byte size、sheet count、chunk/worker/WASM request name 与 error category，
  不记录 workbook content 或真实 path。

## 10. 失败、恢复、兼容与回滚

| 场景 | 必须行为 |
|------|----------|
| malformed/encrypted/unsupported workbook | 本地化错误；rail header/action 保持可用 |
| workbook 超过 25 MiB | 不执行 `atob`、buffer allocation、WASM init 或 parser；展示 too-large state |
| WASM 或 worker load 失败 | error boundary 展示安全 fallback；不白屏、不崩溃 |
| workbook 包含 external image relationship | 不发起 external request；不渲染 image |
| workbook 包含 cell/image/shape hyperlink | 点击不能导航或调用 `window.open` |
| 打开时切换主题 | viewer palette 更新，不重新读取 artifact API |
| loading 时切换文件/session | 旧结果随旧 component 丢弃 |

兼容性只影响 GUI，除有意替换 dependency 外均为 additive。既有 artifact URL、auth、local/
remote action 与 25 MiB backend limit 保持。只有存在无漏洞 parser 时才能回滚旧 viewer；恢复
`xlsx@0.18.5` 不属于可接受回滚。

## 11. 测试计划与验收

| ID | 优先级 | 验证 | 通过标准 |
|----|--------|------|----------|
| FV-MOP-01 | P0 | `npm ls xlsx` 与 `npm audit --omit=dev` | 无 `xlsx` package/advisory；替代依赖未引入 production vulnerability |
| FV-MOP-02 | P0 | 用真实最小双 sheet workbook 运行 component test | worker/WASM viewer 进入 grid；custom tab 切换；强制 read-only/default-toolbar/image props |
| FV-MOP-03 | P0 | 注入 malformed bytes、lazy import failure 与 loading 中切换文件 | 安全本地化错误、current file 胜出、无 blank rail/crash |
| FV-MOP-04 | P0 | 带 external image/hyperlink workbook，配合 network 与 `window.open` spy | workbook-originated request/navigation 为零；HaaS sheet tab 仍可用 |
| FV-MOP-05 | P0 | Vite dev、manifest traversal 与 production-preview timing | dev worker URL 不解析到 `.vite/deps`；viewer/worker/WASM 不在 sync graph；仅本地 async asset；小 workbook <=1.5 秒；无 >200 ms long task |
| FV-MOP-06 | P0 | light/dark、390/760/1440 px、200% zoom | tab/grid/error state 可读、有界、键盘可达 |
| FV-MOP-07 | P0 | Manager sidecar `.xlsx`、`.xlsm`、`.xls` 测试 | 均分类/列出为 `sheet`；25 MiB 与 binary/download 行为不变 |
| FV-MOP-08 | P0 | DMG build 与 packaged smoke | WASM/worker 从 packaged asset 解析；Manager/HaaS health/task gate 通过 |

### 11.1 实现顺序

1. 添加失败的 dependency/audit、classification 与 component-boundary 测试。
2. 添加 lazy spreadsheet viewer 与本地 WASM/worker 配置。
3. 添加 read-only sheet tabs、theme observer、navigation suppression 与安全失败。
4. 删除 `xlsx`、旧 `SheetViewer` 与 obsolete style，增加 `.xlsm` classification。
5. 执行 focused/full GUI、production preview、audit、强制 reviews 与 packaged smoke。

### 11.2 组件影响分析

| 组件 | 影响 | 必须动作 |
|------|------|----------|
| Artifact read/list | `.xlsm` 加入既有 spreadsheet format；size/auth shape 不变 | 仅更新 classification test；无 public schema delta |
| Manager GUI Performance | 大型 JS/WASM dependency 替换较小 SheetJS chunk | 强制 fully lazy graph、worker parsing 与明确 size/timing budget |
| Security Boundary | rich workbook renderer 增加 link/image 行为 | 禁用 image/navigation，并证明 external request 为零 |
| Manager File Preview | code/text 行为不变 | 保持独立 owner；CodeMirror 不依赖 Office viewer |
| ADK/HaaS protocol、session/event、model/MCP、container runtime | 无影响 | 仅运行现有 regression gate |
