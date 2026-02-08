"""Tests for agent_gate.client — SDK protocol (T1 core + T2 reconnection)."""

from __future__ import annotations

import asyncio
import contextlib
import json
from unittest.mock import AsyncMock, patch

import pytest
import websockets.exceptions

from agent_gate.client import (
    AgentGateClient,
    AgentGateConnectionError,
    AgentGateDenied,
    AgentGateError,
    AgentGateTimeout,
)

# ---------------------------------------------------------------------------
# MockWebSocket
# ---------------------------------------------------------------------------


class MockWebSocket:
    """Simulates a WebSocket for testing the client SDK."""

    def __init__(self) -> None:
        self._sent: list[str] = []
        self._to_receive: asyncio.Queue[str] = asyncio.Queue()
        self._closed = False

    async def send(self, data: str) -> None:
        self._sent.append(data)

    async def recv(self) -> str:
        return await self._to_receive.get()

    async def close(self) -> None:
        self._closed = True

    def feed(self, data: str) -> None:
        """Queue a message for the client to receive."""
        self._to_receive.put_nowait(data)

    def __aiter__(self):
        return self

    async def __anext__(self) -> str:
        try:
            return await asyncio.wait_for(self._to_receive.get(), timeout=0.1)
        except (TimeoutError, asyncio.CancelledError):
            raise StopAsyncIteration from None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

AUTH_SUCCESS = json.dumps({"jsonrpc": "2.0", "result": {"status": "authenticated"}, "id": "auth-1"})


def _tool_result(request_id: int, data: dict) -> str:
    return json.dumps(
        {
            "jsonrpc": "2.0",
            "result": {"status": "executed", "data": data},
            "id": request_id,
        }
    )


def _tool_error(request_id: int, code: int, message: str) -> str:
    return json.dumps(
        {
            "jsonrpc": "2.0",
            "error": {"code": code, "message": message},
            "id": request_id,
        }
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_ws() -> MockWebSocket:
    return MockWebSocket()


@pytest.fixture
def patch_connect(mock_ws: MockWebSocket):
    with patch("agent_gate.client.websockets.connect", new_callable=AsyncMock) as m:
        m.return_value = mock_ws
        yield m


# ---------------------------------------------------------------------------
# T1-1: Error class hierarchy
# ---------------------------------------------------------------------------


class TestErrorClassHierarchy:
    def test_agent_gate_error_has_code_and_message(self):
        err = AgentGateError(42, "something broke")
        assert err.code == 42
        assert err.message == "something broke"
        assert str(err) == "something broke"

    def test_denied_extends_error(self):
        err = AgentGateDenied(-32001, "denied")
        assert isinstance(err, AgentGateError)
        assert err.code == -32001
        assert err.message == "denied"

    def test_timeout_extends_error(self):
        err = AgentGateTimeout(-32002, "timed out")
        assert isinstance(err, AgentGateError)
        assert err.code == -32002
        assert err.message == "timed out"

    def test_connection_error_extends_error(self):
        err = AgentGateConnectionError(-1, "connection failed")
        assert isinstance(err, AgentGateError)
        assert err.code == -1
        assert err.message == "connection failed"


# ---------------------------------------------------------------------------
# T1-2: Connect and authenticate
# ---------------------------------------------------------------------------


class TestConnectAndAuthenticate:
    async def test_connect_and_authenticate(self, mock_ws, patch_connect):
        """Client connects, sends auth message, receives success."""
        mock_ws.feed(AUTH_SUCCESS)

        client = AgentGateClient("ws://localhost:8443", "test-token")
        await client.connect()

        # Verify websockets.connect was called with the URL
        patch_connect.assert_called_once_with("ws://localhost:8443")

        # Verify auth message was sent
        assert len(mock_ws._sent) == 1
        auth_msg = json.loads(mock_ws._sent[0])
        assert auth_msg["jsonrpc"] == "2.0"
        assert auth_msg["method"] == "auth"
        assert auth_msg["params"]["token"] == "test-token"
        assert auth_msg["id"] == "auth-1"

        # Verify reader task is running
        assert client._reader_task is not None
        assert not client._reader_task.done()

        await client.close()

    async def test_auth_failure_invalid_token(self, mock_ws, patch_connect):
        """Server returns error, client raises AgentGateConnectionError."""
        mock_ws.feed(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "error": {"code": -32005, "message": "Invalid token"},
                    "id": "auth-1",
                }
            )
        )

        client = AgentGateClient("ws://localhost:8443", "bad-token")
        with pytest.raises(AgentGateConnectionError) as exc_info:
            await client.connect()

        assert exc_info.value.code == -32005
        assert "Invalid token" in exc_info.value.message
        await client.close()

    async def test_auth_failure_unexpected_response(self, mock_ws, patch_connect):
        """Server returns non-'authenticated' status, raises AgentGateConnectionError."""
        mock_ws.feed(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "result": {"status": "something_else"},
                    "id": "auth-1",
                }
            )
        )

        client = AgentGateClient("ws://localhost:8443", "test-token")
        with pytest.raises(AgentGateConnectionError) as exc_info:
            await client.connect()

        assert exc_info.value.code == -1
        assert "Unexpected auth response" in exc_info.value.message
        await client.close()


