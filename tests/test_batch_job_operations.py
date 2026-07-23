from __future__ import annotations

import hashlib
from sqlalchemy import select

from fastapi.testclient import TestClient
from codex_memory.db_models import ProcessingJobRow


def _factory_and_client() -> tuple[object, TestClient]:
    from codex_memory.db import create_schema, create_session_factory, create_sqlite_engine
    from codex_memory.db_models import ApiKeyRow, ProcessingJobRow, ProjectRow
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
                token_hash=hashlib.sha256(b"read-token").hexdigest(),
                permissions=["read"],
            ),
            ApiKeyRow(
                project_id=project.id,
                token_hash=hashlib.sha256(b"admin-token").hexdigest(),
                permissions=["append", "read", "admin"],
            ),
        ])
        # Create sample jobs
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        for i in range(3):
            session.add(ProcessingJobRow(
                project_id=project.id,
                job_type="test.v1",
                aggregate_type="message",
                aggregate_id=i + 1,
                job_key=f"test-job-{i}",
                payload_version="v1",
                payload={},
                status="dead",
                priority=0,
                max_attempts=3,
                attempt_count=2,
                last_error_message="test error",
                created_at=now,
                updated_at=now,
                next_attempt_at=now,
            ))
        for i in range(2):
            session.add(ProcessingJobRow(
                project_id=project.id,
                job_type="test.v1",
                aggregate_type="message",
                aggregate_id=i + 10,
                job_key=f"pending-job-{i}",
                payload_version="v1",
                payload={},
                status="pending",
                priority=0,
                max_attempts=3,
                created_at=now,
                updated_at=now,
                next_attempt_at=now,
            ))
        for i in range(2):
            session.add(ProcessingJobRow(
                project_id=project.id,
                job_type="test.v1",
                aggregate_type="message",
                aggregate_id=i + 20,
                job_key=f"completed-job-{i}",
                payload_version="v1",
                payload={},
                status="completed",
                priority=0,
                max_attempts=3,
                attempt_count=1,
                created_at=now,
                updated_at=now,
                next_attempt_at=now,
                completed_at=now,
            ))
        session.commit()
    return factory, TestClient(create_v1_app(factory))


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_batch_retry_resets_dead_jobs() -> None:
    """批量重试应将 dead 作业重置为 pending 状态。"""
    factory, client = _factory_and_client()
    resp = client.post(
        "/api/v1/admin/jobs/batch-retry",
        headers=_auth("admin-token"),
        json={"status_filter": "dead"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["retried"] == 3
    # Verify all are now pending
    with factory() as session:
        dead = session.scalar(select(ProcessingJobRow).where(ProcessingJobRow.status == "dead").limit(1))
        assert dead is None
        pending = session.scalar(select(ProcessingJobRow).where(ProcessingJobRow.status == "pending").limit(1))
        assert pending is not None


def test_batch_retry_by_job_ids() -> None:
    """通过 job_ids 指定要重试的作业。"""
    factory, client = _factory_and_client()
    # Get job IDs from fixture
    with factory() as session:
        jobs = session.scalars(select(ProcessingJobRow).where(ProcessingJobRow.status == "dead").limit(2)).all()
        target_ids = [j.id for j in jobs]
    resp = client.post(
        "/api/v1/admin/jobs/batch-retry",
        headers=_auth("admin-token"),
        json={"job_ids": target_ids},
    )
    assert resp.status_code == 200
    assert resp.json()["retried"] == 2
    assert resp.json()["job_ids"] == target_ids


def test_batch_cancel_cancels_pending_jobs() -> None:
    """批量取消应将 pending 作业标记为 dead。"""
    factory, client = _factory_and_client()
    resp = client.post(
        "/api/v1/admin/jobs/batch-cancel",
        headers=_auth("admin-token"),
        json={"status_filter": "pending"},
    )
    assert resp.status_code == 200
    assert resp.json()["cancelled"] == 2
    # Verify dead jobs (were dead before) remain dead
    with factory() as session:
        all_dead = session.scalars(
            select(ProcessingJobRow).where(ProcessingJobRow.status == "dead")
        ).all()
    # 3 original dead + 2 newly cancelled = 5
    assert len(all_dead) == 5


def test_batch_cancel_requires_admin_permission() -> None:
    """读权限不足以执行批量取消。"""
    _, client = _factory_and_client()
    resp = client.post(
        "/api/v1/admin/jobs/batch-cancel",
        headers=_auth("read-token"),
        json={"status_filter": "pending"},
    )
    assert resp.status_code == 403


def test_cleanup_removes_old_completed_jobs() -> None:
    """清理应删除指定状态的旧作业。"""
    factory, client = _factory_and_client()
    resp = client.post(
        "/api/v1/admin/jobs/cleanup",
        headers=_auth("admin-token"),
        json={"older_than_days": 0, "status": "completed"},
    )
    assert resp.status_code == 200
    assert resp.json()["deleted"] == 2
    with factory() as session:
        remaining = session.scalar(select(ProcessingJobRow).where(ProcessingJobRow.status == "completed").limit(1))
        assert remaining is None