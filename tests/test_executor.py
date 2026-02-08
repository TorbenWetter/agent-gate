"""Tests for agent_gate.executor — action dispatch routing."""

import pytest

from agent_gate.executor import ExecutionError, Executor
from agent_gate.services.base import ServiceHandler


class MockServiceHandler(ServiceHandler):
    """Mock service handler that records calls."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    async def execute(self, tool_name: str, args: dict) -> dict:
        self.calls.append((tool_name, args))
        return {"mock": True, "tool": tool_name}

    async def health_check(self) -> bool:
        return True

    async def close(self) -> None:
        pass


class TestExecutor:
    async def test_dispatch_ha_get_state(self):
        handler = MockServiceHandler()
        executor = Executor({"homeassistant": handler})
        result = await executor.execute("ha_get_state", {"entity_id": "sensor.temp"})
        assert result == {"mock": True, "tool": "ha_get_state"}
        assert handler.calls == [("ha_get_state", {"entity_id": "sensor.temp"})]

    async def test_dispatch_ha_call_service(self):
        handler = MockServiceHandler()
        executor = Executor({"homeassistant": handler})
        args = {"domain": "light", "service": "turn_on", "entity_id": "light.bedroom"}
        result = await executor.execute("ha_call_service", args)
        assert result["tool"] == "ha_call_service"
        assert handler.calls[0] == ("ha_call_service", args)

    async def test_dispatch_ha_get_states(self):
        handler = MockServiceHandler()
        executor = Executor({"homeassistant": handler})
        await executor.execute("ha_get_states", {})
        assert handler.calls == [("ha_get_states", {})]

    async def test_dispatch_ha_fire_event(self):
        handler = MockServiceHandler()
        executor = Executor({"homeassistant": handler})
        await executor.execute("ha_fire_event", {"event_type": "test"})
        assert handler.calls[0][0] == "ha_fire_event"

    async def test_unknown_tool_raises(self):
        handler = MockServiceHandler()
        executor = Executor({"homeassistant": handler})
        with pytest.raises(ExecutionError, match="Unknown tool"):
            await executor.execute("nonexistent_tool", {})

    async def test_missing_service_raises(self):
        # No services registered
        executor = Executor({})
        with pytest.raises(ExecutionError, match="Service not configured"):
            await executor.execute("ha_get_state", {"entity_id": "sensor.temp"})

    async def test_passes_correct_args(self):
        handler = MockServiceHandler()
        executor = Executor({"homeassistant": handler})
        args = {"entity_id": "light.kitchen", "extra": "data"}
        await executor.execute("ha_get_state", args)
        assert handler.calls[0][1] is args

    async def test_multiple_services(self):
        ha_handler = MockServiceHandler()
        other_handler = MockServiceHandler()
        executor = Executor({"homeassistant": ha_handler, "other": other_handler})
        await executor.execute("ha_get_state", {"entity_id": "sensor.temp"})
        assert len(ha_handler.calls) == 1
        assert len(other_handler.calls) == 0