# ---------------------------------------------------------------------------
# T1-3: Tool requests
# ---------------------------------------------------------------------------


class TestToolRequests:
    async def test_tool_request_success(self, mock_ws, patch_connect):
        """Send tool_request, get result back, verify JSON-RPC format."""
        mock_ws.feed(AUTH_SUCCESS)

        client = AgentGateClient("ws://localhost:8443", "test-token")
        await client.connect()

        async def respond():
            await asyncio.sleep(0.01)
            mock_ws.feed(_tool_result(1, {"state": "21.3"}))

        _task = asyncio.create_task(respond())  # noqa: RUF006
        result = await client.tool_request("ha_get_state", entity_id="sensor.temp")
        assert result == {"state": "21.3"}

        # Verify the sent JSON-RPC
        sent = json.loads(mock_ws._sent[-1])
        assert sent["jsonrpc"] == "2.0"
        assert sent["method"] == "tool_request"
        assert sent["params"]["tool"] == "ha_get_state"
        assert sent["params"]["args"] == {"entity_id": "sensor.temp"}
        assert sent["id"] == 1

        await client.close()

    async def test_tool_request_denied_by_policy(self, mock_ws, patch_connect):
        """Server returns -32003, raises AgentGateDenied."""
        mock_ws.feed(AUTH_SUCCESS)

        client = AgentGateClient("ws://localhost:8443", "test-token")
        await client.connect()

        async def respond():
            await asyncio.sleep(0.01)
            mock_ws.feed(_tool_error(1, -32003, "Policy denied"))

        _task = asyncio.create_task(respond())  # noqa: RUF006
        with pytest.raises(AgentGateDenied) as exc_info:
            await client.tool_request("ha_call_service", domain="lock", service="lock")

        assert exc_info.value.code == -32003
        assert "Policy denied" in exc_info.value.message

        await client.close()

    async def test_tool_request_denied_by_user(self, mock_ws, patch_connect):
        """Server returns -32001, raises AgentGateDenied."""
        mock_ws.feed(AUTH_SUCCESS)

        client = AgentGateClient("ws://localhost:8443", "test-token")
        await client.connect()

        async def respond():
            await asyncio.sleep(0.01)
            mock_ws.feed(_tool_error(1, -32001, "Denied by user"))

        _task = asyncio.create_task(respond())  # noqa: RUF006
        with pytest.raises(AgentGateDenied) as exc_info:
            await client.tool_request("ha_call_service", domain="lock", service="unlock")

        assert exc_info.value.code == -32001
        assert "Denied by user" in exc_info.value.message

        await client.close()

    async def test_tool_request_timeout(self, mock_ws, patch_connect):
        """Server returns -32002, raises AgentGateTimeout."""
        mock_ws.feed(AUTH_SUCCESS)

        client = AgentGateClient("ws://localhost:8443", "test-token")
        await client.connect()

        async def respond():
            await asyncio.sleep(0.01)
            mock_ws.feed(_tool_error(1, -32002, "Approval timed out"))

        _task = asyncio.create_task(respond())  # noqa: RUF006
        with pytest.raises(AgentGateTimeout) as exc_info:
            await client.tool_request("ha_call_service", domain="light", service="turn_on")

        assert exc_info.value.code == -32002
        assert "timed out" in exc_info.value.message.lower()

        await client.close()

    async def test_tool_request_execution_error(self, mock_ws, patch_connect):
        """Server returns -32004, raises AgentGateError."""
        mock_ws.feed(AUTH_SUCCESS)

        client = AgentGateClient("ws://localhost:8443", "test-token")
        await client.connect()

        async def respond():
            await asyncio.sleep(0.01)
            mock_ws.feed(_tool_error(1, -32004, "Execution failed"))

        _task = asyncio.create_task(respond())  # noqa: RUF006
        with pytest.raises(AgentGateError) as exc_info:
            await client.tool_request("ha_get_state", entity_id="sensor.broken")

        assert exc_info.value.code == -32004
        assert "Execution failed" in exc_info.value.message

        await client.close()

    async def test_tool_request_rate_limited(self, mock_ws, patch_connect):
        """Server returns -32006, raises AgentGateError."""
        mock_ws.feed(AUTH_SUCCESS)

        client = AgentGateClient("ws://localhost:8443", "test-token")
        await client.connect()

        async def respond():
            await asyncio.sleep(0.01)
            mock_ws.feed(_tool_error(1, -32006, "Rate limit exceeded"))

        _task = asyncio.create_task(respond())  # noqa: RUF006
        with pytest.raises(AgentGateError) as exc_info:
            await client.tool_request("ha_get_state", entity_id="sensor.temp")

        assert exc_info.value.code == -32006
        assert "Rate limit" in exc_info.value.message

        await client.close()

    async def test_tool_request_other_error(self, mock_ws, patch_connect):
        """Server returns some other error code, raises AgentGateError."""
        mock_ws.feed(AUTH_SUCCESS)

        client = AgentGateClient("ws://localhost:8443", "test-token")
        await client.connect()

        async def respond():
            await asyncio.sleep(0.01)
            mock_ws.feed(_tool_error(1, -99999, "Unknown server error"))

        _task = asyncio.create_task(respond())  # noqa: RUF006
        with pytest.raises(AgentGateError) as exc_info:
            await client.tool_request("ha_get_state", entity_id="sensor.temp")

        assert exc_info.value.code == -99999
        assert "Unknown server error" in exc_info.value.message
        # Should NOT be a subclass-specific exception for unknown codes
        assert type(exc_info.value) is AgentGateError

        await client.close()


