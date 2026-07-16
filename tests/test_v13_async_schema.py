from __future__ import annotations

from alembic import command
from alembic.config import Config
from pathlib import Path

from sqlalchemy import create_engine, inspect, select


def _upgrade_sqlite(tmp_path: Path):
    from codex_memory.db import create_session_factory

    database_url = f"sqlite:///{tmp_path / 'v13.db'}"
    engine = create_engine(database_url)
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    return engine, create_session_factory(engine)


def test_v13_schema_adds_async_contract_columns_and_worker_instances(tmp_path: Path) -> None:
    engine, _ = _upgrade_sqlite(tmp_path)
    inspector = inspect(engine)

    outbox_columns = {item["name"] for item in inspector.get_columns("outbox_events")}
    job_columns = {item["name"] for item in inspector.get_columns("processing_jobs")}
    attempt_columns = {item["name"] for item in inspector.get_columns("job_attempts")}
    flag_columns = {item["name"] for item in inspector.get_columns("project_feature_flags")}

    assert {"idempotency_key", "max_attempts", "completed_at", "replay_count"} <= outbox_columns
    assert {"source_type", "source_id", "handler_version", "idempotency_key", "error_class", "cancelled_at", "cancel_reason"} <= job_columns
    assert {"error_class", "finished_reason"} <= attempt_columns
    assert "async_pipeline_v13_enabled" in flag_columns
    assert inspector.has_table("worker_instances")

    outbox_indexes = {item["name"] for item in inspector.get_indexes("outbox_events")}
    job_indexes = {item["name"] for item in inspector.get_indexes("processing_jobs")}
    assert "uq_outbox_project_idempotency" in outbox_indexes
    assert "uq_jobs_project_type_idempotency" in job_indexes


def test_v13_models_accept_canonical_idempotency_fields() -> None:
    from codex_memory.db import create_schema, create_session_factory, create_sqlite_engine
    from codex_memory.db_models import (
        Base,
        OutboxEventRow,
        ProcessingJobRow,
        ProjectRow,
        V11Base,
        WorkerInstanceRow,
    )

    engine = create_sqlite_engine()
    create_schema(engine)
    V11Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    with factory() as session:
        project = ProjectRow(project_key="demo", name="Demo")
        session.add(project)
        session.flush()
        session.add(
            OutboxEventRow(
                project_id=project.id,
                aggregate_type="message",
                aggregate_id=1,
                event_type="message.appended.v1",
                payload_version="v1",
                idempotency_key="demo.message.appended.message.1.v1",
                payload={"message_id": 1},
            )
        )
        session.add(
            ProcessingJobRow(
                project_id=project.id,
                job_type="extract_memory_candidate",
                aggregate_type="message",
                aggregate_id=1,
                job_key="legacy:1",
                idempotency_key="demo.extract_memory_candidate.message.1.v1",
                source_type="message",
                source_id="1",
                handler_version="v1",
                payload_version="v1",
                payload={"message_id": 1},
            )
        )
        session.add(
            WorkerInstanceRow(
                worker_id="worker-1",
                role="async",
                version="v1",
                status="healthy",
            )
        )
        session.commit()

        assert session.scalar(select(OutboxEventRow).where(OutboxEventRow.idempotency_key.like("demo.%"))) is not None
        assert session.scalar(select(ProcessingJobRow).where(ProcessingJobRow.idempotency_key.like("demo.%"))) is not None
        assert session.get(WorkerInstanceRow, "worker-1") is not None
