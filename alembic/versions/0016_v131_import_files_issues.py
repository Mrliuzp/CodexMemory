"""为异步历史知识导入增加文件存储和问题记录。"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0016_v131_import_files_issues"
down_revision = "0015_v131_import_lifecycle"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    ident = sa.BigInteger()
    if not inspector.has_table("import_files"):
        op.create_table(
            "import_files",
            sa.Column("id", ident, primary_key=True),
            sa.Column("project_id", ident, sa.ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("import_batch_id", ident, sa.ForeignKey("import_batches.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("source_name", sa.String(500), nullable=False),
            sa.Column("source_type", sa.String(32), nullable=False),
            sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("content_hash", sa.String(64), nullable=False),
            sa.Column("storage_backend", sa.String(32), nullable=False, server_default="database"),
            sa.Column("storage_key", sa.String(500)),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("status", sa.String(24), nullable=False, server_default="uploaded"),
            sa.Column("parser_version", sa.String(64), nullable=False, server_default="knowledge-import-v1"),
            sa.Column("metadata_json", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("error_message", sa.Text()),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("import_batch_id", "content_hash", name="uq_import_files_batch_hash"),
        )
        op.create_index("ix_import_files_project_id", "import_files", ["project_id"])
        op.create_index("ix_import_files_import_batch_id", "import_files", ["import_batch_id"])
    inspector = sa.inspect(bind)
    if not inspector.has_table("import_issues"):
        op.create_table(
            "import_issues",
            sa.Column("id", ident, primary_key=True),
            sa.Column("project_id", ident, sa.ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("import_batch_id", ident, sa.ForeignKey("import_batches.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("import_file_id", ident, sa.ForeignKey("import_files.id", ondelete="RESTRICT")),
            sa.Column("source_document_id", ident, sa.ForeignKey("source_documents.id", ondelete="RESTRICT")),
            sa.Column("issue_type", sa.String(64), nullable=False),
            sa.Column("severity", sa.String(20), nullable=False, server_default="warning"),
            sa.Column("message", sa.Text(), nullable=False),
            sa.Column("metadata_json", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )
        op.create_index("ix_import_issues_project_id", "import_issues", ["project_id"])
        op.create_index("ix_import_issues_import_batch_id", "import_issues", ["import_batch_id"])
        op.create_index("ix_import_issues_import_file_id", "import_issues", ["import_file_id"])
        op.create_index("ix_import_issues_source_document_id", "import_issues", ["source_document_id"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("import_issues"):
        op.drop_table("import_issues")
    if inspector.has_table("import_files"):
        op.drop_table("import_files")