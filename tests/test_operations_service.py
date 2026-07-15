from __future__ import annotations


def test_operations_status_is_read_only_and_redacted() -> None:
    from codex_memory.db import create_schema, create_session_factory, create_sqlite_engine
    from codex_memory.operations_service import OperationsService
    engine=create_sqlite_engine(); create_schema(engine)
    status=OperationsService(create_session_factory(engine)).system_status()
    assert status["database"] == "sqlite"
    assert "token" not in str(status).lower()
    assert "postgresql" not in str(status).lower()
