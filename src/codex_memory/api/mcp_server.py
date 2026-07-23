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
    def record_outcome(project: str, type: str, content: dict[str, Any]) -> dict[str, Any]:
        return api_client.post(
            "/api/v1/memory",
            {"project_key": project, "level": "L1", "type": type, "content": content},
        )

    @server.tool()
    def health() -> dict[str, Any]:
        return api_client.get("/api/v1/health")

    return server