# ---------------------------------------------------------------------------
# T1-4: Concurrent requests
# ---------------------------------------------------------------------------


class TestConcurrentRequests:
    async def test_concurrent_tool_requests(self, mock_ws, patch_connect):
        """Two requests sent concurrently, each gets correct response by ID."""
        mock_ws.feed(AUTH_SUCCESS)

        client = AgentGateClient("ws://localhost:8443", "test-token")
        await client.connect()

        async def respond():
            await asyncio.sleep(0.01)
            # Respond to request 2 first, then 1 (out of order)
            mock_ws.feed(_tool_result(2, {"entity": "light.bedroom", "state": "off"}))
            await asyncio.sleep(0.01)
            mock_ws.feed(_tool_result(1, {"entity": "sensor.temp", "state": "22.0"}))

        _responder = asyncio.create_task(respond())  # noqa: RUF006

        # Fire both requests concurrently
        task1 = asyncio.create_task(client.tool_request("ha_get_state", entity_id="sensor.temp"))
        task2 = asyncio.create_task(client.tool_request("ha_get_state", entity_id="light.bedroom"))

        result1, result2 = await asyncio.gather(task1, task2)

        assert result1 == {"entity": "sensor.temp", "state": "22.0"}
        assert result2 == {"entity": "light.bedroom", "state": "off"}

        await client.close()


