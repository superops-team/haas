# Artifact Store Component Specification

**English** | [简体中文](README.zh-CN.md)

Status: Draft
Last reviewed: 2026-09-10
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
async def list_artifacts(session_id: str, ctx: RequestContext) -> list[FileRecord]: ...
async def open_artifact(container_id: str, file_id: str, ctx: RequestContext) -> FileStream: ...
async def build_archive(session_id: str, ctx: RequestContext) -> ArchiveStream: ...
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
| harness output (scan/register) | session container workspace | Read through Container Runtime by `relativePath` |

S6 implements content readback and archiving only for uploaded content; container content reading is integrated after S5 sandbox projection becomes available. When `GET /v1/haas/files/{id}/content` matches a record whose content cannot be read back, it MUST return `404 haas_file_not_found` and MUST NOT return an empty body that falsely indicates success.

### 6.1.2 Principal Scope (S6)

`FileRecord` MUST carry its owning principal (`ownerPrincipalId`), consistently with Security Boundary §8.3:

- Record the caller principal at upload time.
- `list` / `get` / `content` / `archive` MUST filter by principal.
- Cross-principal access returns `404 haas_file_not_found`, not 403, to prevent existence disclosure.

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
- Symlink traversal outside the container root is rejected.
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
