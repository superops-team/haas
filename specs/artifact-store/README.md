# Artifact Store Component Specification

**English** | [简体中文](README.zh-CN.md)

Status: Draft
Last reviewed: 2026-09-20
Change ID: haas-artifact-product-surface
Related specs: [HaaS Protocol](../haas-protocol/README.md), [Session Runtime](../session-runtime/README.md), [Container Runtime](../container-runtime/README.md), [Security Boundary](../security-boundary/README.md)

## 1. Component Role

Artifact Store manages HaaS input files, session container file indexes, agent artifact discovery, downloads, and archives. It supports ADK `actions.artifactDelta` and file input; the initial release MAY leave the complete file capability disabled.

## 2. Sources and Rationale

| Source | Adopted content |
|--------|-----------------|
| ADK 2.0 | inline files (`inlineData`), artifactDelta, download, archive, nosniff, path traversal |
| `mpa-codex-worker` container/event specs | output-directory scanning, artifact summary, path security |
| OpenSandbox AIO | file API, workspace filesystem, download capability |
| Component overview | file input and artifact listing/download |

## 3. Upstream and Downstream Relationships

| Direction | Component | Relationship |
|-----------|-----------|--------------|
| Upstream | HaaS Protocol | upload/list/download/archive endpoints |
| Upstream | Session Runtime | Creates containers and associates response artifacts |
| Upstream | Harness Adapter | Obtains input-file materialization paths and reports produced files |
| Downstream | Container Runtime | session workspace and filesystem |
| Downstream | Security Boundary | path validation, content headers, scope |
| Downstream | Event Log & SSE | artifact annotations and file-created events |

## 4. Responsibility Boundaries

Responsibilities:

- Receive and store files uploaded through `POST /v1/haas/files`.
- Materialize input files into controlled paths in the session workspace.
- Scan for produced artifacts or receive reports of them from adapters.
- Generate `container_file_citation` annotations for response output.
- List all artifacts for a session.
- Download the raw bytes of an individual artifact.
- Package a session artifact archive.
- Limit file size, count, path, and content type.

Non-responsibilities:

- It does not execute file-editing tools; file editing is performed by the harness/AIO/execd.
- It does not treat hidden runtime config, secret files, caches, `.git`, or harness home as artifacts.
- It does not read file contents for logs or metrics.
- It does not share artifacts across sessions unless a future session-sharing spec explicitly permits it.

## 5. Core Interfaces

### 5.1 Public API

| Method | Path | Description |
|--------|------|-------------|
| POST | `/v1/haas/files` | Upload one input file with optional `Idempotency-Key` and return a `file` object |
| GET | `/v1/haas/sessions/{session_id}/artifacts` | List all artifacts for a session |
| GET | `/v1/haas/sessions/{session_id}/artifacts/archive` | Download an artifact ZIP archive |
| GET | `/v1/haas/files/{file_id}/content` | Download artifact bytes |
| GET | `/v1/haas/files/{file_id}/pdf` | Optional PDF preview (when unimplemented, return `501 haas_preview_unavailable`) |

Inline files are passed through ADK `newMessage.parts[].inlineData`, not through the upload endpoint. Referencing an uploaded file by `fileId` is a HaaS extension. For multipart upload idempotency, the request hash includes normalized metadata and the uploaded byte digest. Reusing a key with the same hash returns the first `File`; reusing it with different bytes or metadata returns `409 haas_idempotency_conflict`.

### 5.2 Internal API

```python
async def store_input_file(file: UploadFile, ctx: RequestContext) -> FileRecord: ...
async def materialize_input_file(file_id: str, session: SessionRecord) -> SafePath: ...
async def scan_artifacts(session: SessionRecord, policy: ArtifactPolicy) -> ArtifactScanResult: ...
async def register_artifact(session: SessionRecord, path: SafePath) -> FileRecord: ...
async def list_artifacts(session: SessionRecord, ctx: RequestContext) -> list[FileRecord]: ...
async def open_artifact(container_id: str, file_id: str, ctx: RequestContext) -> FileStream: ...
async def build_archive(session: SessionRecord, ctx: RequestContext) -> ArchiveStream: ...
```

## 6. Data Models

### 6.1 FileRecord

```json
{
  "id": "file_abc",
  "object": "file",
  "sessionId": "s_123",
  "invocationId": "inv_abc",
  "filename": "report.md",
  "relativePath": "output/report.md",
  "bytes": 2048,
  "mediaType": "text/markdown",
  "sha256": "abc",
  "createdAtMs": 1786400240000
}
```

`object` is always the literal `"file"` (required + const in the OpenAPI `File` schema). The serialization layer MUST explicitly emit this field; the internal dataclass does not treat it as mutable state.

### 6.1.1 Content Storage Boundary (S6)

