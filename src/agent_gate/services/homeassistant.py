"""Home Assistant REST API service handler."""

from __future__ import annotations

from typing import Any

import aiohttp

from agent_gate.config import HomeAssistantConfig
from agent_gate.services.base import ServiceHandler


class HomeAssistantError(Exception):
    """Raised when a Home Assistant API call fails."""


class HomeAssistantService(ServiceHandler):
    """Service handler for Home Assistant REST API integration."""

    def __init__(self, config: HomeAssistantConfig) -> None:
        self._config = config
        self._base_url = config.url.rstrip("/")
        self._session: aiohttp.ClientSession | None = None

    def _get_session(self) -> aiohttp.ClientSession:
        """Return the existing session or create a new one."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={"Authorization": f"Bearer {self._config.token}"},
            )
        return self._session

    async def execute(self, tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
        """Execute a Home Assistant tool call and return the result."""
        session = self._get_session()
        try:
            if tool_name == "ha_get_state":
                return await self._get_state(session, args)
            if tool_name == "ha_get_states":
                return await self._get_states(session)
            if tool_name == "ha_call_service":
                return await self._call_service(session, args)
            if tool_name == "ha_fire_event":
                return await self._fire_event(session, args)
            raise HomeAssistantError(f"Unknown tool: {tool_name}")
        except HomeAssistantError:
            raise
        except aiohttp.ClientError as exc:
            raise HomeAssistantError(f"Service unreachable: homeassistant ({exc})") from exc

    async def _get_state(
        self, session: aiohttp.ClientSession, args: dict[str, Any]
    ) -> dict[str, Any]:
        entity_id = args["entity_id"]
        url = f"{self._base_url}/api/states/{entity_id}"
        async with session.get(url) as resp:
            await self._check_response(resp, entity_id=entity_id)
            return await resp.json()

    async def _get_states(self, session: aiohttp.ClientSession) -> dict[str, Any]:
        url = f"{self._base_url}/api/states"
        async with session.get(url) as resp:
            await self._check_response(resp)
            states = await resp.json()
            return {"states": states}

    async def _call_service(
        self, session: aiohttp.ClientSession, args: dict[str, Any]
    ) -> dict[str, Any]:
        domain = args["domain"]
        service = args["service"]
        url = f"{self._base_url}/api/services/{domain}/{service}"
        # Everything except domain/service goes into the request body
        body = {k: v for k, v in args.items() if k not in ("domain", "service")}
        async with session.post(url, json=body) as resp:
            await self._check_response(resp)
            result = await resp.json()
            return {"result": result}

    async def _fire_event(
        self, session: aiohttp.ClientSession, args: dict[str, Any]
    ) -> dict[str, Any]:
        event_type = args["event_type"]
        url = f"{self._base_url}/api/events/{event_type}"
        body = {k: v for k, v in args.items() if k != "event_type"}
        async with session.post(url, json=body) as resp:
            await self._check_response(resp)
            return await resp.json()

    async def _check_response(
        self, resp: aiohttp.ClientResponse, *, entity_id: str | None = None
    ) -> None:
        """Raise HomeAssistantError for non-2xx responses."""
        if 200 <= resp.status < 300:
            return
        if resp.status == 401:
            raise HomeAssistantError("Service authentication failed (HA token expired?)")
        if resp.status == 404:
            detail = f": {entity_id}" if entity_id else ""
            raise HomeAssistantError(f"Entity not found{detail}")
        text = await resp.text()
        raise HomeAssistantError(f"Home Assistant API error {resp.status}: {text}")

    async def health_check(self) -> bool:
        """Check if HA is reachable via GET /api/ with a 5-second timeout."""
        try:
            session = self._get_session()
            async with session.get(
                f"{self._base_url}/api/",
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                return resp.status == 200
        except Exception:
            return False

    async def close(self) -> None:
        """Close the aiohttp session."""
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None
