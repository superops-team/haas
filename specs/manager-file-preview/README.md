# Manager File Preview Component Specification

**English** | [简体中文](README.zh-CN.md)

Status: MFP-001 through MFP-010 implemented; owner visual acceptance pending
Last reviewed: 2026-09-30
Change ID: manager-codemirror-file-preview
Related specs: [Artifact Store](../artifact-store/README.md), [Manager GUI Performance](../manager-gui-performance/README.md), [Manager Project Workbench Experience](../manager-project-workspace-experience/README.md)

## 1. Component Role

Manager File Preview owns the read-only rendering of ordinary source-code and text files opened
from the Manager Artifacts or Files surfaces. It turns the existing `ArtifactContent` read result
into a safe, responsive CodeMirror 6 viewport without changing artifact storage, read APIs, or
specialized previewers for Markdown, HTML, images, PDF, CSV, spreadsheets, folders, and Office
documents.

## 2. Sources and Rationale

| Source | Adopted content |
|--------|-----------------|
| Current Manager `RightRail` | One selected-file owner, stale-read protection, artifact/file origin scoping, dedicated media previewers |
| Local ZCode file viewer, inspected 2026-09-29 | Extension-to-language mapping, line-oriented read-only presentation, theme-aware highlighting, independent scrolling, deferred work, and a 256 KiB syntax-highlighting ceiling |
| CodeMirror 6 | Read-only editor state, virtualized viewport rendering, language extensions, line numbers, selection/copy behavior, and explicit lifecycle disposal |
| HaaS design tokens | Light/dark semantic colors, typography, borders, focus and selection states |

ZCode currently renders code through `@pierre/diffs` plus Shiki, not CodeMirror. HaaS adopts its
viewer contract and large-file discipline while deliberately using CodeMirror 6 as requested; it
does not copy ZCode implementation code, packages, or private color values.

The current Manager fallback is a wrapping `<pre class="artifact-code">`. It has no line numbers,
language recognition, syntax highlighting, editor viewport virtualization, or explicit large-file
degradation. This component replaces that ordinary code/text path rather than adding a second
selectable legacy viewer.

## 3. Upstream and Downstream Relationships

| Direction | Component | Relationship |
|-----------|-----------|--------------|
| Upstream | `RightRail` artifact viewer | Supplies selected path, `kind`, content and truncation state; owns loading/error/download behavior |
| Upstream | Artifact Store / Manager sidecar | Supplies bounded `ArtifactContent`; remains authoritative for access and stale-request safety |
| Downstream | CodeMirror 6 core | Creates the read-only document, viewport, line-number gutter and selection behavior |
| Downstream | Language loader | Resolves a safe local parser from basename/extension and loads it on demand |
| Downstream | HaaS design tokens | Supplies light/dark viewer, gutter, syntax, selection and focus colors |

## 4. Responsibility Boundaries

Responsibilities:

- Render `kind=code` and `kind=text` through one lazy-loaded, read-only CodeMirror surface.
- Resolve the supported language from the selected display path without reading file content.
- Load only the selected language parser and skip parsing for large or truncated content.
- Preserve line selection, browser copy, keyboard scrolling, horizontal code scrolling and text wrapping.
- Destroy the old EditorView and ignore stale language loads when the file changes or the component unmounts.
- Present an accessible, copyable plain-text fallback if the CodeMirror base chunk cannot load.

Non-responsibilities:

- No file editing, saving, diffing, commenting, folding, minimap, find/replace, command palette, or LSP.
- No replacement of Markdown, sandboxed HTML, image, PDF, CSV, spreadsheet, folder, or Office previewers.
- No content-based language guessing and no execution of file content.
- No artifact API, ADK, HaaS native protocol, session, event, credential, container, or persistence change.

## 5. Core Interfaces and Functional Requirements

### 5.1 React boundary

```ts
interface CodeFilePreviewProps {
  path: string;
  kind: "code" | "text" | string;
  content: string;
  truncated?: boolean;
}
```

