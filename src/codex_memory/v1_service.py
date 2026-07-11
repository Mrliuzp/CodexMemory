from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from .auth import Principal, require_permission, require_project_access
from .db_models import AuditLogRow, MemoryRow, MessageRow, ProjectRow, SessionRow


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
