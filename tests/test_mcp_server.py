from __future__ import annotations

import asyncio

from codex_memory.cli import main
from codex_memory.mcp_server import create_server


def test_mcp_server_exposes_expected_tools(tmp_path):
    server = create_server(tmp_path / "memory.db")

    tools = asyncio.run(server.list_tools())
    assert sorted(tool.name for tool in tools) == ["append", "context", "health", "retrieve"]


def test_mcp_server_tools_share_the_same_database(tmp_path):
    server = create_server(tmp_path / "memory.db")

    async def run():
        health = await server.call_tool("health", {})
        append = await server.call_tool(
            "append",
            {
                "project": "project-a",
                "conversation": "conv-1",
                "role": "user",
                "content": "Bug: MCP append should persist raw logs",
                "process_now": True,
            },
        )
        retrieve = await server.call_tool(
            "retrieve",
            {
                "project": "project-a",
                "query": "MCP append",
            },
        )
        context = await server.call_tool(
            "context",
            {
                "project": "project-a",
                "task": "Fix MCP append bug",
            },
        )
        return health[1], append[1], retrieve[1], context[1]

    health, append, retrieve, context = asyncio.run(run())

    assert health["ok"] is True
    assert append["raw_log_id"] > 0
    assert retrieve["results"]
    assert retrieve["results"][0]["project_id"] == "project-a"
    assert "Fix MCP append bug" in context["context"]
    assert "MCP append should persist raw logs" in context["context"]


def test_cli_mcp_command_runs_stdio_server(monkeypatch):
    captured = {}

    class FakeServer:
        def run(self, transport):
            captured["transport"] = transport

    monkeypatch.setattr("codex_memory.cli.create_mcp_server", lambda db_path: FakeServer())
    monkeypatch.setattr("sys.argv", ["codex-memory", "--db", "memory.db", "mcp"])

    main()

    assert captured["transport"] == "stdio"
