from __future__ import annotations

from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import Column, Date, JSON, BigInteger, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Table, Text, UniqueConstraint, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class V11Base(DeclarativeBase):
    pass


IdType = BigInteger()
for _table_name in ("projects", "messages", "memories"):
    Table(_table_name, V11Base.metadata, Column("id", IdType, primary_key=True))

EmbeddingType = Vector()


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
    async_pipeline_v13_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="0")


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
    __table_args__ = (
        Index("ix_outbox_claim", "status", "next_attempt_at", "priority", "created_at", "id"),
        Index("uq_outbox_project_idempotency", "project_id", "idempotency_key", unique=True),
    )
    id: Mapped[int] = mapped_column(IdType, primary_key=True)
    project_id: Mapped[int] = mapped_column(IdType, ForeignKey("projects.id", ondelete="RESTRICT"), index=True)
    aggregate_type: Mapped[str] = mapped_column(String(64))
    aggregate_id: Mapped[int] = mapped_column(IdType)
    event_type: Mapped[str] = mapped_column(String(128))
    payload_version: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    idempotency_key: Mapped[str | None] = mapped_column(String(255))
    priority: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    status: Mapped[str] = mapped_column(String(20), default="pending", server_default="pending")
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    max_attempts: Mapped[int] = mapped_column(Integer, default=5, server_default="5")
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    locked_by: Mapped[str | None] = mapped_column(String(128))
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(64))
    last_error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    replay_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


class ProcessingJobRow(V11TimestampedRow, V11Base):
    __tablename__ = "processing_jobs"
    __table_args__ = (
        Index("ix_jobs_claim", "status", "next_attempt_at", "priority", "created_at", "id"),
        Index("ix_jobs_project_status", "project_id", "status", "created_at"),
        Index("uq_jobs_project_type_idempotency", "project_id", "job_type", "idempotency_key", unique=True),
    )
    id: Mapped[int] = mapped_column(IdType, primary_key=True)
    project_id: Mapped[int] = mapped_column(IdType, ForeignKey("projects.id", ondelete="RESTRICT"), index=True)
    outbox_event_id: Mapped[int | None] = mapped_column(IdType, ForeignKey("outbox_events.id", ondelete="RESTRICT"))
    job_type: Mapped[str] = mapped_column(String(128))
    source_type: Mapped[str | None] = mapped_column(String(64))
    source_id: Mapped[str | None] = mapped_column(String(255))
    handler_version: Mapped[str | None] = mapped_column(String(128))
    idempotency_key: Mapped[str | None] = mapped_column(String(255))
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
    error_class: Mapped[str | None] = mapped_column(String(64))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_reason: Mapped[str | None] = mapped_column(Text)


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
    error_class: Mapped[str | None] = mapped_column(String(64))
    finished_reason: Mapped[str | None] = mapped_column(String(128))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class WorkerInstanceRow(V11Base):
    __tablename__ = "worker_instances"
    worker_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    role: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    current_job_id: Mapped[int | None] = mapped_column(IdType, ForeignKey("processing_jobs.id", ondelete="SET NULL"))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


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


class ImportFileRow(V11TimestampedRow, V11Base):
    __tablename__ = "import_files"
    __table_args__ = (
        UniqueConstraint("import_batch_id", "content_hash", name="uq_import_files_batch_hash"),
    )
    id: Mapped[int] = mapped_column(IdType, primary_key=True)
    project_id: Mapped[int] = mapped_column(IdType, ForeignKey("projects.id", ondelete="RESTRICT"), index=True)
    scope_id: Mapped[int] = mapped_column(IdType, nullable=False, default=0, server_default="0")
    import_batch_id: Mapped[int] = mapped_column(IdType, ForeignKey("import_batches.id", ondelete="RESTRICT"), index=True)
    source_name: Mapped[str] = mapped_column(String(500), nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_backend: Mapped[str] = mapped_column(String(32), nullable=False, default="database", server_default="database")
    storage_key: Mapped[str | None] = mapped_column(String(500))
    content: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="uploaded", server_default="uploaded")
    parser_version: Mapped[str] = mapped_column(String(64), nullable=False, default="knowledge-import-v1", server_default="knowledge-import-v1")
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    error_message: Mapped[str | None] = mapped_column(Text)


