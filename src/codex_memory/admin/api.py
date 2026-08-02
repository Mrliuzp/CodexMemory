from __future__ import annotations

import base64
import json
import os
import re
import secrets
from datetime import datetime, timezone
from typing import Any, Callable
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile, status
from pydantic import BaseModel, Field
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import String, cast, false, func, inspect, or_, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from ..auth import PermissionDenied, ProjectAccessDenied, Principal, TokenAuthenticationError, authenticate_bearer, require_permission, require_project_access, issue_admin_session
from ..db_models import AuditLogRow, MemoryRow, MessageRow, ProjectRow
from ..v11_models import ImportBatchRow, ImportFileRow, ImportIssueRow, ImportUploadPartRow, MemoryCandidateRow, OutboxEventRow, ProcessingJobRow, ReferenceCandidateRow, RetrievalAuditRow
from ..persistence.v14_models import TaskEventRow, TaskFileChangeRow, TaskReportRow, TaskRunRow
from ..contract_revisions import ContractRevisionConflictError, ContractRevisionService
from ..api_operations import OpenAPIContractError
from ..api_operations import MAX_DOCUMENT_BYTES
from ..config import is_placeholder_value
from ..persistence.v15_models import ContractRevisionRow, ContractServiceRow

SORT_FIELDS = {
    "created_at",
    "updated_at",
    "started_at",
    "ended_at",
    "current_report_revision",
    "session_key",
    "id",
    "status",
    "project_key",
    "title",
}
SENSITIVE_KEY = re.compile(r"(?:api[_-]?key|authorization|bearer|password|secret|token|credential)", re.I)
# V1.5 当前没有稳定的用户身份字段，先以固定占位身份记录审计字段；后续接入身份系统时替换。
V15_ACTOR_PLACEHOLDER = "admin"
_CONTRACT_OPENAPI_ERROR_CODES = {
    "invalid_extension": "contract_invalid_file",
    "invalid_encoding": "contract_invalid_file",
    "invalid_syntax": "contract_invalid_file",
    "invalid_root": "contract_invalid_file",
    "document_too_large": "contract_file_too_large",
    "invalid_version": "contract_unsupported_version",
    "unsupported_version": "contract_unsupported_version",
    "lossy_normalization": "contract_profile_unsupported",
    "missing_operation_id": "contract_operation_id_invalid",
    "duplicate_operation_id": "contract_operation_id_invalid",
    "operation_id_changed": "contract_operation_id_conflict",
}

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


def _contract_openapi_error_code(code: str) -> str:
    return _CONTRACT_OPENAPI_ERROR_CODES.get(code, "contract_validation_failed")


def _contract_openapi_error_status(code: str) -> int:
    if code in {"invalid_extension", "invalid_encoding", "invalid_syntax", "invalid_root"}:
        return status.HTTP_400_BAD_REQUEST
    if code == "document_too_large":
        return status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
    if code == "operation_id_changed":
        return status.HTTP_409_CONFLICT
    return status.HTTP_422_UNPROCESSABLE_ENTITY


def _contract_revision_error_code(code: str) -> str:
    if code == "service_exists":
        return "contract_service_conflict"
    return "contract_revision_conflict"


def _contract_service_payload(
    detail: dict[str, Any],
    project_key: str | None,
    revision_sizes: dict[int, int] | None = None,
) -> dict[str, Any]:
    service = dict(detail.get("service") or {})
    revisions = []
    for value in detail.get("revisions") or []:
        revision = dict(value)
        if revision_sizes is not None and revision.get("id") in revision_sizes:
            revision["size_bytes"] = revision_sizes[revision["id"]]
        revisions.append(revision)
    current_id = service.get("current_published_revision_id")
    current = next((item for item in revisions if item.get("id") == current_id), None)
    service.update(
        {
            "project_key": project_key,
            "status": "published" if current_id is not None else ("proposed" if revisions else "empty"),
            "published_revision_number": current.get("revision_number") if current else None,
            "revisions": revisions,
        }
    )
    return service


