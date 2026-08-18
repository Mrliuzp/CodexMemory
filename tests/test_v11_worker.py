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


def test_append_to_candidate_pipeline_initializes_missing_flags_as_observable_failure() -> None:
    from codex_memory.auth import Principal
    from codex_memory.db_models import (
        MemoryCandidateRow,
        OutboxEventRow,
        ProcessingJobRow,
        ProjectFeatureFlagRow,
        SecurityAuditRow,
    )
    from codex_memory.v1_service import V1MemoryService
    from codex_memory.worker import run_v11_once

    factory = _factory_with_outbox()
    result = V1MemoryService(factory).append_message_v11(
        Principal(project_key="erp", permissions=frozenset({"append"})),
        "erp",
        "s1",
        "s1:t1:user",
        "user",
        "Use OrderService.",
    )
    assert result.status == "accepted"

    processed = run_v11_once(factory, "worker-a")
    assert processed == {"dispatched": 1, "claimed": 1, "completed": 0, "retry_wait": 0, "dead": 1}

    with factory() as session:
        flags = session.scalar(select(ProjectFeatureFlagRow))
        job = session.scalar(select(ProcessingJobRow))
        event = session.scalar(select(OutboxEventRow))
        audit = session.scalar(
            select(SecurityAuditRow).where(SecurityAuditRow.event_type == "feature_flags_auto_initialized")
        )
        assert flags is not None
        assert flags.memory_v11_enabled is False
        assert job is not None and job.status == "dead"
        assert job.last_error_message is not None and "自动补齐" in job.last_error_message
        assert event is not None and event.status == "dead"
        assert audit is not None
        assert session.scalar(select(MemoryCandidateRow)) is None


def test_append_outbox_worker_produces_candidate_after_explicit_pipeline_enablement() -> None:
    from codex_memory.auth import Principal
    from codex_memory.db_models import MemoryCandidateRow, ProjectFeatureFlagRow, ProjectRow, OutboxEventRow, ProcessingJobRow
    from codex_memory.v1_service import V1MemoryService
    from codex_memory.v11_flags import ProjectPolicyService
    from codex_memory.worker import run_v11_once

    factory = _factory_with_outbox()
    with factory() as session:
        project = session.scalar(select(ProjectRow).where(ProjectRow.project_key == "erp"))
        project_id = project.id

    ProjectPolicyService(factory).update_flags(
        project_id,
        memory_v11_enabled=True,
        server_outbox_enabled=True,
    )
    result = V1MemoryService(factory).append_message_v11(
        Principal(project_key="erp", permissions=frozenset({"append"})),
        "erp",
        "s1",
        "s1:t2:user",
        "user",
        "Use OrderService for order updates.",
    )
    assert result.status == "accepted"

    first = run_v11_once(factory, "worker-a")
    second = run_v11_once(factory, "worker-b")
    assert first == {"dispatched": 1, "claimed": 1, "completed": 1, "retry_wait": 0, "dead": 0}
    assert second == {"dispatched": 0, "claimed": 0, "completed": 0, "retry_wait": 0, "dead": 0}

    with factory() as session:
        flags = session.get(ProjectFeatureFlagRow, project_id)
        candidate = session.scalar(select(MemoryCandidateRow))
        event = session.scalar(select(OutboxEventRow))
        job = session.scalar(select(ProcessingJobRow))
        assert flags is not None and flags.memory_v11_enabled is True
        assert candidate is not None
        assert candidate.source_message_id == result.message_id
        assert candidate.status == "generated"
        assert event is not None and event.status == "completed"
        assert job is not None and job.status == "succeeded"

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

def test_worker_lease_check_accepts_postgresql_aware_timestamps() -> None:
    from datetime import datetime, timedelta, timezone
    from types import SimpleNamespace

    from codex_memory.v11_worker import V11JobWorker

    job = SimpleNamespace(
        status="running",
        locked_by="worker-a",
        lease_expires_at=datetime.now(timezone.utc) + timedelta(seconds=30),
    )

    assert V11JobWorker._owns_live_job(job, "worker-a", datetime.now(timezone.utc).replace(tzinfo=None)) is True
