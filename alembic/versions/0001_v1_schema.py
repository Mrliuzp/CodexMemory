"""Create the Codex Memory V1 relational schema."""

from __future__ import annotations

from alembic import op

from codex_memory.db_models import Base


revision = "0001_v1_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    Base.metadata.create_all(bind)
    if bind.dialect.name == "postgresql":
        op.execute(
            "CREATE INDEX IF NOT EXISTS ix_memory_embeddings_embedding_hnsw "
            "ON memory_embeddings USING hnsw (embedding vector_cosine_ops)"
        )


def downgrade() -> None:
    Base.metadata.drop_all(op.get_bind())
