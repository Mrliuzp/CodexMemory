from __future__ import annotations

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.exc import IntegrityError


ROOT = Path(__file__).resolve().parents[1]
POSTGRES_TEST_URL_ENV = "CODEX_MEMORY_POSTGRES_TEST_URL"


def _alembic_config(database_url: str) -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def _assert_scope_schema(database_url: str) -> None:
    engine = create_engine(database_url)
    inspector = inspect(engine)
    assert "knowledge_scopes" in set(inspector.get_table_names())
    columns = {column["name"] for column in inspector.get_columns("knowledge_scopes")}
    assert {"id", "project_id", "scope_key", "name", "description", "is_default", "status", "created_at", "updated_at"} <= columns
    assert any(
        foreign_key["referred_table"] == "projects"
        and foreign_key["constrained_columns"] == ["project_id"]
        for foreign_key in inspector.get_foreign_keys("knowledge_scopes")
    )


def test_v12_scope_model_exposes_real_scopes_and_legacy_default_projection() -> None:
    from codex_memory.db import create_schema, create_session_factory, create_sqlite_engine
    from codex_memory.db_models import ProjectRow
    from codex_memory.v12_models import KnowledgeScopeRow, V12Base, legacy_default_scope

    engine = create_sqlite_engine()
    create_schema(engine)
    V12Base.metadata.create_all(engine)
    factory = create_session_factory(engine)

    with factory() as session:
        project = ProjectRow(project_key="erp", name="ERP")
        session.add(project)
        session.flush()
        default_scope = KnowledgeScopeRow(project_id=project.id, scope_key="default", name="Default", is_default=True)
        session.add(default_scope)
        session.commit()

        projected = session.scalar(legacy_default_scope(project.id))

    assert projected is not None
    assert projected.id == default_scope.id
    assert projected.scope_key == "default"
    assert projected.is_default is True


def test_v12_migration_creates_default_scopes_without_rewriting_legacy_records(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'v12.db'}"
    config = _alembic_config(database_url)
    command.upgrade(config, "0010_v11_provider_budgets")
    engine = create_engine(database_url)

    with engine.begin() as connection:
        connection.execute(text("INSERT INTO projects (project_key, name, status) VALUES ('erp', 'ERP', 'active')"))
        project_id = connection.scalar(text("SELECT id FROM projects WHERE project_key = 'erp'"))
        connection.execute(
            text(
                "INSERT INTO memories (project_id, level, memory_type, content, confidence, status, usage_count, deprecated, scope, source_kind, review_status) "
                "VALUES (:project_id, 'L2', 'fact', '{}', 0.5, 'accepted', 0, 0, 'project', 'rule', 'accepted')"
            ),
            {"project_id": project_id},
        )

    command.upgrade(config, "head")
    command.upgrade(config, "head")
    _assert_scope_schema(database_url)

    with engine.begin() as connection:
        default_scope = connection.execute(
            text("SELECT id, scope_key, is_default FROM knowledge_scopes WHERE project_id = :project_id"),
            {"project_id": project_id},
        ).one()
        legacy_scope = connection.scalar(text("SELECT scope FROM memories WHERE project_id = :project_id"), {"project_id": project_id})

    assert default_scope.scope_key == "default"
    assert default_scope.is_default
    assert legacy_scope == "project"

    command.downgrade(config, "0010_v11_provider_budgets")
    assert "knowledge_scopes" not in set(inspect(engine).get_table_names())


def test_v12_migration_rejects_scope_for_unknown_project(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'v12-fk.db'}"
    command.upgrade(_alembic_config(database_url), "head")
    engine = create_engine(database_url)

    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO knowledge_scopes (project_id, scope_key, name, is_default, status) "
                    "VALUES (999999, 'default', 'Default', 1, 'active')"
                )
            )


@pytest.mark.skipif(not os.getenv(POSTGRES_TEST_URL_ENV), reason=f"set {POSTGRES_TEST_URL_ENV} to run PostgreSQL migration coverage")
def test_v12_migration_upgrade_and_downgrade_postgresql() -> None:
    database_url = os.environ[POSTGRES_TEST_URL_ENV]
    config = _alembic_config(database_url)
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    _assert_scope_schema(database_url)
    command.downgrade(config, "0010_v11_provider_budgets")
    assert "knowledge_scopes" not in set(inspect(create_engine(database_url)).get_table_names())
