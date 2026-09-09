"""HaaS delegated execution bridge for manager sessions.

HaaS is a complete execution backend, not a model provider. This module keeps
the manager-facing policy and HTTP/SSE translation separate from the local
TurnEngine and provider router.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from .secrets import write_private_text

BINDING_KEY = "haas_delegation"


DEFAULT_TRIGGER_KEYWORDS = [
    "fix",
    "implement",
    "modify",
    "edit",
    "add",
    "update",
    "refactor",
    "debug",
    "test",
    "修复",
    "实现",
    "修改",
    "新增",
    "开发",
    "重构",
    "调试",
    "测试",
]


@dataclass
class HaasDelegationConfig:
    enabled: bool = False
    base_url: str = "http://127.0.0.1:8092"
    api_token: str = "dev-token"
    user_id: str = "manager"
    harness_id: str = "chrn_codex_default"
    harness_base: str = "codex"
    image: str = "haas:local"
    image_digest: str = ""
    strategy: str = "deterministic"
    require_trusted_workspace: bool = True
    agent_allowlist: list[str] = field(default_factory=lambda: ["code"])
    trigger_keywords: list[str] = field(
        default_factory=lambda: list(DEFAULT_TRIGGER_KEYWORDS)
    )
    idle_ttl_seconds: int = 1800
    max_container_lifetime_seconds: int = 28800
    request_timeout_seconds: float = 30.0
    local_autostart: bool = False
    allow_unpinned_local_image: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "base_url": self.base_url,
            "user_id": self.user_id,
            "harness_id": self.harness_id,
            "harness_base": self.harness_base,
            "image": self.image,
            "image_digest": self.image_digest,
            "image_digest_configured": bool(self.image_digest),
            "strategy": self.strategy,
            "require_trusted_workspace": self.require_trusted_workspace,
            "agent_allowlist": list(self.agent_allowlist),
            "trigger_keywords": list(self.trigger_keywords),
            "idle_ttl_seconds": self.idle_ttl_seconds,
            "max_container_lifetime_seconds": self.max_container_lifetime_seconds,
            "request_timeout_seconds": self.request_timeout_seconds,
            "local_autostart": self.local_autostart,
            "allow_unpinned_local_image": self.allow_unpinned_local_image,
        }


def _loopback_target(base_url: str) -> tuple[str, int] | None:
    parsed = urlparse(base_url)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}:
        return None
    try:
        port = parsed.port or 80
    except ValueError:
        return None
    return parsed.hostname, port


class LocalHaasSupervisor:
    """Start and stop a local HaaS sidecar for GUI-launched manager sessions."""

    def __init__(self, data_dir: str | Path) -> None:
        self.data_dir = Path(data_dir).expanduser()
        self._process: subprocess.Popen[Any] | None = None
        self._last_error: str | None = None
        self._base_url: str | None = None

    def status(self, config: HaasDelegationConfig) -> dict[str, Any]:
        target = _loopback_target(config.base_url)
        running = self._process is not None and self._process.poll() is None
        status = "disabled"
        reason: str | None = None
        if config.enabled and config.local_autostart:
            if target is None:
                status = "blocked"
                reason = "local_autostart_requires_loopback"
            elif self._healthy(config.base_url):
                status = "running"
            elif running:
                status = "starting"
            else:
                status = "stopped"
                reason = self._last_error
        return {
            "enabled": bool(config.local_autostart),
            "status": status,
            "running": status == "running",
            "managed": running,
            "pid": self._process.pid if running else None,
            "url": config.base_url.rstrip("/"),
            "reason": reason,
        }

    def ensure(self, config: HaasDelegationConfig) -> dict[str, Any]:
        if not config.enabled or not config.local_autostart:
            self.stop()
            return self.status(config)
        target = _loopback_target(config.base_url)
        if target is None:
            self.stop()
            self._last_error = "local_autostart_requires_loopback"
            return self.status(config)
        base_url = config.base_url.rstrip("/")
        running = self._process is not None and self._process.poll() is None
        if running and self._base_url and self._base_url != base_url:
            self.stop()
        if self._healthy(base_url):
            self._last_error = None
            return self.status(config)
        if self._process is not None and self._process.poll() is None:
            return self.status(config)
        self._process = None

        host, port = target
        config_path = self._write_config(config, host=host, port=port)
        cmd = self._command(host=host, port=port, config_path=config_path)
        log = self._log_file()
        env = self._process_env(config_path)
        env["HAAS_CONFIG"] = str(config_path)
        env["HAAS_STATIC_TOKEN_FILE"] = str(self._token_file(config))
        env.setdefault("HAAS_ADAPTER_BASE", "codex")
        try:
            self._process = subprocess.Popen(
                cmd,
                cwd=str(self._haas_root()),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=log or subprocess.DEVNULL,
                stderr=subprocess.STDOUT if log else subprocess.DEVNULL,
            )
            self._base_url = base_url
        except OSError:
            self._last_error = "local_haas_start_failed"
        finally:
            if log is not None:
                log.close()
        if self._process is None:
            return self.status(config)

        for _ in range(20):
            time.sleep(0.1)
            if self._healthy(base_url):
                self._last_error = None
                return self.status(config)
            if self._process.poll() is not None:
                self._last_error = "local_haas_exited"
                return self.status(config)
        self._last_error = "local_haas_starting"
        return self.status(config)

    def stop(self) -> None:
        if self._process is None:
            return
        proc = self._process
        self._process = None
        self._base_url = None
        if proc.poll() is not None:
            return
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)

    def _healthy(self, base_url: str) -> bool:
        try:
            resp = httpx.get(f"{base_url.rstrip('/')}/v1/haas/health", timeout=0.5)
        except Exception:
            return False
        return resp.status_code == 200

    def _haas_root(self) -> Path:
        return Path(__file__).resolve().parents[2]

    def _command(self, *, host: str, port: int, config_path: Path) -> list[str]:
        override = os.environ.get("COWORKER_HAAS_SERVER_CMD")
        if override:
            return shlex.split(override)
        uv = shutil.which("uv")
        if uv:
            return [
                uv,
                "run",
                "--directory",
                str(self._haas_root()),
                "uvicorn",
                "haas.config:create_app",
                "--factory",
                "--host",
                host,
                "--port",
                str(port),
            ]
        return [
            sys.executable,
            "-m",
            "uvicorn",
            "haas.config:create_app",
            "--factory",
            "--host",
            host,
            "--port",
            str(port),
        ]

    def _process_env(self, config_path: Path) -> dict[str, str]:
        env: dict[str, str] = {"HAAS_CONFIG": str(config_path)}
        for key in (
            "HOME",
            "PATH",
            "TMPDIR",
            "TEMP",
            "TMP",
            "LANG",
            "LC_ALL",
            "SSL_CERT_FILE",
            "REQUESTS_CA_BUNDLE",
        ):
            value = os.environ.get(key)
            if value:
                env[key] = value
        return env

    def _write_config(self, config: HaasDelegationConfig, *, host: str, port: int) -> Path:
        path = self.data_dir / "haas-supervised.yaml"
        token_path = self._token_file(config)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "\n".join(
                [
                    "server:",
                    f'  host: "{host}"',
                    f"  port: {port}",
                    "identity:",
                    "  provider: static",
                    f"  static_token_file: {json.dumps(str(token_path))}",
                    "adapters:",
                    "  default_base: codex",
                    "  codex:",
                    "    transport: stdio",
                    "    socket_path: /tmp/haas/codex.sock",
                    "delegation:",
                    "  container_backend: docker",
                    "  docker_network: none",
                    (
                        "  allow_unpinned_local_image: "
                        f"{str(config.allow_unpinned_local_image).lower()}"
                    ),
                    f"  idle_ttl_seconds: {config.idle_ttl_seconds}",
                    f"  max_container_lifetime_seconds: {config.max_container_lifetime_seconds}",
                    "  rw_workspace_concurrency: single_writer",
                    "  queue_policy: fifo",
                    "  restore_policy: fail_closed",
                    "  policy_change_mode: snapshot_per_session",
                    "  mount_policy: project_rw_extra_ro",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        return path

    def _token_file(self, config: HaasDelegationConfig) -> Path:
        path = self.data_dir / "haas-token"
        write_private_text(path, config.api_token + "\n")
        return path

    def _log_file(self) -> Any:
        log_dir = self.data_dir / "logs"
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            return open(log_dir / "haas-sidecar.log", "a", encoding="utf-8")
        except OSError:
            return None


@dataclass(frozen=True)
class DelegationDecision:
    backend: str
    reason: str
    binding: dict[str, Any] | None = None
    config: HaasDelegationConfig | None = None

    @property
    def use_haas(self) -> bool:
        return self.backend == "haas"


class HaasDelegationError(RuntimeError):
    """Raised when a HaaS-bound session cannot continue safely."""


def binding_from_record(record: Any | None) -> dict[str, Any] | None:
    if record is None:
        return None
    bindings = getattr(record, "bindings", None)
    if not isinstance(bindings, dict):
        return None
    binding = bindings.get(BINDING_KEY)
    return binding if isinstance(binding, dict) else None


def _text_part(item: dict[str, Any]) -> str | None:
    text = item.get("text")
    if not isinstance(text, str):
        return None
    if item.get("type") == "text" or not item.get("type"):
        return text
    return None


def text_from_content(content: str | list[Any]) -> str:
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        if text := _text_part(item):
            parts.append(text)
    return "\n".join(parts)


def is_text_only_content(content: str | list[Any]) -> bool:
    if isinstance(content, str):
        return True
    return all(
        isinstance(item, dict) and _text_part(item) is not None for item in content
    )


def adk_message_from_content(content: str | list[Any]) -> dict[str, Any]:
    if isinstance(content, str):
        return {"role": "user", "parts": [{"text": content}]}
    parts: list[dict[str, str]] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        if text := _text_part(item):
            parts.append({"text": text})
        else:
            raise HaasDelegationError(
                "HaaS delegation currently supports text input only."
            )
    return {"role": "user", "parts": parts}


def deterministic_decision(
    *,
    config: HaasDelegationConfig,
    session_id: str,
    agent: str,
    workspace: str | None,
    workspace_trusted: bool,
    content: str | list[Any],
    binding: dict[str, Any] | None = None,
) -> DelegationDecision:
    if binding is not None:
        return DelegationDecision("haas", "existing_binding", binding, config)
    if not config.enabled:
        return DelegationDecision("local", "disabled", config=config)
    if config.strategy != "deterministic":
        return DelegationDecision("local", "unsupported_strategy", config=config)
    if agent not in set(config.agent_allowlist):
        return DelegationDecision("local", "agent_not_allowed", config=config)
    if not workspace or not Path(workspace).is_dir():
        return DelegationDecision("local", "no_project_workspace", config=config)
    if config.require_trusted_workspace and not workspace_trusted:
        return DelegationDecision("local", "workspace_not_trusted", config=config)
    if not is_text_only_content(content):
        return DelegationDecision("local", "unsupported_content", config=config)
    text = text_from_content(content).lower()
    if not any(keyword.lower() in text for keyword in config.trigger_keywords):
        return DelegationDecision("local", "no_deterministic_trigger", config=config)
    return DelegationDecision("haas", "deterministic_rule", config=config)


def make_delegated_session_body(
    *,
    config: HaasDelegationConfig,
    manager_session_id: str,
    workspace: str,
    model: str,
    extra_roots: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if not config.image_digest and not config.allow_unpinned_local_image:
        raise HaasDelegationError("HaaS delegation image digest is not configured.")
    provider_id, bare_model = split_provider_model(model)
    extra_mounts = []
    for root in extra_roots or []:
        path = str(root.get("path") or "").strip()
        if not path:
            continue
        extra_mounts.append(
            {
                "hostPathCanonical": str(Path(path).expanduser().resolve()),
                "containerPath": f"/mnt/extra/{len(extra_mounts) + 1}",
                "access": "ro",
            }
        )
    return {
        "managerSessionId": manager_session_id,
        "haasSessionId": f"hsess_{manager_session_id}",
        "haasUserId": config.user_id,
        "harnessId": config.harness_id,
        "harnessBase": config.harness_base,
        "image": {"reference": config.image, "digest": config.image_digest},
        "provider": {
            "providerId": provider_id,
            "model": bare_model,
            "credentialRef": f"secret://provider/{provider_id}",
        },
        "mountManifest": {
            "version": 1,
            "primaryWorkspace": {
                "hostPathCanonical": str(Path(workspace).expanduser().resolve()),
                "containerPath": "/workspace",
                "access": "rw",
            },
            "extraMounts": extra_mounts,
        },
        "delegationPolicySnapshot": {
            "version": 1,
            "idleTtlSeconds": config.idle_ttl_seconds,
            "maxContainerLifetimeSeconds": config.max_container_lifetime_seconds,
            "rwWorkspaceConcurrency": "single_writer",
            "queuePolicy": "fifo",
            "restorePolicy": "fail_closed",
            "policyChangeMode": "snapshot_per_session",
            "mountPolicy": "project_rw_extra_ro",
        },
    }


def split_provider_model(model: str) -> tuple[str, str]:
    if ":" in model:
        provider, bare = model.split(":", 1)
        if provider:
            return provider, bare
    return "openai", model


def binding_from_haas_response(
    data: dict[str, Any], config: HaasDelegationConfig
) -> dict[str, Any]:
    return {
        "backend": "haas",
        "haas_base_url": config.base_url.rstrip("/"),
        "delegated_session_id": data["id"],
        "haas_session_id": data["haasSessionId"],
        "haas_user_id": data["haasUserId"],
        "harness_id": data["harnessId"],
        "harness_base": data.get("harnessBase", "codex"),
        "manager_session_id": data["managerSessionId"],
        "mount_manifest": data["mountManifest"],
        "delegation_policy_snapshot": data["delegationPolicySnapshot"],
        "provider": data["provider"],
        "image": data["image"],
        "runtime": data.get("runtime", {}),
        "bound_at": int(time.time()),
    }


def apply_config_snapshot(
    config: HaasDelegationConfig, binding: dict[str, Any]
) -> HaasDelegationConfig:
    return HaasDelegationConfig(
        enabled=config.enabled,
        base_url=str(binding.get("haas_base_url") or config.base_url),
        api_token=config.api_token,
        user_id=str(binding.get("haas_user_id") or config.user_id),
        harness_id=str(binding.get("harness_id") or config.harness_id),
        harness_base=str(binding.get("harness_base") or config.harness_base),
        image=str((binding.get("image") or {}).get("reference") or config.image),
        image_digest=str(
            (binding.get("image") or {}).get("digest") or config.image_digest
        ),
        strategy=config.strategy,
        require_trusted_workspace=config.require_trusted_workspace,
        agent_allowlist=list(config.agent_allowlist),
        trigger_keywords=list(config.trigger_keywords),
        idle_ttl_seconds=int(
            (binding.get("delegation_policy_snapshot") or {}).get(
                "idleTtlSeconds", config.idle_ttl_seconds
            )
        ),
        max_container_lifetime_seconds=int(
            (binding.get("delegation_policy_snapshot") or {}).get(
                "maxContainerLifetimeSeconds", config.max_container_lifetime_seconds
            )
        ),
        request_timeout_seconds=config.request_timeout_seconds,
    )


def require_binding_value(binding: dict[str, Any], key: str) -> str:
    value = binding.get(key)
    if not isinstance(value, str) or not value:
        raise HaasDelegationError(f"HaaS delegation binding is missing {key}.")
    return value


def extract_adk_text(event: dict[str, Any]) -> str:
    content = event.get("content")
    if not isinstance(content, dict):
        return ""
    parts = content.get("parts")
    if not isinstance(parts, list):
        return ""
    return "".join(part.get("text", "") for part in parts if isinstance(part, dict))


def extract_adk_status(event: dict[str, Any]) -> str | None:
    actions = event.get("actions")
    if not isinstance(actions, dict):
        return None
    delta = actions.get("stateDelta")
    if not isinstance(delta, dict):
        return None
    status = delta.get("status")
    return status if isinstance(status, str) else None


class HaasDelegationClient:
    def __init__(
        self,
        config: HaasDelegationConfig,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.config = config
        self._transport = transport

    def _client(self, *, timeout: httpx.Timeout | float | None = None) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.config.base_url.rstrip("/"),
            timeout=timeout
            if timeout is not None
            else self.config.request_timeout_seconds,
            transport=self._transport,
        )

    def _headers(self, *, idempotency_key: str | None = None) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self.config.api_token}"}
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        return headers

    async def create_delegated_session(self, body: dict[str, Any]) -> dict[str, Any]:
        try:
            async with self._client() as client:
                resp = await client.post(
                    "/v1/haas/delegated-sessions",
                    json=body,
                    headers=self._headers(
                        idempotency_key=f"manager-delegate:{body['managerSessionId']}"
                    ),
                )
            return _checked_data(resp)
        except httpx.HTTPError as exc:
            raise HaasDelegationError("HaaS delegated backend is unreachable.") from exc

    async def restore(self, delegated_session_id: str) -> dict[str, Any]:
        try:
            async with self._client() as client:
                resp = await client.post(
                    f"/v1/haas/delegated-sessions/{delegated_session_id}/restore",
                    headers=self._headers(),
                )
            return _checked_data(resp)
        except httpx.HTTPError as exc:
            raise HaasDelegationError("HaaS delegated backend is unreachable.") from exc

    async def run_sse(
        self,
        *,
        haas_session_id: str,
        message: dict[str, Any],
        harness_id: str | None = None,
        user_id: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        body = {
            "appName": harness_id or self.config.harness_id,
            "userId": user_id or self.config.user_id,
            "sessionId": haas_session_id,
            "newMessage": message,
            "streaming": True,
        }
        timeout = httpx.Timeout(
            self.config.request_timeout_seconds,
            read=None,
        )
        try:
            async with (
                self._client(timeout=timeout) as client,
                client.stream(
                    "POST",
                    "/run_sse",
                    json=body,
                    headers=self._headers(
                        idempotency_key=(
                            f"manager-turn:{haas_session_id}:{time.time_ns()}"
                        )
                    ),
                ) as resp,
            ):
                if resp.status_code >= 400:
                    raise HaasDelegationError(_safe_error(resp))
                async for line in resp.aiter_lines():
                    line = line.strip()
                    if not line or line.startswith(":"):
                        continue
                    if not line.startswith("data:"):
                        continue
                    payload = line.removeprefix("data:").strip()
                    if not payload:
                        continue
                    try:
                        event = json.loads(payload)
                    except json.JSONDecodeError as exc:
                        raise HaasDelegationError(
                            "HaaS returned malformed SSE data."
                        ) from exc
                    if isinstance(event, dict):
                        yield event
        except httpx.HTTPError as exc:
            raise HaasDelegationError("HaaS delegated backend is unreachable.") from exc


def _checked_data(resp: httpx.Response) -> dict[str, Any]:
    if resp.status_code >= 400:
        raise HaasDelegationError(_safe_error(resp))
    try:
        data = resp.json()
    except ValueError as exc:
        raise HaasDelegationError("HaaS returned malformed JSON.") from exc
    if not isinstance(data, dict):
        raise HaasDelegationError("HaaS returned an invalid response.")
    inner = data.get("data")
    if not isinstance(inner, dict):
        raise HaasDelegationError("HaaS returned an invalid response.")
    return inner


def _safe_error(resp: httpx.Response) -> str:
    try:
        data = resp.json()
    except ValueError:
        data = {}
    if isinstance(data, dict):
        haas_error = data.get("haasError")
        if isinstance(haas_error, dict):
            reason = haas_error.get("safeReason") or haas_error.get("code")
            if isinstance(reason, str) and reason:
                return reason
        detail = data.get("detail")
        if isinstance(detail, str) and detail:
            return detail[:200]
    return f"HaaS request failed with HTTP {resp.status_code}."
