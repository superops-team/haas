from __future__ import annotations

import base64
import json
import os
import secrets
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows integration supplies its own lock backend
    fcntl = None  # type: ignore[assignment]


class SupervisorLockError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ReadyRecord:
    port: int
    pid: int
    launch_id: str


class SupervisorState:
    def __init__(self, state_dir: str | Path) -> None:
        self.state_dir = Path(state_dir).expanduser()
        self.lock_file = self.state_dir / "supervisor.lock"
        self.token_file = self.state_dir / "token"
        self.ready_file = self.state_dir / "port-ready.json"
        self._ensure_dir()

    def _ensure_dir(self) -> None:
        if self.state_dir.is_symlink():
            raise SupervisorLockError("supervisor state directory must not be a symlink")
        self.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.state_dir, 0o700)

    @contextmanager
    def acquire_lock(self) -> Iterator[None]:
        self._ensure_dir()
        if fcntl is None:
            raise SupervisorLockError("supervisor locking is unavailable")
        handle = self.lock_file.open("a+", encoding="utf-8")
        os.chmod(self.lock_file, 0o600)
        try:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise SupervisorLockError("local HaaS supervisor is already owned") from exc
            yield
        finally:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                handle.close()

    def ensure_token(self) -> str:
        self._ensure_dir()
        if self.token_file.is_symlink():
            raise SupervisorLockError("supervisor token file must not be a symlink")
        try:
            token = self.token_file.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            token = ""
        if token:
            os.chmod(self.token_file, 0o600)
            return token
        token = secrets.token_urlsafe(32)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        try:
            fd = os.open(self.token_file, flags, 0o600)
        except FileExistsError as exc:
            raise SupervisorLockError("supervisor token file is empty or raced") from exc
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(token + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return token

    @staticmethod
    def new_launch_id() -> str:
        return secrets.token_urlsafe(24)

    def read_ready(
        self,
        *,
        launch_id: str,
        child_pid: int,
        is_pid_alive: Callable[[int], bool],
    ) -> ReadyRecord:
        if self.ready_file.is_symlink():
            raise SupervisorLockError("ready file must not be a symlink")
        try:
            raw = json.loads(self.ready_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SupervisorLockError("ready file is unavailable or invalid") from exc
        if not isinstance(raw, dict):
            raise SupervisorLockError("ready file is invalid")
        port, pid, actual_launch = raw.get("port"), raw.get("pid"), raw.get("launchId")
        if (
            not isinstance(port, int)
            or not 1 <= port <= 65535
            or pid != child_pid
            or actual_launch != launch_id
            or not is_pid_alive(child_pid)
        ):
            raise SupervisorLockError("ready file does not identify the owned sidecar")
        return ReadyRecord(port, pid, actual_launch)


class LocalBootstrap:
    def __init__(
        self,
        state: SupervisorState,
        *,
        harness_id: str,
        harness_base: str,
        harness_name: str,
    ) -> None:
        self.state = state
        self.harness_id = harness_id
        self.harness_base = harness_base
        self.harness_name = harness_name

    def process_env(
        self,
        *,
        base_env: Mapping[str, str],
        config_path: str | Path,
        host: str,
        port: int,
        launch_id: str,
    ) -> dict[str, str]:
        self.state.ensure_token()
        home = self.state.state_dir / "home"
        temporary = self.state.state_dir / "tmp"
        home.mkdir(mode=0o700, exist_ok=True)
        temporary.mkdir(mode=0o700, exist_ok=True)
        env = {key: base_env[key] for key in ("PATH", "LANG", "LC_ALL") if base_env.get(key)}
        principal = {
            "principalId": "manager-local",
            "tenantId": "manager-local",
            "workspaceId": "manager-local",
            "defaultUserId": "manager",
            "allowedUserIds": ["manager"],
            "roles": ["manager"],
        }
        env.update(
            {
                "HOME": str(home),
                "TMPDIR": str(temporary),
                "HAAS_CONFIG": str(config_path),
                "HAAS_SERVER_HOST": host,
                "HAAS_SERVER_PORT": str(port),
                "HAAS_STATIC_TOKEN_FILE": str(self.state.token_file),
                "HAAS_STATIC_PRINCIPAL_JSON": base64.b64encode(
                    json.dumps(principal, separators=(",", ":")).encode()
                ).decode(),
                "HAAS_PORT_READY_FILE": str(self.state.ready_file),
                "HAAS_LAUNCH_ID": launch_id,
                "HAAS_BOOTSTRAP_HARNESS_ID": self.harness_id,
                "HAAS_BOOTSTRAP_HARNESS_BASE": self.harness_base,
                "HAAS_BOOTSTRAP_HARNESS_NAME": self.harness_name,
            }
        )
        return env

    @staticmethod
    def initial_profile_steps(
        *, registry_empty: bool, profile_ref: Mapping[str, object] | None
    ) -> tuple[str, ...]:
        if registry_empty and profile_ref:
            return ("create", "validate", "activate")
        return ()
