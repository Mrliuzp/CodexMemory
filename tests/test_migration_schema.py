from __future__ import annotations

from sqlalchemy import inspect


def test_operations_schema_has_migration_and_archive_tables() -> None:
    from codex_memory.db import create_schema, create_sqlite_engine

    engine = create_sqlite_engine()
    create_schema(engine)

    names = set(inspect(engine).get_table_names())
    assert {"migration_batches", "migration_issues", "archive_status"} <= names


def test_message_source_fingerprint_is_unique_per_project() -> None:
    from codex_memory.db import create_schema, create_sqlite_engine

    engine = create_sqlite_engine()
    create_schema(engine)

    indexes = inspect(engine).get_indexes("messages")
    assert any(
        item["unique"] and item["column_names"] == ["project_id", "source_fingerprint"]
        for item in indexes
    )


def test_archive_status_is_unique_per_project() -> None:
    from codex_memory.db import create_schema, create_sqlite_engine

    engine = create_sqlite_engine()
    create_schema(engine)

    constraints = inspect(engine).get_unique_constraints("archive_status")
    assert any(item["column_names"] == ["project_id"] for item in constraints)
