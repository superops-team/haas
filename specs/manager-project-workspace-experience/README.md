# Manager Project Workbench Experience Specification

**English** | [简体中文](README.zh-CN.md)

Status: MPW-022 through MPW-040 implemented; owner visual acceptance pending
Last reviewed: 2026-09-29
Change ID: `manager-project-workspace-experience`
Related specs: [Manager Conversation Experience](../manager-conversation-experience/README.md), [Manager HaaS Sidecar Backend](../manager-haas-sidecar-backend/README.md), [Manager Product Identity](../manager-product-identity/README.md), [Manager Delegation](../manager-delegation/README.md), [Stores](../stores/README.md), [Security Boundary](../security-boundary/README.md)

## 1. Component Role

Manager Project Workbench Experience owns how OpenHarness organizes projects, executable
workspaces, HaaS execution locations, Git context, conversations, command activity disclosure,
and desktop-window chrome into one coherent task workbench.

The product hierarchy is normative:

```text
Project
  -> one or more WorkspaceBindings (local folder/worktree or remote HaaS workspace)
  -> one default ExecutionTarget (local-managed or configured remote HaaS endpoint)
  -> conversations bound to one workspace + execution-target snapshot
  -> turns and chronological command/reasoning activity
```

This component is a Manager product surface. It MUST NOT redefine ADK fields, HaaS northbound
session semantics, or harness-native protocols.

## 2. Sources and Rationale

The design is driven by:

- user-provided ZCode reference screenshots for project-grouped navigation, project creation,
  Git branch selection, work-location selection, and compact command disclosure;
- current HaaS `manager/coworker/projects.py`, whose `project_key()` already collapses Git
  worktrees through the repository common directory;
- current `EndpointRecordStore`, which already defines secretless `local_managed` and `remote`
  HaaS endpoint identities;
- current Manager `SessionRecord`, HaaS binding, recent-workspace, folder-picker, and Git-summary
  boundaries; and
- the packaged macOS bug evidence that custom WebView chrome does not yet provide a complete
  native drag/double-click/maximize contract.

Three implementation strategies were evaluated:

1. derive sidebar groups only in React from session paths;
2. duplicate project/workspace/endpoint fields into every session; or
3. persist a Project aggregate and reference it from sessions.

Strategy 3 is required. Strategy 1 cannot represent renamed projects, remote workspaces, or Git
worktrees reliably. Strategy 2 duplicates authority and creates migration drift.

## 3. Goals, Non-goals, and Scenarios

### 3.1 Goals

- Group sidebar conversations by stable project identity instead of one flat recent list.
- Create a project with a user-visible name and selected workspace location.
- Match the reference's progressive project navigation: information on hover/focus, one
  project-scoped overflow menu, and a separate sidebar organization menu.
- Persist project pin, rename, manual order, archive/remove-from-sidebar, and conversation sort
  preferences without deriving a second client-side project model.
- Treat Git repository worktrees as workspace bindings under one project.
- Show Git branch, detached state, and dirty count; support safe branch search/create/switch.
- Select a configured local-managed or remote HaaS execution location before the first turn.
- Freeze workspace and execution target at accepted-session boundaries so live work cannot drift.
- Match the reference command list and inline Shell detail interaction without side-panel detail.
- Provide desktop-native window dragging, double-click maximize/restore, Dock/tray reopen, and
  platform-appropriate window controls.
- Preserve streaming performance, dual themes, keyboard access, secretless behavior, and the
  existing conversation-v2 single-owner component model.

### 3.2 Non-goals

- A general Git client, merge/rebase UI, remote branch fetch UI, or conflict editor.
- Permanent deletion of project files, Git repositories, worktrees, conversations, or transcripts.
- Arbitrary drag-and-drop reparenting of conversations between projects.
- Copying ZCode private protocols, telemetry, visual assets, or Electron implementation details.
- Sending a Manager host absolute path to a remote HaaS instance.
- Moving an accepted/running turn between endpoints.
- Adding a second conversation renderer, project derivation path, endpoint store, or legacy
  session-list fallback.
- Changing ADK `/run`, `/run_sse`, ADK Session/Event schemas, or public HaaS error semantics.

### 3.3 Primary scenarios

1. A user creates `haas`, chooses a local repository folder, and starts a conversation shown under
   that project.
2. A repository has several worktrees/branches; all appear under one project, while each session
   retains the workspace and branch snapshot it used.
3. A user starts a draft under the same project but chooses a configured remote HaaS endpoint and
   an endpoint-scoped remote workspace reference.
4. The user expands one completed command row and sees one inline Shell panel containing the safe
   command and output, then collapses it from the same row.
5. The user drags any non-interactive titlebar background and double-clicks it to maximize/restore
   using native window behavior.
6. The user hovers or keyboard-focuses a project or conversation row and sees one bounded summary
   card without changing selection or expansion; the project card exposes Pin and Edit shortcuts.
7. The user opens a project's trailing overflow menu to pin, edit, reveal, create a persistent
   worktree, archive its conversations, or remove it from the active sidebar without deleting data.
8. The user opens the project-section overflow menu to change project and conversation ordering;
   the chosen order survives restart. Pinned projects retain priority, while selection changes only
   active styling and expansion rather than moving a project row.

## 4. Upstream and Downstream Relationships

| Relationship | Contract |
|---|---|
| Sidebar / Composer / project dialogs | Consume Manager project projections only; never derive project identity independently |
| Manager session runtime | Persists immutable accepted workspace/endpoint snapshots and mutable draft selections |
| `projects.py` | Sole canonical-key derivation for existing local folders and Git common directories |
| Endpoint record store | Sole endpoint identity and URL-fingerprint owner; tokens remain opaque secret refs |
| HaaS backend | Executes against the frozen endpoint/workspace binding and returns canonical process events |
| Git service | Reads repository state and performs guarded mutations; React never shells out directly |
| Tauri shell | Owns native window drag/maximize/reopen behavior; React only declares hit regions and intent |
| Conversation projection | Owns chronological work rows and inline command disclosure; project UI cannot duplicate activity facts |
| Project navigation controller | Owns the project-list projection, order preferences, mutation pending/error state, and stable callbacks; Sidebar does not fetch or persist projects directly |
| Sidebar presentation | Renders memoized project/conversation rows and emits typed intents; it does not own persistence, derive project identity, or duplicate overlay positioning |

## 5. Responsibility Boundaries

