"""Configuration — layered TOML: built-in defaults < global < per-workspace.

Global:    <state-dir>/config.toml   (see `secrets.state_dir`; platform-native)
Workspace: <workspace>/.coworker/config.toml   (overrides global)

Workspace command allowances apply only after the user trusts that exact canonical
workspace path. Other permission grants remain global-only.
"""

from __future__ import annotations

import os

try:
    import tomllib  # stdlib since 3.11
except ModuleNotFoundError:  # 3.10, the floor requires-python declares
    import tomli as tomllib  # type: ignore[no-redef]
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .delegation import HaasDelegationConfig
from .secrets import state_dir

# Commands auto-run WITHOUT an approval prompt. There is no generally safe executable:
# nominally read-only programs can read secrets outside the workspace, expand environment
# variables, load project-controlled config/plugins, or execute helpers (for example
# `find -exec` and pytest collection). Keep the built-in list empty. A user may explicitly
# opt into command prefixes in their user-owned global config, accepting that authority.
DEFAULT_ALLOWED_COMMANDS: list[str] = []


@dataclass
class Config:
    model: str = "gpt-5.6-sol"
    mode: str = "interactive"
    max_iterations: int = 150
    allowed_commands: list[str] = field(
        default_factory=lambda: list(DEFAULT_ALLOWED_COMMANDS)
    )
    # In "custom" permission mode, these tools are auto-approved (e.g. file edits)
    # while everything else still asks.
    auto_allow: list[str] = field(default_factory=list)
    # Egress destinations `web_fetch` may reach WITHOUT an approval prompt (exact host or
    # subdomain). Empty by default — the first fetch to any host asks. A power-user opt-in,
    # like `allowed_commands`; user-global only, so a repo can't widen the agent's network reach.
    allowed_domains: list[str] = field(default_factory=list)
    # Auto-Approve mode's feature flag (spec §1.5): when true, sessions get an LLM reviewer
    # that judges would-be approval cards in Mode.AUTO_APPROVE. Off by default; user-global
    # only — a cloned repo must not be able to hand itself a looser reviewer.
    auto_approve: bool = False
    # Shadow evaluation (spec Part 6 step 3): the reviewer records what it WOULD have
    # decided on every approval card while the human still decides. Verdicts land in the
    # audit log next to the human's outcome and nothing else changes — this is how the ship
    # gates (zero false-allows; ≥30% fewer prompts) get measured on real sessions. Costs
    # one model call per card while on. Off by default; user-global only.
    auto_approve_shadow: bool = False
    host: str = "127.0.0.1"
    port: int = 8765
    # Web search provider: "duckduckgo" (keyless default) | "tavily" | "brave" (need a key).
    web_search_provider: str = "duckduckgo"
    # OpenWorker Cloud (sign-in + managed connectors). Config, never constants:
    # dev/staging/BYO-VPC deployments point these at their own instances.
    cloud_base_url: str = "https://api.openworker.com"
    # Auth0 tenant + API audience are registered identifiers, not branding: the
    # tenant name can never be renamed, and the audience must match the API
    # identifier registered in Auth0 — both keep the legacy value on purpose.
    cloud_auth_domain: str = "opencoworker.us.auth0.com"
    cloud_client_id: str = "g1l4Q1lhYWmyS03qPSf4KEJGrgq02Qam"
    cloud_audience: str = "https://api.opencoworker.app"
    # Managed relay WebSocket endpoint (Slack/GitHub inbound). Defaults to the
    # PRODUCTION relay so a fresh install relays out of the box — an empty
    # default shipped once as "connected but relay OFF" on every machine
    # without a hand-edited config.toml. Empty override ⇒ relay disabled
    # (manual Socket Mode still works); dev/BYO deployments point elsewhere.
    cloud_relay_ws_url: str = (
        "wss://l4z1paxb83.execute-api.us-east-1.amazonaws.com/ocw-connect"
    )
    # HaaS delegated execution backend. It is user-global by default: a repository-local
    # config file must not opt itself into a stronger execution backend or broader mounts.
    haas_delegation: HaasDelegationConfig = field(default_factory=HaasDelegationConfig)


_FIELDS = {
    "model",
    "mode",
    "max_iterations",
    "allowed_commands",
    "auto_allow",
    "allowed_domains",
    "auto_approve",
    "auto_approve_shadow",
    "host",
    "port",
    "web_search_provider",
    "cloud_base_url",
    "cloud_auth_domain",
    "cloud_client_id",
    "cloud_audience",
    "cloud_relay_ws_url",
}

# These fields change what consequential actions can run without a prompt, so the normal
# workspace override pass never applies them. `allowed_commands` is added separately only
# for a canonically trusted workspace; `auto_allow` and `allowed_domains` remain user-global
# only (a repo must not be able to widen the agent's command or network reach).
_GLOBAL_ONLY_FIELDS = {
    "allowed_commands",
    "auto_allow",
    "allowed_domains",
    "auto_approve",
    "auto_approve_shadow",
}
_WORKSPACE_FIELDS = _FIELDS - _GLOBAL_ONLY_FIELDS


_HAAS_DELEGATION_FIELDS = {
    "enabled",
    "base_url",
    "api_token",
    "user_id",
    "harness_id",
    "harness_base",
    "image",
    "image_digest",
    "strategy",
    "require_trusted_workspace",
    "agent_allowlist",
    "trigger_keywords",
    "idle_ttl_seconds",
    "max_container_lifetime_seconds",
    "request_timeout_seconds",
    "local_autostart",
    "allow_unpinned_local_image",
}


