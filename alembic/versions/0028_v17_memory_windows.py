"""V1.7 会话级 Memory Window 与人工变更集（仅增量结构）。"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0028_v17_memory_windows"
down_revision = "0027_v16_decision_contracts"
branch_labels = None
depends_on = None


def _has(bind: sa.Connection, table: str) -> bool:
    return sa.inspect(bind).has_table(table)


def _column(bind: sa.Connection, table: str, name: str) -> bool:
    return name in {item["name"] for item in sa.inspect(bind).get_columns(table)}


def _duplicate_memory_versions(bind: sa.Connection) -> list[tuple[int, int, int]]:
    """在创建唯一约束前显式发现历史重复，避免半完成迁移或静默删数据。"""
    if not _has(bind, "memory_versions"):
        return []
    rows = bind.execute(sa.text(
        "SELECT memory_id, version, COUNT(*) AS duplicate_count "
        "FROM memory_versions GROUP BY memory_id, version HAVING COUNT(*) > 1"
    )).fetchall()
    return [(int(row[0]), int(row[1]), int(row[2])) for row in rows]


def _drop_memory_version_change_set_foreign_keys(bind: sa.Connection) -> None:
    """在删除 ChangeSet 表前移除其反向外键。

    PostgreSQL 不允许删除被 ``memory_versions.source_change_set_id`` 引用的
    ``memory_change_sets``。该外键在添加列时通常是数据库命名的，因此不能
    依赖固定约束名；按列和被引用表精确识别，并在已有约束时才删除。
    """

    if not _has(bind, "memory_versions"):
        return
    inspector = sa.inspect(bind)
    for foreign_key in inspector.get_foreign_keys("memory_versions"):
        if foreign_key.get("referred_table") != "memory_change_sets":
            continue
        if "source_change_set_id" not in set(foreign_key.get("constrained_columns") or ()):
            continue
        name = foreign_key.get("name")
        if name:
            op.drop_constraint(name, "memory_versions", type_="foreignkey")


def upgrade() -> None:
    bind = op.get_bind()
    duplicates = _duplicate_memory_versions(bind)
    if duplicates:
        sample = ", ".join(f"memory_id={memory_id}, version={version}, count={count}" for memory_id, version, count in duplicates[:3])
        raise RuntimeError(
            "memory_versions 存在重复版本，已停止迁移；请先人工保留正确快照并复核审计后重试：" + sample
        )
    if not _has(bind, "project_memory_window_policies"):
        op.create_table(
            "project_memory_window_policies",
            sa.Column("project_id", sa.BigInteger(), sa.ForeignKey("projects.id", ondelete="RESTRICT"), primary_key=True),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("mode", sa.String(20), nullable=False, server_default="shadow"),
            sa.Column("manual_apply_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("max_messages", sa.Integer(), nullable=False, server_default="200"),
            sa.Column("max_input_chars", sa.Integer(), nullable=False, server_default="120000"),
            sa.Column("policy_version", sa.String(64), nullable=False, server_default="memory-window-v1"),
            sa.Column("updated_by", sa.String(255), nullable=False, server_default="system"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.CheckConstraint("max_messages > 0", name="ck_window_policy_max_messages"),
            sa.CheckConstraint("max_input_chars > 0", name="ck_window_policy_max_input_chars"),
            sa.CheckConstraint("mode = 'shadow'", name="ck_window_policy_mode_shadow"),
        )
    if not _has(bind, "memory_windows"):
        op.create_table(
            "memory_windows",
            sa.Column("id", sa.BigInteger(), primary_key=True),
            sa.Column("project_id", sa.BigInteger(), sa.ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("session_id", sa.BigInteger(), sa.ForeignKey("sessions.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("session_key", sa.String(160), nullable=False),
            sa.Column("status", sa.String(20), nullable=False, server_default="open"),
            sa.Column("input_hash", sa.String(64)),
            sa.Column("sealed_at", sa.DateTime(timezone=True)),
            sa.Column("sealed_by", sa.String(255)),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint("project_id", "session_id", "status", name="uq_memory_windows_project_session_status"),
            sa.CheckConstraint("status IN ('open', 'sealed', 'processing', 'completed', 'failed')", name="ck_memory_windows_status"),
        )
        op.create_index("ix_memory_windows_project_status", "memory_windows", ["project_id", "status", "created_at"])
    if not _has(bind, "memory_window_messages"):
        op.create_table(
            "memory_window_messages",
            sa.Column("id", sa.BigInteger(), primary_key=True),
            sa.Column("window_id", sa.BigInteger(), sa.ForeignKey("memory_windows.id", ondelete="CASCADE"), nullable=False),
            sa.Column("message_id", sa.BigInteger(), sa.ForeignKey("messages.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("position", sa.Integer(), nullable=False),
            sa.Column("content_hash", sa.String(64), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint("window_id", "message_id", name="uq_memory_window_messages_message"),
            sa.UniqueConstraint("window_id", "position", name="uq_memory_window_messages_position"),
        )
    if not _has(bind, "memory_change_sets"):
        op.create_table(
            "memory_change_sets",
            sa.Column("id", sa.BigInteger(), primary_key=True),
            sa.Column("project_id", sa.BigInteger(), sa.ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("window_id", sa.BigInteger(), sa.ForeignKey("memory_windows.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("decision_run_id", sa.BigInteger(), sa.ForeignKey("decision_runs.id", ondelete="RESTRICT")),
            sa.Column("operation", sa.String(20), nullable=False),
            sa.Column("target_memory_id", sa.BigInteger(), sa.ForeignKey("memories.id", ondelete="RESTRICT")),
            sa.Column("target_revision", sa.Integer()),
            sa.Column("output_json", sa.JSON(), nullable=False),
            sa.Column("input_hash", sa.String(64), nullable=False),
            sa.Column("status", sa.String(20), nullable=False, server_default="shadow"),
            sa.Column("reviewer", sa.String(255)),
            sa.Column("review_reason", sa.Text()),
            sa.Column("reviewed_at", sa.DateTime(timezone=True)),
            sa.Column("applied_memory_id", sa.BigInteger(), sa.ForeignKey("memories.id", ondelete="RESTRICT")),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.CheckConstraint("operation IN ('create', 'update', 'no_change')", name="ck_memory_change_sets_operation"),
            sa.CheckConstraint("status IN ('shadow', 'approved', 'rejected', 'applied')", name="ck_memory_change_sets_status"),
            sa.UniqueConstraint("window_id", "input_hash", name="uq_memory_change_sets_window_input"),
        )
        op.create_index("ix_memory_change_sets_project_status", "memory_change_sets", ["project_id", "status", "created_at"])
    if _has(bind, "memories") and not _column(bind, "memories", "revision"):
        op.add_column("memories", sa.Column("revision", sa.Integer(), nullable=False, server_default="1"))
    if _has(bind, "memory_versions"):
        additions = [
            ("title", sa.String(300)),
            ("level", sa.String(10)),
            ("memory_type", sa.String(50)),
            ("scope", sa.String(20)),
            ("scope_id", sa.BigInteger()),
            ("confidence", sa.Float()),
            ("status", sa.String(20)),
            ("deprecated", sa.Boolean()),
            ("content_hash", sa.String(64)),
            ("source_change_set_id", sa.BigInteger(), sa.ForeignKey("memory_change_sets.id", ondelete="RESTRICT")),
        ]
        for item in additions:
            name, column, *constraints = item
            if not _column(bind, "memory_versions", name):
                op.add_column("memory_versions", sa.Column(name, column, *constraints))
        inspector = sa.inspect(bind)
        constraints = {item.get("name") for item in inspector.get_unique_constraints("memory_versions")}
        if "uq_memory_versions_memory_version" not in constraints:
            op.create_unique_constraint("uq_memory_versions_memory_version", "memory_versions", ["memory_id", "version"])


def downgrade() -> None:
    bind = op.get_bind()
    # 必须先解除 memory_versions -> memory_change_sets 的反向外键，
    # 否则 PostgreSQL 无法删除被引用的 ChangeSet 表。
    _drop_memory_version_change_set_foreign_keys(bind)
    for table in ("memory_change_sets", "memory_window_messages", "memory_windows", "project_memory_window_policies"):
        if _has(bind, table):
            op.drop_table(table)
    if _has(bind, "memory_versions"):
        inspector = sa.inspect(bind)
        if "uq_memory_versions_memory_version" in {item.get("name") for item in inspector.get_unique_constraints("memory_versions")}:
            op.drop_constraint("uq_memory_versions_memory_version", "memory_versions", type_="unique")
        for name in ("source_change_set_id", "content_hash", "deprecated", "status", "confidence", "scope_id", "scope", "memory_type", "level", "title"):
            if _column(bind, "memory_versions", name):
                op.drop_column("memory_versions", name)
    if _has(bind, "memories") and _column(bind, "memories", "revision"):
        op.drop_column("memories", "revision")
