from __future__ import annotations


def test_worker_run_once_reflects_active_projects() -> None:
    from codex_memory.auth import Principal
    from codex_memory.db import create_schema, create_session_factory, create_sqlite_engine
    from codex_memory.db_models import ProjectRow
    from codex_memory.v1_service import V1MemoryService
    from codex_memory.worker import run_once

    engine = create_sqlite_engine()
    create_schema(engine)
    factory = create_session_factory(engine)
    with factory() as session:
        session.add(ProjectRow(project_key="erp", name="ERP", status="active"))
        session.add(ProjectRow(project_key="archived", name="Archived", status="inactive"))
        session.commit()

    reports = run_once(factory)

    assert reports == {"erp": {"processed_messages": 0, "l1_created": 0, "l2_created": 0, "l3_created": 0}}
