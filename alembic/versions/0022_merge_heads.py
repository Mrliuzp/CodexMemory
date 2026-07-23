"""合并历史导入与异步导入迁移链。"""

from __future__ import annotations

revision = "0022_merge_heads"
down_revision = ("0013_legacy_memory_fingerprints", "0021_v131_memory_scope")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