# ---------------------------------------------------------------------------
# T1-5: Context manager
# ---------------------------------------------------------------------------


class TestContextManager:
    async def test_context_manager(self, mock_ws, patch_connect):
        """async with works: connect on enter, close on exit."""
        mock_ws.feed(AUTH_SUCCESS)

        async with AgentGateClient("ws://localhost:8443", "test-token") as client:
            assert client._ws is not None
            assert client._reader_task is not None
            assert not client._reader_task.done()

        # After exit, ws should be closed
        assert mock_ws._closed is True


# ---------------------------------------------------------------------------
# T1-6: Close behavior
# ---------------------------------------------------------------------------


class TestClose:
    async def test_close_cancels_reader(self, mock_ws, patch_connect):
        """Reader task is cancelled on close."""
        mock_ws.feed(AUTH_SUCCESS)

        client = AgentGateClient("ws://localhost:8443", "test-token")
        await client.connect()

        reader_task = client._reader_task
        assert reader_task is not None
        assert not reader_task.done()

        await client.close()

        assert reader_task.done()
        assert client._ws is None


# ---------------------------------------------------------------------------
# T1-7: get_pending_results
# ---------------------------------------------------------------------------


class TestGetPendingResults:
    async def test_get_pending_results_with_results(self, mock_ws, patch_connect):
        """Returns results list and resolves matching pending futures."""
        mock_ws.feed(AUTH_SUCCESS)

        client = AgentGateClient("ws://localhost:8443", "test-token")
        await client.connect()

        # Simulate a pending future for a request made before disconnect
        loop = asyncio.get_running_loop()
        old_future = loop.create_future()
        client._pending[42] = old_future

        results_data = [
            {
                "request_id": 42,
                "result": json.dumps({"status": "executed", "data": {"state": "on"}}),
            },
            {
                "request_id": 99,
                "result": json.dumps({"status": "denied", "data": "Policy denied"}),
            },
        ]

        async def respond():
            await asyncio.sleep(0.01)
            mock_ws.feed(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "result": {"results": results_data},
                        "id": 1,
                    }
                )
            )

        _task = asyncio.create_task(respond())  # noqa: RUF006
        results = await client.get_pending_results()

        assert len(results) == 2
        assert results[0]["request_id"] == 42
        assert results[1]["request_id"] == 99

        # The old pending future for id=42 should be resolved
        assert old_future.done()
        assert old_future.result() == {"state": "on"}

        await client.close()

    async def test_get_pending_results_empty(self, mock_ws, patch_connect):
        """Returns empty list when no pending results."""
        mock_ws.feed(AUTH_SUCCESS)

        client = AgentGateClient("ws://localhost:8443", "test-token")
        await client.connect()

        async def respond():
            await asyncio.sleep(0.01)
            mock_ws.feed(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "result": {"results": []},
                        "id": 1,
                    }
                )
            )

        _task = asyncio.create_task(respond())  # noqa: RUF006
        results = await client.get_pending_results()

        assert results == []

        await client.close()


# ---------------------------------------------------------------------------
# T1-8: _next_id increments
# ---------------------------------------------------------------------------


class TestNextId:
    def test_next_id_increments(self):
        """IDs increment: 1, 2, 3..."""
        client = AgentGateClient("ws://localhost:8443", "test-token")
        assert client._next_id() == 1
        assert client._next_id() == 2
        assert client._next_id() == 3


# ---------------------------------------------------------------------------
# T2: Auto-reconnection with exponential backoff
# ---------------------------------------------------------------------------