### 5.1 Project authority

- `ProjectStore` owns project id, canonical key, display name, pinned state, manual ordering,
  archive state, defaults, and workspace membership.
- Existing `project_key(workspace)` remains the only local canonical-key derivation. A Git common
  directory maps all worktrees to one project; a non-Git folder maps by canonical resolved path.
- React receives `ProjectSummary[]`; it MUST NOT group by basename, raw path equality, current
  branch, or session title.
- Removing a project from the active sidebar is exactly `archived=true`. Archive/unarchive is
  additive and never deletes project metadata, workspace bindings, sessions, transcripts, or files.
- Archiving project conversations updates only the project's currently unarchived conversations;
  it does not archive or remove the project itself.
- Project display names are not identity and MAY repeat. The UI disambiguates duplicate names with
  safe workspace/endpoint context. The built-in `prj_personal` project uses canonical key
  `manager://personal`, owns sessions without an explicit workspace, and cannot be deleted or
  renamed.

### 5.2 Workspace authority

- A `WorkspaceBinding` is an executable location, not a project alias.
- Local bindings use a canonical local path. Remote bindings use an endpoint-scoped opaque
  `remoteWorkspaceRef` and a safe display label; local absolute paths MUST NOT be reused remotely.
- Git state is an observed snapshot, not workspace identity. Branch changes update the snapshot
  but do not create a new project.

### 5.3 Execution-target authority

- `EndpointRecordStore` remains authoritative for endpoint id, mode, base URL fingerprint,
  server identity, TLS policy, and token ref.
- A project may define a default endpoint, while each draft may explicitly choose another
  configured endpoint.
- First command acceptance freezes `endpointId`, `workspaceBindingId`, `harnessId`, policy/profile
  revision, and remote workspace ref into the session binding.
- An endpoint switch after accepted work creates a new conversation by default. Rebind is allowed
  only for an empty idle draft; running/queued/waiting/paused sessions reject it structurally.

### 5.4 Window authority

- Tauri owns window operations. CSS cannot emulate maximize, drag, close, minimize, or reopen.
- Non-interactive titlebar background uses Tauri's synchronous drag-region path. Interactive
  controls are explicit no-drag islands.
- Double-click on a drag region toggles native maximize/restore once. It MUST NOT fire when the
  target is a button, link, input, selectable transcript text, popover, or disclosure row.

## 6. Core Interfaces

These are Manager-local APIs, not HaaS northbound APIs. JSON uses camelCase. Every mutation accepts
`Idempotency-Key` and returns the first result for duplicates.

### 6.1 Project APIs

```text
GET    /v1/projects
POST   /v1/projects
PATCH  /v1/projects/{projectId}
POST   /v1/projects/reorder
POST   /v1/settings/sidebar-order
POST   /v1/projects/{projectId}/reveal
GET    /v1/projects/{projectId}/worktrees/preview?branchName=...
POST   /v1/projects/{projectId}/worktrees
POST   /v1/projects/{projectId}/sessions/archive
GET    /v1/projects/{projectId}/workspaces
POST   /v1/projects/{projectId}/workspaces
GET    /v1/projects/{projectId}/sessions
```

`POST /v1/projects`:

```json
{
  "name": "haas",
  "workspace": {
    "location": "local",
    "path": "/safe/user-selected/path"
  },
  "defaultEndpointId": "hep_local_managed"
}
```

Remote creation uses:

```json
{
  "name": "haas-remote",
  "workspace": {
    "location": "remote",
    "endpointId": "hep_team_dev",
    "remoteWorkspaceRef": "wsref_opaque",
    "displayPath": "~/workspace/github/haas"
  },
  "defaultEndpointId": "hep_team_dev"
}
```

Responses never contain endpoint bearer tokens, unapproved remote host filesystem paths, or raw
project configuration.

`PATCH /v1/projects/{projectId}` accepts a non-empty subset of `name`, `defaultEndpointId`, `pinned`,
and `archived`. `name` remains 1..80 trimmed characters; a new default endpoint must already exist
and pass identity validation. `prj_personal` rejects rename/pin/archive. Archiving clears `pinned`;
a request that sets both true is rejected. Unarchiving restores the stored manual position without
reconstructing identity from a display name. Archiving/removing is rejected while any owned
conversation is running, waiting, queued, pausing, paused, resuming, or stopping.

`POST /v1/projects/reorder` accepts the complete ordered list of mutable, unarchived, non-Personal
project ids plus an `observedRevision`, independent of the current search/filter. It atomically
rewrites contiguous manual positions or returns `project_order_stale`; missing, duplicated,
foreign, Personal, or archived ids are rejected. Manual movement is disabled while a search/filter
is active or a non-manual sort mode is selected.

`GET /v1/projects` additively returns `orderRevision` with `projects`. The revision changes whenever
project creation, rename, pinned state, archive state, or manual position can change the visible
ordering. Existing clients that read only `projects` remain compatible. The GUI transport keeps its
existing `getProjects(): ProjectSummary[]` wrapper for old callers and adds one projection reader
for mutation-aware sidebar ownership; components never parse response shapes directly.

`POST /v1/settings/sidebar-order` follows the existing Manager settings mutation convention and
persists `projectOrder` (`manual | recent | name`) and
`conversationOrder` (`recent | oldest | name`) as Manager UI preferences. It does not rewrite
project positions or session timestamps. Preference mutation is serialized with the Manager's
other settings writers and uses atomic file replacement so concurrent settings updates cannot
truncate or lose unrelated keys.

`POST /v1/projects/{projectId}/reveal` is a capability-scoped local desktop action. It resolves
only the project's validated local primary binding and asks the OS file manager to reveal it.
Remote, missing, and untrusted projects return a structured unsupported result. The server projects
capability and safe disabled-reason fields; React does not infer them from paths or platform globals.

`POST /v1/projects/{projectId}/worktrees` accepts a validated branch name and optional safe display
name, then creates one persistent Git worktree through the Manager Git service. It is available
only for an idle local Git project; dirty/conflict/stale/occupied guards from section 6.2 apply.
The confirmation view previews the server-chosen sibling destination
`<repo-parent>/<repo-name>-<sanitized-branch>`; collisions receive a deterministic numeric suffix.
The client sends no destination path. Success creates a new WorkspaceBinding under the same project
and never changes the current conversation's frozen binding.

