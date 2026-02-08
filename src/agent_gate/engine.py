"""Permission engine — signature building, input validation, policy evaluation."""

from __future__ import annotations

import re
from collections.abc import Callable
from fnmatch import fnmatch

from agent_gate.config import Permissions
from agent_gate.models import Decision

# Strict allowlist for HA identifiers (domain, service, entity_id, event_type)
HA_IDENTIFIER_RE = re.compile(r"^[a-z_][a-z0-9_]*(\.[a-z0-9_]+)?$")

# Characters forbidden in ANY argument value (prevents glob/signature injection)
FORBIDDEN_CHARS_RE = re.compile(r"[*?\[\](),\x00-\x1f]")

# HA identifier fields that get extra validation
_HA_IDENTIFIER_FIELDS = frozenset({"entity_id", "domain", "service", "event_type"})

# Per-tool signature builders: tool_name → (args → list of signature parts)
SIGNATURE_BUILDERS: dict[str, Callable[[dict], list[str]]] = {
    "ha_call_service": lambda args: [
        f"{args.get('domain', '')}.{args.get('service', '')}",
        args.get("entity_id", ""),
    ],
    "ha_get_state": lambda args: [args.get("entity_id", "")],
    "ha_get_states": lambda args: [],
    "ha_fire_event": lambda args: [args.get("event_type", "")],
}


def validate_args(tool_name: str, args: dict) -> None:
    """Reject args with forbidden characters. Raises ValueError."""
    for key, value in args.items():
        if not isinstance(value, str):
            continue
        if FORBIDDEN_CHARS_RE.search(value):
            raise ValueError(f"Argument '{key}' contains forbidden characters")
        if (
            tool_name.startswith("ha_")
            and key in _HA_IDENTIFIER_FIELDS
            and not HA_IDENTIFIER_RE.match(value)
        ):
            raise ValueError(f"Invalid HA identifier format: {key}={value}")


def build_signature(tool_name: str, args: dict) -> str:
    """Build a deterministic, matchable signature string.

    Examples:
        build_signature("ha_get_state", {"entity_id": "sensor.temp"})
        → "ha_get_state(sensor.temp)"

        build_signature("ha_call_service",
            {"domain": "light", "service": "turn_on", "entity_id": "light.bedroom"})
        → "ha_call_service(light.turn_on, light.bedroom)"

        build_signature("ha_get_states", {})
        → "ha_get_states"
    """
    validate_args(tool_name, args)

    builder = SIGNATURE_BUILDERS.get(tool_name)
    if builder:
        parts = builder(args)
        return f"{tool_name}({', '.join(parts)})" if parts else tool_name

    # Fallback for unknown tools: sorted keys for determinism
    parts = [str(args[k]) for k in sorted(args.keys())]
    return f"{tool_name}({', '.join(parts)})" if parts else tool_name


class PermissionEngine:
    """Evaluates tool requests against permission rules."""

    def __init__(self, permissions: Permissions) -> None:
        self._permissions = permissions

    def evaluate(self, tool_name: str, args: dict) -> Decision:
        """Evaluate a tool request and return allow/deny/ask."""
        signature = build_signature(tool_name, args)

        # Phase 1: Check explicit rules (deny > allow > ask)
        for action_type in ("deny", "allow", "ask"):
            for rule in self._permissions.rules:
                if rule.action == action_type and fnmatch(signature, rule.pattern):
                    return Decision(action_type)

        # Phase 2: Check defaults (first match wins)
        for default in self._permissions.defaults:
            if fnmatch(signature, default.pattern):
                return Decision(default.action)

        # Phase 3: Global fallback
        return Decision.ASK
