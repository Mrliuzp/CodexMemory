from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, status
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
    from .v1_service import V1MemoryService

    service = V1MemoryService(session_factory)
    app = FastAPI(title="Codex Memory V1 API", version="1.0.0")
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
            result = service.append_message(
                principal,
                payload.project_key,
                payload.session_key,
                payload.event_key,
                payload.role,
                payload.content,
                source=payload.source,
                metadata=payload.metadata,
            )
        except (ProjectAccessDenied, PermissionDenied) as error:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error)) from error
        return {"id": result.message_id, "status": result.status}

    @app.post("/api/v1/memory")
    def create_memory_v1(payload: MemoryV1Request, principal: Any = Depends(current_principal)) -> dict[str, Any]:
        try:
            memory = service.create_l1_memory(principal, payload.project_key, payload.type, payload.content, payload.title)
        except (ProjectAccessDenied, PermissionDenied) as error:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error)) from error
        return {"id": memory.id, "level": memory.level, "status": memory.status}

    @app.post("/api/v1/context")
    def context_v1(payload: ContextV1Request, principal: Any = Depends(current_principal)) -> dict[str, Any]:
        enforce(principal, payload.project_key, "read")
        return {"critical_rules": [], "long_term_rules": [], "recent_insights": [], "source_ids": []}

    @app.post("/api/v1/search")
    def search_v1(payload: SearchV1Request, principal: Any = Depends(current_principal)) -> dict[str, Any]:
        enforce(principal, payload.project_key, "read")
        return {"results": []}

    @app.post("/api/v1/reflect")
    def reflect_v1(payload: ReflectV1Request, principal: Any = Depends(current_principal)) -> dict[str, Any]:
        enforce(principal, payload.project_key, "reflect")
        return {"status": "accepted"}

    @app.get("/api/v1/health")
    def health_v1() -> dict[str, str]:
        return {"status": "ok", "database": "configured", "vector": "configured"}

    return app
