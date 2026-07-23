from __future__ import annotations

from sqlalchemy import select


def _factory():
    from codex_memory.db import create_schema, create_session_factory, create_postgres_test_engine
    from codex_memory.db_models import ProjectRow, V11Base

    engine = create_postgres_test_engine()
    create_schema(engine)
    V11Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    with factory() as session:
        session.add(ProjectRow(project_key="erp", name="ERP"))
        session.commit()
    return factory


def test_project_flags_are_default_off_and_canary_updates_are_audited() -> None:
    from codex_memory.db_models import ProjectFeatureFlagRow, ProjectRow, SecurityAuditRow
    from codex_memory.v11_flags import ProjectPolicyService

    factory = _factory()
    with factory() as session:
        project = session.scalar(select(ProjectRow).where(ProjectRow.project_key == "erp"))
        project_id = project.id

    service = ProjectPolicyService(factory)
    flags = service.get_flags(project_id)
    assert flags.server_outbox_enabled is False
    updated = service.update_flags(project_id, server_outbox_enabled=True, lexical_retrieval_enabled=True)
    assert updated.server_outbox_enabled is True
    assert updated.lexical_retrieval_enabled is True

    with factory() as session:
        audit = session.scalar(select(SecurityAuditRow).order_by(SecurityAuditRow.id.desc()))
        assert audit is not None
        assert audit.event_type == "feature_flags_updated"


def test_invalid_flag_and_global_profile_switch_are_rejected_without_admin_policy() -> None:
    from codex_memory.v11_flags import ProjectPolicyService

    factory = _factory()
    service = ProjectPolicyService(factory)
    with __import__("pytest").raises(ValueError, match="unknown flag"):
        service.update_flags(1, not_a_flag=True)
    with __import__("pytest").raises(ValueError, match="profile"):
        service.set_active_profile(1, 999)
def test_profile_canary_percentages_and_rollback_are_durable() -> None:
    from codex_memory.db_models import ProjectRetrievalProfileRow, ProjectRow
    from codex_memory.v11_embedding import EmbeddingProfileService
    from codex_memory.v11_flags import ProjectPolicyService

    factory = _factory()
    profile_service = EmbeddingProfileService(factory)
    old = profile_service.create_profile(
        name="old",
        provider="local",
        model="old",
        dimension=2,
        chunker_version="v1",
        content_normalization_version="v1",
    )
    new = profile_service.create_profile(
        name="new",
        provider="local",
        model="new",
        dimension=2,
        chunker_version="v1",
        content_normalization_version="v1",
    )
    service = ProjectPolicyService(factory)
    service.set_active_profile(1, old.id)
    setting = service.set_canary_profile(1, new.id, 10)
    assert setting.canary_embedding_profile_id == new.id
    assert setting.canary_percent == 10
    setting = service.set_canary_profile(1, new.id, 100)
    assert setting.active_embedding_profile_id == new.id
    rolled_back = service.rollback_profile(1, "dense quality regression")
    assert rolled_back.active_embedding_profile_id == old.id
    with factory() as session:
        assert session.scalar(select(ProjectRetrievalProfileRow).where(ProjectRetrievalProfileRow.project_id == 1)).canary_percent == 0