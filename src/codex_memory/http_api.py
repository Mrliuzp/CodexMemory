from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from .models import Layer
from .service import MemoryService
from .v1_schemas import (
    AppendV1Request,
    AppendV1Response,
    ContextV1Request,
    MemoryV1Request,
    ReflectV1Request,
    SearchV1Request,
)


LOGGER = logging.getLogger("codex_memory.http")


class AppendRequest(BaseModel):
    project: str
    conversation: str
    role: str
    content: str
    metadata: dict[str, Any] | None = None
    process_now: bool = False
    enqueue_async: bool = False


class RetrieveRequest(BaseModel):
    project: str
    query: str
    tag: list[str] = Field(default_factory=list)
    module: list[str] = Field(default_factory=list)
    tag_type: list[str] = Field(default_factory=list)
    layer: list[Layer] = Field(default_factory=list)
    memory_type: list[str] = Field(default_factory=list)
    limit: int = 8


class ContextRequest(BaseModel):
    project: str
    task: str
    tag: list[str] = Field(default_factory=list)
    module: list[str] = Field(default_factory=list)
    tag_type: list[str] = Field(default_factory=list)
    layer: list[Layer] = Field(default_factory=list)
    memory_type: list[str] = Field(default_factory=list)
    limit: int = 8
    project_context: str | None = None
    skip_pending: bool = False


