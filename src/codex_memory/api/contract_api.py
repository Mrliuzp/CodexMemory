"""供 MCP 使用的 V1.5 接口契约 API。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ..api_operations import MAX_DOCUMENT_BYTES, OpenAPIContractError
from ..auth import (
    PermissionDenied,
    Principal,
    ProjectAccessDenied,
    TokenAuthenticationError,
    authenticate_bearer,
    require_permission,
    require_project_access,
)
from ..contract_revisions import ContractRevisionConflictError, ContractRevisionService
from ..db_models import ProjectRow


class ContractServiceEnsureRequest(BaseModel):
    project_key: str = Field(min_length=1, max_length=255)
    service_key: str = Field(min_length=1, max_length=150)
    name: str | None = Field(default=None, max_length=200)
    description: str | None = None


class ContractRevisionProposalRequest(BaseModel):
    project_key: str = Field(min_length=1, max_length=255)
    filename: str = Field(default="openapi.json", min_length=1, max_length=500)
    document: str = Field(min_length=1, max_length=MAX_DOCUMENT_BYTES)


def create_contract_router(session_factory: sessionmaker[Session]) -> APIRouter:
    """创建仅允许提案、不允许发布的契约 API。"""

    router = APIRouter(prefix="/api/v1/contracts", tags=["contracts-v1"])
    bearer = HTTPBearer(auto_error=False)
    contracts = ContractRevisionService(session_factory)

    def principal(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> Principal:
        if credentials is None or credentials.scheme.lower() != "bearer":
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="需要 Bearer 令牌")
        try:
            return authenticate_bearer(session_factory, credentials.credentials)
        except TokenAuthenticationError as error:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(error)) from error

    def project(project_key: str, current: Principal, permission: str) -> ProjectRow:
        try:
            require_project_access(current, project_key)
            require_permission(current, permission)
        except (ProjectAccessDenied, PermissionDenied) as error:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error)) from error
        with session_factory() as session:
            row = session.scalar(select(ProjectRow).where(ProjectRow.project_key == project_key))
            if row is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="项目不存在")
            session.expunge(row)
            return row

    def service_payload(service_id: int, project_id: int, project_key: str) -> dict[str, Any]:
        detail = contracts.service_detail(service_id, project_id)
        service = detail["service"]
        return {
            "id": service["id"],
            "project_key": project_key,
            "service_key": service["service_key"],
            "name": service["name"],
            "description": service["description"],
            "current_published_revision_id": service["current_published_revision_id"],
            "revisions": detail["revisions"],
        }

    @router.get("/services")
    def list_services(
        project_key: str,
        keyword: str | None = None,
        current: Principal = Depends(principal),
    ) -> dict[str, Any]:
        project_row = project(project_key, current, "read")
        rows = contracts.list_services(project_row.id, keyword=keyword)
        return {
            "services": [service_payload(row.id, project_row.id, project_key) for row in rows],
            "count": len(rows),
        }

    @router.get("/services/{service_key}")
    def get_service(
        service_key: str,
        project_key: str = Query(..., min_length=1),
        current: Principal = Depends(principal),
    ) -> dict[str, Any]:
        project_row = project(project_key, current, "read")
        try:
            row = contracts.get_service_by_key(project_row.id, service_key)
        except LookupError as error:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
        return {"service": service_payload(row.id, project_row.id, project_key)}

    @router.post("/services/ensure")
    def ensure_service(
        payload: ContractServiceEnsureRequest,
        current: Principal = Depends(principal),
    ) -> dict[str, Any]:
        project_row = project(payload.project_key, current, "contract_write")
        try:
            row, reused = contracts.ensure_service(
                project_row.id,
                payload.service_key,
                payload.name,
                payload.description,
            )
        except ValueError as error:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)) from error
        return {
            "service": service_payload(row.id, project_row.id, payload.project_key),
            "reused": reused,
        }

    @router.post("/services/{service_key}/revisions")
    def propose_revision(
        service_key: str,
        payload: ContractRevisionProposalRequest,
        current: Principal = Depends(principal),
    ) -> dict[str, Any]:
        project_row = project(payload.project_key, current, "contract_write")
        try:
            service_row = contracts.get_service_by_key(project_row.id, service_key)
            revision, reused = contracts.create_revision(
                service_row.id,
                payload.filename,
                payload.document.encode("utf-8"),
                project_row.id,
                created_by=f"mcp:{current.project_key}",
            )
            detail = contracts.get_revision(service_row.id, revision.revision_number, project_row.id)
        except LookupError as error:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
        except OpenAPIContractError as error:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"message": str(error), "validation_errors": error.errors},
            ) from error
        except ContractRevisionConflictError as error:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"message": str(error), "code": error.code, "meta": error.meta},
            ) from error
        return {
            "service_key": service_key,
            "revision": {
                "id": detail["id"],
                "revision_number": detail["revision_number"],
                "status": detail["status"],
                "content_hash": detail["content_hash"],
                "source_version": detail["source_version"],
                "normalized_version": detail["normalized_version"],
                "profile_version": detail["profile_version"],
                "operation_count": detail["operation_count"],
                "warnings": detail["warnings"],
            },
            "reused": reused,
        }

    return router


__all__ = ["create_contract_router"]
