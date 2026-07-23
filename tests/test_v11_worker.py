from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select


def _factory_with_outbox():
    from codex_memory.db import create_schema, create_session_factory, create_postgres_test_engine
    from codex_memory.db_models import ProjectRow, V11Base

    engine = create_postgres_test_engine()
    create_schema(engine)
    V11Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    with factory() as session:
        session.add(ProjectRow(project_key="erp", name="ERP"))
        session.commit()
    return factory


def test_dispatcher_creates_one_idempotent_job_per_outbox_event() -> None:
    from codex_memory.db_models import OutboxEventRow, ProcessingJobRow, ProjectRow
    from codex_memory.v11_worker import OutboxDispatcher

    factory = _factory_with_outbox()
    with factory() as session:
        project = session.scalar(select(ProjectRow).where(ProjectRow.project_key == "erp"))
        session.add(
            OutboxEventRow(
                project_id=project.id,
                aggregate_type="message",
                aggregate_id=9,
                event_type="message.appended.v1",
                payload_version="v1",
                payload={"message_id": 9},
            )
        )
        session.commit()

    dispatcher = OutboxDispatcher(factory)
    assert dispatcher.dispatch_once("dispatcher-a") == 1
    assert dispatcher.dispatch_once("dispatcher-b") == 0

    with factory() as session:
        event = session.scalar(select(OutboxEventRow))
        jobs = session.scalars(select(ProcessingJobRow)).all()
        assert event.status == "dispatched"
        assert len(jobs) == 1
        assert jobs[0].job_key == "outbox:1:message.appended.v1:9:v1"
        assert jobs[0].status == "pending"


def test_job_lease_heartbeat_completion_and_expiry_recovery() -> None:
    from codex_memory.db_models import OutboxEventRow, ProjectRow
    from codex_memory.v11_worker import OutboxDispatcher, V11JobWorker

    factory = _factory_with_outbox()
    with factory() as session:
        project = session.scalar(select(ProjectRow).where(ProjectRow.project_key == "erp"))
        session.add(
            OutboxEventRow(
                project_id=project.id,
                aggregate_type="message",
                aggregate_id=10,
                event_type="message.appended.v1",
                payload_version="v1",
                payload={"message_id": 10},
            )
        )
        session.commit()

    OutboxDispatcher(factory).dispatch_once("dispatcher")
    worker = V11JobWorker(factory, lease_seconds=30)
    claims = worker.claim_jobs("worker-a")
    assert len(claims) == 1
    assert worker.heartbeat(claims[0].job_id, "worker-a") is True
    assert worker.complete(claims[0].job_id, "worker-a") is True
    assert worker.complete(claims[0].job_id, "worker-a") is False

    with factory() as session:
        job = claims[0].job_id
        row = session.get(__import__("codex_memory.db_models", fromlist=["ProcessingJobRow"]).ProcessingJobRow, job)
        row.status = "running"
        row.locked_by = "crashed-worker"
        row.lease_expires_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=1)
        session.commit()

    assert worker.sweep_expired() == 1
    recovered = worker.claim_jobs("worker-b")
    assert len(recovered) == 1
    assert recovered[0].job_id == claims[0].job_id


def test_v11_worker_processes_message_append_into_idempotent_candidate() -> None:
    import hashlib

    from codex_memory.db_models import (
        MemoryCandidateRow,
        MessageRow,
        OutboxEventRow,
        ProjectFeatureFlagRow,
        ProjectRow,
        SessionRow,
    )
    from codex_memory.worker import run_v11_once

    factory = _factory_with_outbox()
    with factory() as session:
        project = session.scalar(select(ProjectRow).where(ProjectRow.project_key == "erp"))
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
                payload={"project_id": project.id, "message_id": message.id, "project_key": "erp"},
            )
        )
        session.commit()

    first = run_v11_once(factory, "worker-a")
    second = run_v11_once(factory, "worker-b")
    assert first == {"dispatched": 1, "claimed": 1, "completed": 1, "retry_wait": 0, "dead": 0}
    assert second == {"dispatched": 0, "claimed": 0, "completed": 0, "retry_wait": 0, "dead": 0}
    with factory() as session:
        assert len(session.scalars(select(MemoryCandidateRow)).all()) == 1

def test_retryable_failure_backoffs_and_dead_jobs_stop_claiming() -> None:
    from codex_memory.db_models import OutboxEventRow, ProcessingJobRow, ProjectRow
    from codex_memory.v11_worker import OutboxDispatcher, V11JobWorker

    factory = _factory_with_outbox()
    with factory() as session:
        project = session.scalar(select(ProjectRow).where(ProjectRow.project_key == "erp"))
        event = OutboxEventRow(
            project_id=project.id,
            aggregate_type="message",
            aggregate_id=11,
            event_type="message.appended.v1",
            payload_version="v1",
            payload={"message_id": 11},
        )
        session.add(event)
        session.commit()

    OutboxDispatcher(factory).dispatch_once("dispatcher")
    worker = V11JobWorker(factory, max_backoff_seconds=60)
    claim = worker.claim_jobs("worker-a")[0]
    assert worker.fail(claim.job_id, "worker-a", "timeout", "remote timeout", retryable=True) == "retry_wait"
    retry = worker.claim_jobs("worker-b")
    assert retry == []

    with factory() as session:
        row = session.get(ProcessingJobRow, claim.job_id)
        row.next_attempt_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=1)
        row.attempt_count = row.max_attempts
        session.commit()

    claim = worker.claim_jobs("worker-c")[0]
    assert worker.fail(claim.job_id, "worker-c", "timeout", "remote timeout", retryable=True) == "dead"
    assert worker.claim_jobs("worker-d") == []