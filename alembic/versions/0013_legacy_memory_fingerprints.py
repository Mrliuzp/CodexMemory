"""Add source fingerprints for imported legacy memories."""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0013_legacy_memory_fingerprints"
down_revision = "0012_global_http_operations"
branch_labels = None
depends_on = None


def _column_names(bind, table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(bind).get_columns(table)}


def _index_names(bind, table: str) -> set[str]:
    return {index["name"] for index in sa.inspect(bind).get_indexes(table)}


def upgrade() -> None:
    bind = op.get_bind()
    for table, index_name in (
        ("memories", "uq_memories_source_fingerprint"),
        ("memory_versions", "uq_memory_versions_source_fingerprint"),
    ):
        if "source_fingerprint" not in _column_names(bind, table):
            with op.batch_alter_table(table) as batch:
                batch.add_column(sa.Column("source_fingerprint", sa.String(64), nullable=True))
        if index_name not in _index_names(bind, table):
            op.create_index(index_name, table, ["source_fingerprint"], unique=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table, index_name in (
        ("memory_versions", "uq_memory_versions_source_fingerprint"),
        ("memories", "uq_memories_source_fingerprint"),
    ):
        if index_name in _index_names(bind, table):
            op.drop_index(index_name, table_name=table)
        if "source_fingerprint" in _column_names(bind, table):
            with op.batch_alter_table(table) as batch:
                batch.drop_column("source_fingerprint")