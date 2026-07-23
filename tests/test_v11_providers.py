from __future__ import annotations

import pytest
from sqlalchemy import select


def _factory():
    from codex_memory.db import create_schema, create_session_factory, create_postgres_test_engine
    from codex_memory.db_models import EmbeddingProfileRow, ProjectProcessingPolicyRow, ProjectRow, V11Base

    engine = create_postgres_test_engine()
    create_schema(engine)
    V11Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    with factory() as session:
        project = ProjectRow(project_key="erp", name="ERP")
        session.add(project)
        session.flush()
        session.add(ProjectProcessingPolicyRow(project_id=project.id, remote_embedding_allowed=False, remote_llm_allowed=False))
        session.add(
            EmbeddingProfileRow(
                name="remote",
                provider="remote",
                model="remote-v1",
                dimension=3,
                chunker_version="v1",
                content_normalization_version="v1",
            )
        )
        session.commit()
    return factory


def test_remote_embedding_is_blocked_by_project_policy() -> None:
    from codex_memory.v11_providers import ProviderPolicyError, ProfileEmbeddingProvider

    factory = _factory()
    with pytest.raises(ProviderPolicyError, match="remote embedding"):
        ProfileEmbeddingProvider(factory).embed_documents(1, 1, ["hello"], backend=lambda texts: [[1, 2, 3]])


def test_local_embedding_and_remote_timeout_are_classified() -> None:
    from codex_memory.db_models import EmbeddingProfileRow, ProjectProcessingPolicyRow
    from codex_memory.v11_embedding import EmbeddingProfileService
    from codex_memory.v11_providers import RetryableProviderError, ProfileEmbeddingProvider

    factory = _factory()
    with factory() as session:
        session.add(
            EmbeddingProfileRow(
                name="local",
                provider="local",
                model="local-v1",
                dimension=3,
                chunker_version="v1",
                content_normalization_version="v1",
            )
        )
        policy = session.get(ProjectProcessingPolicyRow, 1)
        policy.remote_embedding_allowed = True
        policy.allowed_embedding_providers = ["remote"]
        session.commit()

    local_profile = EmbeddingProfileService(factory).create_profile(
        name="local-2",
        provider="local",
        model="local-v2",
        dimension=3,
        chunker_version="v1",
        content_normalization_version="v1",
    )
    vectors = ProfileEmbeddingProvider(factory).embed_documents(1, local_profile.id, ["hello"])
    assert len(vectors) == 1
    assert len(vectors[0]) == 3

    with pytest.raises(RetryableProviderError, match="timeout"):
        ProfileEmbeddingProvider(factory).embed_documents(
            1,
            1,
            ["hello"],
            backend=lambda texts: (_ for _ in ()).throw(TimeoutError("timeout")),
        )