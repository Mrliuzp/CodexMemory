from __future__ import annotations

import hashlib
from fastapi.testclient import TestClient
from codex_memory.db_models import MemoryRow, ProjectRow


def _factory_and_client() -> tuple[object, TestClient]:
    from codex_memory.db import create_schema, create_session_factory, create_sqlite_engine
    from codex_memory.db_models import ApiKeyRow
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
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        session.add(MemoryRow(
            project_id=project.id,
            level="L1",
            memory_type="working",
            title="测试记忆",
            content={"summary": "这是一条测试记忆"},
            confidence=0.9,
            status="active",
            usage_count=0,
            scope="project",
            source_kind="agent",
            review_status="approved",
            created_at=now,
            updated_at=now,
        ))
        session.flush()
        session.add(MemoryRow(
            project_id=project.id,
            level="L2",
            memory_type="knowledge",
            title="可删除的记忆",
            content={"summary": "将被删除"},
            confidence=0.8,
            status="active",
            usage_count=0,
            scope="project",
            source_kind="agent",
            review_status="approved",
            created_at=now,
            updated_at=now,
        ))
        session.commit()
    return factory, TestClient(create_v1_app(factory))


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _get_memory_id(factory, index=0):
    with factory() as session:
        memories = session.scalars(
            __import__("sqlalchemy").select(MemoryRow).order_by(MemoryRow.id)
        ).all()
        return memories[index].id


def test_update_memory_title() -> None:
    """更新记忆标题。"""
    factory, client = _factory_and_client()
    mid = _get_memory_id(factory, 0)
    resp = client.put(
        f"/api/v1/admin/memories/{mid}",
        headers=_auth("admin-token"),
        json={"title": "新标题"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["title"] == "新标题"
    with factory() as session:
        mem = session.get(MemoryRow, mid)
        assert mem.title == "新标题"


def test_update_memory_content() -> None:
    """更新记忆内容。"""
    factory, client = _factory_and_client()
    mid = _get_memory_id(factory, 0)
    resp = client.put(
        f"/api/v1/admin/memories/{mid}",
        headers=_auth("admin-token"),
        json={"content": {"summary": "更新后的内容"}},
    )
    assert resp.status_code == 200
    with factory() as session:
        mem = session.get(MemoryRow, mid)
        assert mem.content["summary"] == "更新后的内容"


def test_delete_memory() -> None:
    """删除记忆。"""
    factory, client = _factory_and_client()
    mid = _get_memory_id(factory, 1)  # second memory
    resp = client.delete(
        f"/api/v1/admin/memories/{mid}",
        headers=_auth("admin-token"),
    )
    assert resp.status_code == 200
    assert resp.json()["deleted"] == mid
    with factory() as session:
        assert session.get(MemoryRow, mid) is None


def test_delete_nonexistent_memory() -> None:
    """删除不存在的记忆返回 404。"""
    _, client = _factory_and_client()
    resp = client.delete(
        "/api/v1/admin/memories/99999",
        headers=_auth("admin-token"),
    )
    assert resp.status_code == 404


def test_change_memory_level() -> None:
    """变更记忆层级。"""
    factory, client = _factory_and_client()
    mid = _get_memory_id(factory, 0)
    assert mid is not None
    resp = client.post(
        f"/api/v1/admin/memories/{mid}/level",
        headers=_auth("admin-token"),
        json={"level": "L3"},
    )
    assert resp.status_code == 200
    assert resp.json()["level"] == "L3"
    with factory() as session:
        mem = session.get(MemoryRow, mid)
        assert mem.level == "L3"


def test_change_memory_level_invalid() -> None:
    """无效层级返回 422。"""
    _, client = _factory_and_client()
    resp = client.post(
        "/api/v1/admin/memories/1/level",
        headers=_auth("admin-token"),
        json={"level": "L4"},
    )
    assert resp.status_code == 422