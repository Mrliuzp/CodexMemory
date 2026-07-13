from __future__ import annotations

import hashlib

from fastapi.testclient import TestClient
from sqlalchemy import select


def _client_and_factory():
    from codex_memory.db import create_schema, create_session_factory, create_sqlite_engine
    from codex_memory.db_models import ApiKeyRow, ProjectRow, V11Base

    engine = create_sqlite_engine()
    create_schema(engine)
    V11Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    with factory() as session:
        project = ProjectRow(project_key="erp", name="ERP")
        session.add(project)
        session.flush()
        session.add(ApiKeyRow(
            project_id=project.id,
            token_hash=hashlib.sha256(b"secret").hexdigest(),
            permissions=["append", "read", "memory_write", "reflect"],
        ))
        session.commit()
    return TestClient(__import__("codex_memory.http_api", fromlist=["create_v1_app"]).create_v1_app(factory)), factory


def test_append_returns_accepted_and_writes_l0_and_outbox() -> None:
    from codex_memory.db_models import MessageRow, OutboxEventRow

    client, factory = _client_and_factory()
    response = client.post(
        "/api/v1/append",
        headers={"Authorization": "Bearer secret"},
        json={
            "project_key": "erp",
            "session_key": "s1",
            "event_key": "s1:t1:user",
            "role": "user",
            "content": "change order",
            "occurred_at": "2026-07-12T10:20:30Z",
        },
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["status"] == "accepted"
    assert payload["message_id"] > 0
    assert payload["event_id"] > 0

    with factory() as session:
        message = session.scalar(select(MessageRow).where(MessageRow.id == payload["message_id"]))
        assert message is not None
        assert message.occurred_at is not None
        event = session.scalar(select(OutboxEventRow).where(OutboxEventRow.id == payload["event_id"]))
        assert event is not None
        assert event.event_type == "message.appended.v1"
        assert event.status == "pending"


def test_append_same_event_and_hash_is_duplicate() -> None:
    client, _ = _client_and_factory()
    headers = {"Authorization": "Bearer secret"}
    payload = {
        "project_key": "erp",
        "session_key": "s1",
        "event_key": "s1:t1:user",
        "role": "user",
        "content": "change order",
    }

    first = client.post("/api/v1/append", headers=headers, json=payload)
    second = client.post("/api/v1/append", headers=headers, json=payload)

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json()["status"] == "duplicate"
    assert second.json()["message_id"] == first.json()["message_id"]


def test_append_same_event_key_is_scoped_to_project() -> None:
    from codex_memory.auth import Principal
    from codex_memory.db_models import ProjectRow
    from codex_memory.v1_service import V1MemoryService

    _, factory = _client_and_factory()
    with factory() as session:
        session.add(ProjectRow(project_key="mall", name="Mall"))
        session.commit()

    service = V1MemoryService(factory)
    erp = Principal(project_key="erp", permissions=frozenset({"append"}))
    mall = Principal(project_key="mall", permissions=frozenset({"append"}))

    first = service.append_message_v11(erp, "erp", "s1", "same-event", "user", "erp content")
    second = service.append_message_v11(mall, "mall", "s1", "same-event", "user", "mall content")

    assert first.status == "accepted"
    assert second.status == "accepted"
    assert second.message_id != first.message_id

def test_append_same_event_with_different_hash_is_conflict() -> None:
    client, _ = _client_and_factory()
    headers = {"Authorization": "Bearer secret"}
    base = {
        "project_key": "erp",
        "session_key": "s1",
        "event_key": "s1:t1:user",
        "role": "user",
        "content": "change order",
    }

    assert client.post("/api/v1/append", headers=headers, json=base).status_code == 201
    conflict = client.post(
        "/api/v1/append",
        headers=headers,
        json={**base, "content": "delete order"},
    )

    assert conflict.status_code == 409
    assert conflict.json()["error"] == "event_key_conflict"
    assert conflict.json()["audit_id"] > 0
