"""新增 V1.5 OpenAPI Revision 持久化表。"""

from alembic import op
import sqlalchemy as sa


revision = "0023_v15_openapi_revisions"
down_revision = "0022_v14_task_execution_reports"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "contract_services",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("project_id", sa.BigInteger(), sa.ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("service_key", sa.String(150), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("project_id", "service_key", name="uq_contract_services_project_key"),
    )
    op.create_index("ix_contract_services_project_id", "contract_services", ["project_id"])
    op.create_index("ix_contract_services_project_created", "contract_services", ["project_id", "created_at"])

    op.create_table(
        "contract_revisions",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("project_id", sa.BigInteger(), sa.ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("service_id", sa.BigInteger(), sa.ForeignKey("contract_services.id", ondelete="CASCADE"), nullable=False),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="proposed"),
        sa.Column("source_filename", sa.String(500), nullable=False),
        sa.Column("source_extension", sa.String(10), nullable=False),
        sa.Column("source_version", sa.String(20), nullable=False),
        sa.Column("normalized_version", sa.String(20), nullable=False, server_default="3.1.0"),
        sa.Column("profile_version", sa.String(20), nullable=False, server_default="v1"),
        sa.Column("source_document", sa.JSON(), nullable=False),
        sa.Column("normalized_document", sa.JSON(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("validation_summary", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("validation_result", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("markdown", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_by", sa.String(255), nullable=False, server_default="system"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("published_by", sa.String(255)),
        sa.UniqueConstraint("service_id", "revision_number", name="uq_contract_revisions_service_number"),
        sa.UniqueConstraint("service_id", "content_hash", name="uq_contract_revisions_service_hash"),
        sa.CheckConstraint("status IN ('proposed', 'published', 'superseded')", name="ck_contract_revisions_status"),
    )
    op.create_index("ix_contract_revisions_project_id", "contract_revisions", ["project_id"])
    op.create_index("ix_contract_revisions_project_status_created", "contract_revisions", ["project_id", "status", "created_at"])
    op.create_index("ix_contract_revisions_service_id", "contract_revisions", ["service_id"])
    op.create_index("ix_contract_revisions_service_status", "contract_revisions", ["service_id", "status"])

    op.create_table(
        "api_operations",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("project_id", sa.BigInteger(), sa.ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("service_id", sa.BigInteger(), sa.ForeignKey("contract_services.id", ondelete="CASCADE"), nullable=False),
        sa.Column("revision_id", sa.BigInteger(), sa.ForeignKey("contract_revisions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("method", sa.String(10), nullable=False),
        sa.Column("path", sa.String(1000), nullable=False),
        sa.Column("operation_id", sa.String(255), nullable=False),
        sa.Column("operation_hash", sa.String(64), nullable=False),
        sa.Column("summary", sa.String(1000)),
        sa.Column("tags_json", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("operation_json", sa.JSON(), nullable=False),
        sa.UniqueConstraint("revision_id", "method", "path", name="uq_api_operations_revision_route"),
        sa.UniqueConstraint("revision_id", "operation_id", name="uq_api_operations_revision_operation_id"),
    )
    op.create_index("ix_api_operations_project_id", "api_operations", ["project_id"])
    op.create_index("ix_api_operations_service_id", "api_operations", ["service_id"])
    op.create_index("ix_api_operations_revision_id", "api_operations", ["revision_id"])
    op.create_index("ix_api_operations_project_route", "api_operations", ["project_id", "method", "path"])
    op.create_index("ix_api_operations_revision_order", "api_operations", ["revision_id", "path", "method"])
    op.add_column("contract_services", sa.Column("current_published_revision_id", sa.BigInteger(), nullable=True))
    op.create_index("ix_contract_services_current_published_revision_id", "contract_services", ["current_published_revision_id"])
    op.create_foreign_key("fk_contract_services_current_published_revision", "contract_services", "contract_revisions", ["current_published_revision_id"], ["id"], ondelete="SET NULL")


def downgrade() -> None:
    op.drop_constraint("fk_contract_services_current_published_revision", "contract_services", type_="foreignkey")
    op.drop_index("ix_contract_services_current_published_revision_id", table_name="contract_services")
    op.drop_column("contract_services", "current_published_revision_id")
    op.drop_index("ix_api_operations_revision_order", table_name="api_operations")
    op.drop_index("ix_api_operations_project_route", table_name="api_operations")
    op.drop_index("ix_api_operations_revision_id", table_name="api_operations")
    op.drop_index("ix_api_operations_service_id", table_name="api_operations")
    op.drop_index("ix_api_operations_project_id", table_name="api_operations")
    op.drop_table("api_operations")
    op.drop_index("ix_contract_revisions_service_status", table_name="contract_revisions")
    op.drop_index("ix_contract_revisions_service_id", table_name="contract_revisions")
    op.drop_index("ix_contract_revisions_project_status_created", table_name="contract_revisions")
    op.drop_index("ix_contract_revisions_project_id", table_name="contract_revisions")
    op.drop_table("contract_revisions")
    op.drop_index("ix_contract_services_project_created", table_name="contract_services")
    op.drop_index("ix_contract_services_project_id", table_name="contract_services")
    op.drop_table("contract_services")
