from __future__ import annotations


def _service_with_memories():
    from codex_memory.auth import Principal
    from codex_memory.db import create_schema, create_session_factory, create_postgres_test_engine
    from codex_memory.db_models import MemoryRow, ProjectRow
    from codex_memory.v1_service import V1MemoryService

    engine = create_postgres_test_engine()
    create_schema(engine)
    factory = create_session_factory(engine)
    with factory() as session:
        project = ProjectRow(project_key="erp", name="ERP")
        session.add(project)
        session.flush()
        session.add_all(
            [
                MemoryRow(project_id=project.id, level="L1", memory_type="solution", title="Working order note", content={"text": "use service layer"}, status="active"),
                MemoryRow(project_id=project.id, level="L2", memory_type="coding_rule", title="Order rule", content={"text": "orders require service layer"}, status="active"),
                MemoryRow(project_id=project.id, level="L3", memory_type="bug_solution", title="Direct mutation bug", content={"text": "do not mutate order status directly"}, status="active"),
            ]
        )
        session.commit()
    return V1MemoryService(factory), Principal(project_key="erp", permissions=frozenset({"read"}))


def test_context_groups_memories_by_priority_layer() -> None:
    service, principal = _service_with_memories()

    context = service.build_context(principal, "erp", "change order")

    assert context["critical_rules"][0]["title"] == "Direct mutation bug"
    assert context["long_term_rules"][0]["title"] == "Order rule"
    assert context["recent_insights"][0]["title"] == "Working order note"
    assert len(context["source_ids"]) == 3


def test_search_returns_matching_project_memories_only() -> None:
    service, principal = _service_with_memories()

    results = service.search_memories(principal, "erp", "service")

    assert [result["title"] for result in results] == ["Order rule", "Working order note"]
