"""把历史导入实体的 scope_id 映射到 knowledge_scopes。"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0020_v131_real_scope"
down_revision = "0019_v131_upload_parts"
branch_labels = None
depends_on = None

_TABLES = ("import_batches", "import_files", "import_issues", "source_documents", "document_chunks", "reference_candidates", "import_upload_parts")
_IDENT = sa.BigInteger()


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def _ensure_default_scopes() -> None:
    bind = op.get_bind()
    if not sa.inspect(bind).has_table("knowledge_scopes") or not sa.inspect(bind).has_table("projects"):
        return
    op.execute(sa.text("""
        INSERT INTO knowledge_scopes (project_id, scope_key, name, description, is_default, status)
        SELECT p.id, 'default', '默认 Scope', NULL, :is_default, 'active'
        FROM projects p
        WHERE NOT EXISTS (
            SELECT 1 FROM knowledge_scopes s WHERE s.project_id = p.id AND s.scope_key = 'default'
        )
    """).bindparams(is_default=True))


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("knowledge_scopes"):
        # 尚未建立 V1.2 Scope 表时保持原状，避免生成无法解析的关联标识。
        return
    _ensure_default_scopes()
    for table in _TABLES:
        if not inspector.has_table(table):
            continue
        columns = _columns(table)
        if "scope_id" not in columns:
            op.add_column(table, sa.Column("scope_id", _IDENT, nullable=True))
            columns.add("scope_id")
        if "scope_ref_id" not in columns:
            op.add_column(table, sa.Column("scope_ref_id", _IDENT, nullable=True))
        old = "scope_id"
        op.execute(sa.text(f"""
            UPDATE {table} AS target
            SET scope_ref_id = (
                SELECT s.id FROM knowledge_scopes s
                WHERE s.project_id = target.project_id
                  AND (CAST(s.id AS TEXT) = CAST(target.{old} AS TEXT)
                       OR s.scope_key = CAST(target.{old} AS TEXT)
                       OR (CAST(target.{old} AS TEXT) IN ('project', 'default') AND s.is_default = :is_default))
                ORDER BY s.is_default DESC, s.id
                LIMIT 1
            )
            WHERE target.scope_ref_id IS NULL
        """).bindparams(is_default=True))
        op.execute(sa.text(f"""
            UPDATE {table}
            SET scope_ref_id = (SELECT s.id FROM knowledge_scopes s WHERE s.project_id = {table}.project_id AND s.is_default = :is_default ORDER BY s.id LIMIT 1)
            WHERE scope_ref_id IS NULL
        """).bindparams(is_default=True))
        op.drop_column(table, "scope_id")
        op.alter_column(table, "scope_ref_id", new_column_name="scope_id", existing_type=_IDENT, nullable=False, server_default="0")
        op.create_foreign_key(f"fk_{table}_scope_id", table, "knowledge_scopes", ["scope_id"], ["id"], ondelete="RESTRICT")
        inspector = sa.inspect(bind)


def downgrade() -> None:
    # 真实 Scope 标识不可无损还原为早期字符串字段，因此不执行破坏性降级。
    return
