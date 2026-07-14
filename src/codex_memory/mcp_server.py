from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from mcp.server.auth.provider import TokenVerifier
from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import FastMCP

from .mcp_auth import MCP_REQUIRED_SCOPES
from .models import Layer
from .service import MemoryService


def _build_service(db_path: str | Path) -> MemoryService:
    return MemoryService(db_path)


def create_server(db_path: str | Path = "memory.db") -> FastMCP:
    service = _build_service(db_path)
    server = FastMCP("Codex Memory MCP")

    @server.tool()
    def health() -> dict[str, Any]:
        return service.health_status()

    @server.tool()
    def append(
        project: str,
        conversation: str,
        role: str,
        content: str,
        metadata: dict[str, Any] | None = None,
        process_now: bool = False,
        enqueue_async: bool = False,
    ) -> dict[str, Any]:
        raw_id = service.append_conversation(
            project_id=project,
            conversation_id=conversation,
            role=role,
            content=content,
            metadata=metadata,
            process_now=process_now,
            enqueue_async=enqueue_async,
        )
        if enqueue_async:
            service.drain_async_processor()
            service.stop_async_processor()
        return {"raw_log_id": raw_id}

    @server.tool()
    def retrieve(
        project: str,
        query: str,
        tag: list[str] | None = None,
        module: list[str] | None = None,
        tag_type: list[str] | None = None,
        layer: list[Layer] | None = None,
        memory_type: list[str] | None = None,
        limit: int = 8,
    ) -> dict[str, Any]:
        results = service.retrieve(
            project,
            query,
            tags=tag or None,
            modules=module or None,
            type_tags=tag_type or None,
            layers=layer or None,
            memory_types=memory_type or None,
            limit=limit,
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

    @server.tool()
    def context(
        project: str,
        task: str,
        tag: list[str] | None = None,
        module: list[str] | None = None,
        tag_type: list[str] | None = None,
        layer: list[Layer] | None = None,
        memory_type: list[str] | None = None,
        limit: int = 8,
        project_context: str | None = None,
        skip_pending: bool = False,
    ) -> dict[str, Any]:
        if not skip_pending:
            service.process_project_pending_memories(project)
        return {
            "context": service.build_context(
                project,
                task,
                tags=tag or None,
                modules=module or None,
                type_tags=tag_type or None,
                layers=layer or None,
                memory_types=memory_type or None,
                limit=limit,
                project_context=project_context,
            )
        }

    return server


def run_server(db_path: str | Path = "memory.db", transport: str = "stdio") -> None:
    create_server(db_path).run(transport=transport)

def create_v1_server(
    api_client: Any,
    host: str = "127.0.0.1",
    port: int = 8000,
    token_verifier: TokenVerifier | None = None,
) -> FastMCP:
    """Create the HTTP-backed MCP surface used by deployed Codex clients."""
    auth = None
    if token_verifier is not None:
        auth = AuthSettings(
            issuer_url="http://127.0.0.1:8001",
            resource_server_url="http://127.0.0.1:8001/mcp",
            required_scopes=MCP_REQUIRED_SCOPES,
        )
    server = FastMCP(
        "Codex Memory V1 MCP",
        host=host,
        port=port,
        stateless_http=True,
        token_verifier=token_verifier,
        auth=auth,
    )

    @server.tool()
    def build_context(project: str, task: str, filters: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = {"project_key": project, "task": task}
        payload.update(filters or {})
        return api_client.post("/api/v1/context", payload)

    @server.tool()
    def retrieve_memory(project: str, query: str, filters: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = {"project_key": project, "query": query}
        payload.update(filters or {})
        return api_client.post("/api/v1/search", payload)

    @server.tool()
    def record_outcome(project: str, type: str, content: dict[str, Any]) -> dict[str, Any]:
        return api_client.post(
            "/api/v1/memory",
            {"project_key": project, "level": "L1", "type": type, "content": content},
        )

    @server.tool()
    def append_message(
        project: str,
        session: str,
        event: str,
        role: Literal["user", "assistant", "system"],
        content: str,
        occurred_at: str | None = None,
        source: str = "skill",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return api_client.post(
            "/api/v1/append",
            {
                "project_key": project,
                "session_key": session,
                "event_key": event,
                "role": role,
                "content": content,
                "occurred_at": occurred_at,
                "source": source,
                "metadata": metadata or {},
            },
        )

    @server.tool()
    def health() -> dict[str, Any]:
        return api_client.get("/api/v1/health")

    return server
