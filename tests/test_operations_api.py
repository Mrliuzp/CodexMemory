from __future__ import annotations

import hashlib

from fastapi.testclient import TestClient


def _app() -> TestClient:
    from codex_memory.db import create_postgres_test_engine, create_schema, create_session_factory
    from codex_memory.db_models import ApiKeyRow, ProjectRow
    from codex_memory.http_api import create_v1_app
    from codex_memory.v11_models import V11Base

    engine = create_postgres_test_engine()
    create_schema(engine)
    V11Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    with factory() as session:
        project_a = ProjectRow(project_key="project-a", name="Project A")
        project_b = ProjectRow(project_key="project-b", name="Project B")
        session.add_all([project_a, project_b])
        session.flush()
        session.add_all(
            [
                ApiKeyRow(
                    project_id=project_a.id,
                    token_hash=hashlib.sha256(b"project-a-reader").hexdigest(),
                    permissions=["read"],
                ),
                ApiKeyRow(
                    project_id=project_a.id,
                    token_hash=hashlib.sha256(b"project-a-admin").hexdigest(),
                    permissions=["read", "admin"],
                ),
                ApiKeyRow(
                    project_id=project_a.id,
                    token_hash=hashlib.sha256(b"project-a-operations").hexdigest(),
                    permissions=["read", "operations_read"],
                ),
            ]
        )
        session.commit()
    return TestClient(create_v1_app(factory))


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_system_status_requires_admin_permission() -> None:
    client = _app()

    response = client.get("/api/admin/v1/system/status", headers=_auth("project-a-reader"))

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "permission_denied"


def test_project_detail_is_limited_to_granted_project() -> None:
    client = _app()

    own = client.get("/api/admin/v1/projects/project-a", headers=_auth("project-a-reader"))
    other = client.get("/api/admin/v1/projects/project-b", headers=_auth("project-a-reader"))

    assert own.status_code == 200
    assert own.json()["data"]["project_key"] == "project-a"
    assert other.status_code == 403
    assert other.json()["error"]["code"] == "project_access_denied"


def test_system_status_redacts_sensitive_values() -> None:
    client = _app()

    response = client.get("/api/admin/v1/system/status", headers=_auth("project-a-admin"))

    assert response.status_code == 200
    assert response.json()["request_id"]
    assert "project-a-admin" not in response.text
    assert "postgresql+psycopg" not in response.text

def test_system_status_allows_only_the_dedicated_operations_permission() -> None:
    client = _app()

    response = client.get("/api/admin/v1/system/status", headers=_auth("project-a-operations"))

    assert response.status_code == 200
    assert response.json()["data"]["migration_schema"] == "pending"