def _contract_document_size(document: Any) -> int:
    if isinstance(document, bytes):
        return len(document)
    if isinstance(document, str):
        return len(document.encode("utf-8"))
    return len(json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8"))


def _contract_revision_payload(data: dict[str, Any]) -> dict[str, Any]:
    payload = dict(data)
    if payload.get("size_bytes") is None and payload.get("source_document") is not None:
        payload["size_bytes"] = _contract_document_size(payload["source_document"])
    return payload


def _require_v15_project_access(principal: Principal, project_key: str) -> None:
    if principal.project_key != "*" and principal.project_key != project_key:
        raise ProjectAccessDenied(f"令牌无权访问项目：{project_key}")

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


def _scope_display_name(scope_key: Any, name: Any, is_default: Any) -> str:
    value = str(name or '').strip()
    if value and not re.fullmatch(r"\?+", value):
        return value
    if bool(is_default) or str(scope_key) == "default":
        return "默认 Scope"
    return str(scope_key or "未命名 Scope")


def _utc_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _literal_like_pattern(value: str) -> str:
    escaped = value.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _task_prompt(event: TaskEventRow | None) -> tuple[str | None, bool]:
    if event is None:
        return None, False
    stored = event.payload_json if isinstance(event.payload_json, dict) else {}
    sources = [stored.get("payload"), stored.get("metadata"), event.metadata_json]
    prompt: str | None = None
    source_truncated = False
    for source in sources:
        if not isinstance(source, dict) or "prompt" not in source:
            continue
        value = source.get("prompt")
        if isinstance(value, dict):
            source_truncated = bool(value.get("truncated"))
            value = value.get("value")
        if isinstance(value, str):
            prompt = value
            break
    if prompt is None:
        return None, source_truncated
    compact = re.sub(r"\s+", " ", prompt).strip()
    return compact[:160], source_truncated or len(compact) > 160

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
        raise ValueError("content_base64 不是有效的 Base64") from error
    return "base64:" + base64.b64encode(raw).decode("ascii")


def _chunk_size(payload: ImportChunkPartRequest) -> int:
    if payload.content_base64 is not None:
        try:
            return len(base64.b64decode(payload.content_base64, validate=True))
        except (ValueError, base64.binascii.Error) as error:
            raise ValueError("content_base64 不是有效的 Base64") from error
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
    project_key: str
    session_key: str
    prompt_excerpt: str | None
    prompt_truncated: bool
    status: str
    started_at: str | None
    ended_at: str | None
    current_report_revision: int
    uncertain: bool | None


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
        session_secret = os.environ.get("CODEX_MEMORY_ADMIN_SESSION_SECRET", "")
        if is_placeholder_value(expected_password) or is_placeholder_value(session_secret):
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

    def project_context(request: Request, project_key: str, scope_id: str | None, current: Principal, *, access_error_code: str = "project_access_denied", strict_access: bool = False) -> ProjectRow:
        try:
            (_require_v15_project_access if strict_access else require_project_access)(current, project_key)
        except ProjectAccessDenied as error:
            raise _error(request, access_error_code, str(error), status.HTTP_403_FORBIDDEN) from error
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

    def query_rows(
        model: Any,
        project_id: int | None,
        page: int,
        page_size: int,
        sort: str,
        order: str,
        scope_id: str | None = None,
        *,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
        filters: dict[str, Any] | None = None,
        keyword: str | None = None,
        keyword_fields: tuple[str, ...] = (),
    ) -> tuple[list[Any], int]:
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
            created_column = getattr(model, "created_at", None)
            if created_column is None and (created_from is not None or created_to is not None):
                # 没有时间证据的记录不能被视为命中指定时间窗。
                query = query.where(false())
            elif created_column is not None:
                if created_from is not None:
                    query = query.where(created_column >= created_from)
                if created_to is not None:
                    query = query.where(created_column <= created_to)
            for field, value in (filters or {}).items():
                if value is None or (isinstance(value, str) and not value.strip()):
                    continue
                column = getattr(model, field, None)
                if column is not None:
                    if isinstance(value, (tuple, list, set, frozenset)):
                        query = query.where(column.in_(value))
                    else:
                        query = query.where(column == (value.strip() if isinstance(value, str) else value))
            if keyword and keyword.strip() and keyword_fields:
                pattern = _literal_like_pattern(keyword)
                keyword_conditions = [
                    cast(getattr(model, field), String).ilike(pattern, escape="\\")
                    for field in keyword_fields
                    if getattr(model, field, None) is not None
                ]
                if keyword_conditions:
                    query = query.where(or_(*keyword_conditions))
            total = int(session.scalar(select(func.count()).select_from(query.subquery())) or 0)
            column = getattr(model, sort, None)
            if column is None:
                column = getattr(model, "created_at", model.id)
            query = query.order_by(column.asc() if order == "asc" else column.desc(), model.id.desc())
            return session.scalars(query.offset((page - 1) * page_size).limit(page_size)).all(), total

    @router.get("/me")
    def me(request: Request, current: Principal = Depends(principal)) -> dict[str, Any]:
        return {
            "data": {
                "project_key": current.project_key,
                "permissions": sorted(current.permissions),
                "display_name": current.display_name,
                "auth_type": current.auth_type,
                "expires_at": _row_value(current, "expires_at"),
            },
            "request_id": _request_id(request),
        }

    @router.get("/dashboard")
    def dashboard(
        request: Request,
        project_key: str | None = None,
        current: Principal = Depends(principal),
    ) -> dict[str, Any]:
        resolved_project_key = project_key or (current.project_key if current.project_key != "*" else None)
        project_id = project_context(request, resolved_project_key, None, current, strict_access=True).id if resolved_project_key else None
        with session_factory() as session:
            inspector = inspect(session.bind)
            table_names = set(inspector.get_table_names())

            def count_rows(model: Any, *conditions: Any) -> int:
                if model.__tablename__ not in table_names:
                    return 0
                query = select(func.count()).select_from(model)
                if project_id is not None and hasattr(model, "project_id"):
                    query = query.where(model.project_id == project_id)
                if conditions:
                    query = query.where(*conditions)
                return int(session.scalar(query) or 0)

            counts: dict[str, int] = {}
            for name, model in (("raw_records", MessageRow), ("candidates", MemoryCandidateRow), ("memories", MemoryRow), ("jobs", ProcessingJobRow)):
                counts[name] = count_rows(model)

            uncertain_task_runs = 0
            if TaskReportRow.__tablename__ in table_names:
                uncertain_query = select(func.count(func.distinct(TaskReportRow.task_run_id))).where(TaskReportRow.uncertain.is_(True))
                if project_id is not None:
                    uncertain_query = uncertain_query.where(TaskReportRow.project_id == project_id)
                uncertain_task_runs = int(session.scalar(uncertain_query) or 0)

            audit_query = select(AuditLogRow)
            if project_id is not None:
                audit_query = audit_query.where(AuditLogRow.project_id == project_id)
            recent_audits = session.scalars(audit_query.order_by(AuditLogRow.created_at.desc(), AuditLogRow.id.desc()).limit(8)).all()
            data = {
                **counts,
                "project_key": resolved_project_key,
                "attention": {
                    "pending_candidates": count_rows(MemoryCandidateRow, MemoryCandidateRow.status.in_(("generated", "pending", "pending_review"))),
                    "failed_jobs": count_rows(ProcessingJobRow, ProcessingJobRow.status == "failed"),
                    "dead_letters": count_rows(OutboxEventRow, OutboxEventRow.status == "dead"),
                    "active_imports": count_rows(ImportBatchRow, ImportBatchRow.status.notin_(("completed", "failed", "cancelled", "rolled_back"))),
                    "uncertain_task_runs": uncertain_task_runs,
                    "proposed_revisions": count_rows(ContractRevisionRow, ContractRevisionRow.status == "proposed"),
                },
                "pipeline": {
                    "l0": counts["raw_records"],
                    "raw_records": counts["raw_records"],
                    "candidate": counts["candidates"],
                    "candidates": counts["candidates"],
                    "memory": counts["memories"],
                    "memories": counts["memories"],
                    "l1": count_rows(MemoryRow, MemoryRow.level == "L1"),
                    "l2": count_rows(MemoryRow, MemoryRow.level == "L2"),
                    "l3": count_rows(MemoryRow, MemoryRow.level == "L3"),
                },
                "recent_audit_events": [
                    {
                        "id": row.id,
                        "event_type": row.event_type,
                        "subject_type": row.subject_type,
                        "subject_id": row.subject_id,
                        "created_at": _row_value(row, "created_at"),
                    }
                    for row in recent_audits
                ],
            }
        return {"data": data, "request_id": _request_id(request)}

    @router.get("/projects")
    def projects(
        request: Request,
        status_filter: str | None = Query(default=None, alias="status"),
        keyword: str | None = None,
        current: Principal = Depends(principal),
        paging: tuple[int, int, str, str, str] = Depends(pagination),
    ) -> dict[str, Any]:
        page, page_size, sort, order, _ = paging
        with session_factory() as session:
            query = select(ProjectRow)
            if "admin" not in current.permissions:
                query = query.where(ProjectRow.project_key == current.project_key)
            if status_filter and status_filter.strip():
                query = query.where(ProjectRow.status == status_filter.strip())
            if keyword and keyword.strip():
                pattern = _literal_like_pattern(keyword)
                query = query.where(
                    or_(
                        ProjectRow.project_key.ilike(pattern, escape="\\"),
                        ProjectRow.name.ilike(pattern, escape="\\"),
                        ProjectRow.repository.ilike(pattern, escape="\\"),
                    )
                )
            total = int(session.scalar(select(func.count()).select_from(query.subquery())) or 0)
            column = getattr(ProjectRow, sort, ProjectRow.created_at)
            rows = session.scalars(query.order_by(column.asc() if order == "asc" else column.desc()).offset((page - 1) * page_size).limit(page_size)).all()
            has_scopes = inspect(session.bind).has_table("knowledge_scopes")
            scope_counts = {
                row.id: int(
                    session.execute(
                        text("SELECT COUNT(1) FROM knowledge_scopes WHERE project_id = :project_id"),
                        {"project_id": row.id},
                    ).scalar()
                    or 0
                )
                for row in rows
            } if has_scopes else {row.id: 0 for row in rows}
            return list_response(
                request,
                rows,
                total,
                page,
                page_size,
                lambda row: {
                    "id": row.id,
                    "project_key": row.project_key,
                    "name": row.name,
                    "repository": row.repository,
                    "status": row.status,
                    "scope_count": scope_counts[row.id],
                },
            )

    @router.get("/projects/{project_key}")
    def project_detail(project_key: str, request: Request, current: Principal = Depends(principal)) -> dict[str, Any]:
        project = project_context(request, project_key, None, current)
        with session_factory() as session:
            scope_count = int(
                session.execute(
                    text("SELECT COUNT(1) FROM knowledge_scopes WHERE project_id = :project_id"),
                    {"project_id": project.id},
                ).scalar()
                or 0
            ) if inspect(session.bind).has_table("knowledge_scopes") else 0
        return {"data": {"id": project.id, "project_key": project.project_key, "name": project.name, "repository": project.repository, "description": project.description, "status": project.status, "scope_count": scope_count}, "request_id": _request_id(request)}

    @router.post("/contract-services")
    def create_contract_service(payload: ContractServiceCreateRequest, request: Request, current: Principal = Depends(principal)) -> dict[str, Any]:
        try:
            require_permission(current, "admin")
        except PermissionDenied as error:
            raise _error(request, "permission_denied", str(error), status.HTTP_403_FORBIDDEN) from error
        project_key = (payload.project_key or (current.project_key if current.project_key != "*" else "")).strip()
        if not project_key:
            raise _error(request, "project_required", "必须指定项目键", status.HTTP_422_UNPROCESSABLE_ENTITY)
        project = project_context(request, project_key, None, current, access_error_code="permission_denied", strict_access=True)
        service_key = (payload.service_key or payload.key or payload.service_name or payload.name or "").strip()
        if not service_key:
            raise _error(request, "service_key_required", "必须指定服务标识或名称", status.HTTP_422_UNPROCESSABLE_ENTITY)
        try:
            row = contract_service.create_service(project.id, service_key, payload.name, payload.description)
        except ContractRevisionConflictError as error:
            raise _error(request, _contract_revision_error_code(error.code), str(error), status.HTTP_409_CONFLICT) from error
        return {"data": {"id": row.id, "project_id": row.project_id, "project_key": project.project_key, "service_key": row.service_key, "name": row.name, "description": row.description, "current_published_revision_id": row.current_published_revision_id, "status": "empty", "published_revision_number": None, "created_at": _row_value(row, "created_at"), "updated_at": _row_value(row, "updated_at"), "revisions": []}, "meta": {}, "request_id": _request_id(request)}

    @router.get("/contract-services")
    def list_contract_services(request: Request, project_key: str | None = None, status_filter: str | None = Query(default=None, alias="status"), keyword: str | None = None, current: Principal = Depends(principal), paging: tuple[int, int, str, str, str] = Depends(pagination)) -> dict[str, Any]:
        try:
            require_permission(current, "read")
        except PermissionDenied as error:
            raise _error(request, "permission_denied", str(error), status.HTTP_403_FORBIDDEN) from error
        if project_key:
            project_id = project_context(request, project_key, None, current, access_error_code="permission_denied", strict_access=True).id
        elif current.project_key == "*":
            project_id = None
        else:
            project_id = project_context(request, current.project_key, None, current, access_error_code="permission_denied", strict_access=True).id
        page, page_size, _, _, _ = paging
        if status_filter is not None and status_filter not in {"proposed", "published", "superseded"}:
            raise _error(request, "invalid_revision_status", "Revision 状态无效", status.HTTP_422_UNPROCESSABLE_ENTITY)
        rows = contract_service.list_services(project_id, status_filter, keyword)
        total = len(rows)
        page_rows = rows[(page - 1) * page_size: page * page_size]
        project_ids = {row.project_id for row in page_rows}
        service_ids = {row.id for row in page_rows}
        with session_factory() as session:
            project_keys = {
                row.id: row.project_key
                for row in session.scalars(select(ProjectRow).where(ProjectRow.id.in_(project_ids))).all()
            } if project_ids else {}
            revision_sizes = {
                revision_id: _contract_document_size(source_document)
                for revision_id, source_document in session.execute(
                    select(ContractRevisionRow.id, ContractRevisionRow.source_document).where(ContractRevisionRow.service_id.in_(service_ids))
                ).all()
            } if service_ids else {}
        items = []
        for row in page_rows:
            detail = contract_service.service_detail(row.id, row.project_id)
            items.append(_contract_service_payload(detail, project_keys.get(row.project_id), revision_sizes))
        return _page(items, total, page, page_size, _request_id(request))

    def _contract_service_project(request: Request, service_id: int, current: Principal, *, require_admin: bool = False) -> tuple[ContractServiceRow, int, str]:
        if require_admin:
            try:
                require_permission(current, "admin")
            except PermissionDenied as error:
                raise _error(request, "permission_denied", str(error), status.HTTP_403_FORBIDDEN) from error
        with session_factory() as session:
            row = session.get(ContractServiceRow, service_id)
            if row is None:
                raise _error(request, "contract_service_not_found", "服务不存在", status.HTTP_404_NOT_FOUND)
            project = session.get(ProjectRow, row.project_id)
            if project is None:
                raise _error(request, "project_not_found", "项目不存在", status.HTTP_404_NOT_FOUND)
            try:
                _require_v15_project_access(current, project.project_key)
            except ProjectAccessDenied as error:
                raise _error(request, "permission_denied", str(error), status.HTTP_403_FORBIDDEN) from error
            session.expunge(row)
            return row, project.id, project.project_key

    @router.get("/contract-services/{service_id}")
    def contract_service_detail(service_id: int, request: Request, current: Principal = Depends(principal)) -> dict[str, Any]:
        try:
            require_permission(current, "read")
        except PermissionDenied as error:
            raise _error(request, "permission_denied", str(error), status.HTTP_403_FORBIDDEN) from error
        row, project_id, project_key = _contract_service_project(request, service_id, current)
        try:
            detail = contract_service.service_detail(row.id, project_id)
        except LookupError as error:
            raise _error(request, "contract_service_not_found", str(error), status.HTTP_404_NOT_FOUND) from error
        with session_factory() as session:
            revision_sizes = {
                revision_id: _contract_document_size(source_document)
                for revision_id, source_document in session.execute(
                    select(ContractRevisionRow.id, ContractRevisionRow.source_document).where(ContractRevisionRow.service_id == row.id)
                ).all()
            }
        return {"data": _contract_service_payload(detail, project_key, revision_sizes), "meta": {}, "request_id": _request_id(request)}

    @router.post("/contract-services/{service_id}/revisions")
    def upload_contract_revision(service_id: int, request: Request, file: UploadFile = File(...), current: Principal = Depends(principal)) -> dict[str, Any]:
        row, project_id, _ = _contract_service_project(request, service_id, current, require_admin=True)
        filename = file.filename or ""
        try:
            content = file.file.read(MAX_DOCUMENT_BYTES + 1)
        finally:
            file.file.close()
        if not isinstance(content, bytes):
            content = bytes(content)
        try:
            revision, reused = contract_service.create_revision(row.id, filename, content, project_id, created_by=V15_ACTOR_PLACEHOLDER)
        except OpenAPIContractError as error:
            validation_code = str(error.errors[0].get("code", "invalid_openapi_document")) if error.errors else "invalid_openapi_document"
            raise _error(request, _contract_openapi_error_code(validation_code), str(error), _contract_openapi_error_status(validation_code), meta={"validation_errors": error.errors}) from error
        except ContractRevisionConflictError as error:
            raise _error(request, _contract_revision_error_code(error.code), str(error), status.HTTP_409_CONFLICT, meta=error.meta) from error
        except LookupError as error:
            raise _error(request, "contract_service_not_found", str(error), status.HTTP_404_NOT_FOUND) from error
        data = _contract_revision_payload(contract_service.get_revision(row.id, revision.revision_number, project_id))
        return {"data": data, "meta": {"reused": reused}, "request_id": _request_id(request)}

    @router.get("/contract-services/{service_id}/revisions/{revision_number}")
    def contract_revision_detail(service_id: int, revision_number: int, request: Request, current: Principal = Depends(principal)) -> dict[str, Any]:
        try:
            require_permission(current, "read")
        except PermissionDenied as error:
            raise _error(request, "permission_denied", str(error), status.HTTP_403_FORBIDDEN) from error
        row, project_id, _ = _contract_service_project(request, service_id, current)
        try:
            data = _contract_revision_payload(contract_service.get_revision(row.id, revision_number, project_id))
        except LookupError as error:
            raise _error(request, "contract_revision_not_found", str(error), status.HTTP_404_NOT_FOUND) from error
        return {"data": data, "meta": {}, "request_id": _request_id(request)}

    @router.post("/contract-services/{service_id}/revisions/{revision_number}/publish")
    def publish_contract_revision(service_id: int, revision_number: int, payload: ContractPublishRequest, request: Request, current: Principal = Depends(principal)) -> dict[str, Any]:
        row, project_id, _ = _contract_service_project(request, service_id, current, require_admin=True)
        try:
            revision, idempotent = contract_service.publish(row.id, revision_number, payload.expected_content_hash, project_id, published_by=V15_ACTOR_PLACEHOLDER)
        except OpenAPIContractError as error:
            validation_code = str(error.errors[0].get("code", "invalid_openapi_document")) if error.errors else "invalid_openapi_document"
            raise _error(request, _contract_openapi_error_code(validation_code), str(error), _contract_openapi_error_status(validation_code), meta={"validation_errors": error.errors}) from error
        except ContractRevisionConflictError as error:
            raise _error(request, _contract_revision_error_code(error.code), str(error), status.HTTP_409_CONFLICT, meta=error.meta) from error
        except LookupError as error:
            raise _error(request, "contract_revision_not_found", str(error), status.HTTP_404_NOT_FOUND) from error
        data = _contract_revision_payload(contract_service.get_revision(row.id, revision.revision_number, project_id))
        return {"data": data, "meta": {"idempotent": idempotent}, "request_id": _request_id(request)}

    @router.get("/projects/{project_key}/scopes")
    def scopes(project_key: str, request: Request, current: Principal = Depends(principal), paging: tuple[int, int, str, str, str] = Depends(pagination)) -> dict[str, Any]:
        project = project_context(request, project_key, None, current)
        page, page_size, _, _, _ = paging
        with session_factory() as session:
            if not inspect(session.bind).has_table("knowledge_scopes"):
                return _page([], 0, page, page_size, _request_id(request))
            rows = session.execute(text("SELECT id, scope_key, name, description, is_default, status, created_at, updated_at FROM knowledge_scopes WHERE project_id = :id ORDER BY id"), {"id": project.id}).mappings().all()
        items = [
            {
                **dict(row),
                "name": _scope_display_name(row["scope_key"], row["name"], row["is_default"]),
            }
            for row in rows
        ]
        return _page(items[(page - 1) * page_size: page * page_size], len(items), page, page_size, _request_id(request))

    @router.get("/scopes")
    def scopes_alias(request: Request, current: Principal = Depends(principal), paging: tuple[int, int, str, str, str] = Depends(pagination)) -> dict[str, Any]:
        return scopes(current.project_key, request, current, paging)
    def query_list(
        model: Any,
        mapper: Callable[[Any], dict[str, Any]],
        project_key: str | None,
        scope_id: str | None,
        request: Request,
        current: Principal,
        paging: tuple[int, int, str, str, str],
        *,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
        filters: dict[str, Any] | None = None,
        keyword: str | None = None,
        keyword_fields: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        project_id = None
        if project_key:
            project_id = project_context(request, project_key, scope_id, current).id
        elif "admin" not in current.permissions:
            project_id = project_context(request, current.project_key, scope_id, current).id
        page, page_size, sort, order, _ = paging
        created_from = _utc_datetime(created_from)
        created_to = _utc_datetime(created_to)
        if created_from is not None and created_to is not None and created_from > created_to:
            raise _error(request, "invalid_date_range", "开始时间不能晚于结束时间", status.HTTP_422_UNPROCESSABLE_ENTITY)
        try:
            rows, total = query_rows(
                model,
                project_id,
                page,
                page_size,
                sort,
                order,
                scope_id,
                created_from=created_from,
                created_to=created_to,
                filters=filters,
                keyword=keyword,
                keyword_fields=keyword_fields,
            )
        except OperationalError:
            rows, total = [], 0
        return list_response(request, rows, total, page, page_size, mapper)

    @router.get("/raw-records")
    def raw_records(
        request: Request,
        project_key: str | None = None,
        scope_id: str | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
        role: str | None = None,
        keyword: str | None = None,
        current: Principal = Depends(principal),
        paging: tuple[int, int, str, str, str] = Depends(pagination),
    ) -> dict[str, Any]:
        return query_list(
            MessageRow,
            lambda row: {"id": row.id, "project_id": row.project_id, "event_key": row.event_key, "role": row.role, "content": _redact(row.content), "source": row.source, "created_at": _row_value(row, "created_at")},
            project_key,
            scope_id,
            request,
            current,
            paging,
            created_from=created_from,
            created_to=created_to,
            filters={"role": role},
            keyword=keyword,
            keyword_fields=("event_key", "content", "source"),
        )

    @router.get("/candidates")
    def candidates(
        request: Request,
        project_key: str | None = None,
        scope_id: str | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
        status_filter: str | None = Query(default=None, alias="status"),
        level: str | None = None,
        memory_type: str | None = None,
        keyword: str | None = None,
        current: Principal = Depends(principal),
        paging: tuple[int, int, str, str, str] = Depends(pagination),
    ) -> dict[str, Any]:
        return query_list(
            MemoryCandidateRow,
            lambda row: {"id": row.id, "project_id": row.project_id, "level": row.level, "scope": row.scope, "memory_type": row.memory_type, "title": row.title, "content": _redact(row.content), "status": row.status, "abstain": row.abstain, "model_confidence": row.model_confidence, "published_memory_id": row.published_memory_id, "created_at": _row_value(row, "created_at")},
            project_key,
            scope_id,
            request,
            current,
            paging,
            created_from=created_from,
            created_to=created_to,
            filters={
                "status": ("generated", "pending", "pending_review") if status_filter == "pending" else status_filter,
                "level": level,
                "memory_type": memory_type,
            },
            keyword=keyword,
            keyword_fields=("title", "content"),
        )

    @router.get("/memories")
    def memories(
        request: Request,
        project_key: str | None = None,
        scope_id: str | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
        status_filter: str | None = Query(default=None, alias="status"),
        level: str | None = None,
        memory_type: str | None = None,
        keyword: str | None = None,
        current: Principal = Depends(principal),
        paging: tuple[int, int, str, str, str] = Depends(pagination),
    ) -> dict[str, Any]:
        return query_list(
            MemoryRow,
            lambda row: {"id": row.id, "project_id": row.project_id, "level": row.level, "memory_type": row.memory_type, "title": row.title, "content": _redact(row.content), "confidence": row.confidence, "status": row.status, "scope": row.scope, "created_at": _row_value(row, "created_at")},
            project_key,
            scope_id,
            request,
            current,
            paging,
            created_from=created_from,
            created_to=created_to,
            filters={"status": status_filter, "level": level, "memory_type": memory_type},
            keyword=keyword,
            keyword_fields=("title", "content"),
        )

    @router.get("/jobs")
    def jobs(
        request: Request,
        project_key: str | None = None,
        scope_id: str | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
        status_filter: str | None = Query(default=None, alias="status"),
        job_type: str | None = None,
        keyword: str | None = None,
        current: Principal = Depends(principal),
        paging: tuple[int, int, str, str, str] = Depends(pagination),
    ) -> dict[str, Any]:
        return query_list(
            ProcessingJobRow,
            lambda row: {"id": row.id, "project_id": row.project_id, "job_type": row.job_type, "job_key": row.job_key, "status": row.status, "attempt_count": row.attempt_count, "last_error_code": row.last_error_code, "created_at": _row_value(row, "created_at")},
            project_key,
            scope_id,
            request,
            current,
            paging,
            created_from=created_from,
            created_to=created_to,
            filters={"status": status_filter, "job_type": job_type},
            keyword=keyword,
            keyword_fields=("job_key", "source_id", "last_error_message"),
        )

    @router.get("/outbox-events")
    def outbox_events(
        request: Request,
        project_key: str | None = None,
        scope_id: str | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
        status_filter: str | None = Query(default=None, alias="status"),
        event_type: str | None = None,
        current: Principal = Depends(principal),
        paging: tuple[int, int, str, str, str] = Depends(pagination),
    ) -> dict[str, Any]:
        return query_list(
            OutboxEventRow,
            lambda row: {"id": row.id, "project_id": row.project_id, "event_type": row.event_type, "status": row.status, "attempt_count": row.attempt_count, "created_at": _row_value(row, "created_at")},
            project_key,
            scope_id,
            request,
            current,
            paging,
            created_from=created_from,
            created_to=created_to,
            filters={"status": status_filter, "event_type": event_type},
        )

    @router.get("/retrieval-audits")
    def retrieval_audits(
        request: Request,
        project_key: str | None = None,
        scope_id: str | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
        degraded: bool | None = None,
        retrieval_mode: str | None = None,
        current: Principal = Depends(principal),
        paging: tuple[int, int, str, str, str] = Depends(pagination),
    ) -> dict[str, Any]:
        return query_list(
            RetrievalAuditRow,
            lambda row: {"id": row.id, "project_id": row.project_id, "retrieval_mode": row.retrieval_mode, "degraded": row.degraded, "degraded_reason": row.degraded_reason, "latency_ms": row.latency_ms, "created_at": None},
            project_key,
            scope_id,
            request,
            current,
            paging,
            created_from=created_from,
            created_to=created_to,
            filters={"degraded": degraded, "retrieval_mode": retrieval_mode},
        )

    @router.get("/audit-events")
    def audit_events(
        request: Request,
        project_key: str | None = None,
        scope_id: str | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
        event_type: str | None = None,
        subject_type: str | None = None,
        current: Principal = Depends(principal),
        paging: tuple[int, int, str, str, str] = Depends(pagination),
    ) -> dict[str, Any]:
        return query_list(
            AuditLogRow,
            lambda row: {"id": row.id, "project_id": row.project_id, "event_type": row.event_type, "subject_type": row.subject_type, "subject_id": row.subject_id, "created_at": _row_value(row, "created_at")},
            project_key,
            scope_id,
            request,
            current,
            paging,
            created_from=created_from,
            created_to=created_to,
            filters={"event_type": event_type, "subject_type": subject_type},
        )



    @router.get("/import-batches")
    def import_batches(
        request: Request,
        project_key: str | None = None,
        scope_id: str | None = None,
        status_filter: str | None = Query(default=None, alias="status"),
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
            filters={
                "status": (
                    "pending", "queued", "uploading", "running", "processing", "awaiting_review"
                ) if status_filter == "active" else status_filter,
            },
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
            rows = session.scalars(select(ImportUploadPartRow).where(ImportUploadPartRow.import_batch_id == batch_id, ImportUploadPartRow.upload_id == upload_id).order_by(ImportUploadPartRow.part_number)).all()
            if not rows:
                raise _error(request, "upload_not_found", "分片上传不存在", status.HTTP_404_NOT_FOUND)
            return {"data": {"upload_id": upload_id, "source_name": rows[0].source_name, "source_type": rows[0].source_type, "total_parts": rows[0].total_parts, "uploaded_parts": [row.part_number for row in rows if row.status in {"uploaded", "completed"}], "status": "completed" if all(row.status == "completed" for row in rows) else "uploading"}, "request_id": _request_id(request)}

    @router.put("/import-batches/{batch_id}/uploads/{upload_id}/parts/{part_number}")
    def put_import_upload_part(batch_id: int, upload_id: str, part_number: int, payload: ImportChunkPartRequest, request: Request, current: Principal = Depends(principal)) -> dict[str, Any]:
        require_permission(current, "admin")
        try:
            size = _chunk_size(payload)
        except ValueError as error:
            raise _error(request, "invalid_import_content", str(error), status.HTTP_422_UNPROCESSABLE_ENTITY) from error
        if size > 4 * 1024 * 1024:
            raise _error(request, "import_part_too_large", "单个导入分片不能超过 4 MiB", status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)
        from ..pipelines.v131_import import KnowledgeImportService
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
            part = session.scalar(select(ImportUploadPartRow).where(ImportUploadPartRow.import_batch_id == batch_id, ImportUploadPartRow.upload_id == upload_id).order_by(ImportUploadPartRow.part_number))
            if part is None:
                if payload.total_parts is None or not payload.source_name:
                    raise _error(request, "upload_not_found", "分片上传不存在，请先初始化上传", status.HTTP_404_NOT_FOUND)
                source_name, source_type, total_parts = payload.source_name, payload.source_type, payload.total_parts
            else:
                source_name, source_type, total_parts = part.source_name, part.source_type, part.total_parts
        try:
            result = KnowledgeImportService(session_factory).put_upload_part(batch_id, upload_id, part_number, total_parts, source_name, source_type, _chunk_content(payload))
        except (LookupError, ValueError) as error:
            raise _error(request, "import_part_failed", str(error), status.HTTP_422_UNPROCESSABLE_ENTITY) from error
        return {"data": result, "request_id": _request_id(request)}

    @router.post(
        "/import-batches/{batch_id}/uploads/{upload_id}:complete",
        operation_id="complete_import_upload_colon_alias",
    )
    @router.post("/import-batches/{batch_id}/uploads/{upload_id}/complete")
    def complete_import_upload(batch_id: int, upload_id: str, request: Request, current: Principal = Depends(principal)) -> dict[str, Any]:
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
            result = KnowledgeImportService(session_factory).complete_upload(batch_id, upload_id)
        except (LookupError, ValueError) as error:
            raise _error(request, "import_upload_complete_failed", str(error), status.HTTP_422_UNPROCESSABLE_ENTITY) from error
        write_import_audit(project.project_key, "import.upload.completed", "import_batch", str(batch_id), {"upload_id": upload_id, "request_id": _request_id(request)})
        return {"data": result, "request_id": _request_id(request)}

    @router.post(
        "/import-batches/{batch_id}:start",
        operation_id="start_import_batch_colon_alias",
    )
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

    @router.post(
        "/import-batches/{batch_id}:retry",
        operation_id="retry_import_batch_colon_alias",
    )
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

    @router.post(
        "/import-batches/{batch_id}:cancel",
        operation_id="cancel_import_batch_colon_alias",
    )
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
        status_filter: str | None = Query(default=None, alias="status"),
        uncertain: bool | None = None,
        keyword: str | None = None,
        started_from: datetime | None = None,
        started_to: datetime | None = None,
        current: Principal = Depends(principal),
        paging: tuple[int, int, str, str, str] = Depends(pagination),
    ) -> dict[str, Any]:
        page, page_size, sort, order, request_id = paging
        project_id: int | None = None
        if project_key:
            project_id = project_context(request, project_key, None, current).id
        elif "admin" not in current.permissions:
            project_id = project_context(request, current.project_key, None, current).id
        started_from = _utc_datetime(started_from)
        started_to = _utc_datetime(started_to)
        if started_from is not None and started_to is not None and started_from > started_to:
            raise _error(request, "invalid_date_range", "开始时间不能晚于结束时间", status.HTTP_422_UNPROCESSABLE_ENTITY)
        with session_factory() as session:
            query = select(TaskRunRow)
            if project_id is not None:
                query = query.where(TaskRunRow.project_id == project_id)
            if status_filter and status_filter.strip():
                query = query.where(TaskRunRow.status == status_filter.strip())
            if uncertain is not None:
                latest_uncertain = (
                    select(TaskReportRow.uncertain)
                    .where(TaskReportRow.task_run_id == TaskRunRow.id)
                    .order_by(TaskReportRow.revision.desc(), TaskReportRow.id.desc())
                    .limit(1)
                    .scalar_subquery()
                )
                query = query.where(latest_uncertain.is_(uncertain))
            if started_from is not None:
                query = query.where(TaskRunRow.started_at >= started_from)
            if started_to is not None:
                query = query.where(TaskRunRow.started_at <= started_to)
            if keyword and keyword.strip():
                pattern = _literal_like_pattern(keyword)
                prompt_values = (
                    TaskEventRow.payload_json["payload"]["prompt"]["value"].as_string(),
                    TaskEventRow.payload_json["payload"]["prompt"].as_string(),
                    TaskEventRow.payload_json["metadata"]["prompt"]["value"].as_string(),
                    TaskEventRow.payload_json["metadata"]["prompt"].as_string(),
                    TaskEventRow.metadata_json["prompt"]["value"].as_string(),
                    TaskEventRow.metadata_json["prompt"].as_string(),
                )
                prompt_match = select(TaskEventRow.id).where(
                    TaskEventRow.task_run_id == TaskRunRow.id,
                    TaskEventRow.event_type == "UserPromptSubmit",
                    or_(*(value.ilike(pattern, escape="\\") for value in prompt_values)),
                ).exists()
                query = query.where(or_(TaskRunRow.session_key.ilike(pattern, escape="\\"), prompt_match))
            total = int(session.scalar(select(func.count()).select_from(query.subquery())) or 0)
            sort_column = getattr(TaskRunRow, sort, None)
            if sort_column is None:
                sort_column = TaskRunRow.created_at
            rows = session.scalars(
                query.order_by(sort_column.asc() if order == "asc" else sort_column.desc(), TaskRunRow.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            ).all()
            run_ids = [row.id for row in rows]
            project_ids = {row.project_id for row in rows}
            project_keys = {
                row.id: row.project_key
                for row in session.scalars(select(ProjectRow).where(ProjectRow.id.in_(project_ids))).all()
            } if project_ids else {}
            prompt_events: dict[int, TaskEventRow] = {}
            latest_reports: dict[int, TaskReportRow] = {}
            if run_ids:
                for event in session.scalars(
                    select(TaskEventRow)
                    .where(TaskEventRow.task_run_id.in_(run_ids), TaskEventRow.event_type == "UserPromptSubmit")
                    .order_by(TaskEventRow.task_run_id, TaskEventRow.sequence_no, TaskEventRow.id)
                ).all():
                    prompt_events.setdefault(event.task_run_id, event)
                for report in session.scalars(
                    select(TaskReportRow)
                    .where(TaskReportRow.task_run_id.in_(run_ids))
                    .order_by(TaskReportRow.task_run_id, TaskReportRow.revision.desc(), TaskReportRow.id.desc())
                ).all():
                    latest_reports.setdefault(report.task_run_id, report)
            prompt_summaries = {run_id: _task_prompt(event) for run_id, event in prompt_events.items()}
            data = [
                TaskRunListItem(
                    id=row.id,
                    project_id=row.project_id,
                    project_key=project_keys.get(row.project_id, ""),
                    session_key=row.session_key,
                    prompt_excerpt=prompt_summaries.get(row.id, (None, False))[0],
                    prompt_truncated=prompt_summaries.get(row.id, (None, False))[1],
                    status=row.status,
                    started_at=_row_value(row, "started_at"),
                    ended_at=_row_value(row, "ended_at"),
                    current_report_revision=row.current_report_revision,
                    uncertain=latest_reports[row.id].uncertain if row.id in latest_reports else None,
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
            require_permission(current, "operations_read")
        except PermissionDenied as error:
            raise _error(request, "permission_denied", str(error), status.HTTP_403_FORBIDDEN) from error
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
