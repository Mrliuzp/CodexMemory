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
        from datetime import datetime, timezone
        import hashlib as hl
        from codex_memory.db_models import MemoryRow
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        session.add(MemoryRow(
            project_id=project.id, level="L2", memory_type="knowledge",
            title="迁移测试记忆", content={"text": "test"},
            confidence=0.5, status="active", usage_count=0,
            scope="project", source_kind="import",
            review_status="pending", created_at=now, updated_at=now,
        ))
        session.commit()
    return factory, TestClient(create_v1_app(factory))


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_migration_status() -> None:
    """迁移状态端点应返回正确结构。"""
    _, client = _factory_and_client()
    resp = client.get("/api/v1/admin/migrations", headers=_auth("admin-token"))
    assert resp.status_code == 200
    data = resp.json()
    assert "current_revision" in data
    assert "revisions" in data
    assert "pending_revisions" in data
    assert "up_to_date" in data
    assert isinstance(data["revisions"], list)
    assert isinstance(data["up_to_date"], bool)