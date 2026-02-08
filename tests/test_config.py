"""Tests for agent_gate.config — YAML loading, env var substitution, validation."""

import os
import textwrap

import pytest

from agent_gate.config import (
    ConfigError,
    Permissions,
    load_config,
    load_permissions,
    substitute_env_vars,
)


class TestSubstituteEnvVars:
    def test_replaces_env_var_in_string(self, monkeypatch):
        monkeypatch.setenv("MY_TOKEN", "secret123")
        assert substitute_env_vars("${MY_TOKEN}") == "secret123"

    def test_replaces_multiple_vars_in_string(self, monkeypatch):
        monkeypatch.setenv("HOST", "localhost")
        monkeypatch.setenv("PORT", "8080")
        assert substitute_env_vars("${HOST}:${PORT}") == "localhost:8080"

    def test_replaces_in_nested_dict(self, monkeypatch):
        monkeypatch.setenv("TOKEN", "abc")
        data = {"outer": {"inner": "${TOKEN}"}}
        result = substitute_env_vars(data)
        assert result == {"outer": {"inner": "abc"}}

    def test_replaces_in_list(self, monkeypatch):
        monkeypatch.setenv("VAL", "x")
        data = ["${VAL}", "literal"]
        result = substitute_env_vars(data)
        assert result == ["x", "literal"]

    def test_raises_on_unset_env_var(self):
        # Ensure the var is not set
        os.environ.pop("UNSET_VAR_XYZ", None)
        with pytest.raises(ConfigError, match="UNSET_VAR_XYZ"):
            substitute_env_vars("${UNSET_VAR_XYZ}")

    def test_ignores_non_string_values(self):
        assert substitute_env_vars(42) == 42
        assert substitute_env_vars(True) is True
        assert substitute_env_vars(None) is None
        assert substitute_env_vars(3.14) == 3.14


# --- Fixtures for config/permissions YAML files ---

VALID_CONFIG_YAML = textwrap.dedent("""\
    gateway:
      host: "0.0.0.0"
      port: 8443
      tls:
        cert: "/path/cert.pem"
        key: "/path/key.pem"
    agent:
      token: "test-token"
    messenger:
      type: "telegram"
      telegram:
        token: "bot-token"
        chat_id: -100123
        allowed_users: [111, 222]
    services:
      homeassistant:
        url: "http://ha.local:8123"
        token: "ha-token"
    storage:
      type: "sqlite"
      path: "./data/test.db"
""")

VALID_PERMISSIONS_YAML = textwrap.dedent("""\
    defaults:
      - pattern: "ha_get_*"
        action: allow
      - pattern: "*"
        action: ask
    rules:
      - pattern: "ha_call_service(lock.*)"
        action: deny
        description: "Lock control denied"
""")


