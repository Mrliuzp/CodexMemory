from __future__ import annotations

from sqlalchemy import select


def _factory_with_memories():
    from codex_memory.db import create_schema, create_session_factory, create_sqlite_engine
    from codex_memory.db_models import MemoryRow, ProjectRow, V11Base

    engine = create_sqlite_engine()
    create_schema(engine)
    V11Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    with factory() as session:
        erp = ProjectRow(project_key="erp", name="ERP")
        mall = ProjectRow(project_key="mall", name="Mall")
        session.add_all([erp, mall])
        session.flush()
        session.add_all(
            [
                MemoryRow(
                    project_id=erp.id,
                    level="L3",
                    memory_type="error",
                    title="Cache error",
                    content={"text": "deployment cache invalidation failed"},
                    status="published",
                    scope="project",
                    review_status="accepted",
                ),
                MemoryRow(
                    project_id=erp.id,
                    level="L1",
                    memory_type="working",
                    title="Pending note",
                    content={"text": "deployment pending note"},
                    status="candidate",
                    scope="project",
                    review_status="needs_review",
                ),
                MemoryRow(
                    project_id=None,
                    level="L2",
                    memory_type="knowledge",
                    title="Shared deployment",
                    content={"text": "shared deployment standard"},
                    status="published",
                    scope="global",
                    review_status="accepted",
                ),
                MemoryRow(
                    project_id=mall.id,
                    level="L1",
                    memory_type="private",
                    title="Mall deployment",
                    content={"text": "mall deployment secret"},
                    status="published",
                    scope="project",
                    review_status="accepted",
                ),
            ]
        )
        session.commit()
    return factory


def test_v11_search_is_project_isolated_and_returns_rrf_metadata() -> None:
    from codex_memory.v11_retrieval import V11Retriever

    factory = _factory_with_memories()
    retriever = V11Retriever(factory)
    result = retriever.search("erp", "deployment", scope_mode="project_and_global", include_audit=True)

    assert result["retrieval_mode"] == "lexical"
    assert result["degraded"] is False
    assert result["profile_id"] is None
    assert result["parameters"]["scope_mode"] == "project_and_global"
    assert result["results"]
    assert all(item["project_id"] in (None, 1) for item in result["results"])
    assert all("rrf_score" in item and "rank" in item for item in result["results"])
    assert result["audit_id"] > 0


def test_v11_scope_and_context_exclude_pending_and_respect_budget() -> None:
    from codex_memory.v11_retrieval import V11Retriever

    factory = _factory_with_memories()
    retriever = V11Retriever(factory)
    project_only = retriever.search("erp", "deployment", scope_mode="project_only")
    assert all(item["project_id"] == 1 for item in project_only["results"])

    context = retriever.build_context(
        "erp",
        "deployment",
        scope_mode="project_and_global",
        context_budget_tokens=30,
    )
    assert "Pending note" not in context["context"]
    assert context["source_ids"]
    assert context["budget"]["used_tokens"] <= 30
    assert context["budget"]["truncated"] is True


def test_v11_dense_failure_degrades_and_hybrid_uses_profile_vectors() -> None:
    from codex_memory.db_models import ProjectFeatureFlagRow, ProjectRow
    from codex_memory.v11_embedding import EmbeddingProfileService
    from codex_memory.v11_flags import ProjectPolicyService
    from codex_memory.v11_retrieval import V11Retriever

    factory = _factory_with_memories()
    with factory() as session:
        project = session.scalar(select(ProjectRow).where(ProjectRow.project_key == "erp"))
        flags = ProjectFeatureFlagRow(project_id=project.id, dense_retrieval_enabled=True)
        session.add(flags)
        session.commit()

    degraded = V11Retriever(factory).search("erp", "deployment")
    assert degraded["degraded"] is True
    assert degraded["degraded_reason"] == "active_profile_missing"

    profile = EmbeddingProfileService(factory).create_profile(
        name="dense",
        provider="local",
        model="hash-v1",
        dimension=8,
        chunker_version="v1",
        content_normalization_version="v1",
    )
    ProjectPolicyService(factory).set_active_profile(1, profile.id)
    EmbeddingProfileService(factory).backfill_memory(1, 1, profile.id)
    hybrid = V11Retriever(factory).search("erp", "deployment")
    assert hybrid["retrieval_mode"] == "hybrid"
    assert hybrid["profile_id"] == profile.id
    assert hybrid["degraded"] is False
    assert hybrid["results"][0]["rrf_score"] > 0

def test_v11_search_supports_layer_and_memory_type_filters() -> None:
    from codex_memory.v11_retrieval import V11Retriever

    factory = _factory_with_memories()
    result = V11Retriever(factory).search(
        "erp",
        "deployment",
        layers=["L2"],
        memory_types=["knowledge"],
    )
    assert [item["level"] for item in result["results"]] == ["L2"]