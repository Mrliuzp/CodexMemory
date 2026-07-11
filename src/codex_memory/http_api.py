from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from pydantic import BaseModel, Field

from .models import Layer
from .service import MemoryService


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
