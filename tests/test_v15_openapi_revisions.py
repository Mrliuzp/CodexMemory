"""V1.5 OpenAPI 归一化、操作索引和迁移元数据测试。"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def _document(*, operation_id: str = "getPet", path: str = "/pets") -> dict[str, object]:
    return {
        "openapi": "3.0.3",
        "info": {"title": "宠物服务", "version": "1.0.0"},
        "paths": {path: {"get": {"operationId": operation_id, "summary": "查询宠物"}}},
    }


def test_v15_metadata_isolated_and_contains_expected_tables() -> None:
    from codex_memory.db_models import Base, V11Base, V14Base, V15Base

    expected = {"contract_services", "contract_revisions", "api_operations"}
    assert expected <= set(V15Base.metadata.tables)
    assert expected.isdisjoint(Base.metadata.tables)
    assert expected.isdisjoint(V11Base.metadata.tables)
    assert expected.isdisjoint(V14Base.metadata.tables)


def test_openapi_json_bom_and_nullable_are_normalized() -> None:
    from codex_memory.api_operations import parse_and_normalize_openapi

    document = _document()
    document["paths"]["/pets"]["get"]["responses"] = {"200": {"content": {"application/json": {"schema": {"type": "string", "nullable": True}}}}}
    parsed = parse_and_normalize_openapi("contract.JSON", b"\xef\xbb\xbf" + json.dumps(document, ensure_ascii=False).encode())
    schema = parsed.document["paths"]["/pets"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    assert parsed.document["openapi"] == "3.1.0"
    assert parsed.document["profile_version"] == "v1"
    assert schema["type"] == ["string", "null"]
    assert len(parsed.content_hash) == 64


def test_yaml_local_ref_cycle_is_safe_and_external_ref_is_rejected() -> None:
    from codex_memory.api_operations import OpenAPIContractError, parse_and_normalize_openapi

    yaml_document = """openapi: 3.1.2
info:
  title: 循环服务
  version: '1'
paths:
  /pets:
    get:
      operationId: getPet
      responses:
        '200':
          description: 成功
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/Pet'
components:
  schemas:
    Pet:
      type: object
      properties:
        parent:
          $ref: '#/components/schemas/Pet'
""".encode()
    parsed = parse_and_normalize_openapi("contract.yaml", yaml_document)
    assert parsed.document["openapi"] == "3.1.0"
    with pytest.raises(OpenAPIContractError, match="本地"):
        parse_and_normalize_openapi("contract.yaml", yaml_document.replace(b"#/components/schemas/Pet", b"https://example.test/Pet", 1))


def test_operation_identity_and_route_warning() -> None:
    from codex_memory.api_operations import OpenAPIContractError, parse_and_normalize_openapi

    first = parse_and_normalize_openapi("contract.json", json.dumps(_document()).encode())
    changed_route = parse_and_normalize_openapi("contract.json", json.dumps(_document(path="/animals")).encode(), [{"method": "GET", "path": "/pets", "operation_id": "getPet"}])
    assert changed_route.warnings[0]["code"] == "route_changed"
    with pytest.raises(OpenAPIContractError, match="不可变更"):
        parse_and_normalize_openapi("contract.json", json.dumps(_document(operation_id="other")).encode(), [{"method": "GET", "path": "/pets", "operation_id": "getPet"}])
    assert first.markdown == parse_and_normalize_openapi("contract.json", json.dumps(_document()).encode()).markdown


def test_openapi_limits_and_forbidden_features() -> None:
    from codex_memory.api_operations import OpenAPIContractError, parse_and_normalize_openapi

    for version in ("2.0", "3.0.2", "3.2.0"):
        document = _document()
        document["openapi"] = version
        with pytest.raises(OpenAPIContractError):
            parse_and_normalize_openapi("contract.json", json.dumps(document).encode())
    forbidden = _document()
    forbidden["paths"]["/pets"]["get"]["callbacks"] = {}
    with pytest.raises(OpenAPIContractError, match="callbacks"):
        parse_and_normalize_openapi("contract.json", json.dumps(forbidden).encode())


def test_yaml_date_scalar_and_json_nan_are_structured_validation_errors() -> None:
    from codex_memory.api_operations import OpenAPIContractError, parse_and_normalize_openapi

    yaml_date = """openapi: 3.1.0
