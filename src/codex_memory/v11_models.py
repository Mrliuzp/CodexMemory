from __future__ import annotations

from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import Column, Date, JSON, BigInteger, Boolean, DateTime, Float, ForeignKey, Integer, String, Table, Text, UniqueConstraint, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class V11Base(DeclarativeBase):
    pass


IdType = BigInteger().with_variant(Integer, "sqlite")
for _table_name in ("projects", "messages", "memories"):
    Table(_table_name, V11Base.metadata, Column("id", IdType, primary_key=True))

EmbeddingType = Vector().with_variant(JSON, "sqlite")


class V11TimestampedRow:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class ProjectFeatureFlagRow(V11TimestampedRow, V11Base):
    __tablename__ = "project_feature_flags"
    project_id: Mapped[int] = mapped_column(IdType, ForeignKey("projects.id", ondelete="RESTRICT"), primary_key=True)
    memory_v11_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="0")
    server_outbox_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="0")
    lexical_retrieval_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="0")
    dense_retrieval_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="0")
    embedding_profile_v2_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="0")
    llm_shadow_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="0")
    candidate_publish_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="0")


class ProjectProcessingPolicyRow(V11TimestampedRow, V11Base):
    __tablename__ = "project_processing_policies"
    project_id: Mapped[int] = mapped_column(IdType, ForeignKey("projects.id", ondelete="RESTRICT"), primary_key=True)
    remote_embedding_allowed: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="0")
    remote_llm_allowed: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="0")
    redaction_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="1")
    failure_mode: Mapped[str] = mapped_column(String(20), nullable=False, server_default="fail_closed")
    allowed_embedding_providers: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    allowed_llm_providers: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    daily_embedding_token_budget: Mapped[int | None] = mapped_column(Integer, default=0, server_default="0")
    daily_llm_token_budget: Mapped[int | None] = mapped_column(Integer, default=0, server_default="0")
    daily_embedding_token_budget: Mapped[int | None] = mapped_column(Integer, default=0, server_default="0")
    daily_llm_token_budget: Mapped[int | None] = mapped_column(Integer, default=0, server_default="0")
    data_residency_policy: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)


class OutboxEventRow(V11Base):
    __tablename__ = "outbox_events"
    __table_args__ = (__import__("sqlalchemy").Index("ix_outbox_claim", "status", "next_attempt_at", "priority", "created_at", "id"),)
    id: Mapped[int] = mapped_column(IdType, primary_key=True)
    project_id: Mapped[int] = mapped_column(IdType, ForeignKey("projects.id", ondelete="RESTRICT"), index=True)
    aggregate_type: Mapped[str] = mapped_column(String(64))
    aggregate_id: Mapped[int] = mapped_column(IdType)
    event_type: Mapped[str] = mapped_column(String(128))
    payload_version: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    priority: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    status: Mapped[str] = mapped_column(String(20), default="pending", server_default="pending")
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    locked_by: Mapped[str | None] = mapped_column(String(128))
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(64))
    last_error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ProcessingJobRow(V11TimestampedRow, V11Base):
    __tablename__ = "processing_jobs"
    __table_args__ = (
        __import__("sqlalchemy").Index("ix_jobs_claim", "status", "next_attempt_at", "priority", "created_at", "id"),
        __import__("sqlalchemy").Index("ix_jobs_project_status", "project_id", "status", "created_at"),
    )
    id: Mapped[int] = mapped_column(IdType, primary_key=True)
    project_id: Mapped[int] = mapped_column(IdType, ForeignKey("projects.id", ondelete="RESTRICT"), index=True)
    outbox_event_id: Mapped[int | None] = mapped_column(IdType, ForeignKey("outbox_events.id", ondelete="RESTRICT"))
    job_type: Mapped[str] = mapped_column(String(128))
    aggregate_type: Mapped[str] = mapped_column(String(64))
    aggregate_id: Mapped[int] = mapped_column(IdType)
    job_key: Mapped[str] = mapped_column(String(255), unique=True)
    payload_version: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="pending", server_default="pending")
    priority: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    max_attempts: Mapped[int] = mapped_column(Integer, default=5, server_default="5")
    locked_by: Mapped[str | None] = mapped_column(String(128))
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_error_code: Mapped[str | None] = mapped_column(String(64))
    last_error_message: Mapped[str | None] = mapped_column(Text)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class JobAttemptRow(V11Base):
    __tablename__ = "job_attempts"
    id: Mapped[int] = mapped_column(IdType, primary_key=True)
    job_id: Mapped[int] = mapped_column(IdType, ForeignKey("processing_jobs.id", ondelete="CASCADE"), index=True)
    attempt_no: Mapped[int] = mapped_column(Integer)
    worker_id: Mapped[str] = mapped_column(String(128))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    outcome: Mapped[str] = mapped_column(String(20), default="running", server_default="running")
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class MemoryCandidateRow(V11TimestampedRow, V11Base):
    __tablename__ = "memory_candidates"
    id: Mapped[int] = mapped_column(IdType, primary_key=True)
    project_id: Mapped[int] = mapped_column(IdType, ForeignKey("projects.id", ondelete="RESTRICT"), index=True)
    source_message_id: Mapped[int | None] = mapped_column(IdType, ForeignKey("messages.id", ondelete="RESTRICT"))
    task_type: Mapped[str] = mapped_column(String(64))
    level: Mapped[str] = mapped_column(String(10))
    scope: Mapped[str] = mapped_column(String(20), default="project", server_default="project")
    memory_type: Mapped[str] = mapped_column(String(50))
    title: Mapped[str | None] = mapped_column(String(300))
    content: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    model: Mapped[str | None] = mapped_column(String(128))
    prompt_version: Mapped[str | None] = mapped_column(String(64))
    classifier_version: Mapped[str | None] = mapped_column(String(64))
    model_confidence: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(20), default="generated", server_default="generated")
    abstain: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    published_memory_id: Mapped[int | None] = mapped_column(IdType, ForeignKey("memories.id", ondelete="RESTRICT"))


