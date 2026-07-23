from __future__ import annotations

from typing import Any

from sqlalchemy import func, inspect, select
from sqlalchemy.orm import Session, sessionmaker

from .db_models import ArchiveStatusRow, MigrationBatchRow, ProjectRow
from .v11_models import OutboxEventRow, ProcessingJobRow


class OperationsService:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def system_status(self) -> dict[str, Any]:
        with self.session_factory() as session:
            bind = session.get_bind(); tables = set(inspect(bind).get_table_names())
            jobs = int(session.scalar(select(func.count(ProcessingJobRow.id)).where(ProcessingJobRow.status.in_(("pending", "retry_wait")))) or 0) if "processing_jobs" in tables else 0
            outbox = int(session.scalar(select(func.count(OutboxEventRow.id)).where(OutboxEventRow.status != "dispatched")) or 0) if "outbox_events" in tables else 0
            dead = int(session.scalar(select(func.count(ArchiveStatusRow.id)).where(ArchiveStatusRow.dead_letter_count > 0)) or 0) if "archive_status" in tables else 0
            batch = session.scalar(select(MigrationBatchRow.status).order_by(MigrationBatchRow.id.desc())) if "migration_batches" in tables else None
            return {"database": bind.dialect.name, "pending_jobs": jobs, "server_outbox": outbox, "dead_letters": dead, "latest_migration": batch, "migration_schema": "ok" if "migration_batches" in tables else "not_applicable"}

    def project_archive_status(self, project_key: str) -> dict[str, Any] | None:
        with self.session_factory() as session:
            project = session.scalar(select(ProjectRow).where(ProjectRow.project_key == project_key))
            if project is None: return None
            status = session.scalar(select(ArchiveStatusRow).where(ArchiveStatusRow.project_id == project.id))
            if status is None: return {"project_key": project_key, "pending": 0, "dead_letter": 0, "last_user_archived_at": None, "last_assistant_archived_at": None, "last_failure_at": None}
            return {"project_key": project_key, "pending": status.pending_count, "dead_letter": status.dead_letter_count, "last_user_archived_at": status.last_user_archived_at, "last_assistant_archived_at": status.last_assistant_archived_at, "last_failure_at": status.last_failure_at}
