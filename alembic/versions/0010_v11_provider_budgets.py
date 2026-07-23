from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0010_v11_provider_budgets"
down_revision = "0009_v11_canary_profiles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {item["name"] for item in sa.inspect(bind).get_columns("project_processing_policies")}
    for name in ("daily_embedding_token_budget", "daily_llm_token_budget"):
        if name not in columns:
            op.add_column(
                "project_processing_policies",
                sa.Column(name, sa.Integer(), nullable=False, server_default="0"),
            )
    if not sa.inspect(bind).has_table("daily_token_usage"):
        op.create_table(
            "daily_token_usage",
            sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
            sa.Column("project_id", sa.BigInteger(), sa.ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False, index=True),
            sa.Column("usage_date", sa.Date(), nullable=False),
            sa.Column("token_type", sa.String(32), nullable=False),
            sa.Column("tokens_used", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now()),
            sa.UniqueConstraint("project_id", "usage_date", "token_type", name="uq_daily_token_project_date_type"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    if sa.inspect(bind).has_table("daily_token_usage"):
        op.drop_table("daily_token_usage")
    columns = {item["name"] for item in sa.inspect(bind).get_columns("project_processing_policies")}
    for name in ("daily_llm_token_budget", "daily_embedding_token_budget"):
        if name in columns:
            op.drop_column("project_processing_policies", name)