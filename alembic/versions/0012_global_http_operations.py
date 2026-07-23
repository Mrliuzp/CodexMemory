"""Add migration operations metadata and archive status."""
from __future__ import annotations
import sqlalchemy as sa
from alembic import op
revision = "0012_global_http_operations"
down_revision = "0011_v12_admin_scopes"
branch_labels = None
depends_on = None
ID_TYPE = sa.BigInteger().with_variant(sa.Integer, "sqlite")
def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "source_fingerprint" not in {column["name"] for column in inspector.get_columns("messages")}:
        with op.batch_alter_table("messages") as batch:
            batch.add_column(sa.Column("source_fingerprint", sa.String(64), nullable=True))
    if "uq_messages_project_source_fingerprint" not in {item["name"] for item in inspector.get_indexes("messages")}:
        op.create_index("uq_messages_project_source_fingerprint", "messages", ["project_id", "source_fingerprint"], unique=True)
    if "migration_batches" not in tables:
        op.create_table("migration_batches", sa.Column("id", ID_TYPE, primary_key=True), sa.Column("source_path_hash", sa.String(64), nullable=False), sa.Column("source_sha256", sa.String(64), nullable=False), sa.Column("status", sa.String(32), nullable=False, server_default="inventory"), sa.Column("manifest", sa.JSON(), nullable=False), sa.Column("report", sa.JSON(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))
    if "migration_issues" not in tables:
        op.create_table("migration_issues", sa.Column("id", ID_TYPE, primary_key=True), sa.Column("batch_id", ID_TYPE, sa.ForeignKey("migration_batches.id", ondelete="RESTRICT"), nullable=False), sa.Column("source_type", sa.String(64), nullable=False), sa.Column("source_id", sa.String(255), nullable=False), sa.Column("code", sa.String(64), nullable=False), sa.Column("severity", sa.String(20), nullable=False, server_default="error"), sa.Column("detail", sa.JSON(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))
        op.create_index("ix_migration_issues_batch_id", "migration_issues", ["batch_id"])
    if "archive_status" not in tables:
        op.create_table("archive_status", sa.Column("id", ID_TYPE, primary_key=True), sa.Column("project_id", ID_TYPE, sa.ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False), sa.Column("last_user_archived_at", sa.DateTime(timezone=True)), sa.Column("last_assistant_archived_at", sa.DateTime(timezone=True)), sa.Column("last_success_at", sa.DateTime(timezone=True)), sa.Column("last_failure_at", sa.DateTime(timezone=True)), sa.Column("last_failure_summary", sa.String(300)), sa.Column("pending_count", sa.Integer(), nullable=False, server_default="0"), sa.Column("dead_letter_count", sa.Integer(), nullable=False, server_default="0"), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()), sa.UniqueConstraint("project_id", name="uq_archive_status_project"))
        op.create_index("ix_archive_status_project_id", "archive_status", ["project_id"])
def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "archive_status" in tables: op.drop_table("archive_status")
    if "migration_issues" in tables: op.drop_table("migration_issues")
    if "migration_batches" in tables: op.drop_table("migration_batches")
    if "source_fingerprint" in {column["name"] for column in inspector.get_columns("messages")}:
        with op.batch_alter_table("messages") as batch:
            batch.drop_index("uq_messages_project_source_fingerprint")
            batch.drop_column("source_fingerprint")
