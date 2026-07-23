from __future__ import annotations

import atexit
import os
import uuid

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from .db_models import Base


def create_engine_from_url(database_url: str) -> Engine:
    if not database_url.startswith(("postgresql://", "postgresql+psycopg://")):
        raise ValueError("数据库 URL 必须使用 PostgreSQL")
    return create_engine(database_url, future=True, pool_pre_ping=True)


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


_TEST_DATABASES: list[tuple[str, str]] = []


def create_postgres_test_engine(database_url: str | None = None) -> Engine:
    """为测试创建隔离的 PostgreSQL 数据库。"""
    from sqlalchemy.engine import make_url

    from .config import Settings

    base_url = database_url or os.environ.get("CODEX_MEMORY_TEST_DATABASE_URL") or Settings.from_env().database_url
    database_name = f"test_{uuid.uuid4().hex}"
    admin_url = make_url(base_url).set(query={}).render_as_string(hide_password=False)
    admin_engine = create_engine_from_url(admin_url).execution_options(isolation_level="AUTOCOMMIT")
    with admin_engine.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{database_name}"'))
    admin_engine.dispose()

    test_url = make_url(base_url).set(database=database_name, query={}).render_as_string(hide_password=False)
    engine = create_engine_from_url(test_url)
    with engine.begin() as connection:
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    _TEST_DATABASES.append((admin_url, database_name))
    return engine


@atexit.register
def _cleanup_test_databases() -> None:
    while _TEST_DATABASES:
        admin_url, database_name = _TEST_DATABASES.pop()
        engine = create_engine_from_url(admin_url).execution_options(isolation_level="AUTOCOMMIT")
        try:
            with engine.connect() as connection:
                connection.execute(text(f'DROP DATABASE IF EXISTS "{database_name}" WITH (FORCE)'))
        finally:
            engine.dispose()


def create_schema(engine: Engine) -> None:
    Base.metadata.create_all(engine)
