from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Index, JSON, BigInteger, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, func
from pgvector.sqlalchemy import Vector
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


IdType = BigInteger().with_variant(Integer, "sqlite")
EmbeddingType = Vector(1536).with_variant(JSON, "sqlite")


class TimestampedRow:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class ProjectRow(TimestampedRow, Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(IdType, primary_key=True)
    project_key: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    name: Mapped[str | None] = mapped_column(String(200))
    repository: Mapped[str | None] = mapped_column(String(500))
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")


class SessionRow(Base):
    __tablename__ = "sessions"
    __table_args__ = (UniqueConstraint("project_id", "session_key", name="uq_sessions_project_session_key"),)

    id: Mapped[int] = mapped_column(IdType, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False)
    session_key: Mapped[str] = mapped_column(String(160), nullable=False)
    title: Mapped[str | None] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MessageRow(Base):
    __tablename__ = "messages"
    __table_args__ = (Index("uq_messages_project_event_key", "project_id", "event_key", unique=True),)

    id: Mapped[int] = mapped_column(IdType, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False, index=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("sessions.id", ondelete="RESTRICT"), nullable=False, index=True)
    event_key: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(50), nullable=False, default="hook")
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ingestion_version: Mapped[str] = mapped_column(String(32), nullable=False, default="v1", server_default="v1")
    conflict_status: Mapped[str] = mapped_column(String(20), nullable=False, default="none", server_default="none")


class MemoryRow(TimestampedRow, Base):
    __tablename__ = "memories"

    id: Mapped[int] = mapped_column(IdType, primary_key=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id", ondelete="RESTRICT"), index=True)
    level: Mapped[str] = mapped_column(String(10), nullable=False)
    memory_type: Mapped[str] = mapped_column(String(50), nullable=False)
    title: Mapped[str | None] = mapped_column(String(300))
    content: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="candidate")
    usage_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deprecated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    scope: Mapped[str] = mapped_column(String(20), nullable=False, default="project", server_default="project")
    source_kind: Mapped[str] = mapped_column(String(32), nullable=False, default="rule", server_default="rule")
    review_status: Mapped[str] = mapped_column(String(20), nullable=False, default="accepted", server_default="accepted")


class MemoryEmbeddingRow(Base):
    __tablename__ = "memory_embeddings"

    id: Mapped[int] = mapped_column(IdType, primary_key=True)
    memory_id: Mapped[int] = mapped_column(ForeignKey("memories.id", ondelete="CASCADE"), nullable=False, unique=True)
    embedding: Mapped[list[float]] = mapped_column(EmbeddingType, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class MemorySourceRow(Base):
    __tablename__ = "memory_sources"
    __table_args__ = (UniqueConstraint("memory_id", "message_id", name="uq_memory_sources_memory_message"),)

    id: Mapped[int] = mapped_column(IdType, primary_key=True)
    memory_id: Mapped[int] = mapped_column(ForeignKey("memories.id", ondelete="CASCADE"), nullable=False)
    message_id: Mapped[int] = mapped_column(ForeignKey("messages.id", ondelete="RESTRICT"), nullable=False)


class MemoryRelationRow(Base):
    __tablename__ = "memory_relations"
    __table_args__ = (
        UniqueConstraint("from_memory", "to_memory", "relation", name="uq_memory_relations_edge"),
    )

    id: Mapped[int] = mapped_column(IdType, primary_key=True)
    from_memory: Mapped[int] = mapped_column(ForeignKey("memories.id", ondelete="CASCADE"), nullable=False)
    to_memory: Mapped[int] = mapped_column(ForeignKey("memories.id", ondelete="CASCADE"), nullable=False)
    relation: Mapped[str] = mapped_column(String(50), nullable=False)


class MemoryVersionRow(Base):
    __tablename__ = "memory_versions"

    id: Mapped[int] = mapped_column(IdType, primary_key=True)
    memory_id: Mapped[int] = mapped_column(ForeignKey("memories.id", ondelete="CASCADE"), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class ApiKeyRow(Base):
    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(IdType, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    permissions: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class AuditLogRow(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(IdType, primary_key=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id", ondelete="RESTRICT"), index=True)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    subject_type: Mapped[str | None] = mapped_column(String(50))
    subject_id: Mapped[str | None] = mapped_column(String(100))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

from .v11_models import (
    V11Base,
    CandidateEvidenceRow,
    CandidatePolicyResultRow,
    DocumentChunkRow,
    EmbeddingProfileRow,
    ImportBatchRow,
    JobAttemptRow,
    MemoryCandidateRow,
    MemoryChunkRow,
    MemoryEmbeddingVectorRow,
    MemorySearchDocumentRow,
    OutboxEventRow,
    ProcessingJobRow,
    ProjectFeatureFlagRow,
    ProjectProcessingPolicyRow,
    ProjectRetrievalProfileRow,
    ReferenceCandidateRow,
    RetrievalAuditRow,
    SourceDocumentRow,
    SecurityAuditRow,
    WorkerInstanceRow,
)