def _env_bool(name: str) -> bool | None:
    raw = os.environ.get(name)
    if raw is None:
        return None
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _string_list(value: Any, default: list[str]) -> list[str]:
    if not isinstance(value, list):
        return list(default)
    items = [str(v).strip() for v in value if isinstance(v, str) and v.strip()]
    return items or list(default)


def global_config_path() -> Path:
    return state_dir() / "config.toml"


def _read(path: Path) -> dict[str, Any]:
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def _apply_haas_delegation_config(
    cfg: HaasDelegationConfig, raw: Any
) -> HaasDelegationConfig:
    if not isinstance(raw, dict):
        return cfg
    values = cfg.__dict__.copy()
    for key, value in raw.items():
        if key in _HAAS_DELEGATION_FIELDS:
            values[key] = value
    return HaasDelegationConfig(
        enabled=bool(values["enabled"]),
        base_url=str(values["base_url"]).rstrip("/") or cfg.base_url,
        api_token=str(values["api_token"]),
        user_id=str(values["user_id"]),
        harness_id=str(values["harness_id"]),
        harness_base=str(values["harness_base"]),
        image=str(values["image"]),
        image_digest=str(values["image_digest"]),
        strategy=str(values["strategy"]),
        require_trusted_workspace=bool(values["require_trusted_workspace"]),
        agent_allowlist=_string_list(values["agent_allowlist"], cfg.agent_allowlist),
        trigger_keywords=_string_list(values["trigger_keywords"], cfg.trigger_keywords),
        idle_ttl_seconds=max(1, int(values["idle_ttl_seconds"])),
        max_container_lifetime_seconds=max(1, int(values["max_container_lifetime_seconds"])),
        request_timeout_seconds=max(0.1, float(values["request_timeout_seconds"])),
        local_autostart=bool(values["local_autostart"]),
        allow_unpinned_local_image=bool(values["allow_unpinned_local_image"]),
    )


def _apply_haas_delegation_env(cfg: HaasDelegationConfig) -> HaasDelegationConfig:
    values = cfg.__dict__.copy()
    string_env = {
        "base_url": "COWORKER_HAAS_BASE_URL",
        "api_token": "COWORKER_HAAS_API_TOKEN",
        "user_id": "COWORKER_HAAS_USER_ID",
        "harness_id": "COWORKER_HAAS_HARNESS_ID",
        "harness_base": "COWORKER_HAAS_HARNESS_BASE",
        "image": "COWORKER_HAAS_IMAGE",
        "image_digest": "COWORKER_HAAS_IMAGE_DIGEST",
        "strategy": "COWORKER_HAAS_STRATEGY",
    }
    for key, env_name in string_env.items():
        raw = os.environ.get(env_name)
        if raw is not None and raw.strip():
            values[key] = raw.strip()
    for key, env_name in {
        "enabled": "COWORKER_HAAS_DELEGATION_ENABLED",
        "local_autostart": "COWORKER_HAAS_LOCAL_AUTOSTART",
        "allow_unpinned_local_image": "COWORKER_HAAS_ALLOW_UNPINNED_LOCAL_IMAGE",
        "require_trusted_workspace": "COWORKER_HAAS_REQUIRE_TRUSTED_WORKSPACE",
    }.items():
        raw_bool = _env_bool(env_name)
        if raw_bool is not None:
            values[key] = raw_bool
    for key, env_name in {
        "idle_ttl_seconds": "COWORKER_HAAS_IDLE_TTL_SECONDS",
        "max_container_lifetime_seconds": "COWORKER_HAAS_MAX_CONTAINER_LIFETIME_SECONDS",
    }.items():
        raw_int = os.environ.get(env_name)
        if raw_int is not None:
            values[key] = raw_int
    raw_timeout = os.environ.get("COWORKER_HAAS_REQUEST_TIMEOUT_SECONDS")
    if raw_timeout is not None:
        values["request_timeout_seconds"] = raw_timeout
    for key, env_name in {
        "agent_allowlist": "COWORKER_HAAS_AGENT_ALLOWLIST",
        "trigger_keywords": "COWORKER_HAAS_TRIGGER_KEYWORDS",
    }.items():
        raw_list = os.environ.get(env_name)
        if raw_list is not None:
            values[key] = [part.strip() for part in raw_list.split(",") if part.strip()]
    return _apply_haas_delegation_config(cfg, values)


def workspace_allowed_commands(workspace: str | Path) -> list[str]:
    """Command prefixes requested by repository config; advisory until workspace trust."""
    path = Path(workspace).expanduser() / ".coworker" / "config.toml"
    value = _read(path).get("allowed_commands", [])
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(v.strip() for v in value if isinstance(v, str) and v.strip()))


def load_config(
    workspace: str | Path | None = None,
    *,
    global_path: Path | None = None,
    workspace_trusted: bool = False,
) -> Config:
    cfg = Config()

    g = Path(global_path) if global_path is not None else global_config_path()
    if g.is_file():
        global_data = _read(g)
        for key, value in global_data.items():
            if key in _FIELDS:
                setattr(cfg, key, value)
        cfg.haas_delegation = _apply_haas_delegation_config(
            cfg.haas_delegation, global_data.get("haas_delegation")
        )
    cfg.haas_delegation = _apply_haas_delegation_env(cfg.haas_delegation)
    if workspace:
        w = Path(workspace).expanduser() / ".coworker" / "config.toml"
        if w.is_file():
            for key, value in _read(w).items():
                if key in _WORKSPACE_FIELDS:
                    setattr(cfg, key, value)
            if workspace_trusted:
                cfg.allowed_commands = list(
                    dict.fromkeys(
                        [*cfg.allowed_commands, *workspace_allowed_commands(workspace)]
                    )
                )
    return cfg
