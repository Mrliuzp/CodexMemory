from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003_v11_additive_columns"
down_revision = "0002_memory_relations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    message_columns = {column["name"] for column in inspector.get_columns("messages")}
    memory_columns = {column["name"] for column in inspector.get_columns("memories")}

    if "occurred_at" not in message_columns:
        op.add_column("messages", sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=True))
    if "ingestion_version" not in message_columns:
        op.add_column("messages", sa.Column("ingestion_version", sa.String(32), nullable=False, server_default="v1"))
    if "conflict_status" not in message_columns:
        op.add_column("messages", sa.Column("conflict_status", sa.String(20), nullable=False, server_default="none"))

    if "scope" not in memory_columns:
        op.add_column("memories", sa.Column("scope", sa.String(20), nullable=False, server_default="project"))
    if "source_kind" not in memory_columns:
        op.add_column("memories", sa.Column("source_kind", sa.String(32), nullable=False, server_default="rule"))
    if "review_status" not in memory_columns:
        op.add_column("memories", sa.Column("review_status", sa.String(20), nullable=False, server_default="accepted"))

    indexes = {index["name"] for index in inspector.get_indexes("messages")}
    if "uq_messages_project_event_key" not in indexes:
        op.create_index("uq_messages_project_event_key", "messages", ["project_id", "event_key"], unique=True)

    if bind.dialect.name == "postgresql":
        constraints = {constraint["name"] for constraint in inspector.get_unique_constraints("messages")}
        if "uq_messages_event_key" in constraints:
            op.drop_constraint("uq_messages_event_key", "messages", type_="unique")


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    indexes = {index["name"] for index in inspector.get_indexes("messages")}
    if "uq_messages_project_event_key" in indexes:
        op.drop_index("uq_messages_project_event_key", table_name="messages")

    for table, column in (
        ("messages", "conflict_status"),
        ("messages", "ingestion_version"),
        ("messages", "occurred_at"),
        ("memories", "review_status"),
        ("memories", "source_kind"),
        ("memories", "scope"),
    ):
        if column in {item["name"] for item in sa.inspect(bind).get_columns(table)}:
            op.drop_column(table, column)
