from __future__ import annotations

from alembic import op

from codex_memory.v11_models import V11Base

revision = "0004_v11_schema_tables"
down_revision = "0003_v11_additive_columns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    V11Base.metadata.create_all(op.get_bind())


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(V11Base.metadata.sorted_tables):
        if table.name in {"project_feature_flags", "project_processing_policies", "outbox_events", "processing_jobs", "job_attempts", "memory_candidates", "candidate_evidence", "candidate_policy_results", "embedding_profiles", "project_retrieval_profiles", "memory_chunks", "memory_embedding_vectors", "memory_search_documents", "retrieval_audits", "security_audits"}:
            table.drop(bind, checkfirst=True)