def create_app(db_path: str | Path = "memory.db") -> FastAPI:
    service = MemoryService(db_path)
    app = FastAPI(title="Codex Memory API", version="0.1.0")

    @app.middleware("http")
    async def request_logger(request: Request, call_next):
        response = await call_next(request)
        LOGGER.info("%s %s -> %s", request.method, request.url.path, response.status_code)
        return response

    @app.get("/health")
    def health() -> dict[str, Any]:
        return service.health_status()

    @app.post("/append")
    def append(payload: AppendRequest) -> dict[str, Any]:
        raw_id = service.append_conversation(
            project_id=payload.project,
            conversation_id=payload.conversation,
            role=payload.role,
            content=payload.content,
            metadata=payload.metadata,
            process_now=payload.process_now,
            enqueue_async=payload.enqueue_async,
        )
        if payload.enqueue_async:
            service.drain_async_processor()
            service.stop_async_processor()
        return {"raw_log_id": raw_id}

    @app.post("/retrieve")
    def retrieve(payload: RetrieveRequest) -> dict[str, Any]:
        results = service.retrieve(
            payload.project,
            payload.query,
            tags=payload.tag or None,
            modules=payload.module or None,
            type_tags=payload.tag_type or None,
            layers=payload.layer or None,
            memory_types=payload.memory_type or None,
            limit=payload.limit,
        )
        return {
            "results": [
                {
                    "id": result.item.id,
                    "project_id": result.item.project_id,
                    "layer": result.item.layer.value,
                    "title": result.item.title,
                    "memory_type": result.item.memory_type,
                    "body": result.item.body,
                    "tags": result.item.tags,
                    "score": result.score,
                    "semantic_score": result.semantic_score,
                    "recency_score": result.recency_score,
                    "priority_score": result.priority_score,
                }
                for result in results
            ]
        }

    @app.post("/context")
    def context(payload: ContextRequest) -> dict[str, Any]:
        if not payload.skip_pending:
            service.process_project_pending_memories(payload.project)
        return {
            "context": service.build_context(
                payload.project,
                payload.task,
                tags=payload.tag or None,
                modules=payload.module or None,
                type_tags=payload.tag_type or None,
                layers=payload.layer or None,
                memory_types=payload.memory_type or None,
                limit=payload.limit,
                project_context=payload.project_context,
            )
        }

    app.state.memory_service = service
    return app

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
    )
    from .v1_service import AppendConflictError, V1MemoryService
    from .v11_embedding import EmbeddingProfileService
    from .v11_flags import ProjectPolicyService

    service = V1MemoryService(session_factory)
    app = FastAPI(title="Codex Memory V1 API", version="1.0.0")

    @app.get("/health/live")
    def liveness() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready")
    def readiness() -> dict[str, str]:
        from .runtime_health import build_readiness

        payload = build_readiness(session_factory)
        if payload["status"] != "ok":
            return JSONResponse(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, content=payload)
        return payload
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
        from .maintenance import MaintenanceService
        if MaintenanceService(session_factory).is_enabled():
            raise HTTPException(status_code=503, detail="maintenance_mode")
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
        )}}


    @app.post("/api/v1/admin/projects/{project_key}/policy")
    def update_project_policy(project_key: str, payload: dict[str, Any], principal: Any = Depends(current_principal)) -> dict[str, Any]:
        enforce(principal, project_key, "admin")
        from sqlalchemy import select
        from .db_models import ProjectRow
        from .v11_models import ProjectProcessingPolicyRow

        with session_factory() as session:
            project = session.scalar(select(ProjectRow).where(ProjectRow.project_key == project_key))
        if project is None:
            raise HTTPException(status_code=404, detail="项目不存在")
        with session_factory() as session:
            policy = session.execute(
                select(ProjectProcessingPolicyRow).where(
                    ProjectProcessingPolicyRow.project_id == project.id
                )
            ).scalar_one_or_none()
            if policy is None:
                policy = ProjectProcessingPolicyRow(project_id=project.id)
                session.add(policy)
            for key, value in payload.items():
                if hasattr(policy, key):
                    setattr(policy, key, value)
            session.commit()
            return {"project_id": project.id, "policy": {k: getattr(policy, k) for k in [
                "remote_embedding_allowed", "remote_llm_allowed", "redaction_enabled",
                "failure_mode", "allowed_embedding_providers", "allowed_llm_providers",
                "daily_embedding_token_budget", "daily_llm_token_budget", "data_residency_policy",
            ]}}

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
        from datetime import datetime, timedelta, timezone
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

    
    @app.post("/api/v1/admin/jobs/batch-retry")
    def batch_retry_admin_jobs(payload: dict[str, Any], principal: Any = Depends(current_principal)) -> dict[str, Any]:
        try:
            require_permission(principal, "admin")
        except Exception:
            from .auth import PermissionDenied
            raise HTTPException(status_code=403, detail="缺少admin权限")
        from datetime import datetime, timedelta, timezone
        from sqlalchemy import select
        from .db_models import ProcessingJobRow, ProjectRow

        job_ids = payload.get("job_ids")
        project_key = payload.get("project_key")
        status_filter = payload.get("status_filter")
        with session_factory() as session:
            query = select(ProcessingJobRow)
            if job_ids:
                query = query.where(ProcessingJobRow.id.in_(job_ids))
            if project_key:
                project = session.scalar(select(ProjectRow).where(ProjectRow.project_key == project_key))
                if project is None:
                    raise HTTPException(status_code=404, detail="项目不存在")
                query = query.where(ProcessingJobRow.project_id == project.id)
            if status_filter:
                query = query.where(ProcessingJobRow.status == status_filter)
            else:
                query = query.where(ProcessingJobRow.status.in_(["dead", "retry_wait"]))
            jobs = session.scalars(query).all()
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            retried_ids = []
            for job in jobs:
                job.status = "pending"
                job.next_attempt_at = now
                job.last_error_code = None
                job.last_error_message = None
                job.locked_by = None
                job.locked_at = None
                job.heartbeat_at = None
                job.lease_expires_at = None
                retried_ids.append(job.id)
            session.commit()
            return {"retried": len(retried_ids), "job_ids": retried_ids}

    @app.post("/api/v1/admin/jobs/batch-cancel")
    def batch_cancel_admin_jobs(payload: dict[str, Any], principal: Any = Depends(current_principal)) -> dict[str, Any]:
        try:
            require_permission(principal, "admin")
        except Exception:
            from .auth import PermissionDenied
            raise HTTPException(status_code=403, detail="缺少admin权限")
        from sqlalchemy import select
        from .db_models import ProcessingJobRow, ProjectRow

        job_ids = payload.get("job_ids")
        project_key = payload.get("project_key")
        status_filter = payload.get("status_filter")
        with session_factory() as session:
            query = select(ProcessingJobRow)
            if job_ids:
                query = query.where(ProcessingJobRow.id.in_(job_ids))
            if project_key:
                project = session.scalar(select(ProjectRow).where(ProjectRow.project_key == project_key))
                if project is None:
                    raise HTTPException(status_code=404, detail="项目不存在")
                query = query.where(ProcessingJobRow.project_id == project.id)
            if status_filter:
                query = query.where(ProcessingJobRow.status == status_filter)
            else:
                query = query.where(ProcessingJobRow.status.in_(["pending", "retry_wait"]))
            jobs = session.scalars(query).all()
            cancelled_ids = []
            for job in jobs:
                if job.status in ("pending", "retry_wait"):
                    job.status = "dead"
                    job.last_error_message = "cancelled_by_admin"
                    cancelled_ids.append(job.id)
            session.commit()
            return {"cancelled": len(cancelled_ids), "job_ids": cancelled_ids}

    @app.post("/api/v1/admin/jobs/cleanup")
    def cleanup_admin_jobs(payload: dict[str, Any], principal: Any = Depends(current_principal)) -> dict[str, Any]:
        try:
            require_permission(principal, "admin")
        except Exception:
            from .auth import PermissionDenied
            raise HTTPException(status_code=403, detail="缺少admin权限")
        from datetime import datetime, timedelta, timezone
        from sqlalchemy import select
        from .db_models import ProcessingJobRow

        older_than_days = int(payload.get("older_than_days", 30))
        status_filter = str(payload.get("status", "completed"))
        with session_factory() as session:
            cutoff = datetime.now(timezone.utc) - timedelta(days=older_than_days)
            query = select(ProcessingJobRow).where(
                ProcessingJobRow.status == status_filter,
                ProcessingJobRow.completed_at < cutoff,
            )
            jobs = session.scalars(query).all()
            deleted_ids = [j.id for j in jobs]
            for job in jobs:
                session.delete(job)
            session.commit()
            return {"deleted": len(deleted_ids), "job_ids": deleted_ids}
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

    @app.put("/api/v1/admin/memories/{memory_id}")
    def update_admin_memory(memory_id: int, payload: dict[str, Any], principal: Any = Depends(current_principal)) -> dict[str, Any]:
        require_permission(principal, "admin")
        from .db_models import MemoryRow
        with session_factory() as session:
            memory = session.get(MemoryRow, memory_id)
            if memory is None:
                raise HTTPException(status_code=404, detail="记忆不存在")
            if "title" in payload:
                memory.title = str(payload["title"])
            if "content" in payload:
                memory.content = payload["content"]
            if "status" in payload:
                valid = {"active", "archived", "draft"}
                if payload["status"] not in valid:
                    raise HTTPException(status_code=422, detail="状态值无效")
                memory.status = payload["status"]
            session.commit()
            return {"id": memory.id, "title": memory.title, "status": memory.status}

    @app.delete("/api/v1/admin/memories/{memory_id}")
    def delete_admin_memory(memory_id: int, principal: Any = Depends(current_principal)) -> dict[str, Any]:
        require_permission(principal, "admin")
        from .db_models import MemoryRow
        with session_factory() as session:
            memory = session.get(MemoryRow, memory_id)
            if memory is None:
                raise HTTPException(status_code=404, detail="记忆不存在")
            session.delete(memory)
            session.commit()
            return {"deleted": memory_id}

    @app.post("/api/v1/admin/memories/{memory_id}/level")
    def change_admin_memory_level(memory_id: int, payload: dict[str, str], principal: Any = Depends(current_principal)) -> dict[str, Any]:
        require_permission(principal, "admin")
        from .db_models import MemoryRow
        level = payload.get("level", "")
        if level not in {"L1", "L2", "L3"}:
            raise HTTPException(status_code=422, detail="层级无效，有效值: L1, L2, L3")
        with session_factory() as session:
            memory = session.get(MemoryRow, memory_id)
            if memory is None:
                raise HTTPException(status_code=404, detail="记忆不存在")
            memory.level = level
            session.commit()
            return {"id": memory.id, "level": memory.level}


    @app.get("/api/v1/admin/budgets")
    def list_admin_budgets(principal: Any = Depends(current_principal)) -> dict[str, Any]:
        require_permission(principal, "admin")
        from sqlalchemy import select
        from .db_models import ProjectRow
        from .v11_models import ProjectProcessingPolicyRow

        with session_factory() as session:
            rows = session.execute(
                select(
                    ProjectRow.id,
                    ProjectRow.project_key,
                    ProjectProcessingPolicyRow.daily_embedding_token_budget,
                    ProjectProcessingPolicyRow.daily_llm_token_budget,
                )
                .outerjoin(
                    ProjectProcessingPolicyRow,
                    ProjectRow.id == ProjectProcessingPolicyRow.project_id,
                )
                .order_by(ProjectRow.id)
            ).all()
            return {"budgets": [
                {"project_id": r.id, "project_key": r.project_key,
                 "daily_embedding_token_budget": r.daily_embedding_token_budget,
                 "daily_llm_token_budget": r.daily_llm_token_budget}
                for r in rows
            ]}

    @app.put("/api/v1/admin/budgets/{project_id}")
    def update_admin_budget(project_id: int, payload: dict[str, Any], principal: Any = Depends(current_principal)) -> dict[str, Any]:
        require_permission(principal, "admin")
        from sqlalchemy import select
        from .db_models import ProjectRow
        from .v11_models import ProjectProcessingPolicyRow

        project = session_factory().get(ProjectRow, project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="项目不存在")
        with session_factory() as session:
            policy = session.execute(
                select(ProjectProcessingPolicyRow).where(ProjectProcessingPolicyRow.project_id == project_id)
            ).scalar_one_or_none()
            if policy is None:
                policy = ProjectProcessingPolicyRow(project_id=project_id)
                session.add(policy)
            if "daily_embedding_token_budget" in payload:
                policy.daily_embedding_token_budget = int(payload["daily_embedding_token_budget"])
            if "daily_llm_token_budget" in payload:
                policy.daily_llm_token_budget = int(payload["daily_llm_token_budget"])
            session.commit()
            return {"project_id": project_id,
                    "daily_embedding_token_budget": policy.daily_embedding_token_budget,
                    "daily_llm_token_budget": policy.daily_llm_token_budget}

    @app.get("/api/v1/admin/budgets/summary")
    def budget_admin_summary(principal: Any = Depends(current_principal)) -> dict[str, Any]:
        require_permission(principal, "admin")
        from datetime import date
        from sqlalchemy import func, select
        from .db_models import ProjectRow
        from .v11_models import DailyTokenUsageRow, ProjectProcessingPolicyRow

        today = date.today()
        with session_factory() as session:
            rows = session.execute(
                select(
                    ProjectRow.id, ProjectRow.project_key,
                    ProjectProcessingPolicyRow.daily_embedding_token_budget,
                    ProjectProcessingPolicyRow.daily_llm_token_budget,
                )
                .outerjoin(ProjectProcessingPolicyRow, ProjectRow.id == ProjectProcessingPolicyRow.project_id)
            ).all()
            summary = []
            for r in rows:
                embed_used = session.scalar(
                    select(func.coalesce(func.sum(DailyTokenUsageRow.tokens_used), 0))
                    .where(DailyTokenUsageRow.project_id == r.id,
                           DailyTokenUsageRow.usage_date == today,
                           DailyTokenUsageRow.token_type == "embedding")
                ) or 0
                llm_used = session.scalar(
                    select(func.coalesce(func.sum(DailyTokenUsageRow.tokens_used), 0))
                    .where(DailyTokenUsageRow.project_id == r.id,
                           DailyTokenUsageRow.usage_date == today,
                           DailyTokenUsageRow.token_type == "llm")
                ) or 0
                eb = r.daily_embedding_token_budget or 0
                lb = r.daily_llm_token_budget or 0
                ep = round(embed_used / eb * 100, 1) if eb > 0 else 0
                lp = round(llm_used / lb * 100, 1) if lb > 0 else 0
                over = embed_used > eb or llm_used > lb
                alert = "critical" if over else "warning" if max(ep, lp) > 80 else "ok"
                summary.append({
                    "project_id": r.id, "project_key": r.project_key,
                    "embedding_used_today": embed_used, "embedding_budget": eb,
                    "embedding_pct": ep, "llm_used_today": llm_used,
                    "llm_budget": lb, "llm_pct": lp,
                    "over_budget": over, "alert_level": alert,
                })
            return {"summary": summary}

    @app.post("/api/v1/admin/import/preview")
    def admin_import_preview(payload: dict[str, Any], principal: Any = Depends(current_principal)) -> dict[str, Any]:
        require_permission(principal, "admin")
        from sqlalchemy import select
        from .db_models import ProjectRow
        project_key = payload.get("project_key", "")
        items = payload.get("items", [])
        with session_factory() as session:
            project = session.scalar(select(ProjectRow).where(ProjectRow.project_key == project_key))
        if project is None:
            raise HTTPException(status_code=404, detail="项目不存在")
        mem_count = sum(1 for i in items if i.get("type") == "memory")
        msg_count = sum(1 for i in items if i.get("type") == "message")
        return {"project_key": project_key, "total_items": len(items), "memories": mem_count, "messages": msg_count}

    @app.post("/api/v1/admin/import/execute")
    def admin_import_execute(payload: dict[str, Any], principal: Any = Depends(current_principal)) -> dict[str, Any]:
        require_permission(principal, "admin")
        from datetime import datetime, timezone
        from sqlalchemy import select
        from .db_models import MemoryRow, MessageRow, ProjectRow
        project_key = payload.get("project_key", "")
        items = payload.get("items", [])
        with session_factory() as session:
            project = session.scalar(select(ProjectRow).where(ProjectRow.project_key == project_key))
        if project is None:
            raise HTTPException(status_code=404, detail="项目不存在")
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        memories, messages = 0, 0
        with session_factory() as session:
            for item in items:
                t = item.get("type")
                if t == "memory":
                    session.add(MemoryRow(
                        project_id=project.id,
                        level=item.get("level", "L2"),
                        memory_type=item.get("memory_type", "knowledge"),
                        title=item.get("title", ""),
                        content=item.get("content", {}),
                        confidence=0.7, status="active", usage_count=0,
                        scope=item.get("scope", "project"),
                        source_kind="import", review_status="pending",
                        created_at=now, updated_at=now,
                    ))
                    memories += 1
                elif t == "message":
                    session.add(MessageRow(
                        project_id=project.id,
                        session_key=item.get("session_key", f"import-{project_key}-{messages}"),
                        event_key=item.get("event_key") or f"import:{project_key}:{messages}:{now.timestamp()}",
                        role=item.get("role", "user"),
                        content=str(item.get("content", "")),
                        source="import",
                        occurred_at=item.get("occurred_at") or now,
                        created_at=now,
                    ))
                    messages += 1
            session.commit()
        return {"memories_created": memories, "messages_created": messages, "total": memories + messages}

    @app.get("/api/v1/admin/export/projects/{project_key}/memories")
    def admin_export_memories(project_key: str, principal: Any = Depends(current_principal)) -> dict[str, Any]:
        require_permission(principal, "read")
        from sqlalchemy import select
        from .db_models import MemoryRow, ProjectRow
        with session_factory() as session:
            project = session.scalar(select(ProjectRow).where(ProjectRow.project_key == project_key))
            if project is None:
                raise HTTPException(status_code=404, detail="项目不存在")
            rows = session.scalars(select(MemoryRow).where(MemoryRow.project_id == project.id).order_by(MemoryRow.id)).all()
            return {"project_key": project_key, "memories": [
                {"id": r.id, "level": r.level, "memory_type": r.memory_type, "title": r.title, "content": r.content,
                 "scope": r.scope, "confidence": r.confidence, "source_kind": r.source_kind}
                for r in rows
            ]}

    @app.get("/api/v1/admin/export/projects/{project_key}/logs")
    def admin_export_logs(project_key: str, principal: Any = Depends(current_principal)) -> dict[str, Any]:
        require_permission(principal, "read")
        from sqlalchemy import select
        from .db_models import MessageRow, ProjectRow
        with session_factory() as session:
            project = session.scalar(select(ProjectRow).where(ProjectRow.project_key == project_key))
            if project is None:
                raise HTTPException(status_code=404, detail="项目不存在")
            rows = session.scalars(select(MessageRow).where(MessageRow.project_id == project.id).order_by(MessageRow.id)).all()
            return {"project_key": project_key, "logs": [
                {"id": r.id, "event_key": r.event_key, "role": r.role, "content": r.content, "source": r.source}
                for r in rows
            ]}

    @app.post("/api/v1/admin/cleanup/messages")
    def admin_cleanup_messages(payload: dict[str, Any], principal: Any = Depends(current_principal)) -> dict[str, Any]:
        try:
            require_permission(principal, "admin")
        except Exception:
            raise HTTPException(status_code=403, detail="缺少admin权限")
        from datetime import datetime, timedelta, timezone
        from sqlalchemy import func, select
        from .db_models import MessageRow, ProjectRow
        project_key = str(payload.get("project_key", ""))
        older_than_days = int(payload.get("older_than_days", 30))
        dry_run = bool(payload.get("dry_run", False))
        with session_factory() as session:
            project = session.scalar(select(ProjectRow).where(ProjectRow.project_key == project_key))
            if project is None:
                raise HTTPException(status_code=404, detail="项目不存在")
            cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=older_than_days)
            q = select(MessageRow).where(MessageRow.project_id == project.id, MessageRow.created_at < cutoff)
            total = int(session.scalar(select(func.count()).select_from(q.subquery())) or 0)
            if not dry_run:
                for row in session.scalars(q):
                    session.delete(row)
                session.commit()
            return {"deleted": total, "dry_run": dry_run, "project_key": project_key}

    @app.post("/api/v1/admin/cleanup/memories")
    def admin_cleanup_memories(payload: dict[str, Any], principal: Any = Depends(current_principal)) -> dict[str, Any]:
        try:
            require_permission(principal, "admin")
        except Exception:
            raise HTTPException(status_code=403, detail="缺少admin权限")
        from datetime import datetime, timedelta, timezone
        from sqlalchemy import func, select
        from .db_models import MemoryRow, ProjectRow
        project_key = str(payload.get("project_key", ""))
        older_than_days = int(payload.get("older_than_days", 30))
        dry_run = bool(payload.get("dry_run", False))
        with session_factory() as session:
            project = session.scalar(select(ProjectRow).where(ProjectRow.project_key == project_key))
            if project is None:
                raise HTTPException(status_code=404, detail="项目不存在")
            cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=older_than_days)
            q = select(MemoryRow).where(MemoryRow.project_id == project.id, MemoryRow.updated_at < cutoff)
            total = int(session.scalar(select(func.count()).select_from(q.subquery())) or 0)
            if not dry_run:
                for row in session.scalars(q):
                    session.delete(row)
                session.commit()
            return {"deleted": total, "dry_run": dry_run, "project_key": project_key}

    @app.post("/api/v1/admin/cleanup/jobs")
    def admin_cleanup_jobs(payload: dict[str, Any], principal: Any = Depends(current_principal)) -> dict[str, Any]:
        try:
            require_permission(principal, "admin")
        except Exception:
            raise HTTPException(status_code=403, detail="缺少admin权限")
        from datetime import datetime, timedelta, timezone
        from sqlalchemy import func, select
        from .db_models import ProcessingJobRow, ProjectRow
        project_key = str(payload.get("project_key", ""))
        older_than_days = int(payload.get("older_than_days", 30))
        dry_run = bool(payload.get("dry_run", False))
        with session_factory() as session:
            project = session.scalar(select(ProjectRow).where(ProjectRow.project_key == project_key))
            if project is None:
                raise HTTPException(status_code=404, detail="项目不存在")
            cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=older_than_days)
            q = select(ProcessingJobRow).where(
                ProcessingJobRow.project_id == project.id,
                ProcessingJobRow.completed_at < cutoff,
                ProcessingJobRow.status.in_(["completed", "dead"]),
            )
            total = int(session.scalar(select(func.count()).select_from(q.subquery())) or 0)
            if not dry_run:
                for row in session.scalars(q):
                    session.delete(row)
                session.commit()
            return {"deleted": total, "dry_run": dry_run, "project_key": project_key}

    @app.get("/api/v1/admin/archive/projects/{project_key}")
    def admin_archive_status(project_key: str, principal: Any = Depends(current_principal)) -> dict[str, Any]:
        try:
            require_permission(principal, "read")
        except Exception:
            raise HTTPException(status_code=403, detail="缺少权限")
        from sqlalchemy import select
        from .db_models import ArchiveStatusRow, ProjectRow
        with session_factory() as session:
            project = session.scalar(select(ProjectRow).where(ProjectRow.project_key == project_key))
            if project is None:
                raise HTTPException(status_code=404, detail="项目不存在")
            ar = session.scalar(select(ArchiveStatusRow).where(ArchiveStatusRow.project_id == project.id))
            if ar is None:
                return {"project_key": project_key, "pending_count": 0, "dead_letter_count": 0}
            return {"project_key": project_key, "pending_count": ar.pending_count,
                    "dead_letter_count": ar.dead_letter_count, "last_success_at": str(ar.last_success_at or "")}

    @app.get("/api/v1/admin/migrations")
    def admin_list_migrations(principal: Any = Depends(current_principal)) -> dict[str, Any]:
        try:
            require_permission(principal, "admin")
        except Exception:
            raise HTTPException(status_code=403, detail="缺少admin权限")
        from pathlib import Path
        from alembic.config import Config
        from alembic.script import ScriptDirectory
        from sqlalchemy import text

        candidates = [
            Path("alembic.ini"),
            Path(__file__).resolve().parent.parent.parent / "alembic.ini",
        ]
        cfg = None
        for p in candidates:
            if p.exists():
                cfg = Config(str(p))
                break
        if cfg is None:
            raise HTTPException(status_code=500, detail="找不到 alembic.ini")

        script = ScriptDirectory.from_config(cfg)
        current_rev = None
        try:
            with session_factory() as session:
                row = session.execute(text("SELECT version_num FROM alembic_version")).first()
                if row:
                    current_rev = row[0]
        except Exception:
            pass

        revisions = []
        for rev in script.walk_revisions():
            revisions.append({
                "revision": rev.revision, "down_revision": rev.down_revision,
                "is_head": rev.revision in script.get_heads(),
                "is_current": rev.revision == current_rev,
            })
        pending = []
        if current_rev:
            for head in script.get_heads():
                for rev in script.iterate_revisions(head, current_rev):
                    if rev.revision not in pending:
                        pending.append(rev.revision)
        else:
            pending = list(script.get_heads())
        return {"current_revision": current_rev, "revisions": revisions, "pending_revisions": pending, "up_to_date": len(pending) == 0}

    @app.post("/api/v1/admin/migrations/upgrade")
    def admin_upgrade_migrations(principal: Any = Depends(current_principal)) -> dict[str, Any]:
        try:
            require_permission(principal, "admin")
        except Exception:
            raise HTTPException(status_code=403, detail="缺少admin权限")
        from pathlib import Path
        from alembic.config import Config
        from alembic import command

        candidates = [Path("alembic.ini"), Path(__file__).resolve().parent.parent.parent / "alembic.ini"]
        cfg = None
        for p in candidates:
            if p.exists():
                cfg = Config(str(p))
                break
        if cfg is None:
            raise HTTPException(status_code=500, detail="找不到 alembic.ini")
        try:
            command.upgrade(cfg, "head")
            return {"status": "ok", "message": "迁移已执行"}
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"迁移失败: {str(e)}")

    @app.get("/api/v1/admin/events")
    def admin_list_events(principal: Any = Depends(current_principal), limit: int = Query(50, ge=1, le=200)) -> dict[str, Any]:
        require_permission(principal, "read")
        from .db_models import AuditLogRow, ProcessingJobRow

        page_size = limit
        from uuid import uuid4 as _u4
        rid = str(_u4())
        with session_factory() as session:
            events = []
            for row in session.scalars(
                __import__("sqlalchemy").select(AuditLogRow).order_by(AuditLogRow.id.desc()).limit(page_size)
            ):
                meta = row.metadata_json or {}
                events.append({
                    "id": f"audit-{row.id}", "type": row.event_type,
                    "timestamp": str(row.created_at) if row.created_at else "",
                    "summary": f"{row.event_type}: {row.subject_type}/{row.subject_id}" if row.subject_id else row.event_type,
                })
            for row in session.scalars(
                __import__("sqlalchemy").select(ProcessingJobRow).order_by(ProcessingJobRow.updated_at.desc()).limit(page_size)
            ):
                events.append({
                    "id": f"job-{row.id}", "type": f"job_{row.status}",
                    "timestamp": str(row.updated_at) if row.updated_at else "",
                    "summary": f"{row.job_type}: {row.status}",
                })
            events.sort(key=lambda e: e.get("timestamp", ""), reverse=True)
            return {"data": events[:page_size], "request_id": rid}

    @app.get("/api/v1/admin/api-keys")
    def admin_list_api_keys(principal: Any = Depends(current_principal)) -> dict[str, Any]:
        try:
            require_permission(principal, "admin")
        except Exception:
            raise HTTPException(status_code=403, detail="缺少admin权限")
        from sqlalchemy import select
        from .db_models import ApiKeyRow
        with session_factory() as session:
            rows = session.scalars(select(ApiKeyRow).order_by(ApiKeyRow.created_at.desc())).all()
            return {"api_keys": [
                {"id": r.id, "project_id": r.project_id, "permissions": r.permissions,
                 "status": r.status, "created_at": str(r.created_at or "")}
                for r in rows
            ]}

    @app.post("/api/v1/admin/api-keys")
    def admin_create_api_key(payload: dict[str, Any], principal: Any = Depends(current_principal)) -> dict[str, Any]:
        try:
            require_permission(principal, "admin")
        except Exception:
            raise HTTPException(status_code=403, detail="缺少admin权限")
        import secrets
        import hashlib
        from .db_models import ApiKeyRow
        with session_factory() as session:
            raw_token = "cm-" + secrets.token_hex(32)
            entry = ApiKeyRow(
                project_id=int(payload["project_id"]),
                token_hash=hashlib.sha256(raw_token.encode()).hexdigest(),
                permissions=payload.get("permissions", ["read"]),
                status="active",
            )
            session.add(entry)
            session.commit()
            session.refresh(entry)
            return {"id": entry.id, "token": raw_token, "permissions": entry.permissions}

    @app.delete("/api/v1/admin/api-keys/{key_id}")
    def admin_delete_api_key(key_id: int, principal: Any = Depends(current_principal)) -> dict[str, Any]:
        try:
            require_permission(principal, "admin")
        except Exception:
            raise HTTPException(status_code=403, detail="缺少admin权限")
        from .db_models import ApiKeyRow
        with session_factory() as session:
            key = session.get(ApiKeyRow, key_id)
            if key is None:
                raise HTTPException(status_code=404, detail="API Key 不存在")
            session.delete(key)
            session.commit()
            return {"deleted": key_id}

    @app.get("/api/v1/admin/api-keys")
    def admin_list_api_keys(principal: Any = Depends(current_principal)) -> dict[str, Any]:
        try:
            require_permission(principal, "admin")
        except Exception:
            raise HTTPException(status_code=403, detail="缺少admin权限")
        from sqlalchemy import select
        from .db_models import ApiKeyRow
        with session_factory() as session:
            rows = session.scalars(select(ApiKeyRow).order_by(ApiKeyRow.created_at.desc())).all()
            return {"api_keys": [
                {"id": r.id, "project_id": r.project_id, "permissions": r.permissions,
                 "status": r.status, "created_at": str(r.created_at or "")}
                for r in rows
            ]}

    @app.post("/api/v1/admin/api-keys")
    def admin_create_api_key(payload: dict[str, Any], principal: Any = Depends(current_principal)) -> dict[str, Any]:
        try:
            require_permission(principal, "admin")
        except Exception:
            raise HTTPException(status_code=403, detail="缺少admin权限")
        import secrets
        import hashlib
        from .db_models import ApiKeyRow
        with session_factory() as session:
            raw_token = "cm-" + secrets.token_hex(32)
            entry = ApiKeyRow(
                project_id=int(payload["project_id"]),
                token_hash=hashlib.sha256(raw_token.encode()).hexdigest(),
                permissions=payload.get("permissions", ["read"]),
                status="active",
            )
            session.add(entry)
            session.commit()
            session.refresh(entry)
            return {"id": entry.id, "token": raw_token, "permissions": entry.permissions}

    @app.delete("/api/v1/admin/api-keys/{key_id}")
    def admin_delete_api_key(key_id: int, principal: Any = Depends(current_principal)) -> dict[str, Any]:
        try:
            require_permission(principal, "admin")
        except Exception:
            raise HTTPException(status_code=403, detail="缺少admin权限")
        from .db_models import ApiKeyRow
        with session_factory() as session:
            key = session.get(ApiKeyRow, key_id)
            if key is None:
                raise HTTPException(status_code=404, detail="API Key 不存在")
            session.delete(key)
            session.commit()
            return {"deleted": key_id}


    @app.get("/api/v1/admin/audit/stats")
    def admin_audit_stats(
        project_key: str | None = None,
        days: int = Query(30, ge=1, le=365),
        principal: Any = Depends(current_principal),
    ) -> dict[str, Any]:
        require_permission(principal, "read")
        from sqlalchemy import select
        from .db_models import AuditLogRow, ProjectRow

        with session_factory() as session:
            q = select(AuditLogRow)
            if project_key:
                project = session.scalar(select(ProjectRow).where(ProjectRow.project_key == project_key))
                if project:
                    q = q.where(AuditLogRow.project_id == project.id)
            from datetime import datetime, timedelta, timezone
            since = datetime.now(timezone.utc) - timedelta(days=days)
            q = q.where(AuditLogRow.created_at >= since)
            rows = session.scalars(q.order_by(AuditLogRow.id.desc())).all()
            by_type, by_date = {}, {}
            for row in rows:
                by_type[row.event_type] = by_type.get(row.event_type, 0) + 1
                d = row.created_at.strftime("%Y-%m-%d") if row.created_at else "unknown"
                by_date[d] = by_date.get(d, 0) + 1
            return {"total": len(rows), "by_type": by_type,
                    "by_date": [{"date": d, "count": c} for d, c in sorted(by_date.items())],
                    "days": days}

    @app.get("/api/v1/admin/audit/search")
    def admin_audit_search(
        q: str = "",
        project_key: str | None = None,
        event_type: str | None = None,
        limit: int = Query(50, ge=1, le=500),
        offset: int = Query(0, ge=0),
        principal: Any = Depends(current_principal),
    ) -> dict[str, Any]:
        require_permission(principal, "read")
        from sqlalchemy import func, select
        from .db_models import AuditLogRow, ProjectRow

        with session_factory() as session:
            query = select(AuditLogRow)
            if project_key:
                project = session.scalar(select(ProjectRow).where(ProjectRow.project_key == project_key))
                if project:
                    query = query.where(AuditLogRow.project_id == project.id)
            if event_type:
                query = query.where(AuditLogRow.event_type == event_type)
            if q:
                query = query.where(
                    AuditLogRow.event_type.ilike(f"%{q}%")
                )
            total = int(session.scalar(select(func.count()).select_from(query.subquery())) or 0)
            rows = session.scalars(
                query.order_by(AuditLogRow.id.desc()).offset(offset).limit(limit)
            ).all()
            return {"audit_logs": [
                {"id": r.id, "project_id": r.project_id, "event_type": r.event_type,
                 "subject_type": r.subject_type, "subject_id": r.subject_id}
                for r in rows
            ], "total": total, "limit": limit, "offset": offset}


    @app.get("/api/v1/admin/health/check")
    def admin_health_check(principal: Any = Depends(current_principal)) -> dict[str, Any]:
        require_permission(principal, "read")
        from sqlalchemy import func, select, text
        from .db_models import AuditLogRow as SecRow

        with session_factory() as session:
            issues = []
            try:
                session.execute(text("SELECT 1"))
            except Exception:
                issues.append({"severity": "critical", "type": "database", "detail": "数据库不可达"})

            stuck = int(session.scalar(select(func.count("*")).select_from(text("processing_jobs")).where(text("status = 'retry_wait'"))) or 0)
            if stuck > 0:
                issues.append({"severity": "warning", "type": "stuck_jobs", "count": stuck})
            pending = int(session.scalar(select(func.count("*")).select_from(text("memory_candidates")).where(text("status = 'generated'"))) or 0)
            if pending > 0:
                issues.append({"severity": "info", "type": "pending_candidates", "count": pending})
            outbox = int(session.scalar(select(func.count("*")).select_from(text("outbox_events")).where(text("status = 'pending'"))) or 0)
            if outbox > 0:
                issues.append({"severity": "warning", "type": "outbox_backlog", "count": outbox})
            return {"healthy": len(issues) == 0, "issues": issues, "total_issues": len(issues)}

    @app.get("/api/v1/admin/alerts/config")
    def admin_get_alerts(principal: Any = Depends(current_principal)) -> dict[str, Any]:
        require_permission(principal, "read")
        return {"stuck_job_threshold": 10, "candidate_threshold": 20, "outbox_threshold": 50, "budget_warning_pct": 80}

    @app.put("/api/v1/admin/alerts/config")
    def admin_update_alerts(payload: dict[str, Any], principal: Any = Depends(current_principal)) -> dict[str, Any]:
        require_permission(principal, "admin")
        return {"stuck_job_threshold": int(payload.get("stuck_job_threshold", 10)),
                "candidate_threshold": int(payload.get("candidate_threshold", 20)),
                "outbox_threshold": int(payload.get("outbox_threshold", 50)),
                "budget_warning_pct": int(payload.get("budget_warning_pct", 80))}


    @app.get("/api/v1/admin/users")
    def admin_list_users(principal: Any = Depends(current_principal)) -> dict[str, Any]:
        try:
            require_permission(principal, "admin")
        except Exception:
            raise HTTPException(status_code=403, detail="缺少admin权限")
        return {"users": [principal.project_key], "total": 1}


    @app.get("/api/v1/admin/sessions")
    def admin_list_sessions(principal: Any = Depends(current_principal)) -> dict[str, Any]:
        try:
            require_permission(principal, "admin")
        except Exception:
            raise HTTPException(status_code=403, detail="缺少admin权限")
        return {"sessions": [{"id": "current", "username": principal.project_key}], "total": 1}

    @app.delete("/api/v1/admin/sessions/{session_id}")
    def admin_revoke_session(session_id: str, principal: Any = Depends(current_principal)) -> dict[str, Any]:
        try:
            require_permission(principal, "admin")
        except Exception:
            raise HTTPException(status_code=403, detail="缺少admin权限")
        return {"revoked": session_id}
    @app.get("/api/v1/health")
    def health_v1() -> dict[str, Any]:
        from sqlalchemy import inspect, text

        try:
            with session_factory() as session:
                session.execute(text("SELECT 1"))
                dialect = session.bind.dialect.name if session.bind is not None else "unknown"
                vector = "not-applicable"
                if dialect == "postgresql":
                    vector = "ok" if session.execute(text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")).first() else "error"
                outbox = "ok" if inspect(session.bind).has_table("outbox_events") else "not-applicable"
                return {"status": "ok", "database": "ok", "vector": vector, "outbox": outbox, "worker": "unknown", "lexical": "available", "vector_profile": vector}
        except Exception:
            return {"status": "degraded", "database": "error", "vector": "unknown"}

    return app
