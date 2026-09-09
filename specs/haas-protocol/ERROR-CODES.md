# HaaS Error Code Catalog (Single Source of Truth)

**English** | [简体中文](ERROR-CODES.zh-CN.md)

Status: Draft
Last reviewed: 2026-08-26

This file is the sole catalog of all stable HaaS error codes. Errors from `POST /run`,
`/run_sse`, session paths, `/v1/haas/*`, and the legacy shim MUST map to this table.
OpenAPI `haasError.code` values MUST have a one-to-one correspondence with this table.
`ADK` semantic codes have no prefix; HaaS extensions use the `haas_` prefix. The only
unprefixed ADK semantic codes are `missing_credential`, `invalid_credential`,
`invalid_input`, `app_not_found`, `session_not_found`, `session_busy`, and
`session_expired`; every other code MUST use the `haas_` prefix. `type` follows the
ADK/FastAPI style, and `code` is a stable contract.

## 1. Authentication and Scope

| code | HTTP | retryable | safeReason | Source component |
|------|------|-----------|------------|----------|
| `missing_credential` | 401 | no | missing_credential | identity |
| `invalid_credential` | 401 | no | invalid_credential | identity |
| `app_not_found` | 404 | no | app_not_found | harness-registry (appName resolution failed) |
| `haas_harness_not_found` | 404 | no | harness_not_found | harness-registry (`/v1/haas/harnesses/{id}`) |
| `session_not_found` | 404 | no | session_not_found | session-runtime |
| `haas_invocation_not_found` | 404 | no | invocation_not_found | session-runtime |
| `haas_delegated_session_not_found` | 404 | no | delegated_session_not_found | manager-delegation |
| `haas_file_not_found` | 404 | no | file_not_found | artifact-store |

## 2. Protocol and Validation

| code | HTTP | retryable | safeReason | Source component |
|------|------|-----------|------------|----------|
| `invalid_input` | 400 | no | invalid_input | haas-protocol (schema validation) |
| `haas_legacy_request_invalid` | 400 | no | legacy_request_invalid | haas-protocol (shim cannot map the request) |
| `haas_unsupported_base` | 422 | no | unsupported_base | harness-registry |
| `haas_tool_schema_unsupported` | 422 | no | tool_schema_unsupported | model-proxy |

## 3. Session / Invocation

| code | HTTP | retryable | safeReason | Source component |
|------|------|-----------|------------|----------|
| `session_busy` | 409 | yes | session_busy | session-runtime (concurrent run in the same session) |
| `session_expired` | 410 | no | session_expired | session-runtime |
| `haas_offset_expired` | 410 | no | offset_expired | event-log-sse (HaaS native replay cursor expired) |
| `haas_cancel_unsupported` | 422 | no | cancel_unsupported | harness-adapter |
| `haas_delegated_session_conflict` | 409 | no | delegated_session_binding_conflict | manager-delegation |
| `haas_approval_not_found` | 404 | no | approval_not_found | manager-delegation / session-runtime |
| `haas_approval_state_conflict` | 409 | no | approval_state_conflict | manager-delegation / session-runtime |

## 4. Policy and Security

| code | HTTP | retryable | safeReason | Source component |
|------|------|-----------|------------|----------|
| `haas_policy_denied` | 403 | no | policy_denied | policy-controller |
| `haas_policy_invalid` | 400 | no | policy_invalid | policy-controller |
| `haas_policy_unsupported` | 422 | no | policy_unsupported | policy-controller |
| `haas_url_not_allowed` | 403 | no | network_host_not_allowed | security-boundary (SSRF/egress) |
| `haas_secret_input_invalid` | 400 | no | secret_input_invalid | security-boundary |
| `haas_provider_source_invalid` | 422 | no | provider_source_invalid | harness-registry / security-boundary |
| `haas_mcp_source_invalid` | 422 | no | mcp_source_invalid | mcp-tool-skill-runtime |
| `haas_skill_source_invalid` | 422 | no | skill_source_invalid | mcp-tool-skill-runtime (skill path escapes its boundary, missing `SKILL.md`, or invalid bundle) |

