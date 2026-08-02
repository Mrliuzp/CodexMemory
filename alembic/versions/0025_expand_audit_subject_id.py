"""扩展审计对象标识长度，与消息 event_key 契约保持一致。"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0025_expand_audit_subject_id"
down_revision = "0024_repair_scope_names"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "audit_logs",
        "subject_id",
        existing_type=sa.String(100),
        type_=sa.String(255),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "audit_logs",
        "subject_id",
        existing_type=sa.String(255),
        type_=sa.String(100),
        existing_nullable=True,
    )
