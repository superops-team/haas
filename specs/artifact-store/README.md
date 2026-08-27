# Artifact Store 组件规格

Status: Draft
Last reviewed: 2026-08-26
Related specs: [HaaS Protocol](../haas-protocol/README.md), [Session Runtime](../session-runtime/README.md), [Container Runtime](../container-runtime/README.md), [Security Boundary](../security-boundary/README.md)

## 1. 组件定位

Artifact Store 管理 HaaS 输入文件、session container 文件索引、agent 产物发现、下载和归档。它支撑 ADK `actions.artifactDelta` 与文件输入，首期可先不启用完整文件能力。

## 2. 来源与依据

| 来源 | 采用内容 |
|------|----------|
| ADK 2.0 | inline file（`inlineData`）、artifactDelta、download、archive、nosniff、path traversal |
| `mpa-codex-worker` container/event specs | 输出目录扫描、artifact summary、路径安全 |
| OpenSandbox AIO | file API、workspace 文件系统、下载能力 |
| 本组件总览 | file input 和 artifact listing/download |

## 3. 上游与下游关系

| 方向 | 对象 | 关系 |
|------|------|------|
| 上游 | HaaS Protocol | upload/list/download/archive endpoints |
| 上游 | Session Runtime | 创建 container、关联 response artifact |
| 上游 | Harness Adapter | 获取 input file materialization path，报告 produced files |
| 下游 | Container Runtime | session workspace 和 filesystem |
| 下游 | Security Boundary | path validation、content headers、scope |
| 下游 | Event Log & SSE | artifact annotations and file-created events |

## 4. 职责边界

负责：

- 接收和保存 `POST /v1/haas/files` 上传文件。
- 将 input file materialize 到 session workspace 中的受控路径。
- 扫描或接收 adapter 报告的 produced artifacts。
- 为 response output 生成 `container_file_citation` annotation。
- 列出 session 全部 artifacts。
- 下载单个 artifact 原始 bytes。
- 打包 session artifacts archive。
- 限制文件大小、数量、路径和 content-type。

不负责：

- 不执行文件编辑工具；文件编辑由 harness/AIO/execd 执行。
- 不把隐藏 runtime config、secret files、cache、`.git` 或 harness home 作为 artifact。
- 不读取文件内容用于日志或 metrics。
- 不跨 session 共享 artifact，除非未来 session sharing spec 明确允许。

## 5. 核心接口

### 5.1 Public API

| Method | Path | 说明 |
|--------|------|------|
| POST | `/v1/haas/files` | 上传一个 input file，返回 `file` 对象 |
| GET | `/v1/haas/sessions/{session_id}/artifacts` | 列出 session 所有 artifacts |
| GET | `/v1/haas/sessions/{session_id}/artifacts/archive` | 下载 artifacts zip |
| GET | `/v1/haas/files/{file_id}/content` | 下载 artifact bytes |
| GET | `/v1/haas/files/{file_id}/pdf` | 可选 PDF preview（未实现返回 `501 haas_preview_unavailable`） |

inline 文件通过 ADK `newMessage.parts[].inlineData` 传入，不走上传端点。`fileId` 引用已上传文件是 HaaS 扩展。

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

## 6. 数据模型

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

## 7. 运行模型与状态机

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

Artifact lifecycle follows session lifecycle. Deleting a session makes its artifacts unreachable.

## 8. 安全与权限

- File ids are opaque and scoped; do not derive paths directly from ids.
- Relative paths are canonicalized under the session container root.
- Symlink traversal outside the container root is rejected.
- Hidden/runtime directories are excluded by default.
- Downloads set `X-Content-Type-Options: nosniff`.
- Active content should be served as attachment or from a separate origin.
- Upload size and artifact count are bounded.

## 9. 可观测性

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

## 10. 失败与恢复

| 场景 | 行为 |
|------|------|
| upload too large | `413 haas_file_too_large` |
| invalid filename/path | `400 invalid_input` |
| path traversal | reject and log security event |
| file outside caller scope | `404 haas_file_not_found` |
| scan exceeds max files | fail closed for publish; response still may complete with artifact summary marked truncated |
| archive build fails | `500 haas_archive_failed` with safe reason |
| preview unsupported | `501 haas_preview_unavailable` |

## 11. 测试计划与验收

- Unit：path canonicalization、symlink rejection、size/count limits、MIME inference。
- Integration：upload -> task input materialization -> artifact list -> download。
- Security：encoded `../` traversal and cross-principal file access return not found/rejected。
- Compatibility：ADK inline file（`inlineData`）round-trip、download 与 archive 行为。
- E2E：Codex writes a file under allowed output root and response annotation can download it。
