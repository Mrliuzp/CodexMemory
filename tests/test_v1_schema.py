from __future__ import annotations

import pytest


def test_messages_use_event_key_for_idempotency() -> None:
    from codex_memory.db import create_sqlite_engine, create_session_factory, create_schema
    from codex_memory.db_models import MessageRow, ProjectRow, SessionRow

    engine = create_sqlite_engine()
    create_schema(engine)
    session_factory = create_session_factory(engine)

    with session_factory() as session:
        project = ProjectRow(project_key="erp", name="ERP")
        session.add(project)
        session.flush()
        conversation = SessionRow(project_id=project.id, session_key="s1")
        session.add(conversation)
        session.flush()
        session.add(
            MessageRow(
                project_id=project.id,
                session_id=conversation.id,
                event_key="s1:t1:user",
                role="user",
                content="change order",
                content_hash="same-content",
            )
        )
        session.commit()

    with session_factory() as session:
        session.add(
            MessageRow(
                project_id=1,
                session_id=1,
                event_key="s1:t1:user",
                role="user",
                content="change order",
                content_hash="same-content",
            )
        )
        with pytest.raises(Exception):
            session.commit()


def test_messages_allow_same_content_for_different_events() -> None:
    from codex_memory.db import create_sqlite_engine, create_session_factory, create_schema
    from codex_memory.db_models import MessageRow, ProjectRow, SessionRow

    engine = create_sqlite_engine()
    create_schema(engine)
    session_factory = create_session_factory(engine)

    with session_factory() as session:
        project = ProjectRow(project_key="erp", name="ERP")
        session.add(project)
        session.flush()
        conversation = SessionRow(project_id=project.id, session_key="s1")
        session.add(conversation)
        session.flush()
        session.add_all(
            [
                MessageRow(
                    project_id=project.id,
                    session_id=conversation.id,
                    event_key="s1:t1:user",
                    role="user",
                    content="same",
                    content_hash="same-content",
                ),
                MessageRow(
                    project_id=project.id,
                    session_id=conversation.id,
                    event_key="s1:t2:user",
                    role="user",
                    content="same",
                    content_hash="same-content",
                ),
            ]
        )
        session.commit()

    with session_factory() as session:
        assert session.query(MessageRow).count() == 2


def test_schema_includes_memory_relations() -> None:
    from sqlalchemy import inspect

    from codex_memory.db import create_schema, create_sqlite_engine

    engine = create_sqlite_engine()
    create_schema(engine)

    assert inspect(engine).has_table("memory_relations")
