from __future__ import annotations

import hashlib

from fastapi.testclient import TestClient


def test_reflect_endpoint_runs_project_reflection() -> None:
    from codex_memory.db import create_schema, create_session_factory, create_postgres_test_engine
    from codex_memory.db_models import ApiKeyRow, ProjectRow
    from codex_memory.http_api import create_v1_app
    from codex_memory.v1_service import V1MemoryService

    engine = create_postgres_test_engine()
    create_schema(engine)
    factory = create_session_factory(engine)
    with factory() as session:
        project = ProjectRow(project_key="erp", name="ERP")
        session.add(project)
        session.flush()
        session.add(ApiKeyRow(project_id=project.id, token_hash=hashlib.sha256(b"secret").hexdigest(), permissions=["append", "reflect", "read"]))
        session.commit()
    service = V1MemoryService(factory)
    from codex_memory.auth import Principal
    service.append_message(Principal("erp", frozenset({"append"})), "erp", "s1", "s1:t1:user", "user", "Bug: direct status mutation fails.")

    response = TestClient(create_v1_app(factory)).post(
        "/api/v1/reflect",
        headers={"Authorization": "Bearer secret"},
        json={"project_key": "erp"},
    )

    assert response.status_code == 200
    assert response.json()["l3_created"] == 1
