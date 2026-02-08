"""Tests for agent_gate package exports (T3)."""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class TestPackageExports:
    def test_import_client(self):
        """FR7-AC1: from agent_gate import AgentGateClient works."""
        from agent_gate import AgentGateClient

        assert AgentGateClient is not None

    def test_import_errors(self):
        """FR7-AC2: from agent_gate import error classes works."""
        from agent_gate import (
            AgentGateConnectionError,
            AgentGateDenied,
            AgentGateError,
            AgentGateTimeout,
        )

        assert AgentGateError is not None
        assert AgentGateDenied is not None
        assert AgentGateTimeout is not None
        assert AgentGateConnectionError is not None

    def test_all_exports(self):
        """__all__ contains expected exports."""
        import agent_gate

        assert hasattr(agent_gate, "__all__")
        expected = {
            "AgentGateClient",
            "AgentGateError",
            "AgentGateDenied",
            "AgentGateTimeout",
            "AgentGateConnectionError",
        }
        assert set(agent_gate.__all__) == expected

    def test_client_same_reference(self):
        """Importing from package and module gives same class."""
        from agent_gate import AgentGateClient as FromPkg
        from agent_gate.client import AgentGateClient as FromMod

        assert FromPkg is FromMod

    def test_error_same_reference(self):
        """Importing errors from package and module gives same class."""
        from agent_gate import AgentGateError as FromPkg
        from agent_gate.client import AgentGateError as FromMod

        assert FromPkg is FromMod


class TestDockerFiles:
    def test_dockerfile_exists_and_has_from(self):
        """FR8-AC1: Dockerfile exists and uses python:3.12-slim."""
        dockerfile = PROJECT_ROOT / "Dockerfile"
        assert dockerfile.exists()
        content = dockerfile.read_text()
        assert "FROM python:3.12-slim" in content
        assert "CMD" in content
        assert "EXPOSE 8443" in content
        assert "VOLUME" in content

    def test_docker_compose_exists(self):
        """FR9: docker-compose.yml exists with required config."""
        dc = PROJECT_ROOT / "docker-compose.yml"
        assert dc.exists()
        content = dc.read_text()
        assert "8443:8443" in content
        assert "env_file" in content
        assert "unless-stopped" in content

    def test_dockerignore_exists(self):
        """NFR2-AC2: .dockerignore excludes tests, docs, .git, etc."""
        di = PROJECT_ROOT / ".dockerignore"
        assert di.exists()
        content = di.read_text()
        assert "tests/" in content
        assert ".git/" in content
        assert "__pycache__/" in content
        assert ".env" in content
