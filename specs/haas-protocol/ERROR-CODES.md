# HaaS 错误码目录（单一事实源）

Status: Draft
Last reviewed: 2026-08-26

本文件是 HaaS 全部稳定错误码的唯一目录。`POST /run`、`/run_sse`、session
路径、`/v1/haas/*` 与 legacy shim 的错误都必须映射到本表；OpenAPI 的
`haasError.code` 与本表保持一一对应。`ADK` 语义码不加前缀，HaaS 扩展加
`haas_` 前缀。不加前缀的 ADK 语义码仅限：`missing_credential`、
`invalid_credential`、`invalid_input`、`app_not_found`、`session_not_found`、
`session_busy`、`session_expired`；其余一律加 `haas_` 前缀。
`type` 取 ADK/FastAPI 风格，`code` 是稳定契约。

## 1. 鉴权与 scope

| code | HTTP | retryable | safeReason | 来源组件 |
|------|------|-----------|------------|----------|
| `missing_credential` | 401 | no | missing_credential | identity |
| `invalid_credential` | 401 | no | invalid_credential | identity |
| `app_not_found` | 404 | no | app_not_found | harness-registry（appName 解析失败） |
| `haas_harness_not_found` | 404 | no | harness_not_found | harness-registry（`/v1/haas/harnesses/{id}`） |
| `session_not_found` | 404 | no | session_not_found | session-runtime |
| `haas_invocation_not_found` | 404 | no | invocation_not_found | session-runtime |
| `haas_file_not_found` | 404 | no | file_not_found | artifact-store |

## 2. 协议与校验

| code | HTTP | retryable | safeReason | 来源组件 |
|------|------|-----------|------------|----------|
| `invalid_input` | 400 | no | invalid_input | haas-protocol（schema 校验） |
| `haas_legacy_request_invalid` | 400 | no | legacy_request_invalid | haas-protocol（shim 不可映射） |
| `haas_unsupported_base` | 422 | no | unsupported_base | harness-registry |
| `haas_tool_schema_unsupported` | 422 | no | tool_schema_unsupported | model-proxy |

## 3. Session / Invocation

| code | HTTP | retryable | safeReason | 来源组件 |
|------|------|-----------|------------|----------|
| `session_busy` | 409 | yes | session_busy | session-runtime（同 session 并发 run） |
| `session_expired` | 410 | no | session_expired | session-runtime |
| `haas_offset_expired` | 410 | no | offset_expired | event-log-sse（HaaS native replay cursor 过期） |
| `haas_cancel_unsupported` | 422 | no | cancel_unsupported | harness-adapter |

## 4. Policy 与安全

| code | HTTP | retryable | safeReason | 来源组件 |
|------|------|-----------|------------|----------|
| `haas_policy_denied` | 403 | no | policy_denied | policy-controller |
| `haas_policy_invalid` | 400 | no | policy_invalid | policy-controller |
| `haas_policy_unsupported` | 422 | no | policy_unsupported | policy-controller |
| `haas_url_not_allowed` | 403 | no | network_host_not_allowed | security-boundary（SSRF/egress） |
| `haas_secret_input_invalid` | 400 | no | secret_input_invalid | security-boundary |
| `haas_provider_source_invalid` | 422 | no | provider_source_invalid | harness-registry / security-boundary |
| `haas_mcp_source_invalid` | 422 | no | mcp_source_invalid | mcp-tool-skill-runtime |

## 5. Harness / Adapter

| code | HTTP | retryable | safeReason | 来源组件 |
|------|------|-----------|------------|----------|
| `haas_adapter_unavailable` | 503 | yes | adapter_unavailable | harness-adapter |
| `haas_adapter_incompatible` | 503 | no | adapter_incompatible | harness-adapter（schema drift） |
| `haas_adapter_overloaded` | 429 | yes | adapter_overloaded | harness-adapter |
| `haas_adapter_error` | 502 | yes | adapter_error | harness-adapter（原生错误泛型） |

## 6. Model / MCP / Provider

| code | HTTP | retryable | safeReason | 来源组件 |
|------|------|-----------|------------|----------|
| `haas_model_unavailable` | 422 | no | model_unavailable | harness-registry |
| `haas_provider_error` | 502 | yes | provider_error | model-proxy |
| `haas_provider_timeout` | 504 | yes | provider_timeout | model-proxy（stream idle 耗尽） |
| `haas_mcp_unavailable` | 503 | yes | mcp_unavailable | mcp-tool-skill-runtime（required MCP 不可用） |

## 7. Sandbox / Runtime

| code | HTTP | retryable | safeReason | 来源组件 |
|------|------|-----------|------------|----------|
| `haas_sandbox_widening_rejected` | 403 | no | sandbox_widening_rejected | sandbox-runtime |
| `haas_vault_unavailable` | 503 | yes | vault_unavailable | sandbox-runtime / security-boundary |
| `haas_request_timeout` | 504 | no | request_timeout | session-runtime |

## 8. Admission / Store

| code | HTTP | retryable | safeReason | 来源组件 |
|------|------|-----------|------------|----------|
| `haas_rate_limited` | 429 | yes | rate_limit_exceeded | admission-control |
| `haas_quota_exceeded` | 429 | yes | quota_exceeded | admission-control |
| `haas_queue_full` | 503 | yes | queue_full | admission-control |
| `haas_queue_timeout` | 429 | yes | queue_timeout | admission-control |
| `haas_store_unavailable` | 503 | yes | store_unavailable | stores |
| `haas_idempotency_store_unavailable` | 503 | yes | idempotency_store_unavailable | session-runtime / stores |
| `haas_idempotency_conflict` | 409 | no | idempotency_conflict | session-runtime / stores（同 key 不同 request hash） |
| `haas_identity_unavailable` | 503 | yes | identity_unavailable | identity |

## 9. Artifact

| code | HTTP | retryable | safeReason | 来源组件 |
|------|------|-----------|------------|----------|
| `haas_file_too_large` | 413 | no | file_too_large | artifact-store |
| `haas_preview_unavailable` | 501 | no | preview_unavailable | artifact-store |
| `haas_archive_failed` | 500 | yes | archive_failed | artifact-store |

## 规则

1. `retryable=true` 的码在 `haasError.retryable` 中一致反映；`retryAfterMs`
   对 429/409 可选提供。
2. 新增错误码必须先更新本目录，再实现；不得在组件内私自新增稳定码。
3. 所有 `safeReason`/`detail` 不得含 secret、内部 host、绝对路径、stack trace。
