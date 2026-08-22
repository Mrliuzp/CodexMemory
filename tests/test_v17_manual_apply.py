"""V1.7 人工应用的并发与版本化契约。"""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _source() -> str:
    return (ROOT / "src/codex_memory/pipelines/v11_candidates.py").read_text(encoding="utf-8")


def test_apply_locks_changeset_and_target() -> None:
    source = _source()
    assert "MemoryChangeSetRow).where(MemoryChangeSetRow.id == change_set_id).with_for_update()" in source
    assert "MemoryRow.id == output.target_memory_id" in source
    assert ".with_for_update())" in source


def test_update_lazily_repairs_complete_v1_snapshot() -> None:
    source = _source()
    assert "旧数据可能没有完整 v1 快照" in source
    assert "source_change_set_id=change_set.id" in source
    assert "MemoryVersionRow.memory_id == target.id" in source


def test_manual_apply_remains_explicitly_enabled_and_shadow_only() -> None:
    source = _source()
    assert "not policy.manual_apply_enabled" in source
    assert "policy.mode != \"shadow\"" in source
