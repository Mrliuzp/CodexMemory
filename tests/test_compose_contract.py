from __future__ import annotations

from pathlib import Path


def test_compose_declares_pgvector_and_required_services() -> None:
    compose = Path("docker-compose.yml").read_text(encoding="utf-8")

    assert "pgvector/pgvector:pg16" in compose
    assert "postgres:" in compose
    assert "api:" in compose
    assert "mcp:" in compose
    assert "admin-web:" in compose
    assert "worker:" in compose
    assert "healthcheck:" in compose
    assert compose.count("condition: service_healthy") >= 3


def test_compose_binds_public_ports_to_loopback() -> None:
    compose = Path("docker-compose.yml").read_text(encoding="utf-8")

    assert '"127.0.0.1:8001:8001"' in compose
    assert '"127.0.0.1:5174:80"' in compose
    assert '"8000:8000"' not in compose


def test_compose_uses_restart_and_production_mode() -> None:
    compose = Path("docker-compose.yml").read_text(encoding="utf-8")

    assert compose.count("restart: unless-stopped") >= 5
    assert "CODEX_MEMORY_DEPLOYMENT_MODE: production" in compose
    assert "CODEX_MEMORY_MCP_TOKEN" in compose


def test_env_example_uses_placeholders_not_real_tokens() -> None:
    env_example = Path(".env.example").read_text(encoding="utf-8")

    assert "CODEX_MEMORY_DATABASE_URL=" in env_example
    assert "CODEX_MEMORY_SERVICE_TOKEN=change-me" in env_example
    assert "CODEX_MEMORY_MCP_TOKEN=change-me-mcp-token" in env_example


def test_mcp_service_receives_independent_api_and_mcp_tokens() -> None:
    compose = Path("docker-compose.yml").read_text(encoding="utf-8")
    mcp_service = compose.split("\n  mcp:\n", maxsplit=1)[1].split("\n  worker:\n", maxsplit=1)[0]

    assert "CODEX_MEMORY_API_TOKEN: ${CODEX_MEMORY_SERVICE_TOKEN}" in mcp_service
    assert "CODEX_MEMORY_MCP_TOKEN: ${CODEX_MEMORY_MCP_TOKEN}" in mcp_service
