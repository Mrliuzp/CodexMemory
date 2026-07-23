from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, select


ROOT = Path(__file__).resolve().parents[1]


EXPECTED_V11_TABLES = {
    "project_feature_flags",
    "project_processing_policies",
    "outbox_events",
    "processing_jobs",
    "job_attempts",
    "memory_candidates",
    "candidate_evidence",
    "candidate_policy_results",
    "embedding_profiles",
    "project_retrieval_profiles",
    "memory_chunks",
    "memory_embedding_vectors",
    "memory_search_documents",
    "retrieval_audits",
    "security_audits",
}


def _alembic_config(database_url: str) -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def test_v11_models_are_additive_and_postgresql_compatible() -> None:
    from codex_memory.db import create_schema, create_postgres_test_engine, create_session_factory
    from codex_memory.db_models import (
        Base,
        EmbeddingProfileRow,
        MemoryEmbeddingRow,
        MemoryEmbeddingVectorRow,
        ProjectFeatureFlagRow,
        ProjectProcessingPolicyRow,
        ProjectRow,
        V11Base,
    )

    engine = create_postgres_test_engine()
    create_schema(engine)
    V11Base.metadata.create_all(engine)
    tables = set(inspect(engine).get_table_names())

    assert EXPECTED_V11_TABLES <= tables
    assert "memory_embeddings" in tables
    assert {"occurred_at", "ingestion_version", "conflict_status"} <= {
        column["name"] for column in inspect(engine).get_columns("messages")
    }
    assert {"scope", "source_kind", "review_status"} <= {
        column["name"] for column in inspect(engine).get_columns("memories")
    }
    assert "memory_embeddings" in Base.metadata.tables
    assert "memory_embedding_vectors" not in Base.metadata.tables
    assert MemoryEmbeddingRow.__tablename__ == "memory_embeddings"
    assert MemoryEmbeddingVectorRow.__tablename__ == "memory_embedding_vectors"

    factory = create_session_factory(engine)
    with factory() as session:
        project = ProjectRow(project_key="v11", name="V1.1")
        session.add(project)
        session.flush()
        session.add_all(
            [
                ProjectFeatureFlagRow(project_id=project.id),
                ProjectProcessingPolicyRow(project_id=project.id),
                EmbeddingProfileRow(
                    name="legacy-test",
                    provider="test",
                    model="test-model",
                    dimension=3,
                    similarity_metric="cosine",
                    normalization="none",
                    chunker_version="v1",
                    content_normalization_version="v1",
                ),
            ]
        )
        session.flush()
        flags = session.get(ProjectFeatureFlagRow, project.id)
        policy = session.get(ProjectProcessingPolicyRow, project.id)

        assert flags is not None
        assert flags.memory_v11_enabled is False
        assert flags.server_outbox_enabled is False
        assert flags.lexical_retrieval_enabled is False
        assert flags.dense_retrieval_enabled is False
        assert flags.embedding_profile_v2_enabled is False
        assert flags.llm_shadow_enabled is False
        assert flags.candidate_publish_enabled is False
        assert policy is not None
        assert policy.remote_embedding_allowed is False
        assert policy.remote_llm_allowed is False
        assert policy.redaction_enabled is True
        assert policy.failure_mode == "fail_closed"


def test_v11_migrations_upgrade_postgresql_and_keep_legacy_embedding_table() -> None:
    from codex_memory.db import create_postgres_test_engine

    engine = create_postgres_test_engine()
    database_url = engine.url.render_as_string(hide_password=False)
    command.upgrade(_alembic_config(database_url), "head")

    engine = create_engine(database_url)
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())

    assert EXPECTED_V11_TABLES <= tables
    assert {"messages", "memories", "memory_embeddings", "memory_relations"} <= tables
    assert {"occurred_at", "ingestion_version", "conflict_status"} <= {
        column["name"] for column in inspector.get_columns("messages")
    }
    assert {"scope", "source_kind", "review_status"} <= {
        column["name"] for column in inspector.get_columns("memories")
    }
    index_names = {
        index["name"]
        for index in inspector.get_indexes("messages")
        if index["name"]
    }
    assert "uq_messages_project_event_key" in index_names
    assert "ix_outbox_claim" in {
        index["name"] for index in inspector.get_indexes("outbox_events")
    }

    command.downgrade(_alembic_config(database_url), "0002_memory_relations")
    downgraded = inspect(engine)
    assert "memory_embeddings" in set(downgraded.get_table_names())
    assert not (EXPECTED_V11_TABLES & set(downgraded.get_table_names()))
    assert {"messages", "memories", "memory_relations"} <= set(downgraded.get_table_names())


def test_v11_flag_models_expose_server_defaults() -> None:
    from codex_memory.db_models import ProjectFeatureFlagRow, ProjectProcessingPolicyRow

    assert ProjectFeatureFlagRow.__table__.c.memory_v11_enabled.server_default is not None
    assert ProjectFeatureFlagRow.__table__.c.candidate_publish_enabled.server_default is not None
    assert ProjectProcessingPolicyRow.__table__.c.remote_embedding_allowed.server_default is not None
    assert ProjectProcessingPolicyRow.__table__.c.failure_mode.server_default is not None
