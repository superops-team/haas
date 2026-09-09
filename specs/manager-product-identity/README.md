# Manager Product Identity Specification

**English** | [简体中文](README.zh-CN.md)

Status: Draft
Last reviewed: 2026-09-09
Change ID: manager-openharness-no-login
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

## 4. Acceptance

- Desktop app window, tray title, package metadata, boot text, sidebar wordmark,
  and settings text use OpenHarness.
- GUI tests assert that the sidebar no longer shows cloud sign-in controls.
- API tests assert that `/v1/cloud/login`, `/auth/callback`, and cloud logout are
  disabled/no-login flows.
- The existing HaaS delegation settings flow still works after branding changes.
