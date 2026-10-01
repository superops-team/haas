# Manager HaaS Sidecar Backend Specification

**English** | [简体中文](README.zh-CN.md)

Status: Draft
Last reviewed: 2026-09-26
Change ID: manager-haas-sidecar-spec, unified-runtime-approval-policy, long-task-model-proxy-stability, haas-artifact-product-surface, manager-conversation-interaction-v2
Related specs: [HaaS Protocol](../haas-protocol/README.md), [Manager Delegation](../manager-delegation/README.md), [Manager Conversation Experience](../manager-conversation-experience/README.md), [Manager Project Workbench Experience](../manager-project-workspace-experience/README.md), [Harness Profile](../harness-profile/README.md), [Container Runtime](../container-runtime/README.md), [Config](../config/README.md), [Security Boundary](../security-boundary/README.md)

## 1. Component Role

OpenHarness defaults to embedded, non-containerized HaaS `local_managed` with autostart enabled. The packaged app launches the local HaaS sidecar and executes ordinary desktop turns through HaaS `/run_sse` (`execution_mode=local_api`). Remote HaaS and delegated container sessions are selectable product capabilities, but they are not the default desktop execution path. One `HaasClient` uses only HaaS HTTP/SSE in all HaaS-backed modes; Manager must not import HaaS service objects or call Codex/providers/MCP directly for HaaS-backed sessions.

This specification owns transport, binding, persistence, and normalized Manager facts. [Manager Conversation Experience](../manager-conversation-experience/README.md) owns their user-visible placement, interaction hierarchy, React component boundaries, and visual behavior; it cannot reinterpret the facts defined here.

```text
GUI -> Manager local API/session owner -> HaasClient -> HaaS control sidecar
    -> session runtime -> harness adapter -> workspace/model/MCP
```

### 1.1 Packaged Runtime

The desktop package includes the native Codex 0.152.1 executable alongside the HaaS sidecar. Packaging requires `COWORKER_CODEX_BIN` to identify the build input and rejects a missing executable or a version mismatch. The packaged supervisor resolves Codex from its own resources, never from a user-installed CLI or Node wrapper; a missing bundled executable prevents sidecar startup. Development launches may continue using the developer's PATH. Credentials and personal Codex configuration are not bundled or inherited.

Acceptance requires starting the packaged sidecar with a minimal system PATH and isolated HOME, discovering the bundled version, and executing a configured-provider turn through Manager and HaaS. Health/readiness alone does not prove model configuration, credential resolution or task execution. Verify package input rejection and supervisor resource resolution before rebuilding the app, then run the isolated packaged smoke. These requirements do not change ADK/HaaS fields or container images.
During `tauri dev`, the desktop shell MUST resolve the repository Manager virtual-environment server before any staged release sidecar under `src-tauri/binaries`; staged artifacts may be older than the source checkout. Release builds continue to resolve their bundled resource. `COWORKER_SERVER_BIN` remains the highest-priority explicit override in both modes.

Manager's assistant-text projection excludes ADK parts marked `thought=true`. Reasoning must not be concatenated into final assistant messages or persisted as their answer text; canonical HaaS events remain unchanged. Mixed thought/text fixtures and browser transcript checks verify this boundary for local and delegated paths.

### 1.2 Local Model Configuration Application

Curated models must use provider-advertised API identifiers, not display labels or identifiers copied from another provider. Volcengine standard Ark recommends `doubao-seed-2-1-turbo-260628`; its model suggestions and capability matrix use the same identifier. Existing explicit user selections are not silently rewritten; an unavailable model remains a failure and users can select the corrected model in the same chat. This catalog correction changes no ADK/HaaS fields or provider identity. Offline tests pin catalog consistency; opt-in packaged smoke verifies Responses acceptance against the configured provider.

Before a local API turn, Manager derives the provider from the session's selected model and its provider descriptor/SecretStore settings; providerId and name remain distinct and the vendor prefix is not sent as the model. It registers a session-scoped credential grant, synchronizes a validated active profile using HTTP, and pins the revision on first use. Changed configuration uses explicit non-delegated profile-rebind on the same session before submission; occupancy waits rather than triggering fallback. Unchanged intent reuses its profile. Concurrent sessions must not overwrite another session's applied route. The descriptor explicitly declares local HaaS Responses support (OpenAI, BytePlus Ark, Ark Agent Plan, and Volcengine Ark); an explicit api_type setting may select Responses for a custom endpoint. Other protocols are rejected, never inferred from an API key or URL.

Local profile synchronization is a recoverable two-phase operation. Before creating a profile or rebinding a session, Manager reads `GET /v1/haas/sessions/{sessionId}/profile` and reconciles a stale local checkpoint. A profile whose credential-free execution intent is unchanged MUST be reused only within that same session and current supervisor grant; a rotated opaque `credentialRef` is runtime material, not by itself a user configuration change. If a crash occurs after HaaS applies a rebind but before Manager persists it, the next send adopts the authoritative applied reference when it matches the requested execution intent, or performs one compare-and-swap rebind from that authoritative version when a fresh credential handle is required. Manager persists the synchronized profile checkpoint before capability discovery or `/run_sse`. It MUST NOT repeatedly create equivalent profiles, retry a stale expected version, share scoped credential handles across sessions, or bypass a genuine concurrent configuration change.

Local credential resolution uses an inherited Unix socketpair owned exclusively by the supervising Manager and its HaaS child. Only the descriptor number crosses the launch environment; it is removed before harness launch. Grants bind reference, harness, session, model and exact provider URL; sequenced requests reject replay and unknown grants. No filesystem credential endpoint or public credential API is added. A process not owned by this supervisor cannot acquire a grant. Missing credentials, unsupported API types and unavailable IPC fail closed with safe errors while settings remain usable.

Acceptance covers a controlled Responses server with real bundled Codex, two turns on the same session, a model change without a new session, concurrent configuration isolation, wrong-scope/replay IPC denial, SSE cancellation/timeout, provider failure readback, and negative secret assertions. Remote credential provisioning, container broker and MCP materialization are unchanged by this slice.

### 1.3 Runtime Interaction and Completion Integrity

The desktop experience MUST expose the execution facts already produced by the harness instead of reducing a turn to accumulated assistant text. For every accepted invocation, Manager concurrently consumes the ADK compatibility stream and the canonical HaaS invocation stream. The GUI receives typed reasoning/progress, tool lifecycle, approval/input requests, and the authoritative terminal outcome in causal order. Raw chain-of-thought, complete tool arguments/results, provider bodies, credentials, and host paths remain private; the visible reasoning surface is a bounded redacted progress summary.

The selected GUI mode and the effective HaaS capability MUST agree. `Ask for approval` is selectable for a HaaS-backed chat only when capability discovery reports `approval.mode=human_bridge` and structured input requests are available. Manager projects the mode into `policy.tools.approvalMode=on-request`; HaaS bridges Codex command/file approvals and `request_user_input` server requests without ending or replacing the turn. Until this gate passes, the UI MUST show the HaaS limitation and MUST NOT imply that interactive confirmation is active.

An invocation terminal and a user task terminal are different facts. `turn_end` reports the authoritative invocation result. Manager additionally owns a task phase (`running|waiting_for_input|waiting_for_approval|verifying|completed|incomplete|failed|cancelled`). A failed/incomplete/cancelled invocation, an unresolved blocking request, or an unfinished explicit plan MUST NOT be rendered or persisted as a completed task. Partial assistant text is retained and labelled as partial progress. Safe automatic continuation is bounded and uses the same logical session; it is forbidden while human input is required or when replay could duplicate an external side effect.

The GUI consumes a Manager-owned activity projection rather than rendering HaaS event
objects directly. Protocol events remain factual and presentation-neutral; Manager folds
their lifecycle into semantic work units, and the GUI decides their density and layout.
This boundary prevents adapter names, event keys, and safe metadata field names from
becoming user-visible copy while preserving the canonical events for replay and audit.

## 2. Sources and Rationale

Confirmed decisions: HaaS is the default execution backend without keyword routing; packaged local execution uses the embedded non-containerized HaaS local API by default; delegated sessions remain a separate containerized mode. Configuration updates replace the same delegated session's configuration when that mode is selected; busy sessions wait automatically; confirmed 24h idempotency expiry creates a new turn on next use; Lite is default for container images and Mac Apple Silicon uses Docker CLI with arm64 images. These are target contracts, not proof of current runtime support.

## 3. Upstream and Downstream Relationships

GUI owns user intent and approvals; Manager owns transcript, send queue, endpoint settings and durable HaaS bindings. HaaS owns accepted invocations, canonical events, runtime configuration application, policy and execution. Runtime containers are replaceable resources, not manager chat identities.

## 4. Responsibility Boundaries

- The local supervisor manages process lifecycle, not execution semantics.
- Manager supplies authorized configuration intent; HaaS validates and materializes it.
- Provider/MCP credentials remain in the manager secret store or operator vault and are provisioned only to the trusted resolver/broker described by Security Boundary §7.1, never to workers.
- No implicit local execution after a HaaS failure. `Run locally` is an explicit pre-first-turn opt-out or creates a separate local chat.
- P0 remote local-project execution remains unsupported until remote workspace transfer/sync/conflict contracts are implemented. Saving a remote endpoint does not make unsupported workspace modes usable.

## 5. Core Interfaces

### 5.1 Settings and Endpoint Identity

```toml
[execution]
backend = "haas"
[haas]
mode = "local_managed"
execution_mode = "local_api"
base_url = "http://127.0.0.1:8092"
token_ref = "secret://manager/haas/default"
connect_timeout_seconds = 5
response_header_timeout_seconds = 30
stream_idle_timeout_seconds = 90
turn_timeout_seconds = 86400
reconnect_max_attempts = 8
reconnect_backoff_initial_ms = 250
reconnect_backoff_max_ms = 10000
[haas.local_managed]
autostart = true
host = "127.0.0.1"
port = 8092
port_selection = "fixed"
data_dir = "<manager-state>/haas"
log_file = "<manager-state>/logs/haas-sidecar.log"
image_variant = "lite"
container_backend = "disabled"
[haas.remote]
base_url = "https://haas.example.com"
tls_verify = true
capability_probe = true
```

`mode` selects endpoint ownership, not protocol semantics. `execution_mode=local_api` runs through the embedded non-containerized HaaS sidecar and does not create delegated sessions or containers. `execution_mode=delegated_session` is the explicit containerized mode and then follows Manager Delegation. `local_managed` permits loopback URLs only. Lite architecture follows the Docker execution node when container mode is used; Mac arm64 does not require a Linux build machine or amd64 emulation. Remote URLs require HTTPS; an insecure-development override must be explicit and visible. Reject credentials embedded in URLs.

Manager persists an `endpointId` referencing an endpoint record with canonical URL, mode, server identity, TLS policy and token reference. A binding stores endpointId plus URL fingerprint; a hash alone cannot recover the address. Changing the global endpoint creates/selects a different record for new chats; retain old endpoint records while referenced. Token rotation updates the matching endpoint credential, never substitutes the new global endpoint's token into an old binding. Local auto-port changes require the authenticated launch handshake for that endpoint. Backend migration is not configuration replacement.

### 5.1.1 Local Bootstrap Handshake

