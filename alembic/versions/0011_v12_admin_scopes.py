"""Add V1.2 knowledge scopes and project default Scope projection."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0011_v12_admin_scopes"
down_revision = "0010_v11_provider_budgets"
branch_labels = None
depends_on = None


def _knowledge_scopes_table() -> sa.Table:
    id_type = sa.BigInteger().with_variant(sa.Integer, "sqlite")
    return sa.table(
        "knowledge_scopes",
        sa.column("id", id_type),
        sa.column("project_id", id_type),
        sa.column("scope_key", sa.String(100)),
        sa.column("name", sa.String(200)),
        sa.column("description", sa.Text()),
        sa.column("is_default", sa.Boolean()),
        sa.column("status", sa.String(20)),
    )


def _create_knowledge_scopes_table() -> None:
    id_type = sa.BigInteger().with_variant(sa.Integer, "sqlite")
    op.create_table(
        "knowledge_scopes",
        sa.Column("id", id_type, primary_key=True, autoincrement=True),
        sa.Column("project_id", id_type, sa.ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False, index=True),
        sa.Column("scope_key", sa.String(100), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("project_id", "scope_key", name="uq_knowledge_scopes_project_key"),
    )


def _create_missing_default_scopes(bind: sa.Connection) -> None:
    projects = sa.table("projects", sa.column("id", sa.BigInteger().with_variant(sa.Integer, "sqlite")))
    scopes = _knowledge_scopes_table()
    missing_default_scope = ~sa.exists(
        sa.select(sa.literal(1)).where(
            scopes.c.project_id == projects.c.id,
            scopes.c.scope_key == "default",
        )
    )
    bind.execute(
        sa.insert(scopes).from_select(
            ["project_id", "scope_key", "name", "description", "is_default", "status"],
            sa.select(
                projects.c.id,
                sa.literal("default"),
                sa.literal("Default"),
                sa.null(),
                sa.true(),
                sa.literal("active"),
            ).where(missing_default_scope),
        )
    )


def _create_sqlite_foreign_key_guard(bind: sa.Connection) -> None:
    if bind.dialect.name == "sqlite":
        bind.execute(
            sa.text(
                "CREATE TRIGGER IF NOT EXISTS trg_knowledge_scopes_project_fk "
                "BEFORE INSERT ON knowledge_scopes "
                "FOR EACH ROW WHEN NOT EXISTS (SELECT 1 FROM projects WHERE id = NEW.project_id) "
                "BEGIN SELECT RAISE(ABORT, 'knowledge_scopes.project_id references an unknown project'); END"
            )
        )


def upgrade() -> None:
    bind = op.get_bind()
    if not sa.inspect(bind).has_table("knowledge_scopes"):
        _create_knowledge_scopes_table()
    _create_sqlite_foreign_key_guard(bind)
    _create_missing_default_scopes(bind)


def downgrade() -> None:
    bind = op.get_bind()
    if sa.inspect(bind).has_table("knowledge_scopes"):
        op.drop_table("knowledge_scopes")
