from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from .db_models import MemoryCandidateRow, MessageRow, SecurityAuditRow
from .v11_candidates import CandidatePolicyService
from .v11_embedding import EmbeddingProfileService
from .v11_flags import DEFAULT_FEATURE_FLAG_VALUES, ensure_project_feature_flags
from .v11_decision import CandidateDecisionError, CandidateDecisionModel, CandidateDecisionWorker
from .v11_worker import JobClaim
from .v13_handlers import ErrorClassification, HandlerContext, HandlerResult


class PermanentJobError(Exception):
    pass


class V11JobHandlers:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        candidate_decision_model: CandidateDecisionModel | None = None,
        codex_cli_runner: Any | None = None,
        auto_publish_threshold: float | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.candidate_decision_worker = CandidateDecisionWorker(
            session_factory,
            candidate_decision_model,
            runner=codex_cli_runner,
            auto_publish_threshold=auto_publish_threshold,
        )

    def handle(self, claim: JobClaim) -> None:
        self.validate(claim)
        self.execute(claim, HandlerContext())

    def validate(self, claim: JobClaim) -> None:
        if claim.job_type not in {
            "message.appended.v1",
            "memory.candidate_requested.v1",
            "extract_memory_candidate",
            "memory.embedding_requested.v1",
            "generate_embedding",
            "memory.published.v1",
            "publish_memory",
            "memory.reindex_requested.v1",
            "candidate.decision.requested.v1",
            "decide_candidate",
            "parse_document",
            "chunk_document",
            "task.event.received.v1",
        }:
            raise PermanentJobError(f"不支持的任务类型：{claim.job_type}")
        if claim.job_type in {"candidate.decision.requested.v1", "decide_candidate"}:
            try:
                self.candidate_decision_worker.validate(claim)
            except ValueError as error:
                raise PermanentJobError(str(error)) from error

    def execute(self, claim: JobClaim, context: HandlerContext) -> HandlerResult:
        if claim.job_type in {"parse_document", "chunk_document"}:
            self._handle_import_request(claim.payload)
            return HandlerResult()
        if claim.job_type in {"message.appended.v1", "memory.candidate_requested.v1", "extract_memory_candidate"}:
            self._handle_candidate_request(claim.payload)
            return HandlerResult()
        if claim.job_type in {"memory.embedding_requested.v1", "generate_embedding"}:
            self._handle_embedding_request(claim.payload)
            return HandlerResult()
        if claim.job_type in {"memory.published.v1", "publish_memory"}:
            self._handle_publish_request(claim.payload)
            return HandlerResult()
        if claim.job_type in {"candidate.decision.requested.v1", "decide_candidate"}:
            return self.candidate_decision_worker.execute(claim, context)
        if claim.job_type == "memory.reindex_requested.v1":
            return HandlerResult()
        if claim.job_type == "task.event.received.v1":
            from .v14_worker import TaskReportWorker

            return TaskReportWorker(self.session_factory).execute(claim, context)
        raise PermanentJobError(f"不支持的任务类型：{claim.job_type}")

    def compensate(self, claim: JobClaim, error: Exception) -> None:
        return None

    def on_dead(self, claim: JobClaim, error: Exception) -> None:
        if claim.job_type in {"candidate.decision.requested.v1", "decide_candidate"}:
            self.candidate_decision_worker.on_dead(claim, error)

    def classify_error(self, error: Exception) -> ErrorClassification:
        if isinstance(error, CandidateDecisionError):
            return self.candidate_decision_worker.classify_error(error)
        if isinstance(error, PermanentJobError):
            return ErrorClassification(kind="permanent", code="permanent", retryable=False)
        return ErrorClassification(kind="retryable", code="handler_error", retryable=True)

    def _handle_import_request(self, payload: dict[str, Any]) -> None:
        try:
            from .v131_import import KnowledgeImportService
            KnowledgeImportService(self.session_factory).process_import_file(int(payload["import_file_id"]))
        except (KeyError, LookupError, ValueError) as error:
            raise PermanentJobError(str(error)) from error

    def _handle_candidate_request(self, payload: dict[str, Any]) -> None:
        project_id = int(payload["project_id"])
        message_id = int(payload["message_id"])
        with self.session_factory() as session:
            message = session.get(MessageRow, message_id)
            if message is None or message.project_id != project_id:
                raise PermanentJobError("message does not belong to project")
            flags, initialized = ensure_project_feature_flags(session, project_id)
            if initialized:
                session.add(
                    SecurityAuditRow(
                        project_id=project_id,
                        event_type="feature_flags_auto_initialized",
                        subject_type="project",
                        subject_id=str(project_id),
                        reason_code="missing_defaults",
                        metadata_json={"defaults": DEFAULT_FEATURE_FLAG_VALUES},
                    )
                )
                session.commit()
                raise PermanentJobError(
                    "项目功能开关缺失，已自动补齐默认关闭值；请启用 memory_v11_enabled 后重试"
                )
            if not flags.memory_v11_enabled:
                return
            existing = session.scalar(
                select(MemoryCandidateRow).where(
                    MemoryCandidateRow.project_id == project_id,
                    MemoryCandidateRow.source_message_id == message_id,
                    MemoryCandidateRow.task_type == "message_ingestion",
                    MemoryCandidateRow.classifier_version == "rule-v1",
                    MemoryCandidateRow.status != "rejected",
                )
            )
            if existing is not None:
                return
        CandidatePolicyService(self.session_factory).create_candidate(
            project_id=project_id,
            source_message_id=message_id,
            task_type="message_ingestion",
            level="L1",
            scope="project",
            memory_type="conversation",
            title=f"{message.role} message",
            content={"text": message.content, "source": "message.appended.v1"},
            evidence=[(message_id, 0, len(message.content))],
        )

    def _handle_embedding_request(self, payload: dict[str, Any]) -> None:
        try:
            EmbeddingProfileService(self.session_factory).backfill_memory(
                int(payload["project_id"]),
                int(payload["memory_id"]),
                int(payload["profile_id"]),
            )
        except (KeyError, LookupError, ValueError) as error:
            raise PermanentJobError(str(error)) from error

    def _handle_publish_request(self, payload: dict[str, Any]) -> None:
        try:
            CandidatePolicyService(self.session_factory).publish(int(payload["candidate_id"]), automatic=True)
        except (KeyError, LookupError, ValueError) as error:
            raise PermanentJobError(str(error)) from error
