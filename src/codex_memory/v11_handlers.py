from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from .db_models import MemoryCandidateRow, MessageRow, ProjectFeatureFlagRow
from .v11_candidates import CandidatePolicyService
from .v11_embedding import EmbeddingProfileService
from .v11_worker import JobClaim


class PermanentJobError(Exception):
    pass


class V11JobHandlers:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def handle(self, claim: JobClaim) -> None:
        if claim.job_type in {"message.appended.v1", "memory.candidate_requested.v1"}:
            self._handle_candidate_request(claim.payload)
            return
        if claim.job_type == "memory.embedding_requested.v1":
            self._handle_embedding_request(claim.payload)
            return
        if claim.job_type == "memory.published.v1":
            self._handle_publish_request(claim.payload)
            return
        if claim.job_type == "memory.reindex_requested.v1":
            return
        raise PermanentJobError(f"unsupported job type: {claim.job_type}")

    def _handle_candidate_request(self, payload: dict[str, Any]) -> None:
        project_id = int(payload["project_id"])
        message_id = int(payload["message_id"])
        with self.session_factory() as session:
            flags = session.get(ProjectFeatureFlagRow, project_id)
            message = session.get(MessageRow, message_id)
            if message is None or message.project_id != project_id:
                raise PermanentJobError("message does not belong to project")
            if flags is None or not flags.memory_v11_enabled:
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
            CandidatePolicyService(self.session_factory).publish(int(payload["candidate_id"]))
        except (KeyError, LookupError, ValueError) as error:
            raise PermanentJobError(str(error)) from error