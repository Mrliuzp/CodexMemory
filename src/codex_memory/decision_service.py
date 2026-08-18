"""Codex CLI 决策记录、策略和人工审核服务。

本服务只负责记录经过校验的模型输出及其治理状态，不创建 Memory、候选版本，
也不执行发布。后续 Integration 必须继续调用 CandidatePolicyService 完成候选审核和发布。
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Mapping

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from .decision_models import (
    DecisionAction,
    DecisionErrorClass,
    DecisionReviewInput,
    DecisionRunFinish,
    DecisionRunInput,
    DecisionRunStatus,
    DecisionStrategy,
    EvidenceRange,
    ModelDecisionOutput,
    ProjectDecisionPolicy,
    ReviewAction,
)
from .persistence.db_models import AuditLogRow, MessageRow, ProjectRow
from .persistence.v11_models import (
    MemoryCandidateRow,
    OutboxEventRow,
    ProcessingJobRow,
    ProjectFeatureFlagRow,
    SecurityAuditRow,
)
from .persistence.v16_models import (
    DecisionReviewActionRow,
    DecisionRunRow,
    ModelDecisionRow,
    ProjectDecisionPolicyRow,
)


class DecisionValidationError(ValueError):
    """模型输出或人工纠错不满足决策契约。"""

    def __init__(self, message: str, *, code: str = "invalid_model_output") -> None:
        super().__init__(message)
        self.code = code


_TERMINAL_RUN_STATUSES = {
    DecisionRunStatus.SUCCEEDED.value,
    DecisionRunStatus.FAILED.value,
    DecisionRunStatus.CANCELLED.value,
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _validation_summary(error: ValidationError) -> str:
    """只返回字段路径和错误类型，避免把不可信原文写入审计。"""

    items: list[str] = []
    for item in error.errors():
        location = ".".join(str(part) for part in item.get("loc", ())) or "root"
        message = str(item.get("msg", "字段无效"))
        items.append(f"{location}:{item.get('type', 'invalid')}:{message}")
    return "; ".join(items)[:1000]


class DecisionService:
    """后续 Runner 可调用的决策持久化契约。"""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    @staticmethod
    def validate_model_output(payload: Mapping[str, Any] | ModelDecisionOutput) -> ModelDecisionOutput:
        """执行不依赖数据库的第一层严格结构校验。"""

        if isinstance(payload, ModelDecisionOutput):
            return payload
        try:
            return ModelDecisionOutput.model_validate(payload)
        except ValidationError as error:
            raise DecisionValidationError(
                "模型输出不符合决策契约", code="invalid_model_output"
            ) from error

    def get_policy(self, project_id: int) -> ProjectDecisionPolicyRow:
        """读取或创建项目级默认关闭策略。"""

        with self.session_factory() as session:
            self._require_project(session, project_id)
            row = session.get(ProjectDecisionPolicyRow, project_id)
            if row is None:
                row = ProjectDecisionPolicyRow(
                    project_id=project_id,
                    enabled=False,
                    auto_publish_enabled=False,
                    strategy="manual_review",
                    allowed_level="L1",
                    allowed_scope="project",
                    min_confidence=0.8,
                    require_evidence=True,
                    allow_risk_flags=False,
                    max_title_length=300,
                    max_content_chars=12_000,
                    max_reason_length=2_000,
                    max_evidence_ranges=16,
                    policy_version="decision-policy-v1",
                    updated_by="system",
                )
                session.add(row)
                session.commit()
            return row

    def update_policy(self, project_id: int, **changes: Any) -> ProjectDecisionPolicyRow:
        """更新项目策略，并拒绝任何 L2/global 或越界配置。"""

        with self.session_factory() as session:
            self._require_project(session, project_id)
            row = session.get(ProjectDecisionPolicyRow, project_id)
            values = self._policy_values(row) if row is not None else ProjectDecisionPolicy().model_dump(mode="json")
            values.update(changes)
            try:
                policy = ProjectDecisionPolicy.model_validate(values)
            except ValidationError as error:
                raise ValueError(f"决策策略无效：{_validation_summary(error)}") from error

            if row is None:
                row = ProjectDecisionPolicyRow(project_id=project_id)
                session.add(row)
            for name, value in policy.model_dump(mode="json").items():
                setattr(row, name, value)
            session.add(
                self._audit_log(
                    project_id=project_id,
                    event_type="decision_policy_updated",
                    subject_type="decision_policy",
                    subject_id=str(project_id),
                    reason_code="admin_update",
                    metadata={
                        "changes": sorted(changes),
                        "policy": policy.model_dump(mode="json"),
                        "policy_version": policy.policy_version,
                    },
                )
            )
            session.commit()
            return row

    def start_run(
        self,
        *,
        project_id: int,
        model: str,
        prompt_version: str,
        input_hash: str,
        max_retries: int = 3,
        processing_job_id: int | None = None,
        outbox_event_id: int | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> DecisionRunRow:
        """记录一次模型运行开始，不读取或保存原始提示内容。"""

        try:
            request = DecisionRunInput(
                model=model,
                prompt_version=prompt_version,
                input_hash=input_hash,
                max_retries=max_retries,
            )
        except ValidationError as error:
            raise ValueError(f"决策运行输入无效：{_validation_summary(error)}") from error

        with self.session_factory() as session:
            self._require_project(session, project_id)
            self._require_related_project(session, ProcessingJobRow, processing_job_id, project_id, "processing_job")
            self._require_related_project(session, OutboxEventRow, outbox_event_id, project_id, "outbox_event")
            run = DecisionRunRow(
                project_id=project_id,
                processing_job_id=processing_job_id,
                outbox_event_id=outbox_event_id,
                model=request.model,
                prompt_version=request.prompt_version,
                input_hash=request.input_hash,
                started_at=_now(),
                status=DecisionRunStatus.RUNNING.value,
                retry_count=0,
                max_retries=request.max_retries,
                metadata_json=dict(metadata or {}),
            )
            session.add(run)
            session.flush()
            self._add_audits(
                session,
                project_id=project_id,
                event_type="decision_run_started",
                subject_type="decision_run",
                subject_id=str(run.id),
                reason_code="worker_start",
                metadata={
                    "model": request.model,
                    "prompt_version": request.prompt_version,
                    "input_hash": request.input_hash,
                },
            )
            session.commit()
            return run

    def finish_run(
        self,
        run_id: int,
        *,
        status: DecisionRunStatus | str,
        retry_count: int | None = None,
        error_class: DecisionErrorClass | str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        total_tokens: int | None = None,
        cost_micros: int | None = None,
    ) -> DecisionRunRow:
        """更新运行终态、重试分类和用量字段。"""

        try:
            update = DecisionRunFinish.model_validate(
                {
                    "status": status,
                    "retry_count": retry_count if retry_count is not None else 0,
                    "error_class": error_class,
                    "error_code": error_code,
                    "error_message": error_message,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "total_tokens": total_tokens,
                    "cost_micros": cost_micros,
                }
            )
        except ValidationError as error:
            raise ValueError(f"决策运行结果无效：{_validation_summary(error)}") from error

        with self.session_factory() as session:
            run = session.get(DecisionRunRow, run_id)
            if run is None:
                raise LookupError(f"decision run does not exist: {run_id}")
            if run.status in _TERMINAL_RUN_STATUSES and run.status != update.status.value:
                raise ValueError("决策运行已经进入不可逆终态")
            run.status = update.status.value
            run.retry_count = update.retry_count
            run.error_class = update.error_class.value if update.error_class is not None else None
            run.error_code = update.error_code
            run.error_message = update.error_message
            run.input_tokens = update.input_tokens
            run.output_tokens = update.output_tokens
            run.total_tokens = update.total_tokens
            run.cost_micros = update.cost_micros
            if update.status.value in _TERMINAL_RUN_STATUSES:
                run.ended_at = _now()
            session.add(
                self._audit_log(
                    project_id=run.project_id,
                    event_type="decision_run_finished",
                    subject_type="decision_run",
                    subject_id=str(run.id),
                    reason_code=update.status.value,
                    metadata={
                        "status": update.status.value,
                        "retry_count": update.retry_count,
                        "error_class": run.error_class,
                        "input_tokens": update.input_tokens,
                        "output_tokens": update.output_tokens,
                        "total_tokens": update.total_tokens,
                        "cost_micros": update.cost_micros,
                    },
                )
            )
            session.commit()
            return run

    def record_decision(
        self,
        run_id: int,
        output: Mapping[str, Any] | ModelDecisionOutput,
        *,
        candidate_id: int | None = None,
    ) -> ModelDecisionRow:
        """校验并记录模型决策；永远不创建候选或发布 Memory。"""

        try:
            payload = self.validate_model_output(output)
        except DecisionValidationError as error:
            self._reject_invalid_output(run_id, error.code)
            raise
        with self.session_factory() as session:
            run = session.get(DecisionRunRow, run_id)
            if run is None:
                raise LookupError(f"decision run does not exist: {run_id}")
            if run.status not in {DecisionRunStatus.RUNNING.value, DecisionRunStatus.SUCCEEDED.value}:
                raise ValueError("只有 running 或 succeeded 运行可以记录决策")

            existing = session.scalar(select(ModelDecisionRow).where(ModelDecisionRow.decision_run_id == run_id))
            normalized = payload.model_dump(mode="json")
            if existing is not None:
                existing_normalized = {
                    "decision": existing.decision,
                    "confidence": existing.confidence,
                    "title": existing.title,
                    "content": existing.content,
                    "reason": existing.reason,
                    "evidence_ranges": existing.evidence_ranges,
                    "risk_flags": existing.risk_flags,
                    "level": existing.level,
                    "scope": existing.scope,
                }
                if existing_normalized != normalized:
                    raise DecisionValidationError("同一 decision run 已记录不同决策", code="decision_conflict")
                return existing

            try:
                policy = self._load_policy(session, run.project_id)
            except DecisionValidationError as error:
                self._reject_run_in_session(session, run, code=error.code, message=str(error))
                session.commit()
                raise
            validation_errors = self._validate_database_constraints(session, run.project_id, payload, policy)
            if candidate_id is not None:
                candidate = session.get(MemoryCandidateRow, candidate_id)
                if candidate is None or candidate.project_id != run.project_id:
                    validation_errors.append("candidate 不属于当前项目")
            if validation_errors:
                self._reject_run_in_session(
                    session,
                    run,
                    code="invalid_decision_constraints",
                    message="; ".join(validation_errors),
                )
                session.commit()
                raise DecisionValidationError("模型决策未通过项目与证据校验", code="invalid_decision_constraints")

            flags = session.get(ProjectFeatureFlagRow, run.project_id)
            auto_publish_allowed = self._auto_publish_allowed(flags, policy, payload)
            row = ModelDecisionRow(
                project_id=run.project_id,
                decision_run_id=run.id,
                candidate_id=candidate_id,
                decision=payload.decision.value,
                confidence=payload.confidence,
                title=payload.title,
                content=payload.content,
                reason=payload.reason,
                evidence_ranges=[item.model_dump(mode="json") for item in payload.evidence_ranges],
                risk_flags=[item.value for item in payload.risk_flags],
                level=payload.level.value,
                scope=payload.scope.value,
                validation_status="validated",
                review_status="pending",
                auto_publish_allowed=auto_publish_allowed,
                policy_version=policy.policy_version,
            )
            session.add(row)
            run.status = DecisionRunStatus.SUCCEEDED.value
            run.ended_at = _now()
            session.flush()
            self._add_audits(
                session,
                project_id=run.project_id,
                event_type="model_decision_recorded",
                subject_type="model_decision",
                subject_id=str(row.id),
                reason_code="model_output_validated",
                metadata={
                    "decision_run_id": run.id,
                    "decision": row.decision,
                    "level": row.level,
                    "scope": row.scope,
                    "evidence_count": len(row.evidence_ranges),
                    "risk_flag_count": len(row.risk_flags),
                    "auto_publish_allowed": auto_publish_allowed,
                },
            )
            session.commit()
            return row

    def review_decision(
        self,
        decision_id: int,
        *,
        project_id: int,
        reviewer: str,
        action: ReviewAction | str,
        reason: str,
        correction: Mapping[str, Any] | ModelDecisionOutput | None = None,
    ) -> DecisionReviewActionRow:
        """写入不可变人工审核/纠错动作，并保留原始模型决策。"""

        try:
            request = DecisionReviewInput.model_validate(
                {
                    "reviewer": reviewer,
                    "action": action,
                    "reason": reason,
                    "correction": correction,
                }
            )
        except ValidationError as error:
            raise ValueError(f"审核动作无效：{_validation_summary(error)}") from error

        with self.session_factory() as session:
            decision = session.get(ModelDecisionRow, decision_id)
            if decision is None:
                raise LookupError(f"model decision does not exist: {decision_id}")
            if decision.project_id != project_id:
                raise PermissionError("decision 不属于当前项目")
            correction_payload = request.correction
            if correction_payload is not None:
                policy = self._load_policy(session, project_id)
                errors = self._validate_database_constraints(session, project_id, correction_payload, policy)
                if errors:
                    self._add_audits(
                        session,
                        project_id=project_id,
                        event_type="decision_review_rejected",
                        subject_type="model_decision",
                        subject_id=str(decision_id),
                        reason_code="invalid_correction",
                        metadata={"error_count": len(errors)},
                    )
                    session.commit()
                    raise DecisionValidationError("人工纠正未通过项目与证据校验", code="invalid_correction")
            action_value = request.action.value
            row = DecisionReviewActionRow(
                project_id=project_id,
                decision_id=decision_id,
                reviewer=request.reviewer,
                action=action_value,
                reason=request.reason,
                correction_json=correction_payload.model_dump(mode="json") if correction_payload is not None else None,
            )
            session.add(row)
            decision.review_status = {
                ReviewAction.APPROVE.value: "approved",
                ReviewAction.REJECT.value: "rejected",
                ReviewAction.CORRECT.value: "corrected",
            }[action_value]
            if action_value in {ReviewAction.REJECT.value, ReviewAction.CORRECT.value}:
                decision.auto_publish_allowed = False
            session.flush()
            self._add_audits(
                session,
                project_id=project_id,
                event_type="decision_reviewed",
                subject_type="model_decision",
                subject_id=str(decision_id),
                reason_code=action_value,
                metadata={
                    "review_action_id": row.id,
                    "reviewer": request.reviewer,
                    "has_correction": correction_payload is not None,
                },
            )
            session.commit()
            return row

    def can_auto_publish(self, decision_id: int, *, project_id: int | None = None) -> bool:
        """返回严格受限的资格结果，不执行发布。"""

        with self.session_factory() as session:
            decision = session.get(ModelDecisionRow, decision_id)
            if decision is None:
                raise LookupError(f"model decision does not exist: {decision_id}")
            if project_id is not None and decision.project_id != project_id:
                raise PermissionError("decision 不属于当前项目")
            if decision.review_status == "rejected":
                return False
            return bool(decision.auto_publish_allowed)

    @staticmethod
    def _require_project(session: Session, project_id: int) -> ProjectRow:
        project = session.get(ProjectRow, project_id)
        if project is None:
            raise LookupError(f"project does not exist: {project_id}")
        return project

    @staticmethod
    def _require_related_project(
        session: Session,
        row_type: type[Any],
        row_id: int | None,
        project_id: int,
        label: str,
    ) -> None:
        if row_id is None:
            return
        row = session.get(row_type, row_id)
        if row is None or row.project_id != project_id:
            raise ValueError(f"{label} 不属于当前项目")

    @staticmethod
    def _policy_values(row: ProjectDecisionPolicyRow | None) -> dict[str, Any]:
        if row is None:
            return ProjectDecisionPolicy().model_dump(mode="json")
        return {
            "enabled": row.enabled,
            "auto_publish_enabled": row.auto_publish_enabled,
            "strategy": row.strategy,
            "allowed_level": row.allowed_level,
            "allowed_scope": row.allowed_scope,
            "min_confidence": row.min_confidence,
            "require_evidence": row.require_evidence,
            "allow_risk_flags": row.allow_risk_flags,
            "max_title_length": row.max_title_length,
            "max_content_chars": row.max_content_chars,
            "max_reason_length": row.max_reason_length,
            "max_evidence_ranges": row.max_evidence_ranges,
            "policy_version": row.policy_version,
            "updated_by": row.updated_by,
        }

    @classmethod
    def _load_policy(cls, session: Session, project_id: int) -> ProjectDecisionPolicy:
        row = session.get(ProjectDecisionPolicyRow, project_id)
        try:
            return ProjectDecisionPolicy.model_validate(cls._policy_values(row))
        except ValidationError as error:
            # 配置被外部写坏时保持 fail-closed，而不是放宽边界。
            raise DecisionValidationError("项目决策策略无效，已拒绝自动路径", code="invalid_policy") from error

    @staticmethod
    def _validate_database_constraints(
        session: Session,
        project_id: int,
        payload: ModelDecisionOutput,
        policy: ProjectDecisionPolicy,
    ) -> list[str]:
        errors: list[str] = []
        if len(payload.title) > policy.max_title_length:
            errors.append("title 超出项目策略长度")
        content_size = len(json.dumps(payload.content, ensure_ascii=False, separators=(",", ":")))
        if content_size > policy.max_content_chars:
            errors.append("content 超出项目策略长度")
        if len(payload.reason) > policy.max_reason_length:
            errors.append("reason 超出项目策略长度")
        if len(payload.evidence_ranges) > policy.max_evidence_ranges:
            errors.append("evidence_ranges 超出项目策略数量")
        if policy.require_evidence and not payload.evidence_ranges:
            errors.append("项目策略要求至少一条 evidence_range")
        errors.extend(DecisionService._validate_evidence_ranges(session, project_id, payload.evidence_ranges))
        return errors

    @staticmethod
    def _validate_evidence_ranges(session: Session, project_id: int, ranges: list[EvidenceRange]) -> list[str]:
        errors: list[str] = []
        for item in ranges:
            message = session.get(MessageRow, item.message_id)
            if message is None:
                errors.append(f"evidence message_id={item.message_id} 不存在")
                continue
            if message.project_id != project_id:
                errors.append(f"evidence message_id={item.message_id} 不属于当前项目")
                continue
            if item.end_char > len(message.content):
                errors.append(f"evidence message_id={item.message_id} 超出内容范围")
                continue
            actual = message.content[item.start_char : item.end_char]
            if actual != item.quote:
                errors.append(f"evidence message_id={item.message_id} quote 不匹配")
            actual_hash = hashlib.sha256(message.content.encode("utf-8")).hexdigest()
            if actual_hash != message.content_hash:
                errors.append(f"evidence message_id={item.message_id} 原始内容哈希不匹配")
        return errors

    @staticmethod
    def _auto_publish_allowed(
        flags: ProjectFeatureFlagRow | None,
        policy: ProjectDecisionPolicy,
        payload: ModelDecisionOutput,
    ) -> bool:
        """自动资格必须同时通过新旧两层开关及硬编码安全边界。"""

        return bool(
            flags is not None
            and getattr(flags, "decision_engine_enabled", False)
            and getattr(flags, "candidate_publish_enabled", False)
            and policy.enabled
            and policy.auto_publish_enabled
            and policy.strategy is DecisionStrategy.AUTO_PUBLISH
            and payload.decision is DecisionAction.PUBLISH
            and payload.level.value == "L1"
            and payload.scope.value == "project"
            and payload.confidence >= policy.min_confidence
            and not payload.risk_flags
            and bool(payload.evidence_ranges)
        )

    @staticmethod
    def _reject_run_in_session(session: Session, run: DecisionRunRow, *, code: str, message: str) -> None:
        run.status = DecisionRunStatus.FAILED.value
        run.ended_at = _now()
        run.error_class = DecisionErrorClass.VALIDATION.value
        run.error_code = code
        run.error_message = message[:4_000]
        DecisionService._add_audits(
            session,
            project_id=run.project_id,
            event_type="model_decision_rejected",
            subject_type="decision_run",
            subject_id=str(run.id),
            reason_code=code,
            metadata={"error_class": run.error_class},
        )

    def _reject_invalid_output(self, run_id: int, code: str) -> None:
        """记录结构校验失败，但不保存未经验证的模型原文。"""

        with self.session_factory() as session:
            run = session.get(DecisionRunRow, run_id)
            if run is None:
                raise LookupError(f"decision run does not exist: {run_id}")
            self._reject_run_in_session(
                session,
                run,
                code=code,
                message="模型输出未通过结构校验，原始输出未持久化",
            )
            session.commit()

    @staticmethod
    def _audit_log(
        *,
        project_id: int,
        event_type: str,
        subject_type: str | None,
        subject_id: str | None,
        reason_code: str | None,
        metadata: Mapping[str, Any],
    ) -> AuditLogRow:
        # 返回 generic audit 行；SecurityAuditRow 由 _add_audits 同步写入。
        return AuditLogRow(
            project_id=project_id,
            event_type=event_type,
            subject_type=subject_type,
            subject_id=subject_id,
            metadata_json=dict(metadata),
        )

    @classmethod
    def _add_audits(
        cls,
        session: Session,
        *,
        project_id: int,
        event_type: str,
        subject_type: str | None,
        subject_id: str | None,
        reason_code: str | None,
        metadata: Mapping[str, Any],
    ) -> None:
        metadata_json = dict(metadata)
        session.add(
            cls._audit_log(
                project_id=project_id,
                event_type=event_type,
                subject_type=subject_type,
                subject_id=subject_id,
                reason_code=reason_code,
                metadata=metadata_json,
            )
        )
        session.add(
            SecurityAuditRow(
                project_id=project_id,
                event_type=event_type,
                subject_type=subject_type,
                subject_id=subject_id,
                reason_code=reason_code,
                metadata_json=metadata_json,
            )
        )


# 便于 Runner/Integration 按领域名称导入。
DecisionRunService = DecisionService


__all__ = ["DecisionRunService", "DecisionService", "DecisionValidationError"]
