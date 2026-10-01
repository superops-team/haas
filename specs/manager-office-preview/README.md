# Manager Office Preview Component Specification

**English** | [简体中文](README.zh-CN.md)

Status: Approved
Last reviewed: 2026-09-30
Change ID: manager-safe-office-preview
Related specs: [Artifact Store](../artifact-store/README.md), [Manager File Preview](../manager-file-preview/README.md), [Manager GUI Performance](../manager-gui-performance/README.md), [Security Boundary](../security-boundary/README.md)

## 1. Component Role

Manager Office Preview owns safe, read-only spreadsheet preview for `.xlsx`, `.xlsm`, and `.xls`
files opened from the Manager Artifacts or Files surfaces. It replaces the vulnerable SheetJS
browser parser with a lazy WebAssembly/Worker viewer while preserving local/default-app actions and
the existing artifact read boundary.

## 2. Sources and Rationale

| Source | Adopted content |
|--------|-----------------|
| Current HaaS Manager | `xlsx@0.18.5`, lazy `SheetViewer`, 25 MiB binary preview cap, local/remote action controls |
| npm audit, 2026-09-29 | `xlsx@0.18.5` has high-severity prototype-pollution and ReDoS advisories; npm exposes no patched SheetJS version |
| Local ZCode Office preview | Lazy Office dispatch, local WASM source, Worker-backed `@extend-ai/react-xlsx`, read-only mode, theme mapping, custom accessible sheet tabs, 25 MiB ceiling and error boundaries |
| `@extend-ai/react-xlsx@0.16.6` evaluation | MIT, React 18-compatible, zero production audit findings in an isolated install, 5.1 MiB tarball / 19.4 MiB unpacked, 4.2 MiB WASM |

HaaS follows ZCode's architecture rather than copying it blindly. The third-party viewer can call
`window.open()` for workbook hyperlinks and can render workbook images. HaaS therefore adds a
stricter document trust boundary: no URL source, no workbook image rendering, and no workbook-owned
external navigation.

## 3. Upstream and Downstream Relationships

| Direction | Component | Relationship |
|-----------|-----------|--------------|
| Upstream | `RightRail` | Supplies selected artifact path and binary `data_url`; retains header, copy/reveal/download and stale-read ownership |
| Upstream | Manager sidecar artifact read | Enforces scope and 25 MiB binary limit; classifies `.xlsx`, `.xlsm`, and `.xls` as `sheet` |
| Downstream | `@extend-ai/react-xlsx` | Renders workbook content in read-only mode and exposes controller state for sheet navigation |
| Downstream | bundled Duke WASM + worker | Parses workbook bytes locally without a CDN or external parser service |
| Downstream | HaaS theme tokens | Supplies light/dark viewer state and surrounding surfaces |

## 4. Responsibility Boundaries

Responsibilities:

- Preview supported spreadsheet formats without the vulnerable `xlsx` npm package.
- Keep the complete spreadsheet renderer, worker, and WASM outside the initial synchronous graph.
- Render formatted cells, merged ranges, frozen panes, charts and sheet navigation when supported by
  the parser, while disabling all editing and export controls.
- Keep all workbook bytes local to the WebView/worker and prevent workbook-originated navigation or
  subresource loading.
- Bound input size, loading, error, theme, stale completion and unmount behavior.

Non-responsibilities:

- No spreadsheet editing, formula mutation, save/export, macro execution, external data refresh,
  external image loading, or direct workbook hyperlink navigation.
- No DOC/DOCX/PPT/PPTX implementation in this change; their existing open-in-default-app behavior
  remains.
- No change to ADK, `/v1/haas/*`, artifact identity, session/event ordering, or container runtime.

## 5. Core Interfaces and Functional Requirements

### 5.1 React boundary

```ts
interface SpreadsheetPreviewProps {
  dataUrl: string;
  filename: string;
}
```

`RightRail` MUST reference only a lightweight `SpreadsheetPreviewBoundary`. The actual viewer and
all `@extend-ai/react-xlsx` imports MUST remain behind `React.lazy`.

### 5.2 P0 requirements

- **MOP-001 — Remove vulnerable parser:** remove direct/transitive production use of the `xlsx`
  package. `npm ls xlsx` must be empty and `npm audit --omit=dev` must contain no `xlsx` advisory.
  Do not use `npm audit fix --force`, an unofficial SheetJS tarball, or an audit ignore.