`FileRecord` is metadata. The storage location for raw bytes depends on their source:

| Source | Content location | Read method |
|--------|------------------|-------------|
| Upload through `POST /v1/haas/files` | Artifact Store-owned content store (in-process for S6; replaceable by a Drive/OSS backend later) | Read directly by `file_id` |
| harness output (P0 scan/register) | Artifact Store-owned immutable byte snapshot captured at publication | Read by scoped opaque `file_id`; `relativePath` remains display/deep-link metadata |

P0 snapshots each accepted produced file into the same content store used for uploads. This makes a
published file readable after the turn without exposing a host path or depending on a live container.
Future large-file or remote backends MAY retain content in the session runtime, but must preserve the
same scoped `file_id` read contract and availability fields. When
`GET /v1/haas/files/{id}/content` matches a record whose content cannot be read back, it MUST return
`404 haas_file_not_found` and MUST NOT return an empty body that falsely indicates success.

### 6.1.2 Principal Scope (S6)

`FileRecord` MUST carry its owning principal (`ownerPrincipalId`), consistently with Security Boundary §8.3:

- Record the caller principal at upload time.
- `list` / `get` / `content` / `archive` MUST filter by principal.
- Cross-principal access returns `404 haas_file_not_found`, not 403, to prevent existence disclosure.

Artifacts attached to a session MUST additionally use the complete ADK session identity
`(appName, userId, sessionId)` as their storage and deletion scope. A bare `sessionId` is not
globally unique. Native routes containing only `{session_id}` MUST first resolve exactly one
caller-visible session and then list/archive that full scope; zero or multiple matches return the
same not-found response used by other native session routes. Deleting one ADK session MUST remove
only that tuple's current and superseded artifacts, never artifacts owned by another tuple that
reuses the same caller-supplied `sessionId`.

### 6.2 ArtifactPolicy

```json
{
  "includeRoots": ["output"],
  "excludePrefixes": [".git", ".haas", ".codex", ".cache", "node_modules"],
  "maxFiles": 1000,
  "maxPublishFiles": 20,
  "maxFileBytes": 104857600,
  "allowHidden": false
}
```

`includeRoots` is an allowlist for publishable artifact relative paths. A
normalized artifact path MUST be equal to one configured include root or be
located below one configured include root after both the candidate path and
policy roots are canonicalized. Segment-aware comparison is required: for
example, `includeRoots=["output"]` allows `output/report.md` but MUST NOT
allow `output_secret/report.md`. An empty `includeRoots` list is invalid for
publishing and MUST fail closed by rejecting every candidate artifact path.

P0 fixes the default publishable root to `output/`. This is a product and
security convention: user-visible deliverables belong there, while source
edits, dependency caches, build trees, coverage directories, and runtime logs
are not artifacts merely because they changed. The fixed default keeps the
first HaaS path safe and testable, and lets agent instructions say: "write
files the user should open to `output/`." It also means HaaS will intentionally
miss files written to project-native locations such as `docs/`, `reports/`,
`coverage/`, or `dist/` unless a later profile/session policy adds those roots.
That omission is preferable to scanning the whole repository by default.

`includeRoots` remains a policy field for future additive configuration. A
profile or session policy MAY add roots such as `reports` or `coverage` after
the same canonicalization, exclusion, count, size, and hidden-path checks pass.
It MUST NOT default to the workspace root, user home, dependency directories,
hidden runtime directories, or an unbounded recent-file diff. UI surfaces SHOULD
keep "Artifacts" distinct from "Changed files": source edits and workspace diffs
are task results, but they are not automatically downloadable artifacts.

### 6.3 Produced Artifact Surface Contract

Produced artifacts are user-facing deliverables, not raw filesystem trivia. A produced
artifact becomes visible only after HaaS has registered a `FileRecord` from an allowed
adapter report or output-root scan. The minimum display record is:

```json
{
  "id": "file_abc",
  "filename": "security-review.html",
  "relativePath": "output/reports/security-review.html",
  "bytes": 24576,
  "mediaType": "text/html",
  "createdAtMs": 1786400240000,
  "previewStatus": "available",
  "downloadStatus": "available"
}
```

`previewStatus` is `available`, `download_only`, or `unavailable`. `downloadStatus` is
`available` or `unavailable`. These fields are additive presentation hints derived from
the storage boundary, media type, policy, and current runtime capability; they do not grant
access and do not replace object-scope checks on content reads. When a harness-produced
record is metadata-only in the current stage, `previewStatus=unavailable` and
`downloadStatus=unavailable` are preferable to a broken empty preview. If archive can include
only readable artifacts, the list record MUST still make unreadable entries distinguishable
so clients can explain the gap.

Status combinations:

