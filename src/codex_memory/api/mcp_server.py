from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

def create_v1_server(api_client: Any, host: str = "127.0.0.1", port: int = 8000) -> FastMCP:
    """Create the HTTP-backed MCP surface used by deployed Codex clients."""
    server = FastMCP("Codex Memory V1 MCP", host=host, port=port, stateless_http=True)

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
    def append_message(
        project: str,
        session: str,
        event: str,
        role: str,
        content: str,
        occurred_at: str | None = None,
        source: str = "skill",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """将一条原始对话消息写入项目记忆。"""
        payload: dict[str, Any] = {
            "project_key": project,
            "session_key": session,
            "event_key": event,
            "role": role,
            "content": content,
            "source": source,
            "metadata": metadata or {},
        }
        if occurred_at is not None:
            payload["occurred_at"] = occurred_at
        return api_client.post("/api/v1/append", payload)
    @server.tool()
    def record_outcome(
        project: str,
        type: str,
        content: dict[str, Any],
        title: str | None = None,
    ) -> dict[str, Any]:
        """写入一条带可选标题的 L1 项目知识。"""
        return api_client.post(
            "/api/v1/memory",
            {"project_key": project, "level": "L1", "type": type, "content": content, "title": title},
        )
    @server.tool()
    def health() -> dict[str, Any]:
        return api_client.get("/api/v1/health")

    return server
