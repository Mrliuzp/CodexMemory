from __future__ import annotations

import hashlib
from fastapi.testclient import TestClient
from codex_memory.db_models import ProjectRow


def _factory_and_client() -> tuple[object, TestClient]:
    from codex_memory.db import create_schema, create_session_factory, create_sqlite_engine
    from codex_memory.db_models import ApiKeyRow
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


def test_list_sessions() -> None:
    _, client = _factory_and_client()
    resp = client.get("/api/v1/admin/sessions", headers=_auth("admin-token"))
    assert resp.status_code == 200
    data = resp.json()
    assert "sessions" in data
    assert isinstance(data["sessions"], list)


def test_revoke_session() -> None:
    _, client = _factory_and_client()
    resp = client.delete("/api/v1/admin/sessions/test123", headers=_auth("admin-token"))
    assert resp.status_code == 200
    assert resp.json()["revoked"] == "test123"