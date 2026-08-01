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
        "paths": {path: {"get": {"operationId": operation_id, "summary": "查询宠物", "responses": {"200": {"description": "成功"}}}}},
    }


def test_v15_metadata_isolated_and_contains_expected_tables() -> None:
    from codex_memory.db_models import Base, V11Base, V14Base, V15Base

    expected = {"contract_services", "contract_revisions", "api_operations"}
    assert expected <= set(V15Base.metadata.tables)
    assert expected.isdisjoint(Base.metadata.tables)
    assert expected.isdisjoint(V11Base.metadata.tables)
    assert expected.isdisjoint(V14Base.metadata.tables)
    assert "markdown_document" in V15Base.metadata.tables["contract_revisions"].c
    assert "markdown" not in V15Base.metadata.tables["contract_revisions"].c
    assert {"tags", "deprecated"} <= set(V15Base.metadata.tables["api_operations"].c.keys())
    assert "tags_json" not in V15Base.metadata.tables["api_operations"].c
    assert V15Base.metadata.tables["api_operations"].c.deprecated.nullable is False


def test_openapi_json_bom_and_nullable_are_normalized() -> None:
    from codex_memory.api_operations import parse_and_normalize_openapi
    from openapi_spec_validator import validate

    document = _document()
    document["paths"]["/pets"]["get"]["responses"] = {"200": {"description": "成功", "content": {"application/json": {"schema": {"type": "string", "nullable": True}}}}}
    parsed = parse_and_normalize_openapi("contract.JSON", b"\xef\xbb\xbf" + json.dumps(document, ensure_ascii=False).encode())
    schema = parsed.document["paths"]["/pets"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    assert parsed.document["openapi"] == "3.1.0"
    assert "profile_version" not in parsed.document
    validate(parsed.document)
    assert schema["type"] == ["string", "null"]
    assert len(parsed.content_hash) == 64


def test_validator_path_exclusive_bounds_and_operation_hash() -> None:
    from codex_memory.api_operations import OpenAPIContractError, parse_and_normalize_openapi

    document = _document()
    document["paths"]["/pets"]["get"]["responses"]["200"] = {"description": "成功", "content": {"application/json": {"schema": {"type": "number", "minimum": 1, "exclusiveMinimum": True, "maximum": 10, "exclusiveMaximum": False}}}}
    parsed = parse_and_normalize_openapi("contract.json", json.dumps(document, ensure_ascii=False).encode())
    schema = parsed.document["paths"]["/pets"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    assert schema["exclusiveMinimum"] == 1
    assert "minimum" not in schema
    assert schema["maximum"] == 10
    assert "exclusiveMaximum" not in schema
    assert parsed.operations[0].operation_hash

    deprecated = _document()
    deprecated["paths"]["/pets"]["get"]["tags"] = ["pets"]
    deprecated["paths"]["/pets"]["get"]["deprecated"] = True
    deprecated_parsed = parse_and_normalize_openapi("contract.json", json.dumps(deprecated).encode())
    assert deprecated_parsed.operations[0].deprecated is True
    assert "已弃用" in deprecated_parsed.markdown

    invalid = _document()
    invalid["paths"]["/pets"]["get"]["responses"] = {"200": {}}
    with pytest.raises(OpenAPIContractError) as error:
        parse_and_normalize_openapi("contract.json", json.dumps(invalid).encode())
    assert error.value.errors[0]["code"] == "openapi_validation_error"
    assert "JSON 路径" in error.value.errors[0]["message"]


def test_operation_id_constraints_precede_general_validator() -> None:
    from codex_memory.api_operations import OpenAPIContractError, parse_and_normalize_openapi

    duplicate = _document()
    duplicate["paths"]["/other"] = {"get": {"operationId": "getPet", "responses": {"200": {"description": "成功"}}}}
    with pytest.raises(OpenAPIContractError) as duplicate_error:
        parse_and_normalize_openapi("contract.json", json.dumps(duplicate, ensure_ascii=False).encode())
    assert duplicate_error.value.errors[0]["code"] == "duplicate_operation_id"
    missing = _document()
    missing["paths"]["/pets"]["get"].pop("operationId")
    with pytest.raises(OpenAPIContractError) as missing_error:
        parse_and_normalize_openapi("contract.json", json.dumps(missing, ensure_ascii=False).encode())
    assert missing_error.value.errors[0]["code"] == "missing_operation_id"


def test_structural_unsupported_fields_do_not_match_schema_properties() -> None:
    from codex_memory.api_operations import OpenAPIContractError, parse_and_normalize_openapi

    document = _document()
    document["components"] = {"schemas": {"Example": {"type": "object", "properties": {"callbacks": {"type": "string"}}}}}
    parsed = parse_and_normalize_openapi("contract.json", json.dumps(document).encode())
    assert "callbacks" in parsed.document["components"]["schemas"]["Example"]["properties"]

    invalid = _document()
    invalid["paths"]["/pets"]["get"]["callbacks"] = {}
    with pytest.raises(OpenAPIContractError) as error:
        parse_and_normalize_openapi("contract.json", json.dumps(invalid).encode())
    assert error.value.errors[0]["code"] == "unsupported_feature"


def test_markdown_contains_servers_auth_request_response_and_schemas() -> None:
    from codex_memory.api_operations import parse_and_normalize_openapi

    document = _document()
    document["servers"] = [{"url": "https://api.example.test", "description": "生产服务"}]
    document["security"] = [{"bearerAuth": []}]
    document["paths"]["/pets"]["get"]["parameters"] = [{"name": "limit", "in": "query", "schema": {"type": "integer"}}]
    document["paths"]["/pets"]["get"]["requestBody"] = {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/Pet"}}}}
    document["components"] = {"schemas": {"Pet": {"type": "object", "properties": {"id": {"type": "integer"}}}}}
    parsed = parse_and_normalize_openapi("contract.json", json.dumps(document, ensure_ascii=False).encode())
    assert "## 服务器" in parsed.markdown
    assert "## 鉴权" in parsed.markdown
    assert "#### 请求" in parsed.markdown
    assert "#### 响应" in parsed.markdown
    assert "## 公共 Schema" in parsed.markdown
    assert "Schema `Pet`" in parsed.markdown
    assert parsed.markdown == parse_and_normalize_openapi("contract.json", json.dumps(document, ensure_ascii=False).encode()).markdown


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


def test_contract_error_code_mapping_is_stable() -> None:
    from codex_memory.admin.api import _contract_openapi_error_code, _contract_openapi_error_status, _contract_revision_error_code

    assert _contract_openapi_error_code("invalid_extension") == "contract_invalid_file"
    assert _contract_openapi_error_code("invalid_encoding") == "contract_invalid_file"
    assert _contract_openapi_error_code("invalid_syntax") == "contract_invalid_file"
    assert _contract_openapi_error_code("document_too_large") == "contract_file_too_large"
    assert _contract_openapi_error_code("unsupported_version") == "contract_unsupported_version"
    assert _contract_openapi_error_code("openapi_validation_error") == "contract_validation_failed"
    assert _contract_openapi_error_code("lossy_normalization") == "contract_profile_unsupported"
    assert _contract_openapi_error_code("missing_operation_id") == "contract_operation_id_invalid"
    assert _contract_openapi_error_code("duplicate_operation_id") == "contract_operation_id_invalid"
    assert _contract_openapi_error_code("operation_id_changed") == "contract_operation_id_conflict"
    assert _contract_openapi_error_status("invalid_extension") == 400
    assert _contract_openapi_error_status("invalid_encoding") == 400
    assert _contract_openapi_error_status("invalid_syntax") == 400
    assert _contract_openapi_error_status("invalid_root") == 400
    assert _contract_openapi_error_status("document_too_large") == 413
    assert _contract_openapi_error_status("operation_id_changed") == 409
    assert _contract_openapi_error_status("openapi_validation_error") == 422
    assert _contract_openapi_error_status("lossy_normalization") == 422
    assert _contract_openapi_error_status("unsupported_version") == 422
    assert _contract_openapi_error_status("missing_operation_id") == 422
    assert _contract_openapi_error_status("duplicate_operation_id") == 422
    assert _contract_revision_error_code("content_hash_mismatch") == "contract_revision_conflict"
    assert _contract_revision_error_code("revision_not_publishable") == "contract_revision_conflict"
    assert _contract_revision_error_code("service_exists") == "contract_service_conflict"


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
    detail = service.get_revision(created.id, 1, 1)
    assert detail["source_version"] == "3.0.3"
    assert detail["normalized_version"] == "3.1.0"
    assert detail["source_document"]["openapi"] == "3.0.3"
    assert detail["validation_summary"]["errors"] == []
    assert detail["operations"][0]["operation_hash"]
    published, idempotent = service.publish(created.id, 1, first.content_hash, 1)
    repeated, repeated_idempotent = service.publish(created.id, 1, first.content_hash, 1)
    assert published.status == repeated.status == "published"
    assert idempotent is False
    assert repeated_idempotent is True


def test_revision_identity_uses_only_current_published_revision() -> None:
    from codex_memory.api_operations import OpenAPIContractError
    from codex_memory.contract_revisions import ContractRevisionService
    from codex_memory.persistence.v15_models import ContractServiceRow, V15Base

    engine = create_engine("sqlite:///:memory:")
    V15Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    service = ContractRevisionService(factory)
    created = service.create_service(1, "pet-service")
    first_document = _document(operation_id="getPet")
    second_document = _document(operation_id="getPetV2")
    first, _ = service.create_revision(created.id, "one.json", json.dumps(first_document).encode(), 1)
    second, _ = service.create_revision(created.id, "two.json", json.dumps(second_document).encode(), 1)
    assert second.revision_number == 2
    service.publish(created.id, 1, first.content_hash, 1)
    third_document = _document(operation_id="getPetV2")
    third_document["paths"]["/pets"]["get"]["summary"] = "变更后的查询"
    with pytest.raises(OpenAPIContractError, match="不可变更"):
        service.create_revision(created.id, "three.json", json.dumps(third_document).encode(), 1)
    with factory() as session:
        service_row = session.get(ContractServiceRow, created.id)
        assert service_row.current_published_revision_id == first.id


def test_admin_contract_api_uses_envelopes_and_project_isolation() -> None:
    import hashlib

    from fastapi.testclient import TestClient
    from sqlalchemy.pool import StaticPool

    from codex_memory.db_models import ApiKeyRow, Base, ProjectRow, V15Base
    from codex_memory.http_api import create_v1_app
    from codex_memory.persistence.v15_models import ContractServiceRow

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    V15Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        project = ProjectRow(id=1, project_key="erp", name="ERP")
        other = ProjectRow(id=2, project_key="crm", name="CRM")
        session.add_all([project, other])
        session.flush()
        session.add_all([
            ApiKeyRow(id=1, project_id=project.id, token_hash=hashlib.sha256(b"admin-token").hexdigest(), permissions=["read", "admin"]),
            ApiKeyRow(id=2, project_id=project.id, token_hash=hashlib.sha256(b"read-token").hexdigest(), permissions=["read"]),
            ContractServiceRow(id=77, project_id=other.id, service_key="crm-pets", name="CRM Pets"),
        ])
        session.commit()
    client = TestClient(create_v1_app(factory))
    headers = {"Authorization": "Bearer admin-token"}
    cross_project_create = client.post("/api/admin/v1/contract-services", json={"project_key": "crm", "service_key": "cross-project"}, headers=headers)
    assert cross_project_create.status_code == 403
    assert cross_project_create.json()["error"]["code"] == "permission_denied"
    cross_project_list = client.get("/api/admin/v1/contract-services?project_key=crm", headers={"Authorization": "Bearer read-token"})
    assert cross_project_list.status_code == 403
    assert cross_project_list.json()["error"]["code"] == "permission_denied"
    cross_project_read = client.get("/api/admin/v1/contract-services/77", headers={"Authorization": "Bearer read-token"})
    assert cross_project_read.status_code == 403
    assert cross_project_read.json()["error"]["code"] == "permission_denied"
    cross_project_write = client.post("/api/admin/v1/contract-services/77/revisions", files={"file": ("blocked.json", b"{}", "application/json")}, headers=headers)
    assert cross_project_write.status_code == 403
    assert cross_project_write.json()["error"]["code"] == "permission_denied"
    created = client.post("/api/admin/v1/contract-services", json={"project_key": "erp", "service_key": "pets", "name": "宠物服务"}, headers=headers)
    assert created.status_code == 200
    assert {"data", "meta", "request_id"} == set(created.json())
    service_id = created.json()["data"]["id"]
    duplicate_service = client.post("/api/admin/v1/contract-services", json={"project_key": "erp", "service_key": "pets", "name": "宠物服务"}, headers=headers)
    assert duplicate_service.status_code == 409
    assert duplicate_service.json()["error"]["code"] == "contract_service_conflict"
    read_headers = {"Authorization": "Bearer read-token"}
    read_list = client.get("/api/admin/v1/contract-services?keyword=pets", headers=read_headers)
    assert read_list.status_code == 200
    assert len(read_list.json()["data"]) == 1
    read_upload = client.post(f"/api/admin/v1/contract-services/{service_id}/revisions", files={"file": ("blocked.json", b"{}", "application/json")}, headers=read_headers)
    assert read_upload.status_code == 403
    invalid_json = client.post(f"/api/admin/v1/contract-services/{service_id}/revisions", files={"file": ("invalid.json", b'{"openapi":"3.1.0","info":{"title":"NaN","version":NaN},"paths":{}}', "application/json")}, headers=headers)
    assert invalid_json.status_code == 400
    assert invalid_json.json()["error"]["code"] == "contract_invalid_file"
    assert invalid_json.json()["meta"]["validation_errors"][0]["code"] == "invalid_syntax"
    invalid_yaml = client.post(f"/api/admin/v1/contract-services/{service_id}/revisions", files={"file": ("invalid.yaml", "openapi: 3.1.0\ninfo:\n  title: 日期\n  version: 2026-07-29\npaths: {}\n".encode(), "application/yaml")}, headers=headers)
    assert invalid_yaml.status_code == 422
    assert invalid_yaml.json()["error"]["code"] == "contract_validation_failed"
    assert invalid_yaml.json()["meta"]["validation_errors"][0]["code"] == "unsupported_scalar"
    invalid_extension = client.post(f"/api/admin/v1/contract-services/{service_id}/revisions", files={"file": ("invalid.txt", b"{}", "text/plain")}, headers=headers)
    assert invalid_extension.status_code == 400
    assert invalid_extension.json()["error"]["code"] == "contract_invalid_file"
    invalid_encoding = client.post(f"/api/admin/v1/contract-services/{service_id}/revisions", files={"file": ("invalid.json", b"\xff", "application/json")}, headers=headers)
    assert invalid_encoding.status_code == 400
    assert invalid_encoding.json()["error"]["code"] == "contract_invalid_file"
    invalid_json_syntax = client.post(f"/api/admin/v1/contract-services/{service_id}/revisions", files={"file": ("invalid.json", b"{", "application/json")}, headers=headers)
    assert invalid_json_syntax.status_code == 400
    assert invalid_json_syntax.json()["error"]["code"] == "contract_invalid_file"
    invalid_yaml_syntax = client.post(f"/api/admin/v1/contract-services/{service_id}/revisions", files={"file": ("invalid.yaml", b"openapi: [\n", "application/yaml")}, headers=headers)
    assert invalid_yaml_syntax.status_code == 400
    assert invalid_yaml_syntax.json()["error"]["code"] == "contract_invalid_file"
    invalid_root = client.post(f"/api/admin/v1/contract-services/{service_id}/revisions", files={"file": ("invalid.json", b"[]", "application/json")}, headers=headers)
    assert invalid_root.status_code == 400
    assert invalid_root.json()["error"]["code"] == "contract_invalid_file"
    invalid_version_document = _document()
    invalid_version_document["openapi"] = "2.0"
    invalid_version = client.post(f"/api/admin/v1/contract-services/{service_id}/revisions", files={"file": ("invalid-version.json", json.dumps(invalid_version_document).encode(), "application/json")}, headers=headers)
    assert invalid_version.status_code == 422
    assert invalid_version.json()["error"]["code"] == "contract_unsupported_version"
    invalid_profile_document = _document()
    invalid_profile_document["x-profile"] = {"exclusiveMinimum": True}
    invalid_profile = client.post(f"/api/admin/v1/contract-services/{service_id}/revisions", files={"file": ("invalid-profile.json", json.dumps(invalid_profile_document).encode(), "application/json")}, headers=headers)
    assert invalid_profile.status_code == 422
    assert invalid_profile.json()["error"]["code"] == "contract_profile_unsupported"
    duplicate_document = _document()
    duplicate_document["paths"]["/other"] = {"get": {"operationId": "getPet", "responses": {"200": {"description": "成功"}}}}
    duplicate_operation = client.post(f"/api/admin/v1/contract-services/{service_id}/revisions", files={"file": ("duplicate.json", json.dumps(duplicate_document, ensure_ascii=False).encode(), "application/json")}, headers=headers)
    assert duplicate_operation.status_code == 422
    assert duplicate_operation.json()["error"]["code"] == "contract_operation_id_invalid"
    assert duplicate_operation.json()["meta"]["validation_errors"][0]["code"] == "duplicate_operation_id"
    missing_document = _document()
    missing_document["paths"]["/pets"]["get"].pop("operationId")
    missing_operation = client.post(f"/api/admin/v1/contract-services/{service_id}/revisions", files={"file": ("missing.json", json.dumps(missing_document, ensure_ascii=False).encode(), "application/json")}, headers=headers)
    assert missing_operation.status_code == 422
    assert missing_operation.json()["error"]["code"] == "contract_operation_id_invalid"
    assert missing_operation.json()["meta"]["validation_errors"][0]["code"] == "missing_operation_id"
    too_large = client.post(f"/api/admin/v1/contract-services/{service_id}/revisions", files={"file": ("large.json", b"{" + b" " * (2 * 1024 * 1024) + b"}", "application/json")}, headers=headers)
    assert too_large.status_code == 413
    assert too_large.json()["error"]["code"] == "contract_file_too_large"
    upload_document = _document()
    upload_document["paths"]["/pets"]["get"]["tags"] = ["pets"]
    upload_document["paths"]["/pets"]["get"]["deprecated"] = True
    body = json.dumps(upload_document, ensure_ascii=False).encode()
    uploaded = client.post(f"/api/admin/v1/contract-services/{service_id}/revisions", files={"file": ("contract.json", body, "application/json")}, headers=headers)
    assert uploaded.status_code == 200
    assert uploaded.json()["data"]["revision_number"] == 1
    assert uploaded.json()["data"]["profile_version"] == "v1"
    assert uploaded.json()["data"]["created_by"] == "admin"
    assert "markdown_document" in uploaded.json()["data"]
    assert "markdown" not in uploaded.json()["data"]
    assert uploaded.json()["data"]["operations"][0]["tags"] == ["pets"]
    assert uploaded.json()["data"]["operations"][0]["deprecated"] is True
    proposed_list = client.get("/api/admin/v1/contract-services?status=proposed&keyword=pets", headers=read_headers)
    assert proposed_list.status_code == 200
    assert proposed_list.json()["meta"]["total"] == 1
    content_hash = uploaded.json()["data"]["content_hash"]
    hash_conflict = client.post(f"/api/admin/v1/contract-services/{service_id}/revisions/1/publish", json={"expected_content_hash": "0" * 64}, headers=headers)
    assert hash_conflict.status_code == 409
    assert hash_conflict.json()["error"]["code"] == "contract_revision_conflict"
    published = client.post(f"/api/admin/v1/contract-services/{service_id}/revisions/1/publish", json={"expected_content_hash": content_hash}, headers=headers)
    assert published.status_code == 200
    assert published.json()["data"]["published_by"] == "admin"
    changed_operation = client.post(f"/api/admin/v1/contract-services/{service_id}/revisions", files={"file": ("changed.json", json.dumps(_document(operation_id="other"), ensure_ascii=False).encode(), "application/json")}, headers=headers)
    assert changed_operation.status_code == 409
    assert changed_operation.json()["error"]["code"] == "contract_operation_id_conflict"
    published_list = client.get("/api/admin/v1/contract-services?status=published&keyword=pets", headers=read_headers)
    assert published_list.status_code == 200
    assert published_list.json()["data"][0]["current_published_revision_id"] == 1
    denied = client.get(f"/api/admin/v1/contract-services/{service_id}", headers={"Authorization": "Bearer missing"})
    assert denied.status_code == 401

    missing_service = client.get("/api/admin/v1/contract-services/999", headers=read_headers)
    assert missing_service.status_code == 404
    assert missing_service.json()["error"]["code"] == "contract_service_not_found"
    missing_revision = client.get(f"/api/admin/v1/contract-services/{service_id}/revisions/999", headers=read_headers)
    assert missing_revision.status_code == 404
    assert missing_revision.json()["error"]["code"] == "contract_revision_not_found"


def test_contract_read_requires_read_permission_with_project_access() -> None:
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
        session.add(project)
        session.flush()
        session.add(ApiKeyRow(id=1, project_id=project.id, token_hash=hashlib.sha256(b"project-token").hexdigest(), permissions=[]))
        session.commit()
    client = TestClient(create_v1_app(factory))
    headers = {"Authorization": "Bearer project-token"}
    for path in ("/api/admin/v1/contract-services", "/api/admin/v1/contract-services/1", "/api/admin/v1/contract-services/1/revisions/1"):
        response = client.get(path, headers=headers)
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "permission_denied"
