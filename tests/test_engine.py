"""Tests for agent_gate.engine — signature building, validation, permission evaluation."""

import pytest

from agent_gate.config import PermissionRule, Permissions
from agent_gate.engine import PermissionEngine, build_signature, validate_args
from agent_gate.models import Decision


class TestBuildSignature:
    def test_ha_call_service(self):
        sig = build_signature(
            "ha_call_service",
            {
                "domain": "light",
                "service": "turn_on",
                "entity_id": "light.bedroom",
            },
        )
        assert sig == "ha_call_service(light.turn_on, light.bedroom)"

    def test_ha_get_state(self):
        sig = build_signature("ha_get_state", {"entity_id": "sensor.temp"})
        assert sig == "ha_get_state(sensor.temp)"

    def test_ha_get_states_no_parens(self):
        sig = build_signature("ha_get_states", {})
        assert sig == "ha_get_states"

    def test_ha_fire_event(self):
        sig = build_signature("ha_fire_event", {"event_type": "custom_event"})
        assert sig == "ha_fire_event(custom_event)"

    def test_unknown_tool_sorted_keys(self):
        sig = build_signature("unknown_tool", {"b": "2", "a": "1"})
        assert sig == "unknown_tool(1, 2)"

    def test_unknown_tool_no_args(self):
        sig = build_signature("no_args_tool", {})
        assert sig == "no_args_tool"

    def test_ha_call_service_without_entity_id(self):
        sig = build_signature(
            "ha_call_service",
            {
                "domain": "homeassistant",
                "service": "restart",
            },
        )
        assert sig == "ha_call_service(homeassistant.restart, )"

    def test_ha_call_service_field_order_irrelevant(self):
        # Dict ordering should not affect signature
        sig1 = build_signature(
            "ha_call_service",
            {
                "entity_id": "light.bedroom",
                "domain": "light",
                "service": "turn_on",
            },
        )
        sig2 = build_signature(
            "ha_call_service",
            {
                "domain": "light",
                "service": "turn_on",
                "entity_id": "light.bedroom",
            },
        )
        assert sig1 == sig2


class TestValidateArgs:
    def test_rejects_asterisk(self):
        with pytest.raises(ValueError, match="forbidden"):
            validate_args("ha_get_state", {"entity_id": "light.*"})

    def test_rejects_question_mark(self):
        with pytest.raises(ValueError, match="forbidden"):
            validate_args("ha_get_state", {"entity_id": "light.?"})

    def test_rejects_bracket(self):
        with pytest.raises(ValueError, match="forbidden"):
            validate_args("ha_get_state", {"entity_id": "light.[a]"})

    def test_rejects_parenthesis(self):
        with pytest.raises(ValueError, match="forbidden"):
            validate_args("ha_get_state", {"entity_id": "light.(x)"})

    def test_rejects_comma(self):
        with pytest.raises(ValueError, match="forbidden"):
            validate_args("ha_get_state", {"entity_id": "a,b"})

    def test_rejects_null_byte(self):
        with pytest.raises(ValueError, match="forbidden"):
            validate_args("ha_get_state", {"entity_id": "light\x00hack"})

    def test_rejects_control_char(self):
        with pytest.raises(ValueError, match="forbidden"):
            validate_args("ha_get_state", {"entity_id": "light\x01"})

    def test_rejects_invalid_ha_identifier_uppercase(self):
        with pytest.raises(ValueError, match="Invalid HA identifier"):
            validate_args("ha_get_state", {"entity_id": "Light.Bedroom"})

    def test_rejects_ha_identifier_with_spaces(self):
        with pytest.raises(ValueError, match="Invalid HA identifier"):
            validate_args("ha_get_state", {"entity_id": "light. bedroom"})

    def test_accepts_valid_ha_identifiers(self):
        # Should not raise
        validate_args("ha_get_state", {"entity_id": "light.bedroom"})
        validate_args("ha_get_state", {"entity_id": "sensor.living_room_temp"})
        validate_args(
            "ha_call_service",
            {
                "domain": "light",
                "service": "turn_on",
                "entity_id": "light.bedroom",
            },
        )

    def test_skips_non_string_values(self):
        # Should not raise — non-string values are skipped
        validate_args(
            "ha_call_service",
            {
                "domain": "light",
                "service": "turn_on",
                "entity_id": "light.bedroom",
                "brightness": 255,
            },
        )

    def test_non_ha_tool_skips_identifier_check(self):
        # Non-HA tools don't validate HA identifier format
        validate_args("custom_tool", {"key": "ANY_VALUE_123"})

    def test_non_ha_tool_still_rejects_forbidden_chars(self):
        with pytest.raises(ValueError, match="forbidden"):
            validate_args("custom_tool", {"key": "value*"})