1. Acquire the per-install OS lock before reading/writing token, port or log files. Use a user-owned 0700 state directory, reject symlinks, and retain the lock while supervising. A second instance attaches only after verifying the authenticated owned sidecar, or uses a separate state directory; never overwrite another instance's files.
2. Reuse the valid per-install token. On first install generate at least 256 random bits and atomically create the 0600 token file. Pass only its path in `HAAS_STATIC_TOKEN_FILE`. Missing/empty files fail closed; no development-token fallback.
3. `HAAS_STATIC_PRINCIPAL_JSON` is base64-encoded validated non-secret JSON mapped by Identity to `{principalId,tenantId,workspaceId,defaultUserId,allowedUserIds,roles}`. Default local user is `manager`; roles are user-owned launch configuration, never project/request input.
4. Fixed port binds the configured loopback port; auto binds port 0. Manager supplies a fresh non-secret `HAAS_LAUNCH_ID`. After socket bind, sidecar atomically writes `{port,pid,launchId}` to owner-only `HAAS_PORT_READY_FILE`. Accept only the expected launch id, live child and authenticated capability result. No assumed OS listening signal. Poll for at most 30 seconds; a stale file or unrelated listener does not prove ownership. For a credential-bearing local endpoint, `running` additionally means that the current Manager owns both the live child and its inherited credential channel. A healthy loopback listener without that ownership is never reusable and MUST NOT be reported as running. During a desktop development hot restart, the old child follows the old Manager's lifetime; the replacement Manager may wait a bounded interval for that verified predecessor to drain and then launch a fresh child/channel. If the listener remains or cannot be attributed safely, startup fails closed with `local_sidecar_not_owned` and does not terminate or attach to the unknown process.
5. Control readiness requires only config/identity/store. At that point capabilities may be read even with unavailable execution. If the registry is empty, scoped launch-only `HAAS_BOOTSTRAP_HARNESS_ID/BASE/NAME` seed the Codex identity once without an active profile. Manager creates, validates and activates the authorized initial profile. Repeated bootstrap preserves user configuration. Only work submission requires execution readiness.
6. Pass an explicit environment: safe process basics (`PATH`, isolated `HOME`/temporary paths, locale), plus Config-defined `HAAS_*` values from user-owned launch settings. Never inherit provider keys, cloud credentials, cookies or MCP tokens. Docker context/auth setup is validated separately, not copied into worker env.

Launch fields are owned by Config §5.2, not a private supervisor convention. Process/port/store/backend changes require a drained restart; provider/MCP/skill/AGENTS.md updates use `/policy` without restarting the host sidecar. Redacted logs rotate at 32 MiB with five files retained. Startup failure leaves settings usable and never runs the task locally.

### 5.2 HaasClient

One HTTP client handles both endpoint modes, structured envelopes/errors, separate timeout budgets, stable mutation keys and bounded replay. Required surfaces:

- health, ready, status, capabilities, list apps/models;
- harness list/get/create/update/delete;
- profile list/get/create/update/validate/activate;
- delegated create/get/restore/update policy;
- `/run_sse`, invocation GET, native invocation/session events, events-page;
- approval list/decision, structured input list/answer, pause/continue/cancel, session GET/PATCH/DELETE;
- file upload/list/download/archive when advertised available.

Every mutation except side-effect-free validate carries a persisted operation-scoped Idempotency-Key. `profile-rebind` is usable only for non-delegated clients; Manager never sends it for delegated chats. `RunSseStream` is established only after successful headers include valid `X-HaaS-Invocation-ID` and `X-HaaS-Session-ID`; persist acceptance before GUI-visible output. A structured pre-header integrity error with `accepted=true` also establishes accepted work and triggers readback, not replacement execution. Pause, Continue, and Stop remain distinct Manager intents. Pause waits for the source invocation's `haas.turn.interrupted` terminal before the GUI becomes paused. Continue creates a new HaaS invocation linked to that source, reattaches the normal dual-stream bridge, and persists the new acceptance headers before rendering output. Stop uses cancel and never exposes Continue afterward.

Manager's transport budgets are not execution budgets. `response_header_timeout_seconds`
guards pre-acceptance connect/header wait, `stream_idle_timeout_seconds` guards one idle
delivery gap before replay/readback recovery, and `turn_timeout_seconds` mirrors HaaS's
accepted invocation deadline. The default local-managed deadline is 24 hours and MUST NOT
be silently lowered for desktop turns. A timeout in an individual shell/tool command is a
tool failure; it may inform recovery, but it does not cancel the whole user task unless
the task deadline or an explicit Stop does.

### 5.3 Discovery and Routing

`GET /v1/haas/capabilities` is the only stable support discovery contract; validate its HaaS envelope and feature status/mode/enforcement. Manager exposes Pause only when the selected harness reports `pausing.status=available|degraded`; it MUST NOT infer Pause support from cancellation plus session continuation. Unknown values fail closed. Readiness answers current availability, not support. Match harness/profile fingerprints and image capabilities, without exposing native transports/ids or host paths.

Routing order: existing binding -> explicit pre-turn local choice -> saved backend preference -> input capability -> execution mode. For `local_api`, submit directly to HaaS `/run_sse` with the configured harness id/user/session and an explicit sandbox workspace root. For `delegated_session`, additionally apply persona eligibility, workspace authorization, workspace capability and delegated-session preparation gates. No trigger keywords or model intent classifier. Failed gates show the reason, never silently run locally.

P0 accepts authorized local `bind_mount` and text input. `snapshot_upload`/`remote_workspace` and human approval remain hidden/unselectable while unsupported. Human approval UI requires `human_bridge`, not Codex P0 `unattended_only`. Probe failures may be saved as unavailable configuration, not treated as successful execution setup.

### 5.4 Configuration Save and Use

Manager creates a forward profile revision using the canonical `ProviderRoute`, `McpServerConfig`, `SkillBundle`, `AgentsMdConfig` and policy schemas. Upload referenced content through `/v1/haas/files`; `contentRef` is its immutable file id, not a Manager-local artifact URI. Provider retains providerId and name; `wireApi=responses` requires Responses, while `openai-compatible` also specifies apiType. MCP URLs identify authorized upstreams, not the worker loopback relay.

For a bound delegated chat, submit the complete new profileRef through `/delegated-sessions/{id}/policy`; authorized mount/policy/image changes use the same endpoint. Manager Delegation §5.1.1 owns desiredRevision/appliedRevision and update results. Manager keeps a durable local send queue while configuration is pending, reads update state, and automatically submits once appliedRevision catches desiredRevision. A failed application preserves old applied state but does not run queued work under stale configuration. Correct/retry configuration without creating a new chat. Global endpoint changes remain separate and do not migrate bindings.

#### 5.4.1 User-Controlled Network Access

The Execution settings surface MUST expose labelled HaaS workspace and network-access controls
for fresh-session defaults. The composer permission picker is the sole approval control for the
current session; Settings MUST NOT expose a parallel approval selector. Fresh sessions default to
`workspace-write`, public network access enabled, and `on-request`. The settings read model returns effective values, save accepts only the closed
schema, and the UI explains that public network access increases exposure but still blocks
private/link-local/metadata/control-plane routes. Credentials, signed URLs and request content are
never inferred as network grants. Existing sessions show their applied revision rather than
pretending that a newly saved default changed running work.

For `local_api`, Manager projects the saved value when the first `/run_sse` creates a session as
`policy.network.defaultAction=allow|deny` (with an empty allow list), and HaaS materializes it as
revision 1. Later run bodies cannot mutate or widen that snapshot. Existing-session changes use
the revisioned session policy endpoint; an accepted invocation keeps its frozen policy while the
next invocation uses the verified applied revision. A missing value from an older installation migrates once to the new explicit default and
records the migration; it MUST NOT be reinterpreted differently on every read. The effective network state
is visible in Settings and is covered by a real task that distinguishes denied from allowed
egress.

For `delegated_session`, Manager stores the same intent in the complete delegated policy
snapshot and submits changes through the revisioned `/policy` barrier. A running invocation
keeps its applied revision; a new invocation waits until `appliedRevision >= desiredRevision`.
If the selected container variant cannot enforce and provide the requested egress, the update
or next invocation fails with `haas_policy_unsupported`; Manager MUST NOT display the switch
as effective and MUST NOT fall back to an unrestricted Docker network.

The composer permission mode and HaaS policy are one control, not parallel state. `Ask for
approval` maps to `on-request` and is the persisted fresh-session default. Capability discovery
controls whether the interactive approval UI is usable, not what `on-request` means: until live
`human_bridge` approval is available, the UI shows the default as unavailable and an action that
needs a grant fails/blocks visibly rather than being silently remapped to `never` or auto-approved.
`Bypass approvals` maps to `never` but
does not widen workspace or network policy: actions inside the current boundary run without a
prompt and actions requiring escalation fail visibly. `Always ask` may be exposed when the adapter
truthfully supports it. Changing the composer mode for an existing HaaS session calls the HaaS
session policy mutation with expected revision and a durable idempotency key. Workspace/network
values saved in Settings are fresh-session defaults; a future current-session editor must use the
same revisioned mutation. Running work keeps its applied
revision; the next send is queued until the desired revision applies. One approval card resolves
only its exact pending action and never changes the saved mode.

### 5.5 Interaction States

| State | Behavior / actions |
|-------|--------------------|
| sidecar_starting / capability_probing | Show safe progress; retry, inspect redacted log, edit settings |
| prepared | Candidate exists, no accepted work; cancel/retry preparation |
| configuration_waiting / configuration_applying | Current work continues; queued sends wait automatically |
| configuration_applied | Refresh fingerprints and submit queued send |
| configuration_failed | Show safe update failure; correct/retry; never use stale config silently |
| waiting_workspace_lock / restoring | Show bounded queue/restore progress; cancel queued send or wait |
| running | Cancel invocation, not session |
| reconnecting | Work continues; replay using original attempt |
| accepted_recovery_required | Read invocation and bounded history; no blind replacement turn |
| resumed_as_new_turn | Confirmed 24h replay expiry produced a new linked attempt on use; no confirmation dialog |
| session_expired | HaaS retention ended; local transcript remains; create a new session, not a retry of an old session id |
| waiting_for_approval | Show safe typed request only with human_bridge; explicit decision through HaaS |
| waiting_for_input | Show structured questions/options; resume the same invocation only after an explicit answer |
| verifying | Execution finished but declared acceptance checks or plan items remain |
| failed_retryable / failed_non_retryable | Keep partial text and safe error; distinguish transport replay from new work |
| incomplete | Keep partial progress, show the stable safe reason, and offer resume/retry according to replay safety; never present it as success |

`haas_terminal_integrity_error` is the stable Manager task code when invocation readback is terminal but the matching canonical terminal event is absent or inconsistent. Manager releases local busy state, persists task phase `incomplete`, suppresses stale approval/input cards, and permits only same-attempt readback/replay; it does not synthesize the missing terminal or submit replacement work.

The chat header always shows effective bound endpoint/mode, not the newly selected global default. Settings show Docker/credential/image readiness and supported features. Runtime internals, raw arguments and credentials never enter GUI payloads.

The Stop action is an execution mutation, not a local rendering hint. For a HaaS-backed turn,
Manager first records stop intent on the local engine and then, as soon as accepted headers
provide `(haasSessionId, invocationId)`, sends
`POST /v1/haas/sessions/{sid}/invocations/{id}/cancel` with
`Idempotency-Key: mgr-cancel:{invocationId}`. Stop may race acceptance; intent therefore lives
for the whole Manager turn and MUST be delivered exactly once after identity becomes known.
Repeated clicks/reconnects reuse the same key. Cancelling the local SSE consumer or closing the
WebSocket does not satisfy Stop.

Manager keeps consuming/replaying authoritative events after the cancel request and leaves the
task in `cancelling` until HaaS reports a terminal invocation. Codex's immediate
`turn/interrupt` response is only acknowledgement; `turn/completed(status=interrupted)` is
normalized by HaaS to `haas.turn.cancelled`, which is the success condition for Stop. If the
cancel request cannot be confirmed, Manager exposes a safe `cancel_failed`/recovery state and
readback action instead of fabricating `interrupted`. Partial reasoning, output and tool evidence
remain attached to the cancelled turn.

### 5.5.1 Acknowledged Conversation Commands and Follow-up Queue

