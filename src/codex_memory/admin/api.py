from __future__ import annotations

import base64
import os
import re
import secrets
from datetime import datetime, timezone
from typing import Any, Callable
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile, status
from pydantic import BaseModel, Field
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import func, inspect, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from ..auth import PermissionDenied, ProjectAccessDenied, Principal, TokenAuthenticationError, authenticate_bearer, require_permission, require_project_access, issue_admin_session
from ..db_models import AuditLogRow, MemoryRow, MessageRow, ProjectRow
from ..v11_models import ImportBatchRow, ImportFileRow, ImportIssueRow, ImportUploadPartRow, MemoryCandidateRow, OutboxEventRow, ProcessingJobRow, ReferenceCandidateRow, RetrievalAuditRow
from ..persistence.v14_models import TaskEventRow, TaskFileChangeRow, TaskReportRow, TaskRunRow
from ..contract_revisions import ContractRevisionConflictError, ContractRevisionService
from ..api_operations import OpenAPIContractError
from ..api_operations import MAX_DOCUMENT_BYTES
from ..persistence.v15_models import ContractRevisionRow, ContractServiceRow

SORT_FIELDS = {"created_at", "updated_at", "id", "status", "project_key", "title"}
SENSITIVE_KEY = re.compile(r"(?:api[_-]?key|authorization|bearer|password|secret|token|credential)", re.I)

def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", None) or str(uuid4())

class AdminAPIError(Exception):
    def __init__(self, status_code: int, code: str, message: str, request_id: str) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        self.request_id = request_id
        self.meta: dict[str, Any] = {}
        self.headers: dict[str, str] = {}
        super().__init__(message)


def _error(request: Request, code: str, message: str, status_code: int, meta: dict[str, Any] | None = None) -> AdminAPIError:
    error = AdminAPIError(status_code, code, message, _request_id(request))
    error.meta = meta or {}
    return error

def _redact(value: Any, *, key: str | None = None) -> Any:
    if key and (key.lower() == "raw" or SENSITIVE_KEY.search(key)):
        return None
    if isinstance(value, dict):
        return {k: clean for k, item in value.items() if (clean := _redact(item, key=k)) is not None}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value

def _row_value(row: Any, name: str, default: Any = None) -> Any:
    value = getattr(row, name, default)
    return value.isoformat() if isinstance(value, datetime) else value

def _page(data: list[dict[str, Any]], total: int, page: int, page_size: int, request_id: str) -> dict[str, Any]:
    return {"data": data, "meta": {"page": page, "page_size": page_size, "total": total, "has_next": page * page_size < total}, "request_id": request_id}

def _validate_sort(sort: str) -> None:
    if sort not in SORT_FIELDS:
        raise ValueError(f"不支持的排序字段：{sort}")

def _scope_allowed(session: Session, project: ProjectRow, scope_id: str | None) -> bool:
    if not scope_id:
        return True
    scope_value = str(scope_id)
    project_suffix = project.project_key.removeprefix("project-")
    if scope_value in {"default", "project", f"scope-{project_suffix}"}:
        return True
    if not inspect(session.bind).has_table("knowledge_scopes"):
        return scope_value == str(project.id)
    row = session.execute(text("SELECT id FROM knowledge_scopes WHERE project_id = :project_id AND (CAST(id AS TEXT) = :scope_id OR scope_key = :scope_id)"), {"project_id": project.id, "scope_id": scope_value}).first()
    return row is not None

class ImportBatchItem(BaseModel):
    source_name: str
    content: str = ""
    content_base64: str | None = None
    source_type: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


def _item_content(item: ImportBatchItem) -> str:
    if item.content_base64 is None:
        return item.content
    try:
        raw = base64.b64decode(item.content_base64, validate=True)
    except (ValueError, base64.binascii.Error) as error:
        raise ValueError("content_base64 不是有效的 Base64") from error
    return "base64:" + base64.b64encode(raw).decode("ascii")

def _item_size(item: ImportBatchItem) -> int:
    if item.content_base64 is not None:
        try:
            return len(base64.b64decode(item.content_base64, validate=True))
        except (ValueError, base64.binascii.Error) as error:
            raise ValueError("content_base64 不是有效的 Base64") from error
    return len(item.content.encode("utf-8"))
class ImportChunkStartRequest(BaseModel):
    source_name: str
    source_type: str | None = None
    total_parts: int = Field(ge=1, le=1024)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ImportChunkPartRequest(BaseModel):
    content: str = ""
    content_base64: str | None = None
    total_parts: int | None = Field(default=None, ge=1, le=1024)
    source_name: str | None = None
    source_type: str | None = None


def _chunk_content(payload: ImportChunkPartRequest) -> str:
    if payload.content_base64 is None:
        return payload.content
    try:
        raw = base64.b64decode(payload.content_base64, validate=True)
    except (ValueError, base64.binascii.Error) as error:
        raise ValueError("content_base64 ????? Base64") from error
    return "base64:" + base64.b64encode(raw).decode("ascii")


def _chunk_size(payload: ImportChunkPartRequest) -> int:
    if payload.content_base64 is not None:
        try:
            return len(base64.b64decode(payload.content_base64, validate=True))
        except (ValueError, base64.binascii.Error) as error:
            raise ValueError("content_base64 ????? Base64") from error
    return len(payload.content.encode("utf-8"))


class ImportBatchCreateRequest(BaseModel):
    project_key: str | None = None
    scope_key: str = "project"
    items: list[ImportBatchItem] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

class ImportReviewRequest(BaseModel):
    decision: str
    reviewer: str | None = None
    reason: str | None = None

class AdminLoginRequest(BaseModel):
    username: str
    password: str


class TaskRunListItem(BaseModel):
    id: int
    project_id: int
    session_key: str
    status: str
    started_at: str | None
    ended_at: str | None
    current_report_revision: int


class TaskRunPageMeta(BaseModel):
    page: int
    page_size: int
    total: int
    has_next: bool


class TaskRunListResponse(BaseModel):
    data: list[TaskRunListItem]
    meta: TaskRunPageMeta
    request_id: str


class TaskGitBaseline(BaseModel):
    branch: str | None
    head: str | None
    status_porcelain: str | None
    diff_hash: str | None
    untracked: list[Any] | None
    available: bool | None


class TaskEventItem(BaseModel):
    id: int
    event_key: str
    event_type: str
    sequence_no: int
    occurred_at: str | None
    content_hash: str
    command_summary: str | None
    result_summary: str | None
    exit_code: int | None
    redaction_applied: bool
    truncated: bool


class TaskReportSummary(BaseModel):
    id: int
    revision: int
    report_kind: str
    status: str
    uncertain: bool
    truncated: bool
    created_at: str | None


class TaskRunDetail(BaseModel):
    id: int
    project_id: int
    session_key: str
    status: str
    started_at: str | None
    ended_at: str | None
    current_report_revision: int
    git_baseline: TaskGitBaseline
    events: list[TaskEventItem]
    reports: list[TaskReportSummary]


class TaskRunDetailResponse(BaseModel):
    data: TaskRunDetail
    request_id: str


class TaskFileChangeItem(BaseModel):
    id: int
    change_index: int
    path: str
    old_path: str | None
    change_type: str
    before_hash: str | None
    after_hash: str | None
    attribution: str
    metadata: dict[str, Any]


class TaskReportDetail(BaseModel):
    id: int
    project_id: int
    task_run_id: int
    source_event_id: int
    revision: int
    report_kind: str
    status: str
    report_json: dict[str, Any]
    body: str
    content_hash: str
    uncertain: bool
    truncated: bool
    created_at: str | None
    file_changes: list[TaskFileChangeItem]


class TaskReportDetailResponse(BaseModel):
    data: TaskReportDetail
    request_id: str


class ContractServiceCreateRequest(BaseModel):
    project_key: str | None = None
    service_key: str | None = None
    key: str | None = None
    service_name: str | None = None
    name: str | None = None
    description: str | None = None


class ContractPublishRequest(BaseModel):
    expected_content_hash: str

