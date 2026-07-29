"""新增 V1.4 任务执行报告持久化表。"""

from alembic import op
import sqlalchemy as sa


revision = "0022_v14_task_execution_reports"
down_revision = "0022_merge_heads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "task_runs",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("project_id", sa.BigInteger(), sa.ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("session_key", sa.String(255), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="running"),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True)),
        sa.Column("git_branch", sa.String(255)),
        sa.Column("git_head", sa.String(128)),
        sa.Column("git_status_porcelain", sa.Text()),
        sa.Column("git_diff_hash", sa.String(64)),
        sa.Column("git_untracked_json", sa.JSON()),
        sa.Column("git_available", sa.Boolean()),
        sa.Column("current_report_revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("project_id", "session_key", name="uq_task_runs_project_session_key"),
    )
    op.create_index("ix_task_runs_project_id", "task_runs", ["project_id"])
    op.create_index("ix_task_runs_project_status_created", "task_runs", ["project_id", "status", "created_at"])

    op.create_table(
        "task_events",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("project_id", sa.BigInteger(), sa.ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("task_run_id", sa.BigInteger(), sa.ForeignKey("task_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("event_key", sa.String(255), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("sequence_no", sa.Integer(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True)),
        sa.Column("payload_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("original_length", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("command_summary", sa.Text()),
        sa.Column("command_original_length", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("command_sha256", sa.String(64)),
        sa.Column("result_summary", sa.Text()),
        sa.Column("result_original_length", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("result_sha256", sa.String(64)),
        sa.Column("exit_code", sa.Integer()),
        sa.Column("redaction_applied", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("truncated", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("task_run_id", "event_key", name="uq_task_events_run_event_key"),
    )
    op.create_index("ix_task_events_project_id", "task_events", ["project_id"])
    op.create_index("ix_task_events_task_run_id", "task_events", ["task_run_id"])
    op.create_index("ix_task_events_project_sequence", "task_events", ["project_id", "task_run_id", "sequence_no"])
    op.create_index("ix_task_events_type_occurred", "task_events", ["task_run_id", "event_type", "occurred_at"])

    op.create_table(
        "task_reports",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("project_id", sa.BigInteger(), sa.ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("task_run_id", sa.BigInteger(), sa.ForeignKey("task_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_event_id", sa.BigInteger(), sa.ForeignKey("task_events.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("report_kind", sa.String(20), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("report_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("body", sa.Text(), nullable=False, server_default=""),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("uncertain", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("truncated", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("task_run_id", "revision", name="uq_task_reports_run_revision"),
        sa.UniqueConstraint("source_event_id", name="uq_task_reports_source_event"),
    )
    op.create_index("ix_task_reports_project_id", "task_reports", ["project_id"])
    op.create_index("ix_task_reports_task_run_id", "task_reports", ["task_run_id"])
    op.create_index("ix_task_reports_project_kind_created", "task_reports", ["project_id", "report_kind", "created_at"])

    op.create_table(
        "task_file_changes",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("project_id", sa.BigInteger(), sa.ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("task_run_id", sa.BigInteger(), sa.ForeignKey("task_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("report_id", sa.BigInteger(), sa.ForeignKey("task_reports.id", ondelete="CASCADE"), nullable=False),
        sa.Column("change_index", sa.Integer(), nullable=False),
        sa.Column("path", sa.String(1000), nullable=False),
        sa.Column("old_path", sa.String(1000)),
        sa.Column("change_type", sa.String(32), nullable=False, server_default="modified"),
        sa.Column("before_hash", sa.String(64)),
        sa.Column("after_hash", sa.String(64)),
        sa.Column("attribution", sa.String(20), nullable=False, server_default="certain"),
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("report_id", "change_index", name="uq_task_file_changes_report_index"),
        sa.UniqueConstraint("report_id", "path", "change_index", name="uq_task_file_changes_report_path_index"),
    )
    op.create_index("ix_task_file_changes_project_id", "task_file_changes", ["project_id"])
    op.create_index("ix_task_file_changes_task_run_id", "task_file_changes", ["task_run_id"])
    op.create_index("ix_task_file_changes_report_id", "task_file_changes", ["report_id"])
    op.create_index("ix_task_file_changes_project_path", "task_file_changes", ["project_id", "path"])


def downgrade() -> None:
    op.drop_index("ix_task_file_changes_project_path", table_name="task_file_changes")
    op.drop_index("ix_task_file_changes_report_id", table_name="task_file_changes")
    op.drop_index("ix_task_file_changes_task_run_id", table_name="task_file_changes")
    op.drop_index("ix_task_file_changes_project_id", table_name="task_file_changes")
    op.drop_table("task_file_changes")
    op.drop_index("ix_task_reports_project_kind_created", table_name="task_reports")
    op.drop_index("ix_task_reports_task_run_id", table_name="task_reports")
    op.drop_index("ix_task_reports_project_id", table_name="task_reports")
    op.drop_table("task_reports")
    op.drop_index("ix_task_events_type_occurred", table_name="task_events")
    op.drop_index("ix_task_events_project_sequence", table_name="task_events")
    op.drop_index("ix_task_events_task_run_id", table_name="task_events")
    op.drop_index("ix_task_events_project_id", table_name="task_events")
    op.drop_table("task_events")
    op.drop_index("ix_task_runs_project_status_created", table_name="task_runs")
    op.drop_index("ix_task_runs_project_id", table_name="task_runs")
    op.drop_table("task_runs")
