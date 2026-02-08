"""Tests for agent_gate.services.homeassistant — Home Assistant REST API client."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from agent_gate.config import HomeAssistantConfig
from agent_gate.services.homeassistant import HomeAssistantError, HomeAssistantService


def _make_config(url: str = "http://ha.local:8123", token: str = "test-token"):
    return HomeAssistantConfig(url=url, token=token)


def _mock_response(*, status: int = 200, json_data: dict | list | None = None, text: str = ""):
    """Create a mock aiohttp response as an async context manager."""
    resp = AsyncMock()
    resp.status = status
    resp.json = AsyncMock(return_value=json_data if json_data is not None else {})
    resp.text = AsyncMock(return_value=text)
    # Make it usable as async context manager
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=resp)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


def _mock_session() -> MagicMock:
    """Create a MagicMock aiohttp session that won't be replaced by _get_session."""
    session = MagicMock()
    session.closed = False
    return session


class TestExecuteHaGetState:
    async def test_sends_get_to_correct_url(self):
        svc = HomeAssistantService(_make_config())
        session = _mock_session()
        json_data = {"entity_id": "sensor.temp", "state": "22.5"}
        session.get = MagicMock(return_value=_mock_response(json_data=json_data))
        svc._session = session

        result = await svc.execute("ha_get_state", {"entity_id": "sensor.temp"})

        session.get.assert_called_once()
        call_args = session.get.call_args
        assert call_args[0][0] == "http://ha.local:8123/api/states/sensor.temp"
        assert result == json_data

    async def test_strips_trailing_slash_from_url(self):
        svc = HomeAssistantService(_make_config(url="http://ha.local:8123/"))
        session = _mock_session()
        session.get = MagicMock(return_value=_mock_response(json_data={}))
        svc._session = session

        await svc.execute("ha_get_state", {"entity_id": "sensor.temp"})

        call_url = session.get.call_args[0][0]
        assert call_url == "http://ha.local:8123/api/states/sensor.temp"


class TestExecuteHaGetStates:
    async def test_sends_get_to_states_endpoint(self):
        svc = HomeAssistantService(_make_config())
        session = _mock_session()
        json_data = [{"entity_id": "sensor.temp", "state": "22.5"}]
        session.get = MagicMock(return_value=_mock_response(json_data=json_data))
        svc._session = session

        result = await svc.execute("ha_get_states", {})

        session.get.assert_called_once()
        call_url = session.get.call_args[0][0]
        assert call_url == "http://ha.local:8123/api/states"
        assert result == {"states": json_data}


class TestExecuteHaCallService:
    async def test_sends_post_to_correct_url(self):
        svc = HomeAssistantService(_make_config())
        session = _mock_session()
        json_data = [{"entity_id": "light.bedroom"}]
        session.post = MagicMock(return_value=_mock_response(json_data=json_data))
        svc._session = session

        await svc.execute(
            "ha_call_service",
            {"domain": "light", "service": "turn_on", "entity_id": "light.bedroom"},
        )

        session.post.assert_called_once()
        call_args = session.post.call_args
        assert call_args[0][0] == "http://ha.local:8123/api/services/light/turn_on"

    async def test_passes_entity_id_and_extra_args_as_body(self):
        svc = HomeAssistantService(_make_config())
        session = _mock_session()
        session.post = MagicMock(return_value=_mock_response(json_data=[]))
        svc._session = session

        await svc.execute(
            "ha_call_service",
            {
                "domain": "light",
                "service": "turn_on",
                "entity_id": "light.bedroom",
                "brightness": 128,
                "color_name": "blue",
            },
        )

        call_kwargs = session.post.call_args[1]
        body = call_kwargs["json"]
        assert body["entity_id"] == "light.bedroom"
        assert body["brightness"] == 128
        assert body["color_name"] == "blue"
        # domain and service should NOT be in the body
        assert "domain" not in body
        assert "service" not in body

    async def test_returns_result_dict(self):
        svc = HomeAssistantService(_make_config())
        session = _mock_session()
        json_data = [{"entity_id": "light.bedroom", "state": "on"}]
        session.post = MagicMock(return_value=_mock_response(json_data=json_data))
        svc._session = session

        result = await svc.execute(
            "ha_call_service",
            {"domain": "light", "service": "turn_on", "entity_id": "light.bedroom"},
        )

        assert result == {"result": json_data}


class TestExecuteHaFireEvent:
    async def test_sends_post_to_events_endpoint(self):
        svc = HomeAssistantService(_make_config())
        session = _mock_session()
        json_data = {"message": "Event fired."}
        session.post = MagicMock(return_value=_mock_response(json_data=json_data))
        svc._session = session

        await svc.execute(
            "ha_fire_event",
            {"event_type": "custom_event", "data_key": "data_value"},
        )

        session.post.assert_called_once()
        call_url = session.post.call_args[0][0]
        assert call_url == "http://ha.local:8123/api/events/custom_event"

    async def test_passes_remaining_args_as_body(self):
        svc = HomeAssistantService(_make_config())
        session = _mock_session()
        session.post = MagicMock(return_value=_mock_response(json_data={}))
        svc._session = session

        await svc.execute(
            "ha_fire_event",
            {"event_type": "my_event", "key1": "val1", "key2": "val2"},
        )

        call_kwargs = session.post.call_args[1]
        body = call_kwargs["json"]
        assert body == {"key1": "val1", "key2": "val2"}
        assert "event_type" not in body


