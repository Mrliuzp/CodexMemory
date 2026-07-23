from __future__ import annotations

import hashlib
import os
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


def test_admin_users_endpoint() -> None:
    _, client = _factory_and_client()
    resp = client.get("/api/v1/admin/users", headers=_auth("admin-token"))
    assert resp.status_code == 200
    data = resp.json()
    assert "users" in data
    assert len(data["users"]) >= 1


def test_login_with_multi_user_support() -> None:
    """admin/main.py 的登录需支持多用户。通过 TestClient 验证基本流程正常。"""
    from admin.main import app, _admin_users
    assert len(_admin_users) >= 1
    assert "admin" in _admin_users
    client = TestClient(app)
    resp = client.post("/api/admin/login", json={"username": "admin", "password": "admin"})
    assert resp.status_code == 200
    assert "access_token" in resp.json()