info:
  title: 日期服务
  version: 2026-07-29
paths: {}
""".encode()
    with pytest.raises(OpenAPIContractError) as date_error:
        parse_and_normalize_openapi("contract.yaml", yaml_date)
    assert date_error.value.errors[0]["code"] == "unsupported_scalar"

    json_nan = '{"openapi":"3.1.0","info":{"title":"NaN 服务","version":"1","x-value":NaN},"paths":{}}'.encode()
    with pytest.raises(OpenAPIContractError) as nan_error:
        parse_and_normalize_openapi("contract.json", json_nan)
    assert nan_error.value.errors[0]["code"] == "invalid_syntax"


def test_revision_service_is_idempotent_and_publishes_atomically() -> None:
    from codex_memory.contract_revisions import ContractRevisionService
    from codex_memory.persistence.v15_models import V15Base

    engine = create_engine("sqlite:///:memory:")
    V15Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    service = ContractRevisionService(factory)
    created = service.create_service(1, "pet-service")
    content = json.dumps(_document(), ensure_ascii=False).encode()
    first, reused = service.create_revision(created.id, "contract.json", content, 1)
    duplicate, reused_again = service.create_revision(created.id, "other.yaml", content, 1)
    assert reused is False
    assert reused_again is True
    assert duplicate.revision_number == first.revision_number == 1
    published, idempotent = service.publish(created.id, 1, first.content_hash, 1)
    repeated, repeated_idempotent = service.publish(created.id, 1, first.content_hash, 1)
    assert published.status == repeated.status == "published"
    assert idempotent is False
    assert repeated_idempotent is True


def test_admin_contract_api_uses_envelopes_and_project_isolation() -> None:
    import hashlib

    from fastapi.testclient import TestClient
    from sqlalchemy.pool import StaticPool

    from codex_memory.db_models import ApiKeyRow, Base, ProjectRow, V15Base
    from codex_memory.http_api import create_v1_app

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    V15Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        project = ProjectRow(id=1, project_key="erp", name="ERP")
        other = ProjectRow(id=2, project_key="crm", name="CRM")
        session.add_all([project, other])
        session.flush()
        session.add(ApiKeyRow(id=1, project_id=project.id, token_hash=hashlib.sha256(b"admin-token").hexdigest(), permissions=["read", "admin"]))
        session.commit()
    client = TestClient(create_v1_app(factory))
    headers = {"Authorization": "Bearer admin-token"}
    created = client.post("/api/admin/v1/contract-services", json={"project_key": "erp", "service_key": "pets", "name": "宠物服务"}, headers=headers)
    assert created.status_code == 200
    assert {"data", "meta", "request_id"} == set(created.json())
    service_id = created.json()["data"]["id"]
    invalid_json = client.post(f"/api/admin/v1/contract-services/{service_id}/revisions", files={"file": ("invalid.json", b'{"openapi":"3.1.0","info":{"title":"NaN","version":NaN},"paths":{}}', "application/json")}, headers=headers)
    assert invalid_json.status_code == 422
    assert invalid_json.json()["meta"]["validation_errors"][0]["code"] == "invalid_syntax"
    invalid_yaml = client.post(f"/api/admin/v1/contract-services/{service_id}/revisions", files={"file": ("invalid.yaml", "openapi: 3.1.0\ninfo:\n  title: 日期\n  version: 2026-07-29\npaths: {}\n".encode(), "application/yaml")}, headers=headers)
    assert invalid_yaml.status_code == 422
    assert invalid_yaml.json()["meta"]["validation_errors"][0]["code"] == "unsupported_scalar"
    body = json.dumps(_document(), ensure_ascii=False).encode()
    uploaded = client.post(f"/api/admin/v1/contract-services/{service_id}/revisions", files={"file": ("contract.json", body, "application/json")}, headers=headers)
    assert uploaded.status_code == 200
    assert uploaded.json()["data"]["revision_number"] == 1
    content_hash = uploaded.json()["data"]["content_hash"]
    published = client.post(f"/api/admin/v1/contract-services/{service_id}/revisions/1/publish", json={"expected_content_hash": content_hash}, headers=headers)
    assert published.status_code == 200
    denied = client.get(f"/api/admin/v1/contract-services/{service_id}", headers={"Authorization": "Bearer missing"})
    assert denied.status_code == 401
