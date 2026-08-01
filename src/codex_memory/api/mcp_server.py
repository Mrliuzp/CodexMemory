from __future__ import annotations

import hashlib
from typing import Any
from urllib.parse import quote, urlencode
from uuid import uuid4

from mcp.server.fastmcp import FastMCP

AUTO_EVENT_TYPES = frozenset(
    {
        "assistant",
        "assistant_message",
        "assistant_response",
        "message",
        "post_tool_use",
        "posttooluse",
        "pre_tool_use",
        "pretooluse",
        "session_end",
        "sessionend",
        "stop",
        "system",
        "system_message",
        "user",
        "user_message",
        "user_prompt_submit",
        "userpromptsubmit",
    }
)


def _resolve_append_event_key(
    project: str,
    session: str,
    event: str,
    role: str,
    event_key: str | None,
) -> tuple[str, bool]:
    """兼容旧 event_key 调用，并为普通事件类型生成逐消息唯一键。"""
    explicit = (event_key or "").strip()
    if explicit:
        return explicit, False
    legacy = event.strip()
    if legacy.casefold() not in AUTO_EVENT_TYPES:
        return legacy, False
    session_hash = hashlib.sha256(f"{project}\0{session}".encode("utf-8")).hexdigest()[:16]
    return f"mcp:{session_hash}:{role}:{uuid4().hex}", True

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
        event_key: str | None = None,
    ) -> dict[str, Any]:
        """将原始对话写入项目记忆；event 是类型，重试幂等时可显式提供 event_key。"""
        resolved_event_key, generated = _resolve_append_event_key(project, session, event, role, event_key)
        resolved_metadata = dict(metadata or {})
        if generated:
            resolved_metadata.setdefault("event_type", event)
        payload: dict[str, Any] = {
            "project_key": project,
            "session_key": session,
            "event_key": resolved_event_key,
            "role": role,
            "content": content,
            "source": source,
            "metadata": resolved_metadata,
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
    def list_contract_services(project: str, keyword: str | None = None) -> dict[str, Any]:
        """列出项目中的接口契约服务及其 Revision 摘要。"""
        query = {"project_key": project}
        if keyword:
            query["keyword"] = keyword
        return api_client.get(f"/api/v1/contracts/services?{urlencode(query)}")

    @server.tool()
    def get_contract_service(project: str, service: str) -> dict[str, Any]:
        """读取一个接口契约服务及其 Revision 时间线。"""
        path = quote(service, safe="")
        return api_client.get(f"/api/v1/contracts/services/{path}?{urlencode({'project_key': project})}")

    @server.tool()
    def ensure_contract_service(
        project: str,
        service: str,
        name: str | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        """幂等创建契约服务；已有服务不会被隐式修改。"""
        return api_client.post(
            "/api/v1/contracts/services/ensure",
            {"project_key": project, "service_key": service, "name": name, "description": description},
        )

    @server.tool()
    def propose_contract_revision(
        project: str,
        service: str,
        document: str,
        filename: str = "openapi.json",
    ) -> dict[str, Any]:
        """校验并上传 OpenAPI 文档，只创建 proposed Revision，不会自动发布。"""
        path = quote(service, safe="")
        return api_client.post(
            f"/api/v1/contracts/services/{path}/revisions",
            {"project_key": project, "filename": filename, "document": document},
        )

    @server.tool()
    def health() -> dict[str, Any]:
        return api_client.get("/api/v1/health")

    return server