`GET /v1/projects/{projectId}/worktrees/preview` runs the same capability, branch validation, and
collision-resolution logic without mutating Git or the filesystem. It returns only the local
user-visible `displayPath`; the subsequent create request still carries no destination path.

`POST /v1/projects/{projectId}/sessions/archive` atomically archives the project's currently
unarchived sessions and returns the affected count. Running, waiting, pausing, paused, resuming,
stopping, or queued sessions are rejected as `project_sessions_busy`; no turn is interrupted
implicitly.

Successful mutations return authoritative projections rather than requiring optimistic guessing:

```text
PATCH project            -> { project: ProjectSummary, orderRevision }
POST reorder             -> { projects: ProjectSummary[], orderRevision }
POST sidebar-order       -> { projectOrder, conversationOrder }
POST reveal              -> { ok: true }
POST worktrees           -> { project, workspace, git, orderRevision }
POST sessions/archive    -> { archivedSessionIds: string[], archivedCount: integer }
```

Mutation idempotency is scoped by operation and target id before entering the shared mutation
store, so the same caller-generated key used accidentally for two different operations conflicts
rather than replaying an unrelated result.

Stable Manager-local errors are:

| Code | Meaning |
|---|---|
| `project_invalid` | Name or source workspace is invalid |
| `project_workspace_exists` | Canonical workspace already belongs to a project; response identifies that project |
| `workspace_unavailable` | Local path is missing/untrusted or remote workspace cannot be resolved |
| `endpoint_unavailable` | Endpoint identity/health/capability validation failed |
| `git_state_stale` | Observed Git revision no longer matches |
| `git_checkout_blocked` | Dirty/conflict/worktree/active-session guard rejected mutation |
| `session_binding_frozen` | Accepted session cannot change workspace or endpoint |
| `project_not_found` | The stable project id does not exist |
| `project_protected` | The built-in Personal project rejects rename/archive/removal |
| `project_order_stale` | Visible project membership/order changed since observation |
| `project_capability_unsupported` | Reveal/worktree action is unavailable for this project/runtime |
| `project_sessions_busy` | One or more project conversations cannot be archived while active |

Conflict/validation errors are structured 409/422 responses with safe fields and never require
callers to parse prose.

### 6.2 Git-context APIs

```text
GET  /v1/workspaces/{workspaceBindingId}/git
POST /v1/workspaces/{workspaceBindingId}/git/switch
POST /v1/workspaces/{workspaceBindingId}/git/branches
```

The read response includes `isRepository`, `headRefType`, `branchName`, `dirtyFileCount`, local
branches, current worktree occupancy, and observed revision. It excludes file contents and diff.

Mutations require an idle local workspace and an observed revision. The server rejects stale
revision, unresolved conflicts, destructive overwrite, an active turn, or a branch owned by
another worktree. The UI offers a new worktree when checkout would violate concurrent-session
isolation. No mutation runs from React or through the agent tool path.

### 6.3 Endpoint and session projection

```text
GET /v1/haas/endpoints
GET /v1/sessions?projectId={projectId}
```

Existing session-list responses gain optional additive fields:

```json
{
  "projectId": "prj_...",
  "workspaceBindingId": "wsb_...",
  "endpointId": "hep_local_managed",
  "executionLocation": "local",
  "branchSnapshot": "main"
}
```

Old clients continue using `workspace`. New clients use project/workspace identity when present.
The migration adapter exists only at the server projection boundary and MUST NOT create a second
React renderer.

### 6.4 Window commands

The Tauri bridge exposes typed commands for `startDragging`, `toggleMaximize`, `isMaximized`,
`minimize`, `closeToTray`, and `showMain`. Commands return structured success/failure and never
swallow an unsupported-platform error as success. Capability permissions are explicitly listed in
`src-tauri/capabilities/default.json`.

## 7. Data Model

### 7.1 Project

```text
Project {
  projectId: prj_...
  canonicalKey: string          # unique; project_key(), manager://personal, or remote digest
  name: string                  # 1..80 user-visible characters
  primaryWorkspaceBindingId: wsb_...
  defaultEndpointId: hep_...
  pinned: boolean
  order: integer
  archived: boolean
  createdAtMs / updatedAtMs: int64
}
```

```text
SidebarOrderPreferences {
  projectOrder: manual | recent | name
  conversationOrder: recent | oldest | name
}

ProjectListProjection {
  orderRevision: int64
  projects: ProjectSummary[]
}

ProjectSummary {
  ...Project
  workspaceCount / sessionCount / activeSessionCount / archivedSessionCount: integer
  workspaces: WorkspaceBinding[]
  capabilities: {
    reveal: { enabled: boolean, reasonCode: string? }
    createWorktree: { enabled: boolean, reasonCode: string? }
  }
}
```

### 7.2 WorkspaceBinding

```text
WorkspaceBinding {
  workspaceBindingId: wsb_...
  projectId: prj_...
  location: local | remote
  endpointId: hep_...
  localPath: string?             # local only, owner-readable persistence
  remoteWorkspaceRef: string?    # remote only, opaque
  displayPath: string            # home-collapsed / endpoint-safe
  git: GitSnapshot?
  state: available | missing | reconnecting | unavailable
  createdAtMs / updatedAtMs: int64
}
```

### 7.3 Session binding additions

```text
ManagerSessionBinding {
  projectId: prj_...
  workspaceBindingId: wsb_...
  endpointId: hep_...
  endpointFingerprint: sha256:...
  branchSnapshot: string?
  workspaceRevision: string?
}
```

Session binding is immutable after first acceptance except through an explicit, idle, empty-draft
rebind. Later project rename/order/default changes do not rewrite historical session binding.

### 7.4 Migration

On first read of a legacy session:

1. use a validated explicit historical project/workspace binding when present;
2. otherwise derive with `project_key(workspace)`, or use `manager://personal` when no workspace
   exists;
3. upsert one Project and applicable WorkspaceBinding;
4. bind the session to `hep_local_managed` unless a validated historical HaaS binding names an
   endpoint;
5. persist the result transactionally; and
6. return one normalized projection.

Migration is idempotent and resumable. It does not move workspace files or rewrite transcripts.
`GET /v1/projects` is the migration barrier: it completes normalization before returning, so the
GUI never maintains a flat legacy-session renderer or a temporary migration group.

The additive migration initializes `pinned=false`, preserves every existing `position` and
`archived` value, creates one monotonic order-revision record, and initializes missing sidebar
preferences to `manual` project order plus `recent` conversation order.

