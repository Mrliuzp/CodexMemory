from __future__ import annotations

import hashlib

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select


def _factory_and_client() -> tuple[object, TestClient]:
    from codex_memory.db import create_postgres_test_engine, create_schema, create_session_factory
    from codex_memory.db_models import ApiKeyRow, ProjectRow, V11Base, V14Base
    from codex_memory.http_api import create_v1_app

    engine = create_postgres_test_engine()
    create_schema(engine)
    V11Base.metadata.create_all(engine)
    V14Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    with factory() as session:
        first = ProjectRow(project_key="erp", name="ERP")
        second = ProjectRow(project_key="crm", name="CRM")
        session.add_all([first, second])
        session.flush()
        session.add_all(
            [
                ApiKeyRow(project_id=first.id, token_hash=hashlib.sha256(b"append-token").hexdigest(), permissions=["append", "read"]),
                ApiKeyRow(project_id=first.id, token_hash=hashlib.sha256(b"read-token").hexdigest(), permissions=["read"]),
                ApiKeyRow(project_id=second.id, token_hash=hashlib.sha256(b"crm-token").hexdigest(), permissions=["append", "read"]),
            ]
        )
        session.commit()
    return factory, TestClient(create_v1_app(factory))


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _event(event_key: str, event_type: str = "PostToolUse", **kwargs: object) -> dict[str, object]:
    value: dict[str, object] = {
        "project_key": "erp",
        "session_key": "session-1",
        "event_key": event_key,
        "event_type": event_type,
        "payload": {"tool": "shell", "file_changes": [{"path": "src/a.py", "change_type": "modified"}]},
        "metadata": {"source": "test"},
    }
    value.update(kwargs)
    return value


def test_task_event_redacts_and_truncates_before_persistence() -> None:
    from codex_memory.v14_service import _redact, _truncate

    clean, changed = _redact({"authorization": "Bearer secret-value", "nested": {"token": "private"}})
    value, original_length, digest, truncated = _truncate("中" * 3000, 4096)
    assert changed is True
    assert "secret-value" not in str(clean)
    assert truncated is True
    assert original_length > 4096
    assert digest is not None
    assert value is not None and len(value.encode("utf-8")) <= 4096


def test_task_event_permission_isolation_and_idempotent_conflict() -> None:
    _, client = _factory_and_client()
    assert client.post("/api/v1/task-events", json=_event("denied"), headers=_auth("read-token")).status_code == 403
    assert client.post("/api/v1/task-events", json={**_event("wrong-project"), "project_key": "crm"}, headers=_auth("append-token")).status_code == 403

    first = client.post("/api/v1/task-events", json=_event("same"), headers=_auth("append-token"))
    duplicate = client.post("/api/v1/task-events", json=_event("same"), headers=_auth("append-token"))
    conflict = client.post("/api/v1/task-events", json=_event("same", result_summary="different"), headers=_auth("append-token"))
    assert first.status_code == 201
    assert duplicate.status_code == 200
    assert duplicate.json()["id"] == first.json()["id"]
    assert conflict.status_code == 409


def test_task_event_and_outbox_are_atomic() -> None:
    from codex_memory.db_models import OutboxEventRow, TaskEventRow

    factory, client = _factory_and_client()
    response = client.post("/api/v1/task-events", json=_event("atomic"), headers=_auth("append-token"))
    assert response.status_code == 201
    with factory() as session:
        event = session.get(TaskEventRow, response.json()["event_id"])
        outbox = session.scalar(select(OutboxEventRow).where(OutboxEventRow.aggregate_id == event.id, OutboxEventRow.aggregate_type == "task_event"))
        assert event is not None
        assert outbox is not None
        assert outbox.payload["event_id"] == event.id


