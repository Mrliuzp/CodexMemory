from __future__ import annotations

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from .db_models import Base


def create_sqlite_engine(database_url: str = "sqlite+pysqlite:///:memory:") -> Engine:
    return create_engine(database_url, future=True)


def create_engine_from_url(database_url: str) -> Engine:
    return create_engine(database_url, future=True, pool_pre_ping=not database_url.startswith("sqlite"))


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


def create_schema(engine: Engine) -> None:
    Base.metadata.create_all(engine)
