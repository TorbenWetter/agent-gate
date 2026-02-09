"""Tests for agentpass.messenger.base — MessengerAdapter ABC and dataclasses."""

import pytest

from agentpass.messenger.base import (
    ApprovalChoice,
    ApprovalRequest,
    ApprovalResult,
    MessengerAdapter,
)


class TestApprovalRequest:
    def test_construction(self):
        req = ApprovalRequest(
            request_id="req-42",
            tool_name="ha_call_service",
            args={"domain": "light", "service": "turn_on", "entity_id": "light.kitchen"},
            signature="ha_call_service(light.turn_on, light.kitchen)",
        )
        assert req.request_id == "req-42"
        assert req.tool_name == "ha_call_service"
        assert req.args == {
            "domain": "light",
            "service": "turn_on",
            "entity_id": "light.kitchen",
        }
        assert req.signature == "ha_call_service(light.turn_on, light.kitchen)"

    def test_field_access(self):
        req = ApprovalRequest(
            request_id="r1",
            tool_name="ha_get_state",
            args={"entity_id": "sensor.temp"},
            signature="ha_get_state(sensor.temp)",
        )
        assert hasattr(req, "request_id")
        assert hasattr(req, "tool_name")
        assert hasattr(req, "args")
        assert hasattr(req, "signature")

    def test_args_is_dict(self):
        req = ApprovalRequest(
            request_id="r1",
            tool_name="test",
            args={},
            signature="test()",
        )
        assert isinstance(req.args, dict)

    def test_empty_args(self):
        req = ApprovalRequest(
            request_id="r1",
            tool_name="test",
            args={},
            signature="test()",
        )
        assert req.args == {}


class TestApprovalChoice:
    def test_construction(self):
        choice = ApprovalChoice(label="Allow", action="allow")
        assert choice.label == "Allow"
        assert choice.action == "allow"

    def test_field_access(self):
        choice = ApprovalChoice(label="Deny", action="deny")
        assert hasattr(choice, "label")
        assert hasattr(choice, "action")

    def test_deny_choice(self):
        choice = ApprovalChoice(label="Deny", action="deny")
        assert choice.label == "Deny"
        assert choice.action == "deny"


class TestApprovalResult:
    def test_construction(self):
        result = ApprovalResult(
            request_id="req-42",
            action="allow",
            user_id="12345678",
            timestamp=1700000000.0,
        )
        assert result.request_id == "req-42"
        assert result.action == "allow"
        assert result.user_id == "12345678"
        assert result.timestamp == 1700000000.0

    def test_field_access(self):
        result = ApprovalResult(
            request_id="r1",
            action="deny",
            user_id="99",
            timestamp=0.0,
        )
        assert hasattr(result, "request_id")
        assert hasattr(result, "action")
        assert hasattr(result, "user_id")
        assert hasattr(result, "timestamp")

    def test_deny_action(self):
        result = ApprovalResult(
            request_id="r1",
            action="deny",
            user_id="555",
            timestamp=1700000001.0,
        )
        assert result.action == "deny"

    def test_user_id_is_string(self):
        result = ApprovalResult(
            request_id="r1",
            action="allow",
            user_id="12345678",
            timestamp=1700000000.0,
        )
        assert isinstance(result.user_id, str)


class TestMessengerAdapterABC:
    def test_cannot_instantiate_directly(self):
        """MessengerAdapter is abstract and cannot be instantiated."""
        with pytest.raises(TypeError):
            MessengerAdapter()

    def test_concrete_subclass_can_be_instantiated(self):
        """A subclass implementing all abstract methods can be instantiated."""

        class ConcreteMessenger(MessengerAdapter):
            async def send_approval(self, request, choices):
                return "msg-1"

            async def update_approval(self, message_id, status, detail):
                pass

            async def on_approval_callback(self, callback):
                pass

            async def start(self):
                pass

            async def stop(self):
                pass

        messenger = ConcreteMessenger()
        assert isinstance(messenger, MessengerAdapter)

    def test_partial_implementation_missing_send_approval(self):
        """A subclass missing send_approval cannot be instantiated."""

        class PartialMessenger(MessengerAdapter):
            async def update_approval(self, message_id, status, detail):
                pass

            async def on_approval_callback(self, callback):
                pass

            async def start(self):
                pass

            async def stop(self):
                pass

        with pytest.raises(TypeError):
            PartialMessenger()

    def test_partial_implementation_missing_update_approval(self):
        """A subclass missing update_approval cannot be instantiated."""

        class PartialMessenger(MessengerAdapter):
            async def send_approval(self, request, choices):
                return "msg-1"

            async def on_approval_callback(self, callback):
                pass

            async def start(self):
                pass

            async def stop(self):
                pass

        with pytest.raises(TypeError):
            PartialMessenger()

    def test_partial_implementation_missing_on_approval_callback(self):
        """A subclass missing on_approval_callback cannot be instantiated."""

        class PartialMessenger(MessengerAdapter):
            async def send_approval(self, request, choices):
                return "msg-1"

            async def update_approval(self, message_id, status, detail):
                pass

            async def start(self):
                pass

            async def stop(self):
                pass

        with pytest.raises(TypeError):
            PartialMessenger()

    def test_partial_implementation_missing_start(self):
        """A subclass missing start cannot be instantiated."""

        class PartialMessenger(MessengerAdapter):
            async def send_approval(self, request, choices):
                return "msg-1"

            async def update_approval(self, message_id, status, detail):
                pass

            async def on_approval_callback(self, callback):
                pass

            async def stop(self):
                pass

        with pytest.raises(TypeError):
            PartialMessenger()

    def test_partial_implementation_missing_stop(self):
        """A subclass missing stop cannot be instantiated."""

        class PartialMessenger(MessengerAdapter):
            async def send_approval(self, request, choices):
                return "msg-1"

            async def update_approval(self, message_id, status, detail):
                pass

            async def on_approval_callback(self, callback):
                pass

            async def start(self):
                pass

        with pytest.raises(TypeError):
            PartialMessenger()

    async def test_concrete_subclass_methods_are_callable(self):
        """Verify that the concrete subclass methods can actually be called."""

        class ConcreteMessenger(MessengerAdapter):
            async def send_approval(self, request, choices):
                return "msg-1"

            async def update_approval(self, message_id, status, detail):
                pass

            async def on_approval_callback(self, callback):
                self._callback = callback

            async def start(self):
                self._started = True

            async def stop(self):
                self._stopped = True

        messenger = ConcreteMessenger()

        # Test send_approval
        req = ApprovalRequest(
            request_id="r1",
            tool_name="test",
            args={},
            signature="test()",
        )
        choices = [
            ApprovalChoice(label="Allow", action="allow"),
            ApprovalChoice(label="Deny", action="deny"),
        ]
        msg_id = await messenger.send_approval(req, choices)
        assert msg_id == "msg-1"

        # Test update_approval
        await messenger.update_approval("msg-1", "approved", "Allowed by user")

        # Test on_approval_callback
        async def my_callback(result: ApprovalResult) -> None:
            pass

        await messenger.on_approval_callback(my_callback)
        assert messenger._callback is my_callback

        # Test start
        await messenger.start()
        assert messenger._started is True

        # Test stop
        await messenger.stop()
        assert messenger._stopped is True
