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
