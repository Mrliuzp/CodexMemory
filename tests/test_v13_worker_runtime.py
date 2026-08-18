from __future__ import annotations

import hashlib

from sqlalchemy import select


def _factory():
    from codex_memory.db import create_schema, create_session_factory, create_postgres_test_engine
    from codex_memory.db_models import V11Base, V16Base

    engine = create_postgres_test_engine()
    create_schema(engine)
    V11Base.metadata.create_all(engine)
    V16Base.metadata.create_all(engine)
    return create_session_factory(engine)


def test_v13_once_consumes_new_action_job_and_completes_outbox() -> None:
    from codex_memory.db_models import (
        MemoryCandidateRow,
        MessageRow,
        OutboxEventRow,
        ProcessingJobRow,
        ProjectFeatureFlagRow,
        ProjectRow,
        SessionRow,
    )
    from codex_memory.worker import run_v13_once

    factory = _factory()
    with factory() as session:
        project = ProjectRow(project_key="demo", name="Demo")
        session.add(project)
        session.flush()
        session.add(ProjectFeatureFlagRow(project_id=project.id, memory_v11_enabled=True))
        conversation = SessionRow(project_id=project.id, session_key="s1")
        session.add(conversation)
        session.flush()
        message = MessageRow(
            project_id=project.id,
            session_id=conversation.id,
            event_key="s1:t1:user",
            role="user",
            content="Use OrderService for order updates.",
            content_hash=hashlib.sha256(b"Use OrderService for order updates.").hexdigest(),
        )
        session.add(message)
        session.flush()
        session.add(
            OutboxEventRow(
                project_id=project.id,
                aggregate_type="message",
                aggregate_id=message.id,
                event_type="message.appended.v1",
                payload_version="v1",
                idempotency_key="demo.message.appended.message.s1:t1:user.v1",
                payload={"project_id": project.id, "message_id": message.id, "project_key": "demo"},
            )
        )
        session.commit()

    first = run_v13_once(factory, "worker-v13")
    second = run_v13_once(factory, "worker-v13")

    assert first["dispatched"] == 1
    assert first["completed"] == 1
    assert second["dispatched"] == 1
    assert second["claimed"] == 1
    assert second["completed"] == 0
    assert second["dead"] == 1
    with factory() as session:
        events = session.scalars(select(OutboxEventRow).order_by(OutboxEventRow.id)).all()
        jobs = session.scalars(select(ProcessingJobRow).order_by(ProcessingJobRow.id)).all()
        assert events[0].status == "completed"
        assert events[1].status == "dead"
        assert jobs[0].job_type == "extract_memory_candidate"
        assert jobs[0].idempotency_key is not None
        assert jobs[1].job_type == "decide_candidate"
        assert jobs[1].last_error_code == "decision_engine_disabled"
        assert session.scalar(select(MemoryCandidateRow)).status == "needs_review"
        assert len(session.scalars(select(MemoryCandidateRow)).all()) == 1


def test_worker_runtime_publishes_heartbeat() -> None:
    from codex_memory.db_models import WorkerInstanceRow
    from codex_memory.v13_worker import WorkerRuntime

    factory = _factory()
    runtime = WorkerRuntime(factory, worker_id="heartbeat-worker")
    cycle = runtime.run_once()

    assert cycle.recovered == 0
    with factory() as session:
        worker = session.get(WorkerInstanceRow, "heartbeat-worker")
        assert worker is not None
        assert worker.status == "healthy"


def test_v13_end_to_end_append_creates_candidate_without_manual_dispatch() -> None:
    from codex_memory.auth import Principal
    from codex_memory.db_models import MemoryCandidateRow, ProjectFeatureFlagRow, ProjectRow
    from codex_memory.v1_service import V1MemoryService
    from codex_memory.worker import run_v13_once

    factory = _factory()
    with factory() as session:
        project = ProjectRow(project_key="demo", name="Demo")
        session.add(project)
        session.flush()
        session.add(ProjectFeatureFlagRow(project_id=project.id, memory_v11_enabled=True))
        session.commit()

    service = V1MemoryService(factory)
    result = service.append_message_v11(
        Principal(project_key="demo", permissions=frozenset({"append"})),
        "demo",
        "s1",
        "s1:t1:user",
        "user",
        "Use OrderService for updates.",
    )
    assert result.status == "accepted"
    cycle = run_v13_once(factory, "worker-v13")
    assert cycle["dispatched"] == 1
    assert cycle["completed"] == 1
    with factory() as session:
        assert len(session.scalars(select(MemoryCandidateRow)).all()) == 1
