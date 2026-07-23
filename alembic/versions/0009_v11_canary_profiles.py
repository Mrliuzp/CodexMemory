from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0009_v11_canary_profiles"
down_revision = "0008_v11_flags_policies"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {item["name"] for item in sa.inspect(bind).get_columns("project_retrieval_profiles")}
    additions = [
        (
            "canary_embedding_profile_id",
            sa.Column(
                "canary_embedding_profile_id",
                sa.BigInteger().with_variant(sa.Integer, "sqlite"),
                sa.ForeignKey("embedding_profiles.id", ondelete="RESTRICT"),
                nullable=True,
            ),
        ),
        (
            "previous_active_embedding_profile_id",
            sa.Column(
                "previous_active_embedding_profile_id",
                sa.BigInteger().with_variant(sa.Integer, "sqlite"),
                sa.ForeignKey("embedding_profiles.id", ondelete="RESTRICT"),
                nullable=True,
            ),
        ),
        ("canary_percent", sa.Column("canary_percent", sa.Integer(), nullable=False, server_default="0")),
        ("rollback_reason", sa.Column("rollback_reason", sa.Text(), nullable=True)),
    ]
    for name, column in additions:
        if name not in columns:
            op.add_column("project_retrieval_profiles", column)


def downgrade() -> None:
    bind = op.get_bind()
    columns = {item["name"] for item in sa.inspect(bind).get_columns("project_retrieval_profiles")}
    names = [
        "rollback_reason",
        "canary_percent",
        "previous_active_embedding_profile_id",
        "canary_embedding_profile_id",
    ]
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("project_retrieval_profiles", recreate="always") as batch:
            for name in names:
                if name in columns:
                    batch.drop_column(name)
    else:
        for name in names:
            if name in columns:
                op.drop_column("project_retrieval_profiles", name)