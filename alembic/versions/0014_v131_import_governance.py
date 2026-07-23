"""为 V1.3.1 导入增加审核发布和回滚字段。"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0014_v131_import_governance"
down_revision = "0013_v131_knowledge_import"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("import_batches"):
        columns = _columns("import_batches")
        if "scope_key" not in columns:
            op.add_column("import_batches", sa.Column("scope_key", sa.String(120), nullable=False, server_default="project"))
        if "rolled_back_at" not in columns:
            op.add_column("import_batches", sa.Column("rolled_back_at", sa.DateTime(timezone=True), nullable=True))
    if inspector.has_table("reference_candidates"):
        columns = _columns("reference_candidates")
        if "scope_key" not in columns:
            op.add_column("reference_candidates", sa.Column("scope_key", sa.String(120), nullable=False, server_default="project"))
        if "published_memory_id" not in columns:
            op.add_column("reference_candidates", sa.Column("published_memory_id", sa.BigInteger(), nullable=True))
            op.create_index("ix_reference_candidates_published_memory_id", "reference_candidates", ["published_memory_id"])
        if "reviewer" not in columns:
            op.add_column("reference_candidates", sa.Column("reviewer", sa.String(160), nullable=True))
        if "review_reason" not in columns:
            op.add_column("reference_candidates", sa.Column("review_reason", sa.Text(), nullable=True))
        if "reviewed_at" not in columns:
            op.add_column("reference_candidates", sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True))
        if "rolled_back_at" not in columns:
            op.add_column("reference_candidates", sa.Column("rolled_back_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("reference_candidates"):
        columns = _columns("reference_candidates")
        for name in ("rolled_back_at", "reviewed_at", "review_reason", "reviewer", "scope_key"):
            if name in columns:
                op.drop_column("reference_candidates", name)
        if "published_memory_id" in columns:
            op.drop_index("ix_reference_candidates_published_memory_id", table_name="reference_candidates")
            op.drop_column("reference_candidates", "published_memory_id")
    if inspector.has_table("import_batches"):
        columns = _columns("import_batches")
        for name in ("rolled_back_at", "scope_key"):
            if name in columns:
                op.drop_column("import_batches", name)
