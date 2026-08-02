"""修复历史默认 Scope 的占位名称。"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0024_repair_scope_names"
down_revision = "0023_v15_openapi_revisions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if not sa.inspect(bind).has_table("knowledge_scopes"):
        return
    scopes = sa.table(
        "knowledge_scopes",
        sa.column("scope_key", sa.String(100)),
        sa.column("name", sa.String(200)),
        sa.column("is_default", sa.Boolean()),
    )
    bind.execute(
        sa.update(scopes)
        .where(
            sa.or_(scopes.c.is_default.is_(True), scopes.c.scope_key == "default"),
            sa.or_(
                scopes.c.name.in_(["?????", "Default", ""]),
                scopes.c.name.is_(None),
            ),
        )
        .values(name="默认 Scope")
    )


def downgrade() -> None:
    # 数据修复不恢复问号占位符或英文旧名称。
    return