class ImportUploadPartRow(V11TimestampedRow, V11Base):
    """?????????????????"""

    __tablename__ = "import_upload_parts"
    __table_args__ = (
        UniqueConstraint("import_batch_id", "upload_id", "part_number", name="uq_import_upload_parts_part"),
    )

    id: Mapped[int] = mapped_column(IdType, primary_key=True)
    project_id: Mapped[int] = mapped_column(IdType, ForeignKey("projects.id", ondelete="RESTRICT"), index=True)
    scope_id: Mapped[int] = mapped_column(IdType, nullable=False, default=0, server_default="0")
    import_batch_id: Mapped[int] = mapped_column(IdType, ForeignKey("import_batches.id", ondelete="RESTRICT"), index=True)
    upload_id: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    source_name: Mapped[str] = mapped_column(String(500), nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    part_number: Mapped[int] = mapped_column(Integer, nullable=False)
    total_parts: Mapped[int] = mapped_column(Integer, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="uploaded", server_default="uploaded")


class ImportIssueRow(V11TimestampedRow, V11Base):
    __tablename__ = "import_issues"
    id: Mapped[int] = mapped_column(IdType, primary_key=True)
    project_id: Mapped[int] = mapped_column(IdType, ForeignKey("projects.id", ondelete="RESTRICT"), index=True)
    scope_id: Mapped[int] = mapped_column(IdType, nullable=False, default=0, server_default="0")
    import_batch_id: Mapped[int] = mapped_column(IdType, ForeignKey("import_batches.id", ondelete="RESTRICT"), index=True)
    import_file_id: Mapped[int | None] = mapped_column(IdType, ForeignKey("import_files.id", ondelete="RESTRICT"), index=True)
    source_document_id: Mapped[int | None] = mapped_column(IdType, ForeignKey("source_documents.id", ondelete="RESTRICT"), index=True)
    issue_type: Mapped[str] = mapped_column(String(64), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False, default="warning", server_default="warning")
    message: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

class ImportBatchRow(V11TimestampedRow, V11Base):
    __tablename__ = "import_batches"
    id: Mapped[int] = mapped_column(IdType, primary_key=True)
    project_id: Mapped[int] = mapped_column(IdType, ForeignKey("projects.id", ondelete="RESTRICT"), index=True)
    scope_id: Mapped[int] = mapped_column(IdType, nullable=False, default=0, server_default="0")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending", server_default="pending")
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    scope_key: Mapped[str] = mapped_column(String(120), nullable=False, default="project", server_default="project")
    source_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    document_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    error_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    processed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rolled_back_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SourceDocumentRow(V11TimestampedRow, V11Base):
    __tablename__ = "source_documents"
    __table_args__ = (
        UniqueConstraint("project_id", "content_hash", name="uq_source_documents_project_hash"),
    )
    id: Mapped[int] = mapped_column(IdType, primary_key=True)
    project_id: Mapped[int] = mapped_column(IdType, ForeignKey("projects.id", ondelete="RESTRICT"), index=True)
    scope_id: Mapped[int] = mapped_column(IdType, nullable=False, default=0, server_default="0")
    import_batch_id: Mapped[int] = mapped_column(IdType, ForeignKey("import_batches.id", ondelete="RESTRICT"), index=True)
    source_name: Mapped[str] = mapped_column(String(500), nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    parser_version: Mapped[str] = mapped_column(String(64), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="parsed", server_default="parsed")
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    error_message: Mapped[str | None] = mapped_column(Text)


class DocumentChunkRow(V11Base):
    __tablename__ = "document_chunks"
    __table_args__ = (
        UniqueConstraint("document_id", "chunk_index", name="uq_document_chunks_document_index"),
    )
    id: Mapped[int] = mapped_column(IdType, primary_key=True)
    project_id: Mapped[int] = mapped_column(IdType, ForeignKey("projects.id", ondelete="RESTRICT"), index=True)
    scope_id: Mapped[int] = mapped_column(IdType, nullable=False, default=0, server_default="0")
    document_id: Mapped[int] = mapped_column(IdType, ForeignKey("source_documents.id", ondelete="CASCADE"), index=True)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    heading: Mapped[str | None] = mapped_column(String(500))
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    start_char: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    end_char: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)


class ReferenceCandidateRow(V11TimestampedRow, V11Base):
    __tablename__ = "reference_candidates"
    __table_args__ = (
        UniqueConstraint("project_id", "chunk_id", name="uq_reference_candidates_project_chunk"),
    )
    id: Mapped[int] = mapped_column(IdType, primary_key=True)
    project_id: Mapped[int] = mapped_column(IdType, ForeignKey("projects.id", ondelete="RESTRICT"), index=True)
    scope_id: Mapped[int] = mapped_column(IdType, nullable=False, default=0, server_default="0")
    document_id: Mapped[int] = mapped_column(IdType, ForeignKey("source_documents.id", ondelete="RESTRICT"), index=True)
    chunk_id: Mapped[int] = mapped_column(IdType, ForeignKey("document_chunks.id", ondelete="RESTRICT"), index=True)
    title: Mapped[str | None] = mapped_column(String(300))
    content: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending_review", server_default="pending_review")
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.5, server_default="0.5")
    dedupe_key: Mapped[str] = mapped_column(String(128), nullable=False)
    evidence_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    scope_key: Mapped[str] = mapped_column(String(120), nullable=False, default="project", server_default="project")
    published_memory_id: Mapped[int | None] = mapped_column(IdType, ForeignKey("memories.id", ondelete="RESTRICT"), index=True)
    reviewer: Mapped[str | None] = mapped_column(String(160))
    review_reason: Mapped[str | None] = mapped_column(Text)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rolled_back_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


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
