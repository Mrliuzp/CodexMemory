from __future__ import annotations

import asyncio


class FakeApiClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def post(self, path: str, payload: dict) -> dict:
        self.calls.append((path, payload))
        return {"path": path, "payload": payload}

    def get(self, path: str) -> dict:
        self.calls.append((path, {}))
        return {"status": "ok"}


def _tool(server, name: str):
    return server._tool_manager.get_tool(name).fn


def test_build_context_calls_v1_context_endpoint() -> None:
    from codex_memory.mcp_server import create_v1_server

    client = FakeApiClient()
    result = _tool(create_v1_server(client), "build_context")(project="erp", task="change order")

    assert result["path"] == "/api/v1/context"
    assert result["payload"] == {"project_key": "erp", "task": "change order"}


def test_record_outcome_only_creates_l1_memory() -> None:
    from codex_memory.mcp_server import create_v1_server

    client = FakeApiClient()
    result = _tool(create_v1_server(client), "record_outcome")(project="erp", type="coding_rule", content={"text": "use service"})

    assert result["path"] == "/api/v1/memory"
    assert result["payload"]["level"] == "L1"


def test_retrieve_memory_calls_v1_search_endpoint() -> None:
    from codex_memory.mcp_server import create_v1_server

    client = FakeApiClient()
    result = _tool(create_v1_server(client), "retrieve_memory")(project="erp", query="order")

    assert result["path"] == "/api/v1/search"
