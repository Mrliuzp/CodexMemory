from __future__ import annotations

from pathlib import Path


def test_compose_declares_pgvector_and_service_ports() -> None:
    compose = Path("docker-compose.yml").read_text(encoding="utf-8")

    assert "pgvector/pgvector:pg16" in compose
    assert '"8000:8000"' in compose
    assert '"8001:8001"' in compose
    assert "postgres:" in compose
    assert "api:" in compose
    assert "mcp:" in compose
    assert "worker:" in compose


def test_env_example_uses_placeholders_not_real_tokens() -> None:
    env_example = Path(".env.example").read_text(encoding="utf-8")

    assert "CODEX_MEMORY_DATABASE_URL=" in env_example
    assert "CODEX_MEMORY_SERVICE_TOKEN=change-me" in env_example
