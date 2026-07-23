from __future__ import annotations


def test_worker_run_once_reflects_active_projects() -> None:
    from codex_memory.auth import Principal
    from codex_memory.db import create_schema, create_session_factory, create_postgres_test_engine
    from codex_memory.db_models import ProjectRow
    from codex_memory.v1_service import V1MemoryService
    from codex_memory.worker import run_once

    engine = create_postgres_test_engine()
    create_schema(engine)
    factory = create_session_factory(engine)
    with factory() as session:
        session.add(ProjectRow(project_key="erp", name="ERP", status="active"))
        session.add(ProjectRow(project_key="archived", name="Archived", status="inactive"))
        session.commit()

    reports = run_once(factory)

    assert reports == {"erp": {"processed_messages": 0, "l1_created": 0, "l2_created": 0, "l3_created": 0}}


def test_worker_schedule_waits_for_next_daily_run() -> None:
    from datetime import datetime

    from codex_memory.worker import seconds_until_schedule

    before = datetime(2026, 7, 12, 1, 59, 30)
    after = datetime(2026, 7, 12, 3, 0, 0)

    assert seconds_until_schedule("02:00", before) == 30
    assert seconds_until_schedule("02:00", after) == 23 * 60 * 60


def test_worker_iteration_always_processes_v11_outbox(monkeypatch) -> None:
    from codex_memory import worker

    monkeypatch.setattr(worker, "run_v11_once", lambda _factory, worker_id: {"dispatched": 2, "completed": 2, "worker_id": worker_id})
    monkeypatch.setattr(worker, "run_once", lambda _factory: {"erp": {"processed_messages": 1}})

    without_reflection = worker.run_worker_iteration(object(), worker_id="poller", include_reflection=False)
    with_reflection = worker.run_worker_iteration(object(), worker_id="poller", include_reflection=True)

    assert without_reflection == {"v11": {"dispatched": 2, "completed": 2, "worker_id": "poller"}}
    assert with_reflection["v11"]["dispatched"] == 2
    assert with_reflection["reflection"] == {"erp": {"processed_messages": 1}}