from __future__ import annotations


def _worker_service():
    from codex_memory.auth import Principal
    from codex_memory.db import create_schema, create_session_factory, create_sqlite_engine
    from codex_memory.db_models import ProjectRow
    from codex_memory.v1_service import V1MemoryService

    engine = create_sqlite_engine()
    create_schema(engine)
    factory = create_session_factory(engine)
    with factory() as session:
        session.add(ProjectRow(project_key="erp", name="ERP"))
        session.commit()
    return V1MemoryService(factory), Principal(project_key="erp", permissions=frozenset({"append", "read", "memory_write", "reflect"}))


def test_reflection_creates_l3_for_bug_and_l1_for_fix() -> None:
    service, principal = _worker_service()
    service.append_message(principal, "erp", "s1", "s1:t1:user", "user", "Bug: direct order mutation fails. Fix: use OrderService.")

    report = service.reflect_project(principal, "erp")
    context = service.build_context(principal, "erp", "change order")

    assert report["l3_created"] == 1
    assert report["l1_created"] == 1
    assert context["critical_rules"]
    assert context["recent_insights"]


def test_reflection_promotes_repeated_solution_from_distinct_sessions() -> None:
    service, principal = _worker_service()
    service.append_message(principal, "erp", "s1", "s1:t1:assistant", "assistant", "Fix: use OrderService for order updates.")
    service.append_message(principal, "erp", "s2", "s2:t1:assistant", "assistant", "Fix: use OrderService for order updates.")

    report = service.reflect_project(principal, "erp")
    context = service.build_context(principal, "erp", "change order")

    assert report["l2_created"] == 1
    assert context["long_term_rules"]
