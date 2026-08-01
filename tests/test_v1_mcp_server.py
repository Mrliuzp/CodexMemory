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
    from codex_memory.api.mcp_server import create_v1_server

    client = FakeApiClient()
    result = _tool(create_v1_server(client), "build_context")(project="erp", task="change order")

    assert result["path"] == "/api/v1/context"
    assert result["payload"] == {"project_key": "erp", "task": "change order"}


def test_record_outcome_only_creates_l1_memory() -> None:
    from codex_memory.api.mcp_server import create_v1_server

    client = FakeApiClient()
    result = _tool(create_v1_server(client), "record_outcome")(project="erp", type="coding_rule", content={"text": "use service"}, title="服务使用规则")

    assert result["path"] == "/api/v1/memory"
    assert result["payload"]["level"] == "L1"
    assert result["payload"]["title"] == "服务使用规则"


def test_retrieve_memory_calls_v1_search_endpoint() -> None:
    from codex_memory.api.mcp_server import create_v1_server

    client = FakeApiClient()
    result = _tool(create_v1_server(client), "retrieve_memory")(project="erp", query="order")

    assert result["path"] == "/api/v1/search"


def test_append_message_maps_all_fields_to_v1_append_payload() -> None:
    from codex_memory.api.mcp_server import create_v1_server

    client = FakeApiClient()
    result = _tool(create_v1_server(client), "append_message")(
        project="erp",
        session="session-42",
        event="codex:erp:session-42:t7:assistant",
        role="assistant",
        content="preserve every mapped field",
        occurred_at="2026-07-15T09:08:07+08:00",
        source="codex-desktop",
        metadata={"trace_id": "trace-42", "review": True},
    )

    assert result["path"] == "/api/v1/append"
    assert result["payload"] == {
        "project_key": "erp",
        "session_key": "session-42",
        "event_key": "codex:erp:session-42:t7:assistant",
        "role": "assistant",
        "content": "preserve every mapped field",
        "occurred_at": "2026-07-15T09:08:07+08:00",
        "source": "codex-desktop",
        "metadata": {"trace_id": "trace-42", "review": True},
    }


def test_append_message_generates_unique_keys_for_event_types() -> None:
    from codex_memory.api.mcp_server import create_v1_server

    client = FakeApiClient()
    tool = _tool(create_v1_server(client), "append_message")

    first = tool(project="erp", session="session-42", event="user_prompt_submit", role="user", content="同一内容")
    second = tool(project="erp", session="session-42", event="user_prompt_submit", role="user", content="同一内容")

    first_payload = first["payload"]
    second_payload = second["payload"]
    assert first_payload["event_key"].startswith("mcp:")
    assert second_payload["event_key"].startswith("mcp:")
    assert first_payload["event_key"] != second_payload["event_key"]
    assert first_payload["metadata"] == {"event_type": "user_prompt_submit"}
    assert second_payload["metadata"] == {"event_type": "user_prompt_submit"}


def test_append_message_accepts_explicit_idempotency_key_for_generic_event() -> None:
    from codex_memory.api.mcp_server import create_v1_server

    client = FakeApiClient()
    result = _tool(create_v1_server(client), "append_message")(
        project="erp",
        session="session-42",
        event="assistant_response",
        event_key="codex:erp:session-42:t8:assistant",
        role="assistant",
        content="完成",
        metadata={"trace_id": "trace-43"},
    )

    assert result["payload"]["event_key"] == "codex:erp:session-42:t8:assistant"
    assert result["payload"]["metadata"] == {"trace_id": "trace-43"}


def test_append_message_preserves_legacy_plain_unique_event_key() -> None:
    from codex_memory.api.mcp_server import create_v1_server

    client = FakeApiClient()
    result = _tool(create_v1_server(client), "append_message")(
        project="erp",
        session="session-42",
        event="turn-20260801-0001",
        role="user",
        content="兼容旧调用",
    )

    assert result["payload"]["event_key"] == "turn-20260801-0001"
    assert result["payload"]["metadata"] == {}
