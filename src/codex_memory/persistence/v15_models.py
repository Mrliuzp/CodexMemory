"""V1.5 OpenAPI Revision 的独立持久化模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, JSON, BigInteger, Column, DateTime, ForeignKey, Index, Integer, String, Table, Text, UniqueConstraint, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class V15Base(DeclarativeBase):
    """V1.5 专用元数据，避免被基础建表逻辑提前创建。"""


IdType = BigInteger().with_variant(Integer(), "sqlite")

# V1.5 表只依赖 projects.id，用占位表保持元数据与既有版本隔离。
Table("projects", V15Base.metadata, Column("id", IdType, primary_key=True))


class ContractServiceRow(V15Base):
    __tablename__ = "contract_services"
    __table_args__ = (
        UniqueConstraint("project_id", "service_key", name="uq_contract_services_project_key"),
        Index("ix_contract_services_project_created", "project_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(IdType, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False, index=True)
    service_key: Mapped[str] = mapped_column(String(150), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class ContractRevisionRow(V15Base):
    __tablename__ = "contract_revisions"
    __table_args__ = (
        UniqueConstraint("service_id", "revision_number", name="uq_contract_revisions_service_number"),
        UniqueConstraint("service_id", "content_hash", name="uq_contract_revisions_service_hash"),
        CheckConstraint("status IN ('proposed', 'published', 'superseded')", name="ck_contract_revisions_status"),
        Index("ix_contract_revisions_project_status_created", "project_id", "status", "created_at"),
        Index("ix_contract_revisions_service_status", "service_id", "status"),
    )

    id: Mapped[int] = mapped_column(IdType, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False, index=True)
    service_id: Mapped[int] = mapped_column(ForeignKey("contract_services.id", ondelete="CASCADE"), nullable=False, index=True)
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="proposed", server_default="proposed")
    source_filename: Mapped[str] = mapped_column(String(500), nullable=False)
    source_extension: Mapped[str] = mapped_column(String(10), nullable=False)
    profile_version: Mapped[str] = mapped_column(String(20), nullable=False, default="v1", server_default="v1")
    normalized_document: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    validation_result: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    markdown: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ApiOperationRow(V15Base):
    __tablename__ = "api_operations"
    __table_args__ = (
        UniqueConstraint("revision_id", "method", "path", name="uq_api_operations_revision_route"),
        UniqueConstraint("revision_id", "operation_id", name="uq_api_operations_revision_operation_id"),
        Index("ix_api_operations_project_route", "project_id", "method", "path"),
        Index("ix_api_operations_revision_order", "revision_id", "path", "method"),
    )

    id: Mapped[int] = mapped_column(IdType, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False, index=True)
    service_id: Mapped[int] = mapped_column(ForeignKey("contract_services.id", ondelete="CASCADE"), nullable=False, index=True)
    revision_id: Mapped[int] = mapped_column(ForeignKey("contract_revisions.id", ondelete="CASCADE"), nullable=False, index=True)
    method: Mapped[str] = mapped_column(String(10), nullable=False)
    path: Mapped[str] = mapped_column(String(1000), nullable=False)
    operation_id: Mapped[str] = mapped_column(String(255), nullable=False)
    summary: Mapped[str | None] = mapped_column(String(1000))
    tags_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    operation_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


# 兼容服务层和外部调用者使用的语义名称。
ContractOperationRow = ApiOperationRow


__all__ = ["V15Base", "ContractServiceRow", "ContractRevisionRow", "ApiOperationRow", "ContractOperationRow"]
