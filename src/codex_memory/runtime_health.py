from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.orm import Session, sessionmaker


def build_readiness(session_factory: sessionmaker[Session]) -> dict[str, str]:
    with session_factory() as session:
        session.execute(text("SELECT 1"))
        bind = session.get_bind()
        dialect = bind.dialect.name
        schema = "ok" if inspect(bind).has_table("projects") else "missing"
        vector = "not-applicable"
        if dialect == "postgresql":
            vector = "ok" if session.execute(text("SELECT 1 FROM pg_extension WHERE extname='vector'")).first() else "missing"
        return {"status": "ok" if schema == "ok" else "degraded", "database": "ok", "schema": schema, "vector": vector}
