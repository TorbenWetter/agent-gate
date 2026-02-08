"""Configuration loading with env var substitution and validation."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


class ConfigError(Exception):
    """Raised on configuration loading or validation errors."""


# --- Env var substitution ---

_ENV_VAR_RE = re.compile(r"\$\{(\w+)\}")


def _replacer(match: re.Match) -> str:
    var = match.group(1)
    val = os.environ.get(var)
    if val is None:
        raise ConfigError(f"Environment variable {var} is not set")
    return val


def substitute_env_vars(obj: Any) -> Any:
    """Recursively substitute ${VAR} in all string values."""
    if isinstance(obj, str):
        return _ENV_VAR_RE.sub(_replacer, obj)
    if isinstance(obj, dict):
        return {k: substitute_env_vars(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [substitute_env_vars(item) for item in obj]
    return obj


# --- Config dataclasses ---


@dataclass
class TLSConfig:
    cert: str
    key: str


@dataclass
class GatewayConfig:
    host: str
    port: int
    tls: TLSConfig | None = None


@dataclass
class AgentConfig:
    token: str


@dataclass
class TelegramConfig:
    token: str
    chat_id: int
    allowed_users: list[int]


@dataclass
class MessengerConfig:
    type: str
    telegram: TelegramConfig | None = None


@dataclass
class HomeAssistantConfig:
    url: str
    token: str


@dataclass
class StorageConfig:
    type: str
    path: str


@dataclass
class RateLimitConfig:
    max_pending_approvals: int = 10
    max_requests_per_minute: int = 60


@dataclass
class Config:
    gateway: GatewayConfig
    agent: AgentConfig
    messenger: MessengerConfig
    services: dict[str, HomeAssistantConfig]
    storage: StorageConfig
    approval_timeout: int = 900
    rate_limit: RateLimitConfig = field(default_factory=RateLimitConfig)


# --- Permission dataclasses ---


@dataclass
class PermissionRule:
    pattern: str
    action: str
    description: str = ""


@dataclass
class Permissions:
    defaults: list[PermissionRule]
    rules: list[PermissionRule]


# --- Helpers ---


def _require(data: dict, key: str, context: str) -> Any:
    """Get a required key from a dict or raise ConfigError."""
    if key not in data or data[key] is None:
        raise ConfigError(f"Missing required config: {context}.{key}")
    return data[key]


def _coerce_int(value: Any, field_name: str) -> int:
    """Coerce a value to int (handles env-substituted strings)."""
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ConfigError(f"Cannot convert {field_name} to int: {value!r}") from None


# --- Loaders ---


def load_config(path: str = "config.yaml") -> Config:
    """Load and validate config.yaml, returning a typed Config."""
    p = Path(path)
    if not p.exists():
        raise ConfigError(f"Config file not found: {path}")

    with open(p) as f:
        raw = yaml.safe_load(f)

    raw = substitute_env_vars(raw)

    # Gateway
    gw_raw = _require(raw, "gateway", "")
    host = _require(gw_raw, "host", "gateway")
    port = _coerce_int(_require(gw_raw, "port", "gateway"), "gateway.port")

    tls = None
    if gw_raw.get("tls"):
        tls_raw = gw_raw["tls"]
        tls = TLSConfig(
            cert=_require(tls_raw, "cert", "gateway.tls"),
            key=_require(tls_raw, "key", "gateway.tls"),
        )

    gateway = GatewayConfig(host=host, port=port, tls=tls)

    # Agent
    agent_raw = _require(raw, "agent", "")
    token = _require(agent_raw, "token", "agent")
    if not token:
        raise ConfigError("Missing required config: agent.token")
    agent = AgentConfig(token=token)

    # Messenger
    msg_raw = _require(raw, "messenger", "")
    msg_type = _require(msg_raw, "type", "messenger")
    if msg_type != "telegram":
        raise ConfigError(
            f"Unsupported messenger type: {msg_type!r} (only 'telegram' is supported)"
        )

    telegram_cfg = None
    if msg_type == "telegram":
        tg_raw = _require(msg_raw, "telegram", "messenger")
        tg_token = _require(tg_raw, "token", "messenger.telegram")
        chat_id = _coerce_int(
            _require(tg_raw, "chat_id", "messenger.telegram"), "messenger.telegram.chat_id"
        )
        allowed_users = _require(tg_raw, "allowed_users", "messenger.telegram")
        if not allowed_users:
            raise ConfigError("messenger.telegram.allowed_users must be a non-empty list")
        allowed_users = [
            _coerce_int(u, "messenger.telegram.allowed_users[]") for u in allowed_users
        ]
        telegram_cfg = TelegramConfig(token=tg_token, chat_id=chat_id, allowed_users=allowed_users)

    messenger = MessengerConfig(type=msg_type, telegram=telegram_cfg)

    # Services
    svc_raw = _require(raw, "services", "")
    services: dict[str, HomeAssistantConfig] = {}
    ha_raw = _require(svc_raw, "homeassistant", "services")
    services["homeassistant"] = HomeAssistantConfig(
        url=_require(ha_raw, "url", "services.homeassistant"),
        token=_require(ha_raw, "token", "services.homeassistant"),
    )

    # Storage
    stor_raw = _require(raw, "storage", "")
    stor_type = _require(stor_raw, "type", "storage")
    if stor_type != "sqlite":
        raise ConfigError(f"Unsupported storage type: {stor_type!r} (only 'sqlite' is supported)")
    storage = StorageConfig(
        type=stor_type,
        path=_require(stor_raw, "path", "storage"),
    )

    # Optional top-level
    approval_timeout = raw.get("approval_timeout", 900)
    if not isinstance(approval_timeout, int) or approval_timeout <= 0:
        raise ConfigError(f"approval_timeout must be a positive integer, got: {approval_timeout!r}")
    rate_limit_raw = raw.get("rate_limit", {})
    rate_limit = RateLimitConfig(
        max_pending_approvals=rate_limit_raw.get("max_pending_approvals", 10),
        max_requests_per_minute=rate_limit_raw.get("max_requests_per_minute", 60),
    )

    return Config(
        gateway=gateway,
        agent=agent,
        messenger=messenger,
        services=services,
        storage=storage,
        approval_timeout=approval_timeout,
        rate_limit=rate_limit,
    )


def load_permissions(path: str = "permissions.yaml") -> Permissions:
    """Load and parse permissions.yaml into typed Permissions."""
    p = Path(path)
    if not p.exists():
        raise ConfigError(f"Permissions file not found: {path}")

    with open(p) as f:
        raw = yaml.safe_load(f)

    raw = substitute_env_vars(raw)

    _VALID_ACTIONS = {"allow", "deny", "ask"}

    defaults = []
    for item in raw.get("defaults", []):
        action = item["action"]
        if action not in _VALID_ACTIONS:
            raise ConfigError(f"Invalid permission action: {action!r} (must be allow/deny/ask)")
        defaults.append(
            PermissionRule(
                pattern=item["pattern"],
                action=action,
                description=item.get("description", ""),
            )
        )

    rules = []
    for item in raw.get("rules", []) or []:
        action = item["action"]
        if action not in _VALID_ACTIONS:
            raise ConfigError(f"Invalid permission action: {action!r} (must be allow/deny/ask)")
        rules.append(
            PermissionRule(
                pattern=item["pattern"],
                action=action,
                description=item.get("description", ""),
            )
        )

    return Permissions(defaults=defaults, rules=rules)
