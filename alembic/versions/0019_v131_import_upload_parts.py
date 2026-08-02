"""增加历史导入文件的上传分片表。"""

from alembic import op
import sqlalchemy as sa

revision = "0019_v131_upload_parts"
down_revision = "0018_v131_storage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if sa.inspect(bind).has_table("import_upload_parts"):
        return
    op.create_table(
        "import_upload_parts",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column("scope_id", sa.String(120), nullable=False, server_default="project"),
        sa.Column("import_batch_id", sa.BigInteger(), nullable=False),
        sa.Column("upload_id", sa.String(80), nullable=False),
        sa.Column("source_name", sa.String(500), nullable=False),
        sa.Column("source_type", sa.String(32), nullable=False),
        sa.Column("part_number", sa.Integer, nullable=False),
        sa.Column("total_parts", sa.Integer, nullable=False),
        sa.Column("size_bytes", sa.Integer, nullable=False, server_default="0"),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("metadata_json", sa.JSON, nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="uploaded"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["import_batch_id"], ["import_batches.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("import_batch_id", "upload_id", "part_number", name="uq_import_upload_parts_part"),
    )
    op.create_index("ix_import_upload_parts_project_id", "import_upload_parts", ["project_id"])
    op.create_index("ix_import_upload_parts_import_batch_id", "import_upload_parts", ["import_batch_id"])
    op.create_index("ix_import_upload_parts_upload_id", "import_upload_parts", ["upload_id"])


def downgrade() -> None:
    bind = op.get_bind()
    if not sa.inspect(bind).has_table("import_upload_parts"):
        return
    op.drop_table("import_upload_parts")
