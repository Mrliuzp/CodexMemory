from __future__ import annotations

import hashlib

from fastapi.testclient import TestClient


def test_admin_flags_profiles_and_health_are_project_scoped() -> None:
    from codex_memory.db import create_schema, create_session_factory, create_sqlite_engine
    from codex_memory.db_models import ApiKeyRow, ProjectRow, V11Base
    from codex_memory.http_api import create_v1_app

    engine = create_sqlite_engine()
    create_schema(engine)
    V11Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    with factory() as session:
        project = ProjectRow(project_key="erp", name="ERP")
        session.add(project)
        session.flush()
        session.add(
            ApiKeyRow(
                project_id=project.id,
                token_hash=hashlib.sha256(b"admin-secret").hexdigest(),
                permissions=["admin", "read", "append"],
            )
        )
        session.commit()

    client = TestClient(create_v1_app(factory))
    headers = {"Authorization": "Bearer admin-secret"}
    flags = client.post(
        "/api/v1/admin/projects/erp/flags",
        headers=headers,
        json={"dense_retrieval_enabled": True},
    )
    assert flags.status_code == 200
    assert flags.json()["flags"]["dense_retrieval_enabled"] is True

    profile = client.post(
        "/api/v1/admin/profiles",
        headers=headers,
        json={
            "name": "admin-profile",
            "provider": "local",
            "model": "hash-v1",
            "dimension": 4,
        },
    )
    assert profile.status_code == 200
    activate = client.post(
        "/api/v1/admin/projects/erp/profile",
        headers=headers,
        json={"profile_id": profile.json()["id"]},
    )
    assert activate.status_code == 200
    assert activate.json()["active_embedding_profile_id"] == profile.json()["id"]

    health = client.get("/api/v1/health")
    assert health.status_code == 200
    assert health.json()["outbox"] == "ok"
def test_admin_jobs_retry_and_shadow_candidate_visibility() -> None:
    import hashlib

    from sqlalchemy import select
    from codex_memory.db import create_schema, create_session_factory, create_sqlite_engine
    from codex_memory.db_models import (
        ApiKeyRow,
        MemoryCandidateRow,
        ProcessingJobRow,
        ProjectRow,
        V11Base,
    )
    from codex_memory.http_api import create_v1_app

    engine = create_sqlite_engine()
    create_schema(engine)
    V11Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    with factory() as session:
        project = ProjectRow(project_key="erp", name="ERP")
        session.add(project)
        session.flush()
        session.add(
            ApiKeyRow(
                project_id=project.id,
                token_hash=hashlib.sha256(b"admin-secret").hexdigest(),
                permissions=["admin"],
            )
        )
        session.add(
            ProcessingJobRow(
                project_id=project.id,
                job_type="message.appended.v1",
                aggregate_type="message",
                aggregate_id=1,
                job_key="dead-job",
                payload_version="v1",
                payload={},
                status="dead",
                attempt_count=5,
            )
        )
        session.add(
            MemoryCandidateRow(
                project_id=project.id,
                task_type="error_memory",
                level="L3",
                scope="project",
                memory_type="error_memory",
                title="shadow",
                content={"text": "shadow"},
                status="shadow",
                abstain=True,
            )
        )
        session.commit()

    client = TestClient(create_v1_app(factory))
    headers = {"Authorization": "Bearer admin-secret"}
    hidden = client.get("/api/v1/admin/candidates", headers=headers, params={"project_key": "erp"})
    visible = client.get(
        "/api/v1/admin/candidates",
        headers=headers,
        params={"project_key": "erp", "include_shadow": "true"},
    )
    jobs = client.get(
        "/api/v1/admin/jobs",
        headers=headers,
        params={"project_key": "erp", "status": "dead"},
    )
    retry = client.post("/api/v1/admin/jobs/1/retry", headers=headers)

    assert hidden.status_code == 200
    assert hidden.json()["candidates"] == []
    assert len(visible.json()["candidates"]) == 1
    assert len(jobs.json()["jobs"]) == 1
    assert retry.status_code == 200
    assert retry.json()["status"] == "pending"