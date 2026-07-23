from __future__ import annotations

import hashlib

from fastapi.testclient import TestClient
from sqlalchemy import select


def _client():
    from codex_memory.db import create_schema, create_session_factory, create_postgres_test_engine
    from codex_memory.db_models import ApiKeyRow, OutboxEventRow, ProcessingJobRow, ProjectRow, V11Base
    from codex_memory.http_api import create_v1_app

    engine = create_postgres_test_engine()
    create_schema(engine)
    V11Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    with factory() as session:
        project = ProjectRow(project_key="demo", name="Demo")
        session.add(project)
        session.flush()
        session.add(ApiKeyRow(project_id=project.id, token_hash=hashlib.sha256(b"admin").hexdigest(), permissions=["admin"]))
        event = OutboxEventRow(
            project_id=project.id,
            aggregate_type="message",
            aggregate_id=1,
            event_type="message.appended.v1",
            payload_version="v1",
            idempotency_key="demo.message.appended.message.1.v1",
            payload={"project_id": project.id, "message_id": 1, "project_key": "demo"},
            status="dead",
        )
        session.add(event)
        session.flush()
        session.add(
            ProcessingJobRow(
                project_id=project.id,
                outbox_event_id=event.id,
                job_type="extract_memory_candidate",
                aggregate_type="message",
                aggregate_id=1,
                job_key="demo.extract_memory_candidate.message.1.v1",
                idempotency_key="demo.extract_memory_candidate.message.1.v1",
                payload_version="v1",
                payload=event.payload,
                status="dead",
            )
        )
        session.commit()
    return TestClient(create_v1_app(factory)), factory


def test_admin_can_list_and_replay_outbox_job() -> None:
    client, factory = _client()
    headers = {"Authorization": "Bearer admin"}

    outbox = client.get("/api/v1/admin/outbox", headers=headers, params={"project_key": "demo"})
    replay = client.post("/api/v1/admin/jobs/1/replay", headers=headers, json={"reason": "恢复异步链路"})

    assert outbox.status_code == 200
    assert outbox.json()["outbox"][0]["status"] == "dead"
    assert replay.status_code == 200
    assert replay.json()["status"] == "pending"
    with factory() as session:
        event = session.scalar(select(__import__("codex_memory.db_models", fromlist=["OutboxEventRow"]).OutboxEventRow))
        assert event.status == "pending"
        assert event.replay_count == 1


def test_admin_cancel_requires_reason_and_updates_job() -> None:
    from codex_memory.db_models import ProcessingJobRow

    client, factory = _client()
    headers = {"Authorization": "Bearer admin"}
    with factory() as session:
        job = session.scalar(select(ProcessingJobRow))
        job.status = "pending"
        session.commit()

    missing = client.post("/api/v1/admin/jobs/1/cancel", headers=headers, json={})
    cancelled = client.post("/api/v1/admin/jobs/1/cancel", headers=headers, json={"reason": "人工停止"})

    assert missing.status_code == 422
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"


def test_admin_imports_reference_layer_without_publishing_memory() -> None:
    client, factory = _client()
    headers = {"Authorization": "Bearer admin"}
    imported = client.post(
        "/api/v1/admin/import",
        headers=headers,
        json={"project_key": "demo", "items": [{"source_name": "guide.md", "content": "# 发布\n\n发布前审核。"}]},
    )
    assert imported.status_code == 200
    assert imported.json()["status"] == "completed"
    assert imported.json()["candidates"] == 1

    candidates = client.get("/api/v1/admin/reference-candidates", headers=headers, params={"project_key": "demo"})
    assert candidates.status_code == 200
    assert candidates.json()["candidates"][0]["status"] == "pending_review"

    search = client.get("/api/v1/reference/search", headers=headers, params={"project_key": "demo", "query": "审核"})
    assert search.status_code == 200
    assert search.json()["results"]
    with factory() as session:
        from codex_memory.db_models import MemoryRow

        assert session.scalar(select(MemoryRow)) is None