`RightRail` MUST import this component through `React.lazy` only in its ordinary code/text branch.
The session shell, rail list, and specialized previewers MUST NOT synchronously import CodeMirror.

### 5.2 P0 requirements

- **MFP-001 — Single read-only viewer:** every successful ordinary `code` or `text` result uses
  `CodeFilePreview`. CodeMirror MUST set both `EditorState.readOnly` and
  `EditorView.editable.of(false)`. Text selection, copy and keyboard/trackpad scrolling remain
  available; document mutation does not.
- **MFP-002 — Deterministic language mapping:** path matching is case-insensitive, basename-aware,
  query/fragment-free, and independent of content. P0 supports JavaScript/JSX, TypeScript/TSX,
  JSON, HTML, CSS, Python, POSIX shell/Bash/Zsh, SQL, and YAML. Unknown extensions and extensionless
  files use plain text. Markdown keeps the existing rendered Markdown viewer.
- **MFP-003 — On-demand parsers:** the CodeMirror base loads only after a code/text preview opens;
  each language family is a statically analyzable dynamic import. Opening Python MUST NOT execute
  the JavaScript, SQL, or YAML parser loader. Repeated opens may reuse the browser module cache.
- **MFP-004 — Large-file degradation:** `256 * 1024` UTF-8 bytes is the syntax-parsing ceiling,
  matching the validated ZCode viewer policy. At or above the ceiling, or whenever
  `ArtifactContent.truncated === true`, the file still opens in the CodeMirror viewport with line
  numbers and selection, but without a language parser. A localized notice explains whether
  highlighting was disabled for size or the displayed content was truncated. No second legacy
  `<pre>` path is used for normal degradation.
- **MFP-005 — Presentation:** line numbers are visible and non-selectable. Code files preserve
  whitespace and use independent horizontal/vertical scrolling without wrapping; bounded ordinary
  text wraps. Large or truncated text disables wrapping and uses viewport-local horizontal scrolling
  so a pathological single line cannot block layout. The viewport fills the artifact preview body,
  uses the repository mono font at the established 13 px scale, and does not create body/page scrolling.
- **MFP-006 — Themes and accessibility:** all viewer surfaces and highlight classes derive from
  HaaS semantic CSS variables and update when `html[data-theme]` changes. Focus is visible without
  a persistent high-contrast frame, selected text remains legible in both themes, the viewport is
  keyboard reachable, and the accessible label includes the filename and read-only state.
- **MFP-007 — Lifecycle and failure:** exactly one EditorView owns the mounted file. Path/content/
  kind/truncation changes replace its state without allowing an older parser promise to overwrite
  the new file. Unmount destroys the view. Base-chunk or parser failure never loses content: base
  failure renders a bounded copyable fallback; parser failure keeps the CodeMirror plain-text mode.
- **MFP-010 — Unobstructed viewer actions:** the file header owns the visual stacking context for
  its action popover. Opening the `...` menu MUST place every visible menu item above the preview
  renderer, inside the visual viewport and inside the artifact rail at 320/390/760/1440 CSS px and
  200% zoom. CodeMirror, spreadsheet, PDF, image, HTML and loading/error surfaces MUST NOT paint over
  or clip the menu. The preview remains independently scrollable and opening the menu does not move
  its scroll position.

The P0 resolver table is normative:

| Language id | Case-insensitive path match |
|-------------|-----------------------------|
| `javascript` | `.js`, `.mjs`, `.cjs` |
| `jsx` | `.jsx` |
| `typescript` | `.ts`, `.mts`, `.cts` |
| `tsx` | `.tsx` |
| `json` | `.json` |
| `jsonc` | `.jsonc` |
| `html` | `.html`, `.htm`, `.xhtml` |
| `css` | `.css` |
| `python` | `.py`, `.pyw` |
| `shell` | `.sh`, `.bash`, `.zsh`, basename `.bashrc`, `.zshrc` |
| `sql` | `.sql` |
| `yaml` | `.yaml`, `.yml` |
| `plaintext` | every other path, including an empty path or unmatched basename |

