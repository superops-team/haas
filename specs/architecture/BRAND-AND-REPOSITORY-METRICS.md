# Brand and Repository Metrics Specification

**English** | [简体中文](BRAND-AND-REPOSITORY-METRICS.zh-CN.md)

Status: Draft
Last reviewed: 2026-09-04
Change ID: `readme-brand-and-repository-metrics`

## 1. Background

The landing page explains HaaS accurately but lacks a recognizable product mark
and a compact, trustworthy project-health summary. This change introduces the
approved A1 “Protocol Bridge” identity and repository-owned metric badges. It
does not change runtime or protocol behavior.

## 2. Goals and Non-goals

Goals:

- Provide a simple HaaS mark that works as a README logo, square icon, and
  monochrome small-size symbol.
- Center both README mastheads while keeping English as the default language.
- Show build status, commit count, source lines, and test coverage from
  reproducible repository data.
- Keep English and Chinese pages structurally equivalent.

Non-goals:

- No HaaS API, event, runtime, storage, adapter, policy, or container changes.
- No claims about downloads, stars, production availability, or benchmarks.
- No hosted analytics service, third-party numeric badge service, or README JS.
- Generated HTML/GIF, specifications, lock files, and vendored dependencies do
  not count as source lines.

## 3. A1 Brand System

The logo is a geometric `H` named **Protocol Bridge**. Its horizontal bridge
represents the stable Google ADK 2.0 REST + SSE surface; its two vertical rails
represent isolation between the HaaS control plane and harness runtimes. Cyan,
emerald, and violet reuse the existing Archify semantic palette. The mark must
remain recognizable in monochrome and at 32 px.

Committed assets:

| Asset | Purpose |
|---|---|
| `docs/brand/haas-logo.svg` | Horizontal README logo and wordmark |
| `docs/brand/haas-mark.svg` | Square icon / social-avatar source |
| `docs/brand/haas-mark-monochrome.svg` | One-color and print fallback |

SVGs must have intrinsic dimensions and `viewBox` and contain no scripts,
external resources, embedded raster images, remote fonts, tracking, or local
machine metadata. The horizontal wordmark uses deterministic geometry rather
than an installed font.

## 4. README Masthead

Both READMEs start with a centered GitHub-compatible HTML masthead containing:

1. The shared horizontal logo.
2. A language-specific one-line product promise.
3. Build, commits, lines, and coverage badges.
4. The English / Simplified Chinese switch.

Every image has meaningful alt text. If dynamic badges are unavailable, the
README, logo, navigation, and architecture content remain usable.

## 5. Metric Definitions

| Metric | Source | Definition |
|---|---|---|
| Build | GitHub Actions | Repository metrics workflow result on `main` |
| Commits | Git | `git rev-list --count HEAD` from a full-history `main` checkout |
| Lines | Git + generator | Non-blank physical lines in tracked source files under `haas/`, `tests/`, and `scripts/` |
| Coverage | coverage.py | `totals.percent_covered` from `coverage json` after the coverage suite succeeds |

The line counter uses an explicit allowlist for the Python, shell, and JavaScript
source extensions used in these directories. It excludes untracked files,
caches, generated media, and documents. Coverage is evidence, not an estimate;
the badge rounds the JSON value to one decimal place.

## 6. Publication Model

`.github/workflows/repository-metrics.yml` runs on every push to `main`, a daily
schedule, and `workflow_dispatch`. Every push is included because the commit
count changes even when source lines do not. It checks out full history, installs the
locked development environment with `uv`, runs coverage, invokes the repository
metric generator, and publishes only generated SVG badges to a dedicated
`metrics` branch.

The workflow has only `contents: write` permission and uses concurrency control.
It must never force-push `main`, rewrite user history, execute fork-provided code
with write credentials, or publish a partial result. The `metrics` branch is an
output channel, not a source branch. README numeric badges load from that branch;
the Build badge links to the workflow run list.

Because a workflow cannot update its own status badge after completion, Build is
GitHub's native workflow-status badge from the default branch. The three numeric
SVGs—commits, lines, and coverage—are the only files published to `metrics`.
Publication copies the previously generated output into a fresh temporary Git
worktree, replaces all three SVGs as one staged set, creates a normal commit, and
pushes it without force. A push race fails safely and is resolved by the next
serialized or scheduled run.

## 7. Generator and Failure Contract

A script under `scripts/quality/` collects commit and line counts, accepts
coverage JSON, validates all values, and writes deterministic accessible SVGs to
an explicit output directory.

- Reject missing, negative, non-finite, or out-of-range coverage values.
- Escape all SVG text; never interpolate untrusted markup.
- Use fixed dimensions, colors, labels, and `<title>` elements.
- Stage all output in a temporary directory and publish only after every badge
  succeeds.
- Identical inputs produce byte-identical output; SVGs contain no timestamps.
- Source-line collection uses `git ls-files -z -- haas tests scripts` and the
  allowlist `.py`, `.sh`, `.js`, `.mjs`, `.cjs`; it counts lines containing at
  least one non-whitespace character.
- A shallow checkout, failed coverage command, missing JSON, or invalid metric is
  a hard failure. Prior published badges remain untouched.
- Logs contain aggregate values only—not prompts, payloads, credentials,
  authorization values, local paths, or complete tool arguments.
- Publication uses the workflow token and GitHub Actions identity; no personal
  token is introduced.

## 8. Component Impact

Architecture/documentation gains a brand and landing-page contract; repository
automation gains one metrics workflow and deterministic generator. Protocol,
sessions, events, adapters, proxies, MCP, skills, policy, artifacts,
observability, OpenSandbox, and the `linux/amd64` contract are unaffected. This
is an additive documentation surface with no API or persisted-schema change.

## 9. Test Plan and Acceptance

1. Validate SVG XML, dimensions, `viewBox`, forbidden elements, external
   references, and visual legibility at native and 32 px sizes.
2. Test line counting with tracked source plus ignored/generated fixtures.
3. Test numeric validation, deterministic output, escaping, and atomic failure.
4. Confirm coverage matches `coverage.json` and commits match full-history Git.
5. Validate workflow YAML, least privilege, and concurrency.
6. Check both README mastheads, image/link targets, language switching, and
   graceful badge failure.
7. Run two-round code review, architecture review, test-quality review,
   `git diff --check`, and `make pre-commit`.

Acceptance requires valid color/monochrome A1 assets, centered equivalent
bilingual mastheads, four evidence-linked badges, correct metric definitions,
atomic publication, and zero blocking review findings. Runtime, Docker,
OpenSandbox, and provider E2E are `not_run` because runtime behavior is unchanged.

## 10. Task Breakdown

1. Create and validate the three A1 SVG assets.
2. Implement and test the metric collector/renderer.
3. Add the least-privilege publication workflow.
4. Update both README mastheads.
5. Generate and inspect badges through the real workflow.
6. Run reviews and repository gates.

## 11. Rollback

Revert the README masthead and workflow commit to stop consuming or publishing
metrics. The independent `metrics` branch can then be deleted without changing
`main` or any runtime artifact. Existing README content below the masthead is
preserved throughout the change.
