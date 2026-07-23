from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


def _factory():
    from codex_memory.db import create_schema, create_session_factory, create_sqlite_engine

    engine = create_sqlite_engine()
    create_schema(engine)
    return create_session_factory(engine)


def test_v1_health_reports_database_probe() -> None:
    from codex_memory.http_api import create_v1_app

    response = TestClient(create_v1_app(_factory())).get("/api/v1/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["database"] == "ok"


def test_liveness_does_not_probe_database() -> None:
    from codex_memory.http_api import create_v1_app

    client = TestClient(create_v1_app(_factory()))

    assert client.get("/health/live").json() == {"status": "ok"}


def test_expected_schema_revision_matches_alembic_head() -> None:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    from codex_memory.runtime_health import EXPECTED_SCHEMA_REVISION

    assert ScriptDirectory.from_config(Config("alembic.ini")).get_current_head() == EXPECTED_SCHEMA_REVISION

def test_readiness_detects_an_unmigrated_schema() -> None:
    from codex_memory.runtime_health import build_readiness

    payload = build_readiness(_factory())

    assert payload["status"] == "degraded"
    assert payload["database"] == "ok"
    assert payload["schema"] == "outdated"
    assert payload["vector"] == "not-applicable"


def test_readiness_returns_200_when_all_checks_are_ready(monkeypatch) -> None:
    from codex_memory import runtime_health
    from codex_memory.http_api import create_v1_app

    payload = {
        "status": "ok",
        "database": "ok",
        "schema": "ok",
        "vector": "not-applicable",
    }
    monkeypatch.setattr(runtime_health, "build_readiness", lambda _factory: payload)

    response = TestClient(create_v1_app(_factory())).get("/health/ready")

    assert response.status_code == 200
    assert response.json() == payload
    assert "database_url" not in response.json()


@pytest.mark.parametrize(
    "payload",
    [
        {"status": "degraded", "database": "error", "schema": "unknown", "vector": "unknown"},
        {"status": "degraded", "database": "ok", "schema": "outdated", "vector": "not-applicable"},
        {"status": "degraded", "database": "ok", "schema": "ok", "vector": "missing"},
    ],
)
def test_readiness_returns_503_when_a_required_check_is_not_ok(
    monkeypatch,
    payload: dict[str, str],
) -> None:
    from codex_memory import runtime_health
    from codex_memory.http_api import create_v1_app

    monkeypatch.setattr(runtime_health, "build_readiness", lambda _factory: payload)

    response = TestClient(create_v1_app(_factory())).get("/health/ready")

    assert response.status_code == 503
    assert response.json() == payload
