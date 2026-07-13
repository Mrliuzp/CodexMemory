from __future__ import annotations

import os
import re
import secrets
from datetime import datetime
from typing import Any, Callable
from uuid import uuid4

from fastapi import APIRouter, Depends, Query, Request, status
from pydantic import BaseModel
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import func, inspect, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from ..auth import PermissionDenied, ProjectAccessDenied, Principal, TokenAuthenticationError, authenticate_bearer, require_permission, require_project_access, issue_admin_session
from ..db_models import AuditLogRow, MemoryRow, MessageRow, ProjectRow
from ..v11_models import MemoryCandidateRow, OutboxEventRow, ProcessingJobRow, RetrievalAuditRow

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
        self.headers: dict[str, str] = {}
        super().__init__(message)


def _error(request: Request, code: str, message: str, status_code: int) -> AdminAPIError:
    return AdminAPIError(status_code, code, message, _request_id(request))

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
    project_suffix = project.project_key.removeprefix("project-")
    if scope_id in {"default", f"scope-{project_suffix}"}:
        return True
    if not inspect(session.bind).has_table("knowledge_scopes"):
        return False
    row = session.execute(text("SELECT id FROM knowledge_scopes WHERE project_id = :project_id AND (CAST(id AS TEXT) = :scope_id OR scope_key = :scope_id)"), {"project_id": project.id, "scope_id": scope_id}).first()
    return row is not None

class AdminLoginRequest(BaseModel):
    username: str
    password: str

def create_admin_router(session_factory: sessionmaker[Session]) -> APIRouter:
    router = APIRouter(prefix="/api/admin/v1", tags=["admin-v1"])
    bearer = HTTPBearer(auto_error=False)

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
            if scope_id and hasattr(model, "scope") and scope_id not in {"default", "project"}:
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

    return router
