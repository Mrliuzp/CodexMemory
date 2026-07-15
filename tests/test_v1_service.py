from __future__ import annotations

import pytest


def test_append_returns_existing_message_for_duplicate_event_key() -> None:
    from codex_memory.auth import Principal
    from codex_memory.db import create_schema, create_session_factory, create_sqlite_engine
    from codex_memory.db_models import ProjectRow
    from codex_memory.v1_service import V1MemoryService

    engine = create_sqlite_engine()
    create_schema(engine)
    session_factory = create_session_factory(engine)
    with session_factory() as session:
        session.add(ProjectRow(project_key="erp", name="ERP"))
        session.commit()

    service = V1MemoryService(session_factory)
    principal = Principal(project_key="erp", permissions=frozenset({"append"}))
    first = service.append_message(principal, "erp", "s1", "s1:t1:user", "user", "change order")
    second = service.append_message(principal, "erp", "s1", "s1:t1:user", "user", "change order")

    assert first.status == "stored"
    assert second.status == "duplicate"
    assert second.message_id == first.message_id


def test_append_rejects_another_projects_principal() -> None:
    from codex_memory.auth import Principal, ProjectAccessDenied
    from codex_memory.db import create_schema, create_session_factory, create_sqlite_engine
    from codex_memory.db_models import ProjectRow
    from codex_memory.v1_service import V1MemoryService

    engine = create_sqlite_engine()
    create_schema(engine)
    session_factory = create_session_factory(engine)
    with session_factory() as session:
        session.add(ProjectRow(project_key="erp", name="ERP"))
        session.commit()

    service = V1MemoryService(session_factory)
    principal = Principal(project_key="mall", permissions=frozenset({"append"}))

    with pytest.raises(ProjectAccessDenied):
        service.append_message(principal, "erp", "s1", "s1:t1:user", "user", "change order")


def test_v11_append_rejects_same_event_key_with_different_content() -> None:
    from codex_memory.auth import Principal
    from codex_memory.db import create_schema, create_session_factory, create_sqlite_engine
    from codex_memory.db_models import ProjectRow
    from codex_memory.v1_service import AppendConflictError, V1MemoryService

    engine = create_sqlite_engine()
    create_schema(engine)
    session_factory = create_session_factory(engine)
    with session_factory() as session:
        session.add(ProjectRow(project_key="erp", name="ERP"))
        session.commit()

    service = V1MemoryService(session_factory)
    principal = Principal(project_key="erp", permissions=frozenset({"append"}))
    first = service.append_message_v11(principal, "erp", "s1", "e1", "user", "original")
    second = service.append_message_v11(principal, "erp", "s1", "e1", "user", "original")

    assert first.status == "stored"
    assert second.status == "duplicate"
    with pytest.raises(AppendConflictError):
        service.append_message_v11(principal, "erp", "s1", "e1", "user", "changed")