@pytest.fixture()
def config_file(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(VALID_CONFIG_YAML)
    return p


@pytest.fixture()
def permissions_file(tmp_path):
    p = tmp_path / "permissions.yaml"
    p.write_text(VALID_PERMISSIONS_YAML)
    return p


class TestLoadConfig:
    def test_valid_config(self, config_file):
        cfg = load_config(str(config_file))
        assert cfg.gateway.host == "0.0.0.0"
        assert cfg.gateway.port == 8443
        assert cfg.gateway.tls.cert == "/path/cert.pem"
        assert cfg.agent.token == "test-token"
        assert cfg.messenger.type == "telegram"
        assert cfg.messenger.telegram.token == "bot-token"
        assert cfg.messenger.telegram.chat_id == -100123
        assert cfg.messenger.telegram.allowed_users == [111, 222]
        assert cfg.services["homeassistant"].url == "http://ha.local:8123"
        assert cfg.storage.type == "sqlite"
        assert cfg.storage.path == "./data/test.db"

    def test_default_approval_timeout(self, config_file):
        cfg = load_config(str(config_file))
        assert cfg.approval_timeout == 900

    def test_default_rate_limit(self, config_file):
        cfg = load_config(str(config_file))
        assert cfg.rate_limit.max_pending_approvals == 10
        assert cfg.rate_limit.max_requests_per_minute == 60

    def test_custom_approval_timeout(self, tmp_path):
        yaml_text = VALID_CONFIG_YAML + "approval_timeout: 300\n"
        p = tmp_path / "config.yaml"
        p.write_text(yaml_text)
        cfg = load_config(str(p))
        assert cfg.approval_timeout == 300

    def test_port_string_coerced_to_int(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MY_PORT", "9999")
        yaml_text = VALID_CONFIG_YAML.replace("port: 8443", 'port: "${MY_PORT}"')
        p = tmp_path / "config.yaml"
        p.write_text(yaml_text)
        cfg = load_config(str(p))
        assert cfg.gateway.port == 9999
        assert isinstance(cfg.gateway.port, int)

    def test_chat_id_string_coerced_to_int(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CHAT_ID", "-100999")
        yaml_text = VALID_CONFIG_YAML.replace("chat_id: -100123", 'chat_id: "${CHAT_ID}"')
        p = tmp_path / "config.yaml"
        p.write_text(yaml_text)
        cfg = load_config(str(p))
        assert cfg.messenger.telegram.chat_id == -100999
        assert isinstance(cfg.messenger.telegram.chat_id, int)

    def test_missing_gateway_host(self, tmp_path):
        yaml_text = VALID_CONFIG_YAML.replace('  host: "0.0.0.0"\n', "")
        p = tmp_path / "config.yaml"
        p.write_text(yaml_text)
        with pytest.raises(ConfigError, match=r"gateway\.host"):
            load_config(str(p))

    def test_missing_agent_token(self, tmp_path):
        yaml_text = VALID_CONFIG_YAML.replace('token: "test-token"', 'token: ""')
        p = tmp_path / "config.yaml"
        p.write_text(yaml_text)
        with pytest.raises(ConfigError, match=r"agent\.token"):
            load_config(str(p))

    def test_empty_allowed_users(self, tmp_path):
        yaml_text = VALID_CONFIG_YAML.replace("allowed_users: [111, 222]", "allowed_users: []")
        p = tmp_path / "config.yaml"
        p.write_text(yaml_text)
        with pytest.raises(ConfigError, match="allowed_users"):
            load_config(str(p))

    def test_missing_config_file(self):
        with pytest.raises(ConfigError, match="not found"):
            load_config("/nonexistent/config.yaml")

    def test_no_tls_config(self, tmp_path):
        yaml_text = VALID_CONFIG_YAML.replace(
            '  tls:\n    cert: "/path/cert.pem"\n    key: "/path/key.pem"\n', ""
        )
        p = tmp_path / "config.yaml"
        p.write_text(yaml_text)
        cfg = load_config(str(p))
        assert cfg.gateway.tls is None

    def test_env_var_in_token(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AGENT_TOKEN", "secret-from-env")
        yaml_text = VALID_CONFIG_YAML.replace('token: "test-token"', 'token: "${AGENT_TOKEN}"', 1)
        p = tmp_path / "config.yaml"
        p.write_text(yaml_text)
        cfg = load_config(str(p))
        assert cfg.agent.token == "secret-from-env"

    def test_unsupported_messenger_type(self, tmp_path):
        yaml_text = VALID_CONFIG_YAML.replace('type: "telegram"', 'type: "slack"')
        p = tmp_path / "config.yaml"
        p.write_text(yaml_text)
        with pytest.raises(ConfigError, match="Unsupported messenger type"):
            load_config(str(p))

    def test_unsupported_storage_type(self, tmp_path):
        yaml_text = VALID_CONFIG_YAML.replace('type: "sqlite"', 'type: "postgres"')
        p = tmp_path / "config.yaml"
        p.write_text(yaml_text)
        with pytest.raises(ConfigError, match="Unsupported storage type"):
            load_config(str(p))

    def test_negative_approval_timeout(self, tmp_path):
        yaml_text = VALID_CONFIG_YAML + "approval_timeout: -1\n"
        p = tmp_path / "config.yaml"
        p.write_text(yaml_text)
        with pytest.raises(ConfigError, match="approval_timeout"):
            load_config(str(p))

    def test_zero_approval_timeout(self, tmp_path):
        yaml_text = VALID_CONFIG_YAML + "approval_timeout: 0\n"
        p = tmp_path / "config.yaml"
        p.write_text(yaml_text)
        with pytest.raises(ConfigError, match="approval_timeout"):
            load_config(str(p))

    def test_missing_homeassistant_service(self, tmp_path):
        yaml_text = VALID_CONFIG_YAML.replace(
            '  homeassistant:\n    url: "http://ha.local:8123"\n    token: "ha-token"\n',
            "  other_service: {}\n",
        )
        p = tmp_path / "config.yaml"
        p.write_text(yaml_text)
        with pytest.raises(ConfigError, match=r"services\.homeassistant"):
            load_config(str(p))

    def test_env_var_in_ha_token(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HA_TOKEN", "ha-secret-from-env")
        yaml_text = VALID_CONFIG_YAML.replace('token: "ha-token"', 'token: "${HA_TOKEN}"')
        p = tmp_path / "config.yaml"
        p.write_text(yaml_text)
        cfg = load_config(str(p))
        assert cfg.services["homeassistant"].token == "ha-secret-from-env"


class TestLoadPermissions:
    def test_valid_permissions(self, permissions_file):
        perms = load_permissions(str(permissions_file))
        assert isinstance(perms, Permissions)
        assert len(perms.defaults) == 2
        assert perms.defaults[0].pattern == "ha_get_*"
        assert perms.defaults[0].action == "allow"
        assert perms.defaults[1].pattern == "*"
        assert perms.defaults[1].action == "ask"
        assert len(perms.rules) == 1
        assert perms.rules[0].pattern == "ha_call_service(lock.*)"
        assert perms.rules[0].action == "deny"
        assert perms.rules[0].description == "Lock control denied"

    def test_empty_rules(self, tmp_path):
        yaml_text = textwrap.dedent("""\
            defaults:
              - pattern: "*"
                action: ask
            rules: []
        """)
        p = tmp_path / "permissions.yaml"
        p.write_text(yaml_text)
        perms = load_permissions(str(p))
        assert perms.rules == []

    def test_missing_rules_key(self, tmp_path):
        yaml_text = textwrap.dedent("""\
            defaults:
              - pattern: "*"
                action: ask
        """)
        p = tmp_path / "permissions.yaml"
        p.write_text(yaml_text)
        perms = load_permissions(str(p))
        assert perms.rules == []

    def test_missing_permissions_file(self):
        with pytest.raises(ConfigError, match="not found"):
            load_permissions("/nonexistent/permissions.yaml")

    def test_invalid_rule_action(self, tmp_path):
        yaml_text = textwrap.dedent("""\
            defaults:
              - pattern: "*"
                action: ask
            rules:
              - pattern: "ha_fire_event(*)"
                action: block
        """)
        p = tmp_path / "permissions.yaml"
        p.write_text(yaml_text)
        with pytest.raises(ConfigError, match="Invalid permission action"):
            load_permissions(str(p))

    def test_invalid_default_action(self, tmp_path):
        yaml_text = textwrap.dedent("""\
            defaults:
              - pattern: "*"
                action: permit
        """)
        p = tmp_path / "permissions.yaml"
        p.write_text(yaml_text)
        with pytest.raises(ConfigError, match="Invalid permission action"):
            load_permissions(str(p))

    def test_rule_description_default(self, tmp_path):
        yaml_text = textwrap.dedent("""\
            defaults:
              - pattern: "*"
                action: ask
            rules:
              - pattern: "ha_fire_event(*)"
                action: deny
        """)
        p = tmp_path / "permissions.yaml"
        p.write_text(yaml_text)
        perms = load_permissions(str(p))
        assert perms.rules[0].description == ""
