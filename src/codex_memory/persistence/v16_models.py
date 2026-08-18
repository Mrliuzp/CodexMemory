"""Codex CLI 决策系统 V1.6 的独立持久化模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, CheckConstraint, Column, DateTime, Float, ForeignKey, Index, Integer, JSON, String, Table, Text, UniqueConstraint, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class V16Base(DeclarativeBase):
    """V1.6 专用元数据，避免旧版建表逻辑提前创建决策表。"""


IdType = BigInteger().with_variant(Integer(), "sqlite")

# 决策表只直接依赖 projects.id；其余既有实体通过项目 ID 和服务端校验关联。
Table("projects", V16Base.metadata, Column("id", IdType, primary_key=True))


class V16TimestampedRow:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class ProjectDecisionPolicyRow(V16TimestampedRow, V16Base):
    """项目级决策策略；默认值必须保持安全关闭和 L1/project 边界。"""

    __tablename__ = "project_decision_policies"
    __table_args__ = (
        CheckConstraint("strategy IN ('manual_review', 'auto_publish')", name="ck_decision_policy_strategy"),
        CheckConstraint("allowed_level = 'L1'", name="ck_decision_policy_allowed_level_l1"),
        CheckConstraint("allowed_scope = 'project'", name="ck_decision_policy_allowed_scope_project"),
        CheckConstraint("min_confidence >= 0 AND min_confidence <= 1", name="ck_decision_policy_confidence"),
        CheckConstraint("max_title_length > 0 AND max_title_length <= 300", name="ck_decision_policy_title_length"),
        CheckConstraint("max_content_chars > 0", name="ck_decision_policy_content_length"),
        CheckConstraint("max_reason_length > 0", name="ck_decision_policy_reason_length"),
        CheckConstraint("max_evidence_ranges > 0", name="ck_decision_policy_evidence_count"),
    )

    project_id: Mapped[int] = mapped_column(
        IdType, ForeignKey("projects.id", ondelete="RESTRICT"), primary_key=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    auto_publish_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    strategy: Mapped[str] = mapped_column(String(32), nullable=False, default="manual_review", server_default="manual_review")
    allowed_level: Mapped[str] = mapped_column(String(10), nullable=False, default="L1", server_default="L1")
    allowed_scope: Mapped[str] = mapped_column(String(20), nullable=False, default="project", server_default="project")
    min_confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.8, server_default="0.8")
    require_evidence: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")
    allow_risk_flags: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    max_title_length: Mapped[int] = mapped_column(Integer, nullable=False, default=300, server_default="300")
    max_content_chars: Mapped[int] = mapped_column(Integer, nullable=False, default=12000, server_default="12000")
    max_reason_length: Mapped[int] = mapped_column(Integer, nullable=False, default=2000, server_default="2000")
    max_evidence_ranges: Mapped[int] = mapped_column(Integer, nullable=False, default=16, server_default="16")
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False, default="decision-policy-v1", server_default="decision-policy-v1")
    updated_by: Mapped[str] = mapped_column(String(255), nullable=False, default="system", server_default="system")


class DecisionRunRow(V16TimestampedRow, V16Base):
    """一次模型调用的生命周期、重试、错误和用量记录。"""

    __tablename__ = "decision_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'failed', 'retry_wait', 'cancelled')",
            name="ck_decision_runs_status",
        ),
        CheckConstraint("retry_count >= 0", name="ck_decision_runs_retry_count"),
        CheckConstraint("max_retries >= 0", name="ck_decision_runs_max_retries"),
        CheckConstraint("input_tokens IS NULL OR input_tokens >= 0", name="ck_decision_runs_input_tokens"),
        CheckConstraint("output_tokens IS NULL OR output_tokens >= 0", name="ck_decision_runs_output_tokens"),
        CheckConstraint("total_tokens IS NULL OR total_tokens >= 0", name="ck_decision_runs_total_tokens"),
        CheckConstraint("cost_micros IS NULL OR cost_micros >= 0", name="ck_decision_runs_cost_micros"),
        Index("ix_decision_runs_project_started", "project_id", "started_at", "id"),
        Index("ix_decision_runs_project_status", "project_id", "status", "created_at"),
    )

    id: Mapped[int] = mapped_column(IdType, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        IdType, ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    processing_job_id: Mapped[int | None] = mapped_column(IdType, index=True)
    outbox_event_id: Mapped[int | None] = mapped_column(IdType, index=True)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="running", server_default="running")
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    max_retries: Mapped[int] = mapped_column(Integer, nullable=False, default=3, server_default="3")
    error_class: Mapped[str | None] = mapped_column(String(64))
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    total_tokens: Mapped[int | None] = mapped_column(Integer)
    cost_micros: Mapped[int | None] = mapped_column(BigInteger)
    cost_currency: Mapped[str] = mapped_column(String(8), nullable=False, default="USD", server_default="USD")
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)


class ModelDecisionRow(V16Base):
    """通过服务端契约校验后的结构化模型决策；不负责发布 Memory。"""

    __tablename__ = "model_decisions"
    __table_args__ = (
        UniqueConstraint("decision_run_id", name="uq_model_decisions_run"),
        CheckConstraint("decision IN ('publish', 'skip', 'needs_review')", name="ck_model_decisions_decision"),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_model_decisions_confidence"),
        CheckConstraint("level IN ('L1', 'L2', 'L3')", name="ck_model_decisions_level"),
        CheckConstraint("scope IN ('project', 'global')", name="ck_model_decisions_scope"),
        CheckConstraint("validation_status = 'validated'", name="ck_model_decisions_validation_status"),
        CheckConstraint(
            "review_status IN ('pending', 'approved', 'rejected', 'corrected')",
            name="ck_model_decisions_review_status",
        ),
        Index("ix_model_decisions_project_created", "project_id", "created_at", "id"),
        Index("ix_model_decisions_project_decision", "project_id", "decision", "created_at"),
    )

    id: Mapped[int] = mapped_column(IdType, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        IdType, ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    decision_run_id: Mapped[int] = mapped_column(
        IdType, ForeignKey("decision_runs.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    # candidate_id 由后续 Integration 在候选链路中绑定，决策服务不会创建或发布候选。
    candidate_id: Mapped[int | None] = mapped_column(IdType, index=True)
    decision: Mapped[str] = mapped_column(String(20), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    content: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_ranges: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    risk_flags: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    level: Mapped[str] = mapped_column(String(10), nullable=False, default="L1", server_default="L1")
    scope: Mapped[str] = mapped_column(String(20), nullable=False, default="project", server_default="project")
    validation_status: Mapped[str] = mapped_column(String(20), nullable=False, default="validated", server_default="validated")
    review_status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending", server_default="pending")
    auto_publish_allowed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False, default="decision-policy-v1", server_default="decision-policy-v1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class DecisionReviewActionRow(V16Base):
    """人工审核与纠错的不可变操作日志。"""

    __tablename__ = "decision_review_actions"
    __table_args__ = (
        CheckConstraint(
            "action IN ('approve', 'reject', 'correct')",
            name="ck_decision_review_actions_action",
        ),
        Index("ix_decision_review_actions_project_created", "project_id", "created_at", "id"),
        Index("ix_decision_review_actions_decision_created", "decision_id", "created_at", "id"),
    )

    id: Mapped[int] = mapped_column(IdType, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        IdType, ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    decision_id: Mapped[int] = mapped_column(
        IdType, ForeignKey("model_decisions.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    reviewer: Mapped[str] = mapped_column(String(255), nullable=False)
    action: Mapped[str] = mapped_column(String(20), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    correction_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


__all__ = [
    "V16Base",
    "ProjectDecisionPolicyRow",
    "DecisionRunRow",
    "ModelDecisionRow",
    "DecisionReviewActionRow",
]
