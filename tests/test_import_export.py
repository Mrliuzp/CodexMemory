from __future__ import annotations

import hashlib
from fastapi.testclient import TestClient


def _factory_and_client() -> tuple[object, TestClient]:
    from codex_memory.db import create_schema, create_session_factory, create_sqlite_engine
    from codex_memory.db_models import ApiKeyRow, ProjectRow
    from codex_memory.http_api import create_v1_app
    from codex_memory.v11_models import V11Base

    engine = create_sqlite_engine()
    create_schema(engine)
    V11Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    with factory() as session:
        project = ProjectRow(project_key="erp", name="ERP")
        session.add(project)
        session.flush()
        session.add_all([
            ApiKeyRow(
                project_id=project.id,
                token_hash=hashlib.sha256(b"admin-token").hexdigest(),
                permissions=["read", "admin"],
            ),
        ])
        session.commit()
    return factory, TestClient(create_v1_app(factory))


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


_import_items = [
    {"type": "memory", "level": "L2", "memory_type": "knowledge",
     "title": "导入测试记忆", "content": {"text": "test"}, "scope": "project"},
    {"type": "memory", "level": "L3", "memory_type": "error_pattern",
     "title": "导入错误模式", "content": {"pattern": "timeout"}, "scope": "project"},
]


def test_import_preview() -> None:
    """导入预览应返回统计信息。"""
    _, client = _factory_and_client()
    resp = client.post(
        "/api/v1/admin/import/preview",
        headers=_auth("admin-token"),
        json={"project_key": "erp", "items": _import_items},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_items"] == 2
    assert data["memories"] == 2
    assert data["messages"] == 0


def test_import_execute_memories() -> None:
    """导入执行应创建记忆。"""
    factory, client = _factory_and_client()
    resp = client.post(
        "/api/v1/admin/import/execute",
        headers=_auth("admin-token"),
        json={"project_key": "erp", "items": _import_items},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["memories_created"] == 2
    assert data["messages_created"] == 0
    assert data["total"] == 2
    from codex_memory.db_models import MemoryRow
    from sqlalchemy import func, select
    with factory() as session:
        count = session.scalar(select(func.count(MemoryRow.id)))
        assert count >= 2


def test_export_memories() -> None:
    """导出应返回项目记忆。"""
    factory, client = _factory_and_client()
    client.post(
        "/api/v1/admin/import/execute",
        headers=_auth("admin-token"),
        json={"project_key": "erp", "items": [_import_items[0]]},
    )
    resp = client.get(
        "/api/v1/admin/export/projects/erp/memories",
        headers=_auth("admin-token"),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["memories"]) >= 1
    assert data["memories"][0]["title"] == "导入测试记忆"