The Manager conversation surface follows the product and React contracts in Manager Conversation
Experience. A GUI `user_message` carries a stable `clientCommandId`, operation-scoped idempotency
key, and explicit delivery intent. Manager persists an accepted or duplicate command receipt before
acknowledging `running` or `queued`; rejection returns a structured safe error without fabricating a
turn. An unknown post-send transport result is reconciled by receipt and session projection and is
never resolved by automatic resubmission.

The content-free Manager readback
`GET /v1/sessions/{sessionId}/conversation-commands/{idempotencyKey}` returns the original durable
receipt or `404 command_not_found`; it never returns the queued prompt or attachment payload.

Command persistence is private execution data. Before SQLite opens the store on POSIX, create
the owned database with mode 0600 and tighten any owned existing database, WAL, SHM, or rollback
journal to 0600. SQLite must inherit private database permissions when creating new sidecars;
chmod after schema/payload writes is insufficient. New store directories use 0700, without
changing unrelated existing parent-directory permissions or the process-wide umask. Reject
symlink, non-regular, multiply linked, or foreign-owned database/sidecar files before touching
their contents or permissions. Permission-preparation failure aborts store opening, not silent
fallback. Windows retains the existing managed-profile ACL boundary; POSIX mode tests do not
claim Windows ACL verification. A1 acceptance uses an isolated umask-022 subprocess, a live WAL
connection, reopen/migration with retained receipts, and permission/type failure cases.
This changes no public protocol or SQLite schema; rollback retains private file permissions.

An accepted immediate command is initially a durable `running` receipt with no command
checkpoint. After Manager persists the first `turn_start` carrying the same Manager turn id, it
marks that receipt checkpointed. At process startup, Manager reconciles every uncheckpointed
running receipt before serving clients. If the persisted transcript already contains the same
turn id (a crash between the two durable writes), Manager marks the receipt checkpointed and
keeps the canonical session recovery path. Otherwise it atomically converts the original command
and payload into one paused `restart_uncertain` follow-up item, clears its obsolete turn id, and
never starts it automatically. Receipt readback then reports `queued`; the snapshot exposes the
same queue identity and safe preview. The user may edit/delete it or explicitly resume the paused
queue, which is new authorization to attempt the work. Repeated startup reconciliation is
idempotent and cannot create a second item. This is a Manager-private additive schema migration;
it does not add or reinterpret an ADK or HaaS native event. Checkpointed HaaS invocations continue
through their existing same-invocation readback/replay path and are not converted to new work.

Manager owns the durable per-session follow-up queue. Queue mutations are revisioned and
idempotent; only queued items can be edited, deleted, reordered, or promoted to send-now.
`queue_move` carries `targetPosition`; send-now stop failure returns `queue_send_now_failed` while
preserving the queued item and paused drain policy. A
configuration barrier, workspace lock, running turn, or recovering binding may keep an item queued
without changing its user intent. HaaS deployment admission and workspace-lock queues remain
separate backend facts and are never projected as user follow-up items.

`queue_send_now` is one idempotent queue mutation. Its first accepted application moves the
selected queued item to the head and clears a pre-existing queue pause so an idle session can
claim it. If foreground work is active, Manager records the one-shot drain intent before sending
Stop. A failed/unconfirmed Stop consumes that intent, atomically pauses the queue with
`send_now_interrupt_failed`, and returns `queue_error` with the authoritative items and
`paused:true`; completion of the old turn cannot drain it. Reusing the mutation key never moves
the item or sends Stop a second time.

`queue_resume` is also a persisted idempotent mutation. It only changes the queue pause flag; it
never moves a `dispatching` item back to editable `queued` state. The first effective resume may
claim the next queued item when the session is idle. Reusing its key returns the original result
without another claim or execution. Within a session, a key reused across mutation kinds is rejected.
Every queue update/error carries the authoritative pause value so reconnect and failure cannot
leave the GUI assuming drain is enabled.

Before sending `queue_edit`, the GUI persists only its session id, queue item id, expected
revision, mutation idempotency key, and creation time. It does not duplicate prompt or attachment
content in the mutation store. `queue_restored` and definitive `queue_error` echo that key.
If delivery or the reply is lost, reconnect replays the same edit mutation; Manager returns the
persisted original result without requiring the removed queue row. The GUI deletes the pending
mutation only after receiving either correlated result. Thus an accepted payload is not lost just
because the first socket closed after the server commit.

The internal Manager conversation WebSocket snapshot carries `queue` plus content-free
`queuePaused`; later `queue_updated` events carry `items` and `paused`. `queue_resume` requires an
operation idempotency key and is the only action that restarts a paused drain. A claimed item stays
`dispatching` until its turn becomes terminal. If Manager restarts or cannot schedule after claim,
that item returns to `queued`, its revision advances, and the durable queue enters
`restart_uncertain`/`dispatch_uncertain` pause instead of replaying. Failure or ordinary Stop pauses
remaining items; successful completion drains normally, while explicit send-now may complete its
already-authorized interrupt-and-drain action.

`user_message.contextRefs` contains bounded typed display references (`skill`, `file`, `session`).
Manager validates and persists them as `_managerContext` for live/replayed GUI parity, and strips
that sidecar before every local provider request. It does not expose provider framing or silently
turn a display reference into model input.

The GUI receives one normalized conversation snapshot plus ordered changes with stable Manager row,
interaction, queue, and command identities correlated to canonical HaaS event, turn, invocation,
and tool identities. Local execution projects equivalent identities. UI components do not parse
HaaS or harness-native payloads and do not infer turn boundaries from adjacent display items.
The snapshot is product-turn-first: one turn owns one work projection and at most one assistant
response. Model-call identities and usage boundaries remain evidence correlation only; they never
create GUI rows, progress phases, cards, headings, or counts.

### 5.6 Streaming Bridge and Completion Barrier

Manager persists accepted headers, then opens native invocation replay from the beginning while continuing to drain the ADK stream. ADK text and native typed facts have independent durable cursors and consumption flags. Native events MUST be delivered live rather than fetched only after ADK closes. Deduplication is scoped by `(endpointId, full session key, invocationId, eventId, projection)`; an ADK projection cannot suppress processing of the native projection of the same record. Native session lifecycle events outside invocations are consumed with a separate session cursor, or reconstructed through delegated GET; invocation-only subscriptions cannot report idle configuration changes.

When native replay uses the session-scoped `events-page` endpoint, its transport cursor advances from the unfiltered page boundary, never from the last event matching the active invocation. `nextCursor` means that another retained page is immediately available. Independently, the last event id in every non-empty returned page becomes the durable `after_event_id` checkpoint for later polling, including when `nextCursor` is null. Invocation filtering happens only after that checkpoint has been captured. A page containing only historical or concurrent invocations therefore still advances; an empty filtered result is not evidence that the active invocation has no later events. Invocation-level deduplication remains in the scoped bridge claim set; Manager does not create a second competing pagination cursor.

Native type determines outcome, not stream closure. Receiving native terminal does not immediately finalize assistant text: wait until the ADK projection reaches the corresponding terminal id and all preceding text has been consumed, or reconcile the missing text from canonical event pages. Then persist exactly one assistant message and one Manager `turn_end`. Failed/cancelled/incomplete outcomes retain accumulated partial output with their exact status/code/safeReason. A delayed projection cannot overwrite terminal state or duplicate text.

The completion barrier is bounded by authoritative readback, not by permanent agreement between two transports. If ADK closes, stalls, or omits its terminal projection, Manager drains canonical pages through the terminal event and attempts to reconcile the invocation GET. Once every preceding canonical page has been consumed, the persisted canonical terminal is sufficient authoritative evidence to finish even when invocation GET is temporarily unavailable. When invocation readback is available, its terminal status and any exposed terminal event id MUST agree before completion. Manager then emits the matching error/task outcome and exactly one `turn_end`, and moves the local binding out of `running`; it MUST NOT wait forever for an ADK claim of the same event id. A terminal invocation readback without a canonical terminal is an explicit recoverable integrity error, never a perpetual running state.

Map non-thought ADK text to `assistant_delta`; `haas.output.reasoning.delta` to a typed `reasoning_delta`; `haas.usage.updated` to measured model-call usage; native tool facts to `tool_proposed/tool_started/tool_output_delta/tool_finished`; `haas.approval.required` to `permission_required`; `haas.input.required` to `question_requested`; delegated lifecycle to status; accepted headers to one `turn_start`; reconciled terminal to one `turn_end`. Heartbeat comments produce no transcript items. Preserve native sequence order; do not append final accumulated text as a second delta. Process events are checkpointed so reconnect reconstructs the same product turn, work segments, evidence correlation, usage, pending interaction, partial answer, and terminal status. Reasoning is never appended to commentary or final answer text. The first classified user-visible assistant delta creates the stable response row; later deltas, tool arrival, usage updates, and terminal sealing update that row without moving it between GUI containers.

Manager may retain correlated native facts in an internal `ModelCallEvidenceProjection` for replay,
usage accounting, and Inspector lookup. This is evidence data, not a GUI layout contract:

```json
{
  "modelCallId": "mcall_0002",
  "status": "running",
  "steps": [
    {"stepId": "item_msg_4", "kind": "commentary", "text": "Checking the event bridge"},
    {
      "stepId": "item_reason_5:0",
      "kind": "reasoning_summary",
      "previewText": "The native event already carries the fields",
      "previewFrozen": true,
      "text": "The native event already carries the fields and the full provider summary continues here"
    },
    {"stepId": "call_7", "kind": "tool", "activityId": "inv_abc:call_7"}
  ],
  "usage": {
    "inputTokens": 8100,
    "outputTokens": 746,
    "reasoningOutputTokens": 214,
    "cacheReadTokens": 3600,
    "totalTokens": 8846
  }
}
```

`kind` is `output_pending|commentary|reasoning_summary|tool|result`; `output_pending` is a transient evidence state and MUST NOT remain after an authoritative phase arrives. Steps retain canonical event order. Consecutive deltas merge only when their `itemId` (and reasoning `summaryIndex`) match; different items or model calls never merge in evidence. An agent-message delta without an authoritative phase starts as `output_pending` and is reclassified in place when the matching item lifecycle supplies `commentary|final_answer`; Manager never guesses from prose. When a legacy provider omits that item phase, a reconciled successful invocation terminal is authoritative for remaining user-visible output and seals the one assistant response. A failed, incomplete, or cancelled terminal MUST NOT use this successful-result fallback. Model-call usage and tool correlation remain measured evidence facts. Missing correlation uses an explicit unknown evidence bucket and missing usage is represented as unavailable, never zero. None of these boundaries may determine primary timeline grouping.

A reasoning-summary evidence item is identified by `(modelCallId, itemId, summaryIndex)`. Its `text`
retains the complete canonical provider-supplied summary for bounded Inspector detail, while
`previewText` is a sanitized projection capped at 240 Unicode characters. The canonical summary
remains subject to the normal safe-content boundary: it is not raw reasoning or hidden
chain-of-thought. Manager does not split summaries at punctuation or invent intermediate reasoning
steps. Replay and persistence reconstruct the same evidence. The product-turn projector may fold
one or more evidence items into one reasoning `WorkSegment`, but it MUST NOT use their prose as a
work title or expose model-call boundaries in the primary timeline.

Measured per-model-call `inputTokens`, `outputTokens`, optional `reasoningOutputTokens`, and cache
counters remain evidence/Inspector data. Cache-read is an input subset and reasoning-output is an
output subset; neither is added again to aggregate totals. Child commentary, reasoning-summary,
tool, and result facts never receive allocated or estimated token numbers. Tool execution itself
has no model token usage unless HaaS supplies a separately scoped measured record.
`cumulativeUsage` updates the task/session total but is not summed with model-call usage. Only an
authoritative aggregate may enter the quiet turn completion footer; missing or pending usage emits
no primary-timeline warning.

