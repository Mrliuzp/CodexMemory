from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def admin_experience() -> tuple[TestClient, Any, dict[str, int]]:
    from codex_memory.db import create_postgres_test_engine, create_schema, create_session_factory
    from codex_memory.db_models import ApiKeyRow, AuditLogRow, MemoryRow, MessageRow, ProjectRow, SessionRow
    from codex_memory.http_api import create_v1_app
    from codex_memory.persistence.v11_models import (
        ImportBatchRow,
        MemoryCandidateRow,
        OutboxEventRow,
        ProcessingJobRow,
        RetrievalAuditRow,
        V11Base,
    )
    from codex_memory.persistence.v12_models import KnowledgeScopeRow, V12Base
    from codex_memory.persistence.v14_models import TaskEventRow, TaskReportRow, TaskRunRow, V14Base
    from codex_memory.persistence.v15_models import V15Base

    engine = create_postgres_test_engine()
    create_schema(engine)
    V11Base.metadata.create_all(engine)
    V12Base.metadata.create_all(engine)
    V14Base.metadata.create_all(engine)
    V15Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    now = datetime(2026, 7, 15, 8, 0, tzinfo=timezone.utc)
    long_prompt = "优化可视化任务 " + "甲" * 180

    with factory() as session:
        project_a = ProjectRow(project_key="project-a", name="甲项目", repository="git/a")
        project_b = ProjectRow(project_key="project-b", name="乙项目", repository="git/b")
        session.add_all([project_a, project_b])
        session.flush()
        session.add_all(
            [
                ApiKeyRow(project_id=project_a.id, token_hash=hashlib.sha256(b"reader-a").hexdigest(), permissions=["read"]),
                ApiKeyRow(project_id=project_a.id, token_hash=hashlib.sha256(b"admin-a").hexdigest(), permissions=["admin", "read"]),
                ApiKeyRow(project_id=project_b.id, token_hash=hashlib.sha256(b"reader-b").hexdigest(), permissions=["read"]),
            ]
        )
        scope_a = KnowledgeScopeRow(project_id=project_a.id, scope_key="default", name="默认作用域", is_default=True)
        scope_a_team = KnowledgeScopeRow(project_id=project_a.id, scope_key="team", name="团队作用域")
        scope_b = KnowledgeScopeRow(project_id=project_b.id, scope_key="default", name="默认作用域", is_default=True)
        session.add_all([scope_a, scope_a_team, scope_b])
        session.flush()

        conversation_a = SessionRow(project_id=project_a.id, session_key="conversation-a")
        conversation_b = SessionRow(project_id=project_b.id, session_key="conversation-b")
        session.add_all([conversation_a, conversation_b])
        session.flush()
        message_a = MessageRow(
            project_id=project_a.id,
            session_id=conversation_a.id,
            event_key="message-a",
            role="user",
            content="筛选针关键字",
            content_hash="a" * 64,
            source="codex",
            created_at=now,
        )
        message_b = MessageRow(
            project_id=project_b.id,
            session_id=conversation_b.id,
            event_key="message-b",
            role="user",
            content="筛选针关键字",
            content_hash="b" * 64,
            source="codex",
            created_at=now,
        )
        session.add_all([message_a, message_b])
        session.flush()
        session.add_all(
            [
                MemoryCandidateRow(
                    project_id=project_a.id,
                    source_message_id=message_a.id,
                    task_type="knowledge",
                    level="L2",
                    scope="project",
                    memory_type="rule",
                    title="筛选针候选",
                    content={"summary": "筛选针"},
                    model_confidence=0.91,
                    status="generated",
                    created_at=now,
                ),
                MemoryCandidateRow(
                    project_id=project_a.id,
                    source_message_id=message_a.id,
                    task_type="knowledge",
                    level="L1",
                    scope="project",
                    memory_type="fact",
                    title="其他候选",
                    content={"summary": "其他"},
                    model_confidence=0.5,
                    status="published",
                    created_at=now,
                ),
                MemoryCandidateRow(
                    project_id=project_b.id,
                    source_message_id=message_b.id,
                    task_type="knowledge",
                    level="L2",
                    scope="project",
                    memory_type="rule",
                    title="筛选针候选",
                    content={"summary": "不得越权"},
                    model_confidence=0.99,
                    status="generated",
                    created_at=now,
                ),
                MemoryRow(
                    project_id=project_a.id,
                    level="L1",
                    memory_type="fact",
                    title="筛选针记忆",
                    content={"summary": "筛选针"},
                    confidence=0.8,
                    status="accepted",
                    scope="project",
                    created_at=now,
                ),
            ]
        )
        session.add(
            ProcessingJobRow(
                project_id=project_a.id,
                job_type="build_memory",
                aggregate_type="message",
                aggregate_id=message_a.id,
                job_key="筛选针-job",
                payload_version="v1",
                payload={},
                status="failed",
                last_error_message="筛选针失败",
                created_at=now,
            )
        )
        session.add(
            OutboxEventRow(
                project_id=project_a.id,
                aggregate_type="message",
                aggregate_id=message_a.id,
                event_type="memory.failed.v1",
                payload_version="v1",
                payload={},
                status="dead",
                created_at=now,
            )
        )
        session.add(
            RetrievalAuditRow(
                project_id=project_a.id,
                query_hash="c" * 64,
                retrieval_mode="lexical",
                degraded=True,
                degraded_reason="测试降级",
                parameters={},
                result_ids=[],
            )
        )
        session.add(
            ImportBatchRow(
                project_id=project_a.id,
                scope_id=scope_a.id,
                scope_key="default",
                status="processing",
                source_type="history",
                created_at=now,
            )
        )
        session.add(
            AuditLogRow(
                project_id=project_a.id,
                event_type="admin.filtered",
                subject_type="memory",
                subject_id="筛选针",
                created_at=now,
            )
        )

        run_a = TaskRunRow(project_id=project_a.id, session_key="task-a", status="completed", started_at=now, current_report_revision=1)
        run_b = TaskRunRow(project_id=project_b.id, session_key="task-b", status="completed", started_at=now)
        session.add_all([run_a, run_b])
        session.flush()
        prompt_event = TaskEventRow(
            project_id=project_a.id,
            task_run_id=run_a.id,
            event_key="prompt-a",
            event_type="UserPromptSubmit",
            sequence_no=1,
            occurred_at=now,
            payload_json={"metadata": {"prompt": {"value": long_prompt, "truncated": False}}},
            metadata_json={},
            content_hash="d" * 64,
            original_length=len(long_prompt.encode("utf-8")),
        )
        session.add(prompt_event)
        session.flush()
        session.add(
            TaskReportRow(
                project_id=project_a.id,
                task_run_id=run_a.id,
                source_event_id=prompt_event.id,
                revision=1,
                report_kind="final",
                status="completed",
                report_json={},
                body="任务报告",
                content_hash="e" * 64,
                uncertain=True,
            )
        )
        session.commit()
        ids = {"project_a": project_a.id, "task_a": run_a.id}

    return TestClient(create_v1_app(factory)), factory, ids


