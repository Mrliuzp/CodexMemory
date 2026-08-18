"""为历史项目补齐缺失的项目功能开关记录。"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0026_init_project_flags"
down_revision = "0025_expand_audit_subject_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("projects") or not inspector.has_table("project_feature_flags"):
        return

    bind.execute(
        sa.text(
            """
            INSERT INTO project_feature_flags (project_id)
            SELECT projects.id
            FROM projects
            WHERE NOT EXISTS (
                SELECT 1
                FROM project_feature_flags
                WHERE project_feature_flags.project_id = projects.id
            )
            """
        )
    )


def downgrade() -> None:
    # 回退不删除自动补齐的行，避免丢失项目当前的开关配置。
    return
