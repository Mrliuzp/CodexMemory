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


def test_cleanup_messages() -> None:
    """清理消息端点应返回统计信息。"""
    _, client = _factory_and_client()
    resp = client.post(
        "/api/v1/admin/cleanup/messages",
        headers=_auth("admin-token"),
        json={"project_key": "erp", "older_than_days": 30, "dry_run": True},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["project_key"] == "erp"
    assert "deleted" in data
    assert data["dry_run"] is True


def test_cleanup_memories() -> None:
    """清理记忆端点应返回统计信息。"""
    _, client = _factory_and_client()
    resp = client.post(
        "/api/v1/admin/cleanup/memories",
        headers=_auth("admin-token"),
        json={"project_key": "erp", "older_than_days": 30, "dry_run": True},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["project_key"] == "erp"
    assert "deleted" in data


def test_cleanup_jobs() -> None:
    """清理作业端点应返回统计信息。"""
    _, client = _factory_and_client()
    resp = client.post(
        "/api/v1/admin/cleanup/jobs",
        headers=_auth("admin-token"),
        json={"project_key": "erp", "older_than_days": 30, "dry_run": True},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["project_key"] == "erp"
    assert "deleted" in data


def test_archive_status() -> None:
    """归档状态端点应返回项目存档信息。"""
    _, client = _factory_and_client()
    resp = client.get(
        "/api/v1/admin/archive/projects/erp",
        headers=_auth("admin-token"),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["project_key"] == "erp"
    assert "pending_count" in data


def test_cleanup_nonexistent_project() -> None:
    """清理不存在的项目应返回 404。"""
    _, client = _factory_and_client()
    resp = client.post(
        "/api/v1/admin/cleanup/messages",
        headers=_auth("admin-token"),
        json={"project_key": "nonexistent", "older_than_days": 30, "dry_run": True},
    )
    assert resp.status_code == 404