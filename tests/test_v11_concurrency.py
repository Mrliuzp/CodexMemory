from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker


def _postgres_factory():
    from codex_memory.db import create_postgres_test_engine, create_schema, create_session_factory
    from codex_memory.db_models import V11Base

    engine = create_postgres_test_engine()
    create_schema(engine)
    V11Base.metadata.create_all(engine)
    return create_session_factory(engine), None

def _seed_erp(factory):
    from codex_memory.db_models import ProjectRow, SessionRow
    with factory() as session:
        project = ProjectRow(project_key="erp", name="ERP")
        session.add(project)
        session.flush()
        session.add(SessionRow(project_id=project.id, session_key="s1"))
        session.commit()


def test_concurrent_append_with_same_event_key_is_idempotent():
    from codex_memory.auth import Principal
    from codex_memory.db_models import MessageRow
    from codex_memory.v1_service import AppendConflictError, V1MemoryService

    factory, _ = _postgres_factory()
    _seed_erp(factory)
    service = V1MemoryService(factory)
    principal = Principal(project_key="erp", permissions=frozenset({"append"}))

    count = 0
    lock_msg = __import__("threading").Lock()

    def try_append(_: int):
        nonlocal count
        try:
            service.append_message_v11(principal, "erp", "s1", "concurrent:1:user", "user", "content")
            with lock_msg:
                count += 1
        except AppendConflictError:
            pass
        except Exception as exc:
            import logging
            logging.warning("concurrent append error: %s", exc)

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(try_append, range(20)))

    with factory() as session:
        messages = session.scalars(select(MessageRow).where(MessageRow.event_key == "concurrent:1:user")).all()
    assert len(messages) == 1, "exactly one message should survive concurrent append"
    assert count >= 1, "at least one caller should succeed"

def test_concurrent_outbox_dispatch_has_no_duplicate_jobs():
    from codex_memory.db_models import OutboxEventRow, ProcessingJobRow, ProjectRow
    from codex_memory.v11_worker import OutboxDispatcher

    factory, _ = _wal_factory()
    with factory() as session:
        project = ProjectRow(project_key="erp", name="ERP")
        session.add(project)
        session.flush()
        session.add_all([
            OutboxEventRow(
                project_id=project.id,
                aggregate_type="message",
                aggregate_id=i,
                event_type="message.appended.v1",
                payload_version="v1",
                payload={"i": i},
            )
            for i in range(1, 21)
        ])
        session.commit()

    def dispatch(worker_id: str) -> int:
        return OutboxDispatcher(factory).dispatch_once(worker_id, limit=5)

    with ThreadPoolExecutor(max_workers=4) as pool:
        dispatched = list(pool.map(dispatch, [f"dispatcher-{i}" for i in range(4)]))

    assert sum(dispatched) == 20
    with factory() as session:
        jobs = session.scalars(select(ProcessingJobRow)).all()
        events = session.scalars(select(OutboxEventRow)).all()
    assert len(jobs) == 20
    assert {event.status for event in events} == {"dispatched"}


def test_concurrent_job_claim_has_no_duplicate_running():
    from codex_memory.db_models import OutboxEventRow, ProcessingJobRow, ProjectRow
    from codex_memory.v11_worker import OutboxDispatcher, V11JobWorker

    factory, _ = _postgres_factory()
    with factory() as session:
        project = ProjectRow(project_key="erp", name="ERP")
        session.add(project)
        session.flush()
        session.add_all([
            OutboxEventRow(
                project_id=project.id, aggregate_type="message",
                aggregate_id=i, event_type="message.appended.v1",
                payload_version="v1", payload={"i": i},
            )
            for i in range(1, 21)
        ])
        session.commit()

    OutboxDispatcher(factory).dispatch_once("dispatcher", limit=50)

    lock_job = __import__("threading").Lock()
    running_ids: set[int] = set()

    def claim_work(worker_id: str):
        worker = V11JobWorker(factory, lease_seconds=30)
        claims = worker.claim_jobs(worker_id, limit=5)
        with lock_job:
            for c in claims:
                assert c.job_id not in running_ids, "duplicate claim detected"
                running_ids.add(c.job_id)
        return {"worker": worker_id, "claimed": len(claims)}

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(claim_work, f"worker-{i}") for i in range(4)]
        for future in as_completed(futures):
            future.result()

    assert len(running_ids) == 20, "all 20 jobs should be claimed exactly once"