def test_task_event_rolls_back_when_outbox_creation_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    from codex_memory.auth import Principal
    from codex_memory.db_models import TaskEventRow, TaskRunRow
    from codex_memory.v14_service import TaskEventService

    factory, _ = _factory_and_client()

    class BrokenOutbox:
        def __init__(self, **kwargs: object) -> None:
            del kwargs
            raise RuntimeError("模拟 Outbox 写入失败")

    monkeypatch.setattr("codex_memory.v14_service.OutboxEventRow", BrokenOutbox)
    with pytest.raises(RuntimeError, match="Outbox"):
        TaskEventService(factory).append_event(
            Principal(project_key="erp", permissions=frozenset({"append"})),
            project_key="erp",
            session_key="atomic-failure",
            event_key="failure",
            event_type="PostToolUse",
        )
    with factory() as session:
        assert session.scalar(select(TaskRunRow).where(TaskRunRow.session_key == "atomic-failure")) is None
        assert session.scalar(select(TaskEventRow).where(TaskEventRow.event_key == "failure")) is None


def test_worker_retry_revision_and_l1_projection() -> None:
    from codex_memory.db_models import MemoryRow, TaskReportRow, TaskRunRow
    from codex_memory.worker import run_v13_once

    factory, client = _factory_and_client()
    headers = _auth("append-token")
    assert client.post("/api/v1/task-events", json=_event("baseline", "PreToolUse", git={"branch": "main", "head": "abc", "status_porcelain": "", "diff_hash": "d"}), headers=headers).status_code == 201
    assert client.post("/api/v1/task-events", json=_event("stop", "Stop"), headers=headers).status_code == 201
    assert client.post("/api/v1/task-events", json=_event("end", "SessionEnd"), headers=headers).status_code == 201

    run_v13_once(factory, "worker-a")
    run_v13_once(factory, "worker-b")
    run_v13_once(factory, "worker-c")
    run_v13_once(factory, "worker-d")

    with factory() as session:
        run = session.scalar(select(TaskRunRow).where(TaskRunRow.session_key == "session-1"))
        reports = session.scalars(select(TaskReportRow).where(TaskReportRow.task_run_id == run.id).order_by(TaskReportRow.revision)).all()
        memories = session.scalars(select(MemoryRow).where(MemoryRow.project_id == run.project_id, MemoryRow.memory_type == "task_report")).all()
        assert run.status == "completed"
        assert [item.revision for item in reports] == [1, 2]
        assert [item.report_kind for item in reports] == ["checkpoint", "final"]
        assert len(memories) == 2
        assert {item.level for item in memories} == {"L1"}
        assert {item.memory_type for item in memories} == {"task_report"}


def test_admin_task_run_response_envelopes_and_project_filter() -> None:
    from codex_memory.worker import run_v13_once

    factory, client = _factory_and_client()
    headers = _auth("append-token")
    client.post("/api/v1/task-events", json=_event("admin-stop", "Stop"), headers=headers)
    run_v13_once(factory, "worker-admin")

    listing = client.get("/api/admin/v1/task-runs?project_key=erp", headers=headers)
    assert listing.status_code == 200
    assert {"data", "meta", "request_id"} == set(listing.json())
    assert {"id", "project_id", "session_key", "status", "started_at", "ended_at", "current_report_revision"} == set(listing.json()["data"][0])
    run_id = listing.json()["data"][0]["id"]
    detail = client.get(f"/api/admin/v1/task-runs/{run_id}", headers=headers)
    assert detail.status_code == 200
    assert {"data", "request_id"} == set(detail.json())
    assert {"id", "project_id", "session_key", "status", "started_at", "ended_at", "current_report_revision", "git_baseline", "events", "reports"} == set(detail.json()["data"])
    report = client.get(f"/api/admin/v1/task-runs/{run_id}/reports/1", headers=headers)
    assert report.status_code == 200
    assert {"data", "request_id"} == set(report.json())
    assert {"id", "project_id", "task_run_id", "source_event_id", "revision", "report_kind", "status", "report_json", "body", "content_hash", "uncertain", "truncated", "created_at", "file_changes"} == set(report.json()["data"])