- **MOP-002 — Local Worker/WASM parsing:** pin exact `@extend-ai/react-xlsx@0.16.6`; import its packaged
  WASM through Vite `?url`, call `setWasmSource` in the lazy module, pass an `ArrayBuffer` via `file`,
  keep `useWorker=true`, and set Vite `worker.format="es"` because the package worker has its own
  module imports. Vite dependency optimization MUST exclude `@extend-ai/react-xlsx` while explicitly
  including its CommonJS `regl` leaf for default-export interop; otherwise the worker-relative
  `new URL("./xlsx-worker.js", import.meta.url)` resolves inside `.vite/deps` where the worker asset
  does not exist, or the unbundled viewer cannot import `regl`. Never use the viewer `src` prop or a CDN.
- **MOP-003 — Read-only product mode:** set `readOnly=true`, `showDefaultToolbar=false`, and do not
  expose export, mutation, formula editing, resize, form-control mutation, or clipboard-write tools.
  Workbook macros are never executed. The only custom controls are accessible sheet tabs.
- **MOP-004 — Sheet navigation:** when more than one visible sheet exists, render a compact tablist
  with active state, truncation/title, click selection, and Left/Right/Home/End keyboard movement.
  One-sheet workbooks do not reserve an empty toolbar row.
- **MOP-005 — Untrusted document boundary:** set `showImages=false`; workbook content cannot create
  a remote image request. Capture and cancel viewer-owned click navigation before third-party cell,
  image, or shape handlers can call `window.open`; only HaaS-owned sheet-tab controls are exempt.
  Use the DOM grid (`experimentalCanvas=false`) so this boundary and cell accessibility remain
  inspectable; internal sheet navigation is provided by the HaaS tabs, not workbook hyperlinks.
- **MOP-006 — Size and failure:** retain the Manager sidecar's 25 MiB binary ceiling and also pass
  `maxFileSizeBytes=25 * 1024 * 1024` to the viewer. Before `atob` or buffer allocation, estimate
  decoded bytes from the base64 payload and fail closed when the limit is exceeded. Invalid,
  encrypted, unsupported, oversized,
  worker, WASM, or render failures show a localized bounded error and preserve the existing
  open/download/reveal actions. Error text must not include workbook content or host paths.
- **MOP-007 — Theme and layout:** map `html[data-theme]` to the viewer's `isDark` prop and update in
  place through a scoped observer. The viewer owns one bounded scroll surface, fills the artifact
  body, and remains usable at 390/760/1440 px and 200% zoom.
- **MOP-008 — Lifecycle:** converting base64, loading WASM, parsing and rendering must be cancellable
  or stale-safe. Switching artifact/session or unmounting cannot publish the prior workbook. All
  observers, workers and listeners owned by HaaS are cleaned up.
- **MOP-009 — Format parity:** local classification/listing and remote media-type mapping recognize
  `.xlsx`, `.xlsm`, and `.xls` as `sheet`. Unsupported legacy workbook content falls back to the
  localized error/open action rather than crashing the rail.

## 6. Data Model

No public schema changes. Existing `ArtifactContent` remains the UI boundary:

```ts
interface ArtifactContent {
  path: string;
  kind: "sheet" | string;
  data_url?: string;
  preview_status?: "available" | "download_only" | "unavailable";
  download_status?: "available" | "unavailable";
}
```

The lazy component derives only local state: decoded buffer, resolved theme, controller tabs,
active tab and safe error state. It does not persist workbook cells or formulas.

## 7. Runtime Model and State Machine

```text
selected sheet ArtifactContent
  -> lazy-load SpreadsheetPreview + local WASM URL
  -> validate estimated decoded size <=25 MiB
  -> decode existing data URL to ArrayBuffer and verify actual size <=25 MiB
  -> worker parse
      -> success: read-only viewer + optional HaaS sheet tabs
      -> invalid/unsupported/oversized: localized safe error
  -> selection changes: old boundary unmounts; stale result cannot replace current file
  -> unmount: disconnect theme observer and release viewer/controller resources
```

## 8. Security and Permissions

- Workbook bytes stay in the local WebView and package worker; no remote `src`, CDN, telemetry, or
  external parser endpoint is allowed.
- `showImages=false` prevents external relationship images from becoming DOM/canvas sources.
- Capture-phase navigation blocking prevents untrusted cell/image/shape hyperlinks from invoking
  `window.open`, `javascript:`, custom schemes, or privileged Tauri navigation.
- Read-only UI is defense in depth; no workbook macro, formula external reference, form control, or
  embedded object may execute.