Before publishing GUI state, Manager folds those transport actions into an internal
`ActivityProjection`. This is not a HaaS public API and MUST NOT be sent back to HaaS:

```json
{
  "activityId": "inv_abc:call_7",
  "taskId": "mtask_abc",
  "invocationId": "inv_abc",
  "kind": "command",
  "status": "running",
  "title": "Run tests",
  "safeSummary": "Run the focused test suite",
  "commandPreview": "pytest tests/test_api.py -q",
  "workingDirectory": "workspace/",
  "outputPreview": "24 passed",
  "omittedLineCount": 0,
  "durationMs": 820,
  "exitCode": 0,
  "evidenceRef": "evd_opaque",
  "evidenceExpiresAtMs": 1789264000000,
  "recoveryGroupId": null,
  "facts": {
    "changedFileCount": null,
    "verificationStatus": null,
    "artifactIds": []
  }
}
```

`kind` is one of `progress|command|read|search|edit|tool|interaction|plan|recovery`;
`status` is one of `pending|running|waiting|succeeded|failed|cancelled`. HaaS
`activityKind=command|read|search|edit|tool`, when present, is authoritative for a tool
activity. Missing or unknown values become `tool`; Manager MUST NOT infer a kind by
parsing `toolName`, arguments, or human summaries. Titles use a bounded catalog plus
the protocol's safe facts. Unknown tools fall back to a localized generic tool title,
never `Used <native-name>` or serialized key/value arguments.
The first visible line MUST prioritize the most specific safe fact available: bounded
`commandPreview` for commands, a safe file/object summary for reads and edits, a query or
target summary for search, and the canonical safe tool summary for other tools. Generic
category copy such as `Run command` is a secondary label or fallback only; it MUST NOT be
the sole primary content when a more meaningful safe fact exists. Full arguments, complete
output, working directory, and structured diagnostics remain in the Inspector.
When a redacted `outputPreview` exists, the default activity surface also shows a compact
key-result excerpt below the action: at most two meaningful lines and 240 display characters.
Status plus available exit code/duration remain visible without opening the Inspector. The
excerpt is supporting evidence, never a replacement for the complete short-lived evidence or
an invitation to render an unbounded output line on the first screen.

All events for one `toolCallId` mutate one activity in place. A completed HaaS tool
maps to `succeeded`; only `haas.tool.failed`, cancellation, or an explicit non-zero
command result maps to a non-success state. Consecutive successful activities of the
same kind MAY collapse into a count summary only after they become terminal; the
selected activity and every running, waiting, failed, cancelled, or recovery activity
remain individually addressable. Failure and recovery share `recoveryGroupId` only
when linked by a persisted Manager retry/recovery action or invocation predecessor;
text similarity is never sufficient.

For a command activity, the list row shows the semantic action plus the bounded
`commandPreview` when available, so a user can identify the real operation without opening
the Inspector. The preview is derived by HaaS from the native command, masks credential
values, and is limited to one display line. Persistent previews still obey Event Log
redaction, so a signed URL can appear as unavailable in the row while remaining usable in
the transient detail. Manager carries `evidenceRef` and its expiry through lifecycle merging
but never resolves or persists the evidence body in the transcript. A terminal event cannot
erase an evidence reference, command preview or working-directory hint learned at start.

For explicitly selected Manager-local execution, the owned `run_shell` tool emits the actual
tool-call id and a `commandPreview` through the same GUI display fields. The preview uses the shared
HaaS bounded redactor (one line, at most 512 UTF-8 bytes); the GUI never derives it from raw arguments.
The desktop bundle includes this redactor. Standalone Manager installations without the optional
HaaS package must still start and execute tools; they omit command previews instead of inventing
an unsafe fallback or failing the turn. Verify this dependency-absence path with isolated imports.
Manager session readback adds equivalent `_managerDisplay` metadata to copied tool-call objects,
without mutating engine/provider messages or storing another transcript. Other tools stay generic
until their owned schema defines safe facts. Tests cover live/replay identity, command visibility,
credential masking, multi-line/oversized input, and malformed/non-command inputs. This is additive
Manager-local display metadata, not a change to ADK/HaaS events, execution policy, or evidence access.

`facts` is an additive, typed map assembled only from canonical plan, artifact, tool
terminal, and verification records. Manager MUST NOT derive a changed-file count, test
result, or risk state from assistant prose. Unknown facts remain null/absent rather than
being guessed. The collapsed result summary uses the same facts and task terminal state,
so live view, replay, automation history, and completion density cannot disagree.
Source event ids and dedupe keys remain in Manager's private checkpoint alongside the
projection; they are not fields of the GUI payload.

`turn_end.status` values `failed`, `incomplete`, and `cancelled` always produce a visible non-success notice with stable `code`, `safeReason`, and an explicitly computed recovery action. `turn_done` only closes Manager's local busy indicator; it MUST NOT imply success and MUST NOT finalize an automation run as successful unless task phase is `completed`.
An invocation terminal failure is a turn-level fact, not a synthetic tool result. It MUST be
rendered in the task failure notice and MUST NOT replace the selected tool's title, summary,
or safe reason. In the same projection transaction, every `running|pending|waiting` tool
belonging to that turn is closed as `failed`, or `cancelled` for a cancelled/interrupted
invocation. Its detail says that it ended without a tool terminal and links the separate turn
failure; it does not claim that the command caused an adapter, provider, or transport failure.
This reconciliation applies equally to live delivery, replay, and persisted transcript hydration.

### 5.7 Task Completion Controller

Manager derives task phase only from structured facts: invocation terminal status, pending approval/input records, explicit plan/work-item state, verification results, and assistant message phase when supplied by the harness. Human prose such as "done" is not authoritative. A successful invocation with unfinished plan items transitions to `verifying` or starts one bounded continuation turn; a provider/transport/security failure transitions to `incomplete` or `failed` and awaits recovery. The continuation budget is persisted per task and defaults to three turns; exhausting it produces a visible `incomplete` state instead of an infinite loop.

For legacy providers that omit assistant message phase, Manager may accept the final assistant message only when the invocation completed, no blocking interaction exists, and no explicit plan/work item remains pending. It MUST NOT fabricate completion after stream loss, missing terminal evidence, token failure, or tool failure required by an acceptance criterion.

Manager persists one task record per user send:

```json
{
  "taskId": "mtask_abc",
  "managerSessionId": "session_abc",
  "phase": "running",
  "currentInvocationId": "inv_abc",
  "continuationCount": 0,
  "continuationLimit": 3,
  "pendingPlanItemIds": [],
  "requiredVerificationIds": [],
  "lastTerminalStatus": null,
  "recoveryAction": null
}
```

Task transitions are persisted before publishing the matching GUI event. `waiting_for_input` and `waiting_for_approval` resume the current invocation; `verifying` or safe continuation creates a linked invocation in the same HaaS session; `incomplete|failed|cancelled` never auto-create work unless the recovery action is explicitly replay-safe. A new user message creates a new task record even when it reuses the same session.

### 5.8 Codex-Inspired Desktop Activity Experience

The transcript is the sole owner of each task's ordered activity stream. A right-side
Activity Inspector shows details for the selected activity; it is not a second timeline
and MUST NOT duplicate the complete activity list. The interaction follows the Codex
principle of separating mutable in-flight work from committed transcript history:

- while a task is active, one compact activity region shows bounded progress plus
  running/waiting activities, updating each lifecycle in place;
- reasoning is one bounded redacted disclosure per product turn, never chain-of-thought; it defaults
  collapsed and exposes canonical safe detail only through explicit user action;
- commentary/progress, reasoning summary, and tool action become typed work segments; none may
  displace or concatenate with the stable assistant response;
- per-model-call input/output/reasoning/cache usage is Inspector-only; authoritative aggregate turn
  usage may appear once in completion, while missing/pending usage is omitted rather than warned;
- each tool call is one semantic activity, not separate started/output/completed rows;
- approval and structured questions remain inline blocking cards and are the only place
  where the user decides or answers; the Inspector is read-only;
- success uses a quiet, non-color-only success indicator; red is reserved for actual
  failure; failure includes safe reason and, when available, exit code and duration;
- failure followed by an explicitly linked recovery is presented as one sequence without
  deleting the failed attempt;
- the activity list's primary line identifies the concrete safe action or target; generic
  category text is secondary, while the Inspector owns full command/output/diagnostic detail;
- turn-level provider/adapter/transport errors remain a separate failure notice and are never
  presented under a tool-detail heading as though the tool itself returned that error;
- partial output and recovery guidance remain distinct for non-success task outcomes.

When task phase becomes `completed`, the product turn keeps the final answer primary and changes its
single work summary to a quiet completed state. Successful work detail defaults collapsed. The
first actionable failure, skipped verification, unresolved risk, and non-success facts remain
reachable without opening model-call evidence. User disclosure is local presentation state and is
not reset by reasoning, model-call, usage, tool, or terminal updates.

Runtime projection MUST avoid a blank handoff between `Working` and the first assistant delta. A
single compact 16 px status slot may represent pre-answer work; the first user-visible delta mounts
the stable assistant-response row after the next coalesced publication and removes redundant
loading. The response row is never delayed by a word threshold and never moved into or out of an
activity container. The default work surface renders one safe summary and, only when immediately
relevant, the active tool or actionable failure. Model-call stages, reasoning chunks, and usage
arrival are not cards, headings, progress counts, or automatic disclosures.

Activity titles use an explicit localized product action, safe tool/object summary, bounded command
preview, then a neutral localized fallback. Raw/internal commentary, reasoning prose,
provider/model text, and model-call ordinals are prohibited fallbacks. Missing or pending usage is
omitted from ordinary conversation UI. Running state uses one persistent text label and at most one
motion owner; gradients, animated card borders/backgrounds, repeated spinners, and simultaneous
streaming motion are prohibited.

On desktop viewports at least 1100 CSS pixels wide, selecting an activity opens a
right-side Inspector sized `clamp(320px, 30vw, 400px)`. On narrower viewports the same
content opens as a non-modal bottom drawer inside the chat work area, above the composer,
using at most half of the available chat height. It MUST NOT cover the composer or an
active approval/input card. The Inspector/drawer presents the semantic title and status,
timestamps, duration, safe operation summary, exit code when applicable, up to five
persisted preview lines, omitted-line count, safe related artifacts, and a full-transcript
action. When an unexpired `evidenceRef` exists, opening the Inspector performs a scoped
on-demand fetch and adds the actual command, working directory and bounded command output.
Ordinary HTTP(S) URLs remain copyable and clickable. A signed user-authorization URL also
remains complete and clickable until evidence expiry; opening it uses `noopener,noreferrer`
and never routes it through analytics, telemetry or a Manager redirect log. Credential
values outside such an intact URL remain redacted. Codex 0.152.1 combined output is labeled
`Command output`; stdout/stderr sections appear only when the adapter supplied an
authoritative stream label.

The evidence response is display-only: `Cache-Control: no-store`, no client persistence, no
copy into a transcript item and no retry after expiry. A `410` replaces the detail body with
an explicit “Execution evidence expired” state while keeping persistent status, exit code
and safe preview visible. A `404` is treated as unavailable/unauthorized without revealing
object existence. The full-transcript action remains the complete retained **redacted**
transcript; large durable output is available only when HaaS deliberately published it as
an authorized artifact.

The side layout also falls back to the drawer whenever the Inspector would reduce the
transcript column below 520 CSS pixels, including when navigation or another product
surface is open. Layout selection therefore depends on available chat-work-area width,
not user-agent detection. Entering the completed collapsed state closes an Inspector for
an ordinary hidden success; selecting a retained failure/recovery fact keeps its details
available. Expanding `Show activity` restores prior selection only when that stable
activity id is still present.

