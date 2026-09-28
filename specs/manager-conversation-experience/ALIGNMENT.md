# ZCode Conversation Alignment Delivery Contract

**English** | [简体中文](ALIGNMENT.zh-CN.md)

Change ID: `manager-conversation-interaction-v2`. Scope: the HaaS Manager chat region and the
session/command paths necessary to make it reliable. Parent: [component spec](README.md).
Design authority: [DESIGN.md](../../DESIGN.md). Reference checkout:
ZCode `29628c9acdb81b703bbd4080c207a0e7ce5e276e`, reviewed 2026-09-27.

Status: implementation, automated gates, production-browser scenarios, package smoke, and local
visible-window launch passed. ZCode's isolated empty workbench was observed from the pinned
revision. A live ZCode task was not accepted as comparison evidence because this host has Node 22
instead of the required Node 24 and no usable isolated provider; source-level invariants and HaaS
synthetic task fixtures remain the evidence for active-task parity.

## Product objective and boundary

The user must be able to start, guide through supported input paths, control, read, recover, and
review a task with the same clarity and continuity as ZCode. Delivery covers visual consistency,
interaction, rendering performance, and ordinary operation; a CSS-only refresh is insufficient.
Existing MCX requirements remain in scope. P0/P1/P2 orders work rather than dropping later items.

Preserve HaaS identity, protocol, safety, and configured-harness capabilities. This project does not
add ZCode-specific workflow engines or invent backend capabilities to mimic a button. Semantic
equivalence is required for supported common chat scenarios, not pixel-identical branding.

## Baseline and comparison protocol

Compare the same synthetic content, history length, tool count, state, language, viewport, theme,
and reduced-motion setting on the same host. Record reference revision, build mode, test scope,
and timestamps with evidence. Source inspection alone cannot establish visual or runtime parity.

| Scenario | Required outcome | Acceptance |
|---|---|---|
| Empty/edit/send/accepted/rejected/unknown | Stable input geometry, intact rejected/newer draft, no duplicate turn | MCX-001–004/015/016/028 |
| Live answer with multiple tools | First text visible promptly; one product turn; no response relocation or competing indicators | MCX-012/029–035/039 |
| Pause/continue/stop and queued follow-up | Clear control semantics; independent queue and task pause; one accepted execution | MCX-005–008/031 |
| Approval/input decision | One dock, preserved decision on reconnect, keyboard resolution/focus return | MCX-011/014/015 |
| Tool details and artifacts | Safe command readable; lazy evidence failure explained; result reachable without losing transcript | MCX-017/023/025/033 |
| Reconnect/restart/delete/session switch | Correct identity and scope; no lost input, replayed effects, or resurrected deleted draft | MCX-003/004/009/010/013 |
| Find/scroll/select in long history | Search visible streaming content and virtual history; preserve selection and reading anchor | MCX-020/027/030/038 |
| Both themes, narrow layouts, EN/ZH | Same hierarchy; no overlap, clipped required actions, or inaccessible text/focus | MCX-014–019/024/037 |

For operation parity, record action count and whether focus/reading context survives. Common paths
must not require extra detours to another page for send, stop, decision resolution, or tool preview.
For design quality, review paired state captures against DESIGN's hierarchy, alignment, density,
motion, and disclosure rules. Do not equate a screenshot-diff pass with design quality.

Performance retains MCX-020/021/022/030/038: <=200 historical mounted rows for 10,000 rows;
zero unrelated commits across 30 live publications after reset; <=1 publication/frame with final
flush; stable first-delta response owner; <=2 CSS px anchor movement in the specified scenario.
Measure same-host production builds for input/first-delta paint and scrolling. Report the median
and p95 of repeated matched runs; diagnose any slower HaaS result instead of asserting parity from
different fixtures or hardware. If ZCode cannot run, report comparison as missing evidence and
retain the project as incomplete while working on independently measurable acceptance cases.

## Ordered vertical slices

Each slice follows spec review → failing behavior test → implementation → focused regression →
required broader gates. Update the owning component contract before a new interface/state change.

