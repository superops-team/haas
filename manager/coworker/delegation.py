"""HaaS delegated execution bridge for manager sessions.

HaaS is a complete execution backend, not a model provider. This module keeps
the manager-facing policy and HTTP/SSE translation separate from the local
TurnEngine and provider router.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import shlex
import socket
import subprocess
import sys
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from .haas import AcceptedInvocationHeaders, HaasProtocolError
from .haas.credentials import CredentialChannel
from .secrets import write_private_text

BINDING_KEY = "haas_delegation"
logger = logging.getLogger("coworker.haas.supervisor")


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
    enabled: bool = True
    mode: str = "local_managed"
    backend_preference: str = "haas"
    execution_mode: str = "local_api"
    base_url: str = "http://127.0.0.1:8092"
    api_token: str = ""
    user_id: str = "manager"
    harness_id: str = "chrn_codex_default"
    harness_base: str = "codex"
    image: str = "haas:local"
    image_digest: str = ""
    strategy: str = "deterministic"
    require_trusted_workspace: bool = True
    agent_allowlist: list[str] = field(default_factory=lambda: ["code"])
    trigger_keywords: list[str] = field(default_factory=lambda: list(DEFAULT_TRIGGER_KEYWORDS))
    idle_ttl_seconds: int = 1800
    max_container_lifetime_seconds: int = 28800
    request_timeout_seconds: float = 30.0
    local_autostart: bool = True
    allow_unpinned_local_image: bool = False
    network_access: bool = True
    workspace_mode: str = "workspace-write"
    approval_mode: str = "on-request"

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "mode": self.mode,
            "backend_preference": self.backend_preference,
            "execution_mode": self.execution_mode,
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
            "network_access": self.network_access,
            "workspace_mode": self.workspace_mode,
            "approval_mode": self.approval_mode,
            "policy_defaults_revision": 1,
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

    def __init__(
        self, data_dir: str | Path, *, resolve_credential: Callable[[str], str | None] | None = None
    ) -> None:
        self.data_dir = Path(data_dir).expanduser()
        self._credential_channel = (
            CredentialChannel(resolve_credential) if resolve_credential is not None else None
        )
        self._process: subprocess.Popen[Any] | None = None
        self._last_error: str | None = None
        self._base_url: str | None = None

    def _log_path(self) -> Path:
        return self.data_dir / "logs" / "haas-sidecar.log"

    def _manager_log_path(self) -> Path:
        return self.data_dir / "logs" / "openworker-server.log"

    def status(self, config: HaasDelegationConfig) -> dict[str, Any]:
        target = _loopback_target(config.base_url)
        running = self._owns_live_process(config.base_url)
        status = "disabled"
        reason: str | None = None
        if config.enabled and config.local_autostart:
            if target is None:
                status = "blocked"
                reason = "local_autostart_requires_loopback"
            elif running and self._healthy(config.base_url):
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
            "logPath": str(self._log_path()),
            "managerLogPath": str(self._manager_log_path()),
        }

    def token(self) -> str | None:
        path = self.data_dir / "haas-token"
        if path.is_symlink():
            return None
        try:
            if not path.is_file():
                return None
            os.chmod(path, 0o600)
            value = path.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        return value or None

    def ensure(self, config: HaasDelegationConfig) -> dict[str, Any]:
        if not config.enabled or not config.local_autostart:
            self.stop()
            return self.status(config)
        target = _loopback_target(config.base_url)
        if target is None:
            self.stop()
            self._last_error = "local_autostart_requires_loopback"
            logger.warning(
                "local HaaS autostart blocked: reason=%s url=%s log_path=%s",
                self._last_error,
                config.base_url.rstrip("/"),
                self._log_path(),
            )
            return self.status(config)
        base_url = config.base_url.rstrip("/")
        host, port = target
        running = self._process is not None and self._process.poll() is None
        if running and self._base_url and self._base_url != base_url:
            self.stop()
            running = False
        if self._healthy(base_url):
            if self._owns_live_process(base_url):
                self._last_error = None
                return self.status(config)
            if running and self._base_url == base_url:
                self.stop()
            elif not self._wait_for_listener_to_drain(base_url):
                self._last_error = "local_sidecar_not_owned"
                logger.warning(
                    "local HaaS listener is not owned: reason=%s url=%s log_path=%s",
                    self._last_error,
                    base_url,
                    self._log_path(),
                )
                return self.status(config)
        elif not running and self._port_is_listening(host, port):
            if not self._wait_for_port_to_drain(host, port):
                self._last_error = "local_sidecar_port_occupied"
                logger.warning(
                    "local HaaS port is occupied by a non-HaaS listener: "
                    "reason=%s url=%s log_path=%s",
                    self._last_error,
                    base_url,
                    self._log_path(),
                )
                return self.status(config)
        if self._process is not None and self._process.poll() is None:
            return self.status(config)
        self._process = None

        try:
            if not config.api_token:
                config.api_token = self._ensure_random_token()
            config_path = self._write_config(config, host=host, port=port)
            token_path = self.data_dir / "haas-token"
        except (HaasDelegationError, OSError):
            self._last_error = "local_haas_config_write_failed"
            logger.warning(
                "local HaaS config write failed: reason=%s url=%s log_path=%s",
                self._last_error,
                base_url,
                self._log_path(),
                exc_info=True,
            )
            return self.status(config)
        cmd = self._command(host=host, port=port, config_path=config_path)
        try:
            env = self._process_env(config_path)
        except HaasDelegationError:
            self._last_error = "bundled_codex_unavailable"
            logger.warning(
                "local HaaS startup blocked: reason=%s url=%s log_path=%s",
                self._last_error,
                base_url,
                self._log_path(),
                exc_info=True,
            )
            return self.status(config)
        log = self._log_file()
        env["HAAS_CONFIG"] = str(config_path)
        env["HAAS_STATIC_TOKEN_FILE"] = str(token_path)
        env.setdefault("HAAS_ADAPTER_BASE", "codex")
        child_socket = None
        spawn_options: dict[str, Any] = {}
        if self._credential_channel is not None:
            self._credential_channel.close()
            parent_socket, child_socket = socket.socketpair()
            self._credential_channel.start(parent_socket)
            env["HAAS_CREDENTIAL_FD"] = str(child_socket.fileno())
            spawn_options["pass_fds"] = (child_socket.fileno(),)
        try:
            self._process = subprocess.Popen(
                cmd,
                cwd=str(self._haas_root()),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=log or subprocess.DEVNULL,
                stderr=subprocess.STDOUT if log else subprocess.DEVNULL,
                **spawn_options,
            )
            self._base_url = base_url
        except OSError:
            self._last_error = "local_haas_start_failed"
            logger.warning(
                "local HaaS process spawn failed: reason=%s url=%s log_path=%s",
                self._last_error,
                base_url,
                self._log_path(),
                exc_info=True,
            )
        finally:
            if child_socket is not None:
                child_socket.close()
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
                logger.warning(
                    "local HaaS process exited before readiness: reason=%s url=%s log_path=%s",
                    self._last_error,
                    base_url,
                    self._log_path(),
                )
                return self.status(config)
        self._last_error = "local_haas_starting"
        logger.warning(
            "local HaaS process did not become ready before startup deadline: "
            "reason=%s url=%s log_path=%s",
            self._last_error,
            base_url,
            self._log_path(),
        )
        return self.status(config)

    def grant_credential(self, provider: str, scope: dict[str, str]) -> str:
        if (
            self._credential_channel is None
            or self._process is None
            or self._process.poll() is not None
            or not self._credential_channel.available
        ):
            raise HaasDelegationError("local credential channel unavailable")
        return self._credential_channel.grant(provider, scope)

    def stop(self) -> None:
        if self._credential_channel is not None:
            self._credential_channel.close()
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

    def _wait_for_listener_to_drain(self, base_url: str) -> bool:
        """Allow a parent-watched predecessor to leave during a desktop hot restart."""
        for _ in range(20):
            time.sleep(0.1)
            if not self._healthy(base_url):
                return True
        return False

    def _port_is_listening(self, host: str, port: int) -> bool:
        try:
            with socket.create_connection((host, port), timeout=0.25):
                return True
        except OSError:
            return False

    def _wait_for_port_to_drain(self, host: str, port: int) -> bool:
        """Bound startup on a non-HaaS process already bound to the configured port."""
        for _ in range(20):
            time.sleep(0.1)
            if not self._port_is_listening(host, port):
                return True
        return False

    def _owns_live_process(self, base_url: str) -> bool:
        process = self._process
        return (
            process is not None
            and process.poll() is None
            and self._base_url == base_url.rstrip("/")
            and (self._credential_channel is None or self._credential_channel.available)
        )

    def _haas_root(self) -> Path:
        return Path(__file__).resolve().parents[2]

    def _command(self, *, host: str, port: int, config_path: Path) -> list[str]:
        override = os.environ.get("COWORKER_HAAS_SERVER_CMD")
        if override:
            return shlex.split(override)
        exe = Path(sys.executable)
        if "openworker-server" in exe.name:
            return [
                str(exe),
                "haas-sidecar",
                "--host",
                host,
                "--port",
                str(port),
            ]
        return [
            sys.executable,
            "-m",
            "coworker.server.run",
            "haas-sidecar",
            "--host",
            host,
            "--port",
            str(port),
        ]

    def _process_env(self, config_path: Path) -> dict[str, str]:
        env: dict[str, str] = {"HAAS_CONFIG": str(config_path)}
        for key in (
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
        if getattr(sys, "frozen", False):
            codex_dir = Path(sys.executable).parent / "_internal" / "codex"
            codex_bin = codex_dir / ("codex.exe" if os.name == "nt" else "codex")
            if not codex_bin.is_file() or not os.access(codex_bin, os.X_OK):
                raise HaasDelegationError("bundled Codex executable is unavailable")
            env["PATH"] = str(codex_dir) + os.pathsep + env.get("PATH", os.defpath)
        env["HOME"] = str(self.data_dir / "home")
        env["COWORKER_EXIT_WITH_PARENT"] = "1"
        env["COWORKER_PARENT_PID"] = str(os.getpid())
        return env

    def _write_config(self, config: HaasDelegationConfig, *, host: str, port: int) -> Path:
        path = self.data_dir / "haas-supervised.yaml"
        token_path = self._token_file(config)
        store_path = self.data_dir / "haas.db"
        home_path = self.data_dir / "home"
        path.parent.mkdir(parents=True, exist_ok=True)
        home_path.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "\n".join(
                [
                    "server:",
                    f'  host: "{host}"',
                    f"  port: {port}",
                    "store:",
                    "  backend: sqlite",
                    f"  dsn: {json.dumps(str(store_path))}",
                    "identity:",
                    "  provider: static",
                    f"  static_token_file: {json.dumps(str(token_path))}",
                    "adapters:",
                    "  default_base: codex",
                    "  codex:",
                    "    transport: stdio",
                    "    socket_path: /tmp/haas/codex.sock",
                    "model_proxy:",
                    '  listen: "127.0.0.1:0"',
                    "delegation:",
                    "  container_backend: disabled",
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
        if not config.api_token:
            config.api_token = self._ensure_random_token()
        path = self.data_dir / "haas-token"
        write_private_text(path, config.api_token + "\n")
        return path

    def _ensure_random_token(self) -> str:
        path = self.data_dir / "haas-token"
        if path.is_symlink():
            raise HaasDelegationError("local HaaS token path is unsafe")
        try:
            existing = path.read_text(encoding="utf-8").strip()
        except OSError:
            existing = ""
        if existing:
            os.chmod(path, 0o600)
            return existing
        token = secrets.token_urlsafe(32)
        write_private_text(path, token + "\n")
        return token

    def _log_file(self) -> Any:
        try:
            self._log_path().parent.mkdir(parents=True, exist_ok=True)
            return open(self._log_path(), "a", encoding="utf-8")
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

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code


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
    return all(isinstance(item, dict) and _text_part(item) is not None for item in content)


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
            raise HaasDelegationError("HaaS delegation currently supports text input only.")
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
    if config.backend_preference == "local":
        return DelegationDecision("local", "explicit_local_choice", config=config)
    if not config.enabled:
        return DelegationDecision("local", "disabled", config=config)
    if config.strategy != "deterministic":
        return DelegationDecision("blocked", "unsupported_strategy", config=config)
    if config.execution_mode not in {"local_api", "delegated_session"}:
        return DelegationDecision("blocked", "unsupported_execution_mode", config=config)
    if not is_text_only_content(content):
        return DelegationDecision("blocked", "unsupported_content", config=config)
    if config.execution_mode == "local_api":
        if config.mode != "local_managed":
            return DelegationDecision("blocked", "remote_local_api_unsupported", config=config)
        return DelegationDecision("haas", "local_api_default", config=config)
    if agent not in set(config.agent_allowlist):
        return DelegationDecision("blocked", "agent_not_allowed", config=config)
    if not workspace or not Path(workspace).is_dir():
        return DelegationDecision("blocked", "no_project_workspace", config=config)
    if config.require_trusted_workspace and not workspace_trusted:
        return DelegationDecision("blocked", "workspace_not_trusted", config=config)
    if config.mode == "remote":
        return DelegationDecision("blocked", "remote_bind_mount_unsupported", config=config)
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
        "delegationPolicySnapshot": delegation_policy_snapshot(config),
    }


def delegation_policy_snapshot(config: HaasDelegationConfig) -> dict[str, Any]:
    return {
        "version": 1,
        "idleTtlSeconds": config.idle_ttl_seconds,
        "maxContainerLifetimeSeconds": config.max_container_lifetime_seconds,
        "rwWorkspaceConcurrency": "single_writer",
        "queuePolicy": "fifo",
        "restorePolicy": "fail_closed",
        "policyChangeMode": "snapshot_per_session",
        "mountPolicy": "project_rw_extra_ro",
        "network": {
            "defaultAction": "allow" if config.network_access else "deny",
            "allow": [],
        },
        "tools": {"disabled": [], "approvalMode": config.approval_mode},
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
        "execution_mode": "delegated_session",
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
    policy_snapshot = binding.get("delegation_policy_snapshot")
    if binding.get("execution_mode") == "delegated_session" and (
        not isinstance(policy_snapshot, dict)
        or not isinstance(policy_snapshot.get("network"), dict)
        or not isinstance(policy_snapshot.get("tools"), dict)
    ):
        raise HaasDelegationError(
            "HaaS delegation binding is missing its immutable policy snapshot."
        )
    policy_snapshot = policy_snapshot if isinstance(policy_snapshot, dict) else {}
    return HaasDelegationConfig(
        enabled=config.enabled,
        mode=config.mode,
        backend_preference=config.backend_preference,
        execution_mode=str(binding.get("execution_mode") or config.execution_mode),
        base_url=str(binding.get("haas_base_url") or config.base_url),
        api_token=config.api_token,
        user_id=str(binding.get("haas_user_id") or config.user_id),
        harness_id=str(binding.get("harness_id") or config.harness_id),
        harness_base=str(binding.get("harness_base") or config.harness_base),
        image=str((binding.get("image") or {}).get("reference") or config.image),
        image_digest=str((binding.get("image") or {}).get("digest") or config.image_digest),
        strategy=config.strategy,
        require_trusted_workspace=config.require_trusted_workspace,
        agent_allowlist=list(config.agent_allowlist),
        trigger_keywords=list(config.trigger_keywords),
        idle_ttl_seconds=int(
            policy_snapshot.get(
                "idleTtlSeconds", config.idle_ttl_seconds
            )
        ),
        max_container_lifetime_seconds=int(
            policy_snapshot.get(
                "maxContainerLifetimeSeconds", config.max_container_lifetime_seconds
            )
        ),
        request_timeout_seconds=config.request_timeout_seconds,
        local_autostart=config.local_autostart,
        allow_unpinned_local_image=config.allow_unpinned_local_image,
        network_access=(
            policy_snapshot.get("network", {})
            .get("defaultAction", "allow")
            == "allow"
        ),
        approval_mode=str(
            policy_snapshot.get("tools", {})
            .get("approvalMode", "on-request")
        ),
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
    return "".join(
        part.get("text", "")
        for part in parts
        if isinstance(part, dict) and part.get("thought") is not True
    )


def extract_adk_reasoning(event: dict[str, Any]) -> str:
    content = event.get("content")
    if not isinstance(content, dict):
        return ""
    parts = content.get("parts")
    if not isinstance(parts, list):
        return ""
    return "".join(
        part.get("text", "")
        for part in parts
        if isinstance(part, dict) and part.get("thought") is True
    )


def extract_adk_artifact(event: dict[str, Any]) -> dict[str, Any]:
    actions = event.get("actions")
    if not isinstance(actions, dict):
        return {}
    artifact = actions.get("artifactDelta")
    return dict(artifact) if isinstance(artifact, dict) else {}


def extract_adk_status(event: dict[str, Any]) -> str | None:
    actions = event.get("actions")
    if not isinstance(actions, dict):
        return None
    delta = actions.get("stateDelta")
    if not isinstance(delta, dict):
        return None
    status = delta.get("status")
    return status if isinstance(status, str) else None


@asynccontextmanager
async def open_delegated_sse(
    client: Any, **kwargs: Any
) -> AsyncIterator[tuple[AcceptedInvocationHeaders, AsyncIterator[dict[str, Any]]]]:
    """Expose accepted headers before consuming event data.

    Current clients implement run_sse_stream. The fallback keeps older
    plugins and test doubles working, although they cannot expose acceptance
    until their first event is available.
    """
    open_stream = getattr(client, "run_sse_stream", None)
    if callable(open_stream):
        async with open_stream(**kwargs) as stream:
            yield stream
        return

    events = client.run_sse(**kwargs).__aiter__()
    try:
        first = await anext(events)
    except StopAsyncIteration as exc:
        raise HaasDelegationError("HaaS returned no accepted invocation stream.") from exc
    accepted = getattr(client, "accepted_headers", None)
    if not isinstance(accepted, AcceptedInvocationHeaders):
        raise HaasDelegationError("HaaS did not provide accepted invocation headers.")

    async def replay_first() -> AsyncIterator[dict[str, Any]]:
        yield first
        async for event in events:
            yield event

    try:
        yield accepted, replay_first()
    finally:
        close = getattr(events, "aclose", None)
        if callable(close):
            await close()


class HaasDelegationClient:
    def __init__(
        self,
        config: HaasDelegationConfig,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.config = config
        self._transport = transport
        self.accepted_headers: AcceptedInvocationHeaders | None = None

    def _client(self, *, timeout: httpx.Timeout | float | None = None) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.config.base_url.rstrip("/"),
            timeout=timeout if timeout is not None else self.config.request_timeout_seconds,
            transport=self._transport,
        )

    def _headers(self, *, idempotency_key: str | None = None) -> dict[str, str]:
        if not self.config.api_token:
            raise HaasDelegationError("HaaS delegated backend credential is unavailable.")
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

    async def get_delegated_session(self, delegated_session_id: str) -> dict[str, Any]:
        return await self._get_data(f"/v1/haas/delegated-sessions/{delegated_session_id}")

    async def capabilities(self) -> dict[str, Any]:
        return await self._get_data("/v1/haas/capabilities")

    async def get_invocation(self, haas_session_id: str, invocation_id: str) -> dict[str, Any]:
        return await self._get_data(
            f"/v1/haas/sessions/{haas_session_id}/invocations/{invocation_id}"
        )

    async def update_delegated_policy(
        self,
        delegated_session_id: str,
        policy_snapshot: dict[str, Any],
        *,
        expected_revision: int | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"delegationPolicySnapshot": policy_snapshot}
        if expected_revision is not None:
            body["expectedRevision"] = expected_revision
        return await self._post_data(
            f"/v1/haas/delegated-sessions/{delegated_session_id}/policy",
            body,
            idempotency_key=idempotency_key
            or f"manager-policy:{delegated_session_id}:"
            + hashlib.sha256(
                json.dumps(policy_snapshot, sort_keys=True).encode()
            ).hexdigest(),
        )

    async def update_session_policy(
        self,
        haas_session_id: str,
        policy: dict[str, Any],
        *,
        expected_revision: int,
    ) -> dict[str, Any]:
        return await self._post_data(
            f"/v1/haas/sessions/{haas_session_id}/policy",
            {"expectedRevision": expected_revision, "policy": policy},
            idempotency_key=(
                f"manager-session-policy:{haas_session_id}:{expected_revision + 1}"
            ),
        )

    async def cancel_invocation(
        self, haas_session_id: str, invocation_id: str
    ) -> dict[str, Any]:
        return await self._post_data(
            f"/v1/haas/sessions/{haas_session_id}/invocations/{invocation_id}/cancel",
            {},
            idempotency_key=f"mgr-cancel:{invocation_id}",
        )

    async def pause_invocation(
        self, haas_session_id: str, invocation_id: str
    ) -> dict[str, Any]:
        return await self._post_data(
            f"/v1/haas/sessions/{haas_session_id}/invocations/{invocation_id}/pause",
            {},
            idempotency_key=f"mgr-pause:{invocation_id}",
        )

    @asynccontextmanager
    async def continue_invocation(
        self,
        haas_session_id: str,
        invocation_id: str,
        *,
        additional_instruction: str | None = None,
    ) -> AsyncIterator[Any]:
        body = (
            {}
            if additional_instruction is None
            else {"additionalInstruction": additional_instruction}
        )
        timeout = httpx.Timeout(self.config.request_timeout_seconds, read=None)
        try:
            async with (
                self._client(timeout=timeout) as client,
                client.stream(
                    "POST",
                    f"/v1/haas/sessions/{haas_session_id}/invocations/{invocation_id}/continue",
                    json=body,
                    headers=self._headers(idempotency_key=f"mgr-continue:{invocation_id}"),
                ) as resp,
            ):
                if resp.status_code >= 400:
                    raise _delegation_http_error(resp)
                accepted = AcceptedInvocationHeaders.parse(resp.headers)

                async def events() -> AsyncIterator[dict[str, Any]]:
                    async for line in resp.aiter_lines():
                        line = line.strip()
                        if not line or line.startswith(":") or not line.startswith("data:"):
                            continue
                        try:
                            event = json.loads(line.removeprefix("data:").strip())
                        except json.JSONDecodeError as exc:
                            raise HaasDelegationError(
                                "HaaS returned malformed SSE data."
                            ) from exc
                        if isinstance(event, dict):
                            yield event

                yield accepted, events()
        except httpx.HTTPError as exc:
            raise HaasDelegationError("HaaS delegated backend is unreachable.") from exc

    async def get_execution_evidence(
        self, haas_session_id: str, invocation_id: str, tool_call_id: str, evidence_ref: str
    ) -> dict[str, Any]:
        try:
            async with self._client() as client:
                resp = await client.get(
                    f"/v1/haas/sessions/{haas_session_id}/invocations/{invocation_id}/tools/{tool_call_id}/evidence",
                    params={"evidence_ref": evidence_ref},
                    headers=self._headers(),
                )
            return _checked_data(resp)
        except httpx.HTTPError as exc:
            raise HaasDelegationError("HaaS delegated backend is unreachable.") from exc

    async def events_page(
        self,
        haas_session_id: str,
        *,
        after_event_id: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"limit": limit}
        if after_event_id:
            params["after_event_id"] = after_event_id
        try:
            async with self._client() as client:
                resp = await client.get(
                    f"/v1/haas/sessions/{haas_session_id}/events-page",
                    params=params,
                    headers=self._headers(),
                )
            data = _checked_list_data(resp)
            return {
                "events": data,
                "next_cursor": _response_cursor(resp),
            }
        except httpx.HTTPError as exc:
            raise HaasDelegationError("HaaS delegated backend is unreachable.") from exc

    async def resolve_approval(
        self, haas_session_id: str, approval_id: str, *, approved: bool
    ) -> dict[str, Any]:
        decision = "approved" if approved else "denied"
        return await self._post_data(
            f"/v1/haas/sessions/{haas_session_id}/approvals/{approval_id}",
            {"decision": decision, "scope": "action"},
            idempotency_key=f"manager-approval:{approval_id}:{decision}",
        )

    async def list_approvals(
        self, haas_session_id: str, *, status: str = "waiting"
    ) -> list[dict[str, Any]]:
        try:
            async with self._client() as client:
                resp = await client.get(
                    f"/v1/haas/sessions/{haas_session_id}/approvals",
                    params={"status": status},
                    headers=self._headers(),
                )
            return _checked_list_data(resp)
        except httpx.HTTPError as exc:
            raise HaasDelegationError("HaaS delegated backend is unreachable.") from exc

    async def list_input_requests(
        self, haas_session_id: str, *, status: str = "waiting"
    ) -> list[dict[str, Any]]:
        try:
            async with self._client() as client:
                resp = await client.get(
                    f"/v1/haas/sessions/{haas_session_id}/input-requests",
                    params={"status": status},
                    headers=self._headers(),
                )
            return _checked_list_data(resp)
        except httpx.HTTPError as exc:
            raise HaasDelegationError("HaaS delegated backend is unreachable.") from exc

    async def answer_input_request(
        self,
        haas_session_id: str,
        input_request_id: str,
        *,
        answers: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        return await self._post_data(
            f"/v1/haas/sessions/{haas_session_id}/input-requests/{input_request_id}",
            {"answers": answers},
            idempotency_key=f"manager-input:{haas_session_id}:{input_request_id}",
        )

    async def _post_data(
        self, path: str, body: dict[str, Any], *, idempotency_key: str
    ) -> dict[str, Any]:
        try:
            async with self._client() as client:
                resp = await client.post(
                    path, json=body, headers=self._headers(idempotency_key=idempotency_key)
                )
            return _checked_data(resp)
        except httpx.HTTPError as exc:
            raise HaasDelegationError("HaaS delegated backend is unreachable.") from exc

    async def invocation_events(
        self, haas_session_id: str, invocation_id: str
    ) -> AsyncIterator[dict[str, Any]]:
        async for event in self._native_events(
            f"/v1/haas/sessions/{haas_session_id}/invocations/{invocation_id}/events"
        ):
            yield event

    async def session_events(
        self, haas_session_id: str, *, after_event_id: str | None = None
    ) -> AsyncIterator[dict[str, Any]]:
        path = f"/v1/haas/sessions/{haas_session_id}/events"
        if after_event_id:
            path += f"?after_event_id={after_event_id}"
        async for event in self._native_events(path):
            yield event

    async def _get_data(self, path: str) -> dict[str, Any]:
        try:
            async with self._client() as client:
                resp = await client.get(path, headers=self._headers())
            return _checked_data(resp)
        except httpx.HTTPError as exc:
            raise HaasDelegationError("HaaS delegated backend is unreachable.") from exc

    async def _native_events(self, path: str) -> AsyncIterator[dict[str, Any]]:
        timeout = httpx.Timeout(self.config.request_timeout_seconds, read=None)
        try:
            async with (
                self._client(timeout=timeout) as client,
                client.stream("GET", path, headers=self._headers()) as resp,
            ):
                if resp.status_code >= 400:
                    raise _delegation_http_error(resp)
                async for line in resp.aiter_lines():
                    line = line.strip()
                    if not line or line.startswith(":") or not line.startswith("data:"):
                        continue
                    try:
                        event = json.loads(line.removeprefix("data:").strip())
                    except json.JSONDecodeError as exc:
                        raise HaasDelegationError("HaaS returned malformed SSE data.") from exc
                    if isinstance(event, dict):
                        yield event
        except httpx.HTTPError as exc:
            raise HaasDelegationError("HaaS delegated backend is unreachable.") from exc

    @asynccontextmanager
    async def run_sse_stream(
        self,
        *,
        haas_session_id: str,
        message: dict[str, Any],
        harness_id: str | None = None,
        user_id: str | None = None,
        idempotency_key: str | None = None,
        approval_policy: str = "never",
    ) -> AsyncIterator[tuple[AcceptedInvocationHeaders, AsyncIterator[dict[str, Any]]]]:
        body = {
            "appName": harness_id or self.config.harness_id,
            "userId": user_id or self.config.user_id,
            "sessionId": haas_session_id,
            "newMessage": message,
            "streaming": True,
            # Delegated egress is owned by the revisioned session snapshot. HaaS
            # injects that frozen network policy into the private worker request.
            "policy": {"approvalPolicy": approval_policy},
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
                        idempotency_key=idempotency_key
                        or f"manager-turn:{haas_session_id}:{time.time_ns()}"
                    ),
                ) as resp,
            ):
                if resp.status_code >= 400:
                    raise _delegation_http_error(resp)
                try:
                    self.accepted_headers = AcceptedInvocationHeaders.parse(resp.headers)
                except HaasProtocolError as exc:
                    raise HaasDelegationError(str(exc)) from exc
                async def events() -> AsyncIterator[dict[str, Any]]:
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

                yield self.accepted_headers, events()
        except httpx.HTTPError as exc:
            raise HaasDelegationError("HaaS delegated backend is unreachable.") from exc

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
        """Compatibility iterator; new callers bind from headers via run_sse_stream."""
        async with self.run_sse_stream(
            haas_session_id=haas_session_id,
            message=message,
            harness_id=harness_id,
            user_id=user_id,
            idempotency_key=idempotency_key,
            approval_policy=approval_policy,
        ) as (_, events):
            async for event in events:
                yield event


def _checked_data(resp: httpx.Response) -> dict[str, Any]:
    if resp.status_code >= 400:
        raise _delegation_http_error(resp)
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


def _checked_list_data(resp: httpx.Response) -> list[dict[str, Any]]:
    if resp.status_code >= 400:
        raise _delegation_http_error(resp)
    try:
        body = resp.json()
    except ValueError as exc:
        raise HaasDelegationError("HaaS returned malformed JSON.") from exc
    inner = body.get("data") if isinstance(body, dict) else None
    if not isinstance(inner, list) or not all(isinstance(item, dict) for item in inner):
        raise HaasDelegationError("HaaS returned an invalid response.")
    return inner


def _response_cursor(resp: httpx.Response) -> str | None:
    try:
        body = resp.json()
    except ValueError:
        return None
    cursor = body.get("nextCursor") if isinstance(body, dict) else None
    return cursor if isinstance(cursor, str) else None


def _delegation_http_error(resp: httpx.Response) -> HaasDelegationError:
    code: str | None = None
    try:
        body = resp.json()
    except ValueError:
        body = {}
    if isinstance(body, dict) and isinstance(body.get("haasError"), dict):
        raw_code = body["haasError"].get("code")
        code = raw_code if isinstance(raw_code, str) else None
    return HaasDelegationError(_safe_error(resp), status_code=resp.status_code, code=code)


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
