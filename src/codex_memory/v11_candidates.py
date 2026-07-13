from __future__ import annotations

import hashlib
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
    ProjectFeatureFlagRow,
    ProjectRow,
)


class CandidatePolicyService:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

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
        with self.session_factory() as session:
            project = session.get(ProjectRow, project_id)
            source = session.get(MessageRow, source_message_id)
            if project is None or source is None or source.project_id != project_id:
                raise ValueError("source message does not belong to project")
            candidate = MemoryCandidateRow(
                project_id=project_id,
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
                if message is None or message.project_id != project_id:
                    raise ValueError("evidence message does not belong to project")
                if start_char < 0 or end_char <= start_char or end_char > len(message.content):
                    raise ValueError("证据偏移量无效")
                quoted = message.content[start_char:end_char]
                session.add(
                    CandidateEvidenceRow(
                        candidate_id=candidate.id,
                        message_id=message_id,
                        start_char=start_char,
                        end_char=end_char,
                        quoted_text=quoted,
                        content_hash=message.content_hash,
                    )
                )
            session.commit()
            return candidate

    def evaluate(self, candidate_id: int) -> CandidatePolicyResultRow:
        with self.session_factory() as session:
            candidate = session.get(MemoryCandidateRow, candidate_id)
            if candidate is None:
                raise LookupError(f"candidate does not exist: {candidate_id}")
            checks = {
                "schema_valid": candidate.level in {"L1", "L2", "L3"} and isinstance(candidate.content, dict),
                "evidence_valid": self._verify_evidence(session, candidate),
                "scope_valid": candidate.scope in {"project", "global"},
                "project_access_valid": session.get(ProjectRow, candidate.project_id) is not None,
                "conflict_check": candidate.published_memory_id is None,
                "feature_enabled": self._publish_enabled(session, candidate.project_id),
            }
            reasons = [name for name, passed in checks.items() if not passed]
            decision = "publish" if not reasons else "reject"
            result = CandidatePolicyResultRow(
                candidate_id=candidate_id,
                policy_version="policy-v1",
                decision=decision,
                reason_codes=reasons,
                checks=checks,
            )
            session.add(result)
            session.commit()
            return result

    def publish(self, candidate_id: int) -> MemoryRow:
        with self.session_factory() as session:
            candidate = session.get(MemoryCandidateRow, candidate_id)
            if candidate is None:
                raise LookupError(f"candidate does not exist: {candidate_id}")
            checks = {
                "schema_valid": candidate.level in {"L1", "L2", "L3"} and isinstance(candidate.content, dict),
                "evidence_valid": self._verify_evidence(session, candidate),
                "scope_valid": candidate.scope in {"project", "global"},
                "project_access_valid": session.get(ProjectRow, candidate.project_id) is not None,
                "conflict_check": candidate.published_memory_id is None,
                "feature_enabled": self._publish_enabled(session, candidate.project_id),
            }
            if not all(checks.values()):
                reasons = ", ".join(name for name, passed in checks.items() if not passed)
                session.add(
                    CandidatePolicyResultRow(
                        candidate_id=candidate_id,
                        policy_version="policy-v1",
                        decision="reject",
                        reason_codes=[name for name, passed in checks.items() if not passed],
                        checks=checks,
                    )
                )
                session.commit()
                if not checks["evidence_valid"]:
                    raise ValueError("evidence could not be verified")
                raise ValueError(f"candidate rejected: {reasons}")
            memory = MemoryRow(
                project_id=candidate.project_id if candidate.scope == "project" else None,
                level=candidate.level,
                memory_type=candidate.memory_type,
                title=candidate.title,
                content=candidate.content,
                status="published",
                scope=candidate.scope,
                source_kind="candidate",
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
            session.add(
                CandidatePolicyResultRow(
                    candidate_id=candidate_id,
                    policy_version="policy-v1",
                    decision="publish",
                    reason_codes=[],
                    checks=checks,
                )
            )
            session.commit()
            return memory

    @staticmethod
    def _publish_enabled(session: Session, project_id: int) -> bool:
        flags = session.get(ProjectFeatureFlagRow, project_id)
        return bool(flags is not None and flags.candidate_publish_enabled)

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