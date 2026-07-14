from __future__ import annotations

from pathlib import Path

import yaml


REQUIRED_SERVICES = ("postgres", "api", "mcp", "worker", "admin-web")


def _compose_services() -> dict[str, dict[str, object]]:
    compose = yaml.safe_load(Path("docker-compose.yml").read_text(encoding="utf-8"))
    return compose["services"]


def test_compose_declares_pgvector_and_required_services() -> None:
    services = _compose_services()

    assert all(service_name in services for service_name in REQUIRED_SERVICES)
    assert services["postgres"]["image"] == "pgvector/pgvector:pg16"
    healthy_dependencies = sum(
        dependency.get("condition") == "service_healthy"
        for service in services.values()
        for dependency in service.get("depends_on", {}).values()
    )
    assert healthy_dependencies >= 3


def test_compose_binds_public_ports_to_loopback() -> None:
    services = _compose_services()

    assert "127.0.0.1:8001:8001" in services["mcp"]["ports"]
    assert "127.0.0.1:5174:80" in services["admin-web"]["ports"]


def test_compose_uses_restart_and_production_mode() -> None:
    services = _compose_services()

    for service_name in REQUIRED_SERVICES:
        assert services[service_name]["restart"] == "unless-stopped"
    assert services["api"]["environment"]["CODEX_MEMORY_DEPLOYMENT_MODE"] == "production"


def test_api_is_only_exposed_on_the_compose_network() -> None:
    api = _compose_services()["api"]

    assert "8000" in api["expose"]
    assert "ports" not in api


def test_mcp_listens_on_the_container_network() -> None:
    environment = _compose_services()["mcp"]["environment"]

    assert environment["CODEX_MEMORY_MCP_HOST"] == "0.0.0.0"
    assert environment["CODEX_MEMORY_MCP_PORT"] == 8001


def test_admin_nginx_proxies_api_over_the_compose_network() -> None:
    nginx = Path("apps/admin-web/nginx.conf").read_text(encoding="utf-8")

    assert "proxy_pass http://api:8000;" in nginx


def test_env_example_uses_placeholders_not_real_tokens() -> None:
    env_example = Path(".env.example").read_text(encoding="utf-8")

    assert "CODEX_MEMORY_DATABASE_URL=" in env_example
    assert "CODEX_MEMORY_SERVICE_TOKEN=change-me" in env_example
    assert "CODEX_MEMORY_MCP_TOKEN=change-me-mcp-token" in env_example


def test_mcp_service_receives_independent_api_and_mcp_tokens() -> None:
    environment = _compose_services()["mcp"]["environment"]

    assert environment["CODEX_MEMORY_API_TOKEN"] == "${CODEX_MEMORY_SERVICE_TOKEN}"
    assert environment["CODEX_MEMORY_MCP_TOKEN"] == "${CODEX_MEMORY_MCP_TOKEN}"