For a remote workspace, `canonicalKey` is an opaque digest of
`endpointId + NUL + remoteWorkspaceRef`; displayPath/name never participates in identity.

## 8. Product and Interaction Contract

### 8.1 Project sidebar

- The sidebar's only hierarchy is `Projects -> conversations`. The server migration barrier binds
  every legacy session before projection; workspace-less sessions appear under built-in Personal.
- Initial loading distinguishes an unresolved project projection from a resolved empty projection.
  While unresolved, the navigation renders a fixed-height neutral project skeleton and MUST NOT
  render the session-only legacy list. Once the first request settles, an authoritative empty result
  may use the applicable non-project layout. Background refreshes retain the last settled projection
  instead of returning to the loading shell.
- Project names and conversation titles use the shared 12 CSS px navigation role with a 1.35 line
  height. Active project emphasis is capped at weight 500; relative age remains the 11 CSS px
  caption role with tabular numerals. Typography MUST NOT inherit the 14 px reading-body role or
  use 600/700 weight as the primary selected-state signal. Project and conversation hit targets
  remain at least 28 CSS px, preserving accessibility while increasing information density.
- The project-section header exposes exactly two quiet controls: a plain `+` that only opens Create
  Project and a `...` organization menu. The old folder-plus glyph is retired. `+` remains visible;
  `...` appears on section hover or `:focus-within`. Both reserve at least a 28 CSS px hit target and
  remain keyboard reachable without moving the heading.
- A project row uses a semantic primary disclosure button plus sibling actions; it never nests
  interactive controls. It shows folder/repository icon, truncated name, expand/collapse, current
  availability, and reserved trailing New conversation plus `...` controls revealed by row hover or
  `:focus-within`. The reserved trailing area prevents the name or chevron from shifting. A
  conversation row similarly exposes a semantic selection button plus sibling Pin/Unpin and
  Archive shortcuts. Shortcut/menu events never toggle a project or select a conversation.
- Hovering for 300 ms or keyboard-focusing a project row opens one summary card with project name,
  active task count, safe display path/remote label, and Pin/New conversation shortcuts. A safe hover corridor keeps the
  card open while moving from row to card. A conversation row card is informational and shows title,
  owning project, and relative update age. Cards close after leaving both anchor and card, focus
  departure, Escape, ancestor scroll, row unmount, or opening a menu; they never change selection.
- Pointer hover uses the 300 ms open delay and a short 120 ms corridor-close delay; keyboard focus
  opens immediately. Native `title` tooltips are removed from rows that own a summary card so two
  hover surfaces never compete.
- Project and conversation hover are variants of one discriminated overlay state. Scheduling a
  different anchor closes the visible preview synchronously before starting its dwell timer, and
  opening either variant replaces the other. At most one hover card exists in the DOM. Opening a
  project, organization, or conversation action menu cancels hover state before the menu appears.
- Conversation metadata and progressive row actions share one reserved trailing grid cell. Hover,
  focus, menu-open, and movement between expanded and collapsed project groups change only
  opacity/visibility and pointer availability; row `x/y/width/height`, title width, and project
  expansion state remain unchanged.
- The interactive project card is a labelled non-modal `role="group"`; focus may enter its Pin/New conversation
  controls without closing it. The informational conversation card uses `role="tooltip"` and its
  anchor uses `aria-describedby` only while the card exists. Project disclosure buttons expose
  `aria-expanded`; overflow triggers expose `aria-haspopup="menu"` and `aria-expanded`.
- The project overflow menu contains, in this order: Pin/Unpin, Edit project, Reveal in Finder,
  Create persistent worktree, Move (Up/Down, Manual mode only), Archive conversations, and Remove
  from sidebar. Unsupported actions remain visible but disabled with a safe reason. Personal cannot
  be renamed or removed. Destructive-looking actions require a second explicit confirmation view
  inside the same menu. Edit reuses the project dialog in edit mode rather than adding a second form.
- Edit mode changes only the display name and validated default execution endpoint for future
  drafts. Existing workspace bindings are listed read-only and accepted sessions keep their frozen
  endpoint/workspace snapshot. Adding or relinking a workspace remains a separate explicit flow.
- New conversation is owned by the pencil/compose shortcut on the project row and project hover
  card. Invoking either shortcut creates one fresh draft session bound to the selected Project's
  primary workspace and default endpoint, preserves earlier sessions and their drafts, closes the
  hover surface, and does not open Edit Project or toggle project expansion. `Edit project` appears
  only in the Project `...` menu and remains the sole entry to project metadata editing.
- `Remove from sidebar` sets the project archived flag and is reversible from archived-project
  management. It preserves files, Git state, workspace bindings, sessions, transcripts, artifacts,
  and immutable accepted-session bindings. `Archive conversations` preserves the project row.
- The section organization menu owns two submenus plus `Archived projects`, which lists hidden
  projects and restores one without reconstructing it. Project order supports Manual, Recent activity,
  and Name. Conversation order supports Recent update, Oldest update, and Name. Pinned projects and
  conversations always lead. Selection and liveness are presentation state and never override the
  chosen conversation order. Manual
  project movement is offered as Move up/Move down in the project menu and persists server-side.
- Project sorting is deterministic: pinned explicit projects first, then the selected mode; ties
  use persisted manual position and finally `projectId`. Selecting a project never participates in
  sorting and therefore cannot move its row.
  Personal is always the final active group and is excluded from pin/reorder/archive controls.
  Conversation sorting is pinned first, then the selected mode; ties use normalized `updated_at`
  descending and finally `session_id`. Selecting a conversation changes only its active styling and
  context. A liveness transition may change status decoration, but liveness itself is not a sort key.
  Recent project activity is the
  maximum unarchived conversation `updated_at`, falling back to project `updatedAtMs` when none
  exists.
- Conversations render under their owning project with the selected stable ordering. The same
  session appears once.
- Project expansion is explicit state, not an XOR against whichever project becomes active. A
  manually expanded project remains expanded when one of its conversations is selected; changing
  active conversation updates styling and context only. Search may temporarily reveal matching
  groups without overwriting the stored expansion choice.
- Project and conversation rows use stable ids and each expanded project is bounded by the
  configured peek limit. Streaming a turn MUST NOT rerender unrelated project headers.
- The project group shows task count and safe display path or remote endpoint label. It never
  exposes endpoint URL credentials.
