"""契约公共 API 与 MCP 工具测试。"""

from __future__ import annotations

import hashlib
import json

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


class FakeApiClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def post(self, path: str, payload: dict) -> dict:
        self.calls.append((path, payload))
        return {"path": path, "payload": payload}

    def get(self, path: str) -> dict:
        self.calls.append((path, {}))
        return {"path": path}


def _tool(server, name: str):
    return server._tool_manager.get_tool(name).fn


def _document() -> str:
    return json.dumps(
        {
            "openapi": "3.0.3",
            "info": {"title": "订单服务", "version": "1.0.0"},
            "paths": {
                "/orders": {
                    "get": {
                        "operationId": "listOrders",
                        "summary": "查询订单",
                        "responses": {"200": {"description": "成功"}},
                    }
                }
            },
        },
        ensure_ascii=False,
    )


def _client() -> tuple[TestClient, sessionmaker]:
    from codex_memory.db_models import ApiKeyRow, Base, ProjectRow, V15Base
    from codex_memory.http_api import create_v1_app

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    V15Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        erp = ProjectRow(id=1, project_key="erp", name="ERP")
        crm = ProjectRow(id=2, project_key="crm", name="CRM")
        session.add_all([erp, crm])
        session.flush()
        session.add_all(
            [
                ApiKeyRow(
                    id=1,
                    project_id=erp.id,
                    token_hash=hashlib.sha256(b"contract-token").hexdigest(),
                    permissions=["read", "contract_write"],
                ),
                ApiKeyRow(
                    id=2,
                    project_id=erp.id,
                    token_hash=hashlib.sha256(b"read-token").hexdigest(),
                    permissions=["read"],
                ),
                ApiKeyRow(
                    id=3,
                    project_id=crm.id,
                    token_hash=hashlib.sha256(b"crm-token").hexdigest(),
                    permissions=["read", "contract_write"],
                ),
            ]
        )
        session.commit()
    return TestClient(create_v1_app(factory)), factory


def test_mcp_contract_tools_map_to_public_api() -> None:
    from codex_memory.api.mcp_server import create_v1_server

    client = FakeApiClient()
    server = create_v1_server(client)

    listed = _tool(server, "list_contract_services")(project="erp", keyword="订单 服务")
    fetched = _tool(server, "get_contract_service")(project="erp", service="订单/api")
    ensured = _tool(server, "ensure_contract_service")(
        project="erp", service="orders", name="订单服务", description="订单接口"
    )
    proposed = _tool(server, "propose_contract_revision")(
        project="erp", service="orders", document=_document(), filename="openapi.json"
    )

    assert listed["path"] == "/api/v1/contracts/services?project_key=erp&keyword=%E8%AE%A2%E5%8D%95+%E6%9C%8D%E5%8A%A1"
    assert fetched["path"] == "/api/v1/contracts/services/%E8%AE%A2%E5%8D%95%2Fapi?project_key=erp"
    assert ensured["payload"]["service_key"] == "orders"
    assert proposed["path"] == "/api/v1/contracts/services/orders/revisions"
    assert proposed["payload"]["document"] == _document()


def test_contract_api_creates_idempotent_proposals_without_publishing() -> None:
    from codex_memory.persistence.v15_models import ContractRevisionRow

    client, factory = _client()
    headers = {"Authorization": "Bearer contract-token"}

    first = client.post(
        "/api/v1/contracts/services/ensure",
        json={"project_key": "erp", "service_key": "orders", "name": "订单服务"},
        headers=headers,
    )
    second = client.post(
        "/api/v1/contracts/services/ensure",
        json={"project_key": "erp", "service_key": "orders", "name": "不会覆盖"},
        headers=headers,
    )
    assert first.status_code == 200
    assert first.json()["reused"] is False
    assert second.status_code == 200
    assert second.json()["reused"] is True
    assert second.json()["service"]["name"] == "订单服务"

    payload = {"project_key": "erp", "filename": "openapi.json", "document": _document()}
    proposed = client.post("/api/v1/contracts/services/orders/revisions", json=payload, headers=headers)
    repeated = client.post("/api/v1/contracts/services/orders/revisions", json=payload, headers=headers)
    assert proposed.status_code == 200
    assert proposed.json()["revision"]["status"] == "proposed"
    assert proposed.json()["revision"]["operation_count"] == 1
    assert repeated.status_code == 200
    assert repeated.json()["reused"] is True
    assert repeated.json()["revision"]["revision_number"] == 1

    listed = client.get("/api/v1/contracts/services?project_key=erp", headers=headers)
    detail = client.get("/api/v1/contracts/services/orders?project_key=erp", headers=headers)
    assert listed.status_code == 200
    assert listed.json()["count"] == 1
    assert detail.status_code == 200
    assert detail.json()["service"]["revisions"][0]["status"] == "proposed"

    with factory() as session:
        revision = session.scalar(select(ContractRevisionRow))
        assert revision is not None
        assert revision.status == "proposed"
        assert revision.created_by == "mcp:erp"


def test_contract_api_enforces_permission_and_project_scope() -> None:
    client, _ = _client()
    read_headers = {"Authorization": "Bearer read-token"}
    crm_headers = {"Authorization": "Bearer crm-token"}
    payload = {"project_key": "erp", "service_key": "orders", "name": "订单服务"}

    denied_write = client.post("/api/v1/contracts/services/ensure", json=payload, headers=read_headers)
    cross_project = client.post("/api/v1/contracts/services/ensure", json=payload, headers=crm_headers)
    allowed_read = client.get("/api/v1/contracts/services?project_key=erp", headers=read_headers)

    assert denied_write.status_code == 403
    assert cross_project.status_code == 403
    assert allowed_read.status_code == 200


def test_contract_api_rejects_invalid_document_and_missing_service() -> None:
    client, _ = _client()
    headers = {"Authorization": "Bearer contract-token"}
    missing = client.post(
        "/api/v1/contracts/services/missing/revisions",
        json={"project_key": "erp", "filename": "openapi.json", "document": _document()},
        headers=headers,
    )
    assert missing.status_code == 404

    client.post(
        "/api/v1/contracts/services/ensure",
        json={"project_key": "erp", "service_key": "orders", "name": "订单服务"},
        headers=headers,
    )
    invalid = client.post(
        "/api/v1/contracts/services/orders/revisions",
        json={"project_key": "erp", "filename": "openapi.json", "document": "{}"},
        headers=headers,
    )
    assert invalid.status_code == 422
    assert invalid.json()["detail"]["validation_errors"]
