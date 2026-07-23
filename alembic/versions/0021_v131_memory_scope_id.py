"""???????? Memory ???? Scope ???"""

from alembic import op
import sqlalchemy as sa

revision = "0021_v131_memory_scope"
down_revision = "0020_v131_real_scope"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("memories"):
        return
    if "scope_id" not in {column["name"] for column in inspector.get_columns("memories")}:
        op.add_column("memories", sa.Column("scope_id", sa.BigInteger(), nullable=True))
        op.create_index("ix_memories_scope_id", "memories", ["scope_id"])


def downgrade() -> None:
    bind = op.get_bind()
    if sa.inspect(bind).has_table("memories") and "scope_id" in {column["name"] for column in sa.inspect(bind).get_columns("memories")}:
        op.drop_index("ix_memories_scope_id", table_name="memories")
        op.drop_column("memories", "scope_id")