The resolver strips only a terminal query or fragment for language selection; it never changes the
path passed to the artifact API. A recognized language is treated as code (no wrapping) even when a
legacy producer reports `kind=text`; `kind=text` wraps only when the resolved language is plaintext.

### 5.3 P1 requirements

- **MFP-008 — Additional languages:** Go, Rust, Java, C/C++, TOML and common configuration basenames
  MAY be added only through the same resolver/loader registry, focused tests and async-chunk budget.
- **MFP-009 — Optional controls:** wrap toggles, find and jump-to-line MAY be added later without
  changing the artifact API. They are not part of this change.

## 6. Data Model

The UI derives a content-free descriptor before mounting the viewer:

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

The descriptor and any diagnostic evidence MUST NOT contain file content, absolute host paths,
prompts, tool arguments, credentials, URLs, or selected text.

## 7. Runtime Model and State Machine

```text
selected code/text ArtifactContent
  -> lazy-load CodeFilePreview + CodeMirror core
  -> derive descriptor from path/kind/UTF-8 length/truncated
      -> large or truncated: mount read-only plain CodeMirror
      -> known and bounded: mount plain CodeMirror immediately
                              -> load selected parser -> reconfigure current view in place
      -> unknown: mount read-only plain CodeMirror
  -> path/content changes: invalidate loader generation -> replace EditorView
  -> unmount: invalidate loader generation -> destroy EditorView

base chunk failure -> copyable read-only fallback
parser failure     -> same CodeMirror viewport in plain-text mode
```

The loading placeholder occupies the final viewport dimensions while the CodeMirror base chunk
loads. Once core is available, a bounded known-language file becomes readable in plain mode before
its parser resolves; parser arrival reconfigures the current view without replacing the document,
selection or scroll position. Parser resolution MUST NOT block the artifact header or actions.

## 8. Security and Permissions

- CodeMirror treats content as document text; no file content is inserted through `innerHTML` or
  executed as script/style/markup.
- Language choice is allowlisted locally. A file extension cannot select an arbitrary import URL or
  package.
- Clipboard reads are not requested. Existing explicit copy actions remain user initiated.
- No content, selected range, absolute path, prompt, full tool arguments, or credentials enter logs,
  metrics, analytics, test snapshots, chunk names, or error messages.
- Artifact object scope, download authorization, sandboxed HTML behavior, `nosniff`, path traversal
  defenses, and remote/local action constraints remain owned by existing components.

## 9. Observability and Performance

- Production manifest evidence MUST prove CodeMirror core and language packages are absent from the
  initial synchronous entry graph and reachable through dynamic imports only.
- A cold bounded code preview SHOULD display its viewport within 200 ms after content becomes
  available on the local production-preview fixture; a warm repeat SHOULD display within 100 ms.
- A 10,000-line bounded fixture and a parser-skipped 512 KiB fixture MUST create no long task above
  100 ms during open/initial scroll and MUST keep the session shell responsive.
- Tests and performance output record only language id, byte bucket, mode, timing, chunk/request
  counts and long-task count. They never print content or host paths.
- This component introduces no polling, network request, persistent cache, event, metric or log sink.

## 10. Failure, Recovery, Compatibility, and Rollback

| Scenario | Required behavior |
|----------|-------------------|
| unsupported extension or extensionless file | open immediately in plain-text CodeMirror |
| content is at least 256 KiB | skip parser and wrapping, show size notice, retain line numbers/copy/viewport scrolling |
| server marks content truncated | skip parser and wrapping, show truncation notice, display all returned text |
| selected parser import rejects | keep content in plain-text CodeMirror; do not show a blank pane |
| CodeMirror base import/render fails | show every returned character in a viewport-bounded, independently scrolling, copyable read-only fallback plus a safe localized error |
| file changes while parser loads | stale result is ignored; only the current file is visible |
| theme changes while open | colors update in place; document and scroll position are not reset |
| component unmounts | EditorView is destroyed and no observer/listener remains |
| header action menu opens above any preview renderer | menu remains fully visible and pointer-reachable; preview geometry and scroll remain unchanged |

