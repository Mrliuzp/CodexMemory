"""Add the V1.3.1 Knowledge Import reference layer."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0013_v131_knowledge_import"
down_revision = "0012_v13_async_contracts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if not sa.inspect(bind).has_table("import_batches"):
        op.create_table(
            "import_batches",
            sa.Column("id", sa.BigInteger().with_variant(sa.Integer, "sqlite"), primary_key=True),
            sa.Column("project_id", sa.BigInteger().with_variant(sa.Integer, "sqlite"), sa.ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
            sa.Column("source_type", sa.String(32), nullable=False),
            sa.Column("source_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("document_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("chunk_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("error_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("metadata_json", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("error_message", sa.Text()),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("completed_at", sa.DateTime(timezone=True)),
        )
        op.create_index("ix_import_batches_project_id", "import_batches", ["project_id"])
    if not sa.inspect(bind).has_table("source_documents"):
        op.create_table(
            "source_documents",
            sa.Column("id", sa.BigInteger().with_variant(sa.Integer, "sqlite"), primary_key=True),
            sa.Column("project_id", sa.BigInteger().with_variant(sa.Integer, "sqlite"), sa.ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("import_batch_id", sa.BigInteger().with_variant(sa.Integer, "sqlite"), sa.ForeignKey("import_batches.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("source_name", sa.String(500), nullable=False),
            sa.Column("source_type", sa.String(32), nullable=False),
            sa.Column("content_hash", sa.String(64), nullable=False),
            sa.Column("parser_version", sa.String(64), nullable=False),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("status", sa.String(20), nullable=False, server_default="parsed"),
            sa.Column("metadata_json", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("error_message", sa.Text()),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("project_id", "content_hash", name="uq_source_documents_project_hash"),
        )
        op.create_index("ix_source_documents_project_id", "source_documents", ["project_id"])
        op.create_index("ix_source_documents_import_batch_id", "source_documents", ["import_batch_id"])
    if not sa.inspect(bind).has_table("document_chunks"):
        op.create_table(
            "document_chunks",
            sa.Column("id", sa.BigInteger().with_variant(sa.Integer, "sqlite"), primary_key=True),
            sa.Column("project_id", sa.BigInteger().with_variant(sa.Integer, "sqlite"), sa.ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("document_id", sa.BigInteger().with_variant(sa.Integer, "sqlite"), sa.ForeignKey("source_documents.id", ondelete="CASCADE"), nullable=False),
            sa.Column("chunk_index", sa.Integer(), nullable=False),
            sa.Column("heading", sa.String(500)),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("content_hash", sa.String(64), nullable=False),
            sa.Column("start_char", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("end_char", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("metadata_json", sa.JSON(), nullable=False, server_default="{}"),
            sa.UniqueConstraint("document_id", "chunk_index", name="uq_document_chunks_document_index"),
        )
        op.create_index("ix_document_chunks_project_id", "document_chunks", ["project_id"])
        op.create_index("ix_document_chunks_document_id", "document_chunks", ["document_id"])
    if not sa.inspect(bind).has_table("reference_candidates"):
        op.create_table(
            "reference_candidates",
            sa.Column("id", sa.BigInteger().with_variant(sa.Integer, "sqlite"), primary_key=True),
            sa.Column("project_id", sa.BigInteger().with_variant(sa.Integer, "sqlite"), sa.ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("document_id", sa.BigInteger().with_variant(sa.Integer, "sqlite"), sa.ForeignKey("source_documents.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("chunk_id", sa.BigInteger().with_variant(sa.Integer, "sqlite"), sa.ForeignKey("document_chunks.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("title", sa.String(300)),
            sa.Column("content", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("status", sa.String(24), nullable=False, server_default="pending_review"),
            sa.Column("confidence", sa.Float(), nullable=False, server_default="0.5"),
            sa.Column("dedupe_key", sa.String(128), nullable=False),
            sa.Column("evidence_json", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("project_id", "chunk_id", name="uq_reference_candidates_project_chunk"),
        )
        op.create_index("ix_reference_candidates_project_id", "reference_candidates", ["project_id"])
        op.create_index("ix_reference_candidates_document_id", "reference_candidates", ["document_id"])
        op.create_index("ix_reference_candidates_chunk_id", "reference_candidates", ["chunk_id"])


def downgrade() -> None:
    bind = op.get_bind()
    for table in ("reference_candidates", "document_chunks", "source_documents", "import_batches"):
        if sa.inspect(bind).has_table(table):
            op.drop_table(table)
