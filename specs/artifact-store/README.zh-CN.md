# Artifact Store 组件规格

[English](README.md) | **简体中文**

Status: Draft
Last reviewed: 2026-09-20
Change ID: haas-artifact-product-surface
Related specs: [HaaS Protocol](../haas-protocol/README.zh-CN.md), [Session Runtime](../session-runtime/README.zh-CN.md), [Container Runtime](../container-runtime/README.zh-CN.md), [Security Boundary](../security-boundary/README.zh-CN.md)

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
| POST | `/v1/haas/files` | 使用可选 `Idempotency-Key` 上传一个 input file，返回 `file` 对象 |
| GET | `/v1/haas/sessions/{session_id}/artifacts` | 列出 session 所有 artifacts |
| GET | `/v1/haas/sessions/{session_id}/artifacts/archive` | 下载 artifacts zip |
| GET | `/v1/haas/files/{file_id}/content` | 下载 artifact bytes |
| GET | `/v1/haas/files/{file_id}/pdf` | 可选 PDF preview（未实现返回 `501 haas_preview_unavailable`） |

inline 文件通过 ADK `newMessage.parts[].inlineData` 传入，不走上传端点。`fileId` 引用已上传文件是 HaaS 扩展。multipart 上传的幂等 request hash 包含规范化 metadata 与上传字节摘要；同一 key + 同一 hash 返回第一次创建的 `File`，同一 key 搭配不同字节或 metadata 返回 `409 haas_idempotency_conflict`。

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

`object` 恒为字面量 `"file"`（OpenAPI `File` schema required + const）。序列化层
必须显式输出该字段；内部 dataclass 不把它作为可变状态。

### 6.1.1 内容存储边界（S6）

`FileRecord` 是 metadata。原始 bytes 的存放位置按来源区分：

| 来源 | 内容位置 | 读取方式 |
|------|----------|----------|
| `POST /v1/haas/files` 上传 | Artifact Store 自持 content store（S6 为进程内，后续可换 Drive/OSS 后端） | 直接按 `file_id` 读回 |
| harness 产出（P0 scan/register） | 发布时写入 Artifact Store 自持的不可变 bytes snapshot | 按 scoped opaque `file_id` 读取；`relativePath` 仅用于展示/deep link |

P0 将每个通过校验的 produced file 快照到与 upload 相同的 content store。这样发布后的
文件无需暴露 host path，也不依赖仍然存活的 container，即可在 turn 结束后读取。未来大文件
或远程 backend 可以将内容保留在 session runtime，但必须保持相同的 scoped `file_id` 读取
合同与 availability 字段。`GET /v1/haas/files/{id}/content` 命中无内容可回读的 record 时返回
`404 haas_file_not_found`，不得返回空 body 伪装成功。

### 6.1.2 Principal Scope（S6）

`FileRecord` 必须携带归属 principal（`ownerPrincipalId`），与 Security Boundary
§8.3 一致：

- 上传时记录调用方 principal。
- `list` / `get` / `content` / `archive` 必须按 principal 过滤。
- 跨 principal 访问返回 `404 haas_file_not_found`，不返回 403，避免存在性泄漏。

关联到 session 的 artifact 还必须以完整 ADK session identity
`(appName, userId, sessionId)` 作为存储和删除 scope，裸 `sessionId` 不具备全局唯一性。
仅携带 `{session_id}` 的 native route 必须先解析出唯一的 caller-visible session，再按该
完整 scope 执行 list/archive；零个或多个匹配都返回与其他 native session route 一致的
not-found 响应。删除一个 ADK session 时，只能删除该 tuple 下当前及历史版本的 artifact，
不得误删复用了同一 caller-supplied `sessionId` 的其他 tuple 的 artifact。

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

`includeRoots` 是可发布 artifact 相对路径的 allowlist。候选 artifact 路径与
policy root 都必须先 canonicalize；规范化后的路径必须等于某个 include root，
或位于某个 include root 之下，才允许发布。比较必须按路径段进行：例如
`includeRoots=["output"]` 允许 `output/report.md`，但不得允许
`output_secret/report.md`。空 `includeRoots` 对发布无效，必须 fail closed，
拒绝所有候选 artifact 路径。

P0 将默认可发布根目录固定为 `output/`。这是产品与安全约定：用户可见交付物写在这里；
源码改动、依赖缓存、构建目录、coverage 目录和 runtime 日志不会因为发生变化就自动成为
artifact。固定默认值让首期 HaaS 路径更安全、可测试，也让 agent instruction 可以明确写：
“需要用户打开的文件写入 `output/`”。代价是写到项目原生目录的文件，例如 `docs/`、
`reports/`、`coverage/` 或 `dist/`，不会被默认展示；除非后续 profile/session policy
显式加入这些根目录。这个遗漏优于默认扫描整个仓库。