Compatibility is additive and GUI-local. ADK and `/v1/haas/*` APIs, artifact schemas, URL behavior,
event order, sessions, storage, security headers and container variants are unchanged. Rollback
removes the lazy viewer and its dependencies in one commit; no data migration or server rollback is
required. The old `.artifact-code` normal rendering path is removed when this component lands.

## 11. Test Plan and Acceptance

### 11.1 Functional verification cases

| ID | Priority | Verification | Expected evidence |
|----|----------|--------------|-------------------|
| FV-MFP-01 | P0 | Unit-test the path resolver with all P0 extensions, mixed case, basenames, query/fragment suffixes and unknowns | deterministic language id; Markdown remains outside this viewer |
| FV-MFP-02 | P0 | Render a bounded TypeScript file | read-only CodeMirror, line numbers, highlighted tokens, code does not wrap, copy selection remains possible |
| FV-MFP-03 | P0 | Render unknown ordinary text | plain CodeMirror, text wraps, no language loader runs |
| FV-MFP-04 | P0 | Render 256 KiB, single-line 512 KiB and `truncated=true` fixtures | parser loader and wrapping do not run; correct localized notice; returned text remains visible/copyable with viewport-local scrolling |
| FV-MFP-05 | P0 | Delay one language loader, switch to another file, then resolve the first; unmount/reopen | stale parser cannot replace current document; old EditorView is destroyed |
| FV-MFP-06 | P0 | Inject parser and base failures | parser failure keeps plain CodeMirror; base failure keeps bounded copyable fallback |
| FV-MFP-07 | P0 | Toggle light/dark/auto theme and test keyboard focus at 200% zoom | semantic colors update, focus visible, line/content alignment stable, no clipped actions |
| FV-MFP-08 | P0 | Build with Vite manifest and traverse the entry graph | no CodeMirror core/language module in synchronous graph; language families remain async |
| FV-MFP-09 | P0 | Production-preview Playwright opens code/text, quickly switches files, scrolls 10,000 lines, then opens 512 KiB | correct visible file, independent scroll, no stale content, budgets in section 9 pass |
| FV-MFP-10 | P0 | Package and smoke the Tauri app, opening a local TypeScript and large text file | browser/Tauri parity; fonts, scroll, theme and copy remain usable |
| FV-MFP-11 | P0 | Open the file `...` menu over code and spreadsheet previews at 390 px and 200% zoom | menu bounds stay within the rail/viewport; center points of every menu item hit that item rather than the preview; menu is neither clipped nor covered |

### 11.2 TDD and implementation sequence

1. Add failing resolver/descriptor tests for FV-MFP-01, -03 and -04.
2. Add failing component lifecycle/read-only tests for FV-MFP-02, -05, -06 and -07.
3. Add the exact CodeMirror core/language dependencies and implement the resolver, language loader,
   theme, component lifecycle and loading/failure boundaries.
4. Replace the ordinary `<pre>` branch, remove its obsolete CSS, and add localized notices.
5. Add manifest and production-preview cases for FV-MFP-08 and -09, then a failing geometry/hit-test
   case for FV-MFP-11 before changing header stacking.
6. Run focused/full GUI gates, required reviews, root gates, DMG build and packaged smoke.

### 11.3 Component impact analysis

| Component | Impact | Required action |
|-----------|--------|-----------------|
| Artifact Store | No protocol/storage change; existing `path`, `kind`, `content`, `truncated` are consumed | Keep object-scope and bounded-read tests unchanged |
| Manager GUI Performance | New heavy optional dependency and viewport work | Enforce lazy graph, parser split and runtime budgets |
| Manager Project Workbench | Files and Artifacts share the viewer | Verify both origins and quick switching |
| Security Boundary | Source text reaches a new renderer | Assert no execution, remote import, content log, or unsafe HTML path |
| ADK/HaaS protocol, sessions, events, model/MCP, container runtime | No impact | Existing compatibility/full-check evidence is sufficient |

All P0 requirements map to executable cases. P1 items are explicitly deferred and are not required
to remove the legacy ordinary-text path.
