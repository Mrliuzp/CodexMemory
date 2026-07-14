from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.orm import Session, sessionmaker


EXPECTED_SCHEMA_REVISION = "0011_v12_admin_scopes"


def _schema_status(session: Session) -> str:
    bind = session.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("projects"):
        return "missing"
    if not inspector.has_table("alembic_version"):
        return "outdated"
    revisions = set(session.scalars(text("SELECT version_num FROM alembic_version")).all())
    return "ok" if revisions == {EXPECTED_SCHEMA_REVISION} else "outdated"


def build_readiness(session_factory: sessionmaker[Session]) -> dict[str, str]:
    try:
        with session_factory() as session:
            session.execute(text("SELECT 1"))
            dialect = session.get_bind().dialect.name
            schema = _schema_status(session)
            vector = "not-applicable"
            if dialect == "postgresql":
                vector = (
                    "ok"
                    if session.execute(
                        text("SELECT 1 FROM pg_extension WHERE extname='vector'")
                    ).first()
                    else "missing"
                )
            ready = schema == "ok" and vector in {"ok", "not-applicable"}
            return {
                "status": "ok" if ready else "degraded",
                "database": "ok",
                "schema": schema,
                "vector": vector,
            }
    except Exception:
        return {
            "status": "degraded",
            "database": "error",
            "schema": "unknown",
            "vector": "unknown",
        }
