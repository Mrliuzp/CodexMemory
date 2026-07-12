"""Add the memory relation edge table."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0002_memory_relations"
down_revision = "0001_v1_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if sa.inspect(bind).has_table("memory_relations"):
        return
    op.create_table(
        "memory_relations",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("from_memory", sa.BigInteger(), sa.ForeignKey("memories.id", ondelete="CASCADE"), nullable=False),
        sa.Column("to_memory", sa.BigInteger(), sa.ForeignKey("memories.id", ondelete="CASCADE"), nullable=False),
        sa.Column("relation", sa.String(length=50), nullable=False),
        sa.UniqueConstraint("from_memory", "to_memory", "relation", name="uq_memory_relations_edge"),
    )


def downgrade() -> None:
    op.drop_table("memory_relations")
