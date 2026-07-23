from __future__ import annotations

import hashlib
from fastapi.testclient import TestClient


def _factory_and_client() -> tuple[object, TestClient]:
    from codex_memory.db import create_schema, create_session_factory, create_sqlite_engine
    from codex_memory.db_models import ApiKeyRow, ProjectRow
    from codex_memory.http_api import create_v1_app
    from codex_memory.v11_models import V11Base

    engine = create_sqlite_engine()
    create_schema(engine)
    V11Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    with factory() as session:
        project = ProjectRow(project_key="erp", name="ERP")
        session.add(project)
        session.flush()
        session.add_all([
            ApiKeyRow(
                project_id=project.id,
                token_hash=hashlib.sha256(b"admin-token").hexdigest(),
                permissions=["read", "admin"],
            ),
        ])
        session.commit()
    return factory, TestClient(create_v1_app(factory))


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_health_check() -> None:
    _, client = _factory_and_client()
    resp = client.get("/api/v1/admin/health/check", headers=_auth("admin-token"))
    assert resp.status_code == 200
    data = resp.json()
    assert "healthy" in data
    assert "issues" in data
    assert isinstance(data["issues"], list)


def test_get_alert_config() -> None:
    _, client = _factory_and_client()
    resp = client.get("/api/v1/admin/alerts/config", headers=_auth("admin-token"))
    assert resp.status_code == 200
    data = resp.json()
    assert "stuck_job_threshold" in data
    assert "candidate_threshold" in data
    assert data["stuck_job_threshold"] == 10


def test_update_alert_config() -> None:
    _, client = _factory_and_client()
    resp = client.put(
        "/api/v1/admin/alerts/config",
        headers=_auth("admin-token"),
        json={"stuck_job_threshold": 5},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["stuck_job_threshold"] == 5