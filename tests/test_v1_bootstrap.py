from __future__ import annotations

import pytest


def test_bootstrap_creates_project_and_api_key_idempotently() -> None:
    from sqlalchemy import select

    from codex_memory.auth import hash_token
    from codex_memory.bootstrap import ensure_bootstrap
    from codex_memory.db import create_schema, create_session_factory, create_postgres_test_engine
    from codex_memory.db_models import ApiKeyRow, ProjectFeatureFlagRow, ProjectRow, V11Base

    engine = create_postgres_test_engine()
    create_schema(engine)
    V11Base.metadata.create_all(engine)
    factory = create_session_factory(engine)

    ensure_bootstrap(factory, "demo", "secret", "Demo")
    ensure_bootstrap(factory, "demo", "secret", "Demo")

    with factory() as session:
        assert session.scalar(select(ProjectRow.project_key)) == "demo"
        keys = session.scalars(select(ApiKeyRow)).all()
        assert len(keys) == 1
        assert keys[0].token_hash == hash_token("secret")
        assert keys[0].permissions == ["append", "read", "memory_write", "contract_write"]
        flags = session.get(ProjectFeatureFlagRow, 1)
        assert flags is not None
        assert flags.memory_v11_enabled is False
        assert flags.server_outbox_enabled is False


def test_bootstrap_upgrades_existing_token_permissions() -> None:
    from sqlalchemy import select

    from codex_memory.auth import hash_token
    from codex_memory.bootstrap import ensure_bootstrap
    from codex_memory.db import create_schema, create_session_factory, create_postgres_test_engine
    from codex_memory.db_models import ApiKeyRow, ProjectFeatureFlagRow, ProjectRow, V11Base

    engine = create_postgres_test_engine()
    create_schema(engine)
    V11Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    with factory() as session:
        project = ProjectRow(project_key="demo", name="Demo")
        session.add(project)
        session.flush()
        session.add(
            ApiKeyRow(
                project_id=project.id,
                token_hash=hash_token("secret"),
                permissions=["append", "read", "memory_write"],
                status="active",
            )
        )
        session.commit()

    ensure_bootstrap(factory, "demo", "secret", "Demo")

    with factory() as session:
        key = session.scalar(select(ApiKeyRow).where(ApiKeyRow.token_hash == hash_token("secret")))
        assert key is not None
        assert key.permissions == ["append", "read", "memory_write", "contract_write"]
        assert session.get(ProjectFeatureFlagRow, 1) is not None


def test_bootstrap_preserves_enabled_feature_flags_for_existing_project() -> None:
    from sqlalchemy import select

    from codex_memory.auth import hash_token
    from codex_memory.bootstrap import ensure_bootstrap
    from codex_memory.db import create_schema, create_session_factory, create_postgres_test_engine
    from codex_memory.db_models import ApiKeyRow, ProjectFeatureFlagRow, ProjectRow, V11Base

    engine = create_postgres_test_engine()
    create_schema(engine)
    V11Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    with factory() as session:
        project = ProjectRow(project_key="demo", name="Demo")
        session.add(project)
        session.flush()
        session.add(
            ProjectFeatureFlagRow(
                project_id=project.id,
                memory_v11_enabled=True,
                server_outbox_enabled=True,
            )
        )
        session.add(
            ApiKeyRow(
                project_id=project.id,
                token_hash=hash_token("secret"),
                permissions=["append"],
                status="active",
            )
        )
        session.commit()

    ensure_bootstrap(factory, "demo", "secret", "Demo")

    with factory() as session:
        project = session.scalar(select(ProjectRow).where(ProjectRow.project_key == "demo"))
        flags = session.get(ProjectFeatureFlagRow, project.id)
        assert flags is not None
        assert flags.memory_v11_enabled is True
        assert flags.server_outbox_enabled is True


def test_bootstrap_module_invokes_cli_help() -> None:
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-m", "codex_memory.bootstrap", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "usage: codex-memory-bootstrap" in result.stdout


def test_bootstrap_rejects_placeholder_token() -> None:
    from codex_memory.bootstrap import ensure_bootstrap
    from codex_memory.db import create_schema, create_session_factory, create_postgres_test_engine

    engine = create_postgres_test_engine()
    create_schema(engine)
    factory = create_session_factory(engine)

    with pytest.raises(ValueError, match="placeholder"):
        ensure_bootstrap(factory, "demo", "change-me")