class CandidateEvidenceRow(V11Base):
    __tablename__ = "candidate_evidence"
    id: Mapped[int] = mapped_column(IdType, primary_key=True)
    candidate_id: Mapped[int] = mapped_column(IdType, ForeignKey("memory_candidates.id", ondelete="CASCADE"), index=True)
    message_id: Mapped[int] = mapped_column(IdType, ForeignKey("messages.id", ondelete="RESTRICT"), index=True)
    start_char: Mapped[int] = mapped_column(Integer)
    end_char: Mapped[int] = mapped_column(Integer)
    quoted_text: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))


class CandidatePolicyResultRow(V11Base):
    __tablename__ = "candidate_policy_results"
    id: Mapped[int] = mapped_column(IdType, primary_key=True)
    candidate_id: Mapped[int] = mapped_column(IdType, ForeignKey("memory_candidates.id", ondelete="CASCADE"), index=True)
    policy_version: Mapped[str] = mapped_column(String(64))
    decision: Mapped[str] = mapped_column(String(20))
    reason_codes: Mapped[list[Any]] = mapped_column(JSON, default=list)
    checks: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    reviewer: Mapped[str | None] = mapped_column(String(128))
    reason: Mapped[str | None] = mapped_column(Text)


class EmbeddingProfileRow(V11TimestampedRow, V11Base):
    __tablename__ = "embedding_profiles"
    id: Mapped[int] = mapped_column(IdType, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    provider: Mapped[str] = mapped_column(String(128))
    model: Mapped[str] = mapped_column(String(128))
    model_revision: Mapped[str | None] = mapped_column(String(128))
    dimension: Mapped[int] = mapped_column(Integer)
    similarity_metric: Mapped[str] = mapped_column(String(20), default="cosine", server_default="cosine")
    normalization: Mapped[str] = mapped_column(String(20), default="none", server_default="none")
    query_input_mode: Mapped[str] = mapped_column(String(32), default="default", server_default="default")
    document_input_mode: Mapped[str] = mapped_column(String(32), default="default", server_default="default")
    max_batch_size: Mapped[int] = mapped_column(Integer, default=32, server_default="32")
    max_inputs_per_request: Mapped[int] = mapped_column(Integer, default=32, server_default="32")
    max_tokens_per_input: Mapped[int] = mapped_column(Integer, default=8192, server_default="8192")
    chunker_version: Mapped[str] = mapped_column(String(64))
    content_normalization_version: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), default="draft", server_default="draft")
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ProjectRetrievalProfileRow(V11Base):
    __tablename__ = "project_retrieval_profiles"
    project_id: Mapped[int] = mapped_column(IdType, ForeignKey("projects.id", ondelete="RESTRICT"), primary_key=True)
    active_embedding_profile_id: Mapped[int | None] = mapped_column(IdType, ForeignKey("embedding_profiles.id", ondelete="RESTRICT"))
    canary_embedding_profile_id: Mapped[int | None] = mapped_column(IdType, ForeignKey("embedding_profiles.id", ondelete="RESTRICT"))
    previous_active_embedding_profile_id: Mapped[int | None] = mapped_column(IdType, ForeignKey("embedding_profiles.id", ondelete="RESTRICT"))
    canary_percent: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    rollback_reason: Mapped[str | None] = mapped_column(Text)
    fallback_mode: Mapped[str] = mapped_column(String(20), default="lexical_only", server_default="lexical_only")
    hybrid_search_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    global_result_limit: Mapped[int] = mapped_column(Integer, default=3, server_default="3")


