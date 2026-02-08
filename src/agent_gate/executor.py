"""Action execution dispatcher — routes tool requests to service handlers."""

from __future__ import annotations

from typing import Any

from agent_gate.services.base import ServiceHandler

# Explicit tool-to-service mapping
TOOL_SERVICE_MAP: dict[str, str] = {
    "ha_get_state": "homeassistant",
    "ha_get_states": "homeassistant",
    "ha_call_service": "homeassistant",
    "ha_fire_event": "homeassistant",
}


class ExecutionError(Exception):
    """Raised when tool dispatch or execution fails."""


class Executor:
    """Routes approved tool requests to service handlers."""

    def __init__(self, services: dict[str, ServiceHandler]) -> None:
        self._services = services

    async def execute(self, tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
        """Dispatch a tool request to the appropriate service handler."""
        service_name = TOOL_SERVICE_MAP.get(tool_name)
        if service_name is None:
            raise ExecutionError(f"Unknown tool: {tool_name}")
        handler = self._services.get(service_name)
        if handler is None:
            raise ExecutionError(f"Service not configured: {service_name}")
        return await handler.execute(tool_name, args)
