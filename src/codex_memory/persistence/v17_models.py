"""V1.7 Memory Window 与不可变 ChangeSet 持久化模型。"""

from __future__ import annotations
from datetime import datetime
from typing import Any
from sqlalchemy import BigInteger, Boolean, CheckConstraint, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint, func, Table, Column
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

class V17Base(DeclarativeBase):
    """V1.7 独立元数据，避免默认启动时创建新表。"""

IdType = BigInteger().with_variant(Integer(), "sqlite")
Table("projects", V17Base.metadata, Column("id", IdType, primary_key=True))
Table("sessions", V17Base.metadata, Column("id", IdType, primary_key=True))
Table("messages", V17Base.metadata, Column("id", IdType, primary_key=True))
Table("memories", V17Base.metadata, Column("id", IdType, primary_key=True))

class V17TimestampedRow:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

class ProjectMemoryWindowPolicyRow(V17TimestampedRow, V17Base):
    __tablename__ = "project_memory_window_policies"
    __table_args__ = (CheckConstraint("max_messages > 0", name="ck_window_policy_max_messages"), CheckConstraint("max_input_chars > 0", name="ck_window_policy_max_input_chars"), CheckConstraint("mode = 'shadow'", name="ck_window_policy_mode_shadow"))
    project_id: Mapped[int] = mapped_column(IdType, ForeignKey("projects.id", ondelete="RESTRICT"), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    mode: Mapped[str] = mapped_column(String(20), nullable=False, default="shadow", server_default="shadow")
    manual_apply_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    max_messages: Mapped[int] = mapped_column(Integer, nullable=False, default=200, server_default="200")
    max_input_chars: Mapped[int] = mapped_column(Integer, nullable=False, default=120000, server_default="120000")
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False, default="memory-window-v1", server_default="memory-window-v1")
    updated_by: Mapped[str] = mapped_column(String(255), nullable=False, default="system", server_default="system")

class MemoryWindowRow(V17TimestampedRow, V17Base):
    __tablename__ = "memory_windows"
    __table_args__ = (UniqueConstraint("project_id", "session_id", "status", name="uq_memory_windows_project_session_status"), CheckConstraint("status IN ('open', 'sealed', 'processing', 'completed', 'failed')", name="ck_memory_windows_status"))
    id: Mapped[int] = mapped_column(IdType, primary_key=True)
    project_id: Mapped[int] = mapped_column(IdType, ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False, index=True)
    session_id: Mapped[int] = mapped_column(IdType, ForeignKey("sessions.id", ondelete="RESTRICT"), nullable=False, index=True)
    session_key: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="open", server_default="open")
    input_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    sealed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sealed_by: Mapped[str | None] = mapped_column(String(255))

class MemoryWindowMessageRow(V17Base):
    __tablename__ = "memory_window_messages"
    __table_args__ = (UniqueConstraint("window_id", "message_id", name="uq_memory_window_messages_message"), UniqueConstraint("window_id", "position", name="uq_memory_window_messages_position"))
    id: Mapped[int] = mapped_column(IdType, primary_key=True)
    window_id: Mapped[int] = mapped_column(IdType, ForeignKey("memory_windows.id", ondelete="CASCADE"), nullable=False, index=True)
    message_id: Mapped[int] = mapped_column(IdType, ForeignKey("messages.id", ondelete="RESTRICT"), nullable=False, index=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

class MemoryChangeSetRow(V17TimestampedRow, V17Base):
    __tablename__ = "memory_change_sets"
    __table_args__ = (CheckConstraint("operation IN ('create', 'update', 'no_change')", name="ck_memory_change_sets_operation"), CheckConstraint("status IN ('shadow', 'approved', 'rejected', 'applied')", name="ck_memory_change_sets_status"), UniqueConstraint("window_id", "input_hash", name="uq_memory_change_sets_window_input"))
    id: Mapped[int] = mapped_column(IdType, primary_key=True)
    project_id: Mapped[int] = mapped_column(IdType, ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False, index=True)
    window_id: Mapped[int] = mapped_column(IdType, ForeignKey("memory_windows.id", ondelete="RESTRICT"), nullable=False, index=True)
    # 决策表由 V1.6 元数据负责创建；不在 V1.7 无迁移 schema 中创建占位表，
    # 以免遮蔽后续 V16Base.metadata.create_all 的完整定义。正式迁移仍保留 FK。
    decision_run_id: Mapped[int | None] = mapped_column(IdType, index=True)
    operation: Mapped[str] = mapped_column(String(20), nullable=False)
    target_memory_id: Mapped[int | None] = mapped_column(IdType, ForeignKey("memories.id", ondelete="RESTRICT"), index=True)
    target_revision: Mapped[int | None] = mapped_column(Integer)
    output_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="shadow", server_default="shadow")
    reviewer: Mapped[str | None] = mapped_column(String(255))
    review_reason: Mapped[str | None] = mapped_column(Text)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    applied_memory_id: Mapped[int | None] = mapped_column(IdType, ForeignKey("memories.id", ondelete="RESTRICT"))

__all__ = ["V17Base", "ProjectMemoryWindowPolicyRow", "MemoryWindowRow", "MemoryWindowMessageRow", "MemoryChangeSetRow"]