def create_admin_router(session_factory: sessionmaker[Session]) -> APIRouter:
    router = APIRouter(prefix="/api/admin/v1", tags=["admin-v1"])
    bearer = HTTPBearer(auto_error=False)
    contract_service = ContractRevisionService(session_factory)

    @router.post("/login")
    def login(payload: AdminLoginRequest, request: Request) -> dict[str, Any]:
        expected_username = os.environ.get("CODEX_MEMORY_ADMIN_USERNAME", "admin")
        expected_password = os.environ.get("CODEX_MEMORY_ADMIN_PASSWORD", "")
        if not expected_password:
            raise _error(request, "login_not_configured", "管理后台登录尚未配置", status.HTTP_503_SERVICE_UNAVAILABLE)
        if not (secrets.compare_digest(payload.username, expected_username) and secrets.compare_digest(payload.password, expected_password)):
            raise _error(request, "invalid_credentials", "用户名或密码错误", status.HTTP_401_UNAUTHORIZED)
        project_key = os.environ.get("CODEX_MEMORY_ADMIN_PROJECT_KEY", os.environ.get("CODEX_MEMORY_BOOTSTRAP_PROJECT_KEY", "*"))
        try:
            token = issue_admin_session(payload.username, project_key=project_key)
        except RuntimeError as error:
            raise _error(request, "login_not_configured", str(error), status.HTTP_503_SERVICE_UNAVAILABLE) from error
        return {"access_token": token, "token_type": "bearer", "expires_in": 8 * 60 * 60, "request_id": _request_id(request)}
    def write_import_audit(project_key: str, event_type: str, subject_type: str, subject_id: str, metadata: dict[str, Any] | None = None) -> None:
        with session_factory() as session:
            project = session.scalar(select(ProjectRow).where(ProjectRow.project_key == project_key))
            session.add(AuditLogRow(project_id=project.id if project else None, event_type=event_type, subject_type=subject_type, subject_id=subject_id, metadata_json=metadata or {}))
            session.commit()
    def principal(request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> Principal:
        if credentials is None or credentials.scheme.lower() != "bearer":
            error = _error(request, "authentication_required", "需要 Bearer 令牌", status.HTTP_401_UNAUTHORIZED)
            error.headers["WWW-Authenticate"] = "Bearer"
            raise error
        try:
            current = authenticate_bearer(session_factory, credentials.credentials)
            require_permission(current, "read")
            return current
        except TokenAuthenticationError as error:
            raise _error(request, "invalid_token", str(error), status.HTTP_401_UNAUTHORIZED) from error
        except PermissionDenied as error:
            raise _error(request, "permission_denied", str(error), status.HTTP_403_FORBIDDEN) from error

    def project_context(request: Request, project_key: str, scope_id: str | None, current: Principal) -> ProjectRow:
        try:
            require_project_access(current, project_key)
        except ProjectAccessDenied as error:
            raise _error(request, "project_access_denied", str(error), status.HTTP_403_FORBIDDEN) from error
        with session_factory() as session:
            project = session.scalar(select(ProjectRow).where(ProjectRow.project_key == project_key))
            if project is None:
                raise _error(request, "project_not_found", "项目不存在", status.HTTP_404_NOT_FOUND)
            if not _scope_allowed(session, project, scope_id):
                raise _error(request, "scope_access_denied", f"令牌无权访问作用域：{scope_id}", status.HTTP_403_FORBIDDEN)
            session.expunge(project)
            return project

    def pagination(request: Request, page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200), sort: str = Query("created_at"), order: str = Query("desc", pattern="^(asc|desc)$")) -> tuple[int, int, str, str, str]:
        try:
            _validate_sort(sort)
        except ValueError as error:
            raise _error(request, "invalid_sort", str(error), status.HTTP_422_UNPROCESSABLE_ENTITY) from error
        return page, page_size, sort, order, _request_id(request)

    def list_response(request: Request, rows: list[Any], total: int, page: int, page_size: int, mapper: Callable[[Any], dict[str, Any]]) -> dict[str, Any]:
        return _page([mapper(row) for row in rows], total, page, page_size, _request_id(request))

    def query_rows(model: Any, project_id: int | None, page: int, page_size: int, sort: str, order: str, scope_id: str | None = None) -> tuple[list[Any], int]:
        with session_factory() as session:
            query = select(model)
            if project_id is not None and hasattr(model, "project_id"):
                query = query.where(model.project_id == project_id)
            if scope_id and hasattr(model, "scope_id"):
                scope_value = str(scope_id)
                if inspect(session.bind).has_table("knowledge_scopes"):
                    scope_row = session.execute(
                        text("SELECT id FROM knowledge_scopes WHERE project_id = :project_id AND (scope_key = :scope_id OR CAST(id AS TEXT) = :scope_id OR (:scope_id IN ('project', 'default') AND is_default = :is_default))"),
                        {"project_id": project_id, "scope_id": scope_value, "is_default": True},
                    ).first()
                    query = query.where(model.scope_id == int(scope_row[0])) if scope_row is not None else query.where(model.scope_id == -1)
                elif scope_value.isdigit():
                    query = query.where(model.scope_id == int(scope_value))
                elif project_id is not None:
                    query = query.where(model.scope_id == project_id)
            elif scope_id and hasattr(model, "scope") and str(scope_id) not in {"default", "project"}:
                query = query.where(model.scope.in_([scope_id, "project"]))
            total = int(session.scalar(select(func.count()).select_from(query.subquery())) or 0)
            column = getattr(model, sort, None) or getattr(model, "created_at", model.id)
            query = query.order_by(column.asc() if order == "asc" else column.desc(), model.id.desc())
            return session.scalars(query.offset((page - 1) * page_size).limit(page_size)).all(), total

    @router.get("/me")
    def me(request: Request, current: Principal = Depends(principal)) -> dict[str, Any]:
        return {"data": {"project_key": current.project_key, "permissions": sorted(current.permissions)}, "request_id": _request_id(request)}

    @router.get("/dashboard")
    def dashboard(request: Request, current: Principal = Depends(principal)) -> dict[str, Any]:
        with session_factory() as session:
            project = session.scalar(select(ProjectRow).where(ProjectRow.project_key == current.project_key))
            project_id = project.id if project else None
            counts: dict[str, int] = {}
            for name, model in (("raw_records", MessageRow), ("candidates", MemoryCandidateRow), ("memories", MemoryRow), ("jobs", ProcessingJobRow)):
                query = select(func.count()).select_from(model)
                if project_id is not None and hasattr(model, "project_id"):
                    query = query.where(model.project_id == project_id)
                counts[name] = int(session.scalar(query) or 0)
        return {"data": counts, "request_id": _request_id(request)}

    @router.get("/projects")
    def projects(request: Request, current: Principal = Depends(principal), paging: tuple[int, int, str, str, str] = Depends(pagination)) -> dict[str, Any]:
        page, page_size, sort, order, _ = paging
        with session_factory() as session:
            query = select(ProjectRow)
            if "admin" not in current.permissions:
                query = query.where(ProjectRow.project_key == current.project_key)
            total = int(session.scalar(select(func.count()).select_from(query.subquery())) or 0)
            column = getattr(ProjectRow, sort, ProjectRow.created_at)
            rows = session.scalars(query.order_by(column.asc() if order == "asc" else column.desc()).offset((page - 1) * page_size).limit(page_size)).all()
            return list_response(request, rows, total, page, page_size, lambda row: {"project_key": row.project_key, "name": row.name, "repository": row.repository, "status": row.status})

    @router.get("/projects/{project_key}")
    def project_detail(project_key: str, request: Request, current: Principal = Depends(principal)) -> dict[str, Any]:
        project = project_context(request, project_key, None, current)
        return {"data": {"id": project.id, "project_key": project.project_key, "name": project.name, "repository": project.repository, "description": project.description, "status": project.status}, "request_id": _request_id(request)}

    @router.post("/contract-services")
    def create_contract_service(payload: ContractServiceCreateRequest, request: Request, current: Principal = Depends(principal)) -> dict[str, Any]:
        try:
            require_permission(current, "admin")
        except PermissionDenied as error:
            raise _error(request, "permission_denied", str(error), status.HTTP_403_FORBIDDEN) from error
        project_key = (payload.project_key or (current.project_key if current.project_key != "*" else "")).strip()
        if not project_key:
            raise _error(request, "project_required", "必须指定项目键", status.HTTP_422_UNPROCESSABLE_ENTITY)
        project = project_context(request, project_key, None, current)
        service_key = (payload.service_key or payload.key or payload.service_name or payload.name or "").strip()
        if not service_key:
            raise _error(request, "service_key_required", "必须指定服务标识或名称", status.HTTP_422_UNPROCESSABLE_ENTITY)
        try:
            row = contract_service.create_service(project.id, service_key, payload.name, payload.description)
        except ContractRevisionConflictError as error:
            raise _error(request, error.code, str(error), status.HTTP_409_CONFLICT) from error
        return {"data": {"id": row.id, "project_id": row.project_id, "service_key": row.service_key, "name": row.name, "description": row.description, "created_at": _row_value(row, "created_at"), "updated_at": _row_value(row, "updated_at"), "revisions": []}, "meta": {}, "request_id": _request_id(request)}

    @router.get("/contract-services")
    def list_contract_services(request: Request, project_key: str | None = None, current: Principal = Depends(principal), paging: tuple[int, int, str, str, str] = Depends(pagination)) -> dict[str, Any]:
        if project_key:
            project_id = project_context(request, project_key, None, current).id
        elif "admin" in current.permissions:
            project_id = None
        else:
            project_id = project_context(request, current.project_key, None, current).id
        page, page_size, _, _, _ = paging
        rows = contract_service.list_services(project_id)
        total = len(rows)
        items = []
        for row in rows[(page - 1) * page_size: page * page_size]:
            detail = contract_service.service_detail(row.id, row.project_id)
            items.append({**detail["service"], "revisions": detail["revisions"]})
        return _page(items, total, page, page_size, _request_id(request))

    def _contract_service_project(request: Request, service_id: int, current: Principal, *, require_admin: bool = False) -> tuple[ContractServiceRow, int]:
        if require_admin:
            try:
                require_permission(current, "admin")
            except PermissionDenied as error:
                raise _error(request, "permission_denied", str(error), status.HTTP_403_FORBIDDEN) from error
        with session_factory() as session:
            row = session.get(ContractServiceRow, service_id)
            if row is None:
                raise _error(request, "service_not_found", "服务不存在", status.HTTP_404_NOT_FOUND)
            project = session.get(ProjectRow, row.project_id)
            if project is None:
                raise _error(request, "project_not_found", "项目不存在", status.HTTP_404_NOT_FOUND)
            try:
                require_project_access(current, project.project_key)
            except ProjectAccessDenied as error:
                raise _error(request, "project_access_denied", str(error), status.HTTP_403_FORBIDDEN) from error
            session.expunge(row)
            return row, project.id

    @router.get("/contract-services/{service_id}")
    def contract_service_detail(service_id: int, request: Request, current: Principal = Depends(principal)) -> dict[str, Any]:
        row, project_id = _contract_service_project(request, service_id, current)
        try:
            detail = contract_service.service_detail(row.id, project_id)
        except LookupError as error:
            raise _error(request, "service_not_found", str(error), status.HTTP_404_NOT_FOUND) from error
        return {"data": detail, "meta": {}, "request_id": _request_id(request)}

    @router.post("/contract-services/{service_id}/revisions")
    def upload_contract_revision(service_id: int, request: Request, file: UploadFile = File(...), current: Principal = Depends(principal)) -> dict[str, Any]:
        row, project_id = _contract_service_project(request, service_id, current, require_admin=True)
        filename = file.filename or ""
        try:
            content = file.file.read(MAX_DOCUMENT_BYTES + 1)
        finally:
            file.file.close()
        if not isinstance(content, bytes):
            content = bytes(content)
        try:
            revision, reused = contract_service.create_revision(row.id, filename, content, project_id)
        except OpenAPIContractError as error:
            raise _error(request, "invalid_openapi_document", str(error), status.HTTP_422_UNPROCESSABLE_ENTITY, meta={"validation_errors": error.errors}) from error
        except ContractRevisionConflictError as error:
            raise _error(request, error.code, str(error), status.HTTP_409_CONFLICT, meta=error.meta) from error
        except LookupError as error:
            raise _error(request, "service_not_found", str(error), status.HTTP_404_NOT_FOUND) from error
        data = contract_service.get_revision(row.id, revision.revision_number, project_id)
        return {"data": data, "meta": {"reused": reused}, "request_id": _request_id(request)}

    @router.get("/contract-services/{service_id}/revisions/{revision_number}")
    def contract_revision_detail(service_id: int, revision_number: int, request: Request, current: Principal = Depends(principal)) -> dict[str, Any]:
        row, project_id = _contract_service_project(request, service_id, current)
        try:
            data = contract_service.get_revision(row.id, revision_number, project_id)
        except LookupError as error:
            raise _error(request, "revision_not_found", str(error), status.HTTP_404_NOT_FOUND) from error
        return {"data": data, "meta": {}, "request_id": _request_id(request)}

    @router.post("/contract-services/{service_id}/revisions/{revision_number}/publish")
    def publish_contract_revision(service_id: int, revision_number: int, payload: ContractPublishRequest, request: Request, current: Principal = Depends(principal)) -> dict[str, Any]:
        row, project_id = _contract_service_project(request, service_id, current, require_admin=True)
        try:
            revision, idempotent = contract_service.publish(row.id, revision_number, payload.expected_content_hash, project_id)
        except ContractRevisionConflictError as error:
            raise _error(request, error.code, str(error), status.HTTP_409_CONFLICT, meta=error.meta) from error
        except LookupError as error:
            raise _error(request, "revision_not_found", str(error), status.HTTP_404_NOT_FOUND) from error
        data = contract_service.get_revision(row.id, revision.revision_number, project_id)
        return {"data": data, "meta": {"idempotent": idempotent}, "request_id": _request_id(request)}

    @router.get("/projects/{project_key}/scopes")
    def scopes(project_key: str, request: Request, current: Principal = Depends(principal), paging: tuple[int, int, str, str, str] = Depends(pagination)) -> dict[str, Any]:
        project = project_context(request, project_key, None, current)
        page, page_size, _, _, _ = paging
        with session_factory() as session:
            if not inspect(session.bind).has_table("knowledge_scopes"):
                return _page([], 0, page, page_size, _request_id(request))
            rows = session.execute(text("SELECT id, scope_key, name, description, is_default, status, created_at, updated_at FROM knowledge_scopes WHERE project_id = :id ORDER BY id"), {"id": project.id}).mappings().all()
        items = [dict(row) for row in rows]
        return _page(items[(page - 1) * page_size: page * page_size], len(items), page, page_size, _request_id(request))

    @router.get("/scopes")
    def scopes_alias(request: Request, current: Principal = Depends(principal), paging: tuple[int, int, str, str, str] = Depends(pagination)) -> dict[str, Any]:
        return scopes(current.project_key, request, current, paging)
    def query_list(model: Any, mapper: Callable[[Any], dict[str, Any]], project_key: str | None, scope_id: str | None, request: Request, current: Principal, paging: tuple[int, int, str, str, str]) -> dict[str, Any]:
        project_id = None
        if project_key:
            project_id = project_context(request, project_key, scope_id, current).id
        elif "admin" not in current.permissions:
            project_id = project_context(request, current.project_key, scope_id, current).id
        page, page_size, sort, order, _ = paging
        try:
            rows, total = query_rows(model, project_id, page, page_size, sort, order, scope_id)
        except OperationalError:
            rows, total = [], 0
        return list_response(request, rows, total, page, page_size, mapper)

    @router.get("/raw-records")
    def raw_records(request: Request, project_key: str | None = None, scope_id: str | None = None, current: Principal = Depends(principal), paging: tuple[int, int, str, str, str] = Depends(pagination)) -> dict[str, Any]:
        return query_list(MessageRow, lambda row: {"id": row.id, "project_id": row.project_id, "event_key": row.event_key, "role": row.role, "content": _redact(row.content), "source": row.source, "created_at": _row_value(row, "created_at")}, project_key, scope_id, request, current, paging)

    @router.get("/candidates")
    def candidates(request: Request, project_key: str | None = None, scope_id: str | None = None, current: Principal = Depends(principal), paging: tuple[int, int, str, str, str] = Depends(pagination)) -> dict[str, Any]:
        return query_list(MemoryCandidateRow, lambda row: {"id": row.id, "project_id": row.project_id, "level": row.level, "scope": row.scope, "memory_type": row.memory_type, "title": row.title, "content": _redact(row.content), "status": row.status, "abstain": row.abstain, "published_memory_id": row.published_memory_id, "created_at": _row_value(row, "created_at")}, project_key, scope_id, request, current, paging)

    @router.get("/memories")
    def memories(request: Request, project_key: str | None = None, scope_id: str | None = None, current: Principal = Depends(principal), paging: tuple[int, int, str, str, str] = Depends(pagination)) -> dict[str, Any]:
        return query_list(MemoryRow, lambda row: {"id": row.id, "project_id": row.project_id, "level": row.level, "memory_type": row.memory_type, "title": row.title, "content": _redact(row.content), "confidence": row.confidence, "status": row.status, "scope": row.scope, "created_at": _row_value(row, "created_at")}, project_key, scope_id, request, current, paging)

    @router.get("/jobs")
    def jobs(request: Request, project_key: str | None = None, scope_id: str | None = None, current: Principal = Depends(principal), paging: tuple[int, int, str, str, str] = Depends(pagination)) -> dict[str, Any]:
        return query_list(ProcessingJobRow, lambda row: {"id": row.id, "project_id": row.project_id, "job_type": row.job_type, "job_key": row.job_key, "status": row.status, "attempt_count": row.attempt_count, "last_error_code": row.last_error_code, "created_at": _row_value(row, "created_at")}, project_key, scope_id, request, current, paging)

    @router.get("/outbox-events")
    def outbox_events(request: Request, project_key: str | None = None, scope_id: str | None = None, current: Principal = Depends(principal), paging: tuple[int, int, str, str, str] = Depends(pagination)) -> dict[str, Any]:
        return query_list(OutboxEventRow, lambda row: {"id": row.id, "project_id": row.project_id, "event_type": row.event_type, "status": row.status, "attempt_count": row.attempt_count, "created_at": _row_value(row, "created_at")}, project_key, scope_id, request, current, paging)

    @router.get("/retrieval-audits")
    def retrieval_audits(request: Request, project_key: str | None = None, scope_id: str | None = None, current: Principal = Depends(principal), paging: tuple[int, int, str, str, str] = Depends(pagination)) -> dict[str, Any]:
        return query_list(RetrievalAuditRow, lambda row: {"id": row.id, "project_id": row.project_id, "retrieval_mode": row.retrieval_mode, "degraded": row.degraded, "degraded_reason": row.degraded_reason, "latency_ms": row.latency_ms}, project_key, scope_id, request, current, paging)

    @router.get("/audit-events")
    def audit_events(request: Request, project_key: str | None = None, scope_id: str | None = None, current: Principal = Depends(principal), paging: tuple[int, int, str, str, str] = Depends(pagination)) -> dict[str, Any]:
        return query_list(AuditLogRow, lambda row: {"id": row.id, "project_id": row.project_id, "event_type": row.event_type, "subject_type": row.subject_type, "subject_id": row.subject_id, "created_at": _row_value(row, "created_at")}, project_key, scope_id, request, current, paging)



    @router.get("/import-batches")
    def import_batches(
        request: Request,
        project_key: str | None = None,
        scope_id: str | None = None,
        current: Principal = Depends(principal),
        paging: tuple[int, int, str, str, str] = Depends(pagination),
    ) -> dict[str, Any]:
        return query_list(
            ImportBatchRow,
            lambda row: {"id": row.id, "project_id": row.project_id, "status": row.status, "scope_key": row.scope_key, "scope_id": row.scope_id, "source_type": row.source_type, "source_count": row.source_count, "document_count": row.document_count, "chunk_count": row.chunk_count, "error_count": row.error_count, "processed_count": row.processed_count, "retry_count": row.retry_count, "created_at": _row_value(row, "created_at"), "started_at": _row_value(row, "started_at"), "completed_at": _row_value(row, "completed_at"), "cancelled_at": _row_value(row, "cancelled_at"), "rolled_back_at": _row_value(row, "rolled_back_at")},
            project_key,
            scope_id,
            request,
            current,
            paging,
        )

    @router.post("/import-batches")
    def create_import_batch(payload: ImportBatchCreateRequest, request: Request, current: Principal = Depends(principal)) -> dict[str, Any]:
        require_permission(current, "admin")
        if not payload.project_key:
            raise _error(request, "project_required", "必须指定项目键", status.HTTP_422_UNPROCESSABLE_ENTITY)
        try:
            project_context(request, payload.project_key, payload.scope_key, current)
            from ..pipelines.v131_import import ImportItem, KnowledgeImportService
            service = KnowledgeImportService(session_factory)
            if payload.items is None:
                batch_id = service.create_batch(payload.project_key, payload.scope_key, payload.metadata)
                write_import_audit(payload.project_key, "import.batch.created", "import_batch", str(batch_id), {"mode": "async", "request_id": _request_id(request)})
                return {"data": {"batch_id": batch_id, "status": "draft", "source_count": 0}, "request_id": _request_id(request)}
            if not payload.items or len(payload.items) > 100:
                raise _error(request, "invalid_import_items", "每批必须包含 1 至 100 个导入项", status.HTTP_422_UNPROCESSABLE_ENTITY)
            total_bytes = sum(_item_size(item) for item in payload.items)
            if total_bytes > 4 * 1024 * 1024:
                raise _error(request, "import_too_large", "单批导入内容不能超过 4 MiB", status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)
            result = service.import_items(
                payload.project_key,
                [ImportItem(item.source_name, _item_content(item), item.source_type, item.metadata) for item in payload.items],
                {**payload.metadata, "scope_key": payload.scope_key},
            )
        except (LookupError, ValueError, FileNotFoundError) as error:
            raise _error(request, "import_failed", str(error), status.HTTP_422_UNPROCESSABLE_ENTITY) from error
        write_import_audit(payload.project_key, "import.batch.created", "import_batch", str(result.batch_id), {"mode": "inline_compatibility", "documents": result.documents, "request_id": _request_id(request)})
        return {"data": result.__dict__, "request_id": _request_id(request)}

    @router.post("/import-batches/{batch_id}/files")
    def upload_import_files(batch_id: int, payload: ImportBatchCreateRequest, request: Request, current: Principal = Depends(principal)) -> dict[str, Any]:
        require_permission(current, "admin")
        if not payload.items or len(payload.items) > 100:
            raise _error(request, "invalid_import_items", "每次必须包含 1 至 100 个导入文件", status.HTTP_422_UNPROCESSABLE_ENTITY)
        try:
            total_bytes = sum(_item_size(item) for item in payload.items)
        except ValueError as error:
            raise _error(request, "invalid_import_content", str(error), status.HTTP_422_UNPROCESSABLE_ENTITY) from error
        if total_bytes > 4 * 1024 * 1024:
            raise _error(request, "import_too_large", "单次上传内容不能超过 4 MiB", status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)
        from ..pipelines.v131_import import ImportItem, KnowledgeImportService
        with session_factory() as session:
            batch = session.get(ImportBatchRow, batch_id)
            if batch is None:
                raise _error(request, "batch_not_found", "导入批次不存在", status.HTTP_404_NOT_FOUND)
            project = session.get(ProjectRow, batch.project_id)
            if project is None:
                raise _error(request, "project_not_found", "项目不存在", status.HTTP_404_NOT_FOUND)
            if not _scope_allowed(session, project, batch.scope_id or batch.scope_key):
                raise _error(request, "scope_access_denied", f"令牌无权访问作用域：{batch.scope_id or batch.scope_key}", status.HTTP_403_FORBIDDEN)
            try:
                require_project_access(current, project.project_key)
            except ProjectAccessDenied as error:
                raise _error(request, "project_access_denied", str(error), status.HTTP_403_FORBIDDEN) from error
        try:
            result = KnowledgeImportService(session_factory).add_files(batch_id, [ImportItem(item.source_name, _item_content(item), item.source_type, item.metadata) for item in payload.items])
        except (LookupError, ValueError) as error:
            raise _error(request, "import_upload_failed", str(error), status.HTTP_422_UNPROCESSABLE_ENTITY) from error
        write_import_audit(project.project_key, "import.files.uploaded", "import_batch", str(batch_id), {"added": result["added"], "request_id": _request_id(request)})
        return {"data": result, "request_id": _request_id(request)}

    @router.post("/import-batches/{batch_id}/uploads")
    def begin_import_upload(batch_id: int, payload: ImportChunkStartRequest, request: Request, current: Principal = Depends(principal)) -> dict[str, Any]:
        require_permission(current, "admin")
        with session_factory() as session:
            batch = session.get(ImportBatchRow, batch_id)
            if batch is None:
                raise _error(request, "batch_not_found", "???????", status.HTTP_404_NOT_FOUND)
            project = session.get(ProjectRow, batch.project_id)
            if project is None:
                raise _error(request, "project_not_found", "?????", status.HTTP_404_NOT_FOUND)
            if not _scope_allowed(session, project, batch.scope_id or batch.scope_key):
                raise _error(request, "scope_access_denied", f"??????????{batch.scope_id or batch.scope_key}", status.HTTP_403_FORBIDDEN)
            try:
                require_project_access(current, project.project_key)
            except ProjectAccessDenied as error:
                raise _error(request, "project_access_denied", str(error), status.HTTP_403_FORBIDDEN) from error
        from ..pipelines.v131_import import KnowledgeImportService
        try:
            result = KnowledgeImportService(session_factory).begin_upload(batch_id, payload.source_name, payload.source_type, payload.total_parts, payload.metadata)
        except (LookupError, ValueError) as error:
            raise _error(request, "import_upload_init_failed", str(error), status.HTTP_422_UNPROCESSABLE_ENTITY) from error
        write_import_audit(project.project_key, "import.upload.started", "import_batch", str(batch_id), {"upload_id": result["upload_id"], "request_id": _request_id(request)})
        return {"data": result, "request_id": _request_id(request)}

    @router.get("/import-batches/{batch_id}/uploads/{upload_id}")
    def import_upload_status(batch_id: int, upload_id: str, request: Request, current: Principal = Depends(principal)) -> dict[str, Any]:
        with session_factory() as session:
            batch = session.get(ImportBatchRow, batch_id)
            if batch is None:
                raise _error(request, "batch_not_found", "???????", status.HTTP_404_NOT_FOUND)
            project = session.get(ProjectRow, batch.project_id)
            if project is None:
                raise _error(request, "project_not_found", "?????", status.HTTP_404_NOT_FOUND)
            if not _scope_allowed(session, project, batch.scope_id or batch.scope_key):
                raise _error(request, "scope_access_denied", f"??????????{batch.scope_id or batch.scope_key}", status.HTTP_403_FORBIDDEN)
            try:
                require_project_access(current, project.project_key)
            except ProjectAccessDenied as error:
                raise _error(request, "project_access_denied", str(error), status.HTTP_403_FORBIDDEN) from error
            rows = session.scalars(select(ImportUploadPartRow).where(ImportUploadPartRow.import_batch_id == batch_id, ImportUploadPartRow.upload_id == upload_id).order_by(ImportUploadPartRow.part_number)).all()
            if not rows:
                raise _error(request, "upload_not_found", "?????????", status.HTTP_404_NOT_FOUND)
            return {"data": {"upload_id": upload_id, "source_name": rows[0].source_name, "source_type": rows[0].source_type, "total_parts": rows[0].total_parts, "uploaded_parts": [row.part_number for row in rows if row.status in {"uploaded", "completed"}], "status": "completed" if all(row.status == "completed" for row in rows) else "uploading"}, "request_id": _request_id(request)}

    @router.put("/import-batches/{batch_id}/uploads/{upload_id}/parts/{part_number}")
    def put_import_upload_part(batch_id: int, upload_id: str, part_number: int, payload: ImportChunkPartRequest, request: Request, current: Principal = Depends(principal)) -> dict[str, Any]:
        require_permission(current, "admin")
        try:
            size = _chunk_size(payload)
        except ValueError as error:
            raise _error(request, "invalid_import_content", str(error), status.HTTP_422_UNPROCESSABLE_ENTITY) from error
        if size > 4 * 1024 * 1024:
            raise _error(request, "import_part_too_large", "???????? 4 MiB", status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)
        from ..pipelines.v131_import import KnowledgeImportService
        with session_factory() as session:
            batch = session.get(ImportBatchRow, batch_id)
            if batch is None:
                raise _error(request, "batch_not_found", "???????", status.HTTP_404_NOT_FOUND)
            project = session.get(ProjectRow, batch.project_id)
            if project is None:
                raise _error(request, "project_not_found", "?????", status.HTTP_404_NOT_FOUND)
            if not _scope_allowed(session, project, batch.scope_id or batch.scope_key):
                raise _error(request, "scope_access_denied", f"??????????{batch.scope_id or batch.scope_key}", status.HTTP_403_FORBIDDEN)
            try:
                require_project_access(current, project.project_key)
            except ProjectAccessDenied as error:
                raise _error(request, "project_access_denied", str(error), status.HTTP_403_FORBIDDEN) from error
            part = session.scalar(select(ImportUploadPartRow).where(ImportUploadPartRow.import_batch_id == batch_id, ImportUploadPartRow.upload_id == upload_id).order_by(ImportUploadPartRow.part_number))
            if part is None:
                if payload.total_parts is None or not payload.source_name:
                    raise _error(request, "upload_not_found", "?????????????????", status.HTTP_404_NOT_FOUND)
                source_name, source_type, total_parts = payload.source_name, payload.source_type, payload.total_parts
            else:
                source_name, source_type, total_parts = part.source_name, part.source_type, part.total_parts
        try:
            result = KnowledgeImportService(session_factory).put_upload_part(batch_id, upload_id, part_number, total_parts, source_name, source_type, _chunk_content(payload))
        except (LookupError, ValueError) as error:
            raise _error(request, "import_part_failed", str(error), status.HTTP_422_UNPROCESSABLE_ENTITY) from error
        return {"data": result, "request_id": _request_id(request)}

    @router.post("/import-batches/{batch_id}/uploads/{upload_id}:complete")
    @router.post("/import-batches/{batch_id}/uploads/{upload_id}/complete")
    def complete_import_upload(batch_id: int, upload_id: str, request: Request, current: Principal = Depends(principal)) -> dict[str, Any]:
        require_permission(current, "admin")
        with session_factory() as session:
            batch = session.get(ImportBatchRow, batch_id)
            if batch is None:
                raise _error(request, "batch_not_found", "???????", status.HTTP_404_NOT_FOUND)
            project = session.get(ProjectRow, batch.project_id)
            if project is None:
                raise _error(request, "project_not_found", "?????", status.HTTP_404_NOT_FOUND)
            if not _scope_allowed(session, project, batch.scope_id or batch.scope_key):
                raise _error(request, "scope_access_denied", f"??????????{batch.scope_id or batch.scope_key}", status.HTTP_403_FORBIDDEN)
            try:
                require_project_access(current, project.project_key)
            except ProjectAccessDenied as error:
                raise _error(request, "project_access_denied", str(error), status.HTTP_403_FORBIDDEN) from error
        from ..pipelines.v131_import import KnowledgeImportService
        try:
            result = KnowledgeImportService(session_factory).complete_upload(batch_id, upload_id)
        except (LookupError, ValueError) as error:
            raise _error(request, "import_upload_complete_failed", str(error), status.HTTP_422_UNPROCESSABLE_ENTITY) from error
        write_import_audit(project.project_key, "import.upload.completed", "import_batch", str(batch_id), {"upload_id": upload_id, "request_id": _request_id(request)})
        return {"data": result, "request_id": _request_id(request)}

    @router.post("/import-batches/{batch_id}:start")
    @router.post("/import-batches/{batch_id}/start")
    def start_import_batch(batch_id: int, request: Request, current: Principal = Depends(principal)) -> dict[str, Any]:
        require_permission(current, "admin")
        with session_factory() as session:
            batch = session.get(ImportBatchRow, batch_id)
            if batch is None:
                raise _error(request, "batch_not_found", "导入批次不存在", status.HTTP_404_NOT_FOUND)
            project = session.get(ProjectRow, batch.project_id)
            if project is None:
                raise _error(request, "project_not_found", "项目不存在", status.HTTP_404_NOT_FOUND)
            if not _scope_allowed(session, project, batch.scope_id or batch.scope_key):
                raise _error(request, "scope_access_denied", f"令牌无权访问作用域：{batch.scope_id or batch.scope_key}", status.HTTP_403_FORBIDDEN)
            try:
                require_project_access(current, project.project_key)
            except ProjectAccessDenied as error:
                raise _error(request, "project_access_denied", str(error), status.HTTP_403_FORBIDDEN) from error
        from ..pipelines.v131_import import KnowledgeImportService
        try:
            result = KnowledgeImportService(session_factory).start_batch(batch_id)
        except (LookupError, ValueError) as error:
            raise _error(request, "import_start_failed", str(error), status.HTTP_422_UNPROCESSABLE_ENTITY) from error
        write_import_audit(project.project_key, "import.batch.queued", "import_batch", str(batch_id), {"queued": result["queued"], "request_id": _request_id(request)})
        return {"data": result, "request_id": _request_id(request)}

    @router.get("/import-batches/{batch_id}/files")
    def import_batch_files(batch_id: int, request: Request, current: Principal = Depends(principal)) -> dict[str, Any]:
        with session_factory() as session:
            batch = session.get(ImportBatchRow, batch_id)
            if batch is None:
                raise _error(request, "batch_not_found", "导入批次不存在", status.HTTP_404_NOT_FOUND)
            project = session.get(ProjectRow, batch.project_id)
            if project is None:
                raise _error(request, "project_not_found", "项目不存在", status.HTTP_404_NOT_FOUND)
            if not _scope_allowed(session, project, batch.scope_id or batch.scope_key):
                raise _error(request, "scope_access_denied", f"令牌无权访问作用域：{batch.scope_id or batch.scope_key}", status.HTTP_403_FORBIDDEN)
            try:
                require_project_access(current, project.project_key)
            except ProjectAccessDenied as error:
                raise _error(request, "project_access_denied", str(error), status.HTTP_403_FORBIDDEN) from error
            rows = session.scalars(select(ImportFileRow).where(ImportFileRow.import_batch_id == batch_id).order_by(ImportFileRow.id)).all()
            data = [{"id": row.id, "scope_id": row.scope_id, "source_name": row.source_name, "source_type": row.source_type, "size_bytes": row.size_bytes, "content_hash": row.content_hash, "storage_backend": row.storage_backend, "storage_key": row.storage_key, "status": row.status, "error_message": row.error_message, "created_at": _row_value(row, "created_at"), "updated_at": _row_value(row, "updated_at")} for row in rows]
            return {"data": data, "request_id": _request_id(request)}

    @router.get("/import-batches/{batch_id}/issues")
    def import_batch_issues(batch_id: int, request: Request, current: Principal = Depends(principal)) -> dict[str, Any]:
        with session_factory() as session:
            batch = session.get(ImportBatchRow, batch_id)
            if batch is None:
                raise _error(request, "batch_not_found", "导入批次不存在", status.HTTP_404_NOT_FOUND)
            project = session.get(ProjectRow, batch.project_id)
            if project is None:
                raise _error(request, "project_not_found", "项目不存在", status.HTTP_404_NOT_FOUND)
            if not _scope_allowed(session, project, batch.scope_id or batch.scope_key):
                raise _error(request, "scope_access_denied", f"令牌无权访问作用域：{batch.scope_id or batch.scope_key}", status.HTTP_403_FORBIDDEN)
            try:
                require_project_access(current, project.project_key)
            except ProjectAccessDenied as error:
                raise _error(request, "project_access_denied", str(error), status.HTTP_403_FORBIDDEN) from error
            rows = session.scalars(select(ImportIssueRow).where(ImportIssueRow.import_batch_id == batch_id).order_by(ImportIssueRow.id)).all()
            data = [{"id": row.id, "scope_id": row.scope_id, "import_file_id": row.import_file_id, "source_document_id": row.source_document_id, "issue_type": row.issue_type, "severity": row.severity, "message": row.message, "metadata": _redact(row.metadata_json), "created_at": _row_value(row, "created_at")} for row in rows]
            return {"data": data, "request_id": _request_id(request)}
    @router.get("/reference-candidates")
    def reference_candidates(request: Request, project_key: str | None = None, scope_id: str | None = None, current: Principal = Depends(principal), paging: tuple[int, int, str, str, str] = Depends(pagination)) -> dict[str, Any]:
        return query_list(
            ReferenceCandidateRow,
            lambda row: {"id": row.id, "project_id": row.project_id, "title": row.title, "content": _redact(row.content), "status": row.status, "confidence": row.confidence, "document_id": row.document_id, "chunk_id": row.chunk_id, "scope_key": row.scope_key, "scope_id": row.scope_id, "published_memory_id": row.published_memory_id, "reviewer": row.reviewer, "review_reason": row.review_reason, "created_at": _row_value(row, "created_at"), "reviewed_at": _row_value(row, "reviewed_at"), "rolled_back_at": _row_value(row, "rolled_back_at")},
            project_key,
            scope_id,
            request,
            current,
            paging,
        )

    @router.post("/reference-candidates/{candidate_id}/review")
    def review_reference_candidate(candidate_id: int, payload: ImportReviewRequest, request: Request, current: Principal = Depends(principal)) -> dict[str, Any]:
        require_permission(current, "admin")
        with session_factory() as session:
            candidate = session.get(ReferenceCandidateRow, candidate_id)
            if candidate is None:
                raise _error(request, "candidate_not_found", "导入候选不存在", status.HTTP_404_NOT_FOUND)
            project = session.get(ProjectRow, candidate.project_id)
            if project is None:
                raise _error(request, "project_not_found", "项目不存在", status.HTTP_404_NOT_FOUND)
            try:
                require_project_access(current, project.project_key)
            except ProjectAccessDenied as error:
                raise _error(request, "project_access_denied", str(error), status.HTTP_403_FORBIDDEN) from error
        try:
            from ..pipelines.v131_import import KnowledgeImportService
            result = KnowledgeImportService(session_factory).review_candidate(candidate_id, payload.decision, payload.reviewer, payload.reason)
        except (LookupError, ValueError) as error:
            raise _error(request, "candidate_review_failed", str(error), status.HTTP_422_UNPROCESSABLE_ENTITY) from error
        write_import_audit(project.project_key, "import.candidate.reviewed", "reference_candidate", str(candidate_id), {"decision": payload.decision, "reviewer": payload.reviewer, "request_id": _request_id(request)})
        return {"data": result, "request_id": _request_id(request)}

    @router.post("/reference-candidates/{candidate_id}/rollback")
    def rollback_reference_candidate(candidate_id: int, payload: ImportReviewRequest, request: Request, current: Principal = Depends(principal)) -> dict[str, Any]:
        require_permission(current, "admin")
        with session_factory() as session:
            candidate = session.get(ReferenceCandidateRow, candidate_id)
            if candidate is None:
                raise _error(request, "candidate_not_found", "导入候选不存在", status.HTTP_404_NOT_FOUND)
            project = session.get(ProjectRow, candidate.project_id)
            if project is None:
                raise _error(request, "project_not_found", "项目不存在", status.HTTP_404_NOT_FOUND)
            require_project_access(current, project.project_key)
        try:
            from ..pipelines.v131_import import KnowledgeImportService
            result = KnowledgeImportService(session_factory).rollback_candidate(candidate_id, payload.reason)
        except (LookupError, ValueError) as error:
            raise _error(request, "candidate_rollback_failed", str(error), status.HTTP_422_UNPROCESSABLE_ENTITY) from error
        write_import_audit(project.project_key, "import.candidate.rolled_back", "reference_candidate", str(candidate_id), {"reason": payload.reason, "request_id": _request_id(request)})
        return {"data": result, "request_id": _request_id(request)}

    @router.post("/import-batches/{batch_id}/rollback")
    def rollback_import_batch(batch_id: int, payload: ImportReviewRequest, request: Request, current: Principal = Depends(principal)) -> dict[str, Any]:
        require_permission(current, "admin")
        with session_factory() as session:
            batch = session.get(ImportBatchRow, batch_id)
            if batch is None:
                raise _error(request, "batch_not_found", "导入批次不存在", status.HTTP_404_NOT_FOUND)
            project = session.get(ProjectRow, batch.project_id)
            if project is None:
                raise _error(request, "project_not_found", "项目不存在", status.HTTP_404_NOT_FOUND)
            if not _scope_allowed(session, project, batch.scope_id or batch.scope_key):
                raise _error(request, "scope_access_denied", f"令牌无权访问作用域：{batch.scope_id or batch.scope_key}", status.HTTP_403_FORBIDDEN)
            require_project_access(current, project.project_key)
        try:
            from ..pipelines.v131_import import KnowledgeImportService
            result = KnowledgeImportService(session_factory).rollback_batch(batch_id, payload.reason)
        except (LookupError, ValueError) as error:
            raise _error(request, "batch_rollback_failed", str(error), status.HTTP_422_UNPROCESSABLE_ENTITY) from error
        write_import_audit(project.project_key, "import.batch.rolled_back", "import_batch", str(batch_id), {"reason": payload.reason, "request_id": _request_id(request)})
        return {"data": result, "request_id": _request_id(request)}
    @router.get("/import-batches/{batch_id}")
    def import_batch_detail(batch_id: int, request: Request, current: Principal = Depends(principal)) -> dict[str, Any]:
        with session_factory() as session:
            batch = session.get(ImportBatchRow, batch_id)
            if batch is None:
                raise _error(request, "batch_not_found", "导入批次不存在", status.HTTP_404_NOT_FOUND)
            project = session.get(ProjectRow, batch.project_id)
            if project is None:
                raise _error(request, "project_not_found", "项目不存在", status.HTTP_404_NOT_FOUND)
            if not _scope_allowed(session, project, batch.scope_id or batch.scope_key):
                raise _error(request, "scope_access_denied", f"令牌无权访问作用域：{batch.scope_id or batch.scope_key}", status.HTTP_403_FORBIDDEN)
            try:
                require_project_access(current, project.project_key)
            except ProjectAccessDenied as error:
                raise _error(request, "project_access_denied", str(error), status.HTTP_403_FORBIDDEN) from error
            return {"data": {"id": batch.id, "project_id": batch.project_id, "status": batch.status, "scope_key": batch.scope_key, "source_type": batch.source_type, "source_count": batch.source_count, "processed_count": batch.processed_count, "document_count": batch.document_count, "chunk_count": batch.chunk_count, "error_count": batch.error_count, "retry_count": batch.retry_count, "error_message": batch.error_message, "created_at": _row_value(batch, "created_at"), "started_at": _row_value(batch, "started_at"), "completed_at": _row_value(batch, "completed_at"), "cancelled_at": _row_value(batch, "cancelled_at"), "rolled_back_at": _row_value(batch, "rolled_back_at")}, "request_id": _request_id(request)}

    @router.post("/import-batches/{batch_id}:retry")
    @router.post("/import-batches/{batch_id}/retry")
    def retry_import_batch(batch_id: int, request: Request, payload: ImportBatchCreateRequest | None = None, current: Principal = Depends(principal)) -> dict[str, Any]:
        require_permission(current, "admin")
        with session_factory() as session:
            batch = session.get(ImportBatchRow, batch_id)
            if batch is None:
                raise _error(request, "batch_not_found", "导入批次不存在", status.HTTP_404_NOT_FOUND)
            project = session.get(ProjectRow, batch.project_id)
            if project is None:
                raise _error(request, "project_not_found", "项目不存在", status.HTTP_404_NOT_FOUND)
            if not _scope_allowed(session, project, batch.scope_id or batch.scope_key):
                raise _error(request, "scope_access_denied", f"令牌无权访问作用域：{batch.scope_id or batch.scope_key}", status.HTTP_403_FORBIDDEN)
            try:
                require_project_access(current, project.project_key)
            except ProjectAccessDenied as error:
                raise _error(request, "project_access_denied", str(error), status.HTTP_403_FORBIDDEN) from error
            if batch.status not in {"failed", "cancelled"}:
                raise _error(request, "batch_not_retryable", "只有失败或已取消批次可以重试", status.HTTP_409_CONFLICT)
            retry_count = int(batch.retry_count or 0) + 1
        from ..pipelines.v131_import import ImportItem, KnowledgeImportService
        service = KnowledgeImportService(session_factory)
        if payload is None or payload.items is None:
            try:
                result = service.retry_batch(batch_id)
            except (LookupError, ValueError) as error:
                raise _error(request, "import_retry_failed", str(error), status.HTTP_422_UNPROCESSABLE_ENTITY) from error
            write_import_audit(project.project_key, "import.batch.retried", "import_batch", str(batch_id), {"mode": "async", "request_id": _request_id(request)})
            return {"data": result, "request_id": _request_id(request)}
        if not payload.items or len(payload.items) > 100:
            raise _error(request, "invalid_import_items", "每批必须包含 1 至 100 个导入项", status.HTTP_422_UNPROCESSABLE_ENTITY)
        try:
            total_bytes = sum(_item_size(item) for item in payload.items)
        except ValueError as error:
            raise _error(request, "invalid_import_content", str(error), status.HTTP_422_UNPROCESSABLE_ENTITY) from error
        if total_bytes > 4 * 1024 * 1024:
            raise _error(request, "import_too_large", "单批导入内容不能超过 4 MiB", status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)
        if payload.project_key != project.project_key:
            raise _error(request, "project_mismatch", "重试项目必须与原批次一致", status.HTTP_422_UNPROCESSABLE_ENTITY)
        try:
            result = service.import_items(
                project.project_key,
                [ImportItem(item.source_name, _item_content(item), item.source_type, item.metadata) for item in payload.items],
                {**payload.metadata, "scope_key": payload.scope_key, "retry_of": batch_id, "retry_count": retry_count},
            )
        except (LookupError, ValueError, FileNotFoundError) as error:
            raise _error(request, "import_retry_failed", str(error), status.HTTP_422_UNPROCESSABLE_ENTITY) from error
        with session_factory() as session:
            original = session.get(ImportBatchRow, batch_id)
            if original is not None:
                original.retry_count = retry_count
                session.commit()
        write_import_audit(project.project_key, "import.batch.retried", "import_batch", str(batch_id), {"new_batch_id": result.batch_id, "request_id": _request_id(request)})
        return {"data": result.__dict__ | {"retry_of": batch_id}, "request_id": _request_id(request)}

    @router.post("/import-batches/{batch_id}:cancel")
    @router.post("/import-batches/{batch_id}/cancel")
    def cancel_import_batch(batch_id: int, request: Request, current: Principal = Depends(principal)) -> dict[str, Any]:
        require_permission(current, "admin")
        with session_factory() as session:
            batch = session.get(ImportBatchRow, batch_id)
            if batch is None:
                raise _error(request, "batch_not_found", "导入批次不存在", status.HTTP_404_NOT_FOUND)
            project = session.get(ProjectRow, batch.project_id)
            if project is None:
                raise _error(request, "project_not_found", "项目不存在", status.HTTP_404_NOT_FOUND)
            if not _scope_allowed(session, project, batch.scope_id or batch.scope_key):
                raise _error(request, "scope_access_denied", f"令牌无权访问作用域：{batch.scope_id or batch.scope_key}", status.HTTP_403_FORBIDDEN)
            try:
                require_project_access(current, project.project_key)
            except ProjectAccessDenied as error:
                raise _error(request, "project_access_denied", str(error), status.HTTP_403_FORBIDDEN) from error
            if batch.status in {"completed", "failed", "cancelled", "rolled_back"}:
                raise _error(request, "batch_not_cancellable", "当前批次已经结束，不能取消", status.HTTP_409_CONFLICT)
            batch.status = "cancelled"
            batch.cancelled_at = datetime.now(timezone.utc)
            for file in session.scalars(select(ImportFileRow).where(ImportFileRow.import_batch_id == batch.id)).all():
                if file.status in {"uploaded", "queued", "processing"}:
                    file.status = "cancelled"
            for event in session.scalars(select(OutboxEventRow).where(OutboxEventRow.project_id == batch.project_id, OutboxEventRow.event_type == "document.imported.v1")).all():
                if (event.payload or {}).get("import_batch_id") == batch.id and event.status in {"pending", "retry_wait", "dispatched"}:
                    event.status = "cancelled"
                    event.locked_by = None
                    event.locked_at = None
                    event.lease_expires_at = None
            for job in session.scalars(select(ProcessingJobRow).where(ProcessingJobRow.project_id == batch.project_id, ProcessingJobRow.job_type == "parse_document")).all():
                if (job.payload or {}).get("import_batch_id") == batch.id and job.status in {"pending", "retry_wait", "running"}:
                    job.status = "cancelled"
                    job.cancelled_at = datetime.now(timezone.utc)
                    job.cancel_reason = "import batch cancelled"
                    job.locked_by = None
                    job.locked_at = None
                    job.heartbeat_at = None
                    job.lease_expires_at = None
                    if job.outbox_event_id:
                        event = session.get(OutboxEventRow, job.outbox_event_id)
                        if event is not None:
                            event.status = "cancelled"
                            event.locked_by = None
                            event.locked_at = None
                            event.lease_expires_at = None
            session.add(AuditLogRow(project_id=project.id, event_type="import.batch.cancelled", subject_type="import_batch", subject_id=str(batch.id), metadata_json={"request_id": _request_id(request)}))
            session.commit()
            return {"data": {"id": batch.id, "status": batch.status, "cancelled_at": _row_value(batch, "cancelled_at")}, "request_id": _request_id(request)}

    @router.get("/task-runs", response_model=TaskRunListResponse)
    def task_runs(
        request: Request,
        project_key: str | None = None,
        current: Principal = Depends(principal),
        paging: tuple[int, int, str, str, str] = Depends(pagination),
    ) -> dict[str, Any]:
        page, page_size, _sort, _order, request_id = paging
        project_id: int | None = None
        if project_key:
            project_id = project_context(request, project_key, None, current).id
        elif "admin" not in current.permissions:
            project_id = project_context(request, current.project_key, None, current).id
        with session_factory() as session:
            query = select(TaskRunRow)
            if project_id is not None:
                query = query.where(TaskRunRow.project_id == project_id)
            total = int(session.scalar(select(func.count()).select_from(query.subquery())) or 0)
            rows = session.scalars(query.order_by(TaskRunRow.created_at.desc(), TaskRunRow.id.desc()).offset((page - 1) * page_size).limit(page_size)).all()
            data = [
                TaskRunListItem(
                    id=row.id,
                    project_id=row.project_id,
                    session_key=row.session_key,
                    status=row.status,
                    started_at=_row_value(row, "started_at"),
                    ended_at=_row_value(row, "ended_at"),
                    current_report_revision=row.current_report_revision,
                ).model_dump()
                for row in rows
            ]
        return {"data": data, "meta": {"page": page, "page_size": page_size, "total": total, "has_next": page * page_size < total}, "request_id": request_id}

    def _task_run_for_admin(request: Request, task_run_id: int, current: Principal) -> TaskRunRow:
        with session_factory() as session:
            run = session.get(TaskRunRow, task_run_id)
            if run is None:
                raise _error(request, "task_run_not_found", "任务运行不存在", status.HTTP_404_NOT_FOUND)
            project = session.get(ProjectRow, run.project_id)
            if project is None:
                raise _error(request, "project_not_found", "项目不存在", status.HTTP_404_NOT_FOUND)
            try:
                require_project_access(current, project.project_key)
            except ProjectAccessDenied as error:
                raise _error(request, "project_access_denied", str(error), status.HTTP_403_FORBIDDEN) from error
            session.expunge(run)
            return run

    @router.get("/task-runs/{task_run_id}", response_model=TaskRunDetailResponse)
    def task_run_detail(task_run_id: int, request: Request, current: Principal = Depends(principal)) -> dict[str, Any]:
        run = _task_run_for_admin(request, task_run_id, current)
        with session_factory() as session:
            events = session.scalars(select(TaskEventRow).where(TaskEventRow.task_run_id == run.id).order_by(TaskEventRow.sequence_no, TaskEventRow.id)).all()
            reports = session.scalars(select(TaskReportRow).where(TaskReportRow.task_run_id == run.id).order_by(TaskReportRow.revision)).all()
            detail = TaskRunDetail(
                id=run.id,
                project_id=run.project_id,
                session_key=run.session_key,
                status=run.status,
                started_at=_row_value(run, "started_at"),
                ended_at=_row_value(run, "ended_at"),
                current_report_revision=run.current_report_revision,
                git_baseline=TaskGitBaseline(branch=run.git_branch, head=run.git_head, status_porcelain=run.git_status_porcelain, diff_hash=run.git_diff_hash, untracked=run.git_untracked_json, available=run.git_available),
                events=[TaskEventItem(id=item.id, event_key=item.event_key, event_type=item.event_type, sequence_no=item.sequence_no, occurred_at=_row_value(item, "occurred_at"), content_hash=item.content_hash, command_summary=item.command_summary, result_summary=item.result_summary, exit_code=item.exit_code, redaction_applied=item.redaction_applied, truncated=item.truncated) for item in events],
                reports=[TaskReportSummary(id=item.id, revision=item.revision, report_kind=item.report_kind, status=item.status, uncertain=item.uncertain, truncated=item.truncated, created_at=_row_value(item, "created_at")) for item in reports],
            )
        return {"data": detail.model_dump(), "request_id": _request_id(request)}

    @router.get("/task-runs/{task_run_id}/reports/{revision}", response_model=TaskReportDetailResponse)
    def task_report_detail(task_run_id: int, revision: int, request: Request, current: Principal = Depends(principal)) -> dict[str, Any]:
        _task_run_for_admin(request, task_run_id, current)
        with session_factory() as session:
            report = session.scalar(select(TaskReportRow).where(TaskReportRow.task_run_id == task_run_id, TaskReportRow.revision == revision))
            if report is None:
                raise _error(request, "task_report_not_found", "任务报告不存在", status.HTTP_404_NOT_FOUND)
            changes = session.scalars(select(TaskFileChangeRow).where(TaskFileChangeRow.report_id == report.id).order_by(TaskFileChangeRow.change_index)).all()
            detail = TaskReportDetail(
                id=report.id,
                project_id=report.project_id,
                task_run_id=report.task_run_id,
                source_event_id=report.source_event_id,
                revision=report.revision,
                report_kind=report.report_kind,
                status=report.status,
                report_json=report.report_json,
                body=report.body,
                content_hash=report.content_hash,
                uncertain=report.uncertain,
                truncated=report.truncated,
                created_at=_row_value(report, "created_at"),
                file_changes=[TaskFileChangeItem(id=item.id, change_index=item.change_index, path=item.path, old_path=item.old_path, change_type=item.change_type, before_hash=item.before_hash, after_hash=item.after_hash, attribution=item.attribution, metadata=item.metadata_json) for item in changes],
            )
        return {"data": detail.model_dump(), "request_id": _request_id(request)}

    @router.get("/system/status")
    def system_status(request: Request, current: Principal = Depends(principal)) -> dict[str, Any]:
        try:
            db_ok = "ok"
            with session_factory() as session:
                session.execute(text("SELECT 1"))
                migration = session.execute(text("SELECT version_num FROM alembic_version")).scalar() or "unknown"
                pending_jobs = session.execute(text("SELECT COUNT(1) FROM processing_jobs WHERE status = 'pending'")).scalar() or 0
                outbox_pending = session.execute(text("SELECT COUNT(1) FROM outbox_events WHERE status = 'pending'")).scalar() or 0
                dead_letters = session.execute(text("SELECT COUNT(1) FROM outbox_events WHERE status = 'dead'")).scalar() or 0
        except Exception:
            db_ok = "error"
            migration = "unknown"
            pending_jobs = 0
            outbox_pending = 0
            dead_letters = 0
        return {
            "data": {
                "database": db_ok,
                "migration_schema": "ok" if migration not in ("unknown", "none", None) else "pending",
                "latest_migration": str(migration or ""),
                "dialect": "postgresql",
                "pending_jobs": pending_jobs,
                "server_outbox": outbox_pending,
                "dead_letters": dead_letters,
            },
            "request_id": _request_id(request),
        }

    return router