def _auth(token: str = "reader-a") -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_admin_me_dashboard_and_projects_are_project_scoped(admin_experience: tuple[TestClient, Any, dict[str, int]]) -> None:
    client, _, ids = admin_experience

    identity = client.get("/api/admin/v1/me", headers=_auth()).json()["data"]
    assert identity == {
        "project_key": "project-a",
        "permissions": ["read"],
        "display_name": None,
        "auth_type": "api_key",
        "expires_at": None,
    }

    dashboard = client.get("/api/admin/v1/dashboard", params={"project_key": "project-a"}, headers=_auth())
    assert dashboard.status_code == 200
    data = dashboard.json()["data"]
    assert {key: data[key] for key in ("raw_records", "candidates", "memories", "jobs")} == {
        "raw_records": 1,
        "candidates": 2,
        "memories": 1,
        "jobs": 1,
    }
    assert data["attention"] == {
        "pending_candidates": 1,
        "failed_jobs": 1,
        "dead_letters": 1,
        "active_imports": 1,
        "uncertain_task_runs": 1,
        "proposed_revisions": 0,
    }
    assert data["pipeline"]["l1"] == 1
    assert client.get("/api/admin/v1/dashboard", params={"project_key": "project-b"}, headers=_auth()).status_code == 403

    projects = client.get("/api/admin/v1/projects", headers=_auth()).json()["data"]
    assert projects == [{"id": ids["project_a"], "project_key": "project-a", "name": "甲项目", "repository": "git/a", "status": "active", "scope_count": 2}]