Activity rows and controls are keyboard reachable. Enter or Space on a row selects it and
moves focus to the Inspector/drawer heading; pointer selection leaves keyboard focus on
the originating control. Escape closes either surface and returns focus to its source
row. The non-modal surface does not trap Tab. Status is conveyed by text/icon semantics as
well as color, live progress uses a polite live region, and new output does not steal
focus. Stable activity ids preserve selection across replay when the item still exists;
otherwise selection clears without opening a different activity.

Submitting a new foreground prompt explicitly starts a new transcript-follow epoch. After
React commits the local user message, the viewport MUST move to the latest content even if
the reader was previously inspecting older history. It MUST then follow height changes from
turn start, waiting state, reasoning/work evidence, tool activity and streamed answer text so
the user can immediately see that the accepted task is making progress. Only a new explicit
upward scroll after submission disengages that epoch; background/replayed updates MUST NOT
take over a reader-pinned viewport. Programmatic scrolling MUST occur after layout and MUST
not misclassify its own intermediate scroll events as user intent.
Opening, restoring, or switching to a persisted session starts a fresh viewport epoch as
well: once its historical transcript has rendered, the viewport MUST align to the latest
content by default. The previous session's scrolled-up position MUST NOT be inherited into
the newly opened session. After that initial alignment, the normal reader-pinned behavior
applies until the user switches sessions again or explicitly jumps to latest.

High-frequency GUI projection updates from assistant text deltas, reasoning deltas, and
work/evidence updates MUST be coalesced before publishing React state. A live render tick may
combine multiple transport frames but MUST preserve append order, terminal flush semantics,
and the canonical persisted transcript. Stream coalescing is a GUI back-pressure rule only; it
MUST NOT alter ADK/HaaS event ordering, response ids, task status, durable cursors, usage, or
recovery behavior. While the viewport is following the active foreground turn, layout follow-up
scrolls SHOULD use immediate post-layout alignment rather than repeated smooth animations; the
explicit user action to jump to latest MAY remain animated. This prevents token-level smooth
scroll animations from competing with touchpad/inertial scrolling and causing page-level shake.
All live-output motion, including stream cursors, waiting/activity spinners, thinking pulses,
and jump-to-latest scrolling, MUST honor `prefers-reduced-motion`: reduce by disabling
continuous animation and using instant scroll alignment. Decorative live-output glyphs MUST
be hidden from assistive technology so screen readers announce only task state and content.

### 5.9 HaaS Artifact Product Surface

When a Manager session is bound to HaaS, the Manager-owned
`/v1/sessions/{managerSessionId}/artifacts` surface remains the GUI contract. The GUI MUST NOT
call the HaaS sidecar directly, expose a HaaS bearer token, or learn the HaaS session id as a
routing primitive. Manager resolves the binding to `(endpointId, haasSessionId, principal scope)`
and proxies artifact operations through `HaasClient`.

The local non-HaaS path continues to scan the session scratch/workspace as before. The HaaS path
MUST call the authoritative HaaS artifact list endpoint and map each `FileRecord` into the
existing GUI artifact shape plus additive source fields:

```json
{
  "source": "haas",
  "id": "file_abc",
  "path": "output/reports/security-review.html",
  "name": "security-review.html",
  "kind": "html",
  "size": 24576,
  "modified_at": 1786400240,
  "preview_status": "available",
  "download_status": "available"
}
```

For P0, the default HaaS publish root is `output/`, matching Artifact Store §6.2. Manager must not
broaden the scan by walking the mounted project directory, recent modified files, `dist/`, `coverage/`,
or the user workspace root. If a future profile/session policy adds extra publish roots, Manager only
reflects what HaaS has already registered; it does not maintain an independent artifact-root policy.
The GUI should continue to use a separate changed-files or workspace-diff surface for source edits.

`path` is the HaaS `relativePath`; it is the only stable deep-link target for transcript links
such as `[Security review](artifact:output/reports/security-review.html)`. GUI matching MAY also
match the basename for old links only when exactly one artifact in the current list has that basename.
If multiple artifacts match, Manager must return an ambiguous-link state instead of opening one by
guess. The canonical link form is the relative path. `abs_path` is absent for remote/HaaS artifacts
unless the artifact is known to be a local-managed file that
the current Manager may reveal safely; copying a remote artifact's `relativePath` MUST NOT pretend
to be a host filesystem path. `kind` is derived from `mediaType` and filename extension using the
same viewer categories as local artifacts: markdown, html, image, pdf, sheet, office, code, text,
folder, or unknown.

`/v1/sessions/{managerSessionId}/artifacts/read?path=...` is also a Manager proxy for HaaS-bound
sessions. It resolves the current artifact by relative path, validates that it belongs to the
bound HaaS session and caller scope, and fetches content by `fileId` only when HaaS reports readable
content. HTML continues to render inside the existing sandboxed viewer with the same CSP rules.
Images, PDFs, sheets, markdown, code, and text use the existing viewer capabilities. If HaaS reports
`previewStatus=download_only`, the viewer shows file metadata and the primary action is Download/Open,
not an empty inline preview. If HaaS reports `previewStatus=unavailable` or content returns
`404 haas_file_not_found`, the viewer shows a stable unavailable state that explains that the
artifact was recorded but content is not currently readable from this runtime.
`/v1/sessions/{managerSessionId}/artifacts/download?path=...` is the corresponding Manager-owned
download proxy. It resolves the path through the same bound-session lookup, fetches by the returned
opaque `fileId`, and responds with attachment disposition plus `nosniff`. The GUI uses this endpoint
for readable remote artifacts and MUST NOT route remote artifacts through the local reveal/open API.

Manager converts readable HaaS bytes into the existing JSON `ArtifactContent` response instead of
returning the HaaS attachment directly to the browser:

```json
{
  "ok": true,
  "source": "haas",
  "path": "output/reports/security-review.html",
  "kind": "html",
  "content": "<!doctype html>...",
  "download_status": "available",
  "preview_status": "available"
}
```

Text-like artifacts return bounded UTF-8 `content` with `truncated` when needed. Image/PDF/sheet
artifacts return a bounded `data_url` only within the existing Manager preview-size limit. Office,
unknown, oversized, or explicitly download-only artifacts return `{ok:true, kind, download_status:
"available", preview_status:"download_only"}` plus metadata and no inline bytes. Unreadable
metadata-only artifacts return `{ok:false, code:"artifact_unavailable", preview_status:"unavailable",
download_status:"unavailable"}`. These JSON responses are Manager-local UI contracts, not HaaS
public API fields.

The same right-rail viewer also backs the Manager-local Files explorer. Files is not the artifact
surface: it browses session roots (workspace, scratch, and user-granted directories) and must not
require the selected path to be present in the HaaS artifact list. Manager therefore treats
`origin=files` on the local `/v1/sessions/{managerSessionId}/artifacts/read` and `/reveal` wrappers as
a roots-browser request. For HaaS-bound sessions, `origin=files` resolves only through the Manager
session roots and remains subject to path traversal, root membership, and local reveal/open rules.
The default origin remains `artifacts`, so transcript `artifact:` links and the Artifacts section keep
the stricter HaaS artifact-scope validation.

The artifact section in the right rail is universal for HaaS-backed sessions. It stays collapsed
by default but shows the count chip after the first successful list. After an accepted HaaS turn
reaches a terminal state, Manager refreshes the artifact list. If native artifact registration facts
arrive before terminal, Manager MAY publish an `artifacts_changed` GUI event to refresh earlier:

```json
{"type": "artifacts_changed", "session_id": "manager_session_1", "source": "haas"}
```

The list endpoint remains authoritative and the event carries no file content, no HaaS bearer token,
and no HaaS session id. A transcript `artifact:` chip must open the viewer on the first click even if
the right rail is hidden. If the artifact list is stale, the viewer refreshes once before falling back
to a metadata-only selection.

User-facing copy uses "Artifacts" / "产物" for session deliverables. Empty state: "No artifacts yet"
/ "还没有生成产物". Terminal count: "Generated N artifacts" / "生成了 N 个产物". A recorded but unreadable
artifact uses copy equivalent to: "This artifact was recorded, but preview is not available from this
runtime yet." The UI MUST NOT expose internal terms such as `FileRecord`, `artifactDelta`, `hsess_...`,
or `file_...` as primary labels, although opaque ids may remain in developer diagnostics.

Security constraints:

- Manager never forwards artifact content into transcript messages, logs, metrics, notifications,
  search indices, model context, or automation summaries.
- Remote artifact `reveal` does not shell out on the local host. It either downloads through the
  authenticated Manager proxy or opens a HaaS-provided safe URL when such a future contract exists.
- HTML preview keeps the null-origin sandbox and offline CSP used by the current local artifact
  viewer. Active content is never rendered from the HaaS API origin.
- Artifact list metadata must not include host paths, hidden runtime paths, raw prompt text, complete
  command output, signed URLs, Authorization/Cookie values, or provider credentials.

Acceptance:

1. A HaaS-bound Manager session with two HaaS `FileRecord`s returns two GUI artifacts through the
   existing Manager `/v1/sessions/{id}/artifacts` endpoint.
2. Clicking an `artifact:` chip for a HaaS relative path opens the viewer on the first click, refreshes
   stale metadata once, and never exposes the HaaS token or session id to the browser.
3. HTML artifacts still render in the existing sandbox; unavailable or metadata-only HaaS artifacts
   render an explicit unavailable/download-only state.
4. Terminal HaaS turns refresh artifact count and right-rail list without requiring a manual page reload.
5. Local non-HaaS artifact scanning behavior is unchanged.
6. Packaged acceptance creates a HaaS-bound Manager session and reads its workspace root through the
   Files surface with `origin=files`. The bundled-sidecar smoke must prove the origin returns a local
   folder/file view while omitting it remains artifact-scoped and unavailable. A launch of the newly
   built `.app` must additionally show that the packaged WebView sends `origin=files` and renders the
   workspace listing, preventing a fresh frontend build from being replaced by stale packaged assets.
7. Tests cover `HaasClient` list/download/archive methods, Manager route proxying, GUI mapping, chip
   behavior, security redaction, and remote reveal/download behavior.

The running indicator derives from task/invocation state, not WebSocket presence.
Reconnect first restores the persisted activity projection and pending interaction, then
resumes live cursors. Unknown event types remain hidden with a diagnostic counter; they
do not become assistant text, success, approval UI, or guessed activities.

Transcript projection changes during initial load, history restore, replay, or live event
reconciliation MUST NOT change the React hook order of an existing keyed turn. Routing
uses one product-turn renderer; source-specific state belongs to transport/projector adapters. A
turn may gain or lose model-call evidence or HaaS activity metadata without unmounting the turn,
assistant response, or transcript root, producing a blank window, or losing the remaining
conversation. A regression test MUST rerender the same turn identity across missing, partial, and
complete evidence without introducing a legacy renderer branch.

Compatibility is additive. ADK `/run` and `/run_sse` are unchanged. HaaS native tool events retain
their existing required fields and add only optional semantic facts. Output and usage events
likewise add optional correlation/scope facts. An older server or stored event therefore projects
one product turn with a neutral work summary, generic tool activities, and only the aggregate usage
it actually reported; it never creates a `legacy` stage card. Older consumers continue to ignore
added fields. No stored-event migration or session replacement is required; packaged Manager GUI
assets and the internal snapshot version change atomically.

Transport disconnect never cancels execution. Reconnect ADK with the same key and Last-Event-ID, native with its own after_event_id; bounded exponential backoff with jitter respects attempt/turn limits. Cursor expiry requires invocation/page readback and never by itself triggers new work. ADK Session 413 falls back to bounded native pages without silent history truncation. Slow/disconnected GUI clients do not block HaaS consumption; reconnect receives persisted transcript and reconciled state. The same vectors must produce equivalent Manager projections locally and remotely.

