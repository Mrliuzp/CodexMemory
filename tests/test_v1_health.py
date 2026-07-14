from __future__ import annotations

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


def test_readiness_reports_database_and_schema() -> None:
    from codex_memory.http_api import create_v1_app

    response = TestClient(create_v1_app(_factory())).get("/health/ready")

    assert response.status_code == 200
    assert response.json()["database"] == "ok"
    assert response.json()["schema"] in {"ok", "development"}
    assert "database_url" not in response.json()