- Logs, metrics, test output and error messages contain only safe kind/size/status information, not
  workbook cells, formulas, paths, credentials, signed URLs, or raw binary/base64.

## 9. Observability and Performance

- Initial synchronous entry graph must contain neither `xlsx`, `@extend-ai/react-xlsx`, its worker,
  nor its WASM.
- The spreadsheet JS viewer must be a dynamic chunk; WASM must be a local emitted asset no larger
  than 5 MiB raw. No CDN/network request is permitted when opening a workbook.
- A small two-sheet workbook should show its first grid within 1.5 seconds in production preview.
  Opening and switching sheets must not create a main-thread long task above 200 ms.
- Evidence records only timing, byte size, sheet count, chunk/worker/WASM request names and error
  category; never workbook content or real paths.

## 10. Failure, Recovery, Compatibility, and Rollback

| Scenario | Required behavior |
|----------|-------------------|
| malformed/encrypted/unsupported workbook | localized error; rail header/actions remain usable |
| workbook exceeds 25 MiB | no `atob`, buffer allocation, WASM initialization or parser run; show too-large state |
| WASM or worker load rejects | error boundary renders safe fallback; no blank pane or app crash |
| workbook contains external image relationship | no external request; image is not rendered |
| workbook contains cell/image/shape hyperlink | click cannot navigate or invoke `window.open` |
| theme changes while open | viewer palette updates without re-reading the artifact API |
| file/session changes while loading | prior result is discarded with the prior component instance |

Compatibility is GUI-local and additive except for the deliberate dependency replacement. Existing
artifact URLs, auth, local/remote actions and 25 MiB backend limit are preserved. Rollback restores
the prior viewer component only if a non-vulnerable parser is available; restoring `xlsx@0.18.5` is
not an acceptable rollback.

## 11. Test Plan and Acceptance

| ID | Priority | Verification | Pass criteria |
|----|----------|--------------|---------------|
| FV-MOP-01 | P0 | `npm ls xlsx` and `npm audit --omit=dev` | no `xlsx` package/advisory; zero production vulnerabilities introduced by the replacement |
| FV-MOP-02 | P0 | Component test with a real minimal two-sheet workbook | worker/WASM viewer reaches grid; custom tabs switch active sheet; read-only/default-toolbar/image props are enforced |
| FV-MOP-03 | P0 | Inject malformed bytes, lazy import failure and file switch during load | safe localized error, current file wins, no blank rail/crash |
| FV-MOP-04 | P0 | Workbook with external image/hyperlink plus network and `window.open` spies | zero workbook-originated request/navigation; HaaS sheet tabs still work |
| FV-MOP-05 | P0 | Vite dev + manifest traversal and production-preview timing | dev worker URL resolves outside `.vite/deps`; viewer/worker/WASM absent from sync graph; local async assets only; small workbook <=1.5 s; no long task >200 ms |
| FV-MOP-06 | P0 | Light/dark, 390/760/1440 px, 200% zoom | tabs/grid/error state remain legible, bounded and keyboard reachable |
| FV-MOP-07 | P0 | Manager sidecar tests for `.xlsx`, `.xlsm`, `.xls` | all classify/list as `sheet`; 25 MiB and binary/download behavior unchanged |
| FV-MOP-08 | P0 | DMG build and packaged smoke | WASM/worker resolve from packaged assets; Manager and HaaS health/task gates pass |

### 11.1 Implementation sequence

1. Add failing dependency/audit, classification and component-boundary tests.
2. Add the lazy spreadsheet viewer and local WASM/worker configuration.
3. Add read-only sheet tabs, theme observation, navigation suppression and safe failures.
4. Remove `xlsx`, old `SheetViewer` and obsolete styles; add `.xlsm` classification.
5. Run focused/full GUI, production preview, audit, required reviews and packaged smoke.

### 11.2 Component impact analysis

| Component | Impact | Required action |
|-----------|--------|-----------------|
| Artifact read/list | `.xlsm` joins existing spreadsheet formats; size/auth shape unchanged | Update classification tests only; no public schema delta |
| Manager GUI Performance | Large JS/WASM dependency replaces smaller SheetJS chunk | Enforce fully lazy graph, worker parsing and explicit size/timing budgets |
| Security Boundary | Rich workbook renderer adds link/image behavior | Disable images/navigation and prove zero external requests |
| Manager File Preview | No code/text behavior change | Retain separate ownership; no CodeMirror dependency on Office viewer |
| ADK, HaaS native API, session/event, model/MCP, container runtime | No impact | Existing regression gates only |
