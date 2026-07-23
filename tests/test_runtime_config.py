from __future__ import annotations

import pytest


def _set_valid_production_env(monkeypatch) -> None:
    monkeypatch.setenv("CODEX_MEMORY_SERVICE_TOKEN", "service-token")
    monkeypatch.setenv("CODEX_MEMORY_MCP_TOKEN", "mcp-token")
    monkeypatch.setenv("CODEX_MEMORY_ADMIN_USERNAME", "memory-admin")
    monkeypatch.setenv("CODEX_MEMORY_ADMIN_PASSWORD", "admin-password")
    monkeypatch.setenv("CODEX_MEMORY_ADMIN_SESSION_SECRET", "admin-session-secret")


def test_production_rejects_sqlite() -> None:
    from codex_memory.config import Settings

    settings = Settings(database_url="sqlite:///memory-v1.db", deployment_mode="production")

    with pytest.raises(ValueError, match="生产环境必须使用 PostgreSQL"):
        settings.validate_runtime()


def test_development_allows_sqlite() -> None:
    from codex_memory.config import Settings

    Settings(database_url="sqlite:///memory-v1.db", deployment_mode="development").validate_runtime()


def test_production_accepts_non_placeholder_configuration(monkeypatch) -> None:
    from codex_memory.config import Settings

    _set_valid_production_env(monkeypatch)

    Settings(
        database_url="postgresql+psycopg://codex:password@db/codex_memory",
        deployment_mode="production",
    ).validate_runtime()


@pytest.mark.parametrize(
    ("variable", "value"),
    [
        ("CODEX_MEMORY_SERVICE_TOKEN", "change-me-service-token"),
        ("CODEX_MEMORY_MCP_TOKEN", "change_me_mcp_token"),
        ("CODEX_MEMORY_ADMIN_USERNAME", "admin"),
        ("CODEX_MEMORY_ADMIN_PASSWORD", "change-me-admin-password"),
        ("CODEX_MEMORY_ADMIN_SESSION_SECRET", "change_me_session_secret"),
    ],
)
def test_production_rejects_placeholder_configuration(monkeypatch, variable: str, value: str) -> None:
    from codex_memory.config import Settings

    _set_valid_production_env(monkeypatch)
    monkeypatch.setenv(variable, value)

    settings = Settings(
        database_url="postgresql+psycopg://codex:password@db/codex_memory",
        deployment_mode="production",
    )

    with pytest.raises(ValueError, match="占位符"):
        settings.validate_runtime()
