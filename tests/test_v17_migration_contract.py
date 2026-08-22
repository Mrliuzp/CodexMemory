"""V1.7 迁移的静态契约测试。

这些测试只读取迁移源码，不连接数据库，也不执行 Alembic upgrade/downgrade。
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
VERSIONS_DIR = ROOT / "alembic" / "versions"
V17_MIGRATION = VERSIONS_DIR / "0028_v17_memory_windows.py"
V17_REVISION = "0028_v17_memory_windows"
V16_REVISION = "0027_v16_decision_contracts"


def _assignment(tree: ast.Module, name: str) -> Any:
    """读取迁移模块中简单赋值的字面量。"""

    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name) and target.id == name:
                return ast.literal_eval(node.value)
    raise AssertionError(f"迁移缺少 {name} 字段")


def _migration_metadata(path: Path) -> tuple[str, object]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return _assignment(tree, "revision"), _assignment(tree, "down_revision")


def _down_revision_ids(value: object) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, str):
        return {value}
    if isinstance(value, (tuple, list)):
        return {item for item in value if isinstance(item, str)}
    raise AssertionError(f"无法解析 down_revision: {value!r}")


def _create_table_names(source: str) -> set[str]:
    tree = ast.parse(source)
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != "create_table" or not node.args:
            continue
        table_name = node.args[0]
        if isinstance(table_name, ast.Constant) and isinstance(table_name.value, str):
            names.add(table_name.value)
    return names


def test_v17_is_the唯一_alembic_head_and_follows_v16() -> None:
    """V1.7 必须接在 V1.6 之后，且仓库只能保留一个 head。"""

    metadata = [_migration_metadata(path) for path in VERSIONS_DIR.glob("*.py")]
    revisions = [revision for revision, _ in metadata]
    assert len(revisions) == len(set(revisions)), "迁移 revision 不能重复"

    referenced = {
        down_revision
        for _, down_revision in metadata
        for down_revision in _down_revision_ids(down_revision)
    }
    heads = set(revisions) - referenced

    assert heads == {V17_REVISION}
    assert _migration_metadata(V17_MIGRATION) == (V17_REVISION, V16_REVISION)


def test_v17_declares_window_and_change_set_tables() -> None:
    source = V17_MIGRATION.read_text(encoding="utf-8")
    assert {
        "project_memory_window_policies",
        "memory_windows",
        "memory_window_messages",
        "memory_change_sets",
    } <= _create_table_names(source)


def test_v17_declares_revision_and_complete_memory_version_snapshot() -> None:
    source = V17_MIGRATION.read_text(encoding="utf-8")

    assert 'op.add_column("memories", sa.Column("revision"' in source
    assert "nullable=False" in source
    assert 'server_default="1"' in source

    snapshot_fields = {
        "title",
        "level",
        "memory_type",
        "scope",
        "scope_id",
        "confidence",
        "status",
        "deprecated",
        "content_hash",
        "source_change_set_id",
    }
    assert all(f'("{field}"' in source for field in snapshot_fields)
    assert 'op.create_unique_constraint("uq_memory_versions_memory_version"' in source
    assert '["memory_id", "version"]' in source


def test_v17_defaults_to_disabled_shadow_governance() -> None:
    source = V17_MIGRATION.read_text(encoding="utf-8")

    assert 'sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false())' in source
    assert 'sa.Column("status", sa.String(20), nullable=False, server_default="shadow")' in source
    assert "status IN ('shadow', 'approved', 'rejected', 'applied')" in source


def test_v17_downgrade_removes_reverse_foreign_key_before_change_sets() -> None:
    """PostgreSQL 降级必须先解除 memory_versions 的反向外键。"""

    source = V17_MIGRATION.read_text(encoding="utf-8")
    drop_helper = source.index("    _drop_memory_version_change_set_foreign_keys(bind)")
    drop_tables = source.index('for table in ("memory_change_sets"')
    assert drop_helper < drop_tables
    assert 'op.drop_constraint(name, "memory_versions", type_="foreignkey")' in source
    assert 'foreign_key.get("referred_table") != "memory_change_sets"' in source
    assert '"source_change_set_id" not in set(foreign_key.get("constrained_columns") or ())' in source


def test_create_schema_includes_v17_tables_without_running_migrations() -> None:
    """旧测试直接建 schema 时也必须拥有 V1.7 表，但不自动启用策略。"""

    from sqlalchemy import create_engine, inspect, text

    from codex_memory.persistence.db import create_schema

    engine = create_engine("sqlite://")
    create_schema(engine)
    tables = set(inspect(engine).get_table_names())
    assert {
        "project_memory_window_policies",
        "memory_windows",
        "memory_window_messages",
        "memory_change_sets",
    } <= tables

    with engine.begin() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM project_memory_window_policies")).scalar_one() == 0


def test_create_schema_policy_defaults_remain_disabled() -> None:
    """无迁移测试 schema 中显式创建策略时，默认仍为 shadow/关闭。"""

    from sqlalchemy import create_engine, text

    from codex_memory.persistence.db import create_schema

    engine = create_engine("sqlite://")
    create_schema(engine)
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO projects (id, project_key, status) VALUES (1, 'demo', 'active')"))
        connection.execute(text("INSERT INTO project_memory_window_policies (project_id) VALUES (1)"))
        row = connection.execute(
            text("SELECT enabled, mode, manual_apply_enabled FROM project_memory_window_policies WHERE project_id = 1")
        ).one()
    assert row.enabled in (False, 0)
    assert row.mode == "shadow"
    assert row.manual_apply_enabled in (False, 0)


def test_create_schema_does_not_shadow_v16_decision_runs_table() -> None:
    """V1.7 无迁移建表不能抢先创建 V1.6 的占位 decision_runs。"""

    from sqlalchemy import create_engine, inspect

    from codex_memory.persistence.db import create_schema
    from codex_memory.persistence.v16_models import V16Base

    engine = create_engine("sqlite://")
    create_schema(engine)
    assert "decision_runs" not in inspect(engine).get_table_names()
    V16Base.metadata.create_all(engine)
    columns = {column["name"] for column in inspect(engine).get_columns("decision_runs")}
    assert {"model", "prompt_version", "status", "metadata_json"} <= columns
