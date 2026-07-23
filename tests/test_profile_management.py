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


def test_create_profile() -> None:
    """创建嵌入配置。"""
    _, client = _factory_and_client()
    resp = client.post(
        "/api/v1/admin/profiles",
        headers=_auth("admin-token"),
        json={"name": "test-emb", "provider": "openai", "model": "text-embedding-3-small", "dimension": 1536},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "test-emb"
    assert data["dimension"] == 1536


def test_activate_profile() -> None:
    """为项目激活嵌入配置。"""
    _, client = _factory_and_client()
    # First create a profile
    create_resp = client.post(
        "/api/v1/admin/profiles",
        headers=_auth("admin-token"),
        json={"name": "act-emb", "provider": "openai", "model": "text-embedding-3-small", "dimension": 768},
    )
    assert create_resp.status_code == 200
    profile_id = create_resp.json()["id"]

    # Activate it for the project
    resp = client.post(
        "/api/v1/admin/projects/erp/profile",
        headers=_auth("admin-token"),
        json={"profile_id": profile_id},
    )
    assert resp.status_code == 200
    assert resp.json()["active_embedding_profile_id"] == profile_id


def test_profile_backfill() -> None:
    """验证 backfill 端点可用。"""
    from codex_memory.db_models import MemoryRow, ProjectRow
    from sqlalchemy import select
    from datetime import datetime, timezone

    factory, client = _factory_and_client()
    # Create a profile
    create_resp = client.post(
        "/api/v1/admin/profiles",
        headers=_auth("admin-token"),
        json={"name": "bf-emb", "provider": "openai", "model": "text-embedding-3-small", "dimension": 768},
    )
    assert create_resp.status_code == 200
    profile_id = create_resp.json()["id"]

    # Create a memory to backfill
    with factory() as session:
        project = session.scalar(
            select(ProjectRow).where(ProjectRow.project_key == "erp")
        )
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        mem = MemoryRow(
            project_id=project.id,
            level="L1",
            memory_type="working",
            title="backfill-test",
            content={"text": "test"},
            confidence=0.9,
            status="active",
            usage_count=0,
            scope="project",
            source_kind="agent",
            review_status="approved",
            created_at=now,
            updated_at=now,
        )
        session.add(mem)
        session.commit()
        mem_id = mem.id

    resp = client.post(
        f"/api/v1/admin/profiles/{profile_id}/backfill",
        headers=_auth("admin-token"),
        json={"project_id": project.id, "memory_id": mem_id},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "completed"