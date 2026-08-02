from __future__ import annotations

import hashlib

from fastapi.testclient import TestClient


def _client() -> TestClient:
    from codex_memory.db import create_schema, create_session_factory, create_postgres_test_engine
    from codex_memory.db_models import ApiKeyRow, ProjectRow
    from codex_memory.http_api import create_v1_app

    engine = create_postgres_test_engine()
    create_schema(engine)
    session_factory = create_session_factory(engine)
    with session_factory() as session:
        project = ProjectRow(project_key="erp", name="ERP")
        session.add(project)
        session.flush()
        session.add(
            ApiKeyRow(
                project_id=project.id,
                token_hash=hashlib.sha256(b"secret").hexdigest(),
                permissions=["append", "read", "memory_write", "reflect"],
            )
        )
        session.commit()
    return TestClient(create_v1_app(session_factory))


def test_v1_openapi_operation_ids_are_unique() -> None:
    document = _client().app.openapi()
    methods = {"get", "post", "put", "patch", "delete", "options", "head", "trace"}
    operation_ids = [
        operation["operationId"]
        for path_item in document["paths"].values()
        for method, operation in path_item.items()
        if method in methods
    ]

    assert len(operation_ids) == len(set(operation_ids))
    assert "complete_import_upload_colon_alias" in operation_ids
    assert "start_import_batch_colon_alias" in operation_ids
    assert "retry_import_batch_colon_alias" in operation_ids
    assert "cancel_import_batch_colon_alias" in operation_ids


def test_v1_append_requires_bearer_token() -> None:
    response = _client().post(
        "/api/v1/append",
        json={"project_key": "erp", "session_key": "s1", "event_key": "s1:t1:user", "role": "user", "content": "change order"},
    )

    assert response.status_code == 401


def test_v1_append_is_idempotent() -> None:
    client = _client()
    headers = {"Authorization": "Bearer secret"}
    payload = {"project_key": "erp", "session_key": "s1", "event_key": "s1:t1:user", "role": "user", "content": "change order"}

    first = client.post("/api/v1/append", headers=headers, json=payload)
    second = client.post("/api/v1/append", headers=headers, json=payload)

    assert first.status_code == 200
    assert second.json() == {"id": first.json()["id"], "status": "duplicate"}


def test_v1_memory_rejects_direct_l2_write() -> None:
    response = _client().post(
        "/api/v1/memory",
        headers={"Authorization": "Bearer secret"},
        json={"project_key": "erp", "level": "L2", "type": "coding_rule", "content": {"text": "use service"}},
    )

    assert response.status_code == 422


def test_v1_memory_creates_l1_memory() -> None:
    response = _client().post(
        "/api/v1/memory",
        headers={"Authorization": "Bearer secret"},
        json={"project_key": "erp", "type": "project_overview", "title": "项目概览", "content": {"text": "使用 MCP 写入项目知识"}},
    )

    assert response.status_code == 200
    assert response.json()["level"] == "L1"


def test_v1_append_rejects_event_key_with_different_content() -> None:
    client = _client()
    headers = {"Authorization": "Bearer secret"}
    first = client.post(
        "/api/v1/append",
        headers=headers,
        json={"project_key": "erp", "session_key": "s1", "event_key": "e1", "role": "user", "content": "original"},
    )
    conflict = client.post(
        "/api/v1/append",
        headers=headers,
        json={"project_key": "erp", "session_key": "s1", "event_key": "e1", "role": "user", "content": "changed"},
    )

    assert first.status_code == 200
    assert conflict.status_code == 409
    assert conflict.json()["error"] == "event_key_conflict"
