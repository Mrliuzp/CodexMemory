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


def test_audit_stats() -> None:
    _, client = _factory_and_client()
    resp = client.get("/api/v1/admin/audit/stats", headers=_auth("admin-token"))
    assert resp.status_code == 200
    data = resp.json()
    assert "total" in data
    assert "by_type" in data
    assert isinstance(data["total"], int)
    assert data["days"] == 30


def test_audit_stats_custom_days() -> None:
    _, client = _factory_and_client()
    resp = client.get("/api/v1/admin/audit/stats?days=7", headers=_auth("admin-token"))
    assert resp.status_code == 200
    data = resp.json()
    assert data["days"] == 7


def test_audit_search() -> None:
    _, client = _factory_and_client()
    resp = client.get("/api/v1/admin/audit/search", headers=_auth("admin-token"))
    assert resp.status_code == 200
    data = resp.json()
    assert "audit_logs" in data
    assert "total" in data
    assert isinstance(data["audit_logs"], list)