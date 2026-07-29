"""V1.4 任务执行报告的持久化模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from .v11_models import V11Base, IdType


class TaskRunRow(V11Base):
    __tablename__ = "task_runs"
    __table_args__ = (
        UniqueConstraint("project_id", "session_key", name="uq_task_runs_project_session_key"),
        Index("ix_task_runs_project_status_created", "project_id", "status", "created_at"),
    )

    id: Mapped[int] = mapped_column(IdType, primary_key=True)
    project_id: Mapped[int] = mapped_column(IdType, ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False, index=True)
    session_key: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="running", server_default="running")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    git_branch: Mapped[str | None] = mapped_column(String(255))
    git_head: Mapped[str | None] = mapped_column(String(128))
    git_status_porcelain: Mapped[str | None] = mapped_column(Text)
    git_diff_hash: Mapped[str | None] = mapped_column(String(64))
    git_untracked_json: Mapped[list[Any] | None] = mapped_column(JSON)
    git_available: Mapped[bool | None] = mapped_column(Boolean)
    current_report_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class TaskEventRow(V11Base):
    __tablename__ = "task_events"
    __table_args__ = (
        UniqueConstraint("task_run_id", "event_key", name="uq_task_events_run_event_key"),
        Index("ix_task_events_project_sequence", "project_id", "task_run_id", "sequence_no"),
        Index("ix_task_events_type_occurred", "task_run_id", "event_type", "occurred_at"),
    )

    id: Mapped[int] = mapped_column(IdType, primary_key=True)
    project_id: Mapped[int] = mapped_column(IdType, ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False, index=True)
    task_run_id: Mapped[int] = mapped_column(IdType, ForeignKey("task_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    event_key: Mapped[str] = mapped_column(String(255), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    sequence_no: Mapped[int] = mapped_column(Integer, nullable=False)
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    original_length: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    command_summary: Mapped[str | None] = mapped_column(Text)
    command_original_length: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    command_sha256: Mapped[str | None] = mapped_column(String(64))
    result_summary: Mapped[str | None] = mapped_column(Text)
    result_original_length: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    result_sha256: Mapped[str | None] = mapped_column(String(64))
    exit_code: Mapped[int | None] = mapped_column(Integer)
    redaction_applied: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    truncated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class TaskReportRow(V11Base):
    __tablename__ = "task_reports"
    __table_args__ = (
        UniqueConstraint("task_run_id", "revision", name="uq_task_reports_run_revision"),
        UniqueConstraint("source_event_id", name="uq_task_reports_source_event"),
        Index("ix_task_reports_project_kind_created", "project_id", "report_kind", "created_at"),
    )

    id: Mapped[int] = mapped_column(IdType, primary_key=True)
    project_id: Mapped[int] = mapped_column(IdType, ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False, index=True)
    task_run_id: Mapped[int] = mapped_column(IdType, ForeignKey("task_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    source_event_id: Mapped[int] = mapped_column(IdType, ForeignKey("task_events.id", ondelete="RESTRICT"), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    report_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    report_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    uncertain: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    truncated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class TaskFileChangeRow(V11Base):
    __tablename__ = "task_file_changes"
    __table_args__ = (
        UniqueConstraint("report_id", "change_index", name="uq_task_file_changes_report_index"),
        UniqueConstraint("report_id", "path", "change_index", name="uq_task_file_changes_report_path_index"),
        Index("ix_task_file_changes_project_path", "project_id", "path"),
    )

    id: Mapped[int] = mapped_column(IdType, primary_key=True)
    project_id: Mapped[int] = mapped_column(IdType, ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False, index=True)
    task_run_id: Mapped[int] = mapped_column(IdType, ForeignKey("task_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    report_id: Mapped[int] = mapped_column(IdType, ForeignKey("task_reports.id", ondelete="CASCADE"), nullable=False, index=True)
    change_index: Mapped[int] = mapped_column(Integer, nullable=False)
    path: Mapped[str] = mapped_column(String(1000), nullable=False)
    old_path: Mapped[str | None] = mapped_column(String(1000))
    change_type: Mapped[str] = mapped_column(String(32), nullable=False, default="modified", server_default="modified")
    before_hash: Mapped[str | None] = mapped_column(String(64))
    after_hash: Mapped[str | None] = mapped_column(String(64))
    attribution: Mapped[str] = mapped_column(String(20), nullable=False, default="certain", server_default="certain")
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


V14Base = V11Base

__all__ = ["V14Base", "TaskRunRow", "TaskEventRow", "TaskReportRow", "TaskFileChangeRow"]
