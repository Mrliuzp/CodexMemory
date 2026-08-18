"""新增 Codex CLI 决策契约、运行记录、审核动作和项目策略。

本迁移接在审计标识扩展和项目功能开关初始化之后，保持单一 Alembic head。
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0027_v16_decision_contracts"
down_revision = "0026_init_project_flags"
branch_labels = None
depends_on = None


def _column_exists(bind: sa.Connection, table_name: str, column_name: str) -> bool:
    return column_name in {column["name"] for column in sa.inspect(bind).get_columns(table_name)}


def upgrade() -> None:
    bind = op.get_bind()
    if not _column_exists(bind, "project_feature_flags", "decision_engine_enabled"):
        op.add_column(
            "project_feature_flags",
            sa.Column("decision_engine_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        )

    op.create_table(
        "project_decision_policies",
        sa.Column("project_id", sa.BigInteger(), sa.ForeignKey("projects.id", ondelete="RESTRICT"), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("auto_publish_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("strategy", sa.String(32), nullable=False, server_default="manual_review"),
        sa.Column("allowed_level", sa.String(10), nullable=False, server_default="L1"),
        sa.Column("allowed_scope", sa.String(20), nullable=False, server_default="project"),
        sa.Column("min_confidence", sa.Float(), nullable=False, server_default="0.8"),
        sa.Column("require_evidence", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("allow_risk_flags", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("max_title_length", sa.Integer(), nullable=False, server_default="300"),
        sa.Column("max_content_chars", sa.Integer(), nullable=False, server_default="12000"),
        sa.Column("max_reason_length", sa.Integer(), nullable=False, server_default="2000"),
        sa.Column("max_evidence_ranges", sa.Integer(), nullable=False, server_default="16"),
        sa.Column("policy_version", sa.String(64), nullable=False, server_default="decision-policy-v1"),
        sa.Column("updated_by", sa.String(255), nullable=False, server_default="system"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("strategy IN ('manual_review', 'auto_publish')", name="ck_decision_policy_strategy"),
        sa.CheckConstraint("allowed_level = 'L1'", name="ck_decision_policy_allowed_level_l1"),
        sa.CheckConstraint("allowed_scope = 'project'", name="ck_decision_policy_allowed_scope_project"),
        sa.CheckConstraint("min_confidence >= 0 AND min_confidence <= 1", name="ck_decision_policy_confidence"),
        sa.CheckConstraint("max_title_length > 0 AND max_title_length <= 300", name="ck_decision_policy_title_length"),
        sa.CheckConstraint("max_content_chars > 0", name="ck_decision_policy_content_length"),
        sa.CheckConstraint("max_reason_length > 0", name="ck_decision_policy_reason_length"),
        sa.CheckConstraint("max_evidence_ranges > 0", name="ck_decision_policy_evidence_count"),
    )

    op.create_table(
        "decision_runs",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("project_id", sa.BigInteger(), sa.ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("processing_job_id", sa.BigInteger()),
        sa.Column("outbox_event_id", sa.BigInteger()),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("prompt_version", sa.String(64), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(20), nullable=False, server_default="running"),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_retries", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("error_class", sa.String(64)),
        sa.Column("error_code", sa.String(64)),
        sa.Column("error_message", sa.Text()),
        sa.Column("input_tokens", sa.Integer()),
        sa.Column("output_tokens", sa.Integer()),
        sa.Column("total_tokens", sa.Integer()),
        sa.Column("cost_micros", sa.BigInteger()),
        sa.Column("cost_currency", sa.String(8), nullable=False, server_default="USD"),
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'failed', 'retry_wait', 'cancelled')",
            name="ck_decision_runs_status",
        ),
        sa.CheckConstraint("retry_count >= 0", name="ck_decision_runs_retry_count"),
        sa.CheckConstraint("max_retries >= 0", name="ck_decision_runs_max_retries"),
        sa.CheckConstraint("input_tokens IS NULL OR input_tokens >= 0", name="ck_decision_runs_input_tokens"),
        sa.CheckConstraint("output_tokens IS NULL OR output_tokens >= 0", name="ck_decision_runs_output_tokens"),
        sa.CheckConstraint("total_tokens IS NULL OR total_tokens >= 0", name="ck_decision_runs_total_tokens"),
        sa.CheckConstraint("cost_micros IS NULL OR cost_micros >= 0", name="ck_decision_runs_cost_micros"),
    )
    op.create_index("ix_decision_runs_project_id", "decision_runs", ["project_id"])
    op.create_index("ix_decision_runs_processing_job_id", "decision_runs", ["processing_job_id"])
    op.create_index("ix_decision_runs_outbox_event_id", "decision_runs", ["outbox_event_id"])
    op.create_index("ix_decision_runs_project_started", "decision_runs", ["project_id", "started_at", "id"])
    op.create_index("ix_decision_runs_project_status", "decision_runs", ["project_id", "status", "created_at"])

    op.create_table(
        "model_decisions",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("project_id", sa.BigInteger(), sa.ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("decision_run_id", sa.BigInteger(), sa.ForeignKey("decision_runs.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("candidate_id", sa.BigInteger()),
        sa.Column("decision", sa.String(20), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("content", sa.JSON(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("evidence_ranges", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("risk_flags", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("level", sa.String(10), nullable=False, server_default="L1"),
        sa.Column("scope", sa.String(20), nullable=False, server_default="project"),
        sa.Column("validation_status", sa.String(20), nullable=False, server_default="validated"),
        sa.Column("review_status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("auto_publish_allowed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("policy_version", sa.String(64), nullable=False, server_default="decision-policy-v1"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("decision_run_id", name="uq_model_decisions_run"),
        sa.CheckConstraint("decision IN ('publish', 'skip', 'needs_review')", name="ck_model_decisions_decision"),
        sa.CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_model_decisions_confidence"),
        sa.CheckConstraint("level IN ('L1', 'L2', 'L3')", name="ck_model_decisions_level"),
        sa.CheckConstraint("scope IN ('project', 'global')", name="ck_model_decisions_scope"),
        sa.CheckConstraint("validation_status = 'validated'", name="ck_model_decisions_validation_status"),
        sa.CheckConstraint(
            "review_status IN ('pending', 'approved', 'rejected', 'corrected')",
            name="ck_model_decisions_review_status",
        ),
    )
    op.create_index("ix_model_decisions_project_id", "model_decisions", ["project_id"])
    op.create_index("ix_model_decisions_decision_run_id", "model_decisions", ["decision_run_id"])
    op.create_index("ix_model_decisions_candidate_id", "model_decisions", ["candidate_id"])
    op.create_index("ix_model_decisions_project_created", "model_decisions", ["project_id", "created_at", "id"])
    op.create_index("ix_model_decisions_project_decision", "model_decisions", ["project_id", "decision", "created_at"])

    op.create_table(
        "decision_review_actions",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("project_id", sa.BigInteger(), sa.ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("decision_id", sa.BigInteger(), sa.ForeignKey("model_decisions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("reviewer", sa.String(255), nullable=False),
        sa.Column("action", sa.String(20), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("correction_json", sa.JSON()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("action IN ('approve', 'reject', 'correct')", name="ck_decision_review_actions_action"),
    )
    op.create_index("ix_decision_review_actions_project_id", "decision_review_actions", ["project_id"])
    op.create_index("ix_decision_review_actions_decision_id", "decision_review_actions", ["decision_id"])
    op.create_index("ix_decision_review_actions_project_created", "decision_review_actions", ["project_id", "created_at", "id"])
    op.create_index("ix_decision_review_actions_decision_created", "decision_review_actions", ["decision_id", "created_at", "id"])


def downgrade() -> None:
    op.drop_index("ix_decision_review_actions_decision_created", table_name="decision_review_actions")
    op.drop_index("ix_decision_review_actions_project_created", table_name="decision_review_actions")
    op.drop_index("ix_decision_review_actions_decision_id", table_name="decision_review_actions")
    op.drop_index("ix_decision_review_actions_project_id", table_name="decision_review_actions")
    op.drop_table("decision_review_actions")

    op.drop_index("ix_model_decisions_project_decision", table_name="model_decisions")
    op.drop_index("ix_model_decisions_project_created", table_name="model_decisions")
    op.drop_index("ix_model_decisions_candidate_id", table_name="model_decisions")
    op.drop_index("ix_model_decisions_decision_run_id", table_name="model_decisions")
    op.drop_index("ix_model_decisions_project_id", table_name="model_decisions")
    op.drop_table("model_decisions")

    op.drop_index("ix_decision_runs_project_status", table_name="decision_runs")
    op.drop_index("ix_decision_runs_project_started", table_name="decision_runs")
    op.drop_index("ix_decision_runs_outbox_event_id", table_name="decision_runs")
    op.drop_index("ix_decision_runs_processing_job_id", table_name="decision_runs")
    op.drop_index("ix_decision_runs_project_id", table_name="decision_runs")
    op.drop_table("decision_runs")
    op.drop_table("project_decision_policies")
    bind = op.get_bind()
    if _column_exists(bind, "project_feature_flags", "decision_engine_enabled"):
        op.drop_column("project_feature_flags", "decision_engine_enabled")