## 6. Durable Data and Idempotency

Bindings retain manager session id, endpointId/fingerprint, mode, delegatedSessionId, HaaS user/session/harness ids, first accepted invocation/time, applied configuration fingerprints and desired/applied revisions. PREPARED is not HAAS_BOUND; first durable acceptance commits HAAS_BOUND, including accepted failures. Backend changes do not demote it.

Turn recovery retains managerTurnId, attemptId, predecessorInvocationId, exact idempotency key or safe reference, effective session/invocation ids, server expiry, independent ADK/native/session cursors and state (`opening|accepted|streaming|reconnecting|terminal|recovery_required`). Persist before output; keep through transcript retention, including terminal records. Never persist tokens/raw configuration in the binding.

### 6.1 Operation Keys

| Operation | Stable namespace |
|-----------|------------------|
| Delegated create | mgr-delegated-create:{managerSessionId} |
| Restore | mgr-delegated-restore:{delegatedSessionId}:{generation} |
| Policy update | mgr-delegated-policy:{delegatedSessionId}:{policyChangeId} |
| Turn attempt | mgr-turn:{managerSessionId}:{turnSeq}:{attemptId} |
| Cancel | mgr-cancel:{invocationId} |
| Approval | mgr-approval:{approvalId}:{decisionId} |
| File upload | mgr-file-upload:{fileClientId} |
| Harness/profile mutation | mgr-config:{resourceId}:{operationId} |

All ids are generated once and persisted before requests, without secrets or host paths. Server deduplication also scopes keys by principal and operation.

### 6.2 24-Hour Replay Expiry

HaaS exposes `Idempotency-Expires-At` (epoch milliseconds) and Invocation.idempotencyExpiresAtMs. Nonterminal reservations are never evicted; lightweight key-hash/acceptance/expiry tombstones remain through session retention. Old execution keys return `410 haas_idempotency_expired`, not a silent new execution.

On next use/send/resume after confirmed expiry, automatically allocate one new turn attempt and fresh key, persist it and its predecessor before submit, omit old Last-Event-ID, and show resumed_as_new_turn. No confirmation dialog is required. Retries or Manager restart reuse the new attempt, not generate another. If prior work is still active, wait/reconcile first. A timer expiring or opening a completed chat is not permission for a background rerun. New work can repeat previous side effects; preserve visible history rather than representing it as a resumed native turn.

Retry of an accepted, non-terminal attempt is recovery, not a new submission. When the
attempt already has an `invocationId`, Manager MUST read back that exact invocation and
resume its canonical invocation-event stream from the persisted native cursor. It MUST NOT
re-sync a profile, rebuild a `/run_sse` body, or POST the original idempotency key again.
Those mutable inputs can drift after acceptance and would turn a safe retry into
`haas_idempotency_conflict`; allocating another key could duplicate tool side effects. A
terminal readback is reconciled through the normal terminal barrier. Only
server-confirmed idempotency expiry may enter the linked-attempt path.

404/401/network/store failures and cursor expiry are not idempotency expiry. Use server expiry/readback, not local key creation time. Control mutations do not inherit auto-new-turn semantics: reread current state, retain policy intent deduplication through session retention, and never change a resolved approval or replay stale configuration over a newer revision.

## 7. Lifecycle

The desktop execution state is `idle|running|pausing|paused|resuming|stopping`.
While running, Composer exposes separate Pause and Stop controls. While paused it
exposes Continue and Stop. Transitional states disable duplicate actions. The state
comes from persisted HaaS readback on reconnect. For a foreground send that has already
been handed to the session WebSocket, the GUI may immediately render `running` so Stop
is available during the pre-acceptance window; the first `turn_start`, `execution_control`,
`input_rejected`, `error`, `turn_done`, or socket close must clear that pending local
projection and restore the server-authoritative state. Reviewer pause is labelled
separately and never changes execution lifecycle.
The session list `liveness=working` is also an execution-state fallback for the currently
open session. If the session WebSocket reconnects with a stale `ready.running=false` or
does not replay process events quickly enough, the GUI must continue to render the
transcript and Composer in running mode from liveness until an explicit terminal event,
terminal readback, or refreshed session list clears it. The same effective running value
must feed Transcript, Composer, waiting-row, jump-to-latest, and side rail controls.
Manager's session WebSocket carries `pause`, `continue`, and `interrupt` intents. Its
`ready.data.execution_control` snapshot and subsequent `execution_control` events use
`controlState`, `supportsResume`, and `resumableInvocationId`; `pause` publishes
`pausing` immediately but publishes `paused` only after authoritative interrupted
readback. `continue` publishes `resuming`, persists the new accepted invocation before
its first output, then publishes `running`. Stop while paused calls the HaaS cancel
operation on `resumableInvocationId`, revokes resumability, and leaves the already
terminal source invocation unchanged. A successful cancel acknowledgement carrying authoritative
`sessionControl` MUST be persisted and published immediately, even when the interrupted source
stream has not yet exited its Manager `finally` block; an in-memory active-turn record MUST NOT
keep the desktop in `stopping` after that acknowledgement.
Continue follows the same authoritative readback rule: once `paused` with a valid
`resumableInvocationId` is persisted, a user Continue may claim a new linked invocation even
if the interrupted source stream has not yet reached its local `finally`/`turn_done`. The old
source stream MUST NOT overwrite the accepted linked invocation, and its delayed cleanup MUST
NOT clear the new active-turn state.
The WebSocket receive loop MUST NOT await the long-running Pause request inline: Pause waits
for authoritative terminal readback, while a following Stop must still be received and dispatched
concurrently so the stronger cancel intent can win. Control tasks remain session-scoped and do not
imply cancellation when the viewing socket disconnects.
If a control mutation is rejected before it takes effect, Manager immediately republishes
the last authoritative state (`running` for an active Pause/Stop, `paused` for Continue or
paused Stop) before reporting the safe error. `turn_done` is only a transport/rendering
boundary: it must not leave the desktop in a transitional state or manufacture `paused`
without authoritative readback.
Manager normalizes HaaS persisted terminal control states before sending them to the desktop:
`cancelled` and any other non-paused terminal state project as desktop `idle`, while task and
invocation outcome events retain the exact terminal status. The GUI never receives a lifecycle
state outside its six-value execution-state contract.

Start -> acquire supervisor lock -> initialize owned endpoint -> control ready/discovery -> initial profile -> execution ready. A fresh eligible chat creates PREPARED, restores runtime, submits a persisted attempt, then commits HAAS_BOUND at acceptance. Follow-up retains the binding, applies pending configuration, restores if necessary, and executes a new turn. Host exit drains only owned resources; shared attachment does not gain authority to stop another supervisor's sidecar.

## 8. Security and Permissions

Bearer auth except health/ready. Token references are endpoint-specific. Workspace-local configuration cannot choose backend/endpoint/image or widen grants. Remote configuration cannot request Manager host mounts. Private worker/broker credentials and bootstrap files never enter public read responses. Configuration content remains untrusted; schema, path, secret and policy checks precede application.

## 9. Observability

Safe manager events include backend_selected, local_sidecar_starting/ready/degraded, remote_probe_failed, binding_created/reused, materialization_requested and linked-attempt creation. Forward `X-HaaS-Trace-ID` as an optional bounded correlation header, never as authorization. Show desired/applied revisions and safe failure separately from invocation outcome.

Local-managed startup and recovery diagnostics are part of the contract, not
debug-only text. `local_status` exposes a stable `status`, `reason`, `url`,
`pid`, `managerLogPath` and `logPath` without returning tokens or credential
material. The common startup failures use stable reasons:
`local_autostart_requires_loopback`, `local_sidecar_not_owned`,
`local_sidecar_port_occupied`, `local_haas_config_write_failed`,
`bundled_codex_unavailable`, `local_haas_start_failed`, `local_haas_exited` and
`local_haas_starting`.
Unknown listeners on the configured loopback port are never adopted, killed, or
used as a fallback HaaS endpoint. Manager waits for a bounded drain window; if
the port remains occupied it fails closed with `local_sidecar_port_occupied`,
keeps the main sidecar and WebSocket usable, emits a structured task error plus
`turn_done`, and points operators at the local HaaS log path.

## 10. Failure and Recovery

Before acceptance, show not-started errors. After acceptance, preserve binding/partial output and reconcile exact terminal state. Configuration failures gate future turns, not the already-running invocation. Docker/image/secret failures never enable local fallback. Remote P0 workspace limitations are explicit. Session expiration and idempotency expiration are different recovery paths. An active invocation receiving `invalid_token` or `token_expired` is a model-proxy lifecycle failure with a stable safe code, never a generic successful stream close. Manager keeps the same binding and offers only replay-safe recovery.

When retrying or continuing an accepted non-terminal attempt, Manager first reads back the
existing invocation and native cursor. It MUST NOT rebuild a new `/run_sse` request with a
new profile solely because the local credential reference, model proxy port, or profile
version changed. If the HaaS session has the same provider scope, Manager refreshes the
session-scoped model proxy capability and rebinds the native Codex profile before the next
model call. If that refresh fails, the task remains failed/incomplete with the exact
`haas_model_proxy_token_invalid|expired` reason and partial transcript; it is not displayed
as a completed answer.

## 11. Test Plan and Acceptance

### 11.1 Observed Baseline and Release Blockers (2026-09-12)

A packaged local session (`execution_mode=local_api`, Codex 0.152.1) ran for 93.6 seconds and produced 11 native reasoning items, 24 function calls and 24 function results. The HaaS event store contained 1,007 text deltas, zero typed reasoning events, zero typed tool events, eight unparsed events, and two terminal events for the same invocation. The request crossed Manager's 90-second SSE idle timeout without periodic HaaS heartbeat; closing the response caused Session Runtime cleanup to revoke the invocation capability while Codex was still active, and its next model call ended with `invalid_token`. HaaS recorded `incomplete`; Manager persisted only accumulated interim assistant text. A blocking `request_user_input` call returned unavailable and execution continued without an actual user answer.

This evidence makes the following release blockers, not optional polish:

1. Preserve explicit normalized event types and enforce one terminal per invocation.
2. Deliver redacted reasoning/progress and complete tool lifecycle to Manager during the turn.
3. Show failed/incomplete/cancelled outcomes and never treat `turn_done` as success.
4. Keep or safely refresh the model capability for the entire accepted invocation.
5. Implement the approval/input bridge before exposing `Ask for approval` on a HaaS-backed chat.
6. Add task-level completion/verification/continuation state so an interim model message cannot close an unfinished task.

### 11.1.1 Observed Local Failure (2026-09-15)

The latest local session `fead0639-f8c` for workspace
`/Users/bytedance/workspace/bytedance/slide/why-mpa` exposed two additional
release blockers. The "install Quarto environment" invocation
`inv_cafe86d3d0624411` started at 22:30:04 and failed at 22:45:04 after exactly
the old 900-second deadline. The task was still actively attempting a slow
Quarto release download; several tool calls had been bounded by their own
timeouts, partial files grew from tens to hundreds of MiB, and a corrupted
resume attempt failed `tar xzf` with `gzip decompression failed`. HaaS then
emitted `haas.turn.failed` with `code=timeout`, so the user task stopped before
environment setup could complete.

Follow-up questions at 22:45:27 and 23:02:29 failed for a separate reason:
Codex resumed the same native thread but attempted a model call through an old
loopback model proxy bearer at `127.0.0.1:65060`, and HaaS returned
`haas_provider_error` with `haas_model_proxy_token_invalid`. This confirms that
invocation-scoped token revocation and port-sensitive native profile state are
not stable enough for long-running local-managed sessions. The fix is
session-scoped capability registration with exact-scope refresh/rebind, plus
replay/readback before any new submission.

### 11.2 Implementation Slices

