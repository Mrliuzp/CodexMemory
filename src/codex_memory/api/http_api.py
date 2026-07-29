from __future__ import annotations

from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from pydantic import BaseModel, Field

from .v1_schemas import (
    AppendV1Request,
    AppendV1Response,
    ContextV1Request,
    MemoryV1Request,
    ReflectV1Request,
    SearchV1Request,
    TaskEventV14Request,
)


class KnowledgeImportItemRequest(BaseModel):
    source_name: str
    content: str
    source_type: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class KnowledgeImportRequest(BaseModel):
    project_key: str
    items: list[KnowledgeImportItemRequest] = Field(min_length=1, max_length=100)
    metadata: dict[str, Any] = Field(default_factory=dict)


def create_v1_app(session_factory: Any) -> FastAPI:
    from .auth import (
        PermissionDenied,
        ProjectAccessDenied,
        TokenAuthenticationError,
        authenticate_bearer,
        require_permission,
        require_project_access,
    )
    from .v1_schemas import (
        AppendV1Request,
        AppendV1Response,
        ContextV1Request,
        MemoryV1Request,
        ReflectV1Request,
        SearchV1Request,
        TaskEventV14Request,
    )
    from .v1_service import AppendConflictError, V1MemoryService
    from ..v14_service import TaskEventConflictError, TaskEventService, TaskEventValidationError
    from .v11_embedding import EmbeddingProfileService
    from .v11_flags import ProjectPolicyService

    service = V1MemoryService(session_factory)
    task_event_service = TaskEventService(session_factory)
    app = FastAPI(title="Codex Memory V1 API", version="1.0.0")
    from .admin import create_admin_router

    app.include_router(create_admin_router(session_factory))
    from .admin.api import AdminAPIError

    @app.exception_handler(AdminAPIError)
    async def admin_error_handler(request: Request, exc: AdminAPIError) -> JSONResponse:
        headers = {"X-Request-ID": exc.request_id, **exc.headers}
        return JSONResponse(status_code=exc.status_code, content={"error": {"code": exc.code, "message": exc.message, "request_id": exc.request_id}}, headers=headers)
    bearer = HTTPBearer(auto_error=False)

    def current_principal(
        credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    ) -> Any:
        if credentials is None or credentials.scheme.lower() != "bearer":
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="bearer token required")
        try:
            return authenticate_bearer(session_factory, credentials.credentials)
        except TokenAuthenticationError as error:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(error)) from error

    def enforce(principal: Any, project_key: str, permission: str) -> None:
        try:
            require_project_access(principal, project_key)
            require_permission(principal, permission)
        except (ProjectAccessDenied, PermissionDenied) as error:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error)) from error

    @app.post("/api/v1/append", response_model=AppendV1Response)
    def append_v1(payload: AppendV1Request, principal: Any = Depends(current_principal)) -> dict[str, Any]:
        try:
            result = service.append_message_v11(
                principal,
                payload.project_key,
                payload.session_key,
                payload.event_key,
                payload.role,
                payload.content,
                payload.occurred_at,
                source=payload.source,
                metadata=payload.metadata,
            )
        except AppendConflictError as error:
            return JSONResponse(
                status_code=status.HTTP_409_CONFLICT,
                content={"error": "event_key_conflict", "audit_id": error.audit_id},
            )
        except (ProjectAccessDenied, PermissionDenied) as error:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error)) from error
        if result.is_v11:
            return JSONResponse(
                status_code=status.HTTP_201_CREATED if result.status == "accepted" else status.HTTP_200_OK,
                content={
                    "id": result.message_id,
                    "status": result.status,
                    "message_id": result.message_id,
                    "event_id": result.event_id,
                },
            )
        return JSONResponse(status_code=status.HTTP_200_OK, content={"id": result.message_id, "status": result.status})

    @app.post("/api/v1/task-events")
    def append_task_event(payload: TaskEventV14Request, principal: Any = Depends(current_principal)) -> JSONResponse:
        try:
            result = task_event_service.append_event(
                principal,
                project_key=payload.project_key,
                session_key=payload.session_key,
                event_key=payload.event_key,
                event_type=payload.event_type,
                occurred_at=payload.occurred_at,
                payload=payload.payload,
                metadata=payload.metadata,
                command_summary=payload.command_summary,
                result_summary=payload.result_summary,
                exit_code=payload.exit_code,
                git=payload.git,
            )
        except TaskEventConflictError as error:
            return JSONResponse(status_code=status.HTTP_409_CONFLICT, content={"error": "event_key_conflict", "event_id": error.event_id})
        except TaskEventValidationError as error:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)) from error
        except LookupError as error:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
        except (ProjectAccessDenied, PermissionDenied) as error:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error)) from error
        return JSONResponse(status_code=status.HTTP_201_CREATED if result["status"] == "accepted" else status.HTTP_200_OK, content=result)

    @app.post("/api/v1/memory")
    def create_memory_v1(payload: MemoryV1Request, principal: Any = Depends(current_principal)) -> dict[str, Any]:
        try:
            memory = service.create_l1_memory(principal, payload.project_key, payload.type, payload.content, payload.title)
        except (ProjectAccessDenied, PermissionDenied) as error:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error)) from error
        return {"id": memory.id, "level": memory.level, "status": memory.status}

    @app.post("/api/v1/context")
    def context_v1(payload: ContextV1Request, principal: Any = Depends(current_principal)) -> dict[str, Any]:
        try:
            if service.v11_schema_available():
                return service.build_context_v11(
                    principal,
                    payload.project_key,
                    payload.task,
                    scope_mode=payload.scope_mode,
                    layers=payload.layers or None,
                    memory_types=payload.memory_types or None,
                    limit=payload.limit,
                    context_budget_tokens=payload.context_budget_tokens,
                )
            return service.build_context(principal, payload.project_key, payload.task, payload.limit)
        except (ProjectAccessDenied, PermissionDenied) as error:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error)) from error

    @app.post("/api/v1/search")
    def search_v1(payload: SearchV1Request, principal: Any = Depends(current_principal)) -> dict[str, Any]:
        try:
            if service.v11_schema_available():
                return service.search_memories_v11(
                    principal,
                    payload.project_key,
                    payload.query,
                    scope_mode=payload.scope_mode,
                    layers=payload.layers or None,
                    memory_types=payload.memory_types or None,
                    limit=payload.limit,
                    include_audit=payload.include_audit,
                )
            return {"results": service.search_memories(principal, payload.project_key, payload.query, payload.limit)}
        except (ProjectAccessDenied, PermissionDenied) as error:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error)) from error

    @app.post("/api/v1/reflect")
    def reflect_v1(payload: ReflectV1Request, principal: Any = Depends(current_principal)) -> dict[str, Any]:
        try:
            return service.reflect_project(principal, payload.project_key)
        except (ProjectAccessDenied, PermissionDenied) as error:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error)) from error

    @app.post("/api/v1/admin/projects/{project_key}/flags")
    def update_flags(project_key: str, payload: dict[str, bool], principal: Any = Depends(current_principal)) -> dict[str, Any]:
        enforce(principal, project_key, "admin")
        from sqlalchemy import select
        from .db_models import ProjectRow

        with session_factory() as session:
            project = session.scalar(select(ProjectRow).where(ProjectRow.project_key == project_key))
        if project is None:
            raise HTTPException(status_code=404, detail="项目不存在")
        try:
            flags = ProjectPolicyService(session_factory).update_flags(project.id, **payload)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return {"project_id": project.id, "flags": {name: getattr(flags, name) for name in (
            "memory_v11_enabled",
            "server_outbox_enabled",
            "lexical_retrieval_enabled",
            "dense_retrieval_enabled",
            "embedding_profile_v2_enabled",
            "llm_shadow_enabled",
            "candidate_publish_enabled",
            "async_pipeline_v13_enabled",
        )}}

    @app.post("/api/v1/admin/profiles")
    def create_profile(payload: dict[str, Any], principal: Any = Depends(current_principal)) -> dict[str, Any]:
        require_permission(principal, "admin")
        try:
            profile = EmbeddingProfileService(session_factory).create_profile(
                name=payload["name"],
                provider=payload["provider"],
                model=payload["model"],
                dimension=int(payload["dimension"]),
                chunker_version=payload.get("chunker_version", "v1"),
                content_normalization_version=payload.get("content_normalization_version", "v1"),
                normalization=payload.get("normalization", "l2"),
            )
        except (KeyError, ValueError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return {"id": profile.id, "name": profile.name, "dimension": profile.dimension, "status": profile.status}

    @app.post("/api/v1/admin/projects/{project_key}/profile")
    def activate_profile(project_key: str, payload: dict[str, int], principal: Any = Depends(current_principal)) -> dict[str, Any]:
        enforce(principal, project_key, "admin")
        from sqlalchemy import select
        from .db_models import ProjectRow

        with session_factory() as session:
            project = session.scalar(select(ProjectRow).where(ProjectRow.project_key == project_key))
        if project is None:
            raise HTTPException(status_code=404, detail="项目不存在")
        try:
            setting = ProjectPolicyService(session_factory).set_active_profile(project.id, int(payload["profile_id"]))
        except (KeyError, ValueError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return {"project_id": project.id, "active_embedding_profile_id": setting.active_embedding_profile_id}

    @app.post("/api/v1/admin/profiles/{profile_id}/backfill")
    def backfill_profile(profile_id: int, payload: dict[str, int], principal: Any = Depends(current_principal)) -> dict[str, Any]:
        require_permission(principal, "admin")
        try:
            rows = EmbeddingProfileService(session_factory).backfill_memory(
                int(payload["project_id"]),
                int(payload["memory_id"]),
                profile_id,
            )
        except (KeyError, LookupError, ValueError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return {"profile_id": profile_id, "vector_ids": [row.id for row in rows], "status": "completed"}
    @app.get("/api/v1/admin/jobs")
    def list_admin_jobs(
        project_key: str,
        job_status: str | None = Query(default=None, alias="status"),
        limit: int = Query(default=50, ge=1, le=200),
        principal: Any = Depends(current_principal),
    ) -> dict[str, Any]:
        enforce(principal, project_key, "admin")
        from sqlalchemy import select
        from .db_models import JobAttemptRow, ProcessingJobRow, ProjectRow

        with session_factory() as session:
            project = session.scalar(select(ProjectRow).where(ProjectRow.project_key == project_key))
            if project is None:
                raise HTTPException(status_code=404, detail="项目不存在")
            query = select(ProcessingJobRow).where(ProcessingJobRow.project_id == project.id)
            if job_status:
                query = query.where(ProcessingJobRow.status == job_status)
            jobs = session.scalars(query.order_by(ProcessingJobRow.created_at, ProcessingJobRow.id).limit(limit)).all()
            payload = []
            for job in jobs:
                attempts = session.scalars(
                    select(JobAttemptRow).where(JobAttemptRow.job_id == job.id).order_by(JobAttemptRow.attempt_no)
                ).all()
                payload.append(
                    {
                        "id": job.id,
                        "job_key": job.job_key,
                        "job_type": job.job_type,
                        "status": job.status,
                        "attempt_count": job.attempt_count,
                        "last_error_code": job.last_error_code,
                        "attempts": [
                            {"attempt_no": item.attempt_no, "worker_id": item.worker_id, "outcome": item.outcome}
                            for item in attempts
                        ],
                    }
                )
            return {"jobs": payload}

    @app.post("/api/v1/admin/jobs/{job_id}/retry")
    def retry_admin_job(job_id: int, principal: Any = Depends(current_principal)) -> dict[str, Any]:
        require_permission(principal, "admin")
        from datetime import datetime, timezone
        from .db_models import ProcessingJobRow, ProjectRow, SecurityAuditRow

        with session_factory() as session:
            job = session.get(ProcessingJobRow, job_id)
            if job is None:
                raise HTTPException(status_code=404, detail="任务不存在")
            project = session.get(ProjectRow, job.project_id)
            if project is None:
                raise HTTPException(status_code=404, detail="项目不存在")
            try:
                require_project_access(principal, project.project_key)
            except ProjectAccessDenied as error:
                raise HTTPException(status_code=403, detail=str(error)) from error
            if job.status not in {"dead", "retry_wait"}:
                raise HTTPException(status_code=409, detail="任务不可重试")
            job.status = "pending"
            job.next_attempt_at = datetime.now(timezone.utc).replace(tzinfo=None)
            job.last_error_code = None
            job.last_error_message = None
            job.locked_by = None
            job.locked_at = None
            job.heartbeat_at = None
            job.lease_expires_at = None
            session.add(
                SecurityAuditRow(
                    project_id=job.project_id,
                    event_type="job_retried",
                    subject_type="job",
                    subject_id=str(job.id),
                    reason_code="admin_retry",
                    metadata_json={"job_key": job.job_key},
                )
            )
            session.commit()
            return {"id": job.id, "status": job.status}

    @app.post("/api/v1/admin/jobs/{job_id}/cancel")
    def cancel_admin_job(job_id: int, payload: dict[str, str] | None = None, principal: Any = Depends(current_principal)) -> dict[str, Any]:
        require_permission(principal, "admin")
        from datetime import datetime, timezone
        from .db_models import OutboxEventRow, ProcessingJobRow, ProjectRow, SecurityAuditRow

        reason = str((payload or {}).get("reason", "")).strip()
        if not reason:
            raise HTTPException(status_code=422, detail="reason 不能为空")
        with session_factory() as session:
            job = session.get(ProcessingJobRow, job_id)
            if job is None:
                raise HTTPException(status_code=404, detail="任务不存在")
            project = session.get(ProjectRow, job.project_id)
            if project is None:
                raise HTTPException(status_code=404, detail="项目不存在")
            enforce(principal, project.project_key, "admin")
            if job.status in {"succeeded", "dead", "cancelled"}:
                raise HTTPException(status_code=409, detail="任务不可取消")
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            job.status = "cancelled"
            job.cancelled_at = now
            job.cancel_reason = reason
            job.locked_by = None
            job.locked_at = None
            job.heartbeat_at = None
            job.lease_expires_at = None
            if job.outbox_event_id is not None:
                event = session.get(OutboxEventRow, job.outbox_event_id)
                if event is not None:
                    event.status = "dead"
                    event.last_error_code = "cancelled"
                    event.last_error_message = reason
                    event.locked_by = None
                    event.locked_at = None
                    event.lease_expires_at = None
            session.add(
                SecurityAuditRow(
                    project_id=job.project_id,
                    event_type="job_cancelled",
                    subject_type="job",
                    subject_id=str(job.id),
                    reason_code="admin_cancel",
                    metadata_json={"reason": reason, "job_key": job.job_key},
                )
            )
            session.commit()
            return {"id": job.id, "status": job.status}

    @app.post("/api/v1/admin/jobs/{job_id}/replay")
    def replay_admin_job(job_id: int, payload: dict[str, str] | None = None, principal: Any = Depends(current_principal)) -> dict[str, Any]:
        require_permission(principal, "admin")
        from datetime import datetime, timezone
        from .db_models import OutboxEventRow, ProcessingJobRow, ProjectRow, SecurityAuditRow

        reason = str((payload or {}).get("reason", "")).strip()
        if not reason:
            raise HTTPException(status_code=422, detail="reason 不能为空")
        with session_factory() as session:
            job = session.get(ProcessingJobRow, job_id)
            if job is None:
                raise HTTPException(status_code=404, detail="任务不存在")
            project = session.get(ProjectRow, job.project_id)
            if project is None:
                raise HTTPException(status_code=404, detail="项目不存在")
            enforce(principal, project.project_key, "admin")
            if job.status not in {"dead", "retry_wait", "cancelled"}:
                raise HTTPException(status_code=409, detail="任务不可回放")
            before = job.status
            job.status = "pending"
            job.next_attempt_at = datetime.now(timezone.utc).replace(tzinfo=None)
            job.last_error_code = None
            job.last_error_message = None
            job.error_class = None
            job.cancelled_at = None
            job.cancel_reason = None
            job.locked_by = None
            job.locked_at = None
            job.heartbeat_at = None
            job.lease_expires_at = None
            if job.outbox_event_id is not None:
                event = session.get(OutboxEventRow, job.outbox_event_id)
                if event is not None:
                    event.status = "pending"
                    event.replay_count = (event.replay_count or 0) + 1
                    event.next_attempt_at = job.next_attempt_at
                    event.last_error_code = None
                    event.last_error_message = None
                    event.locked_by = None
                    event.locked_at = None
                    event.lease_expires_at = None
            session.add(
                SecurityAuditRow(
                    project_id=job.project_id,
                    event_type="job_replayed",
                    subject_type="job",
                    subject_id=str(job.id),
                    reason_code="admin_replay",
                    metadata_json={"reason": reason, "before_status": before, "after_status": job.status},
                )
            )
            session.commit()
            return {"id": job.id, "status": job.status, "replayed": True}

    @app.post("/api/v1/admin/jobs/reset-stale")
    def reset_stale_admin_jobs(principal: Any = Depends(current_principal)) -> dict[str, Any]:
        require_permission(principal, "admin")
        from .v11_worker import V11JobWorker

        return {"reset": V11JobWorker(session_factory).sweep_expired()}

    @app.get("/api/v1/admin/outbox")
    def list_admin_outbox(
        project_key: str,
        event_status: str | None = Query(default=None, alias="status"),
        limit: int = Query(default=50, ge=1, le=200),
        principal: Any = Depends(current_principal),
    ) -> dict[str, Any]:
        enforce(principal, project_key, "admin")
        from sqlalchemy import select
        from .db_models import OutboxEventRow, ProjectRow

        with session_factory() as session:
            project = session.scalar(select(ProjectRow).where(ProjectRow.project_key == project_key))
            if project is None:
                raise HTTPException(status_code=404, detail="项目不存在")
            query = select(OutboxEventRow).where(OutboxEventRow.project_id == project.id)
            if event_status:
                query = query.where(OutboxEventRow.status == event_status)
            rows = session.scalars(query.order_by(OutboxEventRow.created_at, OutboxEventRow.id).limit(limit)).all()
            return {
                "outbox": [
                    {
                        "id": row.id,
                        "event_type": row.event_type,
                        "aggregate_type": row.aggregate_type,
                        "aggregate_id": row.aggregate_id,
                        "idempotency_key": row.idempotency_key,
                        "status": row.status,
                        "attempt_count": row.attempt_count,
                        "replay_count": row.replay_count,
                        "last_error_code": row.last_error_code,
                    }
                    for row in rows
                ]
            }

    @app.post("/api/v1/admin/outbox/{event_id}/replay")
    def replay_admin_outbox(event_id: int, payload: dict[str, str] | None = None, principal: Any = Depends(current_principal)) -> dict[str, Any]:
        require_permission(principal, "admin")
        from datetime import datetime, timezone
        from sqlalchemy import select
        from .db_models import OutboxEventRow, ProcessingJobRow, ProjectRow, SecurityAuditRow

        reason = str((payload or {}).get("reason", "")).strip()
        if not reason:
            raise HTTPException(status_code=422, detail="reason 不能为空")
        with session_factory() as session:
            event = session.get(OutboxEventRow, event_id)
            if event is None:
                raise HTTPException(status_code=404, detail="Outbox 事件不存在")
            project = session.get(ProjectRow, event.project_id)
            if project is None:
                raise HTTPException(status_code=404, detail="项目不存在")
            enforce(principal, project.project_key, "admin")
            if event.status not in {"dead", "retry_wait", "dispatched"}:
                raise HTTPException(status_code=409, detail="Outbox 事件不可回放")
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            event.status = "pending"
            event.replay_count = (event.replay_count or 0) + 1
            event.next_attempt_at = now
            event.last_error_code = None
            event.last_error_message = None
            event.locked_by = None
            event.locked_at = None
            event.lease_expires_at = None
            job = session.scalar(select(ProcessingJobRow).where(ProcessingJobRow.outbox_event_id == event.id))
            if job is not None and job.status in {"dead", "retry_wait", "cancelled"}:
                job.status = "pending"
                job.next_attempt_at = now
                job.locked_by = None
                job.locked_at = None
                job.heartbeat_at = None
                job.lease_expires_at = None
            session.add(
                SecurityAuditRow(
                    project_id=event.project_id,
                    event_type="outbox_replayed",
                    subject_type="outbox",
                    subject_id=str(event.id),
                    reason_code="admin_replay",
                    metadata_json={"reason": reason},
                )
            )
            session.commit()
            return {"id": event.id, "status": event.status, "replayed": True}

    @app.get("/api/v1/admin/candidates")
    def list_admin_candidates(
        project_key: str,
        candidate_status: str | None = Query(default=None, alias="status"),
        include_shadow: bool = False,
        limit: int = Query(default=50, ge=1, le=200),
        principal: Any = Depends(current_principal),
    ) -> dict[str, Any]:
        enforce(principal, project_key, "admin")
        from sqlalchemy import select
        from .db_models import MemoryCandidateRow, ProjectRow

        with session_factory() as session:
            project = session.scalar(select(ProjectRow).where(ProjectRow.project_key == project_key))
            if project is None:
                raise HTTPException(status_code=404, detail="项目不存在")
            query = select(MemoryCandidateRow).where(MemoryCandidateRow.project_id == project.id)
            if candidate_status:
                query = query.where(MemoryCandidateRow.status == candidate_status)
            elif not include_shadow:
                query = query.where(MemoryCandidateRow.status != "shadow")
            rows = session.scalars(query.order_by(MemoryCandidateRow.created_at, MemoryCandidateRow.id).limit(limit)).all()
            return {
                "candidates": [
                    {
                        "id": row.id,
                        "status": row.status,
                        "level": row.level,
                        "scope": row.scope,
                        "memory_type": row.memory_type,
                        "title": row.title,
                        "content": row.content,
                        "abstain": row.abstain,
                        "published_memory_id": row.published_memory_id,
                    }
                    for row in rows
                ]
            }

    @app.post("/api/v1/admin/import")
    def import_knowledge(payload: KnowledgeImportRequest, principal: Any = Depends(current_principal)) -> dict[str, Any]:
        enforce(principal, payload.project_key, "admin")
        from .v131_import import ImportItem, KnowledgeImportService

        try:
            result = KnowledgeImportService(session_factory).import_items(
                payload.project_key,
                [ImportItem(item.source_name, item.content, item.source_type, item.metadata) for item in payload.items],
                payload.metadata,
            )
        except (LookupError, ValueError, FileNotFoundError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return result.__dict__

    @app.get("/api/v1/reference/search")
    def search_reference(project_key: str, query: str, limit: int = Query(default=8, ge=1, le=50), principal: Any = Depends(current_principal)) -> dict[str, Any]:
        enforce(principal, project_key, "read")
        from .v131_import import KnowledgeImportService

        try:
            return {"results": KnowledgeImportService(session_factory).search_reference(project_key, query, limit)}
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.get("/api/v1/admin/reference-candidates")
    def list_reference_candidates(
        project_key: str,
        candidate_status: str | None = Query(default=None, alias="status"),
        limit: int = Query(default=50, ge=1, le=200),
        principal: Any = Depends(current_principal),
    ) -> dict[str, Any]:
        enforce(principal, project_key, "admin")
        from sqlalchemy import select
        from .db_models import ProjectRow, ReferenceCandidateRow

        with session_factory() as session:
            project = session.scalar(select(ProjectRow).where(ProjectRow.project_key == project_key))
            if project is None:
                raise HTTPException(status_code=404, detail="项目不存在")
            query = select(ReferenceCandidateRow).where(ReferenceCandidateRow.project_id == project.id)
            if candidate_status:
                query = query.where(ReferenceCandidateRow.status == candidate_status)
            rows = session.scalars(query.order_by(ReferenceCandidateRow.created_at, ReferenceCandidateRow.id).limit(limit)).all()
            return {"candidates": [{"id": row.id, "document_id": row.document_id, "chunk_id": row.chunk_id, "status": row.status, "title": row.title, "content": row.content, "evidence": row.evidence_json} for row in rows]}

    @app.post("/api/v1/admin/candidates/{candidate_id}/review")
    def review_admin_candidate(candidate_id: int, payload: dict[str, str], principal: Any = Depends(current_principal)) -> dict[str, Any]:
        require_permission(principal, "admin")
        from .db_models import CandidatePolicyResultRow, MemoryCandidateRow, ProjectRow

        decision = payload.get("decision", "")
        if decision not in {"approve", "reject"}:
            raise HTTPException(status_code=422, detail="decision 必须是 approve 或 reject")
        with session_factory() as session:
            candidate = session.get(MemoryCandidateRow, candidate_id)
            if candidate is None:
                raise HTTPException(status_code=404, detail="候选记忆不存在")
            project = session.get(ProjectRow, candidate.project_id)
            if project is None:
                raise HTTPException(status_code=404, detail="项目不存在")
            try:
                require_project_access(principal, project.project_key)
            except ProjectAccessDenied as error:
                raise HTTPException(status_code=403, detail=str(error)) from error
            candidate.status = "rejected" if decision == "reject" else "approved"
            session.add(
                CandidatePolicyResultRow(
                    candidate_id=candidate.id,
                    policy_version="review-v1",
                    decision=decision,
                    reason_codes=[] if decision == "approve" else ["review_rejected"],
                    checks={"reviewed": True},
                    reviewer=payload.get("reviewer"),
                    reason=payload.get("reason"),
                )
            )
            session.commit()
            return {"id": candidate.id, "status": candidate.status, "reviewer": payload.get("reviewer")}

    @app.post("/api/v1/admin/replay")
    def replay_admin(payload: dict[str, Any], principal: Any = Depends(current_principal)) -> dict[str, Any]:
        project_key = str(payload.get("project_key", ""))
        job_type = str(payload.get("job_type", "message.appended.v1"))
        enforce(principal, project_key, "admin")
        from sqlalchemy import select
        from .db_models import MessageRow, ProcessingJobRow, ProjectRow

        with session_factory() as session:
            project = session.scalar(select(ProjectRow).where(ProjectRow.project_key == project_key))
            if project is None:
                raise HTTPException(status_code=404, detail="项目不存在")
            messages = session.scalars(
                select(MessageRow).where(MessageRow.project_id == project.id).order_by(MessageRow.id)
            ).all()
            created = 0
            for message in messages:
                key = f"replay:{job_type}:{message.id}:v1"
                existing = session.scalar(select(ProcessingJobRow).where(ProcessingJobRow.job_key == key))
                if existing is not None:
                    continue
                session.add(
                    ProcessingJobRow(
                        project_id=project.id,
                        job_type=job_type,
                        aggregate_type="message",
                        aggregate_id=message.id,
                        job_key=key,
                        payload_version="v1",
                        payload={"project_id": project.id, "message_id": message.id, "project_key": project_key},
                    )
                )
                created += 1
            session.commit()
            return {"created": created, "job_type": job_type}
    @app.get("/api/v1/health")
    def health_v1() -> dict[str, Any]:
        from datetime import datetime, timezone
        from sqlalchemy import func, inspect, select, text

        try:
            with session_factory() as session:
                session.execute(text("SELECT 1"))
                dialect = session.bind.dialect.name if session.bind is not None else "unknown"
                vector = "not-applicable"
                if dialect == "postgresql":
                    vector = "ok" if session.execute(text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")).first() else "error"
                outbox = "ok" if inspect(session.bind).has_table("outbox_events") else "not-applicable"
                worker_status: dict[str, Any] = {"status": "unknown", "last_heartbeat_age_seconds": None, "active_jobs": 0}
                if inspect(session.bind).has_table("worker_instances"):
                    from .db_models import ProcessingJobRow, WorkerInstanceRow

                    worker = session.scalar(
                        select(WorkerInstanceRow)
                        .where(WorkerInstanceRow.status != "stopped")
                        .order_by(WorkerInstanceRow.last_seen_at.desc())
                    )
                    active_jobs = session.scalar(select(func.count(ProcessingJobRow.id)).where(ProcessingJobRow.status == "running")) or 0
                    worker_status["active_jobs"] = int(active_jobs)
                    if worker is not None:
                        last_seen_at = worker.last_seen_at
                        if last_seen_at.tzinfo is None:
                            last_seen_at = last_seen_at.replace(tzinfo=timezone.utc)
                        age = (datetime.now(timezone.utc) - last_seen_at).total_seconds()
                        worker_status.update(
                            {
                                "status": "healthy" if age <= 120 else "degraded",
                                "last_heartbeat_age_seconds": max(0, int(age)),
                            }
                        )
                return {"status": "ok", "database": "ok", "vector": vector, "outbox": outbox, "worker": worker_status, "lexical": "available", "vector_profile": vector}
        except Exception:
            return {"status": "degraded", "database": "error", "vector": "unknown"}

    return app