class ReconnectMockWebSocket(MockWebSocket):
    """MockWebSocket that can simulate disconnections.

    Unlike the base MockWebSocket which raises StopAsyncIteration on timeout,
    this one blocks indefinitely in __anext__ until a message arrives or
    disconnect() is called, closely mimicking real websocket behavior.
    """

    def __init__(self) -> None:
        super().__init__()
        self._disconnect_event = asyncio.Event()

    def disconnect(self) -> None:
        """Signal that the connection should be closed."""
        self._disconnect_event.set()

    async def __anext__(self) -> str:
        # Race: either a message arrives or disconnect is signaled
        msg_task = asyncio.ensure_future(self._to_receive.get())
        disc_task = asyncio.ensure_future(self._disconnect_event.wait())
        done, pending = await asyncio.wait(
            [msg_task, disc_task], return_when=asyncio.FIRST_COMPLETED
        )
        for t in pending:
            t.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await t
        if disc_task in done:
            # If msg_task also completed, put the message back
            if msg_task in done and not msg_task.cancelled():
                with contextlib.suppress(Exception):
                    self._to_receive.put_nowait(msg_task.result())
            raise websockets.exceptions.ConnectionClosed(None, None)
        return msg_task.result()


def _make_mock_ws(*, auth_success: bool = True) -> ReconnectMockWebSocket:
    """Create a ReconnectMockWebSocket pre-loaded with an auth response."""
    ws = ReconnectMockWebSocket()
    if auth_success:
        ws.feed(AUTH_SUCCESS)
    return ws


async def _yield_control() -> None:
    """Yield control to the event loop so background tasks can run."""
    for _ in range(20):
        await asyncio.sleep(0)


class TestReconnectOnDisconnect:
    async def test_reconnect_on_disconnect(self):
        """Client auto-reconnects after unexpected disconnect, re-authenticates."""
        ws1 = _make_mock_ws()
        ws2 = _make_mock_ws()

        connect_mock = AsyncMock(side_effect=[ws1, ws2])

        sleep_delays: list[float] = []

        async def fake_sleep(delay: float) -> None:
            sleep_delays.append(delay)

        with patch("agent_gate.client.websockets.connect", connect_mock):
            client = AgentGateClient("ws://localhost:8443", "test-token")
            client._backoff_sleep = fake_sleep
            await client.connect()

            # Verify initial connection
            assert connect_mock.call_count == 1

            # Trigger disconnect
            ws1.disconnect()
            await _yield_control()

            # Should have reconnected
            assert connect_mock.call_count == 2

            # Verify auth was sent on the second connection
            found_auth = any(json.loads(m).get("method") == "auth" for m in ws2._sent)
            assert found_auth, "Auth message not sent on reconnection"

            # sleep was called with initial delay of 1.0
            assert len(sleep_delays) >= 1
            assert sleep_delays[0] == 1.0

            await client.close()


class TestReconnectExponentialBackoff:
    async def test_reconnect_exponential_backoff(self):
        """Delay doubles on each failed reconnect: 1s, 2s, 4s."""
        ws1 = _make_mock_ws()
        ws_final = _make_mock_ws()

        # First connect succeeds, next 3 reconnect attempts fail, 4th succeeds
        connect_mock = AsyncMock(
            side_effect=[
                ws1,
                OSError("fail 1"),
                OSError("fail 2"),
                OSError("fail 3"),
                ws_final,
            ]
        )

        sleep_delays: list[float] = []

        async def fake_sleep(delay: float) -> None:
            sleep_delays.append(delay)

        with patch("agent_gate.client.websockets.connect", connect_mock):
            client = AgentGateClient("ws://localhost:8443", "test-token")
            client._backoff_sleep = fake_sleep
            await client.connect()

            # Trigger disconnect
            ws1.disconnect()
            await _yield_control()

            # Should have reconnected after 3 failures
            assert connect_mock.call_count == 5  # 1 initial + 4 reconnect attempts

            # Verify exponential backoff: 1, 2, 4, 8
            assert sleep_delays[0] == 1.0
            assert sleep_delays[1] == 2.0
            assert sleep_delays[2] == 4.0
            assert sleep_delays[3] == 8.0

            await client.close()