def test_expired_lease_swept_and_recovered():
    from codex_memory.db_models import OutboxEventRow, ProjectRow
    from codex_memory.v11_worker import OutboxDispatcher, V11JobWorker

    factory, _ = _postgres_factory()
    with factory() as session:
        project = ProjectRow(project_key="erp", name="ERP")
        session.add(project)
        session.flush()
        session.add(
            OutboxEventRow(
                project_id=project.id, aggregate_type="message",
                aggregate_id=99, event_type="message.appended.v1",
                payload_version="v1", payload={"message_id": 99},
            )
        )
        session.commit()

    OutboxDispatcher(factory).dispatch_once("dispatcher")
    worker_a = V11JobWorker(factory, lease_seconds=0)
    claims = worker_a.claim_jobs("worker-a")
    assert len(claims) == 1

    import time; time.sleep(0.02)
    assert worker_a.heartbeat(claims[0].job_id, "worker-a") is False
    assert worker_a.sweep_expired() == 1

    worker_b = V11JobWorker(factory)
    recovered = worker_b.claim_jobs("worker-b")
    assert len(recovered) == 1
    assert worker_b.complete(recovered[0].job_id, "worker-b") is True


def test_cross_project_event_key_isolation():
    from codex_memory.auth import Principal
    from codex_memory.db_models import MessageRow, ProjectRow, SessionRow
    from codex_memory.v1_service import V1MemoryService

    factory, _ = _postgres_factory()
    with factory() as session:
        erp = ProjectRow(project_key="erp", name="ERP")
        mall = ProjectRow(project_key="mall", name="Mall")
        session.add_all([erp, mall])
        session.flush()
        session.add_all([
            SessionRow(project_id=erp.id, session_key="s1"),
            SessionRow(project_id=mall.id, session_key="s1"),
        ])
        session.commit()

    service = V1MemoryService(factory)
    erp_p = Principal(project_key="erp", permissions=frozenset({"append"}))
    mall_p = Principal(project_key="mall", permissions=frozenset({"append"}))
    r1 = service.append_message_v11(erp_p, "erp", "s1", "s1:t1:user", "user", "erp content")
    r2 = service.append_message_v11(mall_p, "mall", "s1", "s1:t1:user", "user", "mall content")
    assert r1.status == "accepted" and r2.status == "accepted"
    assert r1.message_id != r2.message_id
    with factory() as session:
        messages = session.scalars(select(MessageRow).where(MessageRow.event_key == "s1:t1:user")).all()
    assert len(messages) == 2


def test_full_pipeline_append_to_candidate():
    from codex_memory.db_models import MessageRow, OutboxEventRow, ProjectFeatureFlagRow, ProjectRow, SessionRow
    from codex_memory.v11_worker import OutboxDispatcher, V11JobWorker
    from codex_memory.v11_handlers import V11JobHandlers

    factory, _ = _postgres_factory()
    with factory() as session:
        project = ProjectRow(project_key="erp", name="ERP")
        session.add(project)
        session.flush()
        session.add(ProjectFeatureFlagRow(project_id=project.id, memory_v11_enabled=True))
        session.add(SessionRow(project_id=project.id, session_key="s1"))
        session.flush()
        msg = MessageRow(
            project_id=project.id, session_id=1,
            event_key="s1:t1:user", role="user",
            content="Use OrderService.",
            content_hash=hashlib.sha256(b"Use OrderService.").hexdigest(),
        )
        session.add(msg)
        session.flush()
        session.add(
            OutboxEventRow(
                project_id=project.id, aggregate_type="message",
                aggregate_id=msg.id, event_type="message.appended.v1",
                payload_version="v1",
                payload={"project_id": project.id, "message_id": msg.id, "project_key": "erp"},
            )
        )
        session.commit()

    OutboxDispatcher(factory).dispatch_once("dispatcher")
    result = V11JobWorker(factory).process_once("tester", V11JobHandlers(factory).handle)
    assert result == {"claimed": 1, "completed": 1, "retry_wait": 0, "dead": 0}


def test_handler_permanent_failure_goes_dead():
    from codex_memory.db_models import OutboxEventRow, ProcessingJobRow, ProjectRow
    from codex_memory.v11_worker import OutboxDispatcher, V11JobWorker
    from codex_memory.v11_handlers import V11JobHandlers

    factory, _ = _postgres_factory()
    with factory() as session:
        project = ProjectRow(project_key="erp", name="ERP")
        session.add(project)
        session.flush()
        session.add(
            OutboxEventRow(
                project_id=project.id, aggregate_type="unknown",
                aggregate_id=99, event_type="unknown.type.v1",
                payload_version="v1", payload={},
            )
        )
        session.commit()

    OutboxDispatcher(factory).dispatch_once("dispatcher")
    result = V11JobWorker(factory).process_once("tester", V11JobHandlers(factory).handle)
    assert result == {"claimed": 1, "completed": 0, "retry_wait": 0, "dead": 1}

    with factory() as session:
        job = session.scalar(select(ProcessingJobRow))
        assert job.status == "dead"
        assert job.last_error_code == "permanent"