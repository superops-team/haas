from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

import httpx

from .endpoint import HaasEndpoint

T = TypeVar("T")


class HaasClientError(RuntimeError):
    """Base class for safe Manager-facing HaaS client failures."""


class HaasProtocolError(HaasClientError):
    """HaaS returned a response that violates its public protocol."""


class HaasTransportError(HaasClientError):
    """The configured HaaS endpoint could not be reached."""


class HaasRemoteError(HaasClientError):
    def __init__(
        self,
        *,
        status_code: int,
        detail: str,
        error_type: str,
        code: str,
        safe_reason: str | None,
        retryable: bool,
        trace_id: str | None,
        param: str | None = None,
        accepted: bool = False,
    ) -> None:
        super().__init__(safe_reason or code)
        self.status_code = status_code
        self.detail = detail
        self.error_type = error_type
        self.code = code
        self.safe_reason = safe_reason
        self.retryable = retryable
        self.trace_id = trace_id
        self.param = param
        self.accepted = accepted


@dataclass(frozen=True, slots=True)
class HaasEnvelope(Generic[T]):  # noqa: UP046 - package supports Python 3.10
    data: T
    trace_id: str
    next_cursor: str | None = None


@dataclass(frozen=True, slots=True)
class AcceptedInvocationHeaders:
    invocation_id: str
    session_id: str
    idempotency_expires_at_ms: int

    @classmethod
    def parse(cls, headers: Mapping[str, str]) -> AcceptedInvocationHeaders:
        lowered = {key.lower(): value for key, value in headers.items()}
        invocation_id = lowered.get("x-haas-invocation-id", "")
        session_id = lowered.get("x-haas-session-id", "")
        expires_raw = lowered.get("idempotency-expires-at", "")
        try:
            expires_at = int(expires_raw)
        except (TypeError, ValueError) as exc:
            raise HaasProtocolError("invalid accepted invocation expiry header") from exc
        if not invocation_id.startswith("inv_") or not session_id or expires_at < 0:
            raise HaasProtocolError("invalid accepted invocation headers")
        return cls(invocation_id, session_id, expires_at)


@dataclass(frozen=True, slots=True)
class HaasTimeouts:
    connect: float = 5.0
    response_header: float = 30.0
    stream_idle: float = 90.0
    turn: float = 900.0

    def __post_init__(self) -> None:
        if min(self.connect, self.response_header, self.stream_idle, self.turn) <= 0:
            raise ValueError("HaaS timeouts must be positive")


class HaasSseStream:
    def __init__(self, response: httpx.Response, accepted: AcceptedInvocationHeaders) -> None:
        self._response = response
        self.accepted = accepted

    async def events(self) -> AsyncIterator[dict[str, Any]]:
        async for event in _sse_events(self._response):
            yield event