class TestBearerToken:
    async def test_session_has_authorization_header(self):
        svc = HomeAssistantService(_make_config(token="my-secret-token"))
        session = svc._get_session()

        assert session._default_headers["Authorization"] == "Bearer my-secret-token"
        await session.close()


class TestHealthCheck:
    async def test_returns_true_on_200(self):
        svc = HomeAssistantService(_make_config())
        session = _mock_session()
        session.get = MagicMock(return_value=_mock_response(status=200, json_data={}))
        svc._session = session

        result = await svc.health_check()
        assert result is True

        call_url = session.get.call_args[0][0]
        assert call_url == "http://ha.local:8123/api/"

    async def test_returns_false_on_non_200(self):
        svc = HomeAssistantService(_make_config())
        session = _mock_session()
        session.get = MagicMock(return_value=_mock_response(status=503))
        svc._session = session

        result = await svc.health_check()
        assert result is False

    async def test_returns_false_on_connection_error(self):
        svc = HomeAssistantService(_make_config())
        session = _mock_session()
        session.get = MagicMock(
            side_effect=aiohttp.ClientConnectorError(
                connection_key=MagicMock(),
                os_error=OSError("Connection refused"),
            )
        )
        svc._session = session

        result = await svc.health_check()
        assert result is False

    async def test_returns_false_on_timeout(self):
        svc = HomeAssistantService(_make_config())
        session = _mock_session()
        session.get = MagicMock(side_effect=TimeoutError())
        svc._session = session

        result = await svc.health_check()
        assert result is False

    async def test_uses_5_second_timeout(self):
        svc = HomeAssistantService(_make_config())
        session = _mock_session()
        session.get = MagicMock(return_value=_mock_response(status=200))
        svc._session = session

        await svc.health_check()

        call_kwargs = session.get.call_args[1]
        timeout = call_kwargs["timeout"]
        assert timeout.total == 5


class TestErrorHandling:
    async def test_401_raises_authentication_error(self):
        svc = HomeAssistantService(_make_config())
        session = _mock_session()
        session.get = MagicMock(return_value=_mock_response(status=401, text="Unauthorized"))
        svc._session = session

        with pytest.raises(HomeAssistantError, match=r"(?i)authentication"):
            await svc.execute("ha_get_state", {"entity_id": "sensor.temp"})

    async def test_404_raises_not_found_error(self):
        svc = HomeAssistantService(_make_config())
        session = _mock_session()
        session.get = MagicMock(return_value=_mock_response(status=404, text="Not Found"))
        svc._session = session

        with pytest.raises(HomeAssistantError, match=r"(?i)not found"):
            await svc.execute("ha_get_state", {"entity_id": "sensor.nonexistent"})

    async def test_connection_error_raises_unreachable(self):
        svc = HomeAssistantService(_make_config())
        session = _mock_session()
        session.get = MagicMock(
            side_effect=aiohttp.ClientConnectorError(
                connection_key=MagicMock(),
                os_error=OSError("Connection refused"),
            )
        )
        svc._session = session

        with pytest.raises(HomeAssistantError, match=r"(?i)unreachable"):
            await svc.execute("ha_get_state", {"entity_id": "sensor.temp"})

    async def test_other_http_error_raises_with_status(self):
        svc = HomeAssistantService(_make_config())
        session = _mock_session()
        session.post = MagicMock(
            return_value=_mock_response(status=500, text="Internal Server Error")
        )
        svc._session = session

        with pytest.raises(HomeAssistantError, match="500"):
            await svc.execute(
                "ha_call_service",
                {"domain": "light", "service": "turn_on", "entity_id": "light.x"},
            )

    async def test_generic_aiohttp_error_raises_unreachable(self):
        svc = HomeAssistantService(_make_config())
        session = _mock_session()
        session.get = MagicMock(side_effect=aiohttp.ClientError("some error"))
        svc._session = session

        with pytest.raises(HomeAssistantError, match=r"(?i)unreachable"):
            await svc.execute("ha_get_state", {"entity_id": "sensor.temp"})


class TestUnknownTool:
    async def test_unknown_tool_raises_error(self):
        svc = HomeAssistantService(_make_config())

        with pytest.raises(HomeAssistantError, match=r"(?i)unknown tool"):
            await svc.execute("ha_nonexistent", {"entity_id": "sensor.temp"})


class TestSessionLifecycle:
    async def test_close_closes_session(self):
        svc = HomeAssistantService(_make_config())
        session = svc._get_session()
        assert not session.closed

        await svc.close()
        assert svc._session is None

    async def test_close_is_idempotent(self):
        svc = HomeAssistantService(_make_config())
        # Close without ever creating a session
        await svc.close()
        # Close again
        await svc.close()

    async def test_get_session_creates_new_if_closed(self):
        svc = HomeAssistantService(_make_config())
        session1 = svc._get_session()
        await session1.close()

        session2 = svc._get_session()
        assert session2 is not session1
        assert not session2.closed
        await session2.close()

    async def test_get_session_reuses_existing(self):
        svc = HomeAssistantService(_make_config())
        session1 = svc._get_session()
        session2 = svc._get_session()
        assert session1 is session2
        await session1.close()
