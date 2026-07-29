"""V1.4 迁移路径与元数据隔离回归测试。"""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


ROOT = Path(__file__).resolve().parents[1]
TASK_TABLES = {"task_runs", "task_events", "task_reports", "task_file_changes"}


def _alembic_config(database_url: str) -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def _database_url_and_engine() -> tuple[str, object]:
    from codex_memory.db import create_postgres_test_engine

    engine = create_postgres_test_engine()
    return engine.url.render_as_string(hide_password=False), engine


def _task_tables(engine: object) -> set[str]:
    return set(inspect(engine).get_table_names()).intersection(TASK_TABLES)


def test_v14_metadata_is_isolated_from_v11_metadata() -> None:
    from codex_memory.db_models import V11Base, V14Base

    assert TASK_TABLES.isdisjoint(V11Base.metadata.tables)
    assert TASK_TABLES <= set(V14Base.metadata.tables)


def test_v14_migration_fresh_to_head() -> None:
    database_url, engine = _database_url_and_engine()

    command.upgrade(_alembic_config(database_url), "head")

    assert _task_tables(engine) == TASK_TABLES


def test_v14_migration_0021_to_head() -> None:
    database_url, engine = _database_url_and_engine()
    config = _alembic_config(database_url)

    command.upgrade(config, "0021_v131_memory_scope")
    assert _task_tables(engine) == set()
    command.upgrade(config, "head")

    assert _task_tables(engine) == TASK_TABLES


def test_v14_migration_head_to_0021_downgrade() -> None:
    database_url, engine = _database_url_and_engine()
    config = _alembic_config(database_url)

    command.upgrade(config, "head")
    assert _task_tables(engine) == TASK_TABLES
    command.downgrade(config, "0021_v131_memory_scope")

    assert _task_tables(engine) == set()
