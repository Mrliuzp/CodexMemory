from __future__ import annotations

import hashlib
from fastapi.testclient import TestClient


def _factory_and_client() -> tuple[object, TestClient]:
    from codex_memory.db import create_schema, create_session_factory, create_sqlite_engine
    from codex_memory.db_models import ApiKeyRow, ProjectRow
    from codex_memory.http_api import create_v1_app
    from codex_memory.v11_models import V11Base, DailyTokenUsageRow, ProjectProcessingPolicyRow

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
        # Add a budget configuration
        session.add(ProjectProcessingPolicyRow(
            project_id=project.id,
            daily_embedding_token_budget=1000000,
            daily_llm_token_budget=500000,
        ))
        # Add some today's usage
        from datetime import date
        today = date.today()
        session.add(DailyTokenUsageRow(
            project_id=project.id, usage_date=today,
            token_type="embedding", tokens_used=50000,
        ))
        session.add(DailyTokenUsageRow(
            project_id=project.id, usage_date=today,
            token_type="llm", tokens_used=100000,
        ))
        session.commit()
    return factory, TestClient(create_v1_app(factory))


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_list_budgets() -> None:
    """列出预算配置。"""
    _, client = _factory_and_client()
    resp = client.get("/api/v1/admin/budgets", headers=_auth("admin-token"))
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["budgets"]) >= 1
    budget = data["budgets"][0]
    assert budget["project_key"] == "erp"
    assert budget["daily_embedding_token_budget"] == 1000000
    assert budget["daily_llm_token_budget"] == 500000


def test_update_budget() -> None:
    """更新预算限制。"""
    _, client = _factory_and_client()
    resp = client.put(
        "/api/v1/admin/budgets/1",
        headers=_auth("admin-token"),
        json={"daily_embedding_token_budget": 2000000},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["daily_embedding_token_budget"] == 2000000
    # Verify the other field is unchanged
    assert data["daily_llm_token_budget"] == 500000


def test_budget_summary() -> None:
    """预算摘要包含用量百分比和预警等级。"""
    _, client = _factory_and_client()
    resp = client.get("/api/v1/admin/budgets/summary", headers=_auth("admin-token"))
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["summary"]) >= 1
    s = data["summary"][0]
    assert s["project_key"] == "erp"
    assert s["embedding_used_today"] == 50000
    assert s["embedding_budget"] == 1000000
    assert s["embedding_pct"] == 5.0
    assert s["llm_used_today"] == 100000
    assert s["llm_budget"] == 500000
    assert s["llm_pct"] == 20.0
    assert s["over_budget"] is False
    assert s["alert_level"] == "ok"