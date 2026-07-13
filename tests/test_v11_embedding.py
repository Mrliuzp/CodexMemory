from __future__ import annotations

import pytest
from sqlalchemy import select


def _factory_with_memory():
    from codex_memory.db import create_schema, create_session_factory, create_sqlite_engine
    from codex_memory.db_models import MemoryRow, ProjectRow, V11Base

    engine = create_sqlite_engine()
    create_schema(engine)
    V11Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    with factory() as session:
        project = ProjectRow(project_key="erp", name="ERP")
        session.add(project)
        session.flush()
        memory = MemoryRow(
            project_id=project.id,
            level="L1",
            memory_type="solution",
            title="Order service",
            content={"text": "Use the order service for updates."},
            status="published",
            review_status="accepted",
        )
        session.add(memory)
        session.commit()
        return factory, project.id, memory.id


def test_embedding_profile_is_dimension_checked_and_vectors_are_profile_isolated() -> None:
    from codex_memory.db_models import EmbeddingProfileRow, MemoryEmbeddingVectorRow
    from codex_memory.v11_embedding import EmbeddingProfileService

    factory, project_id, memory_id = _factory_with_memory()
    service = EmbeddingProfileService(factory)
    profile_a = service.create_profile(
        name="local-a",
        provider="local",
        model="hash-v1",
        dimension=8,
        chunker_version="v1",
        content_normalization_version="v1",
    )
    profile_b = service.create_profile(
        name="local-b",
        provider="local",
        model="hash-v2",
        dimension=4,
        chunker_version="v1",
        content_normalization_version="v1",
    )

    vectors_a = service.backfill_memory(project_id, memory_id, profile_a.id)
    vectors_b = service.backfill_memory(project_id, memory_id, profile_b.id)

    assert vectors_a
    assert vectors_b
    assert all(len(row.embedding) == 8 for row in vectors_a)
    assert all(len(row.embedding) == 4 for row in vectors_b)
    with factory() as session:
        assert session.scalar(select(EmbeddingProfileRow).where(EmbeddingProfileRow.id == profile_a.id)).dimension == 8
        assert len(session.scalars(select(MemoryEmbeddingVectorRow)).all()) == 2


def test_embedding_profile_rejects_wrong_vector_dimension() -> None:
    from codex_memory.v11_embedding import EmbeddingProfileService

    factory, _, _ = _factory_with_memory()
    service = EmbeddingProfileService(factory)
    profile = service.create_profile(
        name="local",
        provider="local",
        model="hash-v1",
        dimension=3,
        chunker_version="v1",
        content_normalization_version="v1",
    )
    with pytest.raises(ValueError, match="dimension"):
        service.validate_vector(profile.id, [0.1, 0.2])


def test_profile_backfill_is_idempotent() -> None:
    from codex_memory.db_models import MemoryEmbeddingVectorRow
    from codex_memory.v11_embedding import EmbeddingProfileService

    factory, project_id, memory_id = _factory_with_memory()
    service = EmbeddingProfileService(factory)
    profile = service.create_profile(
        name="local",
        provider="local",
        model="hash-v1",
        dimension=6,
        chunker_version="v1",
        content_normalization_version="v1",
    )
    first = service.backfill_memory(project_id, memory_id, profile.id)
    second = service.backfill_memory(project_id, memory_id, profile.id)
    assert len(first) == len(second) == 1
    with factory() as session:
        assert len(session.scalars(select(MemoryEmbeddingVectorRow)).all()) == 1