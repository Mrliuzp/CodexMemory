"""Add V1.3 asynchronous processing contracts."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from codex_memory.v11_models import WorkerInstanceRow


revision = "0012_v13_async_contracts"
down_revision = "0011_v12_admin_scopes"
branch_labels = None
depends_on = None


def _columns(bind: sa.Connection, table: str) -> set[str]:
    if not sa.inspect(bind).has_table(table):
        return set()
    return {item["name"] for item in sa.inspect(bind).get_columns(table)}


def _add_column_if_missing(table: str, column: sa.Column) -> None:
    if column.name not in _columns(op.get_bind(), table):
        op.add_column(table, column)


def _create_index_if_missing(name: str, table: str, columns: list[str], *, unique: bool = False) -> None:
    inspector = sa.inspect(op.get_bind())
    existing = {item["name"] for item in inspector.get_indexes(table)} if inspector.has_table(table) else set()
    if name not in existing:
        op.create_index(name, table, columns, unique=unique)


def upgrade() -> None:
    bind = op.get_bind()

    _add_column_if_missing("project_feature_flags", sa.Column("async_pipeline_v13_enabled", sa.Boolean(), nullable=False, server_default="0"))

    _add_column_if_missing("outbox_events", sa.Column("idempotency_key", sa.String(255), nullable=True))
    _add_column_if_missing("outbox_events", sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="5"))
    _add_column_if_missing("outbox_events", sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True))
    _add_column_if_missing("outbox_events", sa.Column("replay_count", sa.Integer(), nullable=False, server_default="0"))
    if sa.inspect(bind).has_table("outbox_events"):
        bind.execute(sa.text("UPDATE outbox_events SET idempotency_key = 'legacy.outbox.' || id WHERE idempotency_key IS NULL"))
        _create_index_if_missing("uq_outbox_project_idempotency", "outbox_events", ["project_id", "idempotency_key"], unique=True)

    _add_column_if_missing("processing_jobs", sa.Column("source_type", sa.String(64), nullable=True))
    _add_column_if_missing("processing_jobs", sa.Column("source_id", sa.String(255), nullable=True))
    _add_column_if_missing("processing_jobs", sa.Column("handler_version", sa.String(128), nullable=True))
    _add_column_if_missing("processing_jobs", sa.Column("idempotency_key", sa.String(255), nullable=True))
    _add_column_if_missing("processing_jobs", sa.Column("error_class", sa.String(64), nullable=True))
    _add_column_if_missing("processing_jobs", sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True))
    _add_column_if_missing("processing_jobs", sa.Column("cancel_reason", sa.Text(), nullable=True))
    if sa.inspect(bind).has_table("processing_jobs"):
        bind.execute(sa.text("UPDATE processing_jobs SET idempotency_key = 'legacy.job.' || id WHERE idempotency_key IS NULL"))
        _create_index_if_missing("uq_jobs_project_type_idempotency", "processing_jobs", ["project_id", "job_type", "idempotency_key"], unique=True)

    _add_column_if_missing("job_attempts", sa.Column("error_class", sa.String(64), nullable=True))
    _add_column_if_missing("job_attempts", sa.Column("finished_reason", sa.String(128), nullable=True))
    if sa.inspect(bind).has_table("job_attempts"):
        _create_index_if_missing("uq_job_attempt_number", "job_attempts", ["job_id", "attempt_no"], unique=True)

    if not sa.inspect(bind).has_table("worker_instances"):
        WorkerInstanceRow.__table__.create(bind)


def downgrade() -> None:
    bind = op.get_bind()
    if sa.inspect(bind).has_table("worker_instances"):
        WorkerInstanceRow.__table__.drop(bind)
    for table, indexes in {
        "job_attempts": ["uq_job_attempt_number"],
        "processing_jobs": ["uq_jobs_project_type_idempotency", "ix_processing_jobs_idempotency_key"],
        "outbox_events": ["uq_outbox_project_idempotency", "ix_outbox_events_idempotency_key"],
    }.items():
        inspector = sa.inspect(bind)
        if inspector.has_table(table):
            existing = {item["name"] for item in inspector.get_indexes(table)}
            for index in indexes:
                if index in existing:
                    op.drop_index(index, table_name=table)
    for table, columns in {
        "project_feature_flags": ["async_pipeline_v13_enabled"],
        "job_attempts": ["finished_reason", "error_class"],
        "processing_jobs": ["cancel_reason", "cancelled_at", "error_class", "idempotency_key", "handler_version", "source_id", "source_type"],
        "outbox_events": ["replay_count", "completed_at", "max_attempts", "idempotency_key"],
    }.items():
        existing = _columns(bind, table)
        for column in columns:
            if column in existing:
                op.drop_column(table, column)
