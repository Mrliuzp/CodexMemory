from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from .db_models import (
    EmbeddingProfileRow,
    ProjectFeatureFlagRow,
    ProjectRetrievalProfileRow,
    SecurityAuditRow,
)


_FLAG_NAMES = {
    "memory_v11_enabled",
    "server_outbox_enabled",
    "lexical_retrieval_enabled",
    "dense_retrieval_enabled",
    "embedding_profile_v2_enabled",
    "llm_shadow_enabled",
    "candidate_publish_enabled",
}


class ProjectPolicyService:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def get_flags(self, project_id: int) -> ProjectFeatureFlagRow:
        with self.session_factory() as session:
            flags = session.get(ProjectFeatureFlagRow, project_id)
            if flags is None:
                flags = ProjectFeatureFlagRow(project_id=project_id)
                session.add(flags)
                session.commit()
            return flags

    def update_flags(self, project_id: int, **changes: bool) -> ProjectFeatureFlagRow:
        unknown = set(changes) - _FLAG_NAMES
        if unknown:
            raise ValueError(f"未知功能开关（unknown flag）：{sorted(unknown)[0]}")
        with self.session_factory() as session:
            flags = session.get(ProjectFeatureFlagRow, project_id)
            if flags is None:
                flags = ProjectFeatureFlagRow(project_id=project_id)
                session.add(flags)
                session.flush()
            for name, value in changes.items():
                setattr(flags, name, bool(value))
            session.add(
                SecurityAuditRow(
                    project_id=project_id,
                    event_type="feature_flags_updated",
                    subject_type="project",
                    subject_id=str(project_id),
                    reason_code="admin_update",
                    metadata_json={"changes": changes},
                )
            )
            session.commit()
            return flags

    def set_canary_profile(
        self,
        project_id: int,
        profile_id: int,
        percent: int,
    ) -> ProjectRetrievalProfileRow:
        if percent not in {1, 10, 50, 100}:
            raise ValueError("Canary 百分比必须是 1、10、50 或 100")
        with self.session_factory() as session:
            profile = session.get(EmbeddingProfileRow, profile_id)
            if profile is None or profile.status == "retired":
                raise ValueError("Profile（profile）不可用")
            setting = session.get(ProjectRetrievalProfileRow, project_id)
            if setting is None:
                setting = ProjectRetrievalProfileRow(project_id=project_id)
                session.add(setting)
            if percent == 100:
                setting.previous_active_embedding_profile_id = setting.active_embedding_profile_id
                setting.active_embedding_profile_id = profile_id
            setting.canary_embedding_profile_id = profile_id
            setting.canary_percent = percent
            session.add(
                SecurityAuditRow(
                    project_id=project_id,
                    event_type="embedding_profile_canary",
                    subject_type="embedding_profile",
                    subject_id=str(profile_id),
                    reason_code=f"canary_{percent}",
                    metadata_json={"profile_id": profile_id, "percent": percent},
                )
            )
            session.commit()
            return setting

    def rollback_profile(self, project_id: int, reason: str) -> ProjectRetrievalProfileRow:
        with self.session_factory() as session:
            setting = session.get(ProjectRetrievalProfileRow, project_id)
            if setting is None or setting.previous_active_embedding_profile_id is None:
                raise ValueError("没有可用于回滚的上一个 Profile")
            setting.active_embedding_profile_id = setting.previous_active_embedding_profile_id
            setting.canary_embedding_profile_id = None
            setting.canary_percent = 0
            setting.rollback_reason = reason
            session.add(
                SecurityAuditRow(
                    project_id=project_id,
                    event_type="embedding_profile_rollback",
                    subject_type="embedding_profile",
                    subject_id=str(setting.active_embedding_profile_id),
                    reason_code="admin_rollback",
                    metadata_json={"reason": reason},
                )
            )
            session.commit()
            return setting
    def set_active_profile(self, project_id: int, profile_id: int) -> ProjectRetrievalProfileRow:
        with self.session_factory() as session:
            profile = session.get(EmbeddingProfileRow, profile_id)
            if profile is None or profile.status == "retired":
                raise ValueError("Profile（profile）不可用")
            setting = session.get(ProjectRetrievalProfileRow, project_id)
            if setting is None:
                setting = ProjectRetrievalProfileRow(project_id=project_id)
                session.add(setting)
            setting.active_embedding_profile_id = profile_id
            setting.hybrid_search_enabled = True
            session.add(
                SecurityAuditRow(
                    project_id=project_id,
                    event_type="embedding_profile_activated",
                    subject_type="embedding_profile",
                    subject_id=str(profile_id),
                    reason_code="admin_update",
                    metadata_json={"profile_id": profile_id},
                )
            )
            session.commit()
            return setting