class MemoryChunkRow(V11Base):
    __tablename__ = "memory_chunks"
    id: Mapped[int] = mapped_column(IdType, primary_key=True)
    memory_id: Mapped[int] = mapped_column(IdType, ForeignKey("memories.id", ondelete="CASCADE"), index=True)
    memory_version: Mapped[int] = mapped_column(Integer)
    chunk_index: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    start_char: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    end_char: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    chunker_version: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MemoryEmbeddingVectorRow(V11Base):
    __tablename__ = "memory_embedding_vectors"
    id: Mapped[int] = mapped_column(IdType, primary_key=True)
    project_id: Mapped[int] = mapped_column(IdType, ForeignKey("projects.id", ondelete="RESTRICT"), index=True)
    memory_id: Mapped[int] = mapped_column(IdType, ForeignKey("memories.id", ondelete="CASCADE"), index=True)
    chunk_id: Mapped[int] = mapped_column(IdType, ForeignKey("memory_chunks.id", ondelete="CASCADE"), index=True)
    embedding_profile_id: Mapped[int] = mapped_column(IdType, ForeignKey("embedding_profiles.id", ondelete="RESTRICT"), index=True)
    embedding: Mapped[list[float]] = mapped_column(EmbeddingType)
    dimension: Mapped[int] = mapped_column(Integer)
    content_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MemorySearchDocumentRow(V11Base):
    __tablename__ = "memory_search_documents"
    memory_id: Mapped[int] = mapped_column(IdType, ForeignKey("memories.id", ondelete="CASCADE"), primary_key=True)
    project_id: Mapped[int | None] = mapped_column(IdType, ForeignKey("projects.id", ondelete="RESTRICT"), index=True)
    scope: Mapped[str] = mapped_column(String(20))
    normalized_text: Mapped[str] = mapped_column(Text)
    search_vector: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RetrievalAuditRow(V11Base):
    __tablename__ = "retrieval_audits"
    id: Mapped[int] = mapped_column(IdType, primary_key=True)
    project_id: Mapped[int] = mapped_column(IdType, ForeignKey("projects.id", ondelete="RESTRICT"), index=True)
    query_hash: Mapped[str] = mapped_column(String(64))
    retrieval_mode: Mapped[str] = mapped_column(String(32))
    degraded: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    degraded_reason: Mapped[str | None] = mapped_column(String(128))
    profile_id: Mapped[int | None] = mapped_column(IdType, ForeignKey("embedding_profiles.id", ondelete="RESTRICT"))
    parameters: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    result_ids: Mapped[list[Any]] = mapped_column(JSON, default=list)
    latency_ms: Mapped[int | None] = mapped_column(Integer)


class DailyTokenUsageRow(V11Base):
    __tablename__ = "daily_token_usage"
    __table_args__ = (UniqueConstraint("project_id", "usage_date", "token_type", name="uq_daily_token_project_date_type"),)
    id: Mapped[int] = mapped_column(IdType, primary_key=True)
    project_id: Mapped[int] = mapped_column(IdType, ForeignKey("projects.id", ondelete="RESTRICT"), index=True)
    usage_date: Mapped[str] = mapped_column(Date, nullable=False)
    token_type: Mapped[str] = mapped_column(String(32), nullable=False)
    tokens_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class SecurityAuditRow(V11Base):
    __tablename__ = "security_audits"
    id: Mapped[int] = mapped_column(IdType, primary_key=True)
    project_id: Mapped[int | None] = mapped_column(IdType, ForeignKey("projects.id", ondelete="RESTRICT"), index=True)
    event_type: Mapped[str] = mapped_column(String(64))
    subject_type: Mapped[str | None] = mapped_column(String(64))
    subject_id: Mapped[str | None] = mapped_column(String(128))
    reason_code: Mapped[str | None] = mapped_column(String(64))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