`includeRoots` 保留为未来加法配置字段。Profile 或 session policy 可以在通过同样的
canonicalization、exclude、数量、大小和隐藏路径检查后，添加 `reports` 或 `coverage`
等根目录。它不得默认指向 workspace root、用户 HOME、依赖目录、隐藏 runtime 目录或
无界 recent-file diff。UI 应将“产物”与“文件变更”区分开：源码改动和 workspace diff
也是任务结果，但不会自动成为可下载 artifact。

### 6.3 产物展示合同

产物是面向用户的交付物，不是原始文件系统杂项。只有 HaaS 从允许的 adapter 报告
或输出目录扫描中注册 `FileRecord` 后，产物才进入可见列表。最小展示 record 为：

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

`previewStatus` 取值为 `available`、`download_only` 或 `unavailable`。
`downloadStatus` 取值为 `available` 或 `unavailable`。这些字段是从存储边界、
媒体类型、policy 和当前 runtime 能力推导出的加法展示提示；它们不授予访问权限，
也不能替代 content read 时的 object-scope 检查。当前阶段若某个 harness-produced
record 只有 metadata 而不能读回内容，必须优先展示 `previewStatus=unavailable`
与 `downloadStatus=unavailable`，而不是打开一个损坏的空预览。若 archive 只能包含
可读产物，列表 record 仍必须让不可读条目可区分，使 client 能解释缺口。

状态组合：

| previewStatus | downloadStatus | 含义 |
|---------------|----------------|------|
| `available` | `available` | 内容可读，且 Manager/UI 可按媒体类型 inline preview。 |
| `download_only` | `available` | 内容可读，但 inline preview 不支持或文件过大；client 展示 metadata 和 download/open 操作。 |
| `unavailable` | `unavailable` | 产物已记录，但当前无法读取内容；client 展示明确不可用说明。 |

其他组合无效。HaaS 只可为向后兼容省略这些字段；新 producer 应显式设置。

HaaS 在 turn 终态扫描或 adapter 报告发布新文件时，应该发出或持久化
`haas.artifact.registered`。该 event 的 `haas` metadata 只能包含安全 metadata
（`fileId`、`relativePath`、`mediaType`、`bytes`、`invocationId`、`previewStatus`
和 `downloadStatus`），不得包含文件内容、raw prompt、完整 tool 参数、host path、
signed URL、credential 或隐藏 runtime path。Client 可以用该 event 刷新产物列表，
但 list endpoint 仍是权威来源。若 terminal 时注册多个 artifact，HaaS 可以逐文件发
event，也可以发有界 batch event；两种形式都必须遵守相同 metadata 限制。
同一 turn 的 artifact 注册事件必须先于该 turn 的 terminal event 持久化并输出，保证
terminal 仍是 invocation 的最后一个事件。artifact discovery 是 best-effort：扫描或读取
失败不得把原本成功的 turn 改写为失败。同一 session-relative path 的内容摘要未变化时，
重复 terminal 扫描不得追加新的可见 record 或注册 event；该路径内容变化时，当前 artifact
列表只能展示最新 record。

面向用户的文案应称这些对象为 “Artifacts” / “产物”：agent 为当前 session 创建的
文件、报告、图片、表格、归档或其他持久输出。build artifacts、cache entries、
container layers 和内部日志等实现或打包概念不属于此展示面，除非它们被明确注册到
允许的输出根目录下。

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
- Publish root 自身及每个 candidate file 都必须不是 symlink；即使 target 仍在 workspace
  内也拒绝 symlink traversal。
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
| content not readable back (container-only record) | `404 haas_file_not_found` |
| cross-principal access | `404 haas_file_not_found`（不返回 403） |

## 11. 测试计划与验收

- Unit：path canonicalization、symlink rejection、size/count limits、MIME inference。
- Integration：upload -> task input materialization -> artifact list -> download。
- Idempotency：同一 key 与字节/metadata hash 的重复上传返回第一次创建的 `File`；同一 key 搭配不同字节或 metadata 返回 `409 haas_idempotency_conflict`，且不创建第二个文件。
- Security：encoded `../` traversal and cross-principal file access return not found/rejected。
- Compatibility：ADK inline file（`inlineData`）round-trip、download 与 archive 行为。
- E2E：Codex writes a file under allowed output root and response annotation can download it。
- Product surface：turn 终态发布 artifact 后，client 可见列表刷新；metadata-only
  artifact 以明确的不可预览/仅下载状态展示，而不是打开损坏 viewer。
- Regression：hidden directory、host path、raw command output、prompt text、signed URL
  和 credential 不出现在 artifact list metadata 或 registration event 中。
