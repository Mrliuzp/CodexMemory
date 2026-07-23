from __future__ import annotations

from alembic import op

revision = "0007_v11_lexical_audit"
down_revision = "0006_v11_embedding_profiles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")


def downgrade() -> None:
    return