def test_admin_resource_filters_are_explicit_and_isolated(admin_experience: tuple[TestClient, Any, dict[str, int]]) -> None:
    client, _, ids = admin_experience
    common = {"project_key": "project-a", "created_from": "2026-07-01T00:00:00Z"}
    cases = [
        ("raw-records", {**common, "role": "user", "keyword": "筛选针"}),
        ("candidates", {**common, "status": "generated", "level": "L2", "memory_type": "rule", "keyword": "筛选针"}),
        ("memories", {**common, "status": "accepted", "level": "L1", "memory_type": "fact", "keyword": "筛选针"}),
        ("jobs", {**common, "status": "failed", "job_type": "build_memory", "keyword": "筛选针"}),
        ("outbox-events", {**common, "status": "dead", "event_type": "memory.failed.v1"}),
        ("retrieval-audits", {"project_key": "project-a", "degraded": "true", "retrieval_mode": "lexical"}),
        ("audit-events", {**common, "event_type": "admin.filtered", "subject_type": "memory"}),
    ]
    for route, params in cases:
        response = client.get(f"/api/admin/v1/{route}", params=params, headers=_auth())
        assert response.status_code == 200, (route, response.text)
        assert response.json()["meta"]["total"] == 1, route
        assert {item["project_id"] for item in response.json()["data"]} == {ids["project_a"]}

    candidate = client.get("/api/admin/v1/candidates", params=cases[1][1], headers=_auth()).json()["data"][0]
    assert candidate["model_confidence"] == pytest.approx(0.91)
    pending = client.get(
        "/api/admin/v1/candidates",
        params={"project_key": "project-a", "status": "pending"},
        headers=_auth(),
    )
    assert pending.json()["meta"]["total"] == 1
    invalid_range = client.get(
        "/api/admin/v1/memories",
        params={"project_key": "project-a", "created_from": "2026-08-01T00:00:00Z", "created_to": "2026-07-01T00:00:00Z"},
        headers=_auth(),
    )
    assert invalid_range.status_code == 422
    assert invalid_range.json()["error"]["code"] == "invalid_date_range"


def test_admin_task_run_filters_and_safe_excerpt(admin_experience: tuple[TestClient, Any, dict[str, int]]) -> None:
    client, _, ids = admin_experience
    uncertain_only = client.get(
        "/api/admin/v1/task-runs",
        params={"project_key": "project-a", "uncertain": "true"},
        headers=_auth(),
    )
    assert uncertain_only.status_code == 200
    assert uncertain_only.json()["meta"]["total"] == 1
    for name, params in (
        ("状态", {"project_key": "project-a", "uncertain": "true", "status": "completed"}),
        ("关键词", {"project_key": "project-a", "uncertain": "true", "keyword": "可视化"}),
        ("时间", {"project_key": "project-a", "uncertain": "true", "started_from": "2026-07-01T00:00:00Z", "started_to": "2026-08-01T00:00:00Z"}),
    ):
        filtered = client.get("/api/admin/v1/task-runs", params=params, headers=_auth())
        assert filtered.status_code == 200, name
        assert filtered.json()["meta"]["total"] == 1, name
    response = client.get(
        "/api/admin/v1/task-runs",
        params={
            "project_key": "project-a",
            "status": "completed",
            "uncertain": "true",
            "keyword": "可视化",
            "started_from": "2026-07-01T00:00:00Z",
            "started_to": "2026-08-01T00:00:00Z",
        },
        headers=_auth(),
    )
    assert response.status_code == 200
    assert response.json()["meta"]["total"] == 1
    item = response.json()["data"][0]
    assert item["id"] == ids["task_a"]
    assert item["project_key"] == "project-a"
    assert item["prompt_excerpt"].startswith("优化可视化任务")
    assert len(item["prompt_excerpt"]) == 160
    assert item["prompt_truncated"] is True
    assert item["uncertain"] is True

    no_match = client.get(
        "/api/admin/v1/task-runs",
        params={"project_key": "project-a", "uncertain": "false"},
        headers=_auth(),
    )
    assert no_match.status_code == 200
    assert no_match.json()["meta"]["total"] == 0


def test_admin_import_batch_status_filter(admin_experience: tuple[TestClient, Any, dict[str, int]]) -> None:
    client, _, _ = admin_experience
    response = client.get(
        "/api/admin/v1/import-batches",
        params={"project_key": "project-a", "status": "active"},
        headers=_auth(),
    )
    assert response.status_code == 200
    assert response.json()["meta"]["total"] == 1
    assert response.json()["data"][0]["status"] == "processing"


def test_admin_contract_revision_exposes_computed_size(admin_experience: tuple[TestClient, Any, dict[str, int]]) -> None:
    client, _, _ = admin_experience
    created = client.post(
        "/api/admin/v1/contract-services",
        json={"project_key": "project-a", "service_key": "experience", "name": "体验服务"},
        headers=_auth("admin-a"),
    )
    assert created.status_code == 200
    service_id = created.json()["data"]["id"]
    document = {
        "openapi": "3.0.3",
        "info": {"title": "体验服务", "version": "1.0.0"},
        "paths": {"/health": {"get": {"operationId": "getHealth", "responses": {"200": {"description": "成功"}}}}},
    }
    uploaded = client.post(
        f"/api/admin/v1/contract-services/{service_id}/revisions",
        files={"file": ("experience.json", json.dumps(document, ensure_ascii=False).encode("utf-8"), "application/json")},
        headers=_auth("admin-a"),
    )
    assert uploaded.status_code == 200, uploaded.text
    assert uploaded.json()["data"]["size_bytes"] > 0
    detail = client.get(f"/api/admin/v1/contract-services/{service_id}", headers=_auth())
    assert detail.status_code == 200
    assert detail.json()["data"]["revisions"][0]["size_bytes"] == uploaded.json()["data"]["size_bytes"]
