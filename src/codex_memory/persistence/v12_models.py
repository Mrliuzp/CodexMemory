from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, Column, DateTime, ForeignKey, Integer, String, Table, Text, UniqueConstraint, func, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class V12Base(DeclarativeBase):
    pass


IdType = BigInteger().with_variant(Integer, "sqlite")
Table("projects", V12Base.metadata, Column("id", IdType, primary_key=True))


class V12TimestampedRow:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class KnowledgeScopeRow(V12TimestampedRow, V12Base):
    __tablename__ = "knowledge_scopes"
    __table_args__ = (UniqueConstraint("project_id", "scope_key", name="uq_knowledge_scopes_project_key"),)

    id: Mapped[int] = mapped_column(IdType, primary_key=True)
    project_id: Mapped[int] = mapped_column(IdType, ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False, index=True)
    scope_key: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active", server_default="active")


def legacy_default_scope(project_id: int):
    """Select the real default Scope used to project legacy project-level data."""
    return select(KnowledgeScopeRow).where(
        KnowledgeScopeRow.project_id == project_id,
        KnowledgeScopeRow.scope_key == "default",
        KnowledgeScopeRow.is_default.is_(True),
    )