| Slice | Work and ownership | Red-state evidence / exit condition |
|---|---|---|
| A0 Design foundation | DESIGN, GUI instructions, shared tokens and quiet run controls | Existing control/contrast tests + production state/geometry review |
| A1 Private durable storage | Command DB creation/reopen, journal sidecars; security and backend specs | Under umask 022, DB/WAL/SHM are 0600 before content; reopen repairs owned legacy mode without loss |
| A2 Admission/restart reconciliation | Command store, Manager runtime/receipt readback | Crash between accepted receipt and first checkpoint cannot leave permanent running or replay side effects |
| A3 Queue lifecycle | Store + Manager control handlers + FollowUpQueue | Paused Send now works explicitly; resume is idempotent and cannot unlock active dispatch; failed interruption leaves queue paused |
| A4 Input recovery | Queue edit receipts, client reconciliation, draft lifecycle | Lost edit reply restores exact input once; reconnect clears unknown acceptance correctly; deleting active session cannot re-save its draft |
| A5 Reading and evidence | Timeline/live search/selection, Inspector | Live Find works; virtualization preserves selection with bounded DOM; command/evidence and artifact flows remain coherent |
| A6 Visual and performance parity | Common fixtures, shared components, both themes | Matched reference review and MCX performance/keyboard/responsive gates pass |
| A7 Desktop acceptance | Current-worktree bundle, isolated local data, actual Tauri window | Native send/stream/control/decision/tool/recovery/readback and light/dark walkthrough, then full gates |

The known A1–A5 defects were identified by isolated probes or concrete caller/store traces in the
2026-09-27 review. None is accepted risk. Slice specifications must resolve ambiguous recovery
semantics before code. Do not fix uncertain execution by silently starting a second invocation.

## Testing, coverage, and evidence

- Unit tests cover reducers, receipt/queue transitions, draft transactions, and presentation
  selectors. Integration tests exercise Manager handlers with actual isolated stores and protocol
  shapes. Browser tests use the production bundle and real IndexedDB with synthetic transport.
- New core modules target >=90% coverage; credential/redaction/policy/path/log safety paths target
  >=95%. Report measured line and branch coverage and the actual module/test denominator. Existing
  unrelated coverage cannot substitute for changed-path tests; private-file failure branches need
  explicit tests. Do not infer GUI coverage from backend percentages.
- Each behavioral regression retains red and green results. Evidence records command, environment,
  input scope, counts, failures, corrective action, retest, and stage conclusion. It contains no
  real prompt, full tool arguments, credential, cookie, signed URL, or transcript.
- After implementation: two code-review rounds → brooks-review → brooks-test. Fix confirmed
  findings and rerun affected checks. `make pre-commit` and `make secret-scan` are mandatory;
  cross-component/high-risk and final delivery run `make full-check` and production GUI smoke.
- Native validation uses a bundle built from this worktree and an isolated test profile; distinguish
  actual native interaction from browser mocks and process/health-only smoke. Use synthetic tasks
  for explicit local harness/provider validation. Docker build is required only if container code
  changes. No registry push or external release is part of this request.

## Compatibility, migration, and completion

Public ADK and HaaS HTTP/SSE schemas, artifact authorization, adapter isolation, credential handling,
and Lite/AIO variants remain unchanged unless a later reviewed delta explicitly says otherwise.
Manager-private command/queue changes must be additive, restart-safe, and documented in the backend
spec. Existing command identities and accepted payloads survive migration. A rollback must not
start uncertain work or discard pending input; keep durable records readable and pause uncertain
dispatch. Never retain two active renderers or dual state writers as a rollback strategy.

The owning conversation spec defines UI acceptance; backend and security specs own touched
persistence/recovery boundaries; performance spec owns any changed rendering budget. Registry,
model proxy, MCP/skills, observability, and container specs need no changes while those contracts
remain untouched. Reassess this matrix at each slice.

Completion requires all applicable MCX cases, resolved review findings, measured coverage,
matched reference evidence, and a current-worktree native walkthrough. A running app, passing
unit suite, or published DESIGN alone is insufficient. Keep remaining work visible and continue
iterating; record unavailable external evidence honestly rather than marking it passed.
