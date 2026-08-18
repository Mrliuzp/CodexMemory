"""候选决策 Worker 的项目级可观测查询。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, sessionmaker

from .db_models import (
    JobAttemptRow,
    MemoryCandidateRow,
    ProcessingJobRow,
    ProjectFeatureFlagRow,
    SecurityAuditRow,
)
from .v11_decision import DECISION_JOB_TYPE, resolve_l1_auto_publish_threshold
from .v11_decision_policy import DecisionPolicyProvider, SqlDecisionPolicyProvider


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class DecisionObservabilityService:
    """读取指标，不改变 Worker 或候选状态。"""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        stuck_after_seconds: int = 120,
        policy_provider: DecisionPolicyProvider | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.stuck_after_seconds = max(1, int(stuck_after_seconds))
        self.policy_provider = policy_provider or SqlDecisionPolicyProvider(session_factory)

    def snapshot(self, project_id: int | None = None) -> dict[str, Any]:
        from ..codex_cli_runner import CodexCliSettings

        with self.session_factory() as session:
            def count(model: Any, *conditions: Any) -> int:
                query = select(func.count()).select_from(model)
                if project_id is not None and hasattr(model, "project_id"):
                    query = query.where(model.project_id == project_id)
                if conditions:
                    query = query.where(*conditions)
                return int(session.scalar(query) or 0)

            decision_jobs = ProcessingJobRow.job_type == DECISION_JOB_TYPE
            queued = count(ProcessingJobRow, decision_jobs, ProcessingJobRow.status.in_(("pending", "retry_wait", "running")))
            human_review = count(MemoryCandidateRow, MemoryCandidateRow.status == "needs_review")
            model_failures = count(SecurityAuditRow, SecurityAuditRow.event_type == "candidate_model_failed")
            retries_query = (
                select(func.count())
                .select_from(JobAttemptRow)
                .join(ProcessingJobRow, ProcessingJobRow.id == JobAttemptRow.job_id)
                .where(
                    ProcessingJobRow.job_type == DECISION_JOB_TYPE,
                    or_(JobAttemptRow.attempt_no > 1, JobAttemptRow.outcome.in_(("failed", "abandoned"))),
                )
            )
            if project_id is not None:
                retries_query = retries_query.where(ProcessingJobRow.project_id == project_id)
            retries = int(session.scalar(retries_query) or 0)
            auto_published = count(SecurityAuditRow, SecurityAuditRow.event_type == "candidate_auto_published")
            human_overturned = count(SecurityAuditRow, SecurityAuditRow.event_type == "candidate_human_correction")
            now = _utcnow()
            stuck_query = select(func.count()).select_from(ProcessingJobRow).where(
                ProcessingJobRow.status == "running",
                or_(
                    ProcessingJobRow.lease_expires_at <= now,
                    ProcessingJobRow.heartbeat_at <= now - timedelta(seconds=self.stuck_after_seconds),
                ),
            )
            if project_id is not None:
                stuck_query = stuck_query.where(ProcessingJobRow.project_id == project_id)
            stuck_jobs = int(session.scalar(stuck_query) or 0)

            if project_id is not None:
                threshold = self.policy_provider.get(project_id).min_confidence
            else:
                try:
                    threshold = resolve_l1_auto_publish_threshold()
                except ValueError:
                    threshold = 0.80
            flags_payload: dict[str, Any] = {
                "memory_v11_enabled": False,
                "candidate_publish_enabled": False,
                "async_pipeline_v13_enabled": False,
                "decision_engine_enabled": False,
                "codex_cli_enabled": CodexCliSettings.from_env().enabled,
                "auto_publish_l1_enabled": False,
                "policy_enabled": False,
                "auto_publish_enabled": False,
                "policy_strategy": "manual_review",
                "auto_publish_l1_threshold": threshold,
                "feature_flags_initialized": False,
            }
            if project_id is not None:
                flags = session.get(ProjectFeatureFlagRow, project_id)
                if flags is not None:
                    policy = self.policy_provider.get(project_id)
                    flags_payload.update(
                        {
                            "memory_v11_enabled": bool(flags.memory_v11_enabled),
                            "candidate_publish_enabled": bool(flags.candidate_publish_enabled),
                            "async_pipeline_v13_enabled": bool(flags.async_pipeline_v13_enabled),
                            "decision_engine_enabled": bool(getattr(flags, "decision_engine_enabled", False)),
                            "feature_flags_initialized": True,
                            "policy_enabled": bool(policy.enabled),
                            "auto_publish_enabled": bool(policy.auto_publish_enabled),
                            "policy_strategy": policy.strategy,
                            "auto_publish_l1_enabled": bool(
                                flags.candidate_publish_enabled
                                and getattr(flags, "decision_engine_enabled", False)
                                and policy.enabled
                                and policy.auto_publish_enabled
                                and policy.strategy == "auto_publish"
                            ),
                        }
                    )
            else:
                enabled_query = select(func.count()).select_from(ProjectFeatureFlagRow).where(
                    ProjectFeatureFlagRow.candidate_publish_enabled.is_(True),
                    ProjectFeatureFlagRow.decision_engine_enabled.is_(True),
                )
                flags_payload["projects_with_auto_publish_l1_enabled"] = int(session.scalar(enabled_query) or 0)

            return {
                "queued": queued,
                "human_review_queue": human_review,
                "model_failures": model_failures,
                "retries": retries,
                "auto_published": auto_published,
                "human_overturned": human_overturned,
                "stuck_jobs": stuck_jobs,
                "feature_flags": flags_payload,
            }


__all__ = ["DecisionObservabilityService"]
