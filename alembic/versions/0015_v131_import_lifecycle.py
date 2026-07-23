"""为历史知识导入增加可观测生命周期字段。"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0015_v131_import_lifecycle"
down_revision = "0014_v131_import_governance"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    if not sa.inspect(bind).has_table("import_batches"):
        return
    columns = _columns("import_batches")
    additions = (
        ("started_at", sa.DateTime(timezone=True), None),
        ("cancelled_at", sa.DateTime(timezone=True), None),
        ("retry_count", sa.Integer(), 0),
        ("processed_count", sa.Integer(), 0),
    )
    for name, column_type, default in additions:
        if name not in columns:
            kwargs = {"nullable": False, "server_default": str(default)} if default is not None else {"nullable": True}
            op.add_column("import_batches", sa.Column(name, column_type, **kwargs))


def downgrade() -> None:
    if not sa.inspect(op.get_bind()).has_table("import_batches"):
        return
    columns = _columns("import_batches")
    for name in ("processed_count", "retry_count", "cancelled_at", "started_at"):
        if name in columns:
            op.drop_column("import_batches", name)