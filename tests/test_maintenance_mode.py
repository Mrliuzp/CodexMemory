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
        session.add_all(
            [
                ApiKeyRow(
                    project_id=project.id,
                    token_hash=hashlib.sha256(b"append-token").hexdigest(),
                    permissions=["append", "read"],
                ),
                ApiKeyRow(
                    project_id=project.id,
                    token_hash=hashlib.sha256(b"admin-token").hexdigest(),
                    permissions=["append", "read", "admin"],
                ),
            ]
        )
        session.commit()
    return factory, TestClient(create_v1_app(factory))


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _append_payload() -> dict[str, str]:
    return {
        "project_key": "erp",
        "session_key": "migration-session",
        "event_key": "migration-session:1:user",
        "role": "user",
        "content": "迁移期间不应写入新对话。",
    }


def test_maintenance_mode_rejects_new_append_requests() -> None:
    from codex_memory.maintenance import MaintenanceService

    factory, client = _factory_and_client()
    MaintenanceService(factory).set_enabled(True, reason="迁移冻结", actor="operator")

    response = client.post("/api/v1/append", headers=_auth("append-token"), json=_append_payload())

    assert response.status_code == 503
    assert response.json()["detail"] == "maintenance_mode"


def test_maintenance_mode_stops_worker_iteration_before_claiming_jobs(monkeypatch) -> None:
    from codex_memory import worker
    from codex_memory.maintenance import MaintenanceService

    factory, _ = _factory_and_client()
    MaintenanceService(factory).set_enabled(True, reason="迁移冻结", actor="operator")
    monkeypatch.setattr(worker, "run_v11_once", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("不应领取任务")))

    report = worker.run_worker_iteration(factory, include_reflection=True)

    assert report == {"status": "maintenance_mode"}


def test_admin_can_change_maintenance_mode_and_status_is_audited() -> None:
    from sqlalchemy import select

    from codex_memory.db_models import AuditLogRow

    factory, client = _factory_and_client()

    response = client.post(
        "/api/admin/v1/maintenance",
        headers=_auth("admin-token"),
        json={"enabled": True, "reason": "迁移冻结"},
    )

    assert response.status_code == 200
    assert response.json()["data"]["enabled"] is True
    assert response.json()["data"]["reason"] == "迁移冻结"
    status = client.get("/api/admin/v1/system/status", headers=_auth("admin-token"))
    assert status.json()["data"]["maintenance"]["enabled"] is True
    with factory() as session:
        audit = session.scalar(select(AuditLogRow).where(AuditLogRow.event_type == "maintenance_mode_enabled"))
    assert audit is not None
    assert audit.metadata_json == {"reason": "迁移冻结", "actor": "erp"}
