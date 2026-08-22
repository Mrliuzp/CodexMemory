from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from .db_models import (
    CandidateEvidenceRow,
    CandidatePolicyResultRow,
    MemoryCandidateRow,
    MemoryRow,
    MemorySourceRow,
    MessageRow,
    OutboxEventRow,
    ProjectFeatureFlagRow,
    ProjectRow,
    SecurityAuditRow,
)
from .idempotency import IdempotencyKeyBuilder
from .v11_decision import (
    ACCEPTED_EVENT_TYPE,
    DECISION_EVENT_TYPE,
    DECISION_HANDLER_VERSION,
    CandidateDecision,
    CandidateEvidenceSnapshot,
    CandidateSnapshot,
    DecisionApplication,
    normalize_candidate_decision,
    resolve_l1_auto_publish_threshold,
)
from .v11_decision_policy import CandidateDecisionPolicy, DecisionPolicyProvider, SqlDecisionPolicyProvider
from ..persistence.db_models import MemoryVersionRow
from ..persistence.v17_models import MemoryChangeSetRow, MemoryWindowMessageRow, MemoryWindowRow, ProjectMemoryWindowPolicyRow
from ..v17_models import MemoryChangeSetOutput


class CandidatePolicyService:
    """候选的服务器策略门、发布事件和人工纠错服务。

    该服务不调用模型，也不修改 MessageRow。模型结果只能通过
    ``apply_model_decision`` 进入策略检查，正式 Memory 仍由
    ``candidate.accepted.v1`` 对应的现有发布 Worker 创建。
    """

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        auto_publish_threshold: float | None = None,
        policy_provider: DecisionPolicyProvider | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.auto_publish_threshold = (
            resolve_l1_auto_publish_threshold(auto_publish_threshold)
            if auto_publish_threshold is not None
            else None
        )
        self.policy_provider = policy_provider or SqlDecisionPolicyProvider(session_factory)

    def apply_window_change_set(self, change_set_id: int, *, reviewer: str, reason: str = "人工审核通过") -> MemoryRow | None:
        """仅应用已人工批准且重新通过证据/版本校验的 V1.7 ChangeSet。"""
        with self.session_factory() as session:
            change_set = session.scalar(select(MemoryChangeSetRow).where(MemoryChangeSetRow.id == change_set_id).with_for_update())
            if change_set is None:
                raise LookupError("ChangeSet 不存在")
            if change_set.status == "applied":
                return session.get(MemoryRow, change_set.applied_memory_id) if change_set.applied_memory_id else None
            if change_set.status != "approved":
                raise ValueError("ChangeSet 尚未人工批准")
            policy = session.get(ProjectMemoryWindowPolicyRow, change_set.project_id)
            if policy is None or not policy.enabled or not policy.manual_apply_enabled or policy.mode != "shadow":
                raise ValueError("V1.7 人工应用未启用")
            window = session.get(MemoryWindowRow, change_set.window_id)
            project = session.get(ProjectRow, change_set.project_id)
            if window is None or project is None or window.project_id != change_set.project_id or window.status not in {"sealed", "processing", "completed"}:
                raise ValueError("ChangeSet 项目或窗口无效")
            if window.input_hash != change_set.input_hash:
                raise ValueError("窗口 input_hash 已漂移")
            output = MemoryChangeSetOutput.model_validate(change_set.output_json)
            messages = session.scalars(select(MemoryWindowMessageRow).where(MemoryWindowMessageRow.window_id == window.id)).all()
            allowed_ids = {item.message_id for item in messages}
            for evidence in output.evidence_ranges:
                if evidence.message_id not in allowed_ids:
                    raise ValueError("证据不属于当前窗口")
                message = session.get(MessageRow, evidence.message_id)
                if message is None or message.project_id != project.id or hashlib.sha256(message.content.encode("utf-8")).hexdigest() != message.content_hash or message.content[evidence.start_char:evidence.end_char] != evidence.quote:
                    raise ValueError("证据已漂移或不属于项目")
            if output.operation == "no_change":
                change_set.status = "applied"
                change_set.reviewer = reviewer[:255]
                change_set.review_reason = reason[:2000]
                change_set.reviewed_at = datetime.now(timezone.utc)
                session.commit()
                return None
            if output.operation == "create":
                canonical = json.dumps(output.content or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                existing_memories = session.scalars(select(MemoryRow).where(
                    MemoryRow.project_id == project.id,
                    MemoryRow.level == "L1",
                    MemoryRow.scope == "project",
                    MemoryRow.status == "published",
                    MemoryRow.deprecated.is_(False),
                )).all()
                if any(json.dumps(item.content or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":")) == canonical for item in existing_memories):
                    raise ValueError("create 与现有 L1 重复")
            target = None
            if output.operation == "update":
                if output.target_memory_id not in output.retrieved_memory_ids:
                    raise ValueError("update target 必须出现在检索目标中")
                target = session.scalar(select(MemoryRow).where(MemoryRow.id == output.target_memory_id, MemoryRow.project_id == project.id, MemoryRow.level == "L1", MemoryRow.scope == "project").with_for_update())
                if target is None or int(getattr(target, "revision", 1)) != output.target_revision:
                    raise ValueError("target memory 或 revision 冲突")
                # 旧数据可能没有完整 v1 快照。先在同一事务中补齐当前状态，
                # 再写新版本，确保更新历史可审计且不依赖迁移期全表扫描。
                current_revision = int(getattr(target, "revision", 1))
                snapshot = session.scalar(
                    select(MemoryVersionRow)
                    .where(MemoryVersionRow.memory_id == target.id, MemoryVersionRow.version == current_revision)
                    .order_by(MemoryVersionRow.id)
                )
                if snapshot is None:
                    session.add(MemoryVersionRow(
                        memory_id=target.id,
                        version=current_revision,
                        content=dict(target.content or {}),
                        title=target.title,
                        level=target.level,
                        memory_type=target.memory_type,
                        scope=target.scope,
                        scope_id=target.scope_id,
                        confidence=target.confidence,
                        status=target.status,
                        deprecated=target.deprecated,
                        content_hash=hashlib.sha256(json.dumps(target.content or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest(),
                    ))
                elif snapshot.title is None or snapshot.level is None or snapshot.memory_type is None or snapshot.scope is None or snapshot.content_hash is None:
                    # 已存在的旧快照也必须补成完整状态；不重复插入同一版本。
                    snapshot.content = dict(target.content or {})
                    snapshot.title = target.title
                    snapshot.level = target.level
                    snapshot.memory_type = target.memory_type
                    snapshot.scope = target.scope
                    snapshot.scope_id = target.scope_id
                    snapshot.confidence = target.confidence
                    snapshot.status = target.status
                    snapshot.deprecated = target.deprecated
                    snapshot.content_hash = hashlib.sha256(json.dumps(target.content or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
            content = dict(output.content or {})
            content_text = json.dumps(content, ensure_ascii=False, sort_keys=True)
            if any(evidence.quote not in content_text for evidence in output.evidence_ranges):
                raise ValueError("证据未被 ChangeSet 内容引用")
            content_hash = hashlib.sha256(json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
            if target is None:
                target = MemoryRow(project_id=project.id, level="L1", scope="project", memory_type="conversation", title=output.title, content=content, confidence=output.confidence or 0.0, status="published", review_status="accepted", source_kind="rule", revision=1)
                session.add(target)
                session.flush()
                version = 1
            else:
                version = int(getattr(target, "revision", 1)) + 1
                target.title = output.title
                target.content = content
                target.confidence = output.confidence or 0.0
                target.status = "published"
                target.revision = version
            for evidence in output.evidence_ranges:
                exists = session.scalar(select(MemorySourceRow).where(MemorySourceRow.memory_id == target.id, MemorySourceRow.message_id == evidence.message_id))
                if exists is None:
                    session.add(MemorySourceRow(memory_id=target.id, message_id=evidence.message_id))
            existing_version = session.scalar(
                select(MemoryVersionRow)
                .where(MemoryVersionRow.memory_id == target.id, MemoryVersionRow.version == version)
                .order_by(MemoryVersionRow.id)
            )
            if existing_version is None:
                session.add(MemoryVersionRow(memory_id=target.id, version=version, content=content, title=target.title, level=target.level, memory_type=target.memory_type, scope=target.scope, scope_id=target.scope_id, confidence=target.confidence, status=target.status, deprecated=target.deprecated, content_hash=content_hash, source_change_set_id=change_set.id))
            elif existing_version.source_change_set_id not in (None, change_set.id):
                raise ValueError("目标 Memory 版本已被其他 ChangeSet 占用")
            change_set.status = "applied"
            change_set.applied_memory_id = target.id
            change_set.reviewer = reviewer[:255]
            change_set.review_reason = reason[:2000]
            change_set.reviewed_at = datetime.now(timezone.utc)
            session.commit()
            session.refresh(target)
            session.expunge(target)
            return target

    def create_candidate(
        self,
        *,
        project_id: int,
        source_message_id: int,
        task_type: str,
        level: str,
        scope: str,
        memory_type: str,
        title: str,
        content: dict[str, Any],
        evidence: list[tuple[int, int, int]],
        ) -> MemoryCandidateRow:
        if scope not in {"project", "global"}:
            raise ValueError("scope 必须是 project 或 global")
        if level not in {"L1", "L2", "L3"}:
            raise ValueError("level 必须是 L1、L2 或 L3")
        if not isinstance(content, dict):
            raise ValueError("候选 content 必须是对象")
        with self.session_factory() as session:
            project = session.get(ProjectRow, project_id)
            source = session.get(MessageRow, source_message_id)
            if project is None or source is None or source.project_id != project_id:
                raise ValueError("source message does not belong to project")
            candidate = self._create_candidate_in_session(
                session,
                project=project,
                source_message_id=source_message_id,
                task_type=task_type,
                level=level,
                scope=scope,
                memory_type=memory_type,
                title=title,
                content=content,
                evidence=evidence,
            )
            self._queue_decision_event(session, project, candidate)
            self._audit(
                session,
                project_id,
                "candidate_created",
                "candidate",
                str(candidate.id),
                "decision_queued",
                {"task_type": task_type, "level": level, "scope": scope},
            )
            session.commit()
            return candidate

    def get_candidate_snapshot(self, candidate_id: int) -> CandidateSnapshot:
        with self.session_factory() as session:
            candidate = session.get(MemoryCandidateRow, candidate_id)
            if candidate is None:
                raise LookupError(f"candidate does not exist: {candidate_id}")
            project = session.get(ProjectRow, candidate.project_id)
            if project is None:
                raise LookupError("candidate project does not exist")
            evidence = tuple(
                CandidateEvidenceSnapshot(
                    message_id=row.message_id,
                    start_char=row.start_char,
                    end_char=row.end_char,
                    quoted_text=row.quoted_text,
                    content_hash=row.content_hash,
                )
                for row in session.scalars(
                    select(CandidateEvidenceRow).where(CandidateEvidenceRow.candidate_id == candidate.id)
                ).all()
            )
            return CandidateSnapshot(
                candidate_id=candidate.id,
                project_id=candidate.project_id,
                level=candidate.level,
                scope=candidate.scope,
                memory_type=candidate.memory_type,
                title=candidate.title,
                content=dict(candidate.content or {}),
                source_message_id=candidate.source_message_id,
                evidence=evidence,
                project_key=project.project_key,
            )

    def begin_model_decision(self, candidate_id: int) -> None:
        """进入 validating；重试期间不把候选误报为成功。"""

        with self.session_factory() as session:
            candidate = session.get(MemoryCandidateRow, candidate_id)
            if candidate is None:
                raise LookupError(f"candidate does not exist: {candidate_id}")
            if candidate.status in {"generated", "needs_review", "validating"}:
                candidate.status = "validating"
            session.commit()

    def get_candidate_status(self, candidate_id: int) -> str:
        with self.session_factory() as session:
            candidate = session.get(MemoryCandidateRow, candidate_id)
            if candidate is None:
                raise LookupError(f"candidate does not exist: {candidate_id}")
            return str(candidate.status)

    def decision_engine_enabled(self, candidate_id: int) -> bool:
        """读取明确的项目决策开关；缺记录或缺字段一律视为关闭。"""

        with self.session_factory() as session:
            candidate = session.get(MemoryCandidateRow, candidate_id)
            if candidate is None:
                raise LookupError(f"candidate does not exist: {candidate_id}")
            flags = session.get(ProjectFeatureFlagRow, candidate.project_id)
            return bool(flags is not None and getattr(flags, "decision_engine_enabled", False))

    def evaluate(self, candidate_id: int) -> CandidatePolicyResultRow:
        """保留 V1.1 的显式策略检查入口，不触发发布。"""

        with self.session_factory() as session:
            candidate = session.get(MemoryCandidateRow, candidate_id)
            if candidate is None:
                raise LookupError(f"candidate does not exist: {candidate_id}")
            checks = self._base_checks(session, candidate)
            reasons = [name for name, passed in checks.items() if not passed]
            decision = "publish" if not reasons else "reject"
            result = self._policy_result(session, candidate_id, decision, reasons, checks)
            session.commit()
            return result

    def apply_model_decision(
        self,
        candidate_id: int,
        decision: CandidateDecision,
    ) -> DecisionApplication:
        """应用模型建议；只有完整通过服务器门禁才入队 accepted 事件。"""

        decision = normalize_candidate_decision(decision)
        with self.session_factory() as session:
            candidate = session.get(MemoryCandidateRow, candidate_id)
            if candidate is None:
                raise LookupError(f"candidate does not exist: {candidate_id}")
            project = session.get(ProjectRow, candidate.project_id)
            if project is None:
                raise LookupError("candidate project does not exist")

            if candidate.published_memory_id is not None:
                result = DecisionApplication(candidate_id, "skip", "published", ("already_published",))
                session.commit()
                return result
            if candidate.status in {"rejected", "superseded"}:
                result = DecisionApplication(candidate_id, "skip", candidate.status, ("candidate_closed",))
                session.commit()
                return result

            candidate.model = decision.model
            candidate.prompt_version = decision.prompt_version
            candidate.model_confidence = decision.confidence
            candidate.abstain = decision.abstain

            if decision.decision == "skip":
                candidate.status = "rejected"
                reason_codes = tuple(decision.reason_codes) or ("model_skip",)
                policy = self._policy_result(session, candidate_id, "skip", reason_codes, {"model_decision": True})
                self._audit(session, candidate.project_id, "candidate_skipped", "candidate", str(candidate.id), reason_codes[0], {"model": decision.model})
                session.commit()
                return DecisionApplication(candidate_id, "skip", candidate.status, reason_codes, policy.id)

            if decision.decision == "needs_review":
                candidate.status = "needs_review"
                reason_codes = tuple(decision.reason_codes) or ("model_needs_review",)
                policy = self._policy_result(session, candidate_id, "needs_review", reason_codes, {"model_decision": True})
                self._audit(session, candidate.project_id, "candidate_needs_review", "candidate", str(candidate.id), reason_codes[0], {"model": decision.model})
                session.commit()
                return DecisionApplication(candidate_id, "needs_review", candidate.status, reason_codes, policy.id)

            checks = self._automatic_checks(session, candidate, decision)
            reasons = tuple(name for name, passed in checks.items() if not passed)
            duplicate_memory = self._has_duplicate_memory(session, candidate)
            duplicate_candidate = self._has_duplicate_candidate(session, candidate)
            if not checks["conflict_check"] and (duplicate_memory or duplicate_candidate):
                candidate.status = "superseded"
                reason_codes = ("duplicate_memory" if duplicate_memory else "duplicate_candidate",)
                policy = self._policy_result(session, candidate_id, "skip", reason_codes, checks)
                self._audit(session, candidate.project_id, "candidate_skipped", "candidate", str(candidate.id), reason_codes[0], {"model": decision.model})
                session.commit()
                return DecisionApplication(candidate_id, "skip", candidate.status, reason_codes, policy.id)

            if reasons:
                candidate.status = "needs_review"
                policy = self._policy_result(session, candidate_id, "needs_review", reasons, checks)
                self._audit(session, candidate.project_id, "candidate_needs_review", "candidate", str(candidate.id), reasons[0], {"model": decision.model})
                session.commit()
                return DecisionApplication(candidate_id, "needs_review", candidate.status, reasons, policy.id)

            candidate.status = "approved"
            policy = self._policy_result(session, candidate_id, "approve", (), checks)
            accepted_event_id = self._queue_accepted_event(session, project, candidate)
            self._audit(
                session,
                candidate.project_id,
                "candidate_auto_publish_queued",
                "candidate",
                str(candidate.id),
                "l1_high_confidence",
                {"model": decision.model, "confidence": decision.confidence, "event_id": accepted_event_id},
            )
            session.commit()
            return DecisionApplication(candidate_id, "accepted", candidate.status, (), policy.id, accepted_event_id)

    def publish(self, candidate_id: int, *, automatic: bool = False) -> MemoryRow:
        """创建正式 Memory；自动路径再次执行服务器策略门。"""

        with self.session_factory() as session:
            candidate = session.get(MemoryCandidateRow, candidate_id)
            if candidate is None:
                raise LookupError(f"candidate does not exist: {candidate_id}")
            if candidate.published_memory_id is not None:
                memory = session.get(MemoryRow, candidate.published_memory_id)
                if memory is not None:
                    return memory
            checks = self._base_checks(session, candidate)
            if automatic:
                policy = self._decision_policy(candidate.project_id)
                latest_policy = session.scalar(
                    select(CandidatePolicyResultRow)
                    .where(CandidatePolicyResultRow.candidate_id == candidate_id)
                    .order_by(CandidatePolicyResultRow.id.desc())
                )
                checks.update(
                    {
                        "level_allowed": candidate.level == "L1",
                        "scope_project": candidate.scope == "project",
                        "candidate_approved": candidate.status == "approved",
                        "feature_enabled": self._auto_publish_enabled(session, candidate.project_id),
                        "policy_enabled": policy.enabled and policy.auto_publish_enabled,
                        "policy_strategy": policy.strategy == "auto_publish",
                        "policy_level": policy.allowed_level == "L1",
                        "policy_scope": policy.allowed_scope == "project",
                        "confidence_threshold": candidate.model_confidence is not None
                        and candidate.model_confidence >= self._threshold_for(policy),
                        "model_not_abstained": not bool(candidate.abstain),
                        "decision_approved": bool(
                            latest_policy is not None
                            and latest_policy.decision == "approve"
                            and all(bool(value) for value in (latest_policy.checks or {}).values())
                        ),
                    }
                )
            if not all(checks.values()):
                reasons = [name for name, passed in checks.items() if not passed]
                if automatic and candidate.status not in {"rejected", "superseded", "published"}:
                    candidate.status = "needs_review"
                self._policy_result(session, candidate_id, "reject", reasons, checks)
                if automatic:
                    self._audit(session, candidate.project_id, "candidate_needs_review", "candidate", str(candidate.id), reasons[0], {"automatic": True})
                session.commit()
                if not checks["evidence_valid"]:
                    raise ValueError("evidence could not be verified")
                raise ValueError(f"candidate rejected: {', '.join(reasons)}")

            memory = MemoryRow(
                project_id=candidate.project_id if candidate.scope == "project" else None,
                level=candidate.level,
                memory_type=candidate.memory_type,
                title=candidate.title,
                content=candidate.content,
                confidence=float(candidate.model_confidence or 0.5),
                status="published",
                scope=candidate.scope,
                source_kind="llm" if candidate.model else "rule",
                review_status="accepted",
            )
            session.add(memory)
            session.flush()
            for evidence in session.scalars(
                select(CandidateEvidenceRow).where(CandidateEvidenceRow.candidate_id == candidate_id)
            ).all():
                session.add(MemorySourceRow(memory_id=memory.id, message_id=evidence.message_id))
            candidate.status = "published"
            candidate.published_memory_id = memory.id
            self._policy_result(session, candidate_id, "publish", (), checks)
            self._audit(
                session,
                candidate.project_id,
                "candidate_auto_published" if automatic else "candidate_published",
                "candidate",
                str(candidate.id),
                "worker_publish" if automatic else "explicit_publish",
                {"memory_id": memory.id, "automatic": automatic},
            )
            session.commit()
            return memory

    def record_model_failure(self, candidate_id: int, error_code: str, message: str, attempt_no: int) -> None:
        with self.session_factory() as session:
            candidate = session.get(MemoryCandidateRow, candidate_id)
            if candidate is None:
                return
            self._audit(
                session,
                candidate.project_id,
                "candidate_model_failed",
                "candidate",
                str(candidate_id),
                error_code,
                {"attempt_no": attempt_no, "message": message[:500]},
            )
            session.commit()

    def mark_model_failure_needs_review(self, candidate_id: int, error_code: str, reason: str) -> None:
        with self.session_factory() as session:
            candidate = session.get(MemoryCandidateRow, candidate_id)
            if candidate is None or candidate.status in {"published", "superseded", "rejected"}:
                return
            candidate.status = "needs_review"
            codes = ("model_failed_after_retries", error_code)
            policy = self._policy_result(session, candidate_id, "needs_review", codes, {"model_failed": True})
            self._audit(
                session,
                candidate.project_id,
                "candidate_needs_review",
                "candidate",
                str(candidate_id),
                "model_failed_after_retries",
                {"error_code": error_code, "reason": reason[:500], "policy_result_id": policy.id},
            )
            session.commit()

    def correct(
        self,
        candidate_id: int,
        *,
        action: str,
        reviewer: str,
        reason: str,
        replacement: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """执行可审计人工纠错，不允许修改 L0 消息。"""

        action = action.strip().lower()
        if action not in {"discard", "reject", "supersede", "replace"}:
            raise ValueError("人工纠错 action 必须是 discard、reject、supersede 或 replace")
        if not reviewer.strip() or not reason.strip():
            raise ValueError("人工纠错必须提供 reviewer 和 reason")
        with self.session_factory() as session:
            candidate = session.get(MemoryCandidateRow, candidate_id)
            if candidate is None:
                raise LookupError(f"candidate does not exist: {candidate_id}")
            project = session.get(ProjectRow, candidate.project_id)
            if project is None:
                raise LookupError("candidate project does not exist")
            if action in {"discard", "reject"} and candidate.published_memory_id is not None:
                raise ValueError("已发布候选必须使用 supersede 或 replace")

            replacement_id: int | None = None
            if action == "replace":
                replacement_payload = dict(replacement or {})
                source_message_id = int(replacement_payload.get("source_message_id") or candidate.source_message_id or 0)
                content = replacement_payload.get("content", candidate.content)
                if not isinstance(content, dict):
                    raise ValueError("替代版本 content 必须是对象")
                evidence = self._replacement_evidence(session, candidate, replacement_payload.get("evidence"))
                replacement_row = self._create_candidate_in_session(
                    session,
                    project=project,
                    source_message_id=source_message_id,
                    task_type=str(replacement_payload.get("task_type") or candidate.task_type),
                    level=str(replacement_payload.get("level") or candidate.level),
                    scope=str(replacement_payload.get("scope") or candidate.scope),
                    memory_type=str(replacement_payload.get("memory_type") or candidate.memory_type),
                    title=str(replacement_payload.get("title") or candidate.title or "替代候选"),
                    content=content,
                    evidence=evidence,
                )
                self._queue_decision_event(session, project, replacement_row)
                self._audit(
                    session,
                    candidate.project_id,
                    "candidate_replacement_created",
                    "candidate",
                    str(replacement_row.id),
                    "human_replace",
                    {
                        "supersedes_candidate_id": candidate.id,
                        "source_message_id": replacement_row.source_message_id,
                        "version": replacement_row.created_at.isoformat() if replacement_row.created_at else None,
                    },
                )
                replacement_id = replacement_row.id

            if action in {"supersede", "replace"}:
                candidate.status = "superseded"
                if candidate.published_memory_id is not None:
                    memory = session.get(MemoryRow, candidate.published_memory_id)
                    if memory is not None:
                        memory.review_status = "superseded"
                        memory.deprecated = True
            else:
                candidate.status = "rejected"
            policy_decision = "supersede" if action in {"supersede", "replace"} else "reject"
            policy = self._policy_result(
                session,
                candidate_id,
                policy_decision,
                (f"human_{action}",),
                {"human_reviewed": True, "l0_immutable": True},
                reviewer=reviewer,
                reason=reason,
            )
            self._audit(
                session,
                candidate.project_id,
                "candidate_human_correction",
                "candidate",
                str(candidate_id),
                f"human_{action}",
                {"reviewer": reviewer, "reason": reason[:500], "replacement_candidate_id": replacement_id, "l0_immutable": True},
            )
            session.commit()
            return {
                "candidate_id": candidate_id,
                "status": candidate.status,
                "action": action,
                "replacement_candidate_id": replacement_id,
                "policy_result_id": policy.id,
            }

    def _create_candidate_in_session(
        self,
        session: Session,
        *,
        project: ProjectRow,
        source_message_id: int,
        task_type: str,
        level: str,
        scope: str,
        memory_type: str,
        title: str,
        content: dict[str, Any],
        evidence: Sequence[tuple[int, int, int]],
    ) -> MemoryCandidateRow:
        if scope not in {"project", "global"}:
            raise ValueError("scope 必须是 project 或 global")
        if level not in {"L1", "L2", "L3"}:
            raise ValueError("level 必须是 L1、L2 或 L3")
        source = session.get(MessageRow, source_message_id)
        if source is None or source.project_id != project.id:
            raise ValueError("source message does not belong to project")
        candidate = MemoryCandidateRow(
            project_id=project.id,
            source_message_id=source_message_id,
            task_type=task_type,
            level=level,
            scope=scope,
            memory_type=memory_type,
            title=title,
            content=content,
            classifier_version="rule-v1",
            status="generated",
        )
        session.add(candidate)
        session.flush()
        for message_id, start_char, end_char in evidence:
            message = session.get(MessageRow, message_id)
            if message is None or message.project_id != project.id:
                raise ValueError("evidence message does not belong to project")
            if start_char < 0 or end_char <= start_char or end_char > len(message.content):
                raise ValueError("证据偏移量无效")
            session.add(
                CandidateEvidenceRow(
                    candidate_id=candidate.id,
                    message_id=message_id,
                    start_char=start_char,
                    end_char=end_char,
                    quoted_text=message.content[start_char:end_char],
                    content_hash=message.content_hash,
                )
            )
        return candidate

    @staticmethod
    def _replacement_evidence(session: Session, candidate: MemoryCandidateRow, value: Any) -> list[tuple[int, int, int]]:
        if value is None:
            return [
                (row.message_id, row.start_char, row.end_char)
                for row in session.scalars(select(CandidateEvidenceRow).where(CandidateEvidenceRow.candidate_id == candidate.id)).all()
            ]
        if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
            raise ValueError("替代版本 evidence 必须是数组")
        output: list[tuple[int, int, int]] = []
        for item in value:
            if isinstance(item, Mapping):
                try:
                    output.append((int(item["message_id"]), int(item["start_char"]), int(item["end_char"])))
                except (KeyError, TypeError, ValueError) as error:
                    raise ValueError("替代版本 evidence 字段无效") from error
            elif isinstance(item, Sequence) and len(item) == 3:
                try:
                    output.append((int(item[0]), int(item[1]), int(item[2])))
                except (TypeError, ValueError) as error:
                    raise ValueError("替代版本 evidence 字段无效") from error
            else:
                raise ValueError("替代版本 evidence 字段无效")
        return output

    def _base_checks(self, session: Session, candidate: MemoryCandidateRow) -> dict[str, bool]:
        return {
            "schema_valid": candidate.level in {"L1", "L2", "L3"} and isinstance(candidate.content, dict),
            "evidence_valid": self._verify_evidence(session, candidate),
            "scope_valid": candidate.scope in {"project", "global"},
            "project_access_valid": session.get(ProjectRow, candidate.project_id) is not None,
            "conflict_check": candidate.published_memory_id is None and not self._has_duplicate_candidate(session, candidate),
            "feature_enabled": self._publish_enabled(session, candidate.project_id),
        }

    def _automatic_checks(self, session: Session, candidate: MemoryCandidateRow, decision: CandidateDecision) -> dict[str, bool]:
        policy = self._decision_policy(candidate.project_id)
        return {
            "schema_valid": candidate.level in {"L1", "L2", "L3"} and isinstance(candidate.content, dict),
            "evidence_valid": self._verify_evidence(session, candidate),
            "scope_valid": candidate.scope == "project",
            "project_access_valid": session.get(ProjectRow, candidate.project_id) is not None,
            "conflict_check": candidate.published_memory_id is None
            and not self._has_duplicate_memory(session, candidate)
            and not self._has_duplicate_candidate(session, candidate),
            "feature_enabled": self._auto_publish_enabled(session, candidate.project_id),
            "policy_enabled": policy.enabled and policy.auto_publish_enabled,
            "policy_strategy": policy.strategy == "auto_publish",
            "policy_level": policy.allowed_level == "L1",
            "policy_scope": policy.allowed_scope == "project",
            "level_allowed": candidate.level == "L1",
            "model_level_allowed": decision.metadata.get("level") == candidate.level == "L1",
            "model_scope_allowed": decision.metadata.get("scope") == candidate.scope == "project",
            "model_evidence_valid": self._verify_model_evidence(session, candidate, decision.metadata.get("evidence_ranges")),
            "confidence_threshold": decision.confidence is not None and decision.confidence >= self._threshold_for(policy),
            "model_not_abstained": not decision.abstain,
            "risk_check": not bool(decision.metadata.get("risk_flags") or decision.metadata.get("risk") or decision.metadata.get("validation_errors")),
            "model_validation": decision.metadata.get("validation_passed", True) is not False,
            "model_publish_requested": decision.decision == "publish",
        }

    @staticmethod
    def _publish_enabled(session: Session, project_id: int) -> bool:
        flags = session.get(ProjectFeatureFlagRow, project_id)
        return bool(flags is not None and flags.candidate_publish_enabled)

    @staticmethod
    def _auto_publish_enabled(session: Session, project_id: int) -> bool:
        flags = session.get(ProjectFeatureFlagRow, project_id)
        # candidate_publish_enabled 是项目级发布门；memory_v11_enabled 只控制候选生成，
        # 这样恢复/重放已有候选时不会因为生成开关变化而绕过同一发布策略。
        return bool(
            flags is not None
            and flags.candidate_publish_enabled
            and getattr(flags, "decision_engine_enabled", False)
        )

    def _decision_policy(self, project_id: int) -> CandidateDecisionPolicy:
        return self.policy_provider.get(project_id)

    def _threshold_for(self, policy: CandidateDecisionPolicy) -> float:
        return self.auto_publish_threshold if self.auto_publish_threshold is not None else policy.min_confidence

    @staticmethod
    def _verify_evidence(session: Session, candidate: MemoryCandidateRow) -> bool:
        candidate_text = ""
        if isinstance(candidate.content, dict):
            candidate_text = str(candidate.content.get("text", ""))
        evidence_rows = session.scalars(
            select(CandidateEvidenceRow).where(CandidateEvidenceRow.candidate_id == candidate.id)
        ).all()
        if not evidence_rows:
            return False
        for evidence in evidence_rows:
            message = session.get(MessageRow, evidence.message_id)
            if message is None or message.project_id != candidate.project_id:
                return False
            if hashlib.sha256(message.content.encode("utf-8")).hexdigest() != evidence.content_hash:
                return False
            if evidence.start_char < 0 or evidence.end_char > len(message.content):
                return False
            if message.content[evidence.start_char : evidence.end_char] != evidence.quoted_text:
                return False
            if evidence.quoted_text not in candidate_text:
                return False
        return True

    @staticmethod
    def _verify_model_evidence(session: Session, candidate: MemoryCandidateRow, value: Any) -> bool:
        """验证严格模型输出中的证据仍属于当前项目且哈希/引用未漂移。"""

        if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or not value:
            return False
        for item in value:
            if not isinstance(item, Mapping):
                return False
            try:
                message_id = int(item["message_id"])
                start_char = int(item["start_char"])
                end_char = int(item["end_char"])
                quote = str(item["quote"])
            except (KeyError, TypeError, ValueError):
                return False
            message = session.get(MessageRow, message_id)
            if message is None or message.project_id != candidate.project_id:
                return False
            if start_char < 0 or end_char <= start_char or end_char > len(message.content):
                return False
            if message.content[start_char:end_char] != quote:
                return False
            if hashlib.sha256(message.content.encode("utf-8")).hexdigest() != message.content_hash:
                return False
        return True

    @staticmethod
    def _has_duplicate_memory(session: Session, candidate: MemoryCandidateRow) -> bool:
        content_key = json.dumps(candidate.content or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        rows = session.scalars(
            select(MemoryRow).where(
                MemoryRow.project_id == candidate.project_id,
                MemoryRow.scope == "project",
                MemoryRow.status == "published",
                MemoryRow.level == candidate.level,
                MemoryRow.memory_type == candidate.memory_type,
            )
        ).all()
        return any(json.dumps(row.content or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":")) == content_key for row in rows)

    @staticmethod
    def _has_duplicate_candidate(session: Session, candidate: MemoryCandidateRow) -> bool:
        """阻止同一项目中重复的已批准/处理中候选再次进入 accepted。"""

        content_key = json.dumps(candidate.content or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        rows = session.scalars(
            select(MemoryCandidateRow).where(
                MemoryCandidateRow.project_id == candidate.project_id,
                MemoryCandidateRow.id != candidate.id,
                MemoryCandidateRow.level == candidate.level,
                MemoryCandidateRow.scope == candidate.scope,
                MemoryCandidateRow.memory_type == candidate.memory_type,
                MemoryCandidateRow.status.in_(("validating", "approved", "published")),
            )
        ).all()
        return any(
            json.dumps(row.content or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":")) == content_key
            for row in rows
        )

    @staticmethod
    def _policy_result(
        session: Session,
        candidate_id: int,
        decision: str,
        reason_codes: Sequence[str],
        checks: Mapping[str, Any],
        *,
        reviewer: str | None = None,
        reason: str | None = None,
    ) -> CandidatePolicyResultRow:
        result = CandidatePolicyResultRow(
            candidate_id=candidate_id,
            policy_version=DECISION_HANDLER_VERSION,
            decision=decision,
            reason_codes=list(reason_codes),
            checks=dict(checks),
            reviewer=reviewer,
            reason=reason,
        )
        session.add(result)
        session.flush()
        return result

    @staticmethod
    def _audit(
        session: Session,
        project_id: int,
        event_type: str,
        subject_type: str,
        subject_id: str,
        reason_code: str,
        metadata: Mapping[str, Any],
    ) -> None:
        session.add(
            SecurityAuditRow(
                project_id=project_id,
                event_type=event_type,
                subject_type=subject_type,
                subject_id=subject_id,
                reason_code=reason_code,
                metadata_json=dict(metadata),
            )
        )

    @staticmethod
    def _queue_decision_event(session: Session, project: ProjectRow, candidate: MemoryCandidateRow) -> OutboxEventRow:
        key = IdempotencyKeyBuilder(project.project_key).build(
            "candidate.decision.requested",
            "candidate",
            candidate.id,
            DECISION_HANDLER_VERSION,
        )
        existing = session.scalar(select(OutboxEventRow).where(OutboxEventRow.project_id == project.id, OutboxEventRow.idempotency_key == key))
        if existing is not None:
            return existing
        event = OutboxEventRow(
            project_id=project.id,
            aggregate_type="memory_candidate",
            aggregate_id=candidate.id,
            event_type=DECISION_EVENT_TYPE,
            payload_version="v1",
            idempotency_key=key,
            payload={
                "project_id": project.id,
                "project_key": project.project_key,
                "candidate_id": candidate.id,
                "source_message_id": candidate.source_message_id,
            },
        )
        session.add(event)
        session.flush()
        return event

    @staticmethod
    def _queue_accepted_event(session: Session, project: ProjectRow, candidate: MemoryCandidateRow) -> int:
        key = IdempotencyKeyBuilder(project.project_key).build(
            "candidate.accepted",
            "candidate",
            candidate.id,
            "policy-v1",
        )
        existing = session.scalar(select(OutboxEventRow).where(OutboxEventRow.project_id == project.id, OutboxEventRow.idempotency_key == key))
        if existing is not None:
            return existing.id
        event = OutboxEventRow(
            project_id=project.id,
            aggregate_type="memory_candidate",
            aggregate_id=candidate.id,
            event_type=ACCEPTED_EVENT_TYPE,
            payload_version="v1",
            idempotency_key=key,
            payload={"project_id": project.id, "project_key": project.project_key, "candidate_id": candidate.id},
        )
        session.add(event)
        session.flush()
        return event.id

def apply_window_change_set(session_factory: sessionmaker[Session], change_set_id: int, *, reviewer: str, reason: str = "人工审核通过") -> MemoryRow | None:
    """受限的 V1.7 人工应用函数入口。"""
    return CandidatePolicyService(session_factory).apply_window_change_set(change_set_id, reviewer=reviewer, reason=reason)


__all__ = ["CandidatePolicyService", "apply_window_change_set"]