## 5. Harness / Adapter

| code | HTTP | retryable | safeReason | Source component |
|------|------|-----------|------------|----------|
| `haas_adapter_unavailable` | 503 | yes | adapter_unavailable | harness-adapter |
| `haas_adapter_incompatible` | 503 | no | adapter_incompatible | harness-adapter (schema drift) |
| `haas_adapter_overloaded` | 429 | yes | adapter_overloaded | harness-adapter |
| `haas_adapter_error` | 502 | yes | adapter_error | harness-adapter (generic native error) |

## 6. Model / MCP / Provider

| code | HTTP | retryable | safeReason | Source component |
|------|------|-----------|------------|----------|
| `haas_model_unavailable` | 422 | no | model_unavailable | harness-registry |
| `haas_provider_error` | 502 | yes | provider_error | model-proxy |
| `haas_provider_timeout` | 504 | yes | provider_timeout | model-proxy (stream idle timeout exhausted) |
| `haas_mcp_unavailable` | 503 | yes | mcp_unavailable | mcp-tool-skill-runtime (required MCP unavailable) |

## 7. Sandbox / Runtime

| code | HTTP | retryable | safeReason | Source component |
|------|------|-----------|------------|----------|
| `haas_sandbox_widening_rejected` | 403 | no | sandbox_widening_rejected | sandbox-runtime |
| `haas_vault_unavailable` | 503 | yes | vault_unavailable | sandbox-runtime / security-boundary |
| `haas_request_timeout` | 504 | no | request_timeout | session-runtime |
| `haas_delegation_backend_unavailable` | 503 | yes | delegation_backend_unavailable | manager-delegation |
| `haas_delegation_restore_failed` | 503 | yes | delegation_restore_failed | manager-delegation / sandbox-runtime |
| `haas_delegation_mount_invalid` | 403 | no | delegation_mount_invalid | manager-delegation / policy-controller |
| `haas_delegation_image_unavailable` | 503 | yes | delegation_image_unavailable | manager-delegation / container-runtime |
| `haas_workspace_lock_busy` | 409 | yes | workspace_lock_busy | manager-delegation / admission-control |
| `haas_workspace_lock_timeout` | 429 | yes | workspace_lock_timeout | manager-delegation / admission-control |

## 8. Admission / Store

| code | HTTP | retryable | safeReason | Source component |
|------|------|-----------|------------|----------|
| `haas_rate_limited` | 429 | yes | rate_limit_exceeded | admission-control |
| `haas_quota_exceeded` | 429 | yes | quota_exceeded | admission-control |
| `haas_queue_full` | 503 | yes | queue_full | admission-control |
| `haas_queue_timeout` | 429 | yes | queue_timeout | admission-control |
| `haas_store_unavailable` | 503 | yes | store_unavailable | stores |
| `haas_idempotency_store_unavailable` | 503 | yes | idempotency_store_unavailable | session-runtime / stores |
| `haas_idempotency_conflict` | 409 | no | idempotency_conflict | session-runtime / stores (same key, different request hash) |
| `haas_identity_unavailable` | 503 | yes | identity_unavailable | identity |

## 9. Artifact

| code | HTTP | retryable | safeReason | Source component |
|------|------|-----------|------------|----------|
| `haas_file_too_large` | 413 | no | file_too_large | artifact-store |
| `haas_preview_unavailable` | 501 | no | preview_unavailable | artifact-store |
| `haas_archive_failed` | 500 | yes | archive_failed | artifact-store |

## Rules

1. Codes with `retryable=true` MUST be represented consistently in
   `haasError.retryable`. `retryAfterMs` MAY be provided for 429/409 responses.
2. This catalog MUST be updated before implementing a new error code. Components MUST
   NOT introduce stable codes independently.
3. `safeReason` and `detail` MUST NOT contain secrets, internal hosts, absolute paths,
   or stack traces.