class TestReconnectBackoffCapped:
    async def test_reconnect_backoff_capped_at_30s(self):
        """After enough retries the delay should not exceed 30s."""
        ws1 = _make_mock_ws()
        ws_final = _make_mock_ws()

        # Need enough failures to reach the cap: 1, 2, 4, 8, 16, 32->30, 30
        failures = [OSError(f"fail {i}") for i in range(7)]
        connect_mock = AsyncMock(side_effect=[ws1, *failures, ws_final])

        sleep_delays: list[float] = []

        async def fake_sleep(delay: float) -> None:
            sleep_delays.append(delay)

        with patch("agent_gate.client.websockets.connect", connect_mock):
            client = AgentGateClient("ws://localhost:8443", "test-token")
            client._backoff_sleep = fake_sleep
            await client.connect()

            ws1.disconnect()
            await _yield_control()

            # Delays: 1, 2, 4, 8, 16, 30, 30, 30
            assert all(d <= 30.0 for d in sleep_delays), f"Delays exceeded cap: {sleep_delays}"
            # The 6th delay (index 5) should be capped at 30
            assert sleep_delays[5] == 30.0

            await client.close()


class TestMaxRetriesExhausted:
    async def test_max_retries_exhausted(self):
        """With max_retries=2, after 2 failed attempts raises AgentGateConnectionError."""
        ws1 = _make_mock_ws()

        connect_mock = AsyncMock(
            side_effect=[
                ws1,
                OSError("fail 1"),
                OSError("fail 2"),
            ]
        )

        sleep_delays: list[float] = []

        async def fake_sleep(delay: float) -> None:
            sleep_delays.append(delay)

        with patch("agent_gate.client.websockets.connect", connect_mock):
            client = AgentGateClient("ws://localhost:8443", "test-token", max_retries=2)
            client._backoff_sleep = fake_sleep
            await client.connect()

            # Create a pending future that should be failed
            loop = asyncio.get_running_loop()
            pending_future = loop.create_future()
            client._pending[999] = pending_future

            ws1.disconnect()
            await _yield_control()

            # Pending future should have been failed with ConnectionError
            assert pending_future.done()
            with pytest.raises(AgentGateConnectionError) as exc_info:
                pending_future.result()
            assert "Connection lost" in exc_info.value.message

            await client.close()


class TestInfiniteRetriesDefault:
    async def test_infinite_retries_default(self):
        """With default max_retries=None, keeps retrying. Succeed on 3rd attempt."""
        ws1 = _make_mock_ws()
        ws_final = _make_mock_ws()

        connect_mock = AsyncMock(
            side_effect=[
                ws1,
                OSError("fail 1"),
                OSError("fail 2"),
                ws_final,
            ]
        )

        async def fake_sleep(_delay: float) -> None:
            pass

        with patch("agent_gate.client.websockets.connect", connect_mock):
            client = AgentGateClient("ws://localhost:8443", "test-token")
            client._backoff_sleep = fake_sleep
            await client.connect()

            ws1.disconnect()
            await _yield_control()

            # Should have eventually reconnected
            assert connect_mock.call_count == 4  # initial + 3 reconnect attempts

            await client.close()


class TestReauthOnReconnect:
    async def test_reauth_on_reconnect(self):
        """After reconnection, auth message is sent again with correct token."""
        ws1 = _make_mock_ws()
        ws2 = _make_mock_ws()

        connect_mock = AsyncMock(side_effect=[ws1, ws2])

        async def fake_sleep(_delay: float) -> None:
            pass

        with patch("agent_gate.client.websockets.connect", connect_mock):
            client = AgentGateClient("ws://localhost:8443", "my-secret-token")
            client._backoff_sleep = fake_sleep
            await client.connect()

            # Trigger disconnect
            ws1.disconnect()
            await _yield_control()

            # Verify auth was sent on ws2
            auth_messages = [
                json.loads(m) for m in ws2._sent if json.loads(m).get("method") == "auth"
            ]
            assert len(auth_messages) == 1
            assert auth_messages[0]["params"]["token"] == "my-secret-token"
            assert auth_messages[0]["id"] == "auth-1"

            await client.close()


