"""允许导入文件只保存外部存储引用。"""

from alembic import op
import sqlalchemy as sa

revision = "0018_v131_storage"
down_revision = "0017_v131_import_scope_ids"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if not sa.inspect(bind).has_table("import_files"):
        return
    op.alter_column("import_files", "content", existing_type=sa.Text(), nullable=True)


def downgrade() -> None:
    bind = op.get_bind()
    if not sa.inspect(bind).has_table("import_files"):
        return
    # 已转移到外部存储的记录无法恢复原始内容，因此不执行破坏性降级。