| previewStatus | downloadStatus | Meaning |
|---------------|----------------|---------|
| `available` | `available` | Content is readable and the Manager/UI can render an inline preview for the media type. |
| `download_only` | `available` | Content is readable, but inline preview is unsupported or too large; the client should show metadata plus a download/open action. |
| `unavailable` | `unavailable` | The artifact is recorded but content is not currently readable; the client should show an explanatory unavailable state. |

Other combinations are invalid. HaaS may omit these fields only for backward compatibility;
new producers SHOULD set them explicitly.

HaaS SHOULD emit or persist `haas.artifact.registered` when a turn terminal scan or adapter
report publishes new files. The event's `haas` metadata MUST include only safe metadata
(`fileId`, `relativePath`, `mediaType`, `bytes`, `invocationId`, `previewStatus`, and
`downloadStatus`). It MUST NOT include file contents, raw prompts, complete tool arguments,
host paths, signed URLs, credentials, or hidden runtime paths. Clients MAY use this event to
refresh their artifact list, but the list endpoint remains authoritative. If multiple artifacts
are registered at terminal, HaaS MAY emit one event per file or one bounded batch event; either
form must preserve the same metadata restrictions.
Artifact registration events for a turn MUST be persisted and streamed before that turn's
terminal event so the terminal remains the final invocation event. Artifact discovery is
best-effort: a scan/read failure MUST NOT rewrite an otherwise successful turn to failed.
Repeated terminal scans MUST NOT append another visible record or registration event when the
same session-relative path still has the same content digest. If the content at that path changes,
the current artifact list MUST expose only the newest record for that path.

User-facing terminology SHOULD describe these objects as "Artifacts" / "产物": the files,
reports, images, tables, archives, or other durable outputs created by the agent for the
current session. Implementation or packaging terms such as build artifacts, cache entries,
container layers, and internal logs are not part of this surface unless explicitly registered
under the allowed output roots.

## 7. Runtime Model and State Machine

```text
input upload
  -> validate size/name/content type
  -> store bytes
  -> materialize for session turn

turn terminal
  -> scan allowed output roots
  -> register artifact metadata
  -> add response annotations
  -> expose list/download/archive
```

The artifact lifecycle follows the session lifecycle. Deleting a session makes its artifacts unreachable.

## 8. Security and Permissions

- File IDs are opaque and scoped; paths MUST NOT be derived directly from IDs.
- Relative paths are canonicalized under the session container root.
- The publish root itself and every candidate file MUST be non-symlinks; symlink traversal is
  rejected even when the target resolves inside the workspace.
- Hidden/runtime directories are excluded by default.
- Downloads set `X-Content-Type-Options: nosniff`.
- Active content SHOULD be served as an attachment or from a separate origin.
- Upload size and artifact count are bounded.

## 9. Observability

- `haas.file.uploaded`
- `haas.file.materialized`
- `haas.artifact.scanned`
- `haas.artifact.registered`
- `haas.artifact.downloaded`
- `haas.artifact.path_rejected`
- `haas.artifact.archive_built`

Metrics:

- `haas_file_upload_bytes`
- `haas_artifact_count{adapterBase}`
- `haas_artifact_scan_duration_ms`
- `haas_artifact_download_total{status}`

## 10. Failures and Recovery

| Scenario | Behavior |
|----------|----------|
| upload too large | `413 haas_file_too_large` |
| invalid filename/path | `400 invalid_input` |
| path traversal | reject and log a security event |
| file outside caller scope | `404 haas_file_not_found` |
| scan exceeds maximum files | fail closed for publication; the response may still complete with an artifact summary marked truncated |
| archive build fails | `500 haas_archive_failed` with a safe reason |
| preview unsupported | `501 haas_preview_unavailable` |
| content cannot be read back (container-only record) | `404 haas_file_not_found` |
| cross-principal access | `404 haas_file_not_found` (not 403) |

## 11. Test Plan and Acceptance

- Unit: path canonicalization, symlink rejection, size/count limits, and MIME inference.
- Integration: upload -> task input materialization -> artifact list -> download.
- Idempotency: repeating an upload with the same key and byte/metadata hash returns the first `File`; changing bytes or metadata with the same key returns `409 haas_idempotency_conflict` and creates no second file.
- Security: encoded `../` traversal and cross-principal file access return not found/rejected.
- Compatibility: ADK inline-file (`inlineData`) round trip, download, and archive behavior.
- E2E: Codex writes a file under an allowed output root and the response annotation can download it.
- Product surface: terminal artifact publication refreshes the client-visible list; metadata-only
  artifacts render with an explicit unavailable/download-only state instead of a broken viewer.
- Regression: hidden directories, host paths, raw command output, prompt text, signed URLs, and
  credentials never appear in artifact list metadata or registration events.