class TestPendingResultsFetchedOnReconnect:
    async def test_pending_results_fetched_on_reconnect(self):
        """After reconnection with pending futures, get_pending_results is auto-called."""
        ws1 = _make_mock_ws()
        ws2 = ReconnectMockWebSocket()

        # ws2 auth response + get_pending_results response (fetched via recv)
        ws2.feed(AUTH_SUCCESS)
        ws2.feed(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "result": {
                        "results": [
                            {
                                "request_id": 1,
                                "result": json.dumps(
                                    {"status": "executed", "data": {"state": "on"}}
                                ),
                            },
                        ]
                    },
                    "id": 2,  # ID assigned by _next_id during _fetch_pending_on_reconnect
                }
            )
        )

        connect_mock = AsyncMock(side_effect=[ws1, ws2])

        async def fake_sleep(_delay: float) -> None:
            pass

        with patch("agent_gate.client.websockets.connect", connect_mock):
            client = AgentGateClient("ws://localhost:8443", "test-token")
            client._backoff_sleep = fake_sleep
            await client.connect()

            # Simulate a pending future from a tool_request made before disconnect
            loop = asyncio.get_running_loop()
            pending_future = loop.create_future()
            client._pending[1] = pending_future

            # Trigger disconnect
            ws1.disconnect()
            await _yield_control()

            # The pending future should have been resolved via _fetch_pending_on_reconnect
            assert pending_future.done()
            assert pending_future.result() == {"state": "on"}

            await client.close()


class TestCloseStopsReconnection:
    async def test_close_stops_reconnection(self):
        """If close() is called, reconnection loop stops."""
        ws1 = _make_mock_ws()

        connect_mock = AsyncMock(side_effect=[ws1])

        close_reached = asyncio.Event()

        async def fake_sleep(_delay: float) -> None:
            # Signal that reconnect loop entered sleep
            close_reached.set()
            # Actually sleep briefly to give close() a chance to set _closed
            await asyncio.sleep(0.1)

        with patch("agent_gate.client.websockets.connect", connect_mock):
            client = AgentGateClient("ws://localhost:8443", "test-token")
            client._backoff_sleep = fake_sleep
            await client.connect()

            ws1.disconnect()

            # Wait for reconnect loop to start
            await asyncio.wait_for(close_reached.wait(), timeout=1.0)

            # Now close the client -- should set _closed flag
            await client.close()

            assert client._closed is True
            # No reconnection should have happened (only initial connect)
            assert connect_mock.call_count == 1


class TestToolRequestDuringReconnect:
    async def test_tool_request_during_reconnect(self):
        """A tool_request made before disconnect stays pending, resolved after reconnect."""
        ws1 = _make_mock_ws()
        ws2 = ReconnectMockWebSocket()

        # Prepare ws2 with auth + pending results (for the request made on ws1)
        ws2.feed(AUTH_SUCCESS)
        ws2.feed(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "result": {
                        "results": [
                            {
                                "request_id": 1,
                                "result": json.dumps(
                                    {"status": "executed", "data": {"brightness": 100}}
                                ),
                            },
                        ]
                    },
                    "id": 2,
                }
            )
        )

        connect_mock = AsyncMock(side_effect=[ws1, ws2])

        async def fake_sleep(_delay: float) -> None:
            pass

        with patch("agent_gate.client.websockets.connect", connect_mock):
            client = AgentGateClient("ws://localhost:8443", "test-token")
            client._backoff_sleep = fake_sleep
            await client.connect()

            # Simulate a pending future from a tool_request that was sent
            # before disconnect (inject it manually to avoid send issues)
            loop = asyncio.get_running_loop()
            pending_future = loop.create_future()
            client._pending[1] = pending_future

            # Now disconnect before response arrives
            ws1.disconnect()

            # The pending_future should eventually resolve after reconnect
            result = await asyncio.wait_for(pending_future, timeout=2.0)
            assert result == {"brightness": 100}

            await client.close()


