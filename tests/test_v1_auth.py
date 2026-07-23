from __future__ import annotations

import hashlib

import pytest


def test_authenticate_bearer_returns_project_scoped_permissions() -> None:
    from codex_memory.auth import authenticate_bearer
    from codex_memory.db import create_schema, create_session_factory, create_postgres_test_engine
    from codex_memory.db_models import ApiKeyRow, ProjectRow

    engine = create_postgres_test_engine()
    create_schema(engine)
    session_factory = create_session_factory(engine)
    with session_factory() as session:
        project = ProjectRow(project_key="erp", name="ERP")
        session.add(project)
        session.flush()
        session.add(
            ApiKeyRow(
                project_id=project.id,
                token_hash=hashlib.sha256(b"secret").hexdigest(),
                permissions=["read", "append"],
            )
        )
        session.commit()

    principal = authenticate_bearer(session_factory, "secret")

    assert principal.project_key == "erp"
    assert principal.permissions == frozenset({"read", "append"})


def test_authenticate_bearer_rejects_unknown_token() -> None:
    from codex_memory.auth import TokenAuthenticationError, authenticate_bearer
    from codex_memory.db import create_schema, create_session_factory, create_postgres_test_engine

    engine = create_postgres_test_engine()
    create_schema(engine)
    session_factory = create_session_factory(engine)

    with pytest.raises(TokenAuthenticationError):
        authenticate_bearer(session_factory, "unknown")