- The visible task count is `activeSessionCount`; archived conversations are excluded. Total and
  archived counts remain available only in archived-management/detail surfaces.
- All menus/cards are rendered through one shared anchored-overlay primitive with collision
  handling. At 320/390/760/1440 px they stay inside the window, never cover the Composer, and do not
  create a second sidebar renderer.
- Only one sidebar overlay is open at a time. Menus use ArrowUp/ArrowDown, Home/End, Enter/Space,
  Escape, outside-click dismissal, focus return to the opener, and disabled-item skipping. Hover
  cards do not open on coarse-pointer hover; row shortcuts and `...` controls remain visibly
  available for coarse pointers and their anchors remain keyboard accessible.
- The overlay owner renders once through a portal outside the sidebar scroll clip, uses fixed
  coordinates derived from the anchor, flips/clamps against the visual viewport and Composer top
  edge, and recalculates on resize. Ancestor scroll dismisses instead of leaving a detached overlay.
- Overlay width uses the shared popover token with a 320 CSS px maximum and an 8 CSS px viewport
  gutter. It inherits the semantic surface/border/shadow/radius tokens in both themes; no copied
  ZCode color, shadow, or asset value enters the implementation.
- The existing large Sidebar is not allowed to absorb independent timers and mutation workflows per
  row. Pure ordering selectors live outside React; memoized ProjectRow/ConversationRow components
  receive primitive or stable props; one project-navigation controller owns requests and one overlay
  host owns timers/listeners. A transcript token update must not rebuild project ordering or overlay
  descriptors.

### 8.2 Create project dialog

- Entry points: project-section `+`, empty state, and command palette.
- Required name input and one source-workspace section.
- Local mode uses the native folder picker and shows the canonical safe path before confirmation.
- Remote mode chooses an already configured remote HaaS endpoint and enters an endpoint-scoped
  workspace reference. Readiness/capability validation runs before the first task is accepted; the
  endpoint must advertise the selected harness/session capability. Endpoint creation remains in
  Settings.
- Submit stays disabled until validation succeeds. Cancel preserves no partial project record.
- Duplicate local canonical key opens the existing project and offers adding the selected path as
  another workspace rather than creating a duplicate.

### 8.3 Composer context bar

- A draft shows three quiet context controls above the input: Project, Work location, and Git
  branch when applicable.
- The controls render as one compact context shelf attached behind the Composer. The neutral shelf
  is inset 16 CSS px from each Composer edge, exposes only its top outer corners, and extends 12 CSS px behind the raised
  Composer edge; there is no canvas-colored gap or unrelated floating-chip row between them.
- Project, Work location, and Git branch are borderless peer triggers on one 30 CSS px centerline.
  They use the UI type role, 14 CSS px single-weight semantic icons, primary readable text, and one
  compact horizontal spacing rhythm. The local trigger uses a device glyph rather than code
  brackets. Individual neutral fills appear only for hover, keyboard focus, or an open menu.
- At 320/390/760/1440 CSS px, and separately at 200% zoom on the desktop fixture, the shelf remains centered and contained, the three
  available triggers do not wrap, and long project/endpoint/branch labels ellipsize locally without
  pushing sibling triggers outside the shelf or changing the shelf/Composer overlap. Omitted Branch consumes no space.
- Each trigger exposes `aria-haspopup="menu"`, truthful `aria-expanded`, a visible keyboard focus
  indicator, and a minimum 30 CSS px hit height. Opening or closing a menu does not alter trigger,
  shelf, or Composer geometry.
- Work location lists `Local` plus configured remote HaaS endpoints. Readiness is revalidated on
  send; an unavailable endpoint remains selected with a safe retry state and cannot accept work.
- If a configured remote endpoint is not yet bound to the active project, the work-location menu
  collects its endpoint-scoped workspace ref, creates the binding idempotently, and selects it as a
  new draft. It never rewrites the accepted conversation.
- Branch is read-only for remote endpoints that do not advertise Git mutation capability.
- Switching project/workspace/endpoint while the draft is empty updates its scoped draft key.
  Switching with text/attachments prompts to move the draft; no content is silently discarded.
- After accepted work, changing endpoint or workspace creates a new session under the project.

### 8.4 Git branch interaction

- The trigger displays current branch or `Detached HEAD`; dirty count is secondary.
- The popover supports search, keyboard navigation, current selection, and `Create new branch`.
- Read-only display is P0. Safe switch/create is P1 and follows §6.2 guards.
- A branch used by another worktree is not checked out destructively; offer opening that worktree
  or creating a new worktree.

### 8.5 Command activity disclosure bugfix

- Completed work renders a compact chronological list like the reference: read/search rows and
  one command row per execution, with semantic icon, safe summary/command, terminal state, and a
  trailing disclosure chevron.
- A collapsed command row has exactly one visual line. It shows the safe command as the sole
  primary label, truncates overflow with a trailing ellipsis at the available width, and keeps the
  terminal state plus disclosure chevron fixed at the trailing edge. The redundant visible
  `Ran a command` category line is absent. Ellipsis is presentation-only: JavaScript, persistence,
  event payloads, copy/evidence retrieval, and the accessible command name retain the complete
  redacted command.
- Clicking the row expands one inline panel immediately below that row. It never opens a right
  rail or bottom drawer.
- The panel contains a quiet `Shell` heading, the complete redacted `$ <safe command>` with safe
  wrapping, bounded output preview, omitted count, exit code/duration when useful, and full scoped
  evidence only after authorized retrieval.
- Full evidence output replaces the persisted preview; it does not duplicate it. Clicking the
  source row or pressing Escape collapses and returns focus.
- Raw tool arguments, credentials, external host paths, and signed URLs remain excluded.

### 8.6 Native desktop window behavior

- Every non-interactive area of the top 44 px chrome is draggable, including sidebar header,
  central title background, and empty left/right topbar space.
- Interactive controls are no-drag islands with unchanged click/keyboard behavior.
- Double-clicking draggable chrome invokes Tauri `toggleMaximize` exactly once. It intentionally
  provides maximize/restore on macOS even if the user's system titlebar preference is Minimize;
  Windows/Linux use the same platform-supported maximize contract.
- Drag from maximized state follows platform behavior and never starts from transcript selection.
- Traffic lights/window controls remain aligned across sidebar expanded/collapsed states, zoom,
  maximize, restore, fullscreen, and theme changes.
- Close-to-tray, Dock reopen, single-instance activation, minimum size, restored bounds, and
  multi-display clamping remain intact.

## 9. Runtime Models and State Machines

### 9.1 Project creation

```text
idle -> validating -> creating -> ready
                    -> duplicate_existing
                    -> failed -> editing
cancel from idle/validating/failed -> closed (no record)
```

Filesystem/remote validation occurs before the transaction. Project + initial workspace binding
commit atomically.

### 9.2 Project navigation mutations

```text
idle -> mutating -> committed -> refreshed
                 -> stale -> refreshed (no optimistic reorder retained)
                 -> failed -> idle (previous projection retained)

visible -> remove-confirm -> archived
project-visible + conversations-visible -> archive-confirm -> project-visible + conversations-archived
```

Only one mutation per project is in flight. The UI may show a transient pending affordance but the
server projection remains authoritative. Duplicate idempotency keys return the first result.
Closing a hover card or menu never cancels an accepted mutation.

Archive-conversations and remove-from-sidebar share a project-scoped mutation gate with Manager
turn admission. The handler acquires the gate, resolves current project membership, rechecks every
owned session's authoritative lifecycle/queue state, commits or rejects, and only then releases the
gate. A new turn for that project cannot pass admission between the busy check and commit.

### 9.3 Draft target binding

```text
unbound_draft -> bound_draft -> accepting -> accepted_frozen
bound_draft -- target change --> bound_draft
accepted_frozen -- target change --> new_bound_draft
```

An acceptance-unknown state retains the selected target and draft. Retry reconciles the command
receipt before a new send.

### 9.4 Remote endpoint

```text
configured -> checking -> ready
                     -> unavailable
ready -> reconnecting -> ready | unavailable
```

No task is sent until endpoint identity, TLS policy, token ref, capability, and remote workspace
scope validate. Failure never falls back to local execution.

The Manager resolves a remote session only from the frozen `SessionProjectBinding`: it loads the
matching `EndpointRecordStore` record, verifies its URL fingerprint, resolves that record's
`SecretRef` in memory, checks `/v1/haas/ready?scope=execution` and harness interaction capability,
then calls the remote ADK `/run_sse` path. It passes `remoteWorkspaceRef` unchanged as the remote
sandbox workspace root and never normalizes it through the Manager host filesystem. The remote
HaaS instance owns its active harness/provider profile; Manager MUST NOT broker a local provider
credential or local mount manifest into a remote endpoint. Missing endpoint records, credentials,
workspace refs, readiness, or capabilities fail with a structured unavailable result before task
acceptance.

## 10. Security and Permissions

- Local folder selection is explicit user intent and still passes workspace trust/policy checks.
- Remote endpoints require HTTPS and TLS verification unless the existing explicit development
  override is enabled. URLs cannot carry credentials, paths, queries, or fragments.
- Endpoint bearer values remain in SecretStore; project/session records persist only `tokenRef`,
  endpoint id, and URL fingerprint.
- Project names, branch names, display paths, and remote labels are untrusted display text and are
  never shell-interpolated.
- Hover cards and menus render these values as text only. They do not emit analytics or persistent
  logs containing names, titles, paths, branch names, or menu arguments.
- Reveal and persistent-worktree actions resolve a stored validated binding by id. The client never
  supplies a raw filesystem path to either action.
- Git commands use argv, canonical cwd, timeout, output bounds, and an allowlisted operation set.
- Branch mutation requires the user's direct UI action and cannot be initiated by transcript
  content, agent tool output, project config, or remote server response.
- Remote HaaS receives only its endpoint-scoped workspace ref; Manager local absolute paths and
  host mounts are forbidden.
- Logs/metrics exclude raw prompt, full tool args, bearer values, signed URLs, and exact local
  paths. IDs and safe error codes are sufficient for correlation.

## 11. Observability

Content-free signals:

- `manager_project_total{state}`
- `manager_project_migration_total{result}`
- `manager_workspace_binding_total{location,state}`
- `manager_execution_target_check_total{mode,result}`
- `manager_git_operation_total{operation,result}`
- `manager_window_action_total{action,result,platform}`
- `manager_activity_disclosure_total{action,kind}`
- `manager_project_action_total{action,result}`
- `manager_sidebar_order_change_total{dimension,result}`

Logs include trace id, project/session/workspace/endpoint ids, safe result code, and elapsed time.
They do not include project names, branch names, paths, commands, output, or endpoint URLs.

## 12. Failure, Recovery, Compatibility, and Rollback

- Missing local workspace: project remains visible and marked missing; sessions remain readable;
  relink repairs the binding without changing project/session identity.
- Remote endpoint unavailable: draft/send is blocked with a safe retry action; no local fallback.
- Branch changed externally: refresh the observed revision and show the new state; never overwrite
  the repository to restore a stale UI selection.
- App crash during project creation: atomic transaction leaves either both Project/WorkspaceBinding
  or neither.
- App restart: project expansion, order, active project, draft scope, and accepted bindings recover
  independently; transient popovers do not persist.
- Mutation failure: retain the previous server projection, announce a safe inline/menu error, and
  allow retry with a new idempotency key. A stale reorder refetches before another move.
- Reveal/worktree unsupported: keep the menu available, disable the action with a reason, and do not
  fall back to shell execution or an agent tool call.
- Remove/archive recovery: archived projects and conversations remain queryable and restorable;
  rollback never recreates files or rewrites session bindings.
- Removing the currently selected idle project navigates to the next visible project/session, then
  Personal/new-session fallback. An active or non-terminal project is rejected before navigation.
- Window action failure: leave current bounds unchanged and expose a safe diagnostic; never reload
  the WebView or duplicate the sidecar.
- Compatibility is additive for Manager APIs and persistence. ADK and `/v1/haas/*` remain
  unchanged. Legacy `workspace` continues in session responses during one release migration,
  then retires only after all GUI/readback/packaged gates use workspace binding ids.
- Rollback keeps the additive tables/fields. Older builds ignore them and continue reading legacy
  workspace/session data; no destructive down migration runs.

## 13. Component Impact Analysis

| Component | Impact |
|---|---|
| Manager Project Workbench | Extends the authoritative store/API with pin, rename, reorder, archive, restore, reveal/worktree capabilities and progressive GUI surfaces |
| Manager Conversation Experience | Reuses TurnWork/ActivityInspector; adds project/workspace/endpoint draft context and Codex-style command disclosure acceptance |
| Manager HaaS Sidecar Backend | Exposes configured endpoint list; freezes endpoint/workspace binding; remote never falls back local |
| Manager Product Identity | Extends packaged native window behavior and acceptance |
| Manager Delegation | Uses existing endpoint/session binding; no mount-policy weakening |
| Stores | Adds the project `pinned` field, order revision, sidebar-order preferences, and additive migration; existing bindings remain unchanged |
| Security Boundary | Reuses SecretRef, endpoint URL validation, workspace trust, and path redaction |
| HaaS Protocol / ADK | No impact; Manager-local APIs only |
| Adapter / Event Log / Model Proxy / MCP / Artifacts / Containers | No protocol or behavior change |

## 14. Test Plan and Acceptance

| ID | Priority | Acceptance case |
|---|---|---|
| MPW-001 | P0 | Two local Git worktrees with one common dir produce one project and two workspace bindings |
| MPW-002 | P0 | Plain folders with different canonical paths remain different projects |
| MPW-003 | P0 | Legacy sessions migrate idempotently, explicit binding wins over derivation, and workspace-less sessions render once under Personal |
| MPW-004 | P0 | Sidebar groups sessions by project, bounds each expanded group by its peek limit, and streaming one turn causes zero unrelated project-header commits |
| MPW-005 | P0 | Create dialog selects a local folder, validates it, and atomically creates Project + WorkspaceBinding |
| MPW-006 | P0 | Duplicate canonical folder opens the existing project without duplicate records |
| MPW-007 | P0 | Remote project/draft selects only a configured endpoint and endpoint-scoped workspace ref; readiness/capability is checked before send |
| MPW-008 | P0 | Remote endpoint failure blocks send and never silently uses local-managed HaaS |
| MPW-009 | P0 | First accepted command freezes project/workspace/endpoint snapshot; later defaults do not rewrite it |
| MPW-010 | P0 | Target change after accepted work creates a new session and preserves the old transcript/binding |
| MPW-011 | P0 | Git trigger shows branch/detached state and dirty count; non-Git workspace hides the trigger |
| MPW-012 | P1 | Safe branch search/switch/create handles stale revisions, dirty overwrite, conflicts, and other-worktree ownership |
| MPW-013 | P0 | A long command stays one ellipsized collapsed row at 390/760/1440 px, omits the redundant visible category, and expands exactly one inline Shell panel containing the complete redacted command |
| MPW-014 | P0 | Inline evidence replaces duplicate preview, keeps safe fallback on 404/410, and returns focus on collapse |
| MPW-015 | P0 | Light/dark at 390/760/1440 px matches tokens, contains popovers, and never covers Composer |
| MPW-016 | P0 | Packaged macOS drag works from sidebar/title/empty topbar and never from controls or selectable transcript |
| MPW-017 | P0 | Packaged macOS double-click toggles maximize/restore once and preserves one WebView/sidecar/session |
| MPW-018 | P0 | Expanded/collapsed sidebar retains one native titlebar centerline before/after maximize and restore |
| MPW-019 | P0 | Close-to-tray, Dock reopen, single-instance activation, restored bounds, and multi-display clamp pass |
| MPW-020 | P0 | Secret scan and negative tests find no credential, remote token, raw prompt/tool args, or unapproved host path in API/log/UI evidence |
| MPW-021 | P0 | Duplicate project display names remain distinct by canonical workspace/endpoint context and never collide in persistence or React keys |
| MPW-022 | P0 | Project and conversation hover/focus cards show the specified safe summary after 300 ms, keep project shortcuts reachable, dismiss on all defined boundaries, and never change selection/expansion |
| MPW-023 | P0 | Project/conversation rows reserve stable trailing actions; Edit/`...` and Pin/Archive appear on hover/focus without layout shift or row activation, and every action has a keyboard path |
| MPW-024 | P0 | Project-section `+` is a plain plus and only opens Create Project; the adjacent section `...` owns project/conversation sort and archived-project management |
| MPW-025 | P0 | Pin, rename, manual move, archive conversations, and remove/restore persist across restart; stale-order guards reject stale lists and a concurrent turn cannot enter between the busy check and archive/remove commit |
| MPW-026 | P0 | Remove from sidebar is non-destructive: files, workspaces, sessions, transcripts, artifacts, and accepted bindings remain unchanged |
| MPW-027 | P1 | Reveal in Finder and persistent worktree actions are capability-scoped, path-safe, idempotent, and disabled with a reason for remote/browser/missing/busy projects |
| MPW-028 | P0 | Light/dark at 320/390/760/1440 px keeps hover cards and both menus within the window, outside Composer, with zero unrelated project-header commits during streaming |
| MPW-029 | P0 | Concurrent sidebar-order and unrelated settings writes preserve both values; an interrupted write leaves the previous valid preferences document readable |
| MPW-030 | P0 | At 390/760/1440 px in light and dark themes, computed project and conversation labels are 12 px with line-height 1.35, active project weight is at most 500, age metadata is 11 px with tabular numerals, and every row keeps a minimum 28 px hit target without clipping CJK or long Latin titles |
| MPW-031 | P0 | Rapid pointer movement across project and conversation anchors in mixed expanded/collapsed groups renders at most one hover card; old content disappears before the next dwell, menus suppress hover, and leaving the current anchor/card closes it within the corridor delay |
| MPW-032 | P0 | Hover/focus/menu transitions preserve every visible project/conversation row bounding box and expansion state; section labels are 11 px/500, inactive conversations are 12 px/400 secondary text, and only the selected conversation rises to 12 px/500 primary text |
| MPW-033 | P0 | With sessions resolving before a delayed project projection, initial navigation renders one fixed project-loading skeleton and zero legacy conversation rows; after resolution it atomically shows the project hierarchy without an intermediate list flash, and later refreshes preserve the settled hierarchy |
| MPW-034 | P0 | In light/dark at 320/390/760/1440 px and 200% zoom, Project/Work location/Git branch render as one neutral shelf attached behind the Composer: equal 30 px centerline, borderless idle controls, device glyph for Local, no wrap/overflow, truthful menu semantics, stable geometry through hover/focus/open, and no canvas gap between shelf and Composer |
| MPW-035 | P0 | Clicking the project-row or project-hover-card pencil creates exactly one fresh conversation bound to that Project and never opens Edit Project or toggles expansion; the Project `...` menu remains the only Edit project entry, with correct accessible names and keyboard paths |
| MPW-036 | P0 | Every full-page Manager surface—Settings, AI Assistants, Inbox, Automations list/detail, Connectors, Audit, and persona detail—provides the same native drag affordance as the conversation surface. Empty/title regions start native drag and double-click maximize/restore exactly once; buttons, links, inputs, menus, selectable content, and scrollbars remain no-drag. Browser overlay checks plus packaged macOS movement/maximize cover every surface family |
| MPW-037 | P0 | Every collapsed TurnWork activity row, regardless of command/read/search/file/tool kind, uses one non-wrapping visual line with icon/status/disclosure fixed at the edges and the primary safe summary ellipsized by available width. Category metadata does not create a second line. Clicking expands one inline detail owner containing the complete safe summary/command and bounded evidence; mixed kinds preserve row height and alignment in both themes at 390/760/1440 px |
| MPW-038 | P0 | Selecting a project changes only active styling, expansion, and conversation context. It never changes project row order. Pinned projects remain first; Manual/Recent/Name ordering remains stable before and after selecting each project, including equal-key tie cases |
| MPW-039 | P0 | Selecting any conversation changes only active styling and conversation context and preserves the DOM order of every conversation in its project. Pinned remains the only priority tier; Recent/Oldest/Name and deterministic tie-breakers are unchanged by selection or liveness decoration. The order is identical before click, after click, after reload, and while another row reports working unless its selected ordering field itself changes |
| MPW-040 | P0 | A project expanded explicitly by the user remains expanded after the first or any later conversation selection inside it. The project disclosure `aria-expanded`, row geometry and sibling project expansion remain stable; search-only expansion does not mutate the stored choice |

