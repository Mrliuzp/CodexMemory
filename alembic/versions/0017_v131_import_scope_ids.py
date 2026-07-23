"""为历史导入实体补充显式 Scope 绑定。"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0017_v131_import_scope_ids"
down_revision = "0016_v131_import_files_issues"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    for table in ("import_batches", "import_files", "import_issues", "source_documents", "document_chunks", "reference_candidates"):
        if not sa.inspect(bind).has_table(table):
            continue
        if "scope_id" not in _columns(table):
            op.add_column(table, sa.Column("scope_id", sa.String(120), nullable=False, server_default="project"))


def downgrade() -> None:
    for table in ("reference_candidates", "document_chunks", "source_documents", "import_issues", "import_files", "import_batches"):
        if sa.inspect(op.get_bind()).has_table(table) and "scope_id" in _columns(table):
            op.drop_column(table, "scope_id")