| Order | Slice | Required failing test first | Exit condition |
|-------|-------|-----------------------------|----------------|
| 1 | Long-task deadline and token integrity | 24-hour controlled-clock turn; real long download crossing the old 900-second boundary; injected invalid/expired token; adapter terminal plus finalize | No 900-second failure; exact safe failure only at the 24-hour deadline or successful session-scope refresh; one terminal; partial output retained |
| 2 | Canonical process events | Real 0.152.1 reasoning/item start/output/completed fixtures | Stable redacted reasoning/tool types; no supported event is unparsed |
| 3 | Concurrent Manager bridge | ADK/native arrival permutations, live native events, disconnect/replay | GUI receives ordered process events during execution with independent cursors and no duplicates |
| 4 | Interactive bridge | Command/file approval and blocking input request over disconnect/reconnect | One durable request, one explicit response, same invocation resumes; capability becomes `human_bridge` only after all cases pass |
| 5 | Task completion controller | Partial result, failed required tool, pending plan, verification and continuation exhaustion | Task state is distinct from invocation state; only verified finished work becomes completed |
| 6 | Semantic activity projector | Tool lifecycle replay, missing/unknown activity kind, command exit and recovery linkage | One stable activity per tool call; explicit normalized states; no machine-field copy or heuristic kind inference |
| 7 | Transcript and Inspector UI | Running/completed/non-success states, keyboard navigation and responsive width matrix | Codex-inspired in-flight stream, completion summary, right Inspector/bottom drawer and secretless details match §5.8 |
| 8 | Ephemeral command evidence | Adapter capture, scoped memory store, authenticated read, expiry and GUI no-store rendering | Full useful command evidence while live; credentials masked; authorization URL usable but absent from every durable surface |
| 9 | Packaged acceptance | 15+ minute real-provider task with slow tool IO, tools, interaction, reconnect and verification | App remains running past the former 900-second cutoff, shows the whole lifecycle, completes the requested task when dependencies succeed, and passes secret/terminal/event-volume/UI checks |
| 10 | Terminal reconciliation recovery | More than one session event page, an ADK stream missing its terminal projection, a later canonical failure and reconnect with a stale running binding | The unfiltered page checkpoint advances, authoritative terminal readback closes the bridge, exactly one failure/`turn_end` is emitted, and reconnect presents `failed`/`idle` before `ready` rather than restoring `running` |

- Empty registry, missing Docker/provider, token/port races, stale ready file and two Manager instances; control APIs remain usable and owned files are not overwritten.
- Same contract vectors for local/remote endpoints, token rotation and reopening a bound chat after global endpoint changes.
- Save configuration during accepted/running/cancelling work, queue a send, restart Manager/HaaS, and verify exactly one send using verified applied revision in the same chat.
- Native terminal before ADK text, opposite projection arrival order, disconnect, cursor expiry, Session 413, idle config events and cross-session repeated event ids.
- Server-confirmed replay expiry creates one linked new attempt on use; no timer rerun; ordinary 404 never triggers it; stale policy/approval retries never undo newer state.
- Real Docker Lite arm64 on Mac, Lite amd64 and AIO amd64 first-turn/follow-up/TTL/cancel/readback gates; unsupported remote workspace and human approval remain unselectable.
- No provider/MCP secrets, raw tool arguments, host paths or native ids in GUI, bindings, logs and public events. Spec/schema checks alone do not prove runtime completion.
- A real packaged turn lasting longer than the 90-second stream-idle interval and
  the former 900-second runtime cutoff remains active, renders at least one
  reasoning/progress update and every tool start/terminal pair, and reaches exactly
  one authoritative terminal event. Injected `invalid_token`/`token_expired` is
  recovered through the session-scoped capability path when safe; otherwise it
  remains visible and cannot be rendered as success.
- Interactive local HaaS acceptance covers command approval, file-change approval, blocking `request_user_input`, disconnect/reconnect while waiting, approve/deny/cancel, and same-invocation continuation. The capability and mode selector remain disabled until this suite passes.
- Fresh-session defaults are visible and effective end to end: workspace write succeeds inside the
  authorized root, public egress succeeds, and a command requiring escalation produces one human
  action-scoped approval card. Private/metadata/control routes remain blocked, and resolving the
  card does not change the saved mode or authorize later actions.
- Change approval, network, and workspace settings while a turn runs; UI shows desired/applied
  revisions, current work stays frozen, the next send waits, and apply failure never falls back to
  stale policy or direct local execution.
- Task-completion acceptance covers a multi-step task with a failed tool, an unfinished plan, verification, bounded continuation, and final completion. `turn_done` alone never marks success; an exhausted budget or infrastructure failure leaves a visible resumable `incomplete` task.
- Backward replay covers stored/public tool events that predate optional activity fields: they remain ordinary `tool` activities, preserve canonical ordering/status, and never gain guessed command/read/search/edit semantics.
- Desktop screenshots at 1440/1100 CSS pixels and narrow screenshots at 1099/390 verify the Inspector/drawer breakpoint, completion collapse, no overlap, bounded text, focus return, and failure/recovery visibility.
- Command evidence tests cover start-to-terminal lifecycle merging, exact command/cwd, credential masking, ordinary and signed authorization links, expiry, 404 scope hiding, 410 expiry, no-store headers, combined-output labeling and absence from persisted transcript.

## stream-timeout-approval-recovery

ADK EOF or stream read failure is delivery evidence only. After acceptance, continue canonical polling for the same invocation while status is accepted/running/cancelling, with a bounded recovery deadline and non-busy polling interval. Empty pages do not imply terminal loss. Preserve attempt and cursors; never resubmit work. A terminal status without its canonical event remains an integrity error. Tests cover EOF/read failure, empty pages, delayed failure during approval, exactly one terminal projection and no second submission. Implementation order: deadline regression/fix, Manager recovery regression/fix, integration/smoke and review. All HTTP/SSE schemas and error codes remain unchanged; registry, profile, policy, credential, MCP, artifact and container contracts are unaffected.

A non-success terminal also closes unresolved HaaS approval cards in the current turn. Historical cards and non-HaaS approvals remain unchanged. Closing a card is a cancelled interaction, never an approval decision sent to the server. Regression tests must assert this projection so stale cards cannot issue post-timeout mutations.

## haas-context-recovery-and-recall

Manager transcript and Cowork memory are recovery sources for HaaS-backed chats, but
Manager MUST NOT infer continuation intent from keywords or rewrite a user's prompt with
hand-built historical context. The visible user message is submitted to HaaS as-is. Before
submitting an accepted HaaS turn, Manager still persists the visible user message locally so
a browser refresh, WebSocket disconnect, Manager restart, or HaaS timeout cannot leave only
the HaaS binding without the user's intent.

For model-facing recall, Manager injects a scoped built-in MCP source named
`manager-cowork-recall` into the active local HaaS profile. The source exposes a single
`recall` tool that returns bounded structured data from Cowork's database: global memories,
workspace memories, and recent redacted session transcript facts for the HaaS session
identified by `X-HaaS-Session-ID`. Codex decides when to call this tool based on the task,
so history recovery is not coupled to language-specific trigger words. The tool response
MUST exclude raw tool arguments, host paths, credentials, complete command output, and raw
prompts beyond the retained visible transcript. The MCP source is optional: if unavailable,
the turn may continue, but Manager records the degraded capability through the HaaS profile
and ordinary task outcome path. External arbitrary MCP materialization remains unsupported
for the local Codex path until the full MCP runtime contract is implemented.

The built-in source implements the stateless Streamable HTTP lifecycle needed by the pinned Codex
client. `initialize`, `tools/list`, and `tools/call` return JSON-RPC responses; the
`notifications/initialized` notification returns HTTP 202 with an empty body and MUST NOT be
converted into `-32601 Method not found`. Unknown requests with an `id` still return a structured
JSON-RPC error. Tests execute the complete initialize-notification-list-call sequence rather than
calling `tools/call` in isolation.
Because `recall` is Manager-owned, read-only, loopback-only, session-scoped, and independently
authenticated, its generated Codex MCP config sets `default_tools_approval_mode="approve"` so the
call cannot stall behind an unprojected generic MCP approval. No external MCP server inherits this
exception.

Retry and recovery recall MUST merge the persisted transcript with the latest HaaS
`stream_bridge` checkpoint before filtering. This covers the window where a failed or
interrupted HaaS turn has persisted its visible user message, terminal notice, and bridge
state, but the assistant projection has not yet been committed to the transcript because
the Manager process, browser connection, or stream loop ended early. The synthesized recall
row is still a transcript fact, not a new user prompt: it may include assistant text,
task outcome, reasoning summary, product work summaries, and bounded activity facts from
`_haas_activity`, but only from already-sanitized projection fields such as status,
safeSummary/summary, commandPreview, outputPreview/preview, exitCode, safeReason and
durationMs. Query filtering MUST search those safe fields as well as assistant text so an
agent retry can recall what was already attempted without repeating side effects blindly.

### Automation and desktop reliability (automation-desktop-reliability)

Background: scheduled runs bypass the common execution router and mistake stream EOF for
success; volatile scheduler claims and missing notifications obscure failures. Goals are
shared execution, truthful completion, recoverable interruption, bounded local resources,
and continuous readable desktop status. No new provider protocol or external message grant.

1. Scheduled runs use run_turn_events with the task's session, agent, workspace and selected
model. Broadcast canonical Manager projections and checkpoint through the existing store.
Success requires a successful terminal event and no error/interruption or incomplete task;
EOF alone fails closed. Persist run outcome before notification, release occupancy in finally.
2. Startup reconciles unfinished scheduled/manual runs before catchup: retain session identity,
mark unresolved outcomes as error/recovery-required, and disable the owning schedule so no
blind replay duplicates effects. Users inspect the original conversation and explicitly enable
future scheduling; this is not automatic resubmission or a claim of successful cancellation.
3. Scheduler admits at most four active runs by default; excess due work stays durable and
is admitted on later ticks. A configurable execution timeout defaults to the existing 24h
contract. Same-task overlap remains suppressed. No fabricated monetary/token budget.
4. Success/failure creates a durable Inbox notification with safe status and original session
link, then an app-wide automation_run_finished event. Existing session task_done remains.
Notification transport failure cannot overwrite execution status; no raw result/error bodies
in notifications. No unrequested external notification is sent.
5. Desktop detects its owned Manager child exiting and attempts bounded respawn with backoff,
reusing launch arguments and auth identity. The child retains credential-channel ownership
rules. Quit cancels supervision; unknown listeners are never killed. Exhausted recovery stays
visible with restart guidance; no execution replay is implied by process restart.
6. Narrow conversation layouts overlay panels within the available area, with an accessible
close path and usable composer. Active task status remains visible throughout waiting,
reasoning, tools and finalization; never show Waiting alongside active work or a visible tool.

Compatibility: additive Manager events, retained run statuses and session identities. HaaS
ADK/native schemas, container images, provider proxy, MCP, policies and credentials unchanged.
Stores add recovery operations without removing historical records. Tests: error/EOF/terminal
classification, common route invocation, restart no-duplicate, concurrency, cancellation,
notification failure, native supervisor lifecycle, browser narrow widths and state transitions.
Tasks: implement tested automation slice, desktop/UI slice, full gates, two code-review rounds,
Brooks architecture and test reviews, then re-audit outstanding findings. Backup/migration,
retention controls, virtualization and release-channel trust require separate component designs;
track their feasibility and residual gaps rather than inventing unsafe deletion or signing keys.

Second-pass corrections: consuming a scheduled occurrence is persisted before executing its
side effects; queued candidates are re-read to respect disable/delete. Manual automation
records finalize at the server turn boundary, not solely via the browser callback. HaaS
approval/input events create a durable attention link to the original conversation; they
never auto-approve. Inbox writes use fsync plus atomic replacement and rollback failed in-memory
inserts. Result notification identity is scoped to run ID, separate from attention requests.
Desktop WebSocket reconnect is bounded and does not resend user messages. Startup failures
show recovery guidance even before the normal conversation shell mounts.