class TestPermissionEngine:
    @staticmethod
    def _make_permissions(
        defaults: list[tuple[str, str]] | None = None,
        rules: list[tuple[str, str]] | None = None,
    ) -> Permissions:
        return Permissions(
            defaults=[PermissionRule(pattern=p, action=a) for p, a in (defaults or [])],
            rules=[PermissionRule(pattern=p, action=a) for p, a in (rules or [])],
        )

    def test_deny_rule_wins(self):
        perms = self._make_permissions(
            rules=[
                ("ha_call_service(lock.*)", "deny"),
                ("ha_call_service(lock.front_door)", "allow"),
            ],
        )
        engine = PermissionEngine(perms)
        result = engine.evaluate(
            "ha_call_service",
            {
                "domain": "lock",
                "service": "lock",
                "entity_id": "lock.front_door",
            },
        )
        assert result == Decision.DENY

    def test_allow_rule_when_no_deny(self):
        perms = self._make_permissions(
            rules=[("ha_get_state(sensor.*)", "allow")],
        )
        engine = PermissionEngine(perms)
        result = engine.evaluate("ha_get_state", {"entity_id": "sensor.temp"})
        assert result == Decision.ALLOW

    def test_ask_rule_when_no_deny_or_allow(self):
        perms = self._make_permissions(
            rules=[("ha_call_service(light.*)", "ask")],
        )
        engine = PermissionEngine(perms)
        result = engine.evaluate(
            "ha_call_service",
            {
                "domain": "light",
                "service": "turn_on",
                "entity_id": "light.bedroom",
            },
        )
        assert result == Decision.ASK

    def test_falls_through_to_defaults(self):
        perms = self._make_permissions(
            defaults=[
                ("ha_get_*", "allow"),
                ("*", "ask"),
            ],
        )
        engine = PermissionEngine(perms)
        result = engine.evaluate("ha_get_state", {"entity_id": "sensor.temp"})
        assert result == Decision.ALLOW

    def test_defaults_first_match_wins(self):
        perms = self._make_permissions(
            defaults=[
                ("ha_call_service*", "ask"),
                ("*", "deny"),
            ],
        )
        engine = PermissionEngine(perms)
        result = engine.evaluate(
            "ha_call_service",
            {
                "domain": "light",
                "service": "turn_on",
                "entity_id": "light.bedroom",
            },
        )
        assert result == Decision.ASK

    def test_global_fallback_is_ask(self):
        perms = self._make_permissions()  # No rules, no defaults
        engine = PermissionEngine(perms)
        result = engine.evaluate("unknown_tool", {"key": "value"})
        assert result == Decision.ASK

    def test_deny_overrides_more_specific_allow(self):
        # Broad deny + specific allow → deny wins
        perms = self._make_permissions(
            rules=[
                ("ha_call_service(lock.*)", "deny"),
                ("ha_call_service(lock.front_door, lock.front_door)", "allow"),
            ],
        )
        engine = PermissionEngine(perms)
        result = engine.evaluate(
            "ha_call_service",
            {
                "domain": "lock",
                "service": "front_door",
                "entity_id": "lock.front_door",
            },
        )
        assert result == Decision.DENY

    def test_rules_checked_before_defaults(self):
        perms = self._make_permissions(
            defaults=[("ha_get_*", "ask")],
            rules=[("ha_get_state(sensor.*)", "allow")],
        )
        engine = PermissionEngine(perms)
        result = engine.evaluate("ha_get_state", {"entity_id": "sensor.temp"})
        assert result == Decision.ALLOW

    def test_no_args_tool_matching(self):
        perms = self._make_permissions(
            defaults=[("ha_get_*", "allow")],
        )
        engine = PermissionEngine(perms)
        result = engine.evaluate("ha_get_states", {})
        assert result == Decision.ALLOW

    def test_ha_fire_event_deny_default(self):
        perms = self._make_permissions(
            defaults=[("ha_fire_event(*)", "deny")],
        )
        engine = PermissionEngine(perms)
        result = engine.evaluate("ha_fire_event", {"event_type": "test_event"})
        assert result == Decision.DENY