# ---------------------------------------------------------------------------
# New tests for review findings
# ---------------------------------------------------------------------------


class TestToolRequestWaitsForReconnect:
    async def test_tool_request_waits_for_reconnect(self):
        """tool_request() waits for _connected event when _ws is None during reconnect."""
        ws1 = _make_mock_ws()
        ws2 = ReconnectMockWebSocket()

        ws2.feed(AUTH_SUCCESS)
        # No pending results to fetch (client._pending will be empty at reconnect time
        # because we haven't added any yet)

        connect_mock = AsyncMock(side_effect=[ws1, ws2])

        reconnect_started = asyncio.Event()

        original_fake_sleep_called = False

        async def fake_sleep(_delay: float) -> None:
            nonlocal original_fake_sleep_called
            original_fake_sleep_called = True
            reconnect_started.set()

        with patch("agent_gate.client.websockets.connect", connect_mock):
            client = AgentGateClient("ws://localhost:8443", "test-token")
            client._backoff_sleep = fake_sleep
            await client.connect()

            # Trigger disconnect — _connected.clear() happens in _read_loop
            ws1.disconnect()
            await _yield_control()

            # Wait for reconnect to start (enters sleep)
            await asyncio.wait_for(reconnect_started.wait(), timeout=2.0)

            # Now the reconnect has completed (fake_sleep is instant)
            await _yield_control()

            # At this point ws2 is connected. Send a tool_request — it should succeed.
            async def respond():
                await asyncio.sleep(0.01)
                # request_id is 1 because _next_id was called once for tool_request
                ws2.feed(_tool_result(1, {"state": "42"}))

            _task = asyncio.create_task(respond())  # noqa: RUF006
            result = await asyncio.wait_for(
                client.tool_request("ha_get_state", entity_id="sensor.test"),
                timeout=2.0,
            )
            assert result == {"state": "42"}

            await client.close()


class TestCloseCancelsReconnectTask:
    async def test_close_cancels_reconnect_task(self):
        """close() cancels a running reconnect task."""
        ws1 = _make_mock_ws()

        connect_mock = AsyncMock(side_effect=[ws1])

        reconnect_entered_sleep = asyncio.Event()

        async def fake_sleep(_delay: float) -> None:
            reconnect_entered_sleep.set()
            # Block until cancelled — simulates a long backoff
            await asyncio.sleep(60)

        with patch("agent_gate.client.websockets.connect", connect_mock):
            client = AgentGateClient("ws://localhost:8443", "test-token")
            client._backoff_sleep = fake_sleep
            await client.connect()

            # Trigger disconnect so _reconnect starts
            ws1.disconnect()

            # Wait for reconnect to enter its sleep
            await asyncio.wait_for(reconnect_entered_sleep.wait(), timeout=2.0)

            # reconnect_task should exist and be running
            assert client._reconnect_task is not None
            assert not client._reconnect_task.done()

            # close() should cancel the reconnect task
            await client.close()

            assert client._reconnect_task.done()


class TestReadLoopSurvivesMalformedJson:
    async def test_read_loop_survives_malformed_json(self, mock_ws, patch_connect):
        """A malformed JSON message is skipped; the next valid message is processed."""
        mock_ws.feed(AUTH_SUCCESS)

        client = AgentGateClient("ws://localhost:8443", "test-token")
        await client.connect()

        async def respond():
            await asyncio.sleep(0.01)
            # Feed malformed JSON first
            mock_ws.feed("this is not json {{{")
            await asyncio.sleep(0.01)
            # Then a valid response
            mock_ws.feed(_tool_result(1, {"answer": "ok"}))

        _task = asyncio.create_task(respond())  # noqa: RUF006
        result = await asyncio.wait_for(
            client.tool_request("ha_get_state", entity_id="sensor.test"),
            timeout=2.0,
        )
        assert result == {"answer": "ok"}

        await client.close()