Native result notifications accept only ok/error and use fixed text; OS delivery denial leaves Inbox as fallback. Scheduled HaaS interactions currently require approval in the original conversation: legacy name/target grants are not translated into broader HaaS permissions. Recovery deliberately freezes unknown runs rather than claiming seamless execution resume. Backup/restore, automatic retention, 10k transcript windowing and trusted release manifest/key migration remain unimplemented product work, tracked in Beads.

### Manager conversation display identities

The Manager transcript may carry `_managerTurnId` and `_managerRowId` sidecars. Manager-local
WebSocket envelopes expose the same identities as `turnId` and `rowId`; these are additive internal
display metadata and never change ADK/HaaS invocation identity. The provider outbound encoder
strips both sidecars. Older history is normalized once at the GUI persistence boundary using
user/connector intent boundaries, with no model-stage or tool-adjacency inference in rendering.

### Project workbench endpoint binding

The project workbench consumes the existing endpoint registry and freezes `endpointId`, URL
fingerprint, workspace binding, harness/profile/policy revisions, and remote workspace reference
at first acceptance. Remote selection never serializes a Manager local path or host mount. A failed
remote endpoint remains a structured blocked target and MUST NOT fall back to `local_managed`.
Changing project defaults affects drafts/new sessions only; accepted sessions retain their binding.

## Desktop Restart and Reinstall Task Recovery (`manager-restart-task-recovery-v1`)

### Background and product boundary

The desktop currently terminates its owned Manager child immediately on exit. After a restart or
local reinstall, HaaS can reconcile a persisted local `running` invocation that no longer has a
live adapter owner to `incomplete(sidecar_restart_execution_lost)`, but Manager only repairs
commands that never reached a transcript checkpoint. A checkpointed command can therefore remain
`accepted/running` while its HaaS invocation is terminal and no Codex app-server owns the work. The
session then renders a false running state and a Stop action that cannot stop anything.

This design has two distinct guarantees:

1. A desktop-owned update/restart is a graceful recovery path. Manager pauses each pause-capable
   HaaS invocation, waits for the authoritative `interrupted` terminal, and persists a
   restart-owned continuation marker before the desktop terminates its children. The next launch
   automatically continues the same logical task through HaaS native resume semantics.
2. A crash, force kill, power loss, or external replacement that did not complete the pause barrier
   is fail closed. Startup reconciles the exact accepted invocation and persisted events; it never
   resubmits the prompt or invents a new idempotency key. If live ownership or native continuation
   cannot be proven, the task becomes visible `incomplete` with a safe retry/continue action rather
   than remaining falsely running.

P0 goals are to reconcile every checkpointed `accepted/running` conversation command after the
owned HaaS endpoint is ready; reattach the same invocation when authoritative readback proves a
live owner; atomically converge terminal events, task outcome, binding control and command receipt;
gracefully pause and automatically continue pause-capable local HaaS work across desktop-driven
restart/update/reinstall; and expose Stop only for a currently bound active invocation owner.

Non-goals are automatic prompt replay after a crash or `sidecar_restart_execution_lost`, claiming
instruction-pointer continuation when a harness only supports durable session/thread continuation,
and recovering non-HaaS in-process providers in this slice. ADK, HaaS invocation/canonical-event,
container, model-proxy, MCP and credential schemas do not change.

### Durable state and startup reconciliation

Manager stores only additive secretless facts in the existing HaaS binding:

```json
{
  "restartRecovery": {
    "generation": "restart_<opaque>",
    "state": "preparing|paused|reattaching|continued|recovery_required",
    "sourceInvocationId": "inv_...",
    "requestedAtMs": 1786400000000,
    "reasonCode": "desktop_restart|sidecar_restart_execution_lost|backend_unavailable"
  }
}
```

The generation is one opaque local idempotency identity per desktop restart request, not a
credential. This object MUST NOT contain the prompt, complete tool arguments or output, bearer
token, provider credential, signed URL, or host path.

`conversation_commands` gains one nullable additive `execution_ref`. When the normal turn path
observes an accepted delegated `turn_start`, it checkpoints the Manager `turn_id` and binds the
latest exact HaaS `invocationId` in the same command-store transaction. A structured-plan
continuation within that same Manager turn may advance the reference only in observed acceptance
order; unrelated callers cannot replace it. Startup primarily joins receipt to bridge by this
reference. For
legacy rows without it, Manager may reconcile only when the session single-writer ordering proves
there is one current bridge and no newer accepted command; older running rows are then terminalized
as stale predecessors after the current authoritative terminal is persisted. Ambiguous legacy rows
become `recovery_required` rather than being guessed or replayed.

`running` is an ownership fact, not a historical receipt. After process start, Manager MUST NOT
derive running state from persisted `control_state=running` alone. A session is actionable as
running only after exact invocation readback and `_bind_active_haas_turn` establish a live recovery
pump. While proof is pending, Manager projects additive local recovery state `recovering` with no
Stop action. This does not extend the six-value HaaS execution-control contract or ADK state.
Queue recovery follows the same boundary: a `dispatching` item with no checkpoint returns to a
paused queue, while a checkpointed item remains attached to its accepted invocation and MUST NOT be
requeued. Terminal reconciliation removes that queue item exactly once.

After the owned HaaS endpoint passes execution readiness, Manager scans all checkpointed command
receipts whose disposition is `running`. Reconciliation is single-flight per Manager session and
bounded across sessions so one remote endpoint cannot delay first paint or project hydration.

| Authoritative readback | Required Manager action |
|---|---|
| Invocation is terminal | Consume canonical events after the saved cursor, reconcile the terminal barrier, merge or append one assistant/task-outcome projection, persist `idle` or authoritative `paused`, and terminalize matching stale running receipts. |
| Invocation is non-terminal and HaaS proves a live owner | Start one background recovery pump for the same invocation, register Manager busy/control ownership, continue from saved cursors, and never POST `/run_sse` again. |
| Local invocation has no live owner | Let HaaS fencing reconcile it once to retryable `incomplete` with `code=safeReason=sidecar_restart_execution_lost`; consume that terminal and clear false running/Stop state. |
| Backend is unavailable or ownership is indeterminate | Persist `restartRecovery.state=recovery_required` with safe `backend_unavailable`; expose retryable recovery, not running/completed/failed execution; create no work. |
| Binding, attempt, bridge, session, or invocation identities disagree | Fail closed as `recovery_required`, emit a safe diagnostic, and do not read or mutate another invocation. |

Repeated process starts, invocation GETs, and WebSocket opens MUST NOT duplicate an assistant row,
task outcome, tool activity, terminal event, `turn_done`, or command terminalization. Stable Manager
turn/row identities and bridge invocation/cursors are merge keys. Once the recovered invocation is
terminal, only receipts that cannot represent a newer active invocation may be terminalized; an
older recovery result never overwrites a newer accepted invocation.

### Graceful restart and automatic continuation

The authenticated Manager-local mutation `POST /v1/lifecycle/prepare-restart` supports an optional
`Idempotency-Key` and returns only aggregate safe results:

```json
{"state":"ready|partial|blocked","generation":"restart_<opaque>","paused":1,"recoveryRequired":0}
```

It first blocks new foreground sends and queue dispatch, then concurrently requests Pause for each
Manager-owned pause-capable HaaS invocation with a bounded per-item deadline. Automatic continuation
is allowed only after HaaS persists the source invocation as `interrupted` and returns
`sessionControl.controlState=paused`, `supportsResume=true`, and the exact
`resumableInvocationId`. Manager then persists `restartRecovery.state=paused` for that source.
Pause timeout, unsupported pause, a pre-acceptance turn, an in-process provider turn, identity
mismatch, or persistence failure remains `partial|blocked` and is never promoted to auto-resumable.

The Tauri updater and explicit desktop Quit path call this endpoint before child termination and
wait only for its bounded response. They may still exit after a partial result, but MUST NOT label
unpaused work recoverable. A crash remains outside this graceful contract.

On the next launch, terminal/readback reconciliation runs first. If a `paused` restart marker still
matches the authoritative resumable source invocation, Manager atomically claims the session,
changes the marker to `reattaching`, and invokes existing HaaS Continue once. Continue creates one
linked invocation on the same durable native session/thread; it does not append a synthetic user
message or replay the original request. It supplies one fixed Manager-owned continuation instruction
to inspect current state, avoid repeating completed side effects, finish remaining work, and return
the final result. Events use the normal bridge. Terminal completion sets the
marker to `continued`; pre-acceptance failure restores `paused` for manual resume. Repeated startup
reuses an already accepted linked attempt instead of creating another invocation.

### UX, compatibility, tests, and acceptance

- Project/session shells remain immediate. During readback, the conversation may show a muted
  localized “Recovering task state…” row. `recovery_required` has no active spinner; no owner means
  no Stop.
- A graceful continuation remains in the same conversation and logical user turn. It may add an
  inference round but never duplicates the user message.
- A crash orphan ends with a localized interruption explanation and retryable action. Partial
  reasoning/tool/final evidence remains, and terminal activities stop animating.
- History without `restartRecovery` is lazily reconciled. No bulk rewrite or legacy renderer is
  introduced.
- ADK REST/SSE and `/v1/haas/*` are unchanged. The lifecycle endpoint and recovery projection are
  authenticated Manager-local additive contracts. Routing, artifact, policy, container and
  secretless credential contracts are unchanged.

TDD proceeds in seven vertical slices: (1) additive `execution_ref`, deterministic
`checkpointed_running` command-store scan and idempotent session terminalization; (2) orphan startup reconciliation to one persisted
`incomplete` outcome, `idle`, and terminal receipt; (3) proven live-owner same-invocation pump with
no profile sync or `/run_sse`; (4) bounded idempotent prepare-restart with mixed pause capability and
queue freeze; (5) paused marker to exactly one linked Continue invocation, with pre-acceptance
failure remaining paused; (6) Tauri updater/Quit preparation before child kill without indefinite
exit blocking; and (7) packaged restart plus force-kill acceptance.

Packaged acceptance starts a long pause-capable local Codex turn, triggers desktop restart, launches
the newly installed `.app`, and proves automatic continuation to one canonical terminal result in
the same conversation with no duplicate user row. A force-kill variant must instead converge to one
safe `incomplete`, perform no automatic replay, and expose no false running/Stop.

Component impact: Manager command store, session/binding projection, startup lifecycle, desktop
shutdown, transcript recovery, and packaged smoke are affected. Session Runtime already owns the
required fenced-orphan and Pause/Continue semantics, so no ADK or HaaS native schema delta is
required. Harness adapter, model proxy, MCP, artifact store, policy, container runtime, and
credential schemas are unaffected; their existing secretless/native-resume contracts remain
prerequisites.

### Restart review corrections

Updater failure before installation commits must leave Manager admission open. macOS/Linux
prepare only after successful installation immediately before restart; Windows uses the updater
before-exit hook after verified download/extraction. Explicit Quit retains bounded drain.

Continued product turns may span more than two invocations. Merge all assistant snapshots,
retain first row/turn/timestamp identity and latest answer/outcome, and preserve predecessor
activity/model-stage facts. Current invocation readback replaces its fact collection, removing
stale entries without erasing predecessor facts. Reused ids across invocations stay distinct;
repeated readback is idempotent.

Tasks/acceptance: failing tests for three continuations followed by current bridge readback,
stale replacement and stable identity; updater error/success ordering; implement and second-round
review. Only Manager projection and desktop shutdown ordering change; ADK/HaaS endpoints,
container, permissions and secret contracts are unaffected.
