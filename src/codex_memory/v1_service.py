from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from .auth import Principal, require_permission, require_project_access
from .classifier import MemoryClassifier
from .models import Layer, RawLog
from .db_models import AuditLogRow, MemoryRow, MemorySourceRow, MessageRow, ProjectRow, SessionRow


@dataclass(frozen=True)
class AppendResult:
    message_id: int
    status: Literal["stored", "duplicate"]


class V1MemoryService:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def append_message(
        self,
        principal: Principal,
        project_key: str,
        session_key: str,
        event_key: str,
        role: str,
        content: str,
        source: str = "hook",
        metadata: dict[str, Any] | None = None,
    ) -> AppendResult:
        require_permission(principal, "append")
        require_project_access(principal, project_key)
        with self.session_factory() as session:
            project = session.scalar(select(ProjectRow).where(ProjectRow.project_key == project_key))
            if project is None:
                raise LookupError(f"project does not exist: {project_key}")
            existing = session.scalar(select(MessageRow).where(MessageRow.event_key == event_key))
            if existing is not None:
                return AppendResult(message_id=existing.id, status="duplicate")
            conversation = session.scalar(
                select(SessionRow).where(SessionRow.project_id == project.id, SessionRow.session_key == session_key)
            )
            if conversation is None:
                conversation = SessionRow(project_id=project.id, session_key=session_key)
                session.add(conversation)
                session.flush()
            message = MessageRow(
                project_id=project.id,
                session_id=conversation.id,
                event_key=event_key,
                role=role,
                content=content,
                content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
                source=source,
                metadata_json=metadata or {},
            )
            session.add(message)
            session.add(
                AuditLogRow(
                    project_id=project.id,
                    event_type="message_appended",
                    subject_type="message",
                    subject_id=event_key,
                    metadata_json={"role": role, "source": source},
                )
            )
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                duplicate = session.scalar(select(MessageRow).where(MessageRow.event_key == event_key))
                if duplicate is None:
                    raise
                return AppendResult(message_id=duplicate.id, status="duplicate")
            return AppendResult(message_id=message.id, status="stored")

    def create_l1_memory(
        self,
        principal: Principal,
        project_key: str,
        memory_type: str,
        content: dict[str, Any],
        title: str | None = None,
    ) -> MemoryRow:
        require_permission(principal, "memory_write")
        require_project_access(principal, project_key)
        with self.session_factory() as session:
            project = session.scalar(select(ProjectRow).where(ProjectRow.project_key == project_key))
            if project is None:
                raise LookupError(f"project does not exist: {project_key}")
            memory = MemoryRow(
                project_id=project.id,
                level="L1",
                memory_type=memory_type,
                title=title,
                content=content,
            )
            session.add(memory)
            session.add(
                AuditLogRow(
                    project_id=project.id,
                    event_type="l1_memory_created",
                    subject_type="memory",
                    metadata_json={"memory_type": memory_type},
                )
            )
            session.commit()
            return memory
    def _project_memories(self, principal: Principal, project_key: str) -> list[MemoryRow]:
        require_permission(principal, "read")
        require_project_access(principal, project_key)
        with self.session_factory() as session:
            project = session.scalar(select(ProjectRow).where(ProjectRow.project_key == project_key))
            if project is None:
                raise LookupError(f"project does not exist: {project_key}")
            rows = session.scalars(
                select(MemoryRow).where(MemoryRow.project_id == project.id, MemoryRow.deprecated.is_(False))
            ).all()
            session.expunge_all()
            return rows

    @staticmethod
    def _memory_payload(memory: MemoryRow) -> dict[str, Any]:
        return {
            "id": memory.id,
            "level": memory.level,
            "type": memory.memory_type,
            "title": memory.title or "",
            "content": memory.content,
        }

    def build_context(self, principal: Principal, project_key: str, task: str, limit: int = 8) -> dict[str, Any]:
        del task
        rows = self._project_memories(principal, project_key)
        by_level = {"L3": [], "L2": [], "L1": []}
        for row in rows:
            if row.level in by_level:
                by_level[row.level].append(self._memory_payload(row))
        for values in by_level.values():
            values.sort(key=lambda value: value["id"])
        selected = by_level["L3"][:limit] + by_level["L2"][:limit] + by_level["L1"][:limit]
        return {
            "critical_rules": by_level["L3"][:limit],
            "long_term_rules": by_level["L2"][:limit],
            "recent_insights": by_level["L1"][:limit],
            "source_ids": [item["id"] for item in selected],
        }

    def search_memories(self, principal: Principal, project_key: str, query: str, limit: int = 8) -> list[dict[str, Any]]:
        terms = [term for term in query.lower().split() if term]
        matches: list[dict[str, Any]] = []
        for row in self._project_memories(principal, project_key):
            searchable = f"{row.title or ''} {json.dumps(row.content, ensure_ascii=False)}".lower()
            if all(term in searchable for term in terms):
                matches.append(self._memory_payload(row))
        rank = {"L3": 0, "L2": 1, "L1": 2}
        matches.sort(key=lambda item: (rank.get(item["level"], 3), item["id"]))
        return matches[:limit]
    def reflect_project(self, principal: Principal, project_key: str) -> dict[str, int]:
        require_permission(principal, "reflect")
        require_project_access(principal, project_key)
        report = {"processed_messages": 0, "l1_created": 0, "l2_created": 0, "l3_created": 0}
        classifier = MemoryClassifier()

        with self.session_factory() as session:
            project = session.scalar(select(ProjectRow).where(ProjectRow.project_key == project_key))
            if project is None:
                raise LookupError(f"project does not exist: {project_key}")
            processed_ids = set(session.scalars(select(MemorySourceRow.message_id).join(MemoryRow).where(MemoryRow.project_id == project.id)).all())
            messages = session.scalars(select(MessageRow).where(MessageRow.project_id == project.id)).all()
            for message in messages:
                if message.id in processed_ids:
                    continue
                raw = RawLog(message.id, project_key, str(message.session_id), message.role, message.content, message.metadata_json, message.created_at.isoformat(), None)
                for item in classifier.classify([raw]):
                    level = Layer.L1.value if item.layer == Layer.L2 else item.layer.value
                    memory = MemoryRow(
                        project_id=project.id,
                        level=level,
                        memory_type=item.memory_type,
                        title=item.title,
                        content={"text": item.body, "tags": item.tags, "metadata": item.metadata or {}},
                        status="active" if level == Layer.L3.value else "candidate",
                        confidence=1.0 if level == Layer.L3.value else 0.5,
                    )
                    session.add(memory)
                    session.flush()
                    session.add(MemorySourceRow(memory_id=memory.id, message_id=message.id))
                    report[f"{level.lower()}_created"] += 1
                report["processed_messages"] += 1
            session.flush()
            groups: dict[str, dict[str, Any]] = {}
            rows = session.execute(select(MemoryRow, SessionRow.session_key).join(MemorySourceRow).join(MessageRow).join(SessionRow).where(MemoryRow.project_id == project.id, MemoryRow.level == Layer.L1.value)).all()
            for memory, session_key in rows:
                text_key = str(memory.content.get("text", ""))
                group = groups.setdefault(text_key, {"sessions": set(), "memories": []})
                group["sessions"].add(session_key)
                group["memories"].append(memory)
            existing_l2 = {str(row.content.get("text", "")) for row in session.scalars(select(MemoryRow).where(MemoryRow.project_id == project.id, MemoryRow.level == Layer.L2.value)).all()}
            for text_key, group in groups.items():
                if len(group["sessions"]) < 2 or text_key in existing_l2:
                    continue
                source = group["memories"][0]
                knowledge = MemoryRow(project_id=project.id, level=Layer.L2.value, memory_type="knowledge", title=(source.title or "Knowledge").replace("Working:", "Knowledge:", 1), content={**source.content, "validated_sessions": sorted(group["sessions"])}, status="active", confidence=0.9)
                session.add(knowledge)
                session.flush()
                for memory in group["memories"]:
                    source_ids = session.scalars(select(MemorySourceRow.message_id).where(MemorySourceRow.memory_id == memory.id)).all()
                    for message_id in source_ids:
                        session.add(MemorySourceRow(memory_id=knowledge.id, message_id=message_id))
                report["l2_created"] += 1
            session.add(AuditLogRow(project_id=project.id, event_type="reflection_completed", metadata_json=report.copy()))
            session.commit()
        return report
