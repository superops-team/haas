# Manager Product Identity Specification

**English** | [简体中文](README.zh-CN.md)

Status: Draft
Last reviewed: 2026-09-15
Change ID: manager-openharness-no-login, manager-macos-dock-reopen, manager-macos-one-command-install
Related specs: [Manager Delegation](../manager-delegation/README.md), [Security Boundary](../security-boundary/README.md)

## 1. Component Role

Manager Product Identity defines the downstream manager application's public
identity when distributed with HaaS. The product name is **OpenHarness**. It is a
local-first desktop harness manager and does not require a cloud account or login
to use.

## 2. Scope

In scope:

- Replace visible product naming from OpenWorker to OpenHarness in the desktop
  shell, GUI chrome, startup text, settings copy, and local documentation used by
  this repository.
- Provide a dedicated OpenHarness logo and app icon family for desktop and GUI
  surfaces.
- Remove cloud account sign-in, sign-out, account row, cloud telemetry, cloud
  gallery, and managed one-click connector login entry points from the user
  interface.
- Disable cloud-auth HTTP endpoints so the local sidecar does not initiate or
  complete external login flows.
- Keep local manual provider keys, local connector credentials, local MCP OAuth,
  HaaS delegation settings, and local folder permissions working without login.
- Default fresh installations to the HaaS `local_managed` execution backend with
  sidecar autostart enabled. This local-first default MUST NOT introduce a cloud
  login or silently fall back to direct local execution when HaaS is unavailable.
- Provide a repository-owned, one-command macOS installer for public GitHub
  releases. The installer downloads the release DMG and its SHA-256 sidecar,
  installs OpenHarness into `/Applications`, and removes the quarantine attribute
  from the installed app so unsigned development releases remain launchable.

Out of scope:

- Renaming internal Python packages, database/state directories, and console
  scripts in the same change. Existing `coworker` package names and
  `openworker-server` entrypoints may remain as compatibility implementation
  details until a separate migration.
- Removing all historical test fixtures, mockups, or comments that are not
  user-facing runtime behavior.

## 3. Product Rules

- User-facing surfaces MUST say `OpenHarness`, not `OpenWorker`.
- The application MUST NOT render a sign-in button, signed-in account row, cloud
  account badge, or cloud telemetry control.
- Features that previously required OpenWorker Cloud MUST either be hidden,
  marked unavailable without a login call, or presented through manual/local
  credential setup only.
- Server routes that start or complete cloud login MUST return a stable
  disabled response and MUST NOT open a browser or contact Auth0/OpenWorker
  Cloud.
- Secrets remain local in the existing manager SecretStore; no product identity
  change may move credentials to a cloud service.

## 4. macOS One-Command Installation Contract

- The public copy-and-run command MUST fetch a versioned `scripts/install.sh`
  from this repository over HTTPS. `VERSION=vX.Y.Z` MAY select a release; without
  it, the installer resolves GitHub's latest non-draft, non-prerelease release.
- The installer supports only macOS and MUST reject an architecture for which the
  release has no documented asset. The v0.2.1 release supports Apple Silicon
  (`arm64`/`aarch64`) only and MUST fail clearly on Intel macOS.
- The DMG and `<dmg>.sha256` MUST be fetched from the same immutable GitHub release.
  Installation MUST stop before mounting or replacing the app when download or
  SHA-256 verification fails. Redirects are allowed only as part of GitHub's HTTPS
  release-download flow. The installer MUST validate and compare only a 64-hex
  digest from the checksum file; it MUST NOT trust a filename or path carried by
  that file.
- The installer MUST mount the verified DMG in a private temporary directory and
  require exactly one `OpenHarness.app` at its root. Temporary files and mounts
  MUST be cleaned up on success, error, or interruption.
- The destination is `/Applications/OpenHarness.app`. An existing installation
  MUST be moved to a same-filesystem backup before replacement; a failure after
  that point MUST restore the backup. The backup is deleted only after the new
  app has been copied and post-install checks have passed.
- Privilege escalation MAY be requested explicitly through `sudo` when the caller
  cannot write `/Applications`; the installer MUST NOT silently choose a different
  destination. Backup, replacement, quarantine removal, and rollback MUST use the
  same privilege path so a partial privileged install remains recoverable.
- After copying, the installer MUST run
  `/usr/bin/xattr -dr com.apple.quarantine /Applications/OpenHarness.app`. This is
  an explicit distribution exception for the unsigned build; it MUST be limited to
  the installed OpenHarness bundle and MUST NOT modify a broader directory.
- The installer MUST print the resolved release, download/checksum status, mount,
  backup/replacement, quarantine removal, and final app path. Errors MUST identify
  the failed stage and exit non-zero. It MUST NOT launch the app automatically.

## 5. Acceptance

- Desktop app window, tray title, package metadata, boot text, sidebar wordmark,
  and settings text use OpenHarness.
- The first desktop launch builds the main window hidden and explicitly shows and focuses it
  exactly once, only after the WebView reports its initial page load as finished. Later reloads
  or navigations MUST NOT resurface a window that the user has closed to the tray. This prevents a
  visible blank first frame while still guaranteeing a visible, interactive window.
  Close-to-tray may hide it only after the user closes that visible window; a healthy sidecar
  process with zero visible windows or a white window with an uncommitted WebView frame does
  not satisfy startup acceptance.
- On macOS, clicking the Dock icon for an already-running OpenHarness process MUST restore,
  unminimize, show, and focus the existing main window when no application window is visible.
  This native reopen action MUST reuse the existing WebView and sidecar rather than creating a
  second window, process, session, or backend. It MUST NOT steal focus when a window is already
  visible. Tray Open and single-instance relaunch MUST preserve the same restore behavior.
- The desktop development server MUST ignore `src-tauri/target/**`; Rust builds and bundled
  sidecar resources written there are not frontend sources and MUST NOT reload the WebView.
- A macOS native lifecycle test closes the main window to the tray, activates OpenHarness from
  the Dock, and asserts that the same process returns to one visible frontmost window.
- GUI tests assert that the sidebar no longer shows cloud sign-in controls.
- API tests assert that `/v1/cloud/login`, `/auth/callback`, and cloud logout are
  disabled/no-login flows.
- The existing HaaS delegation settings flow still works after branding changes.
- A fresh no-login installation starts the managed local HaaS sidecar and routes an
  eligible new chat through HaaS without requiring trigger keywords; explicit local
  execution remains a visible per-session opt-out.
- Offline contract tests cover platform/architecture rejection, immutable release
  asset selection, checksum-before-mount ordering, scoped `xattr`, cleanup, and
  rollback. A release smoke test installs the uploaded DMG through the public
  command and confirms `/Applications/OpenHarness.app` exists without a quarantine
  attribute.
