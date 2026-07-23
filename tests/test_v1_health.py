from __future__ import annotations

from fastapi.testclient import TestClient


def test_v1_health_reports_database_probe() -> None:
    from codex_memory.db import create_schema, create_session_factory, create_postgres_test_engine
    from codex_memory.http_api import create_v1_app

    engine = create_postgres_test_engine()
    create_schema(engine)
    response = TestClient(create_v1_app(create_session_factory(engine))).get("/api/v1/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["database"] == "ok"
