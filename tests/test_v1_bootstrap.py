from __future__ import annotations

import pytest


def test_bootstrap_creates_project_and_api_key_idempotently() -> None:
    from sqlalchemy import select

    from codex_memory.auth import hash_token
    from codex_memory.bootstrap import ensure_bootstrap
    from codex_memory.db import create_schema, create_session_factory, create_postgres_test_engine
    from codex_memory.db_models import ApiKeyRow, ProjectRow

    engine = create_postgres_test_engine()
    create_schema(engine)
    factory = create_session_factory(engine)

    ensure_bootstrap(factory, "demo", "secret", "Demo")
    ensure_bootstrap(factory, "demo", "secret", "Demo")

    with factory() as session:
        assert session.scalar(select(ProjectRow.project_key)) == "demo"
        keys = session.scalars(select(ApiKeyRow)).all()
        assert len(keys) == 1
        assert keys[0].token_hash == hash_token("secret")
        assert keys[0].permissions == ["append", "read", "memory_write"]


def test_bootstrap_rejects_placeholder_token() -> None:
    from codex_memory.bootstrap import ensure_bootstrap
    from codex_memory.db import create_schema, create_session_factory, create_postgres_test_engine

    engine = create_postgres_test_engine()
    create_schema(engine)
    factory = create_session_factory(engine)

    with pytest.raises(ValueError, match="placeholder"):
        ensure_bootstrap(factory, "demo", "change-me")
