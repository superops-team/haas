"""Manager -> HaaS delegation bridge tests."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest
from coworker.delegation import (
    HaasDelegationClient,
    HaasDelegationConfig,
    HaasDelegationError,
    LocalHaasSupervisor,
    adk_message_from_content,
    apply_config_snapshot,
    delegation_policy_snapshot,
    deterministic_decision,
    is_text_only_content,
    make_delegated_session_body,
)
from coworker.haas import AcceptedInvocationHeaders, HaasClientError, HaasEnvelope
from coworker.haas.attempts import AttemptLedger
from coworker.haas.stream_bridge import SessionKey, StreamBridgeState
from coworker.memory import Scope
from coworker.permissions import Mode
from coworker.providers import AssistantTurn, ModelCapabilities, ProviderClient
from coworker.server import SessionManager, create_app
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


class ScriptedProvider(ProviderClient):
    def __init__(self) -> None:
        self.calls = 0

    def complete(self, *, model, messages, tools=None, **settings):
        self.calls += 1
        return AssistantTurn(text="local")

    def capabilities(self, model):
        return ModelCapabilities()


class FakeHaasClient:
    def __init__(self, config: HaasDelegationConfig) -> None:
        self.config = config
        self.created: list[dict[str, Any]] = []
        self.restored: list[str] = []
        self.runs: list[dict[str, Any]] = []
        self.policy_updates: list[dict[str, Any]] = []
        self.accepted_headers = None

    async def create_delegated_session(self, body: dict[str, Any]) -> dict[str, Any]:
        self.created.append(body)
        return {
            "id": "dgsess_1",
            "managerSessionId": body["managerSessionId"],
            "haasSessionId": body["haasSessionId"],
            "haasUserId": body["haasUserId"],
            "harnessId": body["harnessId"],
            "harnessBase": body["harnessBase"],
            "image": body["image"],
            "provider": body["provider"],
            "mountManifest": body["mountManifest"],
            "delegationPolicySnapshot": body["delegationPolicySnapshot"],
            "runtime": {"status": "no_runtime", "containerGeneration": 0},
        }

    async def restore(self, delegated_session_id: str) -> dict[str, Any]:
        self.restored.append(delegated_session_id)
        return {
            "id": delegated_session_id,
            "runtime": {"status": "running", "containerGeneration": len(self.restored)},
        }

    async def get_delegated_session(self, delegated_session_id: str) -> dict[str, Any]:
        return {
            "id": delegated_session_id,
            "desiredRevision": 1,
            "appliedRevision": 1,
        }

    async def update_delegated_policy(
        self,
        delegated_session_id: str,
        policy_snapshot: dict[str, Any],
        *,
        expected_revision: int | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        self.policy_updates.append(
            {
                "delegated_session_id": delegated_session_id,
                "policy_snapshot": policy_snapshot,
                "expected_revision": expected_revision,
                "idempotency_key": idempotency_key,
            }
        )
        return {
            "desiredRevision": 2,
            "appliedRevision": 2,
            "status": "applied",
            "delegationPolicySnapshot": policy_snapshot,
        }

    async def get_execution_evidence(
        self, haas_session_id: str, invocation_id: str, tool_call_id: str, evidence_ref: str
    ) -> HaasEnvelope:
        return HaasEnvelope(
            data={
                "evidenceRef": evidence_ref,
                "sessionId": haas_session_id,
                "invocationId": invocation_id,
                "toolCallId": tool_call_id,
                "command": "acme auth login",
                "workingDirectory": "/workspace",
                "output": "Authorize in the browser",
                "outputStream": "combined",
                "links": [],
                "expiresAtMs": 1_900_000_000_000,
            },
            trace_id="trace_evidence",
        )

    async def capabilities(self) -> dict[str, Any]:
        return {
            "harnesses": [
                {
                    "id": self.config.harness_id,
                    "capabilities": {
                        "approval": {"mode": "human_bridge"},
                        "input": {"mode": "human_bridge"},
                        "pausing": {"status": "available", "mode": "native"},
                    },
                }
            ]
        }

    async def events_page(
        self,
        haas_session_id: str,
        *,
        after_event_id: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        del haas_session_id, after_event_id, limit
        return {
            "events": [{"eventId": "evt_2", "type": "haas.turn.completed", "haas": {}}],
            "next_cursor": "evt_2",
        }

    async def run_sse(
        self,
        *,
        haas_session_id: str,
        message: dict[str, Any],
        harness_id: str | None = None,
        user_id: str | None = None,
        idempotency_key: str | None = None,
        approval_policy: str = "never",
    ) -> AsyncIterator[dict[str, Any]]:
        from coworker.haas import AcceptedInvocationHeaders

        self.accepted_headers = AcceptedInvocationHeaders("inv_1", haas_session_id, 1786400000000)
        self.runs.append(
            {
                "haas_session_id": haas_session_id,
                "harness_id": harness_id,
                "user_id": user_id,
                "message": message,
                "idempotency_key": idempotency_key,
                "approval_policy": approval_policy,
            }
        )
        yield {
            "id": "evt_1",
            "content": {"role": "model", "parts": [{"text": "remote"}]},
            "actions": {},
        }
        yield {
            "id": "evt_2",
            "content": {"role": "model", "parts": [{"text": " done"}]},
            "actions": {"stateDelta": {"status": "completed"}},
        }


class FailingHaasClient(FakeHaasClient):
    async def restore(self, delegated_session_id: str) -> dict[str, Any]:
        raise HaasDelegationError("backend unavailable")


class FailedTurnHaasClient(FakeHaasClient):
    async def events_page(
        self,
        haas_session_id: str,
        *,
        after_event_id: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        del haas_session_id, after_event_id, limit
        return {
            "events": [{"eventId": "evt_2", "type": "haas.turn.failed", "haas": {}}],
            "next_cursor": "evt_2",
        }

    async def run_sse(
        self,
        *,
        haas_session_id: str,
        message: dict[str, Any],
        harness_id: str | None = None,
        user_id: str | None = None,
        idempotency_key: str | None = None,
        approval_policy: str = "never",
    ) -> AsyncIterator[dict[str, Any]]:
        del approval_policy
        from coworker.haas import AcceptedInvocationHeaders

        self.accepted_headers = AcceptedInvocationHeaders(
            "inv_failed", haas_session_id, 1786400000000
        )
        self.runs.append(
            {
                "haas_session_id": haas_session_id,
                "harness_id": harness_id,
                "user_id": user_id,
                "message": message,
                "idempotency_key": idempotency_key,
            }
        )
        yield {
            "id": "evt_2",
            "content": {"role": "model", "parts": []},
            "actions": {"stateDelta": {"status": "failed"}},
        }


class PendingPolicyHaasClient(FakeHaasClient):
    def __init__(self, config: HaasDelegationConfig) -> None:
        super().__init__(config)
        self.policy_reads = 0

    async def get_delegated_session(self, delegated_session_id: str) -> dict[str, Any]:
        self.policy_reads += 1
        return {
            "id": delegated_session_id,
            "desiredRevision": 2,
            "appliedRevision": 1 if self.policy_reads == 1 else 2,
        }


class ExpiredReplayHaasClient(FakeHaasClient):
    def __init__(self, config: HaasDelegationConfig) -> None:
        super().__init__(config)
        self.run_calls = 0

    async def run_sse(self, **kwargs) -> AsyncIterator[dict[str, Any]]:
        self.run_calls += 1
        if self.run_calls == 2:
            raise HaasDelegationError(
                "replay window expired",
                status_code=410,
                code="haas_idempotency_expired",
            )
        async for event in super().run_sse(**kwargs):
            yield event


class FakeDirectRunStream:
    def __init__(self, client: FakeDirectHaasClient) -> None:
        self.accepted = AcceptedInvocationHeaders("inv_direct_1", "hsess_s1", 1786400000000)
        self._client = client

    async def __aenter__(self) -> FakeDirectRunStream:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def events(self) -> AsyncIterator[dict[str, Any]]:
        yield {
            "id": "evt_direct_1",
            "content": {
                "role": "model",
                "parts": [
                    {"text": "Inspecting", "thought": True},
                    {"text": "local haas"},
                ],
            },
            "actions": {
                "artifactDelta": {
                    "toolCallId": "call_direct_1",
                    "toolName": "exec_command",
                    "safeSummary": "Run tests",
                }
            },
        }
        yield {
            "id": "evt_direct_2",
            "content": {"role": "model", "parts": [{"text": " done"}]},
            "actions": {"stateDelta": {"status": "completed"}},
        }


class FakeDirectHaasClient:
    def __init__(self, config: HaasDelegationConfig) -> None:
        self.config = config
        self.runs: list[dict[str, Any]] = []

    async def sync_profile(self, configuration, *, profile_id=None):
        self.profile_configuration = configuration
        return {"id": "hprof_direct", "version": 1, "profileFingerprint": "sha256:fixture"}

    async def get_session_profile(self, session_id):
        del session_id
        return HaasEnvelope(
            {
                "profileId": "hprof_direct",
                "profileVersion": 1,
                "profileFingerprint": "sha256:fixture",
                "harnessId": self.config.harness_id,
                "base": self.config.harness_base,
                "executionIntentFingerprint": "sha256:fixture-intent",
            },
            trace_id="tr_profile",
        )

    async def capabilities(self) -> HaasEnvelope[dict[str, Any]]:
        return HaasEnvelope(
            {
                "harnesses": [
                    {
                        "id": self.config.harness_id,
                        "capabilities": {
                            "approval": {"mode": "human_bridge"},
                            "input": {"mode": "human_bridge"},
                            "pausing": {"status": "available", "mode": "native"},
                        },
                    }
                ]
            },
            trace_id="tr_caps",
        )

    async def update_session_policy(
        self,
        session_id: str,
        policy: dict[str, Any],
        *,
        expected_revision: int,
        idempotency_key: str,
    ) -> HaasEnvelope[dict[str, Any]]:
        self.policy_update = {
            "session_id": session_id,
            "policy": policy,
            "expected_revision": expected_revision,
            "idempotency_key": idempotency_key,
        }
        revision = expected_revision + 1
        return HaasEnvelope(
            {
                "desiredRevision": revision,
                "appliedRevision": revision,
                "status": "applied",
                "appliedPolicy": policy,
            },
            trace_id="tr_policy",
        )

    def run_sse(self, body: dict[str, Any], *, idempotency_key: str, last_event_id=None):
        del last_event_id
        self.runs.append({"body": body, "idempotency_key": idempotency_key})
        return FakeDirectRunStream(self)

    async def events_page(
        self,
        session_id: str,
        *,
        after_event_id: str | None = None,
        limit: int = 100,
    ) -> HaasEnvelope[list[dict[str, Any]]]:
        del session_id, after_event_id, limit
        return HaasEnvelope(
            [
                {
                    "eventId": "evt_direct_2",
                    "invocationId": "inv_direct_1",
                    "type": "haas.turn.completed",
                    "haas": {},
                }
            ],
            trace_id="tr_direct",
        )


class CapturingDirectHaasClient(FakeDirectHaasClient):
    def __init__(self, config: HaasDelegationConfig) -> None:
        super().__init__(config)
        self.submitted_messages: list[dict[str, Any]] = []

    def run_sse(self, body: dict[str, Any], *, idempotency_key: str, last_event_id=None):
        self.submitted_messages.append(body["newMessage"])
        return super().run_sse(body, idempotency_key=idempotency_key, last_event_id=last_event_id)


class TerminalReconciliationRunStream:
    def __init__(self) -> None:
        self.accepted = AcceptedInvocationHeaders(
            "inv_terminal_reconcile", "hsess_s1", 1786400000000
        )
        self.release = asyncio.Event()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        self.release.set()

    async def events(self) -> AsyncIterator[dict[str, Any]]:
        yield {
            "id": "evt_adk_partial",
            "invocationId": "inv_terminal_reconcile",
            "content": {"role": "model", "parts": [{"text": "Checking", "thought": True}]},
            "actions": {},
        }
        # Reproduce a transport that remains open after its last ADK projection.
        await self.release.wait()


class TerminalReconciliationDirectHaasClient(FakeDirectHaasClient):
    def __init__(self, config: HaasDelegationConfig) -> None:
        super().__init__(config)
        self.stream = TerminalReconciliationRunStream()
        self.page_cursors: list[str | None] = []

    def run_sse(self, body: dict[str, Any], *, idempotency_key: str, last_event_id=None):
        del last_event_id
        self.runs.append({"body": body, "idempotency_key": idempotency_key})
        return self.stream

    async def events_page(
        self,
        session_id: str,
        *,
        after_event_id: str | None = None,
        limit: int = 100,
    ) -> HaasEnvelope[list[dict[str, Any]]]:
        del session_id, limit
        self.page_cursors.append(after_event_id)
        if after_event_id is None:
            return HaasEnvelope(
                [
                    {
                        "eventId": f"evt_history_{index:03d}",
                        "invocationId": "inv_history",
                        "type": "haas.output.text.delta",
                        "content": {"parts": [{"text": "old"}]},
                        "haas": {},
                    }
                    for index in range(100)
                ],
                trace_id="tr_history",
                next_cursor="evt_history_099",
            )
        if after_event_id == "evt_history_099":
            return HaasEnvelope(
                [
                    {
                        "eventId": "evt_tool_completed",
                        "invocationId": "inv_terminal_reconcile",
                        "type": "haas.tool.completed",
                        "haas": {
                            "toolCallId": "call_resource_usage",
                            "safeSummary": "Inspect system resources",
                            "status": "completed",
                            "exitCode": 0,
                        },
                    },
                    {
                        "eventId": "evt_native_failed",
                        "invocationId": "inv_terminal_reconcile",
                        "type": "haas.turn.failed",
                        "haas": {
                            "status": "failed",
                            "code": "haas_provider_error",
                            "safeReason": "provider unavailable",
                            "retryable": False,
                        },
                    },
                ],
                trace_id="tr_terminal",
            )
        return HaasEnvelope([], trace_id="tr_empty")

    async def get_invocation(
        self, session_id: str, invocation_id: str
    ) -> HaasEnvelope[dict[str, Any]]:
        return HaasEnvelope(
            {
                "id": invocation_id,
                "sessionId": session_id,
                "status": "failed",
            },
            trace_id="tr_invocation_failed",
        )


class CancelAwareDirectRunStream:
    def __init__(self, client: CancelAwareDirectHaasClient) -> None:
        self.accepted = AcceptedInvocationHeaders("inv_cancel_1", "hsess_s1", 1786400000000)
        self._client = client

    async def __aenter__(self) -> CancelAwareDirectRunStream:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def events(self) -> AsyncIterator[dict[str, Any]]:
        await asyncio.wait_for(self._client.cancelled.wait(), timeout=5)
        yield {
            "id": "evt_cancel_terminal",
            "invocationId": "inv_cancel_1",
            "content": {"role": "model", "parts": []},
            "actions": {"stateDelta": {"status": "cancelled"}},
        }


class CancelAwareDirectHaasClient(FakeDirectHaasClient):
    def __init__(self, config: HaasDelegationConfig) -> None:
        super().__init__(config)
        self.cancelled = asyncio.Event()
        self.cancel_calls: list[tuple[str, str]] = []

    def run_sse(self, body: dict[str, Any], *, idempotency_key: str, last_event_id=None):
        del last_event_id
        self.runs.append({"body": body, "idempotency_key": idempotency_key})
        return CancelAwareDirectRunStream(self)

    async def cancel_invocation(
        self, session_id: str, invocation_id: str
    ) -> HaasEnvelope[dict[str, Any]]:
        self.cancel_calls.append((session_id, invocation_id))
        self.cancelled.set()
        return HaasEnvelope(
            {"id": invocation_id, "sessionId": session_id, "status": "running"},
            trace_id="tr_cancel",
        )

    async def events_page(
        self, session_id: str, *, after_event_id: str | None = None, limit: int = 100
    ) -> HaasEnvelope[list[dict[str, Any]]]:
        del session_id, after_event_id, limit
        events = []
        if self.cancelled.is_set():
            events.append(
                {
                    "eventId": "evt_cancel_terminal",
                    "invocationId": "inv_cancel_1",
                    "type": "haas.turn.cancelled",
                    "haas": {"status": "cancelled"},
                }
            )
        return HaasEnvelope(events, trace_id="tr_cancel_events")

    async def get_invocation(
        self, session_id: str, invocation_id: str
    ) -> HaasEnvelope[dict[str, Any]]:
        return HaasEnvelope(
            {
                "id": invocation_id,
                "sessionId": session_id,
                "status": "cancelled" if self.cancelled.is_set() else "running",
            },
            trace_id="tr_cancel_invocation",
        )


class PauseAwareDirectRunStream:
    def __init__(self, client: PauseAwareDirectHaasClient) -> None:
        self.accepted = AcceptedInvocationHeaders(
            "inv_pause_1", "hsess_s1", 1786400000000
        )
        self._client = client

    async def __aenter__(self) -> PauseAwareDirectRunStream:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def events(self) -> AsyncIterator[dict[str, Any]]:
        await asyncio.wait_for(self._client.paused.wait(), timeout=5)
        yield {
            "id": "evt_pause_terminal",
            "invocationId": "inv_pause_1",
            "content": {"role": "model", "parts": []},
            "actions": {"stateDelta": {"status": "interrupted"}},
        }


class PauseAwareDirectHaasClient(FakeDirectHaasClient):
    def __init__(self, config: HaasDelegationConfig) -> None:
        super().__init__(config)
        self.paused = asyncio.Event()
        self.pause_calls: list[tuple[str, str]] = []

    def run_sse(self, body: dict[str, Any], *, idempotency_key: str, last_event_id=None):
        del last_event_id
        self.runs.append({"body": body, "idempotency_key": idempotency_key})
        return PauseAwareDirectRunStream(self)

    async def pause_invocation(
        self, session_id: str, invocation_id: str
    ) -> HaasEnvelope[dict[str, Any]]:
        self.pause_calls.append((session_id, invocation_id))
        self.paused.set()
        return HaasEnvelope(
            {
                "id": invocation_id,
                "sessionId": session_id,
                "status": "interrupted",
                "sessionControl": {
                    "controlState": "paused",
                    "supportsResume": True,
                    "resumableInvocationId": invocation_id,
                },
            },
            trace_id="tr_pause",
        )

    async def events_page(
        self, session_id: str, *, after_event_id: str | None = None, limit: int = 100
    ) -> HaasEnvelope[list[dict[str, Any]]]:
        del session_id, after_event_id, limit
        events = []
        if self.paused.is_set():
            events.append(
                {
                    "eventId": "evt_pause_terminal",
                    "invocationId": "inv_pause_1",
                    "type": "haas.turn.interrupted",
                    "haas": {"status": "interrupted"},
                }
            )
        return HaasEnvelope(events, trace_id="tr_pause_events")

    async def get_invocation(
        self, session_id: str, invocation_id: str
    ) -> HaasEnvelope[dict[str, Any]]:
        return HaasEnvelope(
            {
                "id": invocation_id,
                "sessionId": session_id,
                "status": "interrupted" if self.paused.is_set() else "running",
            },
            trace_id="tr_pause_invocation",
        )


class PauseStopRaceDirectHaasClient(CancelAwareDirectHaasClient):
    def __init__(self, config: HaasDelegationConfig) -> None:
        super().__init__(config)
        self.pause_calls: list[tuple[str, str]] = []

    async def pause_invocation(
        self, session_id: str, invocation_id: str
    ) -> HaasEnvelope[dict[str, Any]]:
        self.pause_calls.append((session_id, invocation_id))
        await asyncio.wait_for(self.cancelled.wait(), timeout=5)
        raise HaasClientError("pause superseded by stop")


class PausedReadbackBeforeTurnDoneStream:
    """Hold the run open after interrupted so Stop can race its finally block."""

    def __init__(self, client: PausedReadbackBeforeTurnDoneClient) -> None:
        self.accepted = AcceptedInvocationHeaders(
            "inv_pause_stop_1", "hsess_s1", 1786400000000
        )
        self._client = client

    async def __aenter__(self) -> PausedReadbackBeforeTurnDoneStream:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def events(self) -> AsyncIterator[dict[str, Any]]:
        await asyncio.wait_for(self._client.paused.wait(), timeout=5)
        self._client.terminal_yielded.set()
        yield {
            "id": "evt_pause_stop_terminal",
            "invocationId": "inv_pause_stop_1",
            "content": {"role": "model", "parts": []},
            "actions": {"stateDelta": {"status": "interrupted"}},
        }
        await asyncio.wait_for(self._client.finish_stream.wait(), timeout=5)


class PausedReadbackBeforeTurnDoneClient(PauseAwareDirectHaasClient):
    def __init__(self, config: HaasDelegationConfig) -> None:
        super().__init__(config)
        self.terminal_yielded = asyncio.Event()
        self.finish_stream = asyncio.Event()
        self.cancel_calls: list[tuple[str, str]] = []

    def run_sse(self, body: dict[str, Any], *, idempotency_key: str, last_event_id=None):
        del last_event_id
        self.runs.append({"body": body, "idempotency_key": idempotency_key})
        return PausedReadbackBeforeTurnDoneStream(self)

    async def pause_invocation(
        self, session_id: str, invocation_id: str
    ) -> HaasEnvelope[dict[str, Any]]:
        self.pause_calls.append((session_id, invocation_id))
        self.paused.set()
        await asyncio.wait_for(self.terminal_yielded.wait(), timeout=5)
        return HaasEnvelope(
            {
                "id": invocation_id,
                "sessionId": session_id,
                "status": "interrupted",
                "sessionControl": {
                    "controlState": "paused",
                    "supportsResume": True,
                    "resumableInvocationId": invocation_id,
                },
            },
            trace_id="tr_pause_stop_pause",
        )

    async def cancel_invocation(
        self, session_id: str, invocation_id: str
    ) -> HaasEnvelope[dict[str, Any]]:
        self.cancel_calls.append((session_id, invocation_id))
        self.finish_stream.set()
        return HaasEnvelope(
            {
                "id": invocation_id,
                "sessionId": session_id,
                "status": "interrupted",
                "sessionControl": {
                    "controlState": "cancelled",
                    "supportsResume": False,
                    "resumableInvocationId": None,
                },
            },
            trace_id="tr_pause_stop_cancel",
        )

    async def events_page(
        self, session_id: str, *, after_event_id: str | None = None, limit: int = 100
    ) -> HaasEnvelope[list[dict[str, Any]]]:
        del session_id, after_event_id, limit
        events = []
        if self.paused.is_set():
            events.append(
                {
                    "eventId": "evt_pause_stop_terminal",
                    "invocationId": "inv_pause_stop_1",
                    "type": "haas.turn.interrupted",
                    "haas": {"status": "interrupted"},
                }
            )
        return HaasEnvelope(events, trace_id="tr_pause_stop_events")

    async def get_invocation(
        self, session_id: str, invocation_id: str
    ) -> HaasEnvelope[dict[str, Any]]:
        return HaasEnvelope(
            {
                "id": invocation_id,
                "sessionId": session_id,
                "status": "interrupted" if self.paused.is_set() else "running",
            },
            trace_id="tr_pause_stop_invocation",
        )


class ContinuedDirectRunStream:
    def __init__(self) -> None:
        self.accepted = AcceptedInvocationHeaders(
            "inv_continue_2", "hsess_s1", 1786400000000
        )

    async def __aenter__(self) -> ContinuedDirectRunStream:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def events(self) -> AsyncIterator[dict[str, Any]]:
        yield {
            "id": "evt_continue_text",
            "invocationId": "inv_continue_2",
            "content": {"role": "model", "parts": [{"text": "resumed"}]},
            "actions": {},
        }
        yield {
            "id": "evt_continue_terminal",
            "invocationId": "inv_continue_2",
            "content": {"role": "model", "parts": []},
            "actions": {"stateDelta": {"status": "completed"}},
        }


class ResumeAwareDirectHaasClient(PauseAwareDirectHaasClient):
    def __init__(self, config: HaasDelegationConfig) -> None:
        super().__init__(config)
        self.continue_calls: list[tuple[str, str, str | None]] = []
        self.cancel_calls: list[tuple[str, str]] = []
        self.continued = False

    async def cancel_invocation(
        self, session_id: str, invocation_id: str
    ) -> HaasEnvelope[dict[str, Any]]:
        self.cancel_calls.append((session_id, invocation_id))
        return HaasEnvelope(
            {
                "id": invocation_id,
                "sessionId": session_id,
                "status": "interrupted",
                "sessionControl": {
                    "controlState": "cancelled",
                    "supportsResume": False,
                    "resumableInvocationId": None,
                },
            },
            trace_id="tr_pause_cancel",
        )

    def continue_invocation(
        self,
        session_id: str,
        invocation_id: str,
        *,
        additional_instruction: str | None = None,
    ) -> ContinuedDirectRunStream:
        self.continue_calls.append(
            (session_id, invocation_id, additional_instruction)
        )
        self.continued = True
        return ContinuedDirectRunStream()

    async def events_page(
        self, session_id: str, *, after_event_id: str | None = None, limit: int = 100
    ) -> HaasEnvelope[list[dict[str, Any]]]:
        if self.continued:
            return HaasEnvelope(
                [
                    {
                        "eventId": "evt_continue_terminal",
                        "invocationId": "inv_continue_2",
                        "type": "haas.turn.completed",
                        "haas": {"status": "completed"},
                    }
                ],
                trace_id="tr_continue_events",
            )
        return await super().events_page(session_id, after_event_id=after_event_id, limit=limit)


class PauseResumeDelegatedHaasClient(FakeHaasClient):
    def __init__(self, config: HaasDelegationConfig) -> None:
        super().__init__(config)
        self.paused = asyncio.Event()
        self.pause_calls: list[tuple[str, str]] = []
        self.continue_calls: list[tuple[str, str, str | None]] = []
        self.continued = False

    @asynccontextmanager
    async def run_sse_stream(self, **kwargs):
        accepted = AcceptedInvocationHeaders(
            "inv_delegated_pause_1", kwargs["haas_session_id"], 1786400000000
        )

        async def events() -> AsyncIterator[dict[str, Any]]:
            await asyncio.wait_for(self.paused.wait(), timeout=5)
            yield {
                "id": "evt_delegated_pause_terminal",
                "content": {"role": "model", "parts": []},
                "actions": {"stateDelta": {"status": "interrupted"}},
            }

        yield accepted, events()

    async def pause_invocation(self, session_id: str, invocation_id: str) -> dict[str, Any]:
        self.pause_calls.append((session_id, invocation_id))
        self.paused.set()
        return {
            "id": invocation_id,
            "sessionId": session_id,
            "status": "interrupted",
            "sessionControl": {
                "controlState": "paused",
                "supportsResume": True,
                "resumableInvocationId": invocation_id,
            },
        }

    @asynccontextmanager
    async def continue_invocation(
        self,
        session_id: str,
        invocation_id: str,
        *,
        additional_instruction: str | None = None,
    ):
        self.continue_calls.append((session_id, invocation_id, additional_instruction))
        self.continued = True

        async def events() -> AsyncIterator[dict[str, Any]]:
            yield {
                "id": "evt_delegated_continue_terminal",
                "content": {"role": "model", "parts": [{"text": "resumed"}]},
                "actions": {"stateDelta": {"status": "completed"}},
            }

        yield (
            AcceptedInvocationHeaders(
                "inv_delegated_continue_2", session_id, 1786400000000
            ),
            events(),
        )

    async def events_page(self, haas_session_id: str, **kwargs) -> dict[str, Any]:
        del haas_session_id, kwargs
        invocation_id = (
            "inv_delegated_continue_2" if self.continued else "inv_delegated_pause_1"
        )
        status = "completed" if self.continued else "interrupted"
        event_id = (
            "evt_delegated_continue_terminal"
            if self.continued
            else "evt_delegated_pause_terminal"
        )
        return {
            "events": [
                {
                    "eventId": event_id,
                    "invocationId": invocation_id,
                    "type": f"haas.turn.{status}",
                    "haas": {"status": status},
                }
            ],
            "next_cursor": event_id,
        }

    async def get_invocation(
        self, haas_session_id: str, invocation_id: str
    ) -> dict[str, Any]:
        del haas_session_id
        return {
            "id": invocation_id,
            "status": "completed" if self.continued else "interrupted",
        }


class SilentAcceptedDelegatedClient(FakeHaasClient):
    def __init__(self, config: HaasDelegationConfig) -> None:
        super().__init__(config)
        self.cancelled = asyncio.Event()
        self.cancel_calls: list[tuple[str, str]] = []

    @asynccontextmanager
    async def run_sse_stream(self, **kwargs):
        accepted = AcceptedInvocationHeaders(
            "inv_silent_1", kwargs["haas_session_id"], 1786400000000
        )
        self.accepted_headers = accepted

        async def events() -> AsyncIterator[dict[str, Any]]:
            await asyncio.wait_for(self.cancelled.wait(), timeout=5)
            yield {
                "id": "evt_silent_cancelled",
                "content": {"role": "model", "parts": []},
                "actions": {"stateDelta": {"status": "cancelled"}},
            }

        yield accepted, events()

    async def cancel_invocation(self, session_id: str, invocation_id: str):
        self.cancel_calls.append((session_id, invocation_id))
        self.cancelled.set()
        return {"sessionId": session_id, "invocationId": invocation_id, "status": "running"}

    async def events_page(self, haas_session_id: str, **kwargs) -> dict[str, Any]:
        del haas_session_id, kwargs
        return {
            "events": [
                {
                    "eventId": "evt_silent_cancelled",
                    "invocationId": "inv_silent_1",
                    "type": "haas.turn.cancelled",
                    "haas": {"status": "cancelled"},
                }
            ],
            "next_cursor": "evt_silent_cancelled",
        }


class InteractiveDirectRunStream:
    def __init__(self, client: InteractiveDirectHaasClient) -> None:
        self.accepted = AcceptedInvocationHeaders("inv_interactive_1", "hsess_s1", 1786400000000)
        self._client = client

    async def __aenter__(self) -> InteractiveDirectRunStream:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def events(self) -> AsyncIterator[dict[str, Any]]:
        await asyncio.wait_for(self._client.input_answered.wait(), timeout=5)
        yield {
            "id": "evt_interactive_terminal",
            "invocationId": "inv_interactive_1",
            "content": {"role": "model", "parts": [{"text": "done"}]},
            "actions": {"stateDelta": {"status": "completed"}},
        }


class InteractiveDirectHaasClient(FakeDirectHaasClient):
    def __init__(self, config: HaasDelegationConfig) -> None:
        super().__init__(config)
        self.approval_resolved = asyncio.Event()
        self.input_answered = asyncio.Event()
        self.approval_calls: list[tuple[str, str, bool]] = []
        self.input_calls: list[tuple[str, str, dict[str, dict[str, Any]]]] = []

    def run_sse(self, body: dict[str, Any], *, idempotency_key: str, last_event_id=None):
        del last_event_id
        self.runs.append({"body": body, "idempotency_key": idempotency_key})
        return InteractiveDirectRunStream(self)

    async def events_page(
        self, session_id: str, *, after_event_id: str | None = None, limit: int = 100
    ) -> HaasEnvelope[list[dict[str, Any]]]:
        del session_id, limit
        events = [
            {
                "eventId": "evt_interactive_approval",
                "invocationId": "inv_interactive_1",
                "type": "haas.approval.required",
                "haas": {
                    "approvalId": "appr_interactive_1",
                    "kind": "command",
                    "safeSummary": "Run command",
                },
            }
        ]
        if self.approval_resolved.is_set():
            events.extend(
                [
                    {
                        "eventId": "evt_interactive_approval_resolved",
                        "invocationId": "inv_interactive_1",
                        "type": "haas.approval.resolved",
                        "haas": {
                            "approvalId": "appr_interactive_1",
                            "status": "approved",
                        },
                    },
                    {
                        "eventId": "evt_interactive_input",
                        "invocationId": "inv_interactive_1",
                        "type": "haas.input.required",
                        "haas": {
                            "inputRequestId": "inreq_interactive_1",
                            "questions": [
                                {
                                    "id": "scope",
                                    "header": "Scope",
                                    "question": "Which scope?",
                                    "options": [{"label": "Current diff"}],
                                }
                            ],
                        },
                    },
                ]
            )
        if self.input_answered.is_set():
            events.extend(
                [
                    {
                        "eventId": "evt_interactive_input_resolved",
                        "invocationId": "inv_interactive_1",
                        "type": "haas.input.resolved",
                        "haas": {
                            "inputRequestId": "inreq_interactive_1",
                            "status": "answered",
                        },
                    },
                    {
                        "eventId": "evt_interactive_terminal",
                        "invocationId": "inv_interactive_1",
                        "type": "haas.turn.completed",
                        "haas": {"status": "completed"},
                    },
                ]
            )
        if after_event_id is not None:
            ids = [event["eventId"] for event in events]
            events = events[ids.index(after_event_id) + 1 :] if after_event_id in ids else []
        return HaasEnvelope(events, trace_id="tr_interactive")

    async def resolve_approval(
        self, session_id: str, approval_id: str, *, approved: bool
    ) -> HaasEnvelope[dict[str, Any]]:
        self.approval_calls.append((session_id, approval_id, approved))
        self.approval_resolved.set()
        return HaasEnvelope({"status": "approved"}, trace_id="tr_approval")

    async def list_approvals(
        self, session_id: str, *, status: str = "waiting"
    ) -> HaasEnvelope[list[dict[str, Any]]]:
        del session_id, status
        return HaasEnvelope(
            []
            if self.approval_resolved.is_set()
            else [
                {
                    "approvalId": "appr_interactive_1",
                    "request": {"kind": "command", "safeSummary": "Run command"},
                }
            ],
            trace_id="tr_approvals",
        )

    async def list_input_requests(
        self, session_id: str, *, status: str = "waiting"
    ) -> HaasEnvelope[list[dict[str, Any]]]:
        del session_id, status
        return HaasEnvelope([], trace_id="tr_inputs")

    async def answer_input_request(
        self,
        session_id: str,
        input_request_id: str,
        *,
        answers: dict[str, dict[str, Any]],
    ) -> HaasEnvelope[dict[str, Any]]:
        self.input_calls.append((session_id, input_request_id, answers))
        self.input_answered.set()
        return HaasEnvelope({"status": "answered"}, trace_id="tr_input")


class PlannedDirectRunStream:
    def __init__(self, run_number: int) -> None:
        self.run_number = run_number
        self.accepted = AcceptedInvocationHeaders(
            f"inv_plan_{run_number}", "hsess_s1", 1786400000000
        )

    async def __aenter__(self) -> PlannedDirectRunStream:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def events(self) -> AsyncIterator[dict[str, Any]]:
        yield {
            "id": f"evt_plan_terminal_{self.run_number}",
            "invocationId": f"inv_plan_{self.run_number}",
            "content": {"role": "model", "parts": [{"text": "progress"}]},
            "actions": {"stateDelta": {"status": "completed"}},
        }


class PlannedDirectHaasClient(FakeDirectHaasClient):
    def run_sse(self, body: dict[str, Any], *, idempotency_key: str, last_event_id=None):
        del last_event_id
        self.runs.append({"body": body, "idempotency_key": idempotency_key})
        return PlannedDirectRunStream(len(self.runs))

    async def events_page(
        self, session_id: str, *, after_event_id: str | None = None, limit: int = 100
    ) -> HaasEnvelope[list[dict[str, Any]]]:
        del session_id, after_event_id, limit
        run_number = len(self.runs)
        pending = 1 if run_number == 1 else 0
        return HaasEnvelope(
            [
                {
                    "eventId": f"evt_plan_state_{run_number}",
                    "invocationId": f"inv_plan_{run_number}",
                    "type": "haas.plan.updated",
                    "haas": {
                        "counts": {
                            "pending": pending,
                            "inProgress": 0,
                            "completed": 2 - pending,
                        },
                        "total": 2,
                    },
                },
                {
                    "eventId": f"evt_plan_terminal_{run_number}",
                    "invocationId": f"inv_plan_{run_number}",
                    "type": "haas.turn.completed",
                    "haas": {"status": "completed"},
                },
            ],
            trace_id="tr_plan",
        )


class NeverFinishedPlanClient(PlannedDirectHaasClient):
    async def events_page(
        self, session_id: str, *, after_event_id: str | None = None, limit: int = 100
    ) -> HaasEnvelope[list[dict[str, Any]]]:
        result = await super().events_page(session_id, after_event_id=after_event_id, limit=limit)
        for event in result.data:
            if event["type"] == "haas.plan.updated":
                event["haas"]["counts"] = {
                    "pending": 1,
                    "inProgress": 0,
                    "completed": 1,
                }
        return result


class FakeLocalHaasSupervisor:
    def __init__(self) -> None:
        self.ensured: list[HaasDelegationConfig] = []
        self.stopped = 0

    def grant_credential(self, provider, scope):
        self.credential_scope = scope
        return "secret://manager/fixture"

    def ensure(self, config: HaasDelegationConfig) -> dict[str, Any]:
        self.ensured.append(config)
        return {
            "enabled": config.local_autostart,
            "status": "running" if config.local_autostart and config.enabled else "disabled",
            "running": config.local_autostart and config.enabled,
            "managed": config.local_autostart and config.enabled,
            "pid": 123 if config.local_autostart and config.enabled else None,
            "url": config.base_url,
            "reason": None,
        }

    def status(self, config: HaasDelegationConfig) -> dict[str, Any]:
        return {
            "enabled": config.local_autostart,
            "status": "running" if config.local_autostart and config.enabled else "disabled",
            "running": config.local_autostart and config.enabled,
            "managed": config.local_autostart and config.enabled,
            "pid": 123 if config.local_autostart and config.enabled else None,
            "url": config.base_url,
            "reason": None,
        }

    def stop(self) -> None:
        self.stopped += 1


class UnownedLocalHaasSupervisor(FakeLocalHaasSupervisor):
    def ensure(self, config: HaasDelegationConfig) -> dict[str, Any]:
        self.ensured.append(config)
        return {
            "enabled": True,
            "status": "stopped",
            "running": False,
            "managed": False,
            "pid": None,
            "url": config.base_url,
            "reason": "local_sidecar_not_owned",
        }


def _haas_config() -> HaasDelegationConfig:
    return HaasDelegationConfig(
        enabled=True,
        execution_mode="delegated_session",
        api_token="test-haas-token",
        image_digest="sha256:test",
        trigger_keywords=["fix"],
    )


def _local_api_config() -> HaasDelegationConfig:
    return HaasDelegationConfig(
        enabled=True,
        execution_mode="local_api",
        api_token="test-haas-token",
        trigger_keywords=["fix"],
    )


def test_legacy_delegated_binding_without_policy_snapshot_fails_closed() -> None:
    with pytest.raises(HaasDelegationError, match="immutable policy snapshot"):
        apply_config_snapshot(
            _haas_config(),
            {
                "execution_mode": "delegated_session",
                "delegated_session_id": "dgsess_legacy",
            },
        )


def test_deterministic_delegation_defaults_to_haas_without_keyword_gate(tmp_path):
    cfg = _haas_config()
    assert (
        deterministic_decision(
            config=cfg,
            session_id="s1",
            agent="code",
            workspace=str(tmp_path),
            workspace_trusted=False,
            content="fix this",
        ).backend
        == "blocked"
    )
    assert deterministic_decision(
        config=cfg,
        session_id="s1",
        agent="code",
        workspace=str(tmp_path),
        workspace_trusted=True,
        content="fix this",
    ).use_haas
    assert deterministic_decision(
        config=cfg,
        session_id="s1",
        agent="code",
        workspace=str(tmp_path),
        workspace_trusted=True,
        content="hello",
    ).use_haas


def test_local_api_default_skips_delegated_agent_allowlist(tmp_path):
    cfg = _local_api_config()
    cfg.agent_allowlist = ["code"]

    decision = deterministic_decision(
        config=cfg,
        session_id="s1",
        agent="cowork",
        workspace=str(tmp_path),
        workspace_trusted=False,
        content="hello",
    )

    assert decision.use_haas
    assert decision.reason == "local_api_default"


def test_remote_local_api_is_blocked_before_workspace_leak(tmp_path):
    cfg = _local_api_config()
    cfg.mode = "remote"
    cfg.base_url = "https://haas.example.com"

    decision = deterministic_decision(
        config=cfg,
        session_id="s1",
        agent="cowork",
        workspace=str(tmp_path),
        workspace_trusted=True,
        content="hello",
    )

    assert decision.backend == "blocked"
    assert decision.reason == "remote_local_api_unsupported"


def test_deterministic_delegation_honors_explicit_local_choice(tmp_path):
    cfg = _haas_config()
    cfg.backend_preference = "local"
    decision = deterministic_decision(
        config=cfg,
        session_id="s1",
        agent="code",
        workspace=str(tmp_path),
        workspace_trusted=True,
        content="fix this",
    )
    assert decision.backend == "local"
    assert decision.reason == "explicit_local_choice"


def test_remote_endpoint_blocks_bind_mount_before_create(tmp_path):
    cfg = _haas_config()
    cfg.mode = "remote"
    cfg.base_url = "https://haas.example.com"
    decision = deterministic_decision(
        config=cfg,
        session_id="s1",
        agent="code",
        workspace=str(tmp_path),
        workspace_trusted=True,
        content="fix this",
    )
    assert decision.backend == "blocked"
    assert decision.reason == "remote_bind_mount_unsupported"


def test_make_delegated_session_body_mounts_workspace_rw(tmp_path):
    body = make_delegated_session_body(
        config=_haas_config(),
        manager_session_id="s1",
        workspace=str(tmp_path),
        model="volcengine-ark:doubao-seed-2.1-turbo",
        extra_roots=[{"path": str(tmp_path), "writable": True}],
    )
    assert body["mountManifest"]["primaryWorkspace"] == {
        "hostPathCanonical": str(tmp_path.resolve()),
        "containerPath": "/workspace",
        "access": "rw",
    }
    assert body["mountManifest"]["extraMounts"][0]["access"] == "ro"
    assert body["provider"] == {
        "providerId": "volcengine-ark",
        "model": "doubao-seed-2.1-turbo",
        "credentialRef": "secret://provider/volcengine-ark",
    }
    assert body["delegationPolicySnapshot"]["network"] == {
        "defaultAction": "allow",
        "allow": [],
    }
    assert body["delegationPolicySnapshot"]["tools"] == {
        "disabled": [],
        "approvalMode": "on-request",
    }

    allowed = _haas_config()
    allowed.network_access = True
    allowed_body = make_delegated_session_body(
        config=allowed,
        manager_session_id="s2",
        workspace=str(tmp_path),
        model="openai:gpt-test",
    )
    assert allowed_body["delegationPolicySnapshot"]["network"] == {
        "defaultAction": "allow",
        "allow": [],
    }


def test_adk_assistant_text_excludes_thoughts() -> None:
    from coworker.delegation import extract_adk_text

    event = {
        "content": {
            "parts": [
                {"text": "reasoning-only", "thought": True},
                {"text": "answer"},
                {"text": " text", "thought": False},
            ]
        }
    }
    assert extract_adk_text(event) == "answer text"
    assert event["content"]["parts"][0]["text"] == "reasoning-only"


def test_adk_message_accepts_text_only_parts() -> None:
    assert adk_message_from_content("hello") == {
        "role": "user",
        "parts": [{"text": "hello"}],
    }
    assert is_text_only_content([{"type": "text", "text": "hello"}])
    assert not is_text_only_content(
        [{"type": "image_url", "image_url": {"url": "data:image/png;base64,aaa"}}]
    )


def test_ws_delegates_first_matching_turn_and_persists_binding(tmp_path, monkeypatch):
    monkeypatch.setattr(SessionManager, "_haas_config", lambda self, workspace: _haas_config())
    monkeypatch.setattr(SessionManager, "_maybe_autotitle", lambda self, session_id: None)
    clients: list[FakeHaasClient] = []

    def factory(config: HaasDelegationConfig) -> FakeHaasClient:
        client = FakeHaasClient(config)
        clients.append(client)
        return client

    provider = ScriptedProvider()
    manager = SessionManager(workspace=tmp_path, provider=provider, haas_client_factory=factory)
    manager.workspace_trust.set_trusted(tmp_path, True)
    client = TestClient(create_app(manager))

    with client.websocket_connect("/ws/session/s1?agent=code") as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"type": "user_message", "text": "fix this", "model": "volcengine-ark:m1"})
        events = []
        while True:
            event = ws.receive_json()
            events.append(event)
            if event["type"] == "turn_done":
                break

    assert provider.calls == 0
    assert [event["type"] for event in events] == [
        "turn_start",
        "execution_control",
        "assistant_delta",
        "assistant_delta",
        "assistant_message",
        "turn_end",
        "execution_control",
        "turn_done",
    ]
    assert events[1]["data"]["controlState"] == "running"
    assert events[1]["data"]["pauseSupported"] is True
    assert events[-2]["data"]["controlState"] == "idle"
    assert clients[0].created[0]["managerSessionId"] == "s1"
    assert clients[0].created[0]["mountManifest"]["primaryWorkspace"]["access"] == "rw"
    assert clients[0].restored == ["dgsess_1"]
    assert clients[0].runs[0]["haas_session_id"] == "hsess_s1"
    assert clients[0].runs[0]["harness_id"] == "chrn_codex_default"
    assert clients[0].runs[0]["user_id"] == "manager"
    record = manager.session_store.load("s1")
    assert record is not None
    binding = record.bindings["haas_delegation"]
    assert binding["binding"] == "haas_bound"
    assert binding["accepted_invocation_id"] == "inv_1"
    assert binding["delegated_session_id"] == "dgsess_1"
    assert binding["haas_base_url"] == "http://127.0.0.1:8092"
    assert binding["runtime"]["status"] == "running"
    sessions = client.get("/v1/sessions").json()["sessions"]
    row = next(s for s in sessions if s["session_id"] == "s1")
    assert row["delegation"] == {
        "backend": "haas",
        "execution_mode": "delegated_session",
        "delegated_session_id": "dgsess_1",
        "haas_session_id": "hsess_s1",
        "runtime_status": "running",
        "container_generation": 1,
    }
    messages = client.get("/v1/sessions/s1/messages").json()["messages"]
    assert messages[-1]["role"] == "assistant"
    assert messages[-1]["content"] == "remote done"

    evidence = client.get(
        "/v1/sessions/s1/execution-evidence",
        params={
            "invocation_id": "inv_1",
            "tool_call_id": "call_1",
            "evidence_ref": "evd_1",
        },
    )
    assert evidence.status_code == 200
    assert evidence.headers["cache-control"] == "no-store"
    assert evidence.headers["referrer-policy"] == "no-referrer"
    assert evidence.json()["data"]["command"] == "acme auth login"


@pytest.mark.parametrize("missing_native", [False, True])
def test_ws_default_uses_local_haas_api_without_delegated_agent_gate(
    tmp_path, monkeypatch, missing_native
):
    cfg = _local_api_config()
    cfg.agent_allowlist = ["code"]
    if missing_native:

        async def empty_events(self, *args, **kwargs):
            return HaasEnvelope([], trace_id="tr_empty")

        async def failed_invocation(self, *args, **kwargs):
            return HaasEnvelope({"status": "failed"}, trace_id="tr_failed")

        monkeypatch.setattr(FakeDirectHaasClient, "events_page", empty_events)
        monkeypatch.setattr(
            FakeDirectHaasClient, "get_invocation", failed_invocation, raising=False
        )
    monkeypatch.setattr(SessionManager, "_haas_config", lambda self, workspace: cfg)
    monkeypatch.setattr(SessionManager, "_maybe_autotitle", lambda self, session_id: None)
    direct_clients: list[FakeDirectHaasClient] = []
    supervisor = FakeLocalHaasSupervisor()

    def direct_factory(config: HaasDelegationConfig) -> FakeDirectHaasClient:
        client = FakeDirectHaasClient(config)
        direct_clients.append(client)
        return client

    manager = SessionManager(
        workspace=tmp_path,
        provider=ScriptedProvider(),
        haas_client_factory=FakeHaasClient,
    )
    manager._haas_supervisor = supervisor
    manager._haas_direct_client_factory = direct_factory
    client = TestClient(create_app(manager))

    with client.websocket_connect("/ws/session/s1?agent=cowork") as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"type": "user_message", "text": "hello from packaged app"})
        events = []
        while True:
            event = ws.receive_json()
            events.append(event)
            if event["type"] == "turn_done":
                break

    if missing_native:
        assert any(event["type"] == "error" for event in events)
        assert not any(event["type"] == "turn_end" for event in events)
        return
    assert [event["type"] for event in events] == [
        "turn_start",
        "execution_control",
        "assistant_delta",
        "reasoning_delta",
        "tool_proposed",
        "tool_started",
        "assistant_delta",
        "tool_finished",
        "assistant_message",
        "turn_end",
        "execution_control",
        "turn_done",
    ]
    assert events[1]["data"]["controlState"] == "running"
    assert events[1]["data"]["pauseSupported"] is True
    assert events[-2]["data"]["controlState"] == "idle"
    process_events = [
        event
        for event in events
        if event["type"] in {"reasoning_delta", "tool_proposed", "tool_started", "tool_finished"}
    ]
    assert process_events
    assert all(event["data"].get("haasEventId") for event in process_events)
    assert direct_clients
    assert direct_clients[0].runs[0]["body"]["appName"] == "chrn_codex_default"
    assert direct_clients[0].runs[0]["body"]["sessionId"] == "hsess_s1"
    assert direct_clients[0].runs[0]["body"]["haas"] == {
        "profileId": "hprof_direct",
        "profileVersion": 1,
    }
    assert direct_clients[0].runs[0]["body"]["policy"] == {
        "approvalPolicy": "on-request",
        "network": {"defaultAction": "allow", "allow": []},
    }
    provider = direct_clients[0].profile_configuration["provider"]
    assert provider["providerId"] == "openai"
    assert provider["apiType"] == "responses"
    assert provider["credentialRef"] == "secret://manager/fixture"
    assert supervisor.credential_scope["sessionId"] == "hsess_s1"
    assert "api_key" not in provider
    assert direct_clients[0].runs[0]["body"]["sandbox"]["workspaceRoot"] == str(
        tmp_path.resolve()
    )
    assert direct_clients[0].runs[0]["idempotency_key"].startswith("manager-turn:s1:")
    assert supervisor.ensured[-1].execution_mode == "local_api"
    binding = manager.session_store.load("s1").bindings["haas_delegation"]
    assert binding["execution_mode"] == "local_api"
    assert "delegated_session_id" not in binding
    messages = client.get("/v1/sessions/s1/messages").json()["messages"]
    assert messages[-1]["role"] == "assistant"
    assert messages[-1]["content"] == "local haas done"


@pytest.mark.asyncio
async def test_local_turn_reports_supervisor_ownership_failure_before_credential_grant(
    tmp_path, monkeypatch
):
    cfg = _local_api_config()
    monkeypatch.setattr(SessionManager, "_haas_config", lambda self, workspace: cfg)
    manager = SessionManager(workspace=tmp_path, provider=ScriptedProvider())
    manager._haas_supervisor = UnownedLocalHaasSupervisor()
    engine = manager.get_engine("s1", workspace=str(tmp_path), agent="cowork")
    assert engine is not None

    with pytest.raises(HaasDelegationError, match="local_sidecar_not_owned"):
        async for _event in manager._run_haas_local_api_turn(
            "s1",
            engine,
            "retry",
            config=cfg,
            binding=None,
            append_user=True,
            display=None,
        ):
            pass

    assert not hasattr(manager._haas_supervisor, "credential_scope")


@pytest.mark.asyncio
async def test_local_terminal_reconciliation_advances_history_and_closes_stalled_adk(
    tmp_path, monkeypatch
):
    cfg = _local_api_config()
    monkeypatch.setattr(SessionManager, "_haas_config", lambda self, workspace: cfg)
    direct = TerminalReconciliationDirectHaasClient(cfg)
    manager = SessionManager(workspace=tmp_path, provider=ScriptedProvider())
    manager._haas_supervisor = FakeLocalHaasSupervisor()
    manager._haas_direct_client_factory = lambda _config: direct
    engine = manager.get_engine("s1", workspace=str(tmp_path), agent="cowork")
    assert engine is not None
    manager.save("s1", engine)

    async def collect():
        return [
            event
            async for event in manager._run_haas_local_api_turn(
                "s1",
                engine,
                "inspect resources",
                config=cfg,
                binding=None,
                append_user=True,
                display=None,
            )
        ]

    events = await asyncio.wait_for(collect(), timeout=1)

    assert direct.page_cursors[:2] == [None, "evt_history_099"]
    assert [event.type.value for event in events].count("turn_end") == 1
    turn_end = next(event for event in events if event.type.value == "turn_end")
    assert turn_end.data == {
        "status": "failed",
        "code": "haas_provider_error",
        "safeReason": "provider unavailable",
        "taskPhase": "failed",
        "retryable": False,
        "haasEventId": "evt_native_failed",
        "iterations": 1,
        "delegated": {
            "backend": "haas",
            "session": "hsess_s1",
            "haas_session_id": "hsess_s1",
            "execution_mode": "local_api",
        },
    }
    stored = manager.session_store.load("s1").bindings["haas_delegation"]
    assert stored["control_state"] == "idle"
    assert stored["stream_bridge"]["completed"] is True
    assert stored["stream_bridge"]["cursors"]["native"] == "evt_native_failed"
    attempt_id = stored["current_attempt_id"]
    attempt = next(
        item
        for item in stored["attempt_ledger"]["attempts"]
        if item["attemptId"] == attempt_id
    )
    assert attempt["terminal"] is True


def test_local_haas_network_setting_projects_allow_policy(tmp_path, monkeypatch):
    cfg = _local_api_config()
    cfg.network_access = True
    monkeypatch.setattr(SessionManager, "_haas_config", lambda self, workspace: cfg)
    monkeypatch.setattr(SessionManager, "_maybe_autotitle", lambda self, session_id: None)
    direct = FakeDirectHaasClient(cfg)
    manager = SessionManager(workspace=tmp_path, provider=ScriptedProvider())
    manager._haas_supervisor = FakeLocalHaasSupervisor()
    manager._haas_direct_client_factory = lambda _config: direct

    with TestClient(create_app(manager)).websocket_connect(
        "/ws/session/s1?agent=cowork"
    ) as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"type": "user_message", "text": "fetch example"})
        while ws.receive_json()["type"] != "turn_done":
            pass

    assert direct.runs[0]["body"]["policy"]["network"] == {
        "defaultAction": "allow",
        "allow": [],
    }


def test_ws_stop_cancels_active_local_haas_invocation_and_waits_for_terminal(
    tmp_path, monkeypatch
):
    cfg = _local_api_config()
    monkeypatch.setattr(SessionManager, "_haas_config", lambda self, workspace: cfg)
    monkeypatch.setattr(SessionManager, "_maybe_autotitle", lambda self, session_id: None)
    direct_clients: list[CancelAwareDirectHaasClient] = []
    manager = SessionManager(
        workspace=tmp_path,
        provider=ScriptedProvider(),
        haas_client_factory=FakeHaasClient,
    )
    manager._haas_supervisor = FakeLocalHaasSupervisor()
    manager._haas_direct_client_factory = lambda config: (
        direct_clients.append(CancelAwareDirectHaasClient(config)) or direct_clients[-1]
    )

    with TestClient(create_app(manager)).websocket_connect(
        "/ws/session/s1?agent=cowork"
    ) as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"type": "user_message", "text": "run until stopped"})
        assert ws.receive_json()["type"] == "turn_start"
        ws.send_json({"type": "interrupt"})
        events = []
        while True:
            event = ws.receive_json()
            events.append(event)
            if event["type"] == "turn_done":
                break

    assert direct_clients[0].cancel_calls == [("hsess_s1", "inv_cancel_1")]
    turn_end = next(event for event in events if event["type"] == "turn_end")
    assert turn_end["data"]["status"] == "cancelled"
    terminal_control = [
        event for event in events if event["type"] == "execution_control"
    ][-1]
    assert terminal_control["data"]["controlState"] == "idle"
    assert events.index(terminal_control) < next(
        index for index, event in enumerate(events) if event["type"] == "turn_done"
    )
    binding = manager.session_store.load("s1").bindings["haas_delegation"]
    assert binding["stream_bridge"]["terminalStatus"] == "cancelled"


def test_ws_rejected_active_stop_restores_running_control_state(tmp_path, monkeypatch):
    cfg = _local_api_config()
    monkeypatch.setattr(SessionManager, "_haas_config", lambda self, workspace: cfg)
    monkeypatch.setattr(SessionManager, "_maybe_autotitle", lambda self, session_id: None)
    direct = CancelAwareDirectHaasClient(cfg)

    async def reject_cancel(session_id: str, invocation_id: str):
        direct.cancel_calls.append((session_id, invocation_id))
        raise HaasClientError("cancel rejected")

    direct.cancel_invocation = reject_cancel  # type: ignore[method-assign]
    manager = SessionManager(workspace=tmp_path, provider=ScriptedProvider())
    manager._haas_supervisor = FakeLocalHaasSupervisor()
    manager._haas_direct_client_factory = lambda _config: direct

    with TestClient(create_app(manager)).websocket_connect(
        "/ws/session/s1?agent=cowork"
    ) as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"type": "user_message", "text": "run until stop fails"})
        assert ws.receive_json()["type"] == "turn_start"
        ws.send_json({"type": "interrupt"})
        restored = ws.receive_json()

    assert restored["type"] == "execution_control"
    assert restored["data"]["controlState"] == "running"
    assert manager.haas_control_state("s1")["controlState"] == "running"


def test_ws_pause_interrupts_active_local_haas_invocation_and_restores_paused_state(
    tmp_path, monkeypatch
):
    cfg = _local_api_config()
    monkeypatch.setattr(SessionManager, "_haas_config", lambda self, workspace: cfg)
    monkeypatch.setattr(SessionManager, "_maybe_autotitle", lambda self, session_id: None)
    direct_clients: list[PauseAwareDirectHaasClient] = []
    manager = SessionManager(
        workspace=tmp_path,
        provider=ScriptedProvider(),
        haas_client_factory=FakeHaasClient,
    )
    manager._haas_supervisor = FakeLocalHaasSupervisor()
    manager._haas_direct_client_factory = lambda config: (
        direct_clients.append(PauseAwareDirectHaasClient(config)) or direct_clients[-1]
    )

    app = create_app(manager)
    with TestClient(app).websocket_connect("/ws/session/s1?agent=cowork") as ws:
        ready = ws.receive_json()
        assert ready["type"] == "ready"
        ws.send_json({"type": "user_message", "text": "run until paused"})
        assert ws.receive_json()["type"] == "turn_start"
        ws.send_json({"type": "pause"})
        events = []
        while True:
            event = ws.receive_json()
            events.append(event)
            if event["type"] == "turn_done":
                break

    assert direct_clients[0].pause_calls == [("hsess_s1", "inv_pause_1")]
    control_states = [
        event["data"]["controlState"]
        for event in events
        if event["type"] == "execution_control"
    ]
    assert control_states == ["running", "pausing", "paused"]
    turn_end = next(event for event in events if event["type"] == "turn_end")
    assert turn_end["data"]["status"] == "interrupted"
    binding = manager.session_store.load("s1").bindings["haas_delegation"]
    assert binding["control_state"] == "paused"
    assert binding["supports_resume"] is True
    assert binding["resumable_invocation_id"] == "inv_pause_1"

    with TestClient(app).websocket_connect("/ws/session/s1?agent=cowork") as ws:
        ready = ws.receive_json()
        assert ready["type"] == "ready"
        assert ready["data"]["execution_control"] == {
            "controlState": "paused",
            "supportsResume": True,
            "resumableInvocationId": "inv_pause_1",
            "pauseSupported": True,
        }


def test_ws_stop_supersedes_inflight_pause_without_blocking_receive_loop(
    tmp_path, monkeypatch
):
    cfg = _local_api_config()
    monkeypatch.setattr(SessionManager, "_haas_config", lambda self, workspace: cfg)
    monkeypatch.setattr(SessionManager, "_maybe_autotitle", lambda self, session_id: None)
    direct = PauseStopRaceDirectHaasClient(cfg)
    manager = SessionManager(workspace=tmp_path, provider=ScriptedProvider())
    manager._haas_supervisor = FakeLocalHaasSupervisor()
    manager._haas_direct_client_factory = lambda _config: direct

    with TestClient(create_app(manager)).websocket_connect(
        "/ws/session/s1?agent=cowork"
    ) as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"type": "user_message", "text": "run until stopped"})
        assert ws.receive_json()["type"] == "turn_start"
        assert ws.receive_json()["type"] == "execution_control"
        ws.send_json({"type": "pause"})
        pausing = ws.receive_json()
        assert pausing["data"]["controlState"] == "pausing"
        ws.send_json({"type": "interrupt"})
        events = []
        while True:
            event = ws.receive_json()
            events.append(event)
            if event["type"] == "turn_done":
                break

    assert direct.pause_calls == [("hsess_s1", "inv_cancel_1")]
    assert direct.cancel_calls == [("hsess_s1", "inv_cancel_1")]
    assert next(event for event in events if event["type"] == "turn_end")["data"][
        "status"
    ] == "cancelled"
    assert manager.haas_control_state("s1")["controlState"] == "idle"


def test_ws_rejected_pause_restores_running_control_state(tmp_path, monkeypatch):
    cfg = _local_api_config()
    monkeypatch.setattr(SessionManager, "_haas_config", lambda self, workspace: cfg)
    monkeypatch.setattr(SessionManager, "_maybe_autotitle", lambda self, session_id: None)
    direct = PauseAwareDirectHaasClient(cfg)

    async def reject_pause(session_id: str, invocation_id: str):
        direct.pause_calls.append((session_id, invocation_id))
        raise HaasClientError("pause rejected")

    direct.pause_invocation = reject_pause  # type: ignore[method-assign]
    manager = SessionManager(workspace=tmp_path, provider=ScriptedProvider())
    manager._haas_supervisor = FakeLocalHaasSupervisor()
    manager._haas_direct_client_factory = lambda _config: direct

    with TestClient(create_app(manager)).websocket_connect(
        "/ws/session/s1?agent=cowork"
    ) as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"type": "user_message", "text": "run until pause fails"})
        assert ws.receive_json()["type"] == "turn_start"
        running = ws.receive_json()
        assert running["type"] == "execution_control"
        assert running["data"] == {
            "controlState": "running",
            "supportsResume": False,
            "resumableInvocationId": None,
            "pauseSupported": True,
        }
        ws.send_json({"type": "pause"})
        assert ws.receive_json() == {
            "type": "execution_control",
            "data": {
                "controlState": "pausing",
                "supportsResume": False,
                "resumableInvocationId": None,
            },
        }
        restored = ws.receive_json()

    assert restored["type"] == "execution_control"
    assert restored["data"]["controlState"] == "running"
    assert manager.haas_control_state("s1")["controlState"] == "running"


def test_ws_stop_while_paused_revokes_resume_without_reopening_source_turn(
    tmp_path, monkeypatch
):
    cfg = _local_api_config()
    monkeypatch.setattr(SessionManager, "_haas_config", lambda self, workspace: cfg)
    monkeypatch.setattr(SessionManager, "_maybe_autotitle", lambda self, session_id: None)
    direct = ResumeAwareDirectHaasClient(cfg)
    manager = SessionManager(workspace=tmp_path, provider=ScriptedProvider())
    manager._haas_supervisor = FakeLocalHaasSupervisor()
    manager._haas_direct_client_factory = lambda _config: direct

    with TestClient(create_app(manager)).websocket_connect(
        "/ws/session/s1?agent=cowork"
    ) as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"type": "user_message", "text": "run until paused"})
        assert ws.receive_json()["type"] == "turn_start"
        ws.send_json({"type": "pause"})
        while ws.receive_json()["type"] != "turn_done":
            pass
        ws.send_json({"type": "interrupt"})
        control = ws.receive_json()

    assert direct.cancel_calls == [("hsess_s1", "inv_pause_1")]
    assert control == {
        "type": "execution_control",
        "data": {
            "controlState": "idle",
            "supportsResume": False,
            "resumableInvocationId": None,
            "pauseSupported": True,
        },
    }
    binding = manager.session_store.load("s1").bindings["haas_delegation"]
    assert binding["stream_bridge"]["terminalStatus"] == "interrupted"
    assert binding["control_state"] == "cancelled"


def test_ws_stop_after_paused_readback_before_turn_done_publishes_idle(
    tmp_path, monkeypatch
):
    """A paused Stop must not remain `stopping` until the old run unwinds."""
    cfg = _local_api_config()
    monkeypatch.setattr(SessionManager, "_haas_config", lambda self, workspace: cfg)
    monkeypatch.setattr(SessionManager, "_maybe_autotitle", lambda self, session_id: None)
    direct = PausedReadbackBeforeTurnDoneClient(cfg)
    manager = SessionManager(workspace=tmp_path, provider=ScriptedProvider())
    manager._haas_supervisor = FakeLocalHaasSupervisor()
    manager._haas_direct_client_factory = lambda _config: direct

    with TestClient(create_app(manager)).websocket_connect(
        "/ws/session/s1?agent=cowork"
    ) as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"type": "user_message", "text": "run until paused"})
        assert ws.receive_json()["type"] == "turn_start"
        assert ws.receive_json()["data"]["controlState"] == "running"
        ws.send_json({"type": "pause"})
        assert ws.receive_json()["data"]["controlState"] == "pausing"
        assert ws.receive_json()["data"]["controlState"] == "paused"

        # The source run has yielded interrupted but deliberately has not reached
        # its finally block yet, matching the real UI race found in live testing.
        ws.send_json({"type": "interrupt"})
        after_stop = []
        while True:
            event = ws.receive_json()
            after_stop.append(event)
            if event["type"] == "turn_done":
                break

        assert {
            "type": "execution_control",
            "data": {
                "controlState": "idle",
                "supportsResume": False,
                "resumableInvocationId": None,
                "pauseSupported": True,
            },
        } in after_stop

    assert direct.cancel_calls == [("hsess_s1", "inv_pause_stop_1")]
    binding = manager.session_store.load("s1").bindings["haas_delegation"]
    assert binding["control_state"] == "cancelled"
    assert binding["supports_resume"] is False


def test_ws_continue_resumes_same_haas_session_with_a_new_invocation(
    tmp_path, monkeypatch
):
    cfg = _local_api_config()
    monkeypatch.setattr(SessionManager, "_haas_config", lambda self, workspace: cfg)
    monkeypatch.setattr(SessionManager, "_maybe_autotitle", lambda self, session_id: None)
    direct = ResumeAwareDirectHaasClient(cfg)
    manager = SessionManager(
        workspace=tmp_path,
        provider=ScriptedProvider(),
        haas_client_factory=FakeHaasClient,
    )
    manager._haas_supervisor = FakeLocalHaasSupervisor()
    manager._haas_direct_client_factory = lambda _config: direct

    with TestClient(create_app(manager)).websocket_connect(
        "/ws/session/s1?agent=cowork"
    ) as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"type": "user_message", "text": "run until paused"})
        assert ws.receive_json()["type"] == "turn_start"
        ws.send_json({"type": "pause"})
        while ws.receive_json()["type"] != "turn_done":
            pass

        ws.send_json({"type": "continue"})
        continued_events = []
        while True:
            event = ws.receive_json()
            continued_events.append(event)
            if event["type"] == "turn_done":
                break

    assert direct.continue_calls == [("hsess_s1", "inv_pause_1", None)]
    assert next(
        event for event in continued_events if event["type"] == "execution_control"
    )["data"]["controlState"] == "resuming"
    turn_start = next(event for event in continued_events if event["type"] == "turn_start")
    assert turn_start["data"]["delegated"]["invocation"] == "inv_continue_2"
    assert turn_start["data"]["continuedFromInvocationId"] == "inv_pause_1"
    turn_end = next(event for event in continued_events if event["type"] == "turn_end")
    assert turn_end["data"]["status"] == "completed"
    binding = manager.session_store.load("s1").bindings["haas_delegation"]
    assert binding["haas_session_id"] == "hsess_s1"
    assert binding["accepted_invocation_id"] == "inv_continue_2"
    assert binding["control_state"] == "idle"
    assert binding["supports_resume"] is False
    assert binding["resumable_invocation_id"] is None


def test_ws_rejected_continue_restores_paused_control_state(tmp_path, monkeypatch):
    cfg = _local_api_config()
    monkeypatch.setattr(SessionManager, "_haas_config", lambda self, workspace: cfg)
    monkeypatch.setattr(SessionManager, "_maybe_autotitle", lambda self, session_id: None)
    direct = ResumeAwareDirectHaasClient(cfg)

    @asynccontextmanager
    async def reject_continue(*args, **kwargs):
        del args, kwargs
        raise HaasClientError("continue rejected")
        yield  # pragma: no cover - makes this an async context manager fixture

    direct.continue_invocation = reject_continue  # type: ignore[method-assign]
    manager = SessionManager(workspace=tmp_path, provider=ScriptedProvider())
    manager._haas_supervisor = FakeLocalHaasSupervisor()
    manager._haas_direct_client_factory = lambda _config: direct

    with TestClient(create_app(manager)).websocket_connect(
        "/ws/session/s1?agent=cowork"
    ) as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"type": "user_message", "text": "run until paused"})
        assert ws.receive_json()["type"] == "turn_start"
        ws.send_json({"type": "pause"})
        while ws.receive_json()["type"] != "turn_done":
            pass

        ws.send_json({"type": "continue"})
        events = []
        while True:
            event = ws.receive_json()
            events.append(event)
            if event["type"] == "turn_done":
                break

    control_states = [
        event["data"]["controlState"]
        for event in events
        if event["type"] == "execution_control"
    ]
    assert control_states == ["resuming", "paused"]
    binding = manager.session_store.load("s1").bindings["haas_delegation"]
    assert binding["control_state"] == "paused"
    assert binding["supports_resume"] is True
    assert binding["resumable_invocation_id"] == "inv_pause_1"


def test_ws_pause_and_continue_preserve_delegated_session_affinity(tmp_path, monkeypatch):
    cfg = _haas_config()
    monkeypatch.setattr(SessionManager, "_haas_config", lambda self, workspace: cfg)
    monkeypatch.setattr(SessionManager, "_maybe_autotitle", lambda self, session_id: None)
    client = PauseResumeDelegatedHaasClient(cfg)
    manager = SessionManager(
        workspace=tmp_path,
        provider=ScriptedProvider(),
        haas_client_factory=lambda _config: client,
    )
    manager.workspace_trust.set_trusted(tmp_path, True)

    with TestClient(create_app(manager)).websocket_connect(
        "/ws/session/s1?agent=code"
    ) as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"type": "user_message", "text": "run until paused"})
        assert ws.receive_json()["type"] == "turn_start"
        ws.send_json({"type": "pause"})
        while ws.receive_json()["type"] != "turn_done":
            pass
        ws.send_json({"type": "continue"})
        events = []
        while True:
            event = ws.receive_json()
            events.append(event)
            if event["type"] == "turn_done":
                break

    assert client.pause_calls == [("hsess_s1", "inv_delegated_pause_1")]
    assert client.continue_calls == [
        ("hsess_s1", "inv_delegated_pause_1", None)
    ]
    turn_start = next(event for event in events if event["type"] == "turn_start")
    assert turn_start["data"]["delegated"]["session"] == "dgsess_1"
    assert (
        turn_start["data"]["delegated"]["invocation"]
        == "inv_delegated_continue_2"
    )
    binding = manager.session_store.load("s1").bindings["haas_delegation"]
    assert binding["delegated_session_id"] == "dgsess_1"
    assert binding["haas_session_id"] == "hsess_s1"


def test_ws_stop_cancels_delegated_invocation_before_first_sse_event(tmp_path, monkeypatch):
    cfg = _haas_config()
    monkeypatch.setattr(SessionManager, "_haas_config", lambda self, workspace: cfg)
    monkeypatch.setattr(SessionManager, "_maybe_autotitle", lambda self, session_id: None)
    clients: list[SilentAcceptedDelegatedClient] = []

    def factory(config: HaasDelegationConfig) -> SilentAcceptedDelegatedClient:
        client = SilentAcceptedDelegatedClient(config)
        clients.append(client)
        return client

    manager = SessionManager(
        workspace=tmp_path,
        provider=ScriptedProvider(),
        haas_client_factory=factory,
    )
    manager.workspace_trust.set_trusted(tmp_path, True)

    with TestClient(create_app(manager)).websocket_connect(
        "/ws/session/s1?agent=code"
    ) as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"type": "user_message", "text": "run silently until stopped"})
        assert ws.receive_json()["type"] == "turn_start"
        ws.send_json({"type": "interrupt"})
        events = []
        while True:
            event = ws.receive_json()
            events.append(event)
            if event["type"] == "turn_done":
                break

    assert clients[0].cancel_calls == [("hsess_s1", "inv_silent_1")]
    assert next(event for event in events if event["type"] == "turn_end")["data"][
        "status"
    ] == "cancelled"


def test_ws_reuses_existing_haas_binding_for_followup(tmp_path, monkeypatch):
    monkeypatch.setattr(SessionManager, "_haas_config", lambda self, workspace: _haas_config())
    monkeypatch.setattr(SessionManager, "_maybe_autotitle", lambda self, session_id: None)
    clients: list[FakeHaasClient] = []

    def factory(config: HaasDelegationConfig) -> FakeHaasClient:
        client = FakeHaasClient(config)
        clients.append(client)
        return client

    manager = SessionManager(
        workspace=tmp_path, provider=ScriptedProvider(), haas_client_factory=factory
    )
    manager.workspace_trust.set_trusted(tmp_path, True)
    client = TestClient(create_app(manager))

    with client.websocket_connect("/ws/session/s1?agent=code") as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"type": "user_message", "text": "fix this", "model": "volcengine-ark:m1"})
        while ws.receive_json()["type"] != "turn_done":
            pass
        ws.send_json({"type": "user_message", "text": "hello again"})
        while ws.receive_json()["type"] != "turn_done":
            pass

    assert sum(len(client.created) for client in clients) == 1
    assert [session for client in clients for session in client.restored] == [
        "dgsess_1",
        "dgsess_1",
    ]


def test_ws_waits_for_pending_policy_before_starting_delegated_turn(tmp_path, monkeypatch):
    monkeypatch.setattr(SessionManager, "_haas_config", lambda self, workspace: _haas_config())
    monkeypatch.setattr(SessionManager, "_maybe_autotitle", lambda self, session_id: None)
    clients: list[PendingPolicyHaasClient] = []
    manager = SessionManager(
        workspace=tmp_path,
        provider=ScriptedProvider(),
        haas_client_factory=lambda config: (
            clients.append(PendingPolicyHaasClient(config)) or clients[-1]
        ),
    )
    manager.workspace_trust.set_trusted(tmp_path, True)

    with TestClient(create_app(manager)).websocket_connect("/ws/session/s1?agent=code") as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"type": "user_message", "text": "fix this"})
        while ws.receive_json()["type"] != "turn_done":
            pass

    assert clients[0].policy_reads == 2
    assert len(clients[0].runs) == 1
    binding = manager.session_store.load("s1").bindings["haas_delegation"]
    assert binding["desired_revision"] == binding["applied_revision"] == 2
    assert binding["stream_bridge"]["completed"] is True
    assert len(binding["attempt_ledger"]["attempts"]) == 1


def test_server_confirmed_410_creates_and_reuses_one_linked_attempt(tmp_path, monkeypatch):
    monkeypatch.setattr(SessionManager, "_haas_config", lambda self, workspace: _haas_config())
    monkeypatch.setattr(SessionManager, "_maybe_autotitle", lambda self, session_id: None)
    fake = ExpiredReplayHaasClient(_haas_config())
    manager = SessionManager(
        workspace=tmp_path,
        provider=ScriptedProvider(),
        haas_client_factory=lambda config: fake,
    )
    manager.workspace_trust.set_trusted(tmp_path, True)

    app_client = TestClient(create_app(manager))
    with app_client.websocket_connect("/ws/session/s1?agent=code") as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"type": "user_message", "text": "fix this"})
        while ws.receive_json()["type"] != "turn_done":
            pass

    record = manager.session_store.load("s1")
    binding = record.bindings["haas_delegation"]
    binding["attempt_ledger"]["attempts"][-1]["terminal"] = False
    manager.session_store.set_bindings("s1", record.bindings)

    with app_client.websocket_connect("/ws/session/s1?agent=code") as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"type": "user_message", "text": "again"})
        failed = []
        while True:
            event = ws.receive_json()
            failed.append(event)
            if event["type"] == "turn_done":
                break
        ws.send_json({"type": "retry"})
        while ws.receive_json()["type"] != "turn_done":
            pass

    assert any(item["type"] == "error" for item in failed)
    attempts = manager.session_store.load("s1").bindings["haas_delegation"]["attempt_ledger"][
        "attempts"
    ]
    assert len(attempts) == 2
    expired = next(item for item in attempts if item["expiryConfirmed"])
    linked = next(item for item in attempts if item["attemptId"] == expired["linkedAttemptId"])
    assert linked["predecessorInvocationId"] == expired["invocationId"]
    assert linked["terminal"] is True


def test_ws_unbound_unsupported_attachment_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(SessionManager, "_haas_config", lambda self, workspace: _haas_config())
    monkeypatch.setattr(SessionManager, "_maybe_autotitle", lambda self, session_id: None)
    provider = ScriptedProvider()
    manager = SessionManager(workspace=tmp_path, provider=provider)
    manager.workspace_trust.set_trusted(tmp_path, True)
    client = TestClient(create_app(manager))

    with client.websocket_connect("/ws/session/s1?agent=code") as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json(
            {
                "type": "user_message",
                "text": "fix this image",
                "model": "volcengine-ark:m1",
                "attachments": [
                    {
                        "kind": "image",
                        "data_url": "data:image/png;base64,aaa",
                    }
                ],
            }
        )
        seen = []
        while True:
            event = ws.receive_json()
            seen.append(event)
            if event["type"] == "turn_done":
                break

    assert provider.calls == 0
    assert seen[0]["type"] == "error"
    assert "unsupported_content" in seen[0]["data"]["error"]


def test_ws_uses_bound_haas_endpoint_after_config_change(tmp_path, monkeypatch):
    first = _haas_config()
    second = _haas_config()
    second.base_url = "http://127.0.0.1:9999"
    configs = [first, second]
    clients: list[FakeHaasClient] = []

    monkeypatch.setattr(SessionManager, "_haas_config", lambda self, workspace: configs.pop(0))
    monkeypatch.setattr(SessionManager, "_maybe_autotitle", lambda self, session_id: None)

    def factory(config: HaasDelegationConfig) -> FakeHaasClient:
        client = FakeHaasClient(config)
        clients.append(client)
        return client

    manager = SessionManager(
        workspace=tmp_path, provider=ScriptedProvider(), haas_client_factory=factory
    )
    manager.workspace_trust.set_trusted(tmp_path, True)
    client = TestClient(create_app(manager))

    with client.websocket_connect("/ws/session/s1?agent=code") as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"type": "user_message", "text": "fix this", "model": "volcengine-ark:m1"})
        while ws.receive_json()["type"] != "turn_done":
            pass
        ws.send_json({"type": "user_message", "text": "hello again"})
        while ws.receive_json()["type"] != "turn_done":
            pass

    assert [client.config.base_url for client in clients] == [
        "http://127.0.0.1:8092",
        "http://127.0.0.1:8092",
    ]


def test_ws_retry_on_haas_bound_session_stays_delegated(tmp_path, monkeypatch):
    monkeypatch.setattr(SessionManager, "_haas_config", lambda self, workspace: _haas_config())
    monkeypatch.setattr(SessionManager, "_maybe_autotitle", lambda self, session_id: None)
    clients: list[FakeHaasClient] = []

    def factory(config: HaasDelegationConfig) -> FakeHaasClient:
        client = FakeHaasClient(config)
        clients.append(client)
        return client

    provider = ScriptedProvider()
    manager = SessionManager(workspace=tmp_path, provider=provider, haas_client_factory=factory)
    manager.workspace_trust.set_trusted(tmp_path, True)
    client = TestClient(create_app(manager))

    with client.websocket_connect("/ws/session/s1?agent=code") as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"type": "user_message", "text": "fix this", "model": "volcengine-ark:m1"})
        while ws.receive_json()["type"] != "turn_done":
            pass
        ws.send_json({"type": "retry"})
        while ws.receive_json()["type"] != "turn_done":
            pass

    assert provider.calls == 0
    assert sum(len(client.created) for client in clients) == 1
    assert [run["message"] for client in clients for run in client.runs] == [
        {"role": "user", "parts": [{"text": "fix this"}]},
        {"role": "user", "parts": [{"text": "fix this"}]},
    ]


def test_ws_bound_haas_session_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(SessionManager, "_haas_config", lambda self, workspace: _haas_config())
    monkeypatch.setattr(SessionManager, "_maybe_autotitle", lambda self, session_id: None)
    manager = SessionManager(
        workspace=tmp_path,
        provider=ScriptedProvider(),
        haas_client_factory=FailingHaasClient,
    )
    manager.workspace_trust.set_trusted(tmp_path, True)
    client = TestClient(create_app(manager))

    with client.websocket_connect("/ws/session/s1?agent=code") as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"type": "user_message", "text": "fix this", "model": "volcengine-ark:m1"})
        events = []
        while True:
            event = ws.receive_json()
            events.append(event)
            if event["type"] == "turn_done":
                break

    assert any(event["type"] == "error" for event in events)
    messages = client.get("/v1/sessions/s1/messages").json()["messages"]
    assert messages[-2]["role"] == "user"
    assert messages[-2]["content"] == "fix this"
    assert messages[-1]["role"] == "notice"
    assert messages[-1]["kind"] == "error"


def test_ws_delegated_failed_terminal_status_is_preserved(tmp_path, monkeypatch):
    monkeypatch.setattr(SessionManager, "_haas_config", lambda self, workspace: _haas_config())
    monkeypatch.setattr(SessionManager, "_maybe_autotitle", lambda self, session_id: None)
    manager = SessionManager(
        workspace=tmp_path,
        provider=ScriptedProvider(),
        haas_client_factory=FailedTurnHaasClient,
    )
    manager.workspace_trust.set_trusted(tmp_path, True)
    client = TestClient(create_app(manager))

    with client.websocket_connect("/ws/session/s1?agent=code") as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"type": "user_message", "text": "fix this", "model": "volcengine-ark:m1"})
        seen = []
        while True:
            event = ws.receive_json()
            seen.append(event)
            if event["type"] == "turn_done":
                break

    turn_end = next(event for event in seen if event["type"] == "turn_end")
    assert turn_end["data"]["status"] == "failed"


def test_ws_defaults_to_haas_when_no_keyword_matches(tmp_path, monkeypatch):
    monkeypatch.setattr(SessionManager, "_haas_config", lambda self, workspace: _haas_config())
    monkeypatch.setattr(SessionManager, "_maybe_autotitle", lambda self, session_id: None)
    clients: list[FakeHaasClient] = []
    manager = SessionManager(
        workspace=tmp_path,
        provider=ScriptedProvider(),
        haas_client_factory=lambda config: clients.append(FakeHaasClient(config)) or clients[-1],
    )
    manager.workspace_trust.set_trusted(tmp_path, True)
    client = TestClient(create_app(manager))

    with client.websocket_connect("/ws/session/s1?agent=code") as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"type": "user_message", "text": "hello", "model": "volcengine-ark:m1"})
        while ws.receive_json()["type"] != "turn_done":
            pass

    assert sum(len(item.runs) for item in clients) == 1


def test_local_human_bridge_resumes_same_invocation_after_approval_and_input(tmp_path, monkeypatch):
    cfg = _local_api_config()
    monkeypatch.setattr(SessionManager, "_haas_config", lambda self, workspace: cfg)
    monkeypatch.setattr(SessionManager, "_maybe_autotitle", lambda self, session_id: None)
    direct = InteractiveDirectHaasClient(cfg)
    manager = SessionManager(
        workspace=tmp_path,
        provider=ScriptedProvider(),
        haas_client_factory=FakeHaasClient,
    )
    manager._haas_supervisor = FakeLocalHaasSupervisor()
    manager._haas_direct_client_factory = lambda _config: direct
    client = TestClient(create_app(manager))

    with client.websocket_connect("/ws/session/s1?agent=cowork") as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"type": "user_message", "text": "run interactively"})
        approval = ws.receive_json()
        while approval["type"] != "permission_required":
            approval = ws.receive_json()
        assert approval["data"]["approvalId"] == "appr_interactive_1"
        ws.send_json(
            {
                "type": "approval",
                "decision": "once",
                "haas_approval_id": "appr_interactive_1",
            }
        )
        question = ws.receive_json()
        while question["type"] != "question_requested":
            question = ws.receive_json()
        assert question["data"]["inputRequestId"] == "inreq_interactive_1"
        ws.send_json(
            {
                "type": "question_response",
                "answer": "Current diff",
                "haas_input_request_id": "inreq_interactive_1",
                "answers": {"scope": {"values": ["Current diff"]}},
            }
        )
        events = []
        while True:
            event = ws.receive_json()
            events.append(event)
            if event["type"] == "turn_done":
                break

    assert direct.approval_calls == [("hsess_s1", "appr_interactive_1", True)]
    assert direct.input_calls == [
        (
            "hsess_s1",
            "inreq_interactive_1",
            {"scope": {"values": ["Current diff"]}},
        )
    ]
    assert (
        next(event for event in events if event["type"] == "turn_end")["data"]["status"]
        == "completed"
    )
    binding = manager.session_store.load("s1").bindings["haas_delegation"]
    assert binding["stream_bridge"]["invocationId"] == "inv_interactive_1"
    assert binding["stream_bridge"]["task"]["phase"] == "completed"


def test_local_structured_plan_gets_one_linked_continuation_then_completes(tmp_path, monkeypatch):
    cfg = _local_api_config()
    monkeypatch.setattr(SessionManager, "_haas_config", lambda self, workspace: cfg)
    monkeypatch.setattr(SessionManager, "_maybe_autotitle", lambda self, session_id: None)
    direct = PlannedDirectHaasClient(cfg)
    manager = SessionManager(
        workspace=tmp_path, provider=ScriptedProvider(), haas_client_factory=FakeHaasClient
    )
    manager._haas_supervisor = FakeLocalHaasSupervisor()
    manager._haas_direct_client_factory = lambda _config: direct
    client = TestClient(create_app(manager))

    with client.websocket_connect("/ws/session/s1?agent=cowork") as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"type": "user_message", "text": "finish the plan"})
        events = []
        while True:
            event = ws.receive_json()
            events.append(event)
            if event["type"] == "turn_done":
                break

    assert len(direct.runs) == 2
    assert direct.runs[0]["idempotency_key"] != direct.runs[1]["idempotency_key"]
    assert direct.runs[1]["body"]["newMessage"]["parts"][0]["text"].startswith(
        "Continue the remaining structured plan steps"
    )
    binding = manager.session_store.load("s1").bindings["haas_delegation"]
    assert binding["stream_bridge"]["task"]["phase"] == "completed"
    assert binding["stream_bridge"]["task"]["continuationCount"] == 1
    assert [event["type"] for event in events].count("turn_start") == 2


def test_local_structured_plan_stops_after_three_continuations(tmp_path, monkeypatch):
    cfg = _local_api_config()
    monkeypatch.setattr(SessionManager, "_haas_config", lambda self, workspace: cfg)
    monkeypatch.setattr(SessionManager, "_maybe_autotitle", lambda self, session_id: None)
    direct = NeverFinishedPlanClient(cfg)
    manager = SessionManager(workspace=tmp_path, provider=ScriptedProvider())
    manager._haas_supervisor = FakeLocalHaasSupervisor()
    manager._haas_direct_client_factory = lambda _config: direct
    client = TestClient(create_app(manager))

    with client.websocket_connect("/ws/session/s1?agent=cowork") as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"type": "user_message", "text": "finish the plan"})
        events = []
        while True:
            event = ws.receive_json()
            events.append(event)
            if event["type"] == "turn_done":
                break

    assert len(direct.runs) == 4
    error = next(event for event in events if event["type"] == "error")
    assert error["data"]["error_type"] == "haas_task_continuation_exhausted"
    binding = manager.session_store.load("s1").bindings["haas_delegation"]
    assert binding["stream_bridge"]["task"]["phase"] == "incomplete"
    messages = client.get("/v1/sessions/s1/messages").json()["messages"]
    assert [message["role"] for message in messages].count("user") == 1


def test_reconnect_restores_pending_haas_interaction_without_new_invocation(tmp_path, monkeypatch):
    cfg = _local_api_config()
    monkeypatch.setattr(SessionManager, "_haas_config", lambda self, workspace: cfg)
    direct = InteractiveDirectHaasClient(cfg)
    manager = SessionManager(workspace=tmp_path, provider=ScriptedProvider())
    manager._haas_direct_client_factory = lambda _config: direct
    engine = manager.get_engine("s1", workspace=str(tmp_path), agent="cowork")
    assert engine is not None
    manager.save("s1", engine)
    binding = manager._direct_haas_binding(session_id="s1", config=cfg, workspace=str(tmp_path))
    manager._persist_haas_binding("s1", engine, binding)
    client = TestClient(create_app(manager))

    with client.websocket_connect("/ws/session/s1?agent=cowork") as ws:
        assert ws.receive_json()["type"] == "ready"
        restored = ws.receive_json()

    assert restored["type"] == "permission_required"
    assert restored["data"]["approvalId"] == "appr_interactive_1"
    assert direct.runs == []


@pytest.mark.asyncio
async def test_local_mode_change_updates_revisioned_haas_policy_and_binding(
    tmp_path, monkeypatch
):
    cfg = _local_api_config()
    monkeypatch.setattr(SessionManager, "_haas_config", lambda self, workspace: cfg)
    direct = FakeDirectHaasClient(cfg)
    manager = SessionManager(workspace=tmp_path, provider=ScriptedProvider())
    manager._haas_direct_client_factory = lambda _config: direct
    engine = manager.get_engine("s1", workspace=str(tmp_path), agent="cowork")
    assert engine is not None
    manager.save("s1", engine)
    binding = manager._direct_haas_binding(
        session_id="s1", config=cfg, workspace=str(tmp_path)
    )
    binding.update({"desired_revision": 3, "applied_revision": 3})
    manager._persist_haas_binding("s1", engine, binding)

    result = await manager.update_haas_approval_mode(
        "s1", engine, Mode.BYPASS_APPROVALS
    )

    assert result is not None
    assert direct.policy_update == {
        "session_id": "hsess_s1",
        "policy": {"tools": {"disabled": [], "approvalMode": "never"}},
        "expected_revision": 3,
        "idempotency_key": "manager-mode-policy:hsess_s1:4:never",
    }
    stored = manager.session_store.load("s1").bindings["haas_delegation"]
    assert stored["desired_revision"] == stored["applied_revision"] == 4
    assert stored["policy_status"] == "applied"


@pytest.mark.asyncio
async def test_local_profile_sync_reconciles_stale_binding_before_cas(tmp_path):
    cfg = _local_api_config()
    manager = SessionManager(workspace=tmp_path, provider=ScriptedProvider())
    manager._haas_supervisor = FakeLocalHaasSupervisor()
    engine = manager.get_engine("s1", workspace=str(tmp_path), agent="cowork")
    assert engine is not None

    class StaleBindingClient(FakeDirectHaasClient):
        def __init__(self):
            super().__init__(cfg)
            self.sync_profile_ids = []
            self.rebinds = []

        async def get_session_profile(self, session_id):
            assert session_id == "hsess_s1"
            return HaasEnvelope(
                {
                    "profileId": "hprof_authoritative",
                    "profileVersion": 51,
                    "profileFingerprint": "sha256:authoritative",
                    "harnessId": cfg.harness_id,
                    "base": cfg.harness_base,
                    "executionIntentFingerprint": "sha256:old-intent",
                },
                trace_id="tr_recovery",
            )

        async def sync_profile(self, configuration, *, profile_id=None):
            self.profile_configuration = configuration
            self.sync_profile_ids.append(profile_id)
            return {"id": "hprof_fresh", "version": 52, "profileFingerprint": "sha256:fresh"}

        async def rebind_profile(self, session_id, profile_id, *, expected_version=None):
            self.rebinds.append((session_id, profile_id, expected_version))
            return HaasEnvelope({}, trace_id="tr_rebind")

    direct = StaleBindingClient()
    binding = manager._direct_haas_binding(
        session_id="s1", config=cfg, workspace=str(tmp_path)
    )
    binding.update(
        {
            "accepted_invocation_id": "inv_previous",
            "profile_id": "hprof_stale",
            "profile_version": 50,
            "profile_fingerprint": "sha256:stale",
        }
    )

    result = await manager._sync_local_haas_profile(direct, engine, binding)

    assert direct.sync_profile_ids == ["hprof_authoritative"]
    assert direct.rebinds == [("hsess_s1", "hprof_fresh", 51)]
    assert result["profile_id"] == "hprof_fresh"
    assert result["profile_version"] == 52


@pytest.mark.asyncio
async def test_local_profile_sync_fails_closed_when_accepted_session_profile_is_missing(
    tmp_path,
):
    cfg = _local_api_config()
    manager = SessionManager(workspace=tmp_path, provider=ScriptedProvider())
    manager._haas_supervisor = FakeLocalHaasSupervisor()
    engine = manager.get_engine("s1", workspace=str(tmp_path), agent="cowork")
    assert engine is not None

    class MissingSessionClient(FakeDirectHaasClient):
        def __init__(self):
            super().__init__(cfg)
            self.synced = False

        async def get_session_profile(self, session_id):
            del session_id
            raise HaasDelegationError("session not found", status_code=404)

        async def sync_profile(self, configuration, *, profile_id=None):
            del configuration, profile_id
            self.synced = True
            return {}

    direct = MissingSessionClient()
    binding = manager._direct_haas_binding(
        session_id="s1", config=cfg, workspace=str(tmp_path)
    )
    binding.update(
        {
            "accepted_invocation_id": "inv_previous",
            "profile_id": "hprof_stale",
            "profile_version": 50,
        }
    )

    with pytest.raises(HaasDelegationError, match="session not found"):
        await manager._sync_local_haas_profile(direct, engine, binding)

    assert direct.synced is False


@pytest.mark.asyncio
async def test_failed_local_mode_change_keeps_engine_and_binding_unchanged(
    tmp_path, monkeypatch
):
    cfg = _local_api_config()
    monkeypatch.setattr(SessionManager, "_haas_config", lambda self, workspace: cfg)
    direct = FakeDirectHaasClient(cfg)

    async def fail_update(*args, **kwargs):
        del args, kwargs
        raise HaasDelegationError("stale policy revision")

    direct.update_session_policy = fail_update
    manager = SessionManager(workspace=tmp_path, provider=ScriptedProvider())
    manager._haas_direct_client_factory = lambda _config: direct
    engine = manager.get_engine("s1", workspace=str(tmp_path), agent="cowork")
    assert engine is not None
    manager.save("s1", engine)
    binding = manager._direct_haas_binding(
        session_id="s1", config=cfg, workspace=str(tmp_path)
    )
    binding.update({"desired_revision": 3, "applied_revision": 3})
    manager._persist_haas_binding("s1", engine, binding)

    with pytest.raises(HaasDelegationError, match="stale policy revision"):
        await manager.update_haas_approval_mode(
            "s1", engine, Mode.BYPASS_APPROVALS
        )

    assert engine.permissions.mode is Mode.INTERACTIVE
    stored = manager.session_store.load("s1").bindings["haas_delegation"]
    assert stored["desired_revision"] == stored["applied_revision"] == 3


@pytest.mark.asyncio
async def test_delegated_mode_change_updates_same_revisioned_policy_control(
    tmp_path, monkeypatch
):
    cfg = _haas_config()
    remote = FakeHaasClient(cfg)
    manager = SessionManager(
        workspace=tmp_path,
        provider=ScriptedProvider(),
        haas_client_factory=lambda _config: remote,
    )
    monkeypatch.setattr(SessionManager, "_haas_config", lambda self, workspace: cfg)
    engine = manager.get_engine("s1", workspace=str(tmp_path), agent="code")
    assert engine is not None
    manager.save("s1", engine)
    policy = delegation_policy_snapshot(cfg)
    binding = {
        "backend": "haas",
        "execution_mode": "delegated_session",
        "haas_base_url": cfg.base_url,
        "delegated_session_id": "dgsess_1",
        "haas_session_id": "hsess_s1",
        "haas_user_id": cfg.user_id,
        "harness_id": cfg.harness_id,
        "harness_base": cfg.harness_base,
        "delegation_policy_snapshot": policy,
        "desired_revision": 1,
        "applied_revision": 1,
    }
    manager._persist_haas_binding("s1", engine, binding)

    result = await manager.update_haas_approval_mode(
        "s1", engine, Mode.BYPASS_APPROVALS
    )

    assert result is not None
    update = remote.policy_updates[-1]
    assert update["policy_snapshot"]["tools"] == {
        "disabled": [],
        "approvalMode": "never",
    }
    assert update["idempotency_key"] == "manager-mode-policy:dgsess_1:2:never"
    assert update["expected_revision"] == 1
    stored = manager.session_store.load("s1").bindings["haas_delegation"]
    assert stored["delegation_policy_snapshot"]["tools"]["approvalMode"] == "never"


@pytest.mark.asyncio
async def test_reconnect_suppresses_stale_interaction_after_non_success_terminal(
    tmp_path, monkeypatch
):
    cfg = _local_api_config()
    monkeypatch.setattr(SessionManager, "_haas_config", lambda self, workspace: cfg)
    direct = InteractiveDirectHaasClient(cfg)

    async def failed_invocation(session_id, invocation_id):
        del session_id, invocation_id
        return HaasEnvelope({"status": "failed"}, trace_id="tr_failed")

    async def failed_events(session_id, *, after_event_id=None, limit=100):
        del session_id, limit
        if after_event_id is not None:
            return HaasEnvelope([], trace_id="tr_empty")
        return HaasEnvelope(
            [
                {
                    "eventId": "evt_terminal_failed",
                    "invocationId": "inv_terminal",
                    "type": "haas.turn.failed",
                    "haas": {
                        "status": "failed",
                        "code": "provider_failed",
                        "safeReason": "Provider unavailable",
                    },
                }
            ],
            trace_id="tr_failed_events",
        )

    direct.get_invocation = failed_invocation
    direct.events_page = failed_events
    manager = SessionManager(workspace=tmp_path, provider=ScriptedProvider())
    manager._haas_direct_client_factory = lambda _config: direct
    engine = manager.get_engine("s1", workspace=str(tmp_path), agent="cowork")
    assert engine is not None
    manager.save("s1", engine)
    binding = manager._direct_haas_binding(
        session_id="s1", config=cfg, workspace=str(tmp_path)
    )
    binding["stream_bridge"] = StreamBridgeState(
        endpoint_id=cfg.base_url,
        session=SessionKey(cfg.harness_id, cfg.user_id, "hsess_s1"),
        invocation_id="inv_terminal",
    ).to_dict()
    ledger = AttemptLedger()
    ledger.add(
        manager_turn_id="turn_terminal",
        attempt_id="attempt_terminal",
        idempotency_key="manager-turn:s1:turn_terminal:attempt_terminal",
        invocation_id="inv_terminal",
    )
    binding.update(
        {
            "attempt_ledger": ledger.to_dict(),
            "current_attempt_id": "attempt_terminal",
            "accepted_invocation_id": "inv_terminal",
            "control_state": "running",
        }
    )
    manager._persist_haas_binding("s1", engine, binding)

    assert await manager.pending_haas_interactions("s1") == []
    stored = manager.session_store.load("s1").bindings["haas_delegation"]
    assert stored["stream_bridge"]["terminalStatus"] == "failed"
    assert stored["stream_bridge"]["task"]["phase"] == "failed"
    assert stored["stream_bridge"]["completed"] is True
    assert stored["control_state"] == "idle"
    assert stored["attempt_ledger"]["attempts"][0]["terminal"] is True

    client = TestClient(create_app(manager))
    with client.websocket_connect("/ws/session/s1?agent=cowork") as ws:
        ready = ws.receive_json()

    assert ready["type"] == "ready"
    assert ready["data"]["running"] is False
    assert ready["data"]["execution_control"]["controlState"] == "idle"
    assert ready["data"]["haas_task_outcome"]["phase"] == "failed"


@pytest.mark.asyncio
async def test_reconnect_reconciles_stale_running_binding_after_success(
    tmp_path, monkeypatch
):
    cfg = _local_api_config()
    monkeypatch.setattr(SessionManager, "_haas_config", lambda self, workspace: cfg)
    direct = InteractiveDirectHaasClient(cfg)

    async def completed_invocation(session_id, invocation_id):
        del session_id, invocation_id
        return HaasEnvelope(
            {"status": "completed", "terminalEventId": "evt_terminal_completed"},
            trace_id="tr_completed",
        )

    async def completed_events(session_id, *, after_event_id=None, limit=100):
        del session_id, limit
        if after_event_id is not None:
            return HaasEnvelope([], trace_id="tr_empty")
        return HaasEnvelope(
            [
                {
                    "eventId": "evt_terminal_completed",
                    "invocationId": "inv_terminal",
                    "type": "haas.turn.completed",
                    "haas": {"status": "completed"},
                }
            ],
            trace_id="tr_completed_events",
        )

    direct.get_invocation = completed_invocation
    direct.events_page = completed_events
    manager = SessionManager(workspace=tmp_path, provider=ScriptedProvider())
    manager._haas_direct_client_factory = lambda _config: direct
    engine = manager.get_engine("s1", workspace=str(tmp_path), agent="cowork")
    assert engine is not None
    manager.save("s1", engine)
    binding = manager._direct_haas_binding(
        session_id="s1", config=cfg, workspace=str(tmp_path)
    )
    binding["stream_bridge"] = StreamBridgeState(
        endpoint_id=cfg.base_url,
        session=SessionKey(cfg.harness_id, cfg.user_id, "hsess_s1"),
        invocation_id="inv_terminal",
    ).to_dict()
    ledger = AttemptLedger()
    ledger.add(
        manager_turn_id="turn_terminal",
        attempt_id="attempt_terminal",
        idempotency_key="manager-turn:s1:turn_terminal:attempt_terminal",
        invocation_id="inv_terminal",
    )
    binding.update(
        {
            "attempt_ledger": ledger.to_dict(),
            "current_attempt_id": "attempt_terminal",
            "accepted_invocation_id": "inv_terminal",
            "control_state": "running",
        }
    )
    manager._persist_haas_binding("s1", engine, binding)

    assert await manager.pending_haas_interactions("s1") == []
    stored = manager.session_store.load("s1").bindings["haas_delegation"]
    assert stored["stream_bridge"]["terminalStatus"] == "completed"
    assert stored["stream_bridge"]["task"]["phase"] == "completed"
    assert stored["stream_bridge"]["completed"] is True
    assert stored["control_state"] == "idle"
    assert stored["attempt_ledger"]["attempts"][0]["terminal"] is True


@pytest.mark.asyncio
async def test_reconnect_marks_missing_canonical_terminal_as_recoverable_incomplete(
    tmp_path, monkeypatch
):
    cfg = _local_api_config()
    monkeypatch.setattr(SessionManager, "_haas_config", lambda self, workspace: cfg)
    direct = InteractiveDirectHaasClient(cfg)

    async def completed_invocation(session_id, invocation_id):
        del session_id, invocation_id
        return HaasEnvelope(
            {"status": "completed", "terminalEventId": "evt_missing"},
            trace_id="tr_completed",
        )

    async def no_terminal_events(session_id, *, after_event_id=None, limit=100):
        del session_id, after_event_id, limit
        return HaasEnvelope([], trace_id="tr_empty")

    direct.get_invocation = completed_invocation
    direct.events_page = no_terminal_events
    manager = SessionManager(workspace=tmp_path, provider=ScriptedProvider())
    manager._haas_direct_client_factory = lambda _config: direct
    engine = manager.get_engine("s1", workspace=str(tmp_path), agent="cowork")
    assert engine is not None
    manager.save("s1", engine)
    binding = manager._direct_haas_binding(
        session_id="s1", config=cfg, workspace=str(tmp_path)
    )
    binding["stream_bridge"] = StreamBridgeState(
        endpoint_id=cfg.base_url,
        session=SessionKey(cfg.harness_id, cfg.user_id, "hsess_s1"),
        invocation_id="inv_terminal",
    ).to_dict()
    ledger = AttemptLedger()
    ledger.add(
        manager_turn_id="turn_terminal",
        attempt_id="attempt_terminal",
        idempotency_key="manager-turn:s1:turn_terminal:attempt_terminal",
        invocation_id="inv_terminal",
    )
    binding.update(
        {
            "attempt_ledger": ledger.to_dict(),
            "current_attempt_id": "attempt_terminal",
            "accepted_invocation_id": "inv_terminal",
            "control_state": "running",
        }
    )
    manager._persist_haas_binding("s1", engine, binding)

    assert await manager.pending_haas_interactions("s1") == []
    stored = manager.session_store.load("s1").bindings["haas_delegation"]
    assert stored["stream_bridge"]["terminalStatus"] is None
    assert stored["stream_bridge"]["task"]["phase"] == "incomplete"
    assert (
        stored["stream_bridge"]["task"]["code"]
        == "haas_terminal_integrity_error"
    )
    assert stored["stream_bridge"]["completed"] is True
    assert stored["control_state"] == "idle"
    assert stored["attempt_ledger"]["attempts"][0]["terminal"] is True


@pytest.mark.asyncio
async def test_reconnect_replays_process_events_with_stable_event_ids(tmp_path, monkeypatch):
    cfg = _local_api_config()
    monkeypatch.setattr(SessionManager, "_haas_config", lambda self, workspace: cfg)
    direct = FakeDirectHaasClient(cfg)

    async def process_page(session_id, *, after_event_id=None, limit=100):
        del session_id, limit
        if after_event_id is not None:
            return HaasEnvelope([], trace_id="tr_empty")
        return HaasEnvelope(
            [
                {
                    "eventId": "evt_reasoning_1",
                    "invocationId": "inv_replay_1",
                    "type": "haas.output.reasoning.delta",
                    "content": {"parts": [{"text": "Inspecting", "thought": True}]},
                    "haas": {},
                },
                {
                    "eventId": "evt_tool_1",
                    "invocationId": "inv_replay_1",
                    "type": "haas.tool.started",
                    "haas": {
                        "toolCallId": "call_1",
                        "toolName": "exec_command",
                        "safeSummary": "Run command",
                    },
                },
            ],
            trace_id="tr_process",
        )

    direct.events_page = process_page
    manager = SessionManager(workspace=tmp_path, provider=ScriptedProvider())
    manager._haas_direct_client_factory = lambda _config: direct
    engine = manager.get_engine("s1", workspace=str(tmp_path), agent="cowork")
    assert engine is not None
    manager.save("s1", engine)
    binding = manager._direct_haas_binding(session_id="s1", config=cfg, workspace=str(tmp_path))
    binding["stream_bridge"] = StreamBridgeState(
        endpoint_id=cfg.base_url,
        session=SessionKey(cfg.harness_id, cfg.user_id, "hsess_s1"),
        invocation_id="inv_replay_1",
    ).to_dict()
    manager._persist_haas_binding("s1", engine, binding)

    events = await manager.replay_haas_process_events("s1")

    assert [event.type.value for event in events] == [
        "reasoning_delta",
        "tool_proposed",
        "tool_started",
    ]
    assert [event.data["haasEventId"] for event in events] == [
        "evt_reasoning_1",
        "evt_tool_1",
        "evt_tool_1",
    ]
    assert direct.runs == []


@pytest.mark.asyncio
async def test_retry_reconnects_accepted_local_attempt_without_resubmitting(tmp_path, monkeypatch):
    cfg = _local_api_config()
    monkeypatch.setattr(SessionManager, "_haas_config", lambda self, workspace: cfg)
    direct = FakeDirectHaasClient(cfg)
    cursors: list[str | None] = []

    async def completed_invocation(session_id, invocation_id):
        assert session_id == "hsess_s1"
        assert invocation_id == "inv_accepted"
        return HaasEnvelope(
            {"status": "completed", "terminalEventId": "evt_terminal"},
            trace_id="tr_completed",
        )

    async def recovered_events(session_id, *, after_event_id=None, limit=100):
        assert session_id == "hsess_s1"
        del limit
        cursors.append(after_event_id)
        return HaasEnvelope(
            [
                {
                    "eventId": "evt_terminal",
                    "invocationId": "inv_accepted",
                    "type": "haas.turn.completed",
                    "haas": {"status": "completed"},
                }
            ],
            trace_id="tr_events",
        )

    async def forbidden_profile_sync(*args, **kwargs):
        raise AssertionError("accepted retry must not synchronize a mutable profile")

    direct.get_invocation = completed_invocation
    direct.events_page = recovered_events
    manager = SessionManager(workspace=tmp_path, provider=ScriptedProvider())
    manager._haas_supervisor = FakeLocalHaasSupervisor()
    manager._haas_direct_client_factory = lambda _config: direct
    manager._sync_local_haas_profile = forbidden_profile_sync
    engine = manager.get_engine("s1", workspace=str(tmp_path), agent="cowork")
    assert engine is not None
    engine.messages.append({"role": "user", "content": "do not execute twice"})
    manager.save("s1", engine)

    bridge = StreamBridgeState(
        endpoint_id=cfg.base_url,
        session=SessionKey(cfg.harness_id, cfg.user_id, "hsess_s1"),
        invocation_id="inv_accepted",
        native_cursor="evt_checkpoint",
    )
    ledger = AttemptLedger()
    ledger.add(
        manager_turn_id="turn_accepted",
        attempt_id="attempt_accepted",
        idempotency_key="manager-turn:s1:turn_accepted:attempt_accepted",
        invocation_id="inv_accepted",
    )
    binding = manager._direct_haas_binding(session_id="s1", config=cfg, workspace=str(tmp_path))
    binding.update(
        {
            "attempt_ledger": ledger.to_dict(),
            "current_attempt_id": "attempt_accepted",
            "accepted_invocation_id": "inv_accepted",
            "control_state": "running",
            "profile_id": "hprof_original",
            "profile_version": 1,
            "stream_bridge": bridge.to_dict(),
        }
    )
    manager._persist_haas_binding("s1", engine, binding)

    events = [
        event
        async for event in manager.run_turn_events(
            "s1", engine, "", workspace=str(tmp_path), retry=True
        )
    ]

    assert direct.runs == []
    assert cursors == ["evt_checkpoint"]
    assert [event.type.value for event in events] == [
        "turn_start",
        "assistant_message",
        "turn_end",
    ]
    stored = manager.session_store.load("s1").bindings["haas_delegation"]
    assert stored["current_attempt_id"] == "attempt_accepted"
    assert stored["attempt_ledger"]["attempts"][0]["terminal"] is True
    assert stored["control_state"] == "idle"


@pytest.mark.asyncio
async def test_new_message_does_not_reuse_accepted_active_attempt(tmp_path, monkeypatch):
    cfg = _local_api_config()
    monkeypatch.setattr(SessionManager, "_haas_config", lambda self, workspace: cfg)
    direct = FakeDirectHaasClient(cfg)
    manager = SessionManager(workspace=tmp_path, provider=ScriptedProvider())
    manager._haas_supervisor = FakeLocalHaasSupervisor()
    manager._haas_direct_client_factory = lambda _config: direct
    engine = manager.get_engine("s1", workspace=str(tmp_path), agent="cowork")
    assert engine is not None
    manager.save("s1", engine)
    bridge = StreamBridgeState(
        endpoint_id=cfg.base_url,
        session=SessionKey(cfg.harness_id, cfg.user_id, "hsess_s1"),
        invocation_id="inv_accepted",
    )
    ledger = AttemptLedger()
    ledger.add(
        manager_turn_id="turn_accepted",
        attempt_id="attempt_accepted",
        idempotency_key="manager-turn:s1:turn_accepted:attempt_accepted",
        invocation_id="inv_accepted",
    )
    binding = manager._direct_haas_binding(session_id="s1", config=cfg, workspace=str(tmp_path))
    binding.update(
        {
            "attempt_ledger": ledger.to_dict(),
            "current_attempt_id": "attempt_accepted",
            "accepted_invocation_id": "inv_accepted",
            "control_state": "running",
            "stream_bridge": bridge.to_dict(),
        }
    )
    manager._persist_haas_binding("s1", engine, binding)

    events = [
        event
        async for event in manager._run_haas_local_api_turn(
            "s1",
            engine,
            "new work",
            config=cfg,
            binding=binding,
            append_user=True,
            display=None,
        )
    ]

    assert direct.runs == []
    assert [event.type.value for event in events] == ["error"]
    assert "still active" in events[0].data["error"]


def test_settings_exposes_haas_delegation_without_token(tmp_path, monkeypatch):
    cfg = _haas_config()
    cfg.api_token = "secret-token"
    monkeypatch.setattr(SessionManager, "_haas_config", lambda self, workspace: cfg)
    manager = SessionManager(workspace=tmp_path, provider=ScriptedProvider())
    client = TestClient(create_app(manager))

    settings = client.get("/v1/settings").json()

    assert settings["haas_delegation"]["enabled"] is True
    assert settings["haas_delegation"]["base_url"] == "http://127.0.0.1:8092"
    assert settings["haas_delegation"]["image_digest_configured"] is True
    assert "api_token" not in settings["haas_delegation"]
    assert "secret-token" not in str(settings)


def test_haas_delegation_settings_rest_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    data_dir = tmp_path / "data"
    manager = SessionManager(data_dir=data_dir, provider=ScriptedProvider())
    manager._haas_supervisor = FakeLocalHaasSupervisor()
    client = TestClient(create_app(manager))

    before = client.get("/v1/settings/haas-delegation").json()
    assert before["enabled"] is True
    assert before["local_autostart"] is True
    assert before["network_access"] is True
    assert before["workspace_mode"] == "workspace-write"
    assert before["approval_mode"] == "on-request"
    assert before["policy_defaults_revision"] == 1
    assert before["has_api_token"] is False

    after = client.post(
        "/v1/settings/haas-delegation",
        json={
            "enabled": True,
            "base_url": "http://127.0.0.1:8092/",
            "api_token": "secret-token",
            "user_id": "u_manager",
            "harness_id": "chrn_codex_default",
            "image": "haas:local",
            "image_digest": "sha256:test",
            "local_autostart": True,
            "network_access": True,
            "trigger_keywords": ["ship"],
            "agent_allowlist": ["code"],
        },
    ).json()
    assert after["ok"] is True
    assert after["enabled"] is True
    assert after["base_url"] == "http://127.0.0.1:8092"
    assert after["has_api_token"] is True
    assert after["local_autostart"] is True
    assert after["network_access"] is True
    assert after["local_status"]["status"] == "running"
    assert "secret-token" not in str(after)

    reborn_manager = SessionManager(data_dir=data_dir, provider=ScriptedProvider())
    reborn_manager._haas_supervisor = FakeLocalHaasSupervisor()
    reborn = TestClient(create_app(reborn_manager))
    restored = reborn.get("/v1/settings/haas-delegation").json()
    assert restored["enabled"] is True
    assert restored["has_api_token"] is True
    assert restored["local_autostart"] is True
    assert restored["network_access"] is True
    assert restored["trigger_keywords"] == ["ship"]
    assert "secret-token" not in str(restored)

    cleared = reborn.post("/v1/settings/haas-delegation", json={"clear_api_token": True}).json()
    assert cleared["has_api_token"] is False

    invalid = reborn.post(
        "/v1/settings/haas-delegation", json={"network_access": "yes"}
    ).json()
    assert invalid["ok"] is False
    assert invalid["network_access"] is True


def test_legacy_haas_policy_preferences_migrate_once_and_preserve_explicit_values(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "prefs.json").write_text(
        json.dumps({"haas_delegation": {"network_access": False}}),
        encoding="utf-8",
    )

    manager = SessionManager(data_dir=data_dir, provider=ScriptedProvider())
    migrated = manager._prefs["haas_delegation"]
    assert migrated == {
        "network_access": False,
        "workspace_mode": "workspace-write",
        "approval_mode": "on-request",
        "policy_defaults_revision": 1,
    }

    migrated["approval_mode"] = "never"
    manager._save_prefs()
    reborn = SessionManager(data_dir=data_dir, provider=ScriptedProvider())
    assert reborn._prefs["haas_delegation"]["approval_mode"] == "never"


def test_launch_env_overrides_persisted_haas_preferences(tmp_path, monkeypatch):
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    data_dir = tmp_path / "data"
    manager = SessionManager(data_dir=data_dir, provider=ScriptedProvider())
    manager._prefs["haas_delegation"] = {
        "enabled": True,
        "backend_preference": "haas",
        "local_autostart": True,
        "image_digest": "sha256:test",
    }
    manager._save_prefs()

    monkeypatch.setenv("COWORKER_HAAS_DELEGATION_ENABLED", "false")
    monkeypatch.setenv("COWORKER_HAAS_BACKEND_PREFERENCE", "local")
    monkeypatch.setenv("COWORKER_HAAS_LOCAL_AUTOSTART", "false")
    app_manager = SessionManager(data_dir=data_dir, provider=ScriptedProvider())
    app_manager._haas_supervisor = FakeLocalHaasSupervisor()
    settings = app_manager.get_haas_delegation_settings()

    assert settings["enabled"] is False
    assert settings["backend_preference"] == "local"
    assert settings["local_autostart"] is False


def test_haas_delegation_settings_autostart_calls_supervisor(tmp_path, monkeypatch):
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    manager = SessionManager(data_dir=tmp_path / "data", provider=ScriptedProvider())
    supervisor = FakeLocalHaasSupervisor()
    manager._haas_supervisor = supervisor
    client = TestClient(create_app(manager))

    after = client.post(
        "/v1/settings/haas-delegation",
        json={
            "enabled": True,
            "local_autostart": True,
            "image_digest": "sha256:test",
        },
    ).json()

    assert after["local_status"]["running"] is True


def test_local_haas_binding_rehydrates_missing_transcript_context(tmp_path, monkeypatch):
    cfg = _local_api_config()
    monkeypatch.setattr(SessionManager, "_haas_config", lambda self, workspace: cfg)
    manager = SessionManager(workspace=tmp_path, provider=ScriptedProvider())
    engine = manager.get_engine("s1", workspace=str(tmp_path), agent="cowork")
    assert engine is not None
    manager.save("s1", engine)

    bridge = StreamBridgeState(
        endpoint_id=cfg.base_url,
        session=SessionKey(cfg.harness_id, cfg.user_id, "hsess_s1"),
        invocation_id="inv_previous",
        assistant_text="Recovered the partial deliverable summary.",
        reasoning_summary="Inspected Quarto and cloned the template.",
    )
    bridge.activities["call_quarto"] = {
        "id": "call_quarto",
        "kind": "command",
        "status": "failed",
        "summary": "Run command",
        "commandPreview": "which quarto && quarto --version",
        "outputPreview": "quarto not found",
        "exitCode": 1,
    }
    bridge.terminal_status = "failed"
    bridge.terminal_code = "haas_request_timeout"
    bridge.terminal_safe_reason = "Codex turn timed out"
    bridge.terminal_retryable = False
    bridge.completed = True
    binding = manager._direct_haas_binding(session_id="s1", config=cfg, workspace=str(tmp_path))
    binding.update(
        {
            "accepted_invocation_id": "inv_previous",
            "control_state": "idle",
            "stream_bridge": bridge.to_dict(),
            "last_user_message": {
                "content": "初始化 Quarto revealjs slide 项目",
                "display": None,
                "ts": 1789456573.0,
            },
        }
    )
    manager._persist_haas_binding("s1", engine, binding)
    manager._engines.pop("s1", None)

    restored = manager.get_engine("s1", workspace=str(tmp_path), agent="cowork")
    assert restored is not None
    restored_context = [
        message for message in restored.messages if message["role"] != "system"
    ]
    assert [message["role"] for message in restored_context] == ["user", "assistant"]
    assert restored_context[0]["content"] == "初始化 Quarto revealjs slide 项目"
    assert restored_context[1]["content"] == "Recovered the partial deliverable summary."
    assert restored_context[1]["_haas_task_outcome"]["status"] == "failed"


@pytest.mark.asyncio
async def test_local_profile_injects_recall_mcp_without_rewriting_user_prompt(
    tmp_path, monkeypatch
):
    cfg = _local_api_config()
    monkeypatch.setattr(SessionManager, "_haas_config", lambda self, workspace: cfg)
    direct = CapturingDirectHaasClient(cfg)
    manager = SessionManager(workspace=tmp_path, provider=ScriptedProvider())
    manager._haas_supervisor = FakeLocalHaasSupervisor()
    manager._haas_direct_client_factory = lambda _config: direct
    engine = manager.get_engine("s1", workspace=str(tmp_path), agent="cowork")
    assert engine is not None
    engine.messages.append(
        {
            "role": "user",
            "content": "初始化 Quarto revealjs slide 项目",
            "_delegated": {
                "backend": "haas",
                "execution_mode": "local_api",
                "session": "hsess_s1",
            },
        }
    )
    engine.messages.append(
        {
            "role": "assistant",
            "content": "Quarto is missing and template clone started.",
            "_delegated": {
                "backend": "haas",
                "execution_mode": "local_api",
                "session": "hsess_s1",
            },
            "_haas_activity": [
                {
                    "id": "call_quarto",
                    "kind": "command",
                    "status": "failed",
                    "summary": "Run command",
                    "commandPreview": "which quarto && quarto --version",
                    "outputPreview": "quarto not found",
                }
            ],
            "_haas_task_outcome": {
                "phase": "failed",
                "status": "failed",
                "code": "haas_request_timeout",
                "safeReason": "Codex turn timed out",
            },
        }
    )
    manager.save("s1", engine)
    binding = manager._direct_haas_binding(session_id="s1", config=cfg, workspace=str(tmp_path))
    binding["recall_token"] = "fixture_recall_token"
    binding["control_state"] = "idle"
    manager._persist_haas_binding("s1", engine, binding)

    events = [
        event
        async for event in manager._run_haas_local_api_turn(
            "s1",
            engine,
            "继续上一个未完成的任务",
            config=cfg,
            binding=binding,
            append_user=True,
            display=None,
        )
    ]

    assert any(event.type.value == "turn_end" for event in events)
    submitted = direct.submitted_messages[-1]
    submitted_text = json.dumps(submitted, ensure_ascii=False)
    assert "继续上一个未完成的任务" in submitted_text
    assert "初始化 Quarto revealjs slide 项目" not in submitted_text
    assert "Quarto is missing and template clone started." not in submitted_text
    assert "which quarto && quarto --version" not in submitted_text
    assert "quarto not found" not in submitted_text
    mcp_servers = direct.profile_configuration["mcpServers"]
    assert mcp_servers == [
        {
            "name": "manager-cowork-recall",
            "url": "http://127.0.0.1:8765/mcp/cowork-recall",
            "transport": "http",
            "enabled": True,
            "required": False,
            "haas_builtin": True,
            "headers": {
                "X-HaaS-Session-ID": "hsess_s1",
                "X-HaaS-Recall-Token": "fixture_recall_token",
            },
            "timeoutSeconds": 10,
            "tools": {
                "recall": {
                    "description": "Recall scoped Cowork memories and recent session history.",
                }
            },
        }
    ]


def test_cowork_recall_mcp_returns_scoped_memory_and_session_history(tmp_path, monkeypatch):
    cfg = _local_api_config()
    monkeypatch.setattr(SessionManager, "_haas_config", lambda self, workspace: cfg)
    manager = SessionManager(workspace=tmp_path, provider=ScriptedProvider())
    manager.memory_store.add(
        "Use the Quarto revealjs template",
        scope=Scope.WORKSPACE,
        workspace=str(tmp_path),
    )
    engine = manager.get_engine("s1", workspace=str(tmp_path), agent="cowork")
    assert engine is not None
    engine.messages.extend(
        [
            {"role": "user", "content": "初始化 Quarto revealjs slide 项目"},
            {"role": "assistant", "content": "Template cloned; Quarto missing."},
        ]
    )
    manager.save("s1", engine)
    binding = manager._direct_haas_binding(session_id="s1", config=cfg, workspace=str(tmp_path))
    binding["recall_token"] = "fixture_recall_token"
    manager._persist_haas_binding("s1", engine, binding)
    client = TestClient(create_app(manager))

    response = client.post(
        "/mcp/cowork-recall",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "recall", "arguments": {"query": "Quarto", "limit": 5}},
        },
        headers={"X-HaaS-Session-ID": "hsess_s1", "X-HaaS-Recall-Token": "fixture_recall_token"},
    )

    assert response.status_code == 200
    result = response.json()["result"]["structuredContent"]
    assert result["sessionId"] == "s1"
    assert result["haasSessionId"] == "hsess_s1"
    assert result["memories"][0]["content"] == "Use the Quarto revealjs template"
    assert [item["role"] for item in result["transcript"]] == ["user", "assistant"]
    denied = client.post(
        "/mcp/cowork-recall",
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "recall", "arguments": {"query": "Quarto"}},
        },
        headers={"X-HaaS-Session-ID": "hsess_s1"},
    ).json()["result"]["structuredContent"]
    assert denied["memories"] == []
    assert denied["transcript"] == []


def test_local_haas_supervisor_rejects_non_loopback_url(tmp_path):
    cfg = _haas_config()
    cfg.base_url = "https://haas.example.com"
    cfg.local_autostart = True
    supervisor = LocalHaasSupervisor(tmp_path)

    status = supervisor.ensure(cfg)

    assert status["status"] == "blocked"
    assert status["reason"] == "local_autostart_requires_loopback"


def test_local_haas_supervisor_process_env_omits_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-real-secret")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-secret")
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setenv("COWORKER_EXIT_WITH_PARENT", "1")
    monkeypatch.setenv("COWORKER_PARENT_PID", "12345")
    supervisor = LocalHaasSupervisor(tmp_path)
    env = supervisor._process_env(tmp_path / "haas.yaml")

    assert env["PATH"] == "/usr/bin"
    assert env["HAAS_CONFIG"] == str(tmp_path / "haas.yaml")
    assert env["COWORKER_EXIT_WITH_PARENT"] == "1"
    assert env["COWORKER_PARENT_PID"] != "12345"
    assert env["COWORKER_PARENT_PID"].isdigit()
    assert "OPENAI_API_KEY" not in env
    assert "ANTHROPIC_API_KEY" not in env
    assert "local-haas-token" not in str(env)


def test_local_haas_supervisor_always_binds_child_to_current_manager(tmp_path, monkeypatch):
    monkeypatch.delenv("COWORKER_EXIT_WITH_PARENT", raising=False)
    monkeypatch.delenv("COWORKER_PARENT_PID", raising=False)
    supervisor = LocalHaasSupervisor(tmp_path)

    env = supervisor._process_env(tmp_path / "haas.yaml")

    assert env["COWORKER_EXIT_WITH_PARENT"] == "1"
    assert env["COWORKER_PARENT_PID"] == str(os.getpid())


def test_local_haas_supervisor_does_not_adopt_healthy_unowned_listener(
    tmp_path, monkeypatch
):
    cfg = _haas_config()
    supervisor = LocalHaasSupervisor(tmp_path, resolve_credential=lambda _provider: "secret")
    monkeypatch.setattr(supervisor, "_healthy", lambda _url: True)
    monkeypatch.setattr("coworker.delegation.time.sleep", lambda _seconds: None)

    status = supervisor.ensure(cfg)

    assert status["status"] == "stopped"
    assert status["running"] is False
    assert status["managed"] is False
    assert status["reason"] == "local_sidecar_not_owned"
    with pytest.raises(HaasDelegationError, match="credential channel unavailable"):
        supervisor.grant_credential(
            "openai",
            {
                "harnessId": "chrn_codex_default",
                "sessionId": "hsess_test",
                "model": "gpt-test",
                "baseUrl": "https://api.openai.com/v1",
            },
        )


def test_local_haas_supervisor_waits_for_predecessor_listener_to_drain(
    tmp_path, monkeypatch
):
    supervisor = LocalHaasSupervisor(tmp_path)
    health = iter([True, True, False])
    sleeps = []
    monkeypatch.setattr(supervisor, "_healthy", lambda _url: next(health))
    monkeypatch.setattr("coworker.delegation.time.sleep", sleeps.append)

    assert supervisor._wait_for_listener_to_drain("http://127.0.0.1:8092") is True
    assert sleeps == [0.1, 0.1, 0.1]


def test_local_haas_supervisor_dev_command_runs_parent_watched_entrypoint(
    tmp_path, monkeypatch
):
    supervisor = LocalHaasSupervisor(tmp_path)
    monkeypatch.setattr(sys, "executable", "/test/manager/.venv/bin/python")

    cmd = supervisor._command(host="127.0.0.1", port=58092, config_path=tmp_path / "haas.yaml")

    assert cmd == [
        "/test/manager/.venv/bin/python",
        "-m",
        "coworker.server.run",
        "haas-sidecar",
        "--host",
        "127.0.0.1",
        "--port",
        "58092",
    ]


def test_local_haas_supervisor_writes_private_token_file(tmp_path):
    cfg = _haas_config()
    cfg.api_token = "local-haas-token"
    supervisor = LocalHaasSupervisor(tmp_path)

    config_path = supervisor._write_config(cfg, host="127.0.0.1", port=58092)
    token_path = tmp_path / "haas-token"

    assert token_path.read_text().strip() == "local-haas-token"
    assert token_path.stat().st_mode & 0o777 == 0o600
    config_text = config_path.read_text()
    assert "static_token_file" in config_text
    assert "container_backend: disabled" in config_text
    assert "allow_unpinned_local_image: false" in config_text
    assert "local-haas-token" not in config_text


def test_local_haas_supervisor_generates_random_token_when_unconfigured(tmp_path):
    cfg = _haas_config()
    cfg.api_token = ""
    supervisor = LocalHaasSupervisor(tmp_path)

    supervisor._write_config(cfg, host="127.0.0.1", port=58092)
    first = cfg.api_token
    assert len(first) >= 43
    assert first != "dev-token"
    assert (tmp_path / "haas-token").stat().st_mode & 0o777 == 0o600

    cfg.api_token = ""
    supervisor._write_config(cfg, host="127.0.0.1", port=58092)
    assert cfg.api_token == first


def test_manager_reloads_generated_local_token(tmp_path, monkeypatch):
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    manager = SessionManager(data_dir=tmp_path / "data", provider=ScriptedProvider())
    cfg = manager._haas_config(None)
    assert cfg.api_token == ""
    manager._haas_supervisor._write_config(cfg, host="127.0.0.1", port=58092)

    reloaded = manager._haas_config(None)
    assert reloaded.api_token == cfg.api_token
    assert len(reloaded.api_token) >= 43


def test_local_haas_supervisor_rejects_symlinked_token(tmp_path):
    target = tmp_path / "target"
    target.write_text("not-a-token")
    (tmp_path / "haas-token").symlink_to(target)
    cfg = _haas_config()
    cfg.api_token = ""
    supervisor = LocalHaasSupervisor(tmp_path)

    assert supervisor.token() is None
    with pytest.raises(HaasDelegationError, match="token path is unsafe"):
        supervisor._write_config(cfg, host="127.0.0.1", port=58092)


def test_haas_client_rejects_missing_token_before_request() -> None:
    cfg = _haas_config()
    cfg.api_token = ""
    with pytest.raises(HaasDelegationError, match="credential is unavailable"):
        HaasDelegationClient(cfg)._headers()


def test_local_haas_supervisor_can_write_local_image_dev_switch(tmp_path):
    cfg = _haas_config()
    cfg.allow_unpinned_local_image = True
    supervisor = LocalHaasSupervisor(tmp_path)

    config_path = supervisor._write_config(cfg, host="127.0.0.1", port=58092)

    assert "allow_unpinned_local_image: true" in config_path.read_text()


def test_local_haas_supervisor_uses_packaged_server_subcommand(tmp_path, monkeypatch):
    supervisor = LocalHaasSupervisor(tmp_path)
    monkeypatch.setattr(sys, "executable", "/Applications/OpenHarness.app/openworker-server")

    cmd = supervisor._command(host="127.0.0.1", port=58092, config_path=tmp_path / "haas.yaml")

    assert cmd == [
        "/Applications/OpenHarness.app/openworker-server",
        "haas-sidecar",
        "--host",
        "127.0.0.1",
        "--port",
        "58092",
    ]


def test_packaged_supervisor_uses_bundled_codex_before_host_path(tmp_path, monkeypatch):
    executable = tmp_path / "sidecar" / "openworker-server"
    codex_dir = executable.parent / "_internal" / "codex"
    codex_dir.mkdir(parents=True)
    codex = codex_dir / "codex"
    codex.write_text("test executable")
    codex.chmod(0o755)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(executable))
    monkeypatch.setenv("PATH", "/usr/bin:/bin")

    env = LocalHaasSupervisor(tmp_path / "state")._process_env(tmp_path / "haas.yaml")

    assert env["PATH"].split(":")[0] == str(codex_dir)
    assert env["HOME"] == str(tmp_path / "state" / "home")


def test_packaged_supervisor_rejects_missing_bundled_codex(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "openworker-server"))
    monkeypatch.setenv("PATH", "/usr/bin:/bin")

    with pytest.raises(HaasDelegationError, match="bundled Codex executable is unavailable"):
        LocalHaasSupervisor(tmp_path)._process_env(tmp_path / "haas.yaml")


def test_missing_bundled_codex_keeps_manager_startup_available(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "openworker-server"))
    supervisor = LocalHaasSupervisor(tmp_path)
    monkeypatch.setattr(supervisor, "_healthy", lambda _url: False)
    cfg = _local_api_config()
    cfg.local_autostart = True

    status = supervisor.ensure(cfg)

    assert status["status"] == "stopped"
    assert status["reason"] == "bundled_codex_unavailable"
    assert not status["managed"]


async def test_startup_and_shutdown_manage_local_haas(tmp_path, monkeypatch):
    cfg = _haas_config()
    cfg.local_autostart = True
    monkeypatch.setattr(SessionManager, "_haas_config", lambda self, workspace: cfg)
    manager = SessionManager(data_dir=tmp_path / "data", provider=ScriptedProvider())
    supervisor = FakeLocalHaasSupervisor()
    manager._haas_supervisor = supervisor

    await manager.start_gateway()
    await manager.aclose()

    assert supervisor.ensured
    assert supervisor.stopped == 1


async def test_haas_client_wraps_network_errors(monkeypatch):
    async def boom(*args, **kwargs):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx.AsyncClient, "post", boom)
    client = HaasDelegationClient(_haas_config())

    try:
        await client.restore("dgsess_1")
    except HaasDelegationError as exc:
        assert "unreachable" in str(exc)
    else:
        raise AssertionError("expected HaasDelegationError")


def test_ws_fails_closed_when_haas_omits_required_accepted_headers(tmp_path, monkeypatch):
    from haas.api import build_app as build_haas_app
    from haas.harnesses import FakeAdapter
    from haas.identity import Principal
    from haas.runtime import FakeDelegatedContainerRuntime

    cfg = _haas_config()
    cfg.api_token = "haas-token"
    monkeypatch.setattr(SessionManager, "_haas_config", lambda self, workspace: cfg)
    monkeypatch.setattr(SessionManager, "_maybe_autotitle", lambda self, session_id: None)

    haas_app = build_haas_app(
        adapter=FakeAdapter(),
        delegated_containers=FakeDelegatedContainerRuntime(),
        identity_tokens={
            "haas-token": Principal(
                principalId="p_manager",
                tenantId="t_manager",
                userIds=frozenset({"manager"}),
            )
        },
    )

    def factory(config: HaasDelegationConfig) -> HaasDelegationClient:
        return HaasDelegationClient(
            config,
            transport=httpx.ASGITransport(app=haas_app),
        )

    manager = SessionManager(
        workspace=tmp_path,
        provider=ScriptedProvider(),
        haas_client_factory=factory,
    )
    manager.workspace_trust.set_trusted(tmp_path, True)
    client = TestClient(create_app(manager))

    with client.websocket_connect("/ws/session/s1?agent=code") as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"type": "user_message", "text": "fix this", "model": "volcengine-ark:m1"})
        seen = []
        while True:
            event = ws.receive_json()
            seen.append(event)
            if event["type"] == "turn_done":
                break

    assert [event["type"] for event in seen] == ["error", "turn_done"]
    assert manager.session_store.load("s1").bindings.get("haas_delegation") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("read_error", [False, True])
async def test_adk_disconnect_waits_for_delayed_native_terminal(tmp_path, monkeypatch, read_error):
    class ClosedStream(TerminalReconciliationRunStream):
        async def events(self):
            if read_error:
                raise HaasClientError("stream lost")
            if False:
                yield {}

    class DelayedClient(TerminalReconciliationDirectHaasClient):
        def __init__(self, config):
            super().__init__(config)
            self.stream = ClosedStream()
            self.reads = 0

        async def events_page(self, session_id, *, after_event_id=None, limit=100):
            self.reads += 1
            if self.reads < 4:
                return HaasEnvelope([], trace_id="tr_waiting")
            return HaasEnvelope([{
                "eventId": "evt_delayed_failure", "invocationId": "inv_terminal_reconcile",
                "type": "haas.turn.failed",
                "haas": {"status": "failed", "code": "haas_request_timeout",
                         "safeReason": "timeout", "retryable": False},
            }], trace_id="tr_terminal")

        async def get_invocation(self, session_id, invocation_id):
            return HaasEnvelope({"status": "running" if self.reads < 4 else "failed"},
                                trace_id="tr_invocation")

    cfg = _local_api_config()
    monkeypatch.setattr(SessionManager, "_haas_config", lambda self, workspace: cfg)
    direct = DelayedClient(cfg)
    manager = SessionManager(workspace=tmp_path, provider=ScriptedProvider())
    manager._haas_supervisor = FakeLocalHaasSupervisor()
    manager._haas_direct_client_factory = lambda _config: direct
    engine = manager.get_engine("s1", workspace=str(tmp_path), agent="cowork")
    manager.save("s1", engine)

    async def collect():
        return [event async for event in manager._run_haas_local_api_turn(
            "s1", engine, "fixture", config=cfg, binding=None,
            append_user=True, display=None,
        )]

    events = await asyncio.wait_for(collect(), 3)
    terminals = [event for event in events if event.type.value == "turn_end"]
    assert len(terminals) == 1
    assert terminals[0].data["status"] == "failed"
    assert terminals[0].data["code"] == "haas_request_timeout"
    assert len(direct.runs) == 1
    assert manager.session_store.load("s1").bindings["haas_delegation"]["control_state"] == "idle"