Required evidence:

```bash
cd manager/surfaces/gui
npm test -- --run
npm run build
npm run e2e -- \
  e2e/project-workbench.spec.ts \
  e2e/haas-activity.spec.ts \
  e2e/session-shell.spec.ts

cd ../../../
PYTHONPATH=manager manager/.venv/bin/pytest \
  manager/tests/test_project_workbench.py \
  manager/tests/test_project_api.py \
  manager/tests/test_haas_endpoint.py \
  manager/tests/test_haas_delegation.py -q
make gui-preview-smoke
make pre-commit
make full-check
```

Packaged acceptance additionally requires DMG build/smoke plus a real macOS walkthrough for drag,
double-click maximize/restore, sidebar toggle geometry, local folder picker, and an isolated
local/remote endpoint fixture. The project-navigation walkthrough also covers Finder reveal,
persistent-worktree confirmation, focus return, and coarse-pointer fallback. A browser screenshot
cannot substitute for native window evidence. New project mutation/ordering modules target at least
90% line coverage, while path validation and capability guards target at least 95%.

## 15. SDD/TDD Implementation Slices

| Order | Slice | First failing evidence | Exit condition |
|---|---|---|---|
| 1 | Project store and migration | common-dir worktrees, duplicate path, partial transaction, legacy session fixtures | one Project authority; additive normalized session projection |
| 2 | Project APIs and sidebar | API contract tests; flat-list browser fixture | project-grouped virtual sidebar with create/edit entry points |
| 3 | Create dialog and draft binding | folder cancel/duplicate/invalid/remote-unavailable fixtures | atomic local/remote project creation and durable draft scope |
| 4 | Git and work location | non-Git/detached/dirty/worktree/endpoint failure fixtures | safe context bar, read snapshot, guarded mutation, frozen acceptance |
| 5 | Command disclosure bugfix | reference-shaped chronological command fixture | compact list plus one inline Shell owner, no duplicate output/side inspector |
| 6 | Native window lifecycle | Tauri command contract tests plus packaged failing walkthrough | drag/double-click/maximize/reopen/bounds behavior passes on macOS |
| 7 | Store and Manager API delta | migration, patch/reorder/archive idempotency, stale revision, protected Personal, and non-destructive fixtures | additive persisted fields and structured mutation contracts |
| 8 | Shared overlay and row interaction | fake-timer hover/focus, corridor, Escape/scroll/unmount, event-propagation, keyboard, and render-count fixtures | one collision-aware overlay owner and stable row geometry |
| 9 | Project and section menus | pin/edit/move/archive/remove/restore, sort preferences, capability-disabled states, and dialog reuse fixtures | complete reference-shaped menus without duplicate forms or renderers |
| 10 | Native capabilities | Finder reveal and persistent-worktree guard tests plus packaged smoke | path-safe capability actions with no shell/tool fallback |
| 11 | Sidebar typography density | computed-style assertions fail on oversized/overweight labels and undersized hit targets | 12 px navigation role, <=500 active weight, 11 px age metadata, and >=28 px rows pass in both themes and all supported widths |
| 12 | Exclusive hover and stable trailing geometry | rapid mixed-row hover plus before/after bounding boxes expose duplicate cards and width changes | one discriminated hover state and grid-stacked trailing content preserve one card and stable rows |
| 13 | Initial project projection | delayed project response with eager sessions exposes legacy-list flash | explicit unresolved/empty states and a fixed skeleton make first paint atomic |
| 14 | Composer context shelf parity | browser geometry and computed-style assertions expose the detached transparent row, weak default hierarchy, code-bracket Local icon, and missing menu-state semantics | one token-driven attached shelf passes both themes, supported widths, keyboard/menu geometry, production preview, and packaged-native visual comparison |
| 15 | Project shortcut action ownership | row/card pencil currently opens duplicate Edit Project surfaces | one `onNewProjectSession` intent creates a project-bound draft; Edit Project remains only in `...`; component and browser tests prove no cross-action |
| 16 | Review and release gates | code-review, brooks-review, brooks-test manifests | no unresolved finding; full-check, preview, DMG and native evidence pass |
| 17 | Full-page native drag parity | Settings and other route surfaces lack the conversation topbar drag owner | shared route-title drag regions cover every full-page family while interactive descendants remain no-drag; packaged movement/maximize passes |
| 18 | Uniform collapsed activity rows | non-command activities wrap and render category subtitles while commands ellipsize | one row shell applies nowrap/ellipsis/fixed trailing slots to every activity kind; inline detail remains the only complete-content owner |

P0/P1 is implementation order, not permission to silently drop scope. Any unimplemented acceptance
case remains explicitly open and prevents this change id from being declared complete.