class HaasClient:
    def __init__(
        self,
        endpoint: HaasEndpoint,
        *,
        token_resolver: Callable[[str], str],
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: httpx.Timeout | float = 30.0,
        timeouts: HaasTimeouts | None = None,
        reconnect_max_attempts: int = 0,
        reconnect_backoff_initial: float = 0.25,
    ) -> None:
        self.endpoint = endpoint
        self._token_resolver = token_resolver
        self._transport = transport
        self._timeout = timeout
        self.timeouts = timeouts or HaasTimeouts()
        self._reconnect_max_attempts = max(0, reconnect_max_attempts)
        self._reconnect_backoff_initial = max(0.0, reconnect_backoff_initial)

    async def health(self) -> HaasEnvelope[dict[str, Any]]:
        return await self._request("GET", "/v1/haas/health")

    async def ready(self, *, scope: str = "control") -> HaasEnvelope[dict[str, Any]]:
        return await self._request("GET", "/v1/haas/ready", params={"scope": scope})

    async def capabilities(self) -> HaasEnvelope[dict[str, Any]]:
        envelope = await self._request("GET", "/v1/haas/capabilities")
        if not isinstance(envelope.data, dict):
            raise HaasProtocolError("HaaS capabilities must be an object")
        return envelope

    async def sync_profile(
        self,
        configuration: Mapping[str, Any],
        *,
        profile_id: str | None = None,
    ) -> dict[str, Any]:
        if profile_id is not None:
            _require_id(profile_id, "hprof_", "profile")
            try:
                current = (await self._request("GET", f"/v1/haas/profiles/{profile_id}")).data
            except HaasRemoteError as exc:
                if exc.status_code != 404:
                    raise
            else:
                if (
                    isinstance(current, dict)
                    and current.get("status") in {"active", "retired"}
                    and all(current.get(key) == value for key, value in configuration.items())
                ):
                    return current
        created = await self._request(
            "POST",
            "/v1/haas/profiles",
            json_body=configuration,
            idempotency_key=f"profile:{uuid.uuid4().hex}",
        )
        profile = _object_envelope(created, "profile").data
        profile_id = profile["id"]
        validation = await self._request("POST", f"/v1/haas/profiles/{profile_id}/validate")
        if not validation.data.get("valid"):
            raise HaasProtocolError("HaaS profile validation failed")
        activated = await self._request("POST", f"/v1/haas/profiles/{profile_id}/activate")
        return _object_envelope(activated, "profile").data

    async def rebind_profile(
        self,
        session_id: str,
        profile_id: str,
        *,
        expected_version: int | None = None,
    ) -> HaasEnvelope[dict[str, Any]]:
        _require_id(session_id, "hsess_", "session")
        _require_id(profile_id, "hprof_", "profile")
        body: dict[str, Any] = {"profileId": profile_id}
        if expected_version is not None:
            body["expectedProfileVersion"] = expected_version
        return await self._request(
            "POST",
            f"/v1/haas/sessions/{session_id}/profile-rebind",
            json_body=body,
            idempotency_key=f"rebind:{session_id}:{profile_id}",
            timeout=self.timeouts.turn + self.timeouts.response_header,
        )

    async def get_session_profile(self, session_id: str) -> HaasEnvelope[dict[str, Any]]:
        _require_id(session_id, "hsess_", "session")
        envelope = await self._request("GET", f"/v1/haas/sessions/{session_id}/profile")
        return _object_envelope(envelope, "session profile")

    async def get_delegated_session(
        self, delegated_session_id: str
    ) -> HaasEnvelope[dict[str, Any]]:
        _require_id(delegated_session_id, "dgsess_", "delegated session")
        envelope = await self._request("GET", f"/v1/haas/delegated-sessions/{delegated_session_id}")
        return _object_envelope(envelope, "delegated session")

    async def update_delegated_policy(
        self,
        delegated_session_id: str,
        policy_update: Mapping[str, Any],
        *,
        idempotency_key: str,
    ) -> HaasEnvelope[dict[str, Any]]:
        _require_id(delegated_session_id, "dgsess_", "delegated session")
        allowed = {"profileRef", "delegationPolicySnapshot", "mountManifest", "image"}
        if not policy_update or not set(policy_update).issubset(allowed):
            raise ValueError("policy update requires one or more complete supported domains")
        envelope = await self._request(
            "POST",
            f"/v1/haas/delegated-sessions/{delegated_session_id}/policy",
            json_body=policy_update,
            idempotency_key=idempotency_key,
        )
        return _object_envelope(envelope, "delegated session")

    async def update_session_policy(
        self,
        session_id: str,
        policy: Mapping[str, Any],
        *,
        expected_revision: int,
        idempotency_key: str,
    ) -> HaasEnvelope[dict[str, Any]]:
        _require_id(session_id, "hsess_", "session")
        allowed = {"workspace", "network", "tools"}
        if (
            expected_revision < 1
            or not policy
            or not set(policy).issubset(allowed)
            or not all(isinstance(value, Mapping) for value in policy.values())
        ):
            raise ValueError("session policy requires complete supported domains")
        envelope = await self._request(
            "POST",
            f"/v1/haas/sessions/{session_id}/policy",
            json_body={"expectedRevision": expected_revision, "policy": dict(policy)},
            idempotency_key=idempotency_key,
        )
        return _object_envelope(envelope, "session policy")

    async def get_invocation(
        self, session_id: str, invocation_id: str
    ) -> HaasEnvelope[dict[str, Any]]:
        _require_id(session_id, "hsess_", "session")
        _require_id(invocation_id, "inv_", "invocation")
        envelope = await self._request(
            "GET", f"/v1/haas/sessions/{session_id}/invocations/{invocation_id}"
        )
        return _object_envelope(envelope, "invocation")

    async def cancel_invocation(
        self, session_id: str, invocation_id: str
    ) -> HaasEnvelope[dict[str, Any]]:
        _require_id(session_id, "hsess_", "session")
        _require_id(invocation_id, "inv_", "invocation")
        envelope = await self._request(
            "POST",
            f"/v1/haas/sessions/{session_id}/invocations/{invocation_id}/cancel",
            idempotency_key=f"mgr-cancel:{invocation_id}",
        )
        return _object_envelope(envelope, "invocation cancellation")

    async def pause_invocation(
        self, session_id: str, invocation_id: str
    ) -> HaasEnvelope[dict[str, Any]]:
        _require_id(session_id, "hsess_", "session")
        _require_id(invocation_id, "inv_", "invocation")
        envelope = await self._request(
            "POST",
            f"/v1/haas/sessions/{session_id}/invocations/{invocation_id}/pause",
            idempotency_key=f"mgr-pause:{invocation_id}",
            timeout=self.timeouts.turn + self.timeouts.response_header,
        )
        return _object_envelope(envelope, "invocation pause")

    @asynccontextmanager
    async def continue_invocation(
        self,
        session_id: str,
        invocation_id: str,
        *,
        additional_instruction: str | None = None,
    ) -> AsyncIterator[HaasSseStream]:
        _require_id(session_id, "hsess_", "session")
        _require_id(invocation_id, "inv_", "invocation")
        body = (
            {}
            if additional_instruction is None
            else {"additionalInstruction": additional_instruction}
        )
        timeout = httpx.Timeout(
            connect=self.timeouts.connect,
            read=self.timeouts.stream_idle,
            write=self.timeouts.response_header,
            pool=self.timeouts.connect,
        )
        try:
            async with (
                httpx.AsyncClient(
                    base_url=self.endpoint.base_url,
                    verify=self.endpoint.tls_verify,
                    timeout=timeout,
                    transport=self._transport,
                ) as client,
                client.stream(
                    "POST",
                    f"/v1/haas/sessions/{session_id}/invocations/{invocation_id}/continue",
                    json=body,
                    headers=self._headers(idempotency_key=f"mgr-continue:{invocation_id}"),
                ) as response,
            ):
                if response.status_code >= 400:
                    await response.aread()
                    raise _remote_error(response)
                yield HaasSseStream(response, AcceptedInvocationHeaders.parse(response.headers))
        except httpx.HTTPError as exc:
            raise HaasTransportError("HaaS endpoint is unreachable") from exc

    async def get_execution_evidence(
        self, session_id: str, invocation_id: str, tool_call_id: str, evidence_ref: str
    ) -> HaasEnvelope[dict[str, Any]]:
        _require_id(session_id, "hsess_", "session")
        _require_id(invocation_id, "inv_", "invocation")
        _require_id(evidence_ref, "evd_", "execution evidence")
        if not tool_call_id:
            raise ValueError("tool call id is required")
        envelope = await self._request(
            "GET",
            f"/v1/haas/sessions/{session_id}/invocations/{invocation_id}/tools/{tool_call_id}/evidence",
            params={"evidence_ref": evidence_ref},
        )
        return _object_envelope(envelope, "execution evidence")

    async def list_artifacts(self, session_id: str) -> HaasEnvelope[list[dict[str, Any]]]:
        _require_id(session_id, "hsess_", "session")
        envelope = await self._request("GET", f"/v1/haas/sessions/{session_id}/artifacts")
        data = envelope.data
        if not isinstance(data, dict):
            raise HaasProtocolError("HaaS artifact list must be an object")
        artifacts = data.get("artifacts")
        if not isinstance(artifacts, list) or not all(
            isinstance(item, dict) for item in artifacts
        ):
            raise HaasProtocolError("HaaS artifact list must contain file objects")
        return HaasEnvelope(artifacts, envelope.trace_id, envelope.next_cursor)

    async def download_file(self, file_id: str) -> tuple[bytes, str]:
        _require_id(file_id, "file_", "file")
        try:
            async with httpx.AsyncClient(
                base_url=self.endpoint.base_url,
                verify=self.endpoint.tls_verify,
                timeout=self._timeout,
                transport=self._transport,
            ) as client:
                response = await client.get(
                    f"/v1/haas/files/{file_id}/content", headers=self._headers()
                )
        except httpx.HTTPError as exc:
            raise HaasTransportError("HaaS endpoint is unreachable") from exc
        if response.status_code >= 400:
            raise _remote_error(response)
        return response.content, response.headers.get("content-type", "application/octet-stream")

    async def download_artifact_archive(self, session_id: str) -> tuple[bytes, str]:
        _require_id(session_id, "hsess_", "session")
        try:
            async with httpx.AsyncClient(
                base_url=self.endpoint.base_url,
                verify=self.endpoint.tls_verify,
                timeout=self._timeout,
                transport=self._transport,
            ) as client:
                response = await client.get(
                    f"/v1/haas/sessions/{session_id}/artifacts/archive",
                    headers=self._headers(),
                )
        except httpx.HTTPError as exc:
            raise HaasTransportError("HaaS endpoint is unreachable") from exc
        if response.status_code >= 400:
            raise _remote_error(response)
        return response.content, response.headers.get("content-type", "application/zip")

    async def events_page(
        self,
        session_id: str,
        *,
        after_event_id: str | None = None,
        limit: int = 100,
    ) -> HaasEnvelope[list[dict[str, Any]]]:
        _require_id(session_id, "hsess_", "session")
        if limit < 1 or limit > 1000:
            raise ValueError("events page limit must be between 1 and 1000")
        params: dict[str, Any] = {"limit": limit}
        if after_event_id is not None:
            _require_id(after_event_id, "evt_", "event")
            params["after_event_id"] = after_event_id
        envelope = await self._request(
            "GET", f"/v1/haas/sessions/{session_id}/events-page", params=params
        )
        if not isinstance(envelope.data, list) or not all(
            isinstance(item, dict) for item in envelope.data
        ):
            raise HaasProtocolError("HaaS events page must contain event objects")
        return envelope

    async def list_approvals(
        self, session_id: str, *, status: str = "waiting"
    ) -> HaasEnvelope[list[dict[str, Any]]]:
        _require_id(session_id, "hsess_", "session")
        envelope = await self._request(
            "GET", f"/v1/haas/sessions/{session_id}/approvals", params={"status": status}
        )
        if not isinstance(envelope.data, list) or not all(
            isinstance(item, dict) for item in envelope.data
        ):
            raise HaasProtocolError("HaaS approvals must contain objects")
        return envelope

    async def resolve_approval(
        self, session_id: str, approval_id: str, *, approved: bool
    ) -> HaasEnvelope[dict[str, Any]]:
        _require_id(session_id, "hsess_", "session")
        _require_id(approval_id, "appr_", "approval")
        decision = "approved" if approved else "denied"
        envelope = await self._request(
            "POST",
            f"/v1/haas/sessions/{session_id}/approvals/{approval_id}",
            json_body={"decision": decision, "scope": "action"},
            idempotency_key=f"manager-approval:{approval_id}:{decision}",
        )
        return _object_envelope(envelope, "approval resolution")

    async def list_input_requests(
        self, session_id: str, *, status: str = "waiting"
    ) -> HaasEnvelope[list[dict[str, Any]]]:
        _require_id(session_id, "hsess_", "session")
        envelope = await self._request(
            "GET",
            f"/v1/haas/sessions/{session_id}/input-requests",
            params={"status": status},
        )
        if not isinstance(envelope.data, list) or not all(
            isinstance(item, dict) for item in envelope.data
        ):
            raise HaasProtocolError("HaaS input requests must contain objects")
        return envelope

    async def answer_input_request(
        self,
        session_id: str,
        input_request_id: str,
        *,
        answers: Mapping[str, Mapping[str, Any]],
    ) -> HaasEnvelope[dict[str, Any]]:
        _require_id(session_id, "hsess_", "session")
        _require_id(input_request_id, "inreq_", "input request")
        envelope = await self._request(
            "POST",
            f"/v1/haas/sessions/{session_id}/input-requests/{input_request_id}",
            json_body={"answers": dict(answers)},
            idempotency_key=f"manager-input:{session_id}:{input_request_id}",
        )
        return _object_envelope(envelope, "input request resolution")

    async def invocation_events(
        self,
        session_id: str,
        invocation_id: str,
        *,
        after_event_id: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Replay and then follow canonical native events for one invocation."""
        _require_id(session_id, "hsess_", "session")
        _require_id(invocation_id, "inv_", "invocation")
        params: dict[str, str] = {}
        if after_event_id is not None:
            _require_id(after_event_id, "evt_", "event")
            params["after_event_id"] = after_event_id
        timeout = httpx.Timeout(
            connect=self.timeouts.connect,
            read=self.timeouts.stream_idle,
            write=self.timeouts.response_header,
            pool=self.timeouts.connect,
        )
        try:
            async with (
                httpx.AsyncClient(
                    base_url=self.endpoint.base_url,
                    verify=self.endpoint.tls_verify,
                    timeout=timeout,
                    transport=self._transport,
                ) as client,
                client.stream(
                    "GET",
                    f"/v1/haas/sessions/{session_id}/invocations/{invocation_id}/events",
                    params=params,
                    headers=self._headers(),
                ) as response,
            ):
                if response.status_code >= 400:
                    await response.aread()
                    raise _remote_error(response)
                async for event in _sse_events(response):
                    yield event
        except httpx.HTTPError as exc:
            raise HaasTransportError("HaaS endpoint is unreachable") from exc

    @asynccontextmanager
    async def run_sse(
        self,
        body: Mapping[str, Any],
        *,
        idempotency_key: str,
        last_event_id: str | None = None,
    ) -> AsyncIterator[HaasSseStream]:
        headers = self._headers(idempotency_key=idempotency_key)
        if last_event_id is not None:
            _require_id(last_event_id, "evt_", "event")
            headers["Last-Event-ID"] = last_event_id
        timeout = httpx.Timeout(
            connect=self.timeouts.connect,
            read=self.timeouts.stream_idle,
            write=self.timeouts.response_header,
            pool=self.timeouts.connect,
        )
        attempts = 0
        while True:
            client = httpx.AsyncClient(
                base_url=self.endpoint.base_url,
                verify=self.endpoint.tls_verify,
                timeout=timeout,
                transport=self._transport,
            )
            context = client.stream("POST", "/run_sse", json=body, headers=headers)
            try:
                response = await context.__aenter__()
            except httpx.HTTPError as exc:
                await client.aclose()
                if attempts >= self._reconnect_max_attempts:
                    raise HaasTransportError("HaaS endpoint is unreachable") from exc
                attempts += 1
                if self._reconnect_backoff_initial:
                    await asyncio.sleep(self._reconnect_backoff_initial * (2 ** (attempts - 1)))
                continue
            try:
                if response.status_code >= 400:
                    await response.aread()
                    raise _remote_error(response)
                yield HaasSseStream(response, AcceptedInvocationHeaders.parse(response.headers))
            finally:
                await context.__aexit__(None, None, None)
                await client.aclose()
            return

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json_body: Mapping[str, Any] | None = None,
        idempotency_key: str | None = None,
        timeout: float | None = None,
    ) -> HaasEnvelope[Any]:
        headers = self._headers(idempotency_key=idempotency_key)
        try:
            async with httpx.AsyncClient(
                base_url=self.endpoint.base_url,
                verify=self.endpoint.tls_verify,
                timeout=timeout if timeout is not None else self._timeout,
                transport=self._transport,
            ) as client:
                response = await client.request(
                    method, path, params=params, json=json_body, headers=headers
                )
        except httpx.HTTPError as exc:
            raise HaasTransportError("HaaS endpoint is unreachable") from exc
        if response.status_code >= 400:
            raise _remote_error(response)
        return _parse_envelope(response)

    def _headers(self, *, idempotency_key: str | None = None) -> dict[str, str]:
        token = self._token_resolver(self.endpoint.token_ref)
        if not isinstance(token, str) or not token:
            raise HaasClientError("HaaS endpoint credential is unavailable")
        headers = {"Authorization": f"Bearer {token}"}
        if idempotency_key is not None:
            if not idempotency_key or len(idempotency_key) > 255:
                raise ValueError("invalid idempotency key")
            headers["Idempotency-Key"] = idempotency_key
        return headers


def _parse_envelope(response: httpx.Response) -> HaasEnvelope[Any]:
    try:
        body = response.json()
    except ValueError as exc:
        raise HaasProtocolError("HaaS returned malformed JSON") from exc
    if not isinstance(body, dict) or "data" not in body:
        raise HaasProtocolError("HaaS returned an invalid envelope")
    trace_id = body.get("traceId")
    if not isinstance(trace_id, str) or not trace_id:
        raise HaasProtocolError("HaaS envelope is missing traceId")
    next_cursor = body.get("nextCursor")
    if next_cursor is not None and not isinstance(next_cursor, str):
        raise HaasProtocolError("HaaS envelope has an invalid cursor")
    return HaasEnvelope(body["data"], trace_id, next_cursor)


async def _sse_events(response: httpx.Response) -> AsyncIterator[dict[str, Any]]:
    async for line in response.aiter_lines():
        line = line.strip()
        if not line or line.startswith(":") or not line.startswith("data:"):
            continue
        payload = line.removeprefix("data:").strip()
        if not payload:
            continue
        try:
            event = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise HaasProtocolError("HaaS returned malformed SSE data") from exc
        if not isinstance(event, dict):
            raise HaasProtocolError("HaaS SSE event must be an object")
        yield event


def _object_envelope(
    envelope: HaasEnvelope[Any], resource_name: str
) -> HaasEnvelope[dict[str, Any]]:
    if not isinstance(envelope.data, dict):
        raise HaasProtocolError(f"HaaS {resource_name} must be an object")
    return envelope


def _require_id(value: str, prefix: str, resource_name: str) -> None:
    if not isinstance(value, str) or not value.startswith(prefix):
        raise ValueError(f"invalid {resource_name} id")
    suffix = value[len(prefix) :]
    if not suffix or not all(ch.isalnum() or ch in {"_", "-"} for ch in suffix):
        raise ValueError(f"invalid {resource_name} id")


def _remote_error(response: httpx.Response) -> HaasRemoteError:
    try:
        body = response.json()
    except ValueError as exc:
        raise HaasProtocolError("HaaS returned a malformed error") from exc
    if not isinstance(body, dict) or not isinstance(body.get("detail"), str):
        raise HaasProtocolError("HaaS returned an invalid error")
    error = body.get("haasError")
    if not isinstance(error, dict):
        raise HaasProtocolError("HaaS returned an unstructured error")
    error_type = error.get("type")
    code = error.get("code")
    if not isinstance(error_type, str) or not isinstance(code, str):
        raise HaasProtocolError("HaaS error is missing type or code")
    safe_reason = error.get("safeReason")
    trace_id = error.get("traceId")
    param = error.get("param")
    return HaasRemoteError(
        status_code=response.status_code,
        detail=body["detail"],
        error_type=error_type,
        code=code,
        safe_reason=safe_reason if isinstance(safe_reason, str) else None,
        retryable=error.get("retryable") is True,
        trace_id=trace_id if isinstance(trace_id, str) else None,
        param=param if isinstance(param, str) else None,
        accepted=error.get("accepted") is True